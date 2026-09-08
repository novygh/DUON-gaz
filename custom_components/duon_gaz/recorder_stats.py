"""Recorder statistics helpers for DUON Gaz."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util


@dataclass(frozen=True, slots=True)
class RecorderSnapshot:
    """A coherent CO/CWU Recorder sum snapshot."""

    timestamp: datetime
    heating_sum_kwh: float
    dhw_sum_kwh: float


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, (int, float)):
        return dt_util.utc_from_timestamp(float(value))
    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    return None


def _rows_by_timestamp(
    result: dict[str, list[dict[str, Any]]], statistic_id: str
) -> dict[int, tuple[datetime, float]]:
    rows: dict[int, tuple[datetime, float]] = {}
    for row in result.get(statistic_id, []):
        start = _as_datetime(row.get("start"))
        sum_value = row.get("sum")
        if start is None or sum_value is None:
            continue
        rows[int(start.timestamp())] = (start, float(sum_value))
    return rows


def _coherent_snapshots(
    result: dict[str, list[dict[str, Any]]],
    heating_entity: str,
    dhw_entity: str,
) -> list[RecorderSnapshot]:
    """Return all common CO/CWU points sorted chronologically."""
    heating_rows = _rows_by_timestamp(result, heating_entity)
    dhw_rows = _rows_by_timestamp(result, dhw_entity)
    common = sorted(heating_rows.keys() & dhw_rows.keys())

    snapshots: list[RecorderSnapshot] = []
    for key in common:
        timestamp, heating_sum = heating_rows[key]
        _, dhw_sum = dhw_rows[key]
        snapshots.append(RecorderSnapshot(timestamp, heating_sum, dhw_sum))
    return snapshots


def _coherent_snapshot(
    result: dict[str, list[dict[str, Any]]],
    heating_entity: str,
    dhw_entity: str,
) -> RecorderSnapshot | None:
    snapshots = _coherent_snapshots(result, heating_entity, dhw_entity)
    return snapshots[-1] if snapshots else None


def _nearest_coherent_snapshot(
    result: dict[str, list[dict[str, Any]]],
    heating_entity: str,
    dhw_entity: str,
    target: datetime,
) -> RecorderSnapshot | None:
    """Return the common CO/CWU hourly point closest to target."""
    snapshots = _coherent_snapshots(result, heating_entity, dhw_entity)
    if not snapshots:
        return None
    return min(
        snapshots,
        key=lambda snapshot: abs((snapshot.timestamp - target).total_seconds()),
    )


async def async_get_recorder_snapshot(
    hass: HomeAssistant,
    heating_entity: str,
    dhw_entity: str,
    *,
    samples: int = 12,
) -> RecorderSnapshot:
    """Return the newest coherent CO/CWU Recorder sum snapshot.

    Prefer short-term 5-minute statistics so a manual physical meter reading is
    anchored close to the actual confirmation time. Fall back to long-term
    hourly statistics if short-term statistics are not available.
    """
    recorder = get_instance(hass)
    start = dt_util.utcnow() - timedelta(hours=2)

    short_term = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        None,
        {heating_entity, dhw_entity},
        "5minute",
        None,
        {"sum"},
    )
    if snapshot := _coherent_snapshot(short_term, heating_entity, dhw_entity):
        return snapshot

    heating_result = await recorder.async_add_executor_job(
        get_last_statistics,
        hass,
        samples,
        heating_entity,
        False,
        {"sum"},
    )
    dhw_result = await recorder.async_add_executor_job(
        get_last_statistics,
        hass,
        samples,
        dhw_entity,
        False,
        {"sum"},
    )

    merged = {
        heating_entity: heating_result.get(heating_entity, []),
        dhw_entity: dhw_result.get(dhw_entity, []),
    }
    if snapshot := _coherent_snapshot(merged, heating_entity, dhw_entity):
        return snapshot

    raise ValueError("Brak wspólnego punktu statystyk Recorder dla CO i CWU.")


async def async_get_recorder_snapshot_at(
    hass: HomeAssistant,
    heating_entity: str,
    dhw_entity: str,
    target: datetime,
    *,
    window: timedelta = timedelta(hours=36),
) -> RecorderSnapshot:
    """Return the coherent hourly Recorder point closest to a historical anchor.

    This is intended for invoice/field-reader readings which may only identify
    the reading date rather than the exact minute. The caller must retain the
    original timestamp precision in the anchor quality metadata.
    """
    if target.tzinfo is None:
        raise ValueError("Historyczny punkt gazomierza musi mieć strefę czasową.")

    recorder = get_instance(hass)
    result = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        target - window,
        target + window,
        {heating_entity, dhw_entity},
        "hour",
        None,
        {"sum"},
    )
    snapshot = _nearest_coherent_snapshot(
        result, heating_entity, dhw_entity, target
    )
    if snapshot is None:
        raise ValueError("Brak statystyk Recorder w pobliżu odczytu z faktury.")
    if abs(snapshot.timestamp - target) > window:
        raise ValueError("Najbliższy punkt Recorder jest zbyt daleko od odczytu z faktury.")
    return snapshot


async def async_get_hourly_recorder_series(
    hass: HomeAssistant,
    heating_entity: str,
    dhw_entity: str,
    start: datetime,
    end: datetime,
) -> list[RecorderSnapshot]:
    """Return coherent hourly cumulative CO/CWU statistics for reconstruction."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Zakres historii Recorder musi mieć strefę czasową.")
    if end <= start:
        raise ValueError("Koniec zakresu historii musi być późniejszy niż początek.")

    recorder = get_instance(hass)
    result = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        {heating_entity, dhw_entity},
        "hour",
        None,
        {"sum"},
    )
    snapshots = _coherent_snapshots(result, heating_entity, dhw_entity)
    if len(snapshots) < 2:
        raise ValueError("Za mało wspólnych godzinowych statystyk Recorder dla CO i CWU.")
    return snapshots
