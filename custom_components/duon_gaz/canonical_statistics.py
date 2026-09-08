"""Publish canonical DUON gas history as external Recorder statistics."""
from __future__ import annotations

from datetime import datetime
import math
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.models.statistics import (
    StatisticData,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfVolume
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .canonical_builder import async_build_canonical_bundle
from .canonical_history import CanonicalHistoryError
from .const import DOMAIN

CANONICAL_GAS_STATISTIC_ID = f"{DOMAIN}:canonical_gas"


def _validate_monotonic(rows: list[StatisticData]) -> None:
    """Reject a publication if its cumulative sum could move backwards."""
    previous_sum: float | None = None
    previous_start = None
    for row in rows:
        start = row["start"]
        current_sum = float(row["sum"])
        state = float(row["state"])
        if not math.isfinite(current_sum) or not math.isfinite(state):
            raise CanonicalHistoryError("Historia kanoniczna zawiera wartość nienumeryczną.")
        if state < -1e-9:
            raise CanonicalHistoryError("Historia kanoniczna zawiera ujemne zużycie godzinowe.")
        if previous_start is not None and start <= previous_start:
            raise CanonicalHistoryError("Godziny historii kanonicznej nie są rosnące.")
        if previous_sum is not None and current_sum < previous_sum - 1e-9:
            raise CanonicalHistoryError("Suma historii kanonicznej nie jest monotoniczna.")
        previous_start = start
        previous_sum = current_sum


def _as_timestamp(value: Any) -> float | None:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is not None:
            return parsed.timestamp()
    return None


async def _async_verify_publication(
    runtime,
    expected_last_start: datetime,
    expected_last_sum: float,
) -> dict[str, Any]:
    """Wait for Recorder and verify the newest imported canonical row."""
    recorder = get_instance(runtime.hass)
    await recorder.async_block_till_done()

    result = await recorder.async_add_executor_job(
        get_last_statistics,
        runtime.hass,
        1,
        CANONICAL_GAS_STATISTIC_ID,
        False,
        {"state", "sum"},
    )
    rows = result.get(CANONICAL_GAS_STATISTIC_ID, [])
    if not rows:
        raise CanonicalHistoryError(
            "Recorder nie zwrócił opublikowanej historii kanonicznej."
        )

    row = rows[-1]
    last_sum = row.get("sum")
    if last_sum is None or not math.isfinite(float(last_sum)):
        raise CanonicalHistoryError(
            "Recorder zwrócił nieprawidłową końcową sumę historii kanonicznej."
        )
    if abs(float(last_sum) - expected_last_sum) > 1e-6:
        raise CanonicalHistoryError(
            "Końcowa suma historii w Recorder nie zgadza się z historią kanoniczną: "
            f"{last_sum} != {expected_last_sum}."
        )

    last_start_ts = _as_timestamp(row.get("start"))
    if last_start_ts is None:
        raise CanonicalHistoryError(
            "Recorder nie zwrócił czasu końcowego rekordu historii kanonicznej."
        )
    if abs(last_start_ts - expected_last_start.timestamp()) > 1.0:
        raise CanonicalHistoryError(
            "Końcowy czas historii w Recorder nie zgadza się z historią kanoniczną."
        )

    return {
        "verified_at": dt_util.utcnow().isoformat(),
        "last_start": row.get("start"),
        "last_state_m3": row.get("state"),
        "last_sum_m3": float(last_sum),
    }


async def async_publish_canonical_statistics(runtime) -> dict[str, Any]:
    """Rebuild, publish and verify settled history plus the provisional tail.

    Settled intervals remain constrained by physical meter anchors. The open tail
    is provisional and may be replaced by later publications when Recorder data
    changes or a new physical meter anchor closes the interval. Existing Ariston
    statistics are never modified.
    """
    build = await async_build_canonical_bundle(runtime)
    summary = build.summary

    if not build.combined.hours:
        raise CanonicalHistoryError("Historia kanoniczna nie zawiera godzin do publikacji.")
    if abs(float(summary["closure_error_m3"])) > 1e-6:
        raise CanonicalHistoryError("Historia kanoniczna nie domyka się do gazomierza.")
    if float(summary["unresolved_rollback_kwh"]) > 1e-6:
        raise CanonicalHistoryError(
            "Historia rozliczona zawiera nierozliczony rollback źródła."
        )
    if float(summary["provisional_unresolved_rollback_kwh"]) > 1e-6:
        raise CanonicalHistoryError(
            "Bieżący ogon zawiera nierozliczony rollback źródła i nie może być opublikowany."
        )

    statistics = [
        StatisticData(
            start=hour.start,
            state=round(max(0.0, hour.gas_m3), 9),
            sum=round(hour.cumulative_m3, 9),
        )
        for hour in build.combined.hours
    ]
    _validate_monotonic(statistics)

    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name="DUON Gaz — historia kanoniczna",
        source=DOMAIN,
        statistic_id=CANONICAL_GAS_STATISTIC_ID,
        unit_class=VolumeConverter.UNIT_CLASS,
        unit_of_measurement=UnitOfVolume.CUBIC_METERS,
    )

    requested_at = dt_util.utcnow().isoformat()
    publication = {
        "status": "publishing",
        "mode": "settled_plus_provisional",
        "requested_at": requested_at,
        "verified": False,
        "statistic_id": CANONICAL_GAS_STATISTIC_ID,
        "row_count": len(statistics),
        "settled_row_count": len(build.settled.hours),
        "provisional_row_count": len(build.provisional.hours),
        "overlap_hour_count": build.combined.overlap_hour_count,
        "start": statistics[0]["start"].isoformat(),
        "end": statistics[-1]["start"].isoformat(),
        "published_through": summary["combined_end"],
        "first_sum_m3": statistics[0]["sum"],
        "last_sum_m3": statistics[-1]["sum"],
        "settled_through": summary["end"],
    }

    # Official Recorder API only; raw Ariston statistics are never rewritten.
    async_add_external_statistics(runtime.hass, metadata, statistics)
    verification = await _async_verify_publication(
        runtime,
        statistics[-1]["start"],
        float(statistics[-1]["sum"]),
    )

    publication.update(
        {
            "status": "verified",
            "verified": True,
            **verification,
        }
    )
    summary["published_to_recorder"] = True
    summary["publication_requested_at"] = requested_at
    summary["publication_verified_at"] = verification["verified_at"]
    summary["publication_statistic_id"] = CANONICAL_GAS_STATISTIC_ID
    summary["publication_row_count"] = len(statistics)
    summary["publication_mode"] = publication["mode"]

    runtime.data["canonical_preview"] = summary
    runtime.data["canonical_publication"] = publication
    await runtime.async_save()
    runtime.async_notify()
    return publication
