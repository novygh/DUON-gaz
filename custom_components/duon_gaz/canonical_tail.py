"""Provisional canonical tail after the newest physical meter anchor."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Iterable

from .canonical_history import (
    CanonicalHistoryError,
    CanonicalHistorySettings,
    CanonicalHour,
    PhysicalAnchor,
    SourcePoint,
    _build_expanded_hours,
    _overlap_fraction,
    _validate_points,
)


@dataclass(frozen=True, slots=True)
class ProvisionalTailResult:
    """Unsettled hourly gas estimate after the newest meter anchor."""

    hours: tuple[CanonicalHour, ...]
    source_end: datetime
    gas_m3: float
    estimated_meter_m3: float
    reconstructed_gap_hours: int
    rollback_correction_kwh: float
    rollback_retracted_kwh: float
    unresolved_rollback_kwh: float


def build_provisional_tail(
    source_points: Iterable[SourcePoint],
    final_anchor: PhysicalAnchor,
    *,
    heating_m3_per_kwh: float,
    dhw_m3_per_kwh: float,
    timezone: tzinfo,
    settings: CanonicalHistorySettings | None = None,
) -> ProvisionalTailResult:
    """Build an unnormalized tail from the newest physical anchor onward.

    The settled history remains constrained by physical meter anchors. The open
    interval after the newest anchor is deliberately not normalized because no
    newer physical meter value exists yet. Recorder rollbacks and missing hours
    are repaired with the same machinery as the settled canonical history.
    """
    if final_anchor.timestamp.tzinfo is None:
        raise CanonicalHistoryError("final meter anchor must be timezone-aware")
    if heating_m3_per_kwh <= 0 or dhw_m3_per_kwh <= 0:
        raise CanonicalHistoryError("calibration coefficients must be positive")

    points = sorted(list(source_points), key=lambda point: point.timestamp)
    _validate_points(points)
    cfg = settings or CanonicalHistorySettings()
    expanded, _heating_profile, _dhw_profile = _build_expanded_hours(
        points, timezone, cfg
    )
    if not expanded:
        raise CanonicalHistoryError("no hourly Recorder profile could be built")
    if expanded[0].start > final_anchor.timestamp:
        raise CanonicalHistoryError("Recorder history starts after the final meter anchor")

    cumulative = float(final_anchor.meter_m3)
    rows: list[CanonicalHour] = []
    gap_hours: set[datetime] = set()
    rollback_correction = 0.0
    rollback_retracted = 0.0
    rollback_unresolved = 0.0

    for row in expanded:
        if row.end <= final_anchor.timestamp:
            continue

        fraction = _overlap_fraction(
            row.start,
            row.end,
            final_anchor.timestamp,
            row.end,
        )
        if fraction <= 0:
            continue

        heating_kwh = row.heating_kwh * fraction
        dhw_kwh = row.dhw_kwh * fraction
        heating_m3 = heating_kwh * heating_m3_per_kwh
        dhw_m3 = dhw_kwh * dhw_m3_per_kwh
        gas_m3 = heating_m3 + dhw_m3
        cumulative += gas_m3

        flags = set(row.flags)
        flags.add("provisional_after_meter_anchor")
        if "gap_estimate" in row.flags:
            gap_hours.add(row.start)

        rollback_correction += fraction * row.rollback_correction_kwh
        rollback_retracted += fraction * (
            row.heating_retracted_kwh + row.dhw_retracted_kwh
        )
        rollback_unresolved += fraction * (
            row.heating_unresolved_kwh + row.dhw_unresolved_kwh
        )

        rows.append(
            CanonicalHour(
                start=row.start,
                heating_kwh=heating_kwh,
                dhw_kwh=dhw_kwh,
                heating_m3=heating_m3,
                dhw_m3=dhw_m3,
                unattributed_m3=0.0,
                gas_m3=gas_m3,
                cumulative_m3=cumulative,
                quality=tuple(sorted(flags)),
            )
        )

    gas_total = cumulative - float(final_anchor.meter_m3)
    return ProvisionalTailResult(
        hours=tuple(rows),
        source_end=expanded[-1].end,
        gas_m3=gas_total,
        estimated_meter_m3=cumulative,
        reconstructed_gap_hours=len(gap_hours),
        rollback_correction_kwh=rollback_correction,
        rollback_retracted_kwh=rollback_retracted,
        unresolved_rollback_kwh=rollback_unresolved,
    )
