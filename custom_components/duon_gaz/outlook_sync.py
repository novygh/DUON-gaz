"""Automatyczna synchronizacja faktur DUON z Microsoft Outlook."""
from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Any

from homeassistant.util import dt as dt_util

from .canonical_history import CanonicalHistoryError
from .canonical_statistics import async_refresh_canonical_tail_statistics
from .const import (
    CONF_INVOICE_PDF_PASSWORD,
    CONF_OUTLOOK_FOLDER,
    CONF_OUTLOOK_SENDER,
    CONF_OUTLOOK_SUBJECT,
)
from .graph import DuonGraphClient, DuonGraphError
from .invoice_import import async_import_invoice
from .invoice_parser import DuonInvoiceParseError, parse_invoice_pdf_bytes


def _processed_message_ids(items: Any) -> set[str]:
    """Zwróć identyfikatory wiadomości już w pełni przetworzonych."""
    if not isinstance(items, list):
        return set()
    result: set[str] = set()
    for item in items:
        if isinstance(item, str):
            result.add(item)
        elif isinstance(item, dict):
            value = item.get("message_id")
            if value:
                result.add(str(value))
    return result


def _pdf_is_encrypted(content: bytes) -> bool:
    """Sprawdź, czy PDF wymaga odszyfrowania przed odczytem.

    Faktury DUON są chronione hasłem. Niezabezpieczone PDF-y dołączane do
    wiadomości (np. informacje taryfowe) nie są fakturami i mają być
    ignorowane przez automatyczny importer.
    """
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as err:
        raise DuonInvoiceParseError("Brak biblioteki pypdf do odczytu faktur.") from err

    try:
        reader = PdfReader(BytesIO(content))
    except (PdfReadError, ValueError, TypeError) as err:
        raise DuonInvoiceParseError("Nie udało się odczytać załącznika PDF.") from err
    return bool(reader.is_encrypted)


