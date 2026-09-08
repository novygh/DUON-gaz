"""Diagnostyczny audyt współczynnika konwersji DUON względem danych Ariston."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
import math
from statistics import median
from typing import Any, Iterable

_EPSILON = 1e-9
_ONE_HOUR = timedelta(hours=1)
_MIN_MODEL_SAMPLES = 6
_MIN_MODEL_COEFF = 0.5
_MAX_MODEL_COEFF = 2.5


class ConversionAuditError(ValueError):
    """Błąd budowy informacyjnego audytu współczynnika konwersji."""


@dataclass(frozen=True, slots=True)
class _AuditSample:
    invoice_number: str
    start: datetime
    end: datetime
    consumption_m3: float
    billed_kwh: float
    invoice_factor_kwh_m3: float
    heating_kwh: float
    dhw_kwh: float
    gross_variable_rate_pln_kwh: float
    reconstructed_gap_hours: int


@dataclass(frozen=True, slots=True)
class ConversionAuditResult:
    """Wynik modelu wraz z punktami przeznaczonymi do encji informacyjnej."""

    data: dict[str, Any]


def _as_float(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as err:
        raise ConversionAuditError(f"Nieprawidłowa wartość pola audytu: {field}.") from err
    if not math.isfinite(result):
        raise ConversionAuditError(f"Nienumeryczna wartość pola audytu: {field}.")
    return result


def _as_date(value: Any, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as err:
            raise ConversionAuditError(f"Nieprawidłowa data pola audytu: {field}.") from err
    raise ConversionAuditError(f"Brak daty pola audytu: {field}.")


def _reading_bounds(period: dict[str, Any], timezone: tzinfo) -> tuple[datetime, datetime]:
    """Ustaw granice odczytów faktury w lokalnym południu, zgodnie z importerem."""
    previous = period.get("previous_reading")
    current = period.get("current_reading")
    if not isinstance(previous, dict) or not isinstance(current, dict):
        raise ConversionAuditError("Faktura nie zawiera pary odczytów gazomierza.")

    start_day = _as_date(previous.get("date"), "previous_reading.date")
    end_day = _as_date(current.get("date"), "current_reading.date")
    start = datetime.combine(start_day, time(hour=12), tzinfo=timezone)
    end = datetime.combine(end_day, time(hour=12), tzinfo=timezone)
    if end <= start:
        raise ConversionAuditError("Przedział odczytów faktury nie jest rosnący.")
    return start, end


def _overlap_fraction(
    row_start: datetime,
    row_end: datetime,
    interval_start: datetime,
    interval_end: datetime,
) -> float:
    start = max(row_start, interval_start)
    end = min(row_end, interval_end)
    if end <= start:
        return 0.0
    return (end - start).total_seconds() / (row_end - row_start).total_seconds()


def _sample_from_period(
    hours: list[Any],
    period: dict[str, Any],
    timezone: tzinfo,
) -> _AuditSample | None:
    start, end = _reading_bounds(period, timezone)
    history_start = hours[0].start
    history_end = hours[-1].start + _ONE_HOUR
    if start < history_start or end > history_end:
        return None

    heating_kwh = 0.0
    dhw_kwh = 0.0
    overlap_hours = 0.0
    gap_hours: set[datetime] = set()
    unresolved = False

    for row in hours:
        row_end = row.start + _ONE_HOUR
        if row_end <= start:
            continue
        if row.start >= end:
            break
        fraction = _overlap_fraction(row.start, row_end, start, end)
        if fraction <= 0:
            continue
        heating_kwh += max(0.0, float(row.heating_kwh)) * fraction
        dhw_kwh += max(0.0, float(row.dhw_kwh)) * fraction
        overlap_hours += fraction
        quality = set(getattr(row, "quality", ()) or ())
        if "gap_estimate" in quality:
            gap_hours.add(row.start)
        if "rollback_unresolved" in quality:
            unresolved = True

    expected_hours = (end - start).total_seconds() / 3600.0
    if abs(overlap_hours - expected_hours) > 1e-6 or unresolved:
        return None

    consumption_m3 = _as_float(period.get("consumption_m3"), "consumption_m3")
    billed_kwh = _as_float(period.get("billed_energy_kwh"), "billed_energy_kwh")
    if consumption_m3 <= _EPSILON or billed_kwh <= _EPSILON:
        return None

    factor_value = period.get("conversion_factor_kwh_m3")
    if factor_value is None:
        factor_value = period.get("conversion_factor")
    invoice_factor = _as_float(factor_value, "conversion_factor_kwh_m3")
    if invoice_factor <= _EPSILON:
        return None

    gas_rate = _as_float(period.get("gas_rate_net_pln_kwh"), "gas_rate_net_pln_kwh")
    dist_rate = _as_float(
        period.get("distribution_variable_net_pln_kwh"),
        "distribution_variable_net_pln_kwh",
    )
    vat = _as_float(period.get("vat_rate"), "vat_rate")
    if gas_rate < 0 or dist_rate < 0 or vat < 0:
        return None

    local_kwh = heating_kwh + dhw_kwh
    if local_kwh <= _EPSILON:
        return None

    return _AuditSample(
        invoice_number=str(period.get("invoice_number") or ""),
        start=start,
        end=end,
        consumption_m3=consumption_m3,
        billed_kwh=billed_kwh,
        invoice_factor_kwh_m3=invoice_factor,
        heating_kwh=heating_kwh,
        dhw_kwh=dhw_kwh,
        gross_variable_rate_pln_kwh=(gas_rate + dist_rate) * (1.0 + vat),
        reconstructed_gap_hours=len(gap_hours),
    )


def _solve_two_component(
    rows: list[tuple[float, float, float]],
    weights: list[float] | None = None,
) -> tuple[float, float] | None:
    if len(rows) < 2:
        return None
    if weights is None:
        weights = [1.0] * len(rows)

    s_cc = s_dd = s_cd = s_ct = s_dt = 0.0
    for (co_kwh, dhw_kwh, target_kwh), weight in zip(rows, weights, strict=True):
        s_cc += weight * co_kwh * co_kwh
        s_dd += weight * dhw_kwh * dhw_kwh
        s_cd += weight * co_kwh * dhw_kwh
        s_ct += weight * co_kwh * target_kwh
        s_dt += weight * dhw_kwh * target_kwh

    determinant = s_cc * s_dd - s_cd * s_cd
    scale = max(s_cc * s_dd, 1.0)
    if determinant <= scale * 1e-12:
        return None

    co_coeff = (s_ct * s_dd - s_dt * s_cd) / determinant
    dhw_coeff = (s_dt * s_cc - s_ct * s_cd) / determinant
    return co_coeff, dhw_coeff


def _robust_fit(samples: list[_AuditSample]) -> tuple[float, float, float]:
    rows = [(item.heating_kwh, item.dhw_kwh, item.billed_kwh) for item in samples]
    solution = _solve_two_component(rows)
    if solution is None:
        raise ConversionAuditError("Dane CO/CWU nie pozwalają wyznaczyć modelu audytu.")

    co_coeff, dhw_coeff = solution
    residuals = [
        target - (co_coeff * co + dhw_coeff * dhw)
        for co, dhw, target in rows
    ]
    center = median(residuals)
    mad = median(abs(value - center) for value in residuals)

    if mad > _EPSILON:
        sigma = 1.4826 * mad
        threshold = max(2.0, 1.5 * sigma)
        weights = [
            1.0 if abs(value - center) <= threshold else threshold / abs(value - center)
            for value in residuals
        ]
        refined = _solve_two_component(rows, weights)
        if refined is not None:
            co_coeff, dhw_coeff = refined

    if not (
        _MIN_MODEL_COEFF <= co_coeff <= _MAX_MODEL_COEFF
        and _MIN_MODEL_COEFF <= dhw_coeff <= _MAX_MODEL_COEFF
    ):
        raise ConversionAuditError("Współczynniki lokalnego modelu energii są niewiarygodne.")

    residuals = [
        target - (co_coeff * co + dhw_coeff * dhw)
        for co, dhw, target in rows
    ]
    mae = sum(abs(value) for value in residuals) / len(residuals)
    return co_coeff, dhw_coeff, mae


def build_conversion_audit(
    hours: Iterable[Any],
    billing_periods: Iterable[dict[str, Any]],
    *,
    timezone: tzinfo,
) -> ConversionAuditResult:
    """Porównaj energię rozliczeniową faktur z lokalnym modelem opartym o Ariston.

    Model nie jest pomiarem laboratoryjnym ciepła spalania. Stałą relację między
    energią raportowaną przez Ariston a energią rozliczeniową usuwa dwuskładnikowy
    model CO/CWU. Wynik pokazuje wyłącznie odchylenie faktur od tej lokalnej relacji.
    """
    rows = sorted(list(hours), key=lambda row: row.start)
    if len(rows) < 2:
        raise ConversionAuditError("Brak historii kanonicznej do audytu konwersji.")

    periods = [item for item in billing_periods if isinstance(item, dict)]
    periods.sort(key=lambda item: str((item.get("current_reading") or {}).get("date") or ""))

    samples: list[_AuditSample] = []
    skipped_outside_or_incomplete = 0
    skipped_invalid = 0
    for period in periods:
        try:
            sample = _sample_from_period(rows, period, timezone)
        except ConversionAuditError:
            skipped_invalid += 1
            continue
        if sample is None:
            skipped_outside_or_incomplete += 1
            continue
        samples.append(sample)

    if len(samples) < _MIN_MODEL_SAMPLES:
        raise ConversionAuditError(
            f"Za mało pełnych okresów do audytu konwersji: {len(samples)} < {_MIN_MODEL_SAMPLES}."
        )

    co_coeff, dhw_coeff, mae_kwh = _robust_fit(samples)
    history: list[dict[str, Any]] = []
    cumulative_pln = 0.0

    for sample in samples:
        predicted_kwh = (
            co_coeff * sample.heating_kwh
            + dhw_coeff * sample.dhw_kwh
        )
        if predicted_kwh <= _EPSILON:
            continue
        residual_kwh = sample.billed_kwh - predicted_kwh
        delta_pln = residual_kwh * sample.gross_variable_rate_pln_kwh
        cumulative_pln += delta_pln
        local_factor = predicted_kwh / sample.consumption_m3
        raw_local_yield = (
            sample.heating_kwh + sample.dhw_kwh
        ) / sample.consumption_m3
        apparent_efficiency = (
            (sample.heating_kwh + sample.dhw_kwh) / sample.billed_kwh * 100.0
        )
        factor_diff_percent = (
            (sample.invoice_factor_kwh_m3 / local_factor - 1.0) * 100.0
        )

        history.append(
            {
                "invoice_number": sample.invoice_number,
                "start": sample.start.isoformat(),
                "end": sample.end.isoformat(),
                "duon_kwh_m3": round(sample.invoice_factor_kwh_m3, 6),
                "local_model_kwh_m3": round(local_factor, 6),
                "raw_ariston_kwh_m3": round(raw_local_yield, 6),
                "apparent_efficiency_percent": round(apparent_efficiency, 4),
                "factor_difference_percent": round(factor_diff_percent, 4),
                "difference_kwh": round(residual_kwh, 6),
                "difference_pln": round(delta_pln, 6),
                "cumulative_pln": round(cumulative_pln, 6),
                "reconstructed_gap_hours": sample.reconstructed_gap_hours,
            }
        )

    if not history:
        raise ConversionAuditError("Audyt konwersji nie utworzył żadnego punktu historii.")

    last = history[-1]
    return ConversionAuditResult(
        data={
            "status": "ok",
            "method": "robust_two_component_local_energy_model",
            "sample_count": len(samples),
            "model_co_billed_kwh_per_ariston_kwh": round(co_coeff, 9),
            "model_dhw_billed_kwh_per_ariston_kwh": round(dhw_coeff, 9),
            "model_co_apparent_efficiency_percent": round(100.0 / co_coeff, 4),
            "model_dhw_apparent_efficiency_percent": round(100.0 / dhw_coeff, 4),
            "model_mae_kwh": round(mae_kwh, 6),
            "skipped_outside_or_incomplete_count": skipped_outside_or_incomplete,
            "skipped_invalid_count": skipped_invalid,
            "cumulative_difference_pln": round(cumulative_pln, 6),
            "last_difference_pln": last["difference_pln"],
            "last_duon_kwh_m3": last["duon_kwh_m3"],
            "last_local_model_kwh_m3": last["local_model_kwh_m3"],
            "last_raw_ariston_kwh_m3": last["raw_ariston_kwh_m3"],
            "last_apparent_efficiency_percent": last[
                "apparent_efficiency_percent"
            ],
            "last_factor_difference_percent": last[
                "factor_difference_percent"
            ],
            "last_start": last["start"],
            "last_end": last["end"],
            "history": history,
        }
    )
