"""Pure canonical history reconstruction for DUON Gaz."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
import math
from statistics import fmean
from typing import Iterable

_EPSILON = 1e-9
_ONE_HOUR = timedelta(hours=1)


class CanonicalHistoryError(ValueError):
    """Raised when canonical history cannot be reconstructed safely."""


@dataclass(frozen=True, slots=True)
class SourcePoint:
    """One coherent cumulative Recorder point for CO and CWU."""

    timestamp: datetime
    heating_sum_kwh: float
    dhw_sum_kwh: float


@dataclass(frozen=True, slots=True)
class PhysicalAnchor:
    """One physical/billing meter anchor used to constrain the model."""

    timestamp: datetime
    meter_m3: float
    source: str = "manual"
    timestamp_precision: str = "exact"


@dataclass(frozen=True, slots=True)
class CanonicalHour:
    """One canonical hourly DUON usage row."""

    start: datetime
    heating_kwh: float
    dhw_kwh: float
    heating_m3: float
    dhw_m3: float
    unattributed_m3: float
    gas_m3: float
    cumulative_m3: float
    quality: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CanonicalInterval:
    """Audit summary for one meter-anchor interval."""

    start: datetime
    end: datetime
    start_meter_m3: float
    end_meter_m3: float
    physical_delta_m3: float
    provisional_m3: float
    scale_factor: float | None
    reconstructed_gap_hours: int
    rollback_correction_kwh: float
    rollback_retracted_kwh: float
    unresolved_rollback_kwh: float
    quality: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CanonicalHistoryResult:
    """Canonical hourly history plus interval-level audit metadata."""

    hours: tuple[CanonicalHour, ...]
    intervals: tuple[CanonicalInterval, ...]


@dataclass(frozen=True, slots=True)
class CanonicalHistorySettings:
    """Generic reconstruction knobs independent from one installation."""

    gap_profile_window: timedelta = timedelta(days=21)
    min_profile_samples: int = 3
    low_scale_min: float = 0.75
    low_scale_max: float = 1.25


@dataclass(slots=True)
class _ExpandedHour:
    start: datetime
    end: datetime
    heating_kwh: float
    dhw_kwh: float
    flags: set[str]
    heating_retracted_kwh: float = 0.0
    dhw_retracted_kwh: float = 0.0
    heating_unresolved_kwh: float = 0.0
    dhw_unresolved_kwh: float = 0.0
    rollback_correction_kwh: float = 0.0


@dataclass(slots=True)
class _HourAccumulator:
    heating_kwh: float = 0.0
    dhw_kwh: float = 0.0
    heating_m3: float = 0.0
    dhw_m3: float = 0.0
    unattributed_m3: float = 0.0
    flags: set[str] | None = None

    def __post_init__(self) -> None:
        if self.flags is None:
            self.flags = set()


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CanonicalHistoryError(f"{field} must be timezone-aware")


def _validate_points(points: list[SourcePoint]) -> None:
    if len(points) < 2:
        raise CanonicalHistoryError("at least two coherent Recorder points are required")
    previous = None
    for index, point in enumerate(points):
        _require_aware(point.timestamp, f"source point #{index} timestamp")
        if not (
            math.isfinite(point.heating_sum_kwh)
            and math.isfinite(point.dhw_sum_kwh)
        ):
            raise CanonicalHistoryError("Recorder cumulative sums must be finite")
        if previous is not None and point.timestamp <= previous:
            raise CanonicalHistoryError("Recorder points must be strictly increasing")
        previous = point.timestamp


def _validate_anchors(anchors: list[PhysicalAnchor]) -> None:
    if len(anchors) < 2:
        raise CanonicalHistoryError("at least two physical meter anchors are required")
    previous_time = None
    previous_meter = None
    for index, anchor in enumerate(anchors):
        _require_aware(anchor.timestamp, f"anchor #{index} timestamp")
        if not math.isfinite(anchor.meter_m3):
            raise CanonicalHistoryError("physical meter values must be finite")
        if previous_time is not None and anchor.timestamp <= previous_time:
            raise CanonicalHistoryError("physical anchors must be strictly increasing")
        if previous_meter is not None and anchor.meter_m3 < previous_meter - _EPSILON:
            raise CanonicalHistoryError("physical meter value cannot decrease")
        previous_time = anchor.timestamp
        previous_meter = anchor.meter_m3


def _whole_hours(start: datetime, end: datetime) -> int:
    seconds = (end - start).total_seconds()
    hours = int(round(seconds / 3600.0))
    if hours < 1 or abs(seconds - hours * 3600.0) > 1.0:
        raise CanonicalHistoryError(
            "Recorder points must lie on a coherent hourly cadence"
        )
    return hours


def _repair_negative_deltas(
    raw_deltas: list[float],
) -> tuple[list[float], list[float], list[float], list[float]]:
    """Turn cumulative corrections into non-negative usage without losing net total.

    A negative delta retracts the most recent positive usage first. This models
    Recorder/source rollbacks as corrections to earlier over-counted usage while
    retaining explicit audit amounts.
    """
    amounts = [max(0.0, float(delta)) for delta in raw_deltas]
    retracted = [0.0] * len(raw_deltas)
    unresolved = [0.0] * len(raw_deltas)
    corrections = [max(0.0, -float(delta)) for delta in raw_deltas]
    positive_stack: list[int] = []

    for index, delta in enumerate(raw_deltas):
        if delta > _EPSILON:
            positive_stack.append(index)
            continue
        if delta >= -_EPSILON:
            continue

        debt = -float(delta)
        while debt > _EPSILON and positive_stack:
            prior = positive_stack[-1]
            take = min(amounts[prior], debt)
            amounts[prior] -= take
            retracted[prior] += take
            debt -= take
            if amounts[prior] <= _EPSILON:
                amounts[prior] = 0.0
                positive_stack.pop()
        if debt > _EPSILON:
            unresolved[index] = debt

    return amounts, retracted, unresolved, corrections


class _Profile:
    def __init__(
        self,
        samples: list[tuple[datetime, float]],
        timezone: tzinfo,
        settings: CanonicalHistorySettings,
    ) -> None:
        self.timezone = timezone
        self.settings = settings
        self.by_hour: dict[int, list[tuple[datetime, float]]] = {
            hour: [] for hour in range(24)
        }
        self.all_values: list[float] = []
        for timestamp, value in samples:
            clean = max(0.0, float(value))
            self.by_hour[timestamp.astimezone(timezone).hour].append((timestamp, clean))
            self.all_values.append(clean)

    def value(self, target: datetime) -> float:
        hour = target.astimezone(self.timezone).hour
        window_seconds = self.settings.gap_profile_window.total_seconds()
        nearby = [
            value
            for timestamp, value in self.by_hour[hour]
            if abs((timestamp - target).total_seconds()) <= window_seconds
        ]
        if len(nearby) >= self.settings.min_profile_samples:
            return max(0.0, fmean(nearby))

        same_hour = [value for _timestamp, value in self.by_hour[hour]]
        if same_hour:
            return max(0.0, fmean(same_hour))
        if self.all_values:
            return max(0.0, fmean(self.all_values))
        return 0.0


def _build_expanded_hours(
    points: list[SourcePoint],
    timezone: tzinfo,
    settings: CanonicalHistorySettings,
) -> tuple[list[_ExpandedHour], _Profile, _Profile]:
    raw_heating = [
        points[index + 1].heating_sum_kwh - points[index].heating_sum_kwh
        for index in range(len(points) - 1)
    ]
    raw_dhw = [
        points[index + 1].dhw_sum_kwh - points[index].dhw_sum_kwh
        for index in range(len(points) - 1)
    ]
    (
        heating,
        heating_retracted,
        heating_unresolved,
        heating_corrections,
    ) = _repair_negative_deltas(raw_heating)
    (
        dhw,
        dhw_retracted,
        dhw_unresolved,
        dhw_corrections,
    ) = _repair_negative_deltas(raw_dhw)

    clean_heating_samples: list[tuple[datetime, float]] = []
    clean_dhw_samples: list[tuple[datetime, float]] = []
    durations: list[int] = []

    for index in range(len(points) - 1):
        duration = _whole_hours(points[index].timestamp, points[index + 1].timestamp)
        durations.append(duration)
        if duration != 1:
            continue
        if (
            heating_retracted[index] <= _EPSILON
            and heating_unresolved[index] <= _EPSILON
        ):
            clean_heating_samples.append((points[index].timestamp, heating[index]))
        if dhw_retracted[index] <= _EPSILON and dhw_unresolved[index] <= _EPSILON:
            clean_dhw_samples.append((points[index].timestamp, dhw[index]))

    heating_profile = _Profile(clean_heating_samples, timezone, settings)
    dhw_profile = _Profile(clean_dhw_samples, timezone, settings)

    result: list[_ExpandedHour] = []
    for index, duration in enumerate(durations):
        start = points[index].timestamp
        end = points[index + 1].timestamp
        base_flags: set[str] = set()
        if heating_retracted[index] > _EPSILON or dhw_retracted[index] > _EPSILON:
            base_flags.add("rollback_reallocated")
        if heating_corrections[index] > _EPSILON or dhw_corrections[index] > _EPSILON:
            base_flags.add("rollback_event")
        if heating_unresolved[index] > _EPSILON or dhw_unresolved[index] > _EPSILON:
            base_flags.add("rollback_unresolved")

        if duration == 1:
            result.append(
                _ExpandedHour(
                    start=start,
                    end=end,
                    heating_kwh=heating[index],
                    dhw_kwh=dhw[index],
                    flags=set(base_flags),
                    heating_retracted_kwh=heating_retracted[index],
                    dhw_retracted_kwh=dhw_retracted[index],
                    heating_unresolved_kwh=heating_unresolved[index],
                    dhw_unresolved_kwh=dhw_unresolved[index],
                    rollback_correction_kwh=(
                        heating_corrections[index] + dhw_corrections[index]
                    ),
                )
            )
            continue

        # Missing Recorder rows are not assumed to be represented by the next
        # cumulative delta. The observed delta is kept in the final hour and
        # every absent hour is reconstructed from a local hour-of-day profile.
        for offset in range(duration - 1):
            missing_start = start + offset * _ONE_HOUR
            result.append(
                _ExpandedHour(
                    start=missing_start,
                    end=missing_start + _ONE_HOUR,
                    heating_kwh=heating_profile.value(missing_start),
                    dhw_kwh=dhw_profile.value(missing_start),
                    flags={"gap_estimate"},
                )
            )

        observed_start = end - _ONE_HOUR
        observed_flags = set(base_flags)
        observed_flags.add("gap_observed_tail")
        result.append(
            _ExpandedHour(
                start=observed_start,
                end=end,
                heating_kwh=heating[index],
                dhw_kwh=dhw[index],
                flags=observed_flags,
                heating_retracted_kwh=heating_retracted[index],
                dhw_retracted_kwh=dhw_retracted[index],
                heating_unresolved_kwh=heating_unresolved[index],
                dhw_unresolved_kwh=dhw_unresolved[index],
                rollback_correction_kwh=(
                    heating_corrections[index] + dhw_corrections[index]
                ),
            )
        )

    return result, heating_profile, dhw_profile


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


def build_canonical_history(
    source_points: Iterable[SourcePoint],
    physical_anchors: Iterable[PhysicalAnchor],
    *,
    heating_m3_per_kwh: float,
    dhw_m3_per_kwh: float,
    timezone: tzinfo,
    settings: CanonicalHistorySettings | None = None,
) -> CanonicalHistoryResult:
    """Reconstruct an auditable, meter-constrained hourly gas history.

    Raw Recorder statistics are never modified. Negative cumulative corrections
    are reallocated backward, missing Recorder hours are estimated from a local
    hour-of-day profile, and each physical meter interval is finally normalized
    so its hourly gas total equals the authoritative meter delta exactly.
    """
    if not (
        math.isfinite(heating_m3_per_kwh) and math.isfinite(dhw_m3_per_kwh)
    ):
        raise CanonicalHistoryError("calibration coefficients must be finite")
    if heating_m3_per_kwh <= 0 or dhw_m3_per_kwh <= 0:
        raise CanonicalHistoryError("calibration coefficients must be positive")

    cfg = settings or CanonicalHistorySettings()
    points = sorted(list(source_points), key=lambda point: point.timestamp)
    anchors = sorted(list(physical_anchors), key=lambda anchor: anchor.timestamp)
    _validate_points(points)
    _validate_anchors(anchors)

    expanded, heating_profile, dhw_profile = _build_expanded_hours(
        points, timezone, cfg
    )
    if not expanded:
        raise CanonicalHistoryError("no hourly Recorder profile could be built")
    if (
        expanded[0].start > anchors[0].timestamp
        or expanded[-1].end < anchors[-1].timestamp
    ):
        raise CanonicalHistoryError(
            "Recorder history does not cover all physical anchors"
        )

    accumulators: dict[datetime, _HourAccumulator] = {}
    interval_results: list[CanonicalInterval] = []

    for start_anchor, end_anchor in zip(anchors, anchors[1:]):
        physical_delta = end_anchor.meter_m3 - start_anchor.meter_m3
        pieces: list[tuple[_ExpandedHour, float, float, float]] = []
        provisional = 0.0
        gap_hours: set[datetime] = set()
        rollback_correction = 0.0
        rollback_retracted = 0.0
        rollback_unresolved = 0.0

        for row in expanded:
            if row.end <= start_anchor.timestamp:
                continue
            if row.start >= end_anchor.timestamp:
                break
            fraction = _overlap_fraction(
                row.start,
                row.end,
                start_anchor.timestamp,
                end_anchor.timestamp,
            )
            if fraction <= 0:
                continue
            heating_kwh = row.heating_kwh * fraction
            dhw_kwh = row.dhw_kwh * fraction
            pieces.append((row, fraction, heating_kwh, dhw_kwh))
            provisional += (
                heating_kwh * heating_m3_per_kwh
                + dhw_kwh * dhw_m3_per_kwh
            )
            if "gap_estimate" in row.flags:
                gap_hours.add(row.start)
            rollback_correction += fraction * row.rollback_correction_kwh
            rollback_retracted += fraction * (
                row.heating_retracted_kwh + row.dhw_retracted_kwh
            )
            rollback_unresolved += fraction * (
                row.heating_unresolved_kwh + row.dhw_unresolved_kwh
            )

        if not pieces:
            raise CanonicalHistoryError(
                "no Recorder profile overlaps a physical meter interval"
            )

        interval_flags: set[str] = {"normalized_to_meter"}
        if gap_hours:
            interval_flags.add("reconstructed_gap")
        if rollback_correction > _EPSILON:
            interval_flags.add("rollback_event")
        if rollback_retracted > _EPSILON:
            interval_flags.add("rollback_reallocated")
        if rollback_unresolved > _EPSILON:
            interval_flags.add("rollback_unresolved")
        if (
            start_anchor.timestamp_precision != "exact"
            or end_anchor.timestamp_precision != "exact"
        ):
            interval_flags.add("uncertain_anchor_time")

        use_profile_fallback = provisional <= _EPSILON and physical_delta > _EPSILON
        profile_weights: list[tuple[float, float]] = []
        if use_profile_fallback:
            interval_flags.add("profile_fallback")
            fallback_total = 0.0
            for row, fraction, _heating_kwh, _dhw_kwh in pieces:
                heating_expected = heating_profile.value(row.start) * fraction
                dhw_expected = dhw_profile.value(row.start) * fraction
                profile_weights.append((heating_expected, dhw_expected))
                fallback_total += (
                    heating_expected * heating_m3_per_kwh
                    + dhw_expected * dhw_m3_per_kwh
                )
            if fallback_total > _EPSILON:
                provisional = fallback_total
            else:
                interval_flags.add("unattributed_usage")

        if physical_delta <= _EPSILON and provisional <= _EPSILON:
            scale: float | None = 1.0
        elif provisional > _EPSILON:
            scale = physical_delta / provisional
        else:
            scale = None

        if scale is not None and (
            scale < cfg.low_scale_min or scale > cfg.low_scale_max
        ):
            interval_flags.add("low_confidence_scale")

        duration_weight_sum = sum(piece[1] for piece in pieces)
        for piece_index, (row, fraction, heating_kwh, dhw_kwh) in enumerate(pieces):
            acc = accumulators.setdefault(row.start, _HourAccumulator())
            acc.flags.update(row.flags)
            acc.flags.update(interval_flags)

            if use_profile_fallback and scale is not None:
                heating_kwh, dhw_kwh = profile_weights[piece_index]

            acc.heating_kwh += heating_kwh
            acc.dhw_kwh += dhw_kwh

            if scale is not None:
                acc.heating_m3 += heating_kwh * heating_m3_per_kwh * scale
                acc.dhw_m3 += dhw_kwh * dhw_m3_per_kwh * scale
            elif physical_delta > _EPSILON:
                # There is no defensible CO/CWU split. Preserve only the meter
                # total instead of fabricating a component allocation.
                duration_weight = fraction / duration_weight_sum
                acc.unattributed_m3 += physical_delta * duration_weight

        interval_results.append(
            CanonicalInterval(
                start=start_anchor.timestamp,
                end=end_anchor.timestamp,
                start_meter_m3=start_anchor.meter_m3,
                end_meter_m3=end_anchor.meter_m3,
                physical_delta_m3=physical_delta,
                provisional_m3=provisional,
                scale_factor=scale,
                reconstructed_gap_hours=len(gap_hours),
                rollback_correction_kwh=rollback_correction,
                rollback_retracted_kwh=rollback_retracted,
                unresolved_rollback_kwh=rollback_unresolved,
                quality=tuple(sorted(interval_flags)),
            )
        )

    cumulative = anchors[0].meter_m3
    hour_results: list[CanonicalHour] = []
    for start in sorted(accumulators):
        acc = accumulators[start]
        gas_m3 = acc.heating_m3 + acc.dhw_m3 + acc.unattributed_m3
        cumulative += gas_m3
        hour_results.append(
            CanonicalHour(
                start=start,
                heating_kwh=acc.heating_kwh,
                dhw_kwh=acc.dhw_kwh,
                heating_m3=acc.heating_m3,
                dhw_m3=acc.dhw_m3,
                unattributed_m3=acc.unattributed_m3,
                gas_m3=gas_m3,
                cumulative_m3=cumulative,
                quality=tuple(sorted(acc.flags)),
            )
        )

    expected_end = anchors[-1].meter_m3
    if abs(cumulative - expected_end) > 1e-6:
        raise CanonicalHistoryError(
            "canonical history does not close on final meter anchor: "
            f"{cumulative} != {expected_end}"
        )

    return CanonicalHistoryResult(
        hours=tuple(hour_results),
        intervals=tuple(interval_results),
    )
