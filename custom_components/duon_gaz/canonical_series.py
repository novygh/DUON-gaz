"""Merge and select canonical DUON hourly history."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .canonical_history import CanonicalHistoryError, CanonicalHour


@dataclass(frozen=True, slots=True)
class CombinedCanonicalSeries:
    """One continuous canonical hourly series ready for publication."""

    hours: tuple[CanonicalHour, ...]
    overlap_hour_count: int


def merge_canonical_hours(
    settled_hours: Iterable[CanonicalHour],
    provisional_hours: Iterable[CanonicalHour],
) -> CombinedCanonicalSeries:
    """Merge settled history with the open provisional tail.

    A physical meter anchor may fall inside an hour. In that case the settled
    reconstruction contains the part before the anchor and the provisional tail
    contains the part after it, both using the same hourly ``start`` timestamp.
    Recorder must receive one full row for that hour, so duplicate starts are
    combined and cumulative sums are rebuilt from the first settled baseline.
    """
    settled = list(settled_hours)
    provisional = list(provisional_hours)
    all_rows = [*settled, *provisional]
    if not all_rows:
        return CombinedCanonicalSeries(hours=(), overlap_hour_count=0)

    baseline_row = settled[0] if settled else provisional[0]
    baseline = baseline_row.cumulative_m3 - baseline_row.gas_m3

    grouped: dict[datetime, list[CanonicalHour]] = {}
    for row in all_rows:
        grouped.setdefault(row.start, []).append(row)

    overlap_hour_count = sum(len(rows) > 1 for rows in grouped.values())
    if overlap_hour_count > 1:
        raise CanonicalHistoryError(
            "Settled and provisional canonical history overlap in more than one hour."
        )

    cumulative = float(baseline)
    merged: list[CanonicalHour] = []
    for start in sorted(grouped):
        rows = grouped[start]
        heating_kwh = sum(row.heating_kwh for row in rows)
        dhw_kwh = sum(row.dhw_kwh for row in rows)
        heating_m3 = sum(row.heating_m3 for row in rows)
        dhw_m3 = sum(row.dhw_m3 for row in rows)
        unattributed_m3 = sum(row.unattributed_m3 for row in rows)
        gas_m3 = heating_m3 + dhw_m3 + unattributed_m3
        cumulative += gas_m3
        quality = tuple(sorted({flag for row in rows for flag in row.quality}))

        merged.append(
            CanonicalHour(
                start=start,
                heating_kwh=heating_kwh,
                dhw_kwh=dhw_kwh,
                heating_m3=heating_m3,
                dhw_m3=dhw_m3,
                unattributed_m3=unattributed_m3,
                gas_m3=gas_m3,
                cumulative_m3=cumulative,
                quality=quality,
            )
        )

    return CombinedCanonicalSeries(
        hours=tuple(merged),
        overlap_hour_count=overlap_hour_count,
    )


def select_provisional_refresh_hours(
    combined_hours: Iterable[CanonicalHour],
    provisional_hours: Iterable[CanonicalHour],
) -> tuple[CanonicalHour, ...]:
    """Return the smallest Recorder slice that can refresh the open tail.

    If the newest physical meter anchor falls inside an hour, the first
    provisional hour shares its start with the final settled hour. The combined
    row for that boundary hour must therefore be rewritten together with all
    later provisional rows.
    """
    provisional = tuple(provisional_hours)
    if not provisional:
        return ()

    refresh_start = provisional[0].start
    return tuple(row for row in combined_hours if row.start >= refresh_start)
