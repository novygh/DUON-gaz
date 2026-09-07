"""Publish settled canonical DUON gas history as external Recorder statistics."""
from __future__ import annotations

import math
from typing import Any

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.models.statistics import (
    StatisticData,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfVolume
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .canonical_history import CanonicalHistoryError
from .canonical_preview import async_build_canonical_history
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


async def async_publish_canonical_statistics(runtime) -> dict[str, Any]:
    """Rebuild and enqueue settled canonical gas statistics for Recorder.

    Only intervals closed by meter anchors are published. The open interval after
    the newest anchor remains provisional and is deliberately excluded for now.
    Existing Ariston statistics are never modified.
    """
    result, summary = await async_build_canonical_history(runtime)
    if not result.hours:
        raise CanonicalHistoryError("Historia kanoniczna nie zawiera godzin do publikacji.")
    if abs(float(summary["closure_error_m3"])) > 1e-6:
        raise CanonicalHistoryError("Historia kanoniczna nie domyka się do gazomierza.")
    if float(summary["unresolved_rollback_kwh"]) > 1e-6:
        raise CanonicalHistoryError(
            "Historia zawiera nierozliczony rollback źródła i nie może być opublikowana."
        )

    statistics = [
        StatisticData(
            start=hour.start,
            state=round(max(0.0, hour.gas_m3), 9),
            sum=round(hour.cumulative_m3, 9),
        )
        for hour in result.hours
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

    # Official Recorder API. This schedules an import task; it does not touch the
    # database directly and repeated publication of the same hourly timestamps is
    # handled by Recorder's statistics import path.
    async_add_external_statistics(runtime.hass, metadata, statistics)

    requested_at = dt_util.utcnow().isoformat()
    publication = {
        "status": "queued",
        "requested_at": requested_at,
        "verified": False,
        "statistic_id": CANONICAL_GAS_STATISTIC_ID,
        "row_count": len(statistics),
        "start": statistics[0]["start"].isoformat(),
        "end": statistics[-1]["start"].isoformat(),
        "first_sum_m3": statistics[0]["sum"],
        "last_sum_m3": statistics[-1]["sum"],
        "settled_through": summary["end"],
    }
    summary["publication_requested_at"] = requested_at
    summary["publication_statistic_id"] = CANONICAL_GAS_STATISTIC_ID
    summary["publication_row_count"] = len(statistics)

    runtime.data["canonical_preview"] = summary
    runtime.data["canonical_publication"] = publication
    await runtime.async_save()
    runtime.async_notify()
    return publication
