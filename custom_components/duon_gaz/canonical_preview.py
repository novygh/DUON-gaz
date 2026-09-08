"""Canonical history reconstruction and dry-run diagnostics for DUON Gaz."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .canonical_history import (
    CanonicalHistoryError,
    CanonicalHistoryResult,
    PhysicalAnchor,
    SourcePoint,
    build_canonical_history,
)
from .canonical_tail import build_provisional_tail
from .recorder_stats import async_get_hourly_recorder_series


def _as_timestamp(value: Any):
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed


def _as_meter(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _anchors_from_runtime(runtime) -> list[PhysicalAnchor]:
    anchors: list[PhysicalAnchor] = []
    for reading in runtime._readings():
        timestamp = _as_timestamp(reading.get("timestamp"))
        meter = _as_meter(reading.get("meter_m3"))
        if timestamp is None or meter is None:
            continue
        anchors.append(
            PhysicalAnchor(
                timestamp=timestamp,
                meter_m3=meter,
                source=str(reading.get("source") or "unknown"),
                timestamp_precision=str(
                    reading.get("timestamp_precision") or "exact"
                ),
            )
        )
    return anchors


def _interval_audit(result: CanonicalHistoryResult) -> list[dict[str, Any]]:
    ranked = sorted(
        result.intervals,
        key=lambda interval: (
            abs((interval.scale_factor or 1.0) - 1.0),
            interval.reconstructed_gap_hours,
        ),
        reverse=True,
    )[:10]
    return [
        {
            "start": interval.start.isoformat(),
            "end": interval.end.isoformat(),
            "physical_delta_m3": round(interval.physical_delta_m3, 6),
            "provisional_m3": round(interval.provisional_m3, 6),
            "scale_factor": (
                None
                if interval.scale_factor is None
                else round(interval.scale_factor, 9)
            ),
            "reconstructed_gap_hours": interval.reconstructed_gap_hours,
            "rollback_correction_kwh": round(
                interval.rollback_correction_kwh, 6
            ),
            "quality": list(interval.quality),
        }
        for interval in ranked
    ]


async def async_build_canonical_history(
    runtime,
) -> tuple[CanonicalHistoryResult, dict[str, Any]]:
    """Build canonical history and its audit summary without persisting it."""
    all_anchors = _anchors_from_runtime(runtime)
    if len(all_anchors) < 2:
        raise CanonicalHistoryError(
            "Do rekonstrukcji historii potrzebne są co najmniej dwa punkty gazomierza."
        )

    heating_coeff = runtime.co_m3_per_kwh
    dhw_coeff = runtime.dhw_m3_per_kwh
    if heating_coeff is None or dhw_coeff is None:
        raise CanonicalHistoryError("Brak współczynników kalibracji CO/CWU.")

    fetch_end = max(
        all_anchors[-1].timestamp + timedelta(hours=2),
        dt_util.utcnow() + timedelta(hours=1),
    )
    snapshots = await async_get_hourly_recorder_series(
        runtime.hass,
        runtime.heating_entity,
        runtime.dhw_entity,
        all_anchors[0].timestamp - timedelta(hours=2),
        fetch_end,
    )
    source_points = [
        SourcePoint(
            timestamp=snapshot.timestamp,
            heating_sum_kwh=snapshot.heating_sum_kwh,
            dhw_sum_kwh=snapshot.dhw_sum_kwh,
        )
        for snapshot in snapshots
    ]
    if len(source_points) < 2:
        raise CanonicalHistoryError("Brak wystarczającej historii Recorder CO/CWU.")

    source_start = source_points[0].timestamp
    source_end = source_points[-1].timestamp + timedelta(hours=1)
    anchors = [
        anchor
        for anchor in all_anchors
        if source_start <= anchor.timestamp <= source_end
    ]
    if len(anchors) < 2:
        raise CanonicalHistoryError(
            "Historia Recorder nie obejmuje co najmniej dwóch punktów gazomierza."
        )

    result = build_canonical_history(
        source_points,
        anchors,
        heating_m3_per_kwh=heating_coeff,
        dhw_m3_per_kwh=dhw_coeff,
        timezone=dt_util.DEFAULT_TIME_ZONE,
    )
    tail = build_provisional_tail(
        source_points,
        anchors[-1],
        heating_m3_per_kwh=heating_coeff,
        dhw_m3_per_kwh=dhw_coeff,
        timezone=dt_util.DEFAULT_TIME_ZONE,
    )

    scales = [
        interval.scale_factor
        for interval in result.intervals
        if interval.scale_factor is not None
    ]
    canonical_total = sum(hour.gas_m3 for hour in result.hours)
    physical_total = anchors[-1].meter_m3 - anchors[0].meter_m3
    reconstructed_gap_hours = sum(
        interval.reconstructed_gap_hours for interval in result.intervals
    )
    rollback_correction = sum(
        interval.rollback_correction_kwh for interval in result.intervals
    )
    rollback_retracted = sum(
        interval.rollback_retracted_kwh for interval in result.intervals
    )
    rollback_unresolved = sum(
        interval.unresolved_rollback_kwh for interval in result.intervals
    )

    summary: dict[str, Any] = {
        "generated_at": dt_util.utcnow().isoformat(),
        "published_to_recorder": False,
        "source_point_count": len(source_points),
        "anchor_count_total": len(all_anchors),
        "anchor_count": len(anchors),
        "anchor_count_without_recorder": len(all_anchors) - len(anchors),
        "interval_count": len(result.intervals),
        "canonical_hour_count": len(result.hours),
        "source_start": source_start.isoformat(),
        "source_end": source_end.isoformat(),
        "start": anchors[0].timestamp.isoformat(),
        "end": anchors[-1].timestamp.isoformat(),
        "start_meter_m3": anchors[0].meter_m3,
        "end_meter_m3": anchors[-1].meter_m3,
        "physical_total_m3": round(physical_total, 6),
        "canonical_total_m3": round(canonical_total, 6),
        "closure_error_m3": round(canonical_total - physical_total, 9),
        "reconstructed_gap_hours": reconstructed_gap_hours,
        "rollback_correction_kwh": round(rollback_correction, 6),
        "rollback_retracted_kwh": round(rollback_retracted, 6),
        "unresolved_rollback_kwh": round(rollback_unresolved, 6),
        "low_confidence_interval_count": sum(
            "low_confidence_scale" in interval.quality
            for interval in result.intervals
        ),
        "uncertain_anchor_interval_count": sum(
            "uncertain_anchor_time" in interval.quality
            for interval in result.intervals
        ),
        "scale_factor_min": None if not scales else round(min(scales), 9),
        "scale_factor_max": None if not scales else round(max(scales), 9),
        "audit_intervals": _interval_audit(result),
        "provisional_hour_count": len(tail.hours),
        "provisional_start": (
            tail.hours[0].start.isoformat() if tail.hours else None
        ),
        "provisional_end": tail.source_end.isoformat(),
        "provisional_m3": round(tail.gas_m3, 6),
        "provisional_meter_m3": round(tail.estimated_meter_m3, 6),
        "provisional_reconstructed_gap_hours": tail.reconstructed_gap_hours,
        "provisional_rollback_correction_kwh": round(
            tail.rollback_correction_kwh, 6
        ),
        "provisional_rollback_retracted_kwh": round(
            tail.rollback_retracted_kwh, 6
        ),
        "provisional_unresolved_rollback_kwh": round(
            tail.unresolved_rollback_kwh, 6
        ),
    }
    return result, summary


async def async_rebuild_canonical_preview(runtime) -> dict[str, Any]:
    """Build canonical history without publishing any Recorder statistics."""
    _result, summary = await async_build_canonical_history(runtime)
    runtime.data["canonical_preview"] = summary
    await runtime.async_save()
    runtime.async_notify()
    return summary
