"""Pure canonical cost allocation for DUON Gaz."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import hashlib
import json
import math
from typing import Any, Iterable

_EPSILON = 1e-9
_ONE_HOUR = timedelta(hours=1)


class CanonicalCostError(ValueError):
    """Raised when canonical invoice costs cannot be allocated safely."""


@dataclass(frozen=True, slots=True)
class CanonicalCostHour:
    """One hourly cost row split into CO, CWU and fixed/remainder charges."""

    start: datetime
    heating_pln: float
    dhw_pln: float
    fixed_pln: float
    invoiced: bool

    @property
    def total_pln(self) -> float:
        return self.heating_pln + self.dhw_pln + self.fixed_pln


@dataclass(frozen=True, slots=True)
class CanonicalCostResult:
    """Hourly costs plus audit metadata."""

    hours: tuple[CanonicalCostHour, ...]
    billing_period_count: int
    applied_invoice_count: int
    skipped_outside_history_count: int
    skipped_partial_history_count: int
    invoiced_gross_pln: float
    published_invoiced_pln: float
    invoiced_closure_error_pln: float
    provisional_pln: float
    total_pln: float
    latest_applied_invoice_end: datetime | None
    unpriced_historical_hour_count: int


def _as_float(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as err:
        raise CanonicalCostError(f"Nieprawidłowa wartość pola kosztowego: {field}.") from err
    if not math.isfinite(result):
        raise CanonicalCostError(f"Nienumeryczna wartość pola kosztowego: {field}.")
    return result


def _as_date(value: Any, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as err:
            raise CanonicalCostError(f"Nieprawidłowa data pola kosztowego: {field}.") from err
    raise CanonicalCostError(f"Brak daty pola kosztowego: {field}.")


def _period_bounds(period: dict[str, Any], timezone: tzinfo) -> tuple[datetime, datetime]:
    previous = period.get("previous_reading")
    current = period.get("current_reading")
    if not isinstance(previous, dict) or not isinstance(current, dict):
        raise CanonicalCostError("Faktura nie zawiera pary odczytów gazomierza.")
    start_day = _as_date(previous.get("date"), "previous_reading.date")
    end_day = _as_date(current.get("date"), "current_reading.date")
    start = datetime.combine(start_day, time(hour=12), tzinfo=timezone)
    end = datetime.combine(end_day, time(hour=12), tzinfo=timezone)
    if end <= start:
        raise CanonicalCostError("Okres odczytów faktury nie jest rosnący.")
    return start, end


def _invoice_variable_and_fixed_gross(period: dict[str, Any]) -> tuple[float, float]:
    """Split exact invoice gross into variable and fixed/remainder gross.

    The invoice gross is authoritative. Variable share is reconstructed from
    billed kWh and the two variable net rates, then applied to the authoritative
    net/gross totals. The remainder therefore absorbs fixed charges and invoice
    rounding while preserving the exact gross total.
    """
    billed_kwh = _as_float(period.get("billed_energy_kwh"), "billed_energy_kwh")
    gas_rate = _as_float(period.get("gas_rate_net_pln_kwh"), "gas_rate_net_pln_kwh")
    dist_var = _as_float(
        period.get("distribution_variable_net_pln_kwh"),
        "distribution_variable_net_pln_kwh",
    )
    net_total = _as_float(period.get("net_total_pln"), "net_total_pln")
    gross_total = _as_float(period.get("gross_total_pln"), "gross_total_pln")

    if billed_kwh < -_EPSILON or gas_rate < -_EPSILON or dist_var < -_EPSILON:
        raise CanonicalCostError("Faktura zawiera ujemny koszt zmienny.")
    if net_total < -_EPSILON or gross_total < -_EPSILON:
        raise CanonicalCostError("Faktura zawiera ujemną sumę rozliczenia.")

    variable_net = max(0.0, billed_kwh) * (max(0.0, gas_rate) + max(0.0, dist_var))
    if net_total <= _EPSILON:
        if gross_total <= _EPSILON and variable_net <= _EPSILON:
            return 0.0, 0.0
        raise CanonicalCostError("Nie można rozdzielić kosztu faktury z zerową kwotą netto.")

    share = variable_net / net_total
    if share < -1e-6 or share > 1.0 + 0.02:
        raise CanonicalCostError(
            "Koszt zmienny faktury przekracza defensywny udział w kwocie netto."
        )
    share = min(1.0, max(0.0, share))
    variable_gross = gross_total * share
    fixed_gross = gross_total - variable_gross
    if fixed_gross < -0.01:
        raise CanonicalCostError("Wyliczony koszt stały faktury jest ujemny.")
    return variable_gross, max(0.0, fixed_gross)


def _actual_month_hours(moment: datetime, timezone: tzinfo) -> float:
    local = moment.astimezone(timezone)
    month_start = datetime(local.year, local.month, 1, tzinfo=timezone)
    if local.month == 12:
        next_month = datetime(local.year + 1, 1, 1, tzinfo=timezone)
    else:
        next_month = datetime(local.year, local.month + 1, 1, tzinfo=timezone)
    return (
        next_month.astimezone(UTC) - month_start.astimezone(UTC)
    ).total_seconds() / 3600.0


def billing_period_fingerprint(periods: Iterable[dict[str, Any]]) -> str:
    """Return a stable fingerprint of invoice fields that affect cost history."""
    normalized: list[dict[str, Any]] = []
    for period in periods:
        if not isinstance(period, dict):
            continue
        previous = period.get("previous_reading")
        current = period.get("current_reading")
        normalized.append(
            {
                "invoice_number": str(period.get("invoice_number") or ""),
                "previous_date": (
                    previous.get("date") if isinstance(previous, dict) else None
                ),
                "current_date": (
                    current.get("date") if isinstance(current, dict) else None
                ),
                "billed_energy_kwh": period.get("billed_energy_kwh"),
                "gas_rate_net_pln_kwh": period.get("gas_rate_net_pln_kwh"),
                "distribution_variable_net_pln_kwh": period.get(
                    "distribution_variable_net_pln_kwh"
                ),
                "net_total_pln": period.get("net_total_pln"),
                "gross_total_pln": period.get("gross_total_pln"),
            }
        )
    normalized.sort(
        key=lambda item: (str(item["current_date"]), item["invoice_number"])
    )
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_canonical_costs(
    hours: Iterable[Any],
    billing_periods: Iterable[dict[str, Any]],
    *,
    timezone: tzinfo,
    conversion_factor_kwh_m3: float,
    gas_rate_net_pln_kwh: float,
    distribution_variable_net_pln_kwh: float,
    subscription_net_pln_month: float,
    distribution_fixed_net_pln_month: float,
    vat_rate: float,
) -> CanonicalCostResult:
    """Build invoice-exact historical costs and a provisional open tail."""
    rows = sorted(list(hours), key=lambda row: row.start)
    if not rows:
        raise CanonicalCostError("Brak godzin kanonicznych do wyliczenia kosztów.")

    for index, row in enumerate(rows):
        if row.start.tzinfo is None or row.start.utcoffset() is None:
            raise CanonicalCostError(
                "Godzina kanoniczna kosztów nie ma strefy czasowej."
            )
        if index and row.start != rows[index - 1].start + _ONE_HOUR:
            raise CanonicalCostError(
                "Historia kanoniczna kosztów nie jest ciągła godzinowo."
            )

    history_start = rows[0].start
    history_end = rows[-1].start + _ONE_HOUR
    periods = [period for period in billing_periods if isinstance(period, dict)]
    period_items: list[tuple[datetime, datetime, dict[str, Any]]] = []
    for period in periods:
        start, end = _period_bounds(period, timezone)
        period_items.append((start, end, period))
    period_items.sort(key=lambda item: (item[1], item[0]))

    heating = [0.0] * len(rows)
    dhw = [0.0] * len(rows)
    fixed = [0.0] * len(rows)
    invoiced = [False] * len(rows)

    applied = 0
    skipped_outside = 0
    skipped_partial = 0
    invoiced_gross = 0.0
    published_invoiced = 0.0
    latest_applied_end: datetime | None = None
    prior_applied_end: datetime | None = None

    for start, end, period in period_items:
        if end <= history_start or start >= history_end:
            skipped_outside += 1
            continue
        if start < history_start or end > history_end:
            skipped_partial += 1
            continue
        if prior_applied_end is not None and start < prior_applied_end:
            raise CanonicalCostError(
                "Okresy faktur nakładają się i podwoiłyby koszty."
            )

        indices = [
            index
            for index, row in enumerate(rows)
            if start <= row.start < end
        ]
        expected_hours = int(
            round(
                (
                    end.astimezone(UTC) - start.astimezone(UTC)
                ).total_seconds()
                / 3600.0
            )
        )
        if len(indices) != expected_hours:
            raise CanonicalCostError(
                "Okres faktury nie ma pełnego pokrycia godzinowego."
            )
        if any(invoiced[index] for index in indices):
            raise CanonicalCostError(
                "Jedna godzina kosztów należy do więcej niż jednej faktury."
            )

        variable_gross, fixed_gross = _invoice_variable_and_fixed_gross(period)
        gross_total = _as_float(period.get("gross_total_pln"), "gross_total_pln")
        component_total = sum(
            max(0.0, float(rows[index].heating_m3))
            + max(0.0, float(rows[index].dhw_m3))
            for index in indices
        )
        if component_total <= _EPSILON and variable_gross > 0.005:
            raise CanonicalCostError(
                "Faktura ma koszt zmienny, ale historia nie ma zużycia CO/CWU w tym okresie."
            )

        allocated_variable = 0.0
        for index in indices:
            if component_total > _EPSILON:
                co_share = (
                    max(0.0, float(rows[index].heating_m3)) / component_total
                )
                dhw_share = max(0.0, float(rows[index].dhw_m3)) / component_total
                heating[index] += variable_gross * co_share
                dhw[index] += variable_gross * dhw_share
                allocated_variable += variable_gross * (co_share + dhw_share)
            fixed[index] += fixed_gross / len(indices)
            invoiced[index] = True

        if indices and abs(variable_gross - allocated_variable) > 1e-12:
            last_index = indices[-1]
            residual = variable_gross - allocated_variable
            if max(0.0, float(rows[last_index].heating_m3)) > _EPSILON:
                heating[last_index] += residual
            else:
                dhw[last_index] += residual

        invoice_published = sum(
            heating[index] + dhw[index] + fixed[index]
            for index in indices
        )
        if abs(invoice_published - gross_total) > 1e-6:
            # Each invoice owns these indices exclusively, so the exact closure
            # can be restored on its last hour without affecting another invoice.
            fixed[indices[-1]] += gross_total - invoice_published
            invoice_published = sum(
                heating[index] + dhw[index] + fixed[index]
                for index in indices
            )
        if abs(invoice_published - gross_total) > 1e-6:
            raise CanonicalCostError(
                "Koszt godzinowy nie domyka się do brutto faktury."
            )

        applied += 1
        invoiced_gross += gross_total
        published_invoiced += invoice_published
        latest_applied_end = end
        prior_applied_end = end

    conversion = float(conversion_factor_kwh_m3)
    gas_rate = float(gas_rate_net_pln_kwh)
    dist_var = float(distribution_variable_net_pln_kwh)
    subscription = float(subscription_net_pln_month)
    dist_fixed = float(distribution_fixed_net_pln_month)
    vat = float(vat_rate)
    for value, field in (
        (conversion, "conversion_factor_kwh_m3"),
        (gas_rate, "gas_rate_net_pln_kwh"),
        (dist_var, "distribution_variable_net_pln_kwh"),
        (subscription, "subscription_net_pln_month"),
        (dist_fixed, "distribution_fixed_net_pln_month"),
        (vat, "vat_rate"),
    ):
        if not math.isfinite(value) or value < 0:
            raise CanonicalCostError(
                f"Nieprawidłowa bieżąca stawka kosztowa: {field}."
            )

    provisional_start = latest_applied_end or history_end
    variable_gross_per_m3 = conversion * (gas_rate + dist_var) * (1.0 + vat)
    fixed_month_gross = (subscription + dist_fixed) * (1.0 + vat)
    provisional_total = 0.0

    for index, row in enumerate(rows):
        if row.start < provisional_start:
            continue
        co_cost = max(0.0, float(row.heating_m3)) * variable_gross_per_m3
        dhw_cost = max(0.0, float(row.dhw_m3)) * variable_gross_per_m3
        month_hours = _actual_month_hours(row.start, timezone)
        fixed_cost = 0.0 if month_hours <= 0 else fixed_month_gross / month_hours
        heating[index] += co_cost
        dhw[index] += dhw_cost
        fixed[index] += fixed_cost
        provisional_total += co_cost + dhw_cost + fixed_cost

    unpriced_historical = sum(
        1
        for index, row in enumerate(rows)
        if not invoiced[index]
        and row.start < provisional_start
        and heating[index] + dhw[index] + fixed[index] <= _EPSILON
    )

    result_hours = tuple(
        CanonicalCostHour(
            start=row.start,
            heating_pln=max(0.0, heating[index]),
            dhw_pln=max(0.0, dhw[index]),
            fixed_pln=max(0.0, fixed[index]),
            invoiced=invoiced[index],
        )
        for index, row in enumerate(rows)
    )
    total = sum(row.total_pln for row in result_hours)
    return CanonicalCostResult(
        hours=result_hours,
        billing_period_count=len(periods),
        applied_invoice_count=applied,
        skipped_outside_history_count=skipped_outside,
        skipped_partial_history_count=skipped_partial,
        invoiced_gross_pln=invoiced_gross,
        published_invoiced_pln=published_invoiced,
        invoiced_closure_error_pln=published_invoiced - invoiced_gross,
        provisional_pln=provisional_total,
        total_pln=total,
        latest_applied_invoice_end=latest_applied_end,
        unpriced_historical_hour_count=unpriced_historical,
    )
