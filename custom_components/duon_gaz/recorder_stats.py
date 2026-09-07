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


def _coherent_snapshot(
    result: dict[str, list[dict[str, Any]]],
    heating_entity: str,
    dhw_entity: str,
) -> RecorderSnapshot | None:
    heating_rows = _rows_by_timestamp(result, heating_entity)
    dhw_rows = _rows_by_timestamp(result, dhw_entity)
    common = heating_rows.keys() & dhw_rows.keys()
    if not common:
        return None

    key = max(common)
    timestamp, heating_sum = heating_rows[key]
    _, dhw_sum = dhw_rows[key]
    return RecorderSnapshot(timestamp, heating_sum, dhw_sum)


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
