"""Import przetworzonych faktur DUON do magazynu danych integracji."""
from __future__ import annotations

from datetime import datetime, time
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .invoice_parser import DuonInvoice, is_trusted_billing_reading

if TYPE_CHECKING:
    from .runtime import DuonGazRuntime


def _processed_invoice_numbers(items: Any) -> set[str]:
    """Zwróć numery faktur już zapisanych w magazynie danych integracji."""
    if not isinstance(items, list):
        return set()

    result: set[str] = set()
    for item in items:
        if isinstance(item, str):
            result.add(item)
        elif isinstance(item, dict):
            number = item.get("invoice_number") or item.get("invoice_id")
            if number is not None:
                result.add(str(number))
    return result


def _billing_period(
    invoice: DuonInvoice,
    source_message_id: str | None,
    source_attachment_name: str | None,
) -> dict[str, Any]:
    """Zbuduj audytowalny rekord rozliczeniowy niezależny od kotwic gazomierza."""
    data = invoice.as_dict()
    data.update(
        {
            "source": "duon_invoice",
            # Klucz zgodności używany przez runtime.conversion_factor.
            "conversion_factor": invoice.conversion_factor_kwh_m3,
            "current_reading_classification": (
                "trusted_billing_reading"
                if is_trusted_billing_reading(invoice.current_reading.reading_type)
                else "billing_only_reading"
            ),
            "source_message_id": source_message_id,
            "source_attachment_name": source_attachment_name,
            "imported_at": dt_util.utcnow().isoformat(),
        }
    )
    return data


def _invoice_day_timestamp(invoice: DuonInvoice) -> datetime:
    """Reprezentuj odczyt z datą dzienną jako lokalne południe.

    DUON podaje datę rozliczeniową, a nie dokładny czas fizycznego odczytu.
    Południe nie udaje odczytu o północy i ogranicza błąd przy dopasowaniu
    najbliższej godzinowej statystyki Recorder.
    """
    return datetime.combine(
        invoice.current_reading.date,
        time(hour=12),
        tzinfo=dt_util.DEFAULT_TIME_ZONE,
    )


async def async_import_invoice(
    runtime: DuonGazRuntime,
    invoice: DuonInvoice,
    *,
    source_message_id: str | None = None,
    source_attachment_name: str | None = None,
) -> dict[str, Any]:
    """Zapisz jedną fakturę i opcjonalnie utwórz kotwicę gazomierza.

    Dane rozliczeniowe są zachowywane zawsze. Tylko typ odczytu jawnie
    sklasyfikowany przez parser jako zaufany może uczestniczyć w modelu
    fizycznym. Ponowny import tego samego numeru faktury jest idempotentny.
    """
    processed = runtime.data.setdefault("processed_invoices", [])
    if invoice.invoice_number in _processed_invoice_numbers(processed):
        return {
            "status": "duplicate",
            "invoice_number": invoice.invoice_number,
            "anchor_added": False,
        }

    trusted = is_trusted_billing_reading(invoice.current_reading.reading_type)
    anchor_added = False

    if trusted:
        anchor_added = await runtime.async_add_invoice_anchor(
            meter_m3=invoice.current_reading.meter_m3,
            timestamp=_invoice_day_timestamp(invoice),
            reading_type=invoice.current_reading.reading_type,
            invoice_id=invoice.invoice_number,
            timestamp_precision="day",
            meter_precision_m3=1.0,
            # Faktura podaje tylko dzień odczytu. Taka kotwica może domykać
            # historię fizyczną, ale nie może kalibrować CO/CWU względem
            # godzinowych statystyk Recorder, bo dokładny czas jest nieznany.
            exclude_from_calibration=True,
        )

    runtime.data.setdefault("billing_periods", []).append(
        _billing_period(invoice, source_message_id, source_attachment_name)
    )
    processed.append(
        {
            "invoice_number": invoice.invoice_number,
            "source_message_id": source_message_id,
            "source_attachment_name": source_attachment_name,
            "imported_at": dt_util.utcnow().isoformat(),
            "reading_type": invoice.current_reading.reading_type,
            "reading_classification": (
                "trusted_billing_reading" if trusted else "billing_only_reading"
            ),
            "anchor_added": anchor_added,
        }
    )
    await runtime.async_save()
    runtime.async_notify()

    return {
        "status": "imported",
        "invoice_number": invoice.invoice_number,
        "anchor_added": anchor_added,
        "reading_classification": (
            "trusted_billing_reading" if trusted else "billing_only_reading"
        ),
    }
