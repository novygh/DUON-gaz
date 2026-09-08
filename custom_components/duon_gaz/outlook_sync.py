"""Automatyczna synchronizacja faktur DUON z Microsoft Outlook."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from hashlib import sha256
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

# Zwiększamy przy świadomej zmianie reguł rozpoznawania układu faktury.
_INVOICE_PARSER_VERSION = 1


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


def _pdf_fingerprint(content: bytes) -> str:
    """Zwróć krótki odcisk PDF bez ujawniania jego treści."""
    return sha256(content).hexdigest()[:16]


def _message_audit_record(message: dict[str, Any]) -> dict[str, Any]:
    """Zbuduj bezpieczny rekord zakończonego przetwarzania wiadomości."""
    return {
        "message_id": str(message.get("id") or ""),
        "internet_message_id": message.get("internetMessageId"),
        "received_at": message.get("receivedDateTime"),
        "subject": message.get("subject"),
        "processed_at": dt_util.utcnow().isoformat(),
    }


def _safe_commit_error(err: Exception) -> str:
    """Zwróć bezpieczny opis błędu zatwierdzania bez ryzyka ujawnienia sekretów."""
    if isinstance(err, (DuonInvoiceParseError, OSError, ValueError, RuntimeError)):
        return str(err)[:500]
    return f"Nieoczekiwany błąd zatwierdzania paczki: {type(err).__name__}"


class DuonOutlookSynchronizer:
    """Pobieraj i importuj nowe faktury z Outlooka bez zapisu sekretów w logach."""

    def __init__(self, runtime, graph: DuonGraphClient, config: dict[str, Any]) -> None:
        self.runtime = runtime
        self.graph = graph
        self.config = config
        self._lock = asyncio.Lock()

    async def async_sync(self, *, reason: str) -> dict[str, Any]:
        """Wykonaj jedną pełną iterację synchronizacji Outlook → DUON."""
        async with self._lock:
            return await self._async_sync_locked(reason=reason)

    async def _save_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Zapisz wynik synchronizacji i odśwież encje diagnostyczne."""
        self.runtime.data["last_outlook_sync"] = result
        await self.runtime.async_save()
        self.runtime.async_notify()
        return result

    async def _block_import(
        self,
        *,
        reason: str,
        started_at: str,
        folder_name: str,
        folder_display: str | None,
        matched_messages: int,
        pdf_count: int,
        ignored_unprotected_pdfs: int,
        stage: str,
        error: str,
        attachment_name: str | None = None,
        attachment_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        """Wstrzymaj całą paczkę zanim jakakolwiek nowa faktura zostanie zapisana."""
        now = dt_util.utcnow().isoformat()
        guard = {
            "status": "blocked",
            "blocked_at": now,
            "stage": stage,
            "attachment": attachment_name,
            "fingerprint": attachment_fingerprint,
            "parser_version": _INVOICE_PARSER_VERSION,
            "error": error,
        }
        self.runtime.data["invoice_import_guard"] = guard
        result = {
            "status": "blocked",
            "reason": reason,
            "started_at": started_at,
            "finished_at": now,
            "folder": folder_display or folder_name,
            "matched_messages": matched_messages,
            "processed_messages": 0,
            "pdf_count": pdf_count,
            "ignored_unprotected_pdfs": ignored_unprotected_pdfs,
            "imported_invoices": 0,
            "duplicate_invoices": 0,
            "anchors_added": 0,
            "parser_version": _INVOICE_PARSER_VERSION,
            "guard": guard,
            "canonical_refresh": None,
            "errors": [
                {
                    "stage": stage,
                    "attachment": attachment_name,
                    "error": error,
                }
            ],
        }
        return await self._save_result(result)

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
                "parser_version": _INVOICE_PARSER_VERSION,
                "canonical_refresh": None,
                "errors": [{"stage": "graph", "error": str(err)}],
            }
            await self._save_result(result)
            raise

        folder_display = str(
            folder.get("path") or folder.get("displayName") or folder_name
        )
        processed_items = self.runtime.data.setdefault("processed_messages", [])
        processed_ids = _processed_message_ids(processed_items)
        pending_messages = [
            message
            for message in messages
            if str(message.get("id") or "")
            and str(message.get("id") or "") not in processed_ids
        ]

        # Faza 1: preflight. Pobieramy i parsujemy CAŁĄ nową paczkę bez
        # zapisywania faktur. Zmiana układu choć jednego zaszyfrowanego PDF-a
        # blokuje całą paczkę, więc nie ma częściowego importu.
        staged: list[tuple[dict[str, Any], Any, Any]] = []
        messages_to_mark: list[dict[str, Any]] = []
        pdf_count = 0
        ignored_unprotected_pdfs = 0

        for message in pending_messages:
            message_id = str(message.get("id") or "")
            try:
                attachments = await self.graph.async_get_pdf_attachments(message_id)
            except DuonGraphError as err:
                return await self._block_import(
                    reason=reason,
                    started_at=started_at,
                    folder_name=folder_name,
                    folder_display=folder_display,
                    matched_messages=len(messages),
                    pdf_count=pdf_count,
                    ignored_unprotected_pdfs=ignored_unprotected_pdfs,
                    stage="attachments",
                    error=str(err),
                )

            messages_to_mark.append(message)

            for attachment in attachments:
                pdf_count += 1
                try:
                    is_encrypted = await self.runtime.hass.async_add_executor_job(
                        _pdf_is_encrypted,
                        attachment.content,
                    )
                except (DuonInvoiceParseError, OSError, ValueError) as err:
                    return await self._block_import(
                        reason=reason,
                        started_at=started_at,
                        folder_name=folder_name,
                        folder_display=folder_display,
                        matched_messages=len(messages),
                        pdf_count=pdf_count,
                        ignored_unprotected_pdfs=ignored_unprotected_pdfs,
                        stage="pdf_probe",
                        error=str(err),
                        attachment_name=attachment.name,
                        attachment_fingerprint=_pdf_fingerprint(attachment.content),
                    )

                if not is_encrypted:
                    ignored_unprotected_pdfs += 1
                    continue

                try:
                    invoice = await self.runtime.hass.async_add_executor_job(
                        parse_invoice_pdf_bytes,
                        attachment.content,
                        password,
                    )
                except (DuonInvoiceParseError, OSError, ValueError) as err:
                    return await self._block_import(
                        reason=reason,
                        started_at=started_at,
                        folder_name=folder_name,
                        folder_display=folder_display,
                        matched_messages=len(messages),
                        pdf_count=pdf_count,
                        ignored_unprotected_pdfs=ignored_unprotected_pdfs,
                        stage="invoice_preflight",
                        error=str(err),
                        attachment_name=attachment.name,
                        attachment_fingerprint=_pdf_fingerprint(attachment.content),
                    )

                staged.append((message, attachment, invoice))

        # Faza 2: etapowanie w pamięci. Po preflight żadna pojedyncza faktura
        # nie zapisuje Store. Cała paczka zostanie utrwalona dopiero jednym
        # atomowym zapisem po pomyślnym przejściu wszystkich importów.
        before_commit = deepcopy(self.runtime.data)
        imported = 0
        duplicates = 0
        anchors_added = 0

        try:
            for message, attachment, invoice in staged:
                message_id = str(message.get("id") or "")
                result = await async_import_invoice(
                    self.runtime,
                    invoice,
                    source_message_id=message_id,
                    source_attachment_name=attachment.name,
                    persist=False,
                )
                if result.get("status") == "duplicate":
                    duplicates += 1
                elif result.get("status") == "imported":
                    imported += 1
                if result.get("anchor_added", False):
                    anchors_added += 1

            processed_items = self.runtime.data.setdefault("processed_messages", [])
            for message in messages_to_mark:
                message_id = str(message.get("id") or "")
                if message_id and message_id not in processed_ids:
                    processed_items.append(_message_audit_record(message))
                    processed_ids.add(message_id)

            self.runtime.data["invoice_import_guard"] = {
                "status": "ok",
                "checked_at": dt_util.utcnow().isoformat(),
                "parser_version": _INVOICE_PARSER_VERSION,
                "validated_encrypted_pdfs": len(staged),
                "ignored_unprotected_pdfs": ignored_unprotected_pdfs,
            }
        except asyncio.CancelledError:
            self.runtime.data = before_commit
            raise
        except Exception as err:  # noqa: BLE001 - rollback obejmuje także nieznane błędy
            self.runtime.data = before_commit
            return await self._block_import(
                reason=reason,
                started_at=started_at,
                folder_name=folder_name,
                folder_display=folder_display,
                matched_messages=len(messages),
                pdf_count=pdf_count,
                ignored_unprotected_pdfs=ignored_unprotected_pdfs,
                stage="invoice_commit",
                error=_safe_commit_error(err),
            )

        # Faza 3: jeden atomowy zapis Store. Home Assistant Store nie propaguje
        # wszystkich błędów zapisu, dlatego po zapisie odczytujemy dane ponownie
        # i sprawdzamy, czy cała paczka rzeczywiście została utrwalona.
        committed_data = deepcopy(self.runtime.data)
        await self.runtime.async_save()
        persisted_data = await self.runtime.store.async_load()
        if persisted_data != committed_data:
            self.runtime.data = before_commit
            return await self._block_import(
                reason=reason,
                started_at=started_at,
                folder_name=folder_name,
                folder_display=folder_display,
                matched_messages=len(messages),
                pdf_count=pdf_count,
                ignored_unprotected_pdfs=ignored_unprotected_pdfs,
                stage="store_commit",
                error="Nie udało się potwierdzić atomowego zapisu paczki w DUON Store.",
            )

        if anchors_added:
            await self.runtime.async_refresh_source_snapshot(notify=False)
        self.runtime.async_notify()

        canonical_refresh = None
        errors: list[dict[str, str]] = []
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
            "folder": folder_display,
            "matched_messages": len(messages),
            "processed_messages": len(messages_to_mark),
            "pdf_count": pdf_count,
            "ignored_unprotected_pdfs": ignored_unprotected_pdfs,
            "imported_invoices": imported,
            "duplicate_invoices": duplicates,
            "anchors_added": anchors_added,
            "parser_version": _INVOICE_PARSER_VERSION,
            "guard": self.runtime.data.get("invoice_import_guard"),
            "canonical_refresh": canonical_refresh,
            "errors": errors,
        }
        return await self._save_result(result)
