"""Diagnostyczny audyt współczynnika konwersji DUON względem danych Ariston."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from decimal import Decimal, ROUND_HALF_UP
import math
from typing import Any, Iterable

_EPSILON = 1e-9
_MANUAL_MATCH_WINDOW_DAYS = 7
_MIN_REFERENCE_SAMPLES = 6


class ConversionAuditError(ValueError):
    """Błąd budowy informacyjnego audytu współczynnika konwersji."""


@dataclass(frozen=True, slots=True)
class _ManualPoint:
    timestamp: datetime
    meter_m3: float


@dataclass(frozen=True, slots=True)
class _AuditSample:
    invoice_number: str
    start: datetime
    end: datetime
    billed_consumption_m3: float
    physical_consumption_m3: float
    invoice_factor_kwh_m3: float
    local_yield_ratio: float
    gross_variable_rate_pln_kwh: float
    reconstructed_gap_hours: int
    interval_count: int


@dataclass(frozen=True, slots=True)
class ConversionAuditResult:
    """Wynik audytu wraz z punktami przeznaczonymi do encji informacyjnej."""

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


def _as_datetime(value: Any, field: str) -> datetime:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as err:
            raise ConversionAuditError(f"Nieprawidłowy czas pola audytu: {field}.") from err
        if parsed.tzinfo is not None:
            return parsed
    raise ConversionAuditError(f"Brak czasu ze strefą dla pola audytu: {field}.")


def _submitted_meter_value(value: float) -> int:
    """Odtwórz całe m3 widoczne na fakturze z dokładnego odczytu lokalnego."""
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _manual_points(readings: Iterable[dict[str, Any]]) -> list[_ManualPoint]:
    points: list[_ManualPoint] = []
    for reading in readings:
        if not isinstance(reading, dict):
            continue
        quality = reading.get("quality")
        if isinstance(quality, dict) and quality.get("exclude_from_estimation", False):
            continue
        try:
            timestamp = _as_datetime(reading.get("timestamp"), "manual.timestamp")
            meter_m3 = _as_float(reading.get("meter_m3"), "manual.meter_m3")
        except ConversionAuditError:
            continue
        if meter_m3 < 0:
            continue
        points.append(_ManualPoint(timestamp=timestamp, meter_m3=meter_m3))
    points.sort(key=lambda item: item.timestamp)
    return points


def _match_manual_point(
    points: list[_ManualPoint],
    invoice_reading: dict[str, Any],
    timezone: tzinfo,
) -> _ManualPoint | None:
    """Znajdź dokładny lokalny odczyt reprezentujący wskazanie z faktury."""
    invoice_day = _as_date(invoice_reading.get("date"), "invoice_reading.date")
    invoice_meter = _as_float(invoice_reading.get("meter_m3"), "invoice_reading.meter_m3")
    invoice_is_whole = abs(invoice_meter - round(invoice_meter)) <= 1e-6

    candidates: list[tuple[int, float, _ManualPoint]] = []
    for point in points:
        local_day = point.timestamp.astimezone(timezone).date()
        day_distance = abs((local_day - invoice_day).days)
        if day_distance > _MANUAL_MATCH_WINDOW_DAYS:
            continue

        if invoice_is_whole:
            same_meter = _submitted_meter_value(point.meter_m3) == int(round(invoice_meter))
        else:
            same_meter = abs(point.meter_m3 - invoice_meter) <= 0.01
        if not same_meter:
            continue

        meter_distance = abs(point.meter_m3 - invoice_meter)
        candidates.append((day_distance, meter_distance, point))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2].timestamp))
    return candidates[0][2]


def _covered_intervals(
    intervals: list[Any],
    start: datetime,
    end: datetime,
) -> list[Any] | None:
    """Zwróć pełny łańcuch kanonicznych przedziałów pomiędzy dokładnymi kotwicami."""
    selected = [
        interval
        for interval in intervals
        if interval.start >= start and interval.end <= end
    ]
    if not selected:
        return None
    selected.sort(key=lambda item: item.start)
    if selected[0].start != start or selected[-1].end != end:
        return None
    cursor = start
    for interval in selected:
        if interval.start != cursor or interval.end <= interval.start:
            return None
        cursor = interval.end
    if cursor != end:
        return None
    return selected


def _sample_from_period(
    intervals: list[Any],
    manual_points: list[_ManualPoint],
    period: dict[str, Any],
    timezone: tzinfo,
) -> _AuditSample | None:
    previous = period.get("previous_reading")
    current = period.get("current_reading")
    if not isinstance(previous, dict) or not isinstance(current, dict):
        raise ConversionAuditError("Faktura nie zawiera pary odczytów gazomierza.")

    start_point = _match_manual_point(manual_points, previous, timezone)
    end_point = _match_manual_point(manual_points, current, timezone)
    if start_point is None or end_point is None:
        return None
    if end_point.timestamp <= start_point.timestamp:
        return None

    covered = _covered_intervals(intervals, start_point.timestamp, end_point.timestamp)
    if covered is None:
        return None

    if any(
        "rollback_unresolved" in set(getattr(interval, "quality", ()) or ())
        for interval in covered
    ):
        return None

    physical_m3 = end_point.meter_m3 - start_point.meter_m3
    if physical_m3 <= _EPSILON:
        return None

    provisional_m3 = sum(float(interval.provisional_m3) for interval in covered)
    if provisional_m3 <= _EPSILON:
        return None

    billed_consumption = _as_float(period.get("consumption_m3"), "consumption_m3")
    if billed_consumption <= _EPSILON:
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

    local_yield_ratio = provisional_m3 / physical_m3
    if not math.isfinite(local_yield_ratio) or local_yield_ratio <= _EPSILON:
        return None

    return _AuditSample(
        invoice_number=str(period.get("invoice_number") or ""),
        start=start_point.timestamp,
        end=end_point.timestamp,
        billed_consumption_m3=billed_consumption,
        physical_consumption_m3=physical_m3,
        invoice_factor_kwh_m3=invoice_factor,
        local_yield_ratio=local_yield_ratio,
        gross_variable_rate_pln_kwh=(gas_rate + dist_rate) * (1.0 + vat),
        reconstructed_gap_hours=sum(
            int(getattr(interval, "reconstructed_gap_hours", 0) or 0)
            for interval in covered
        ),
        interval_count=len(covered),
    )


def _weighted_median(values: list[tuple[float, float]]) -> float:
    """Zwróć medianę ważoną; większe zużycie ma większy wpływ na referencję."""
    usable = [
        (value, weight)
        for value, weight in values
        if math.isfinite(value) and math.isfinite(weight) and weight > _EPSILON
    ]
    if not usable:
        raise ConversionAuditError("Brak danych do wyznaczenia referencji audytu.")
    usable.sort(key=lambda item: item[0])
    total_weight = sum(weight for _value, weight in usable)
    threshold = total_weight / 2.0
    cumulative = 0.0
    for value, weight in usable:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return usable[-1][0]


def build_conversion_audit(
    intervals: Iterable[Any],
    billing_periods: Iterable[dict[str, Any]],
    manual_readings: Iterable[dict[str, Any]],
    *,
    timezone: tzinfo,
    calibration_co_m3_per_kwh: float,
    calibration_dhw_m3_per_kwh: float,
    calibration_mae_m3: float | None = None,
) -> ConversionAuditResult:
    """Porównaj względny dryf współczynnika DUON z lokalnym profilem Ariston.

    Audyt używa wyłącznie faktur, których oba wskazania gazomierza można powiązać
    z dokładnymi ręcznymi odczytami. Lokalny wskaźnik energetyczny pochodzi z
    kanonicznego ``provisional_m3`` wyliczonego z Ariston i zamrożonej kalibracji
    CO/CWU przed normalizacją do rzeczywistego gazomierza.

    Stały poziom bezwzględny nie jest mierzalny bez niezależnego kalorymetru.
    Dlatego referencja kWh/m3 jest stałą medianą ważoną historycznej relacji DUON
    do lokalnego wskaźnika. Wynik pokazuje odchylenie/dryf względem tej relacji.
    Znak jest z perspektywy użytkownika: dodatni oznacza korzyść, ujemny stratę.
    """
    rows = sorted(list(intervals), key=lambda item: item.start)
    if not rows:
        raise ConversionAuditError("Brak kanonicznych przedziałów do audytu konwersji.")

    co_coeff = _as_float(calibration_co_m3_per_kwh, "calibration_co_m3_per_kwh")
    dhw_coeff = _as_float(calibration_dhw_m3_per_kwh, "calibration_dhw_m3_per_kwh")
    if co_coeff <= _EPSILON or dhw_coeff <= _EPSILON:
        raise ConversionAuditError("Brak poprawnej kalibracji CO/CWU do audytu.")

    manual = _manual_points(manual_readings)
    if len(manual) < 2:
        raise ConversionAuditError("Brak dokładnych ręcznych odczytów do audytu.")

    periods = [item for item in billing_periods if isinstance(item, dict)]
    periods.sort(
        key=lambda item: str((item.get("current_reading") or {}).get("date") or "")
    )

    samples: list[_AuditSample] = []
    skipped_without_exact_manual_bounds = 0
    skipped_invalid = 0
    for period in periods:
        try:
            sample = _sample_from_period(rows, manual, period, timezone)
        except ConversionAuditError:
            skipped_invalid += 1
            continue
        if sample is None:
            skipped_without_exact_manual_bounds += 1
            continue
        samples.append(sample)

    if len(samples) < _MIN_REFERENCE_SAMPLES:
        raise ConversionAuditError(
            "Za mało faktur z dwiema dokładnymi ręcznymi granicami do audytu: "
            f"{len(samples)} < {_MIN_REFERENCE_SAMPLES}."
        )

    reference_candidates = [
        (
            sample.invoice_factor_kwh_m3 / sample.local_yield_ratio,
            sample.physical_consumption_m3,
        )
        for sample in samples
    ]
    reference_factor = _weighted_median(reference_candidates)
    if reference_factor <= _EPSILON:
        raise ConversionAuditError("Wyznaczona referencja współczynnika jest nieprawidłowa.")

    history: list[dict[str, Any]] = []
    cumulative_pln = 0.0
    for sample in samples:
        local_factor = reference_factor * sample.local_yield_ratio
        factor_delta = local_factor - sample.invoice_factor_kwh_m3
        difference_kwh = sample.billed_consumption_m3 * factor_delta
        difference_pln = difference_kwh * sample.gross_variable_rate_pln_kwh
        cumulative_pln += difference_pln
        factor_diff_percent = (1.0 - sample.invoice_factor_kwh_m3 / local_factor) * 100.0
        local_yield_percent = (sample.local_yield_ratio - 1.0) * 100.0

        history.append(
            {
                "invoice_number": sample.invoice_number,
                "start": sample.start.isoformat(),
                "end": sample.end.isoformat(),
                "duon_kwh_m3": round(sample.invoice_factor_kwh_m3, 6),
                "local_model_kwh_m3": round(local_factor, 6),
                "reference_kwh_m3": round(reference_factor, 6),
                "local_yield_ratio": round(sample.local_yield_ratio, 9),
                "local_yield_percent": round(local_yield_percent, 4),
                "factor_difference_percent": round(factor_diff_percent, 4),
                "difference_kwh": round(difference_kwh, 6),
                "difference_pln": round(difference_pln, 6),
                "cumulative_pln": round(cumulative_pln, 6),
                "billed_consumption_m3": round(sample.billed_consumption_m3, 6),
                "physical_consumption_m3": round(sample.physical_consumption_m3, 6),
                "reconstructed_gap_hours": sample.reconstructed_gap_hours,
                "canonical_interval_count": sample.interval_count,
            }
        )

    last = history[-1]
    candidate_values = [value for value, _weight in reference_candidates]
    mae = None
    if calibration_mae_m3 is not None:
        try:
            mae = _as_float(calibration_mae_m3, "calibration_mae_m3")
        except ConversionAuditError:
            mae = None

    return ConversionAuditResult(
        data={
            "status": "ok",
            "method": "exact_manual_boundaries_fixed_calibration_weighted_median_reference",
            "sample_count": len(samples),
            "reference_sample_count": len(samples),
            "reference_factor_kwh_m3": round(reference_factor, 9),
            "reference_candidate_min_kwh_m3": round(min(candidate_values), 9),
            "reference_candidate_max_kwh_m3": round(max(candidate_values), 9),
            "calibration_co_m3_per_kwh": round(co_coeff, 12),
            "calibration_dhw_m3_per_kwh": round(dhw_coeff, 12),
            "calibration_mae_m3": None if mae is None else round(mae, 9),
            "skipped_without_exact_manual_bounds_count": (
                skipped_without_exact_manual_bounds
            ),
            "skipped_invalid_count": skipped_invalid,
            "cumulative_difference_pln": round(cumulative_pln, 6),
            "last_difference_pln": last["difference_pln"],
            "last_duon_kwh_m3": last["duon_kwh_m3"],
            "last_local_model_kwh_m3": last["local_model_kwh_m3"],
            "last_local_yield_percent": last["local_yield_percent"],
            "last_factor_difference_percent": last["factor_difference_percent"],
            "last_start": last["start"],
            "last_end": last["end"],
            "history": history,
        }
    )