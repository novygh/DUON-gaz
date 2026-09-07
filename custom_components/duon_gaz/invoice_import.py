"""Import parsed DUON invoices into the integration Store."""
from __future__ import annotations

from datetime import datetime, time
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .invoice_parser import DuonInvoice, is_trusted_billing_reading

if TYPE_CHECKING:
    from .runtime import DuonGazRuntime


def _processed_invoice_numbers(items: Any) -> set[str]:
    """Return invoice numbers already recorded in Store."""
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


def _billing_period(invoice: DuonInvoice, source_message_id: str | None) -> dict[str, Any]:
    """Build the auditable billing record stored independently of anchors."""
    data = invoice.as_dict()
    data.update(
        {
            "source": "duon_invoice",
            # Compatibility key consumed by runtime.conversion_factor.
            "conversion_factor": invoice.conversion_factor_kwh_m3,
            "current_reading_classification": (
                "trusted_billing_reading"
                if is_trusted_billing_reading(invoice.current_reading.reading_type)
                else "billing_only_reading"
            ),
            "source_message_id": source_message_id,
            "imported_at": dt_util.utcnow().isoformat(),
        }
    )
    return data


def _invoice_day_timestamp(invoice: DuonInvoice) -> datetime:
    """Represent a day-only invoice reading at local noon.

    DUON gives a billing date rather than the physical read time. Noon avoids
    pretending the reading happened at midnight and minimizes day-edge bias
    when matching the nearest hourly Recorder statistic.
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
) -> dict[str, Any]:
    """Persist one parsed invoice and optionally create its meter anchor.

    Billing data is always retained. Only a reading type explicitly classified
    as trusted by the parser is allowed to participate in the physical model.
    Re-importing the same invoice number is idempotent.
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
            exclude_from_calibration=False,
        )

    runtime.data.setdefault("billing_periods", []).append(
        _billing_period(invoice, source_message_id)
    )
    processed.append(
        {
            "invoice_number": invoice.invoice_number,
            "source_message_id": source_message_id,
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