class DuonOutlookSynchronizer:
    """Pobieraj i importuj nowe faktury z Outlooka bez zapisu hasła w logach."""

    def __init__(self, runtime, graph: DuonGraphClient, config: dict[str, Any]) -> None:
        self.runtime = runtime
        self.graph = graph
        self.config = config
        self._lock = asyncio.Lock()

    async def async_sync(self, *, reason: str) -> dict[str, Any]:
        """Wykonaj jedną pełną iterację synchronizacji Outlook → DUON."""
        async with self._lock:
            return await self._async_sync_locked(reason=reason)

    async def _async_sync_locked(self, *, reason: str) -> dict[str, Any]:
        folder_name = str(self.config.get(CONF_OUTLOOK_FOLDER) or "").strip()
        sender = str(self.config.get(CONF_OUTLOOK_SENDER) or "").strip()
        subject = str(self.config.get(CONF_OUTLOOK_SUBJECT) or "").strip()
        password = str(self.config.get(CONF_INVOICE_PDF_PASSWORD) or "")

        if not folder_name:
            raise ValueError("Nie skonfigurowano folderu Outlook dla faktur DUON.")
        if not sender:
            raise ValueError("Nie skonfigurowano nadawcy faktur DUON.")
        if not subject:
            raise ValueError("Nie skonfigurowano tematu wiadomości z fakturą DUON.")
        if not password:
            raise ValueError("Nie skonfigurowano hasła do faktur PDF DUON.")

        started_at = dt_util.utcnow().isoformat()
        errors: list[dict[str, str]] = []
        imported = 0
        duplicates = 0
        anchors_added = 0
        messages_marked = 0
        pdf_count = 0
        ignored_unprotected_pdfs = 0

        try:
            folder = await self.graph.async_find_mail_folder(folder_name)
            folder_id = str(folder.get("id") or "")
            messages = await self.graph.async_list_matching_messages(
                folder_id,
                sender=sender,
                subject=subject,
            )
        except DuonGraphError as err:
            result = {
                "status": "error",
                "reason": reason,
                "started_at": started_at,
                "finished_at": dt_util.utcnow().isoformat(),
                "folder": folder_name,
                "matched_messages": 0,
                "processed_messages": 0,
                "pdf_count": 0,
                "ignored_unprotected_pdfs": 0,
                "imported_invoices": 0,
                "duplicate_invoices": 0,
                "anchors_added": 0,
                "errors": [{"stage": "graph", "error": str(err)}],
            }
            self.runtime.data["last_outlook_sync"] = result
            await self.runtime.async_save()
            self.runtime.async_notify()
            raise

        processed_items = self.runtime.data.setdefault("processed_messages", [])
        processed_ids = _processed_message_ids(processed_items)

        for message in messages:
            message_id = str(message.get("id") or "")
            if not message_id or message_id in processed_ids:
                continue

            message_ok = True
            try:
                attachments = await self.graph.async_get_pdf_attachments(message_id)
            except DuonGraphError as err:
                errors.append(
                    {
                        "stage": "attachments",
                        "message_id": message_id,
                        "error": str(err),
                    }
                )
                continue

            if not attachments:
                errors.append(
                    {
                        "stage": "attachments",
                        "message_id": message_id,
                        "error": "Wiadomość nie zawiera załącznika PDF.",
                    }
                )
                continue

            for attachment in attachments:
                pdf_count += 1
                try:
                    is_encrypted = await self.runtime.hass.async_add_executor_job(
                        _pdf_is_encrypted,
                        attachment.content,
                    )
                    if not is_encrypted:
                        ignored_unprotected_pdfs += 1
                        continue

                    invoice = await self.runtime.hass.async_add_executor_job(
                        parse_invoice_pdf_bytes,
                        attachment.content,
                        password,
                    )
                    result = await async_import_invoice(
                        self.runtime,
                        invoice,
                        source_message_id=message_id,
                        source_attachment_name=attachment.name,
                    )
                except (DuonInvoiceParseError, OSError, ValueError) as err:
                    message_ok = False
                    errors.append(
                        {
                            "stage": "invoice",
                            "message_id": message_id,
                            "attachment": attachment.name,
                            "error": str(err),
                        }
                    )
                    continue

                if result.get("status") == "duplicate":
                    duplicates += 1
                elif result.get("status") == "imported":
                    imported += 1
                if result.get("anchor_added", False):
                    anchors_added += 1

            if message_ok:
                processed_items.append(
                    {
                        "message_id": message_id,
                        "internet_message_id": message.get("internetMessageId"),
                        "received_at": message.get("receivedDateTime"),
                        "subject": message.get("subject"),
                        "processed_at": dt_util.utcnow().isoformat(),
                    }
                )
                processed_ids.add(message_id)
                messages_marked += 1

        canonical_refresh = None
        if anchors_added:
            try:
                canonical_refresh = await async_refresh_canonical_tail_statistics(
                    self.runtime,
                    reason="outlook_invoice_sync",
                )
            except (CanonicalHistoryError, ValueError, RuntimeError) as err:
                canonical_refresh = {"status": "error", "reason": str(err)}
                errors.append({"stage": "canonical_refresh", "error": str(err)})

        result = {
            "status": "ok" if not errors else "partial",
            "reason": reason,
            "started_at": started_at,
            "finished_at": dt_util.utcnow().isoformat(),
            "folder": folder.get("path") or folder.get("displayName") or folder_name,
            "matched_messages": len(messages),
            "processed_messages": messages_marked,
            "pdf_count": pdf_count,
            "ignored_unprotected_pdfs": ignored_unprotected_pdfs,
            "imported_invoices": imported,
            "duplicate_invoices": duplicates,
            "anchors_added": anchors_added,
            "canonical_refresh": canonical_refresh,
            "errors": errors[-20:],
        }
        self.runtime.data["last_outlook_sync"] = result
        await self.runtime.async_save()
        self.runtime.async_notify()
        return result
