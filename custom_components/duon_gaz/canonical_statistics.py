"""Publish canonical DUON gas history and costs as external Recorder statistics."""
from __future__ import annotations

import asyncio
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

from .canonical_builder import CanonicalBuild, async_build_canonical_bundle
from .canonical_costs import (
    CanonicalCostError,
    CanonicalCostResult,
    billing_period_fingerprint,
    build_canonical_costs,
)
from .canonical_history import CanonicalHistoryError, CanonicalHour
from .canonical_series import select_provisional_refresh_hours
from .const import DOMAIN
from .publication_rules import active_anchor_fingerprint, active_anchor_set_changed

CANONICAL_GAS_STATISTIC_ID = f"{DOMAIN}:canonical_gas"
CANONICAL_HEATING_STATISTIC_ID = f"{DOMAIN}:canonical_heating"
CANONICAL_DHW_STATISTIC_ID = f"{DOMAIN}:canonical_dhw"
CANONICAL_FIXED_COST_GAS_STATISTIC_ID = f"{DOMAIN}:canonical_fixed_cost_gas"
CANONICAL_HEATING_COST_STATISTIC_ID = f"{DOMAIN}:canonical_heating_cost"
CANONICAL_DHW_COST_STATISTIC_ID = f"{DOMAIN}:canonical_dhw_cost"
CANONICAL_FIXED_COST_STATISTIC_ID = f"{DOMAIN}:canonical_fixed_cost"
_PUBLISH_LOCKS: dict[str, asyncio.Lock] = {}
_EPSILON = 1e-9


def _publish_lock(runtime) -> asyncio.Lock:
    """Return one publication lock per config entry."""
    return _PUBLISH_LOCKS.setdefault(runtime.entry_id, asyncio.Lock())


def forget_publication_lock(entry_id: str) -> None:
    """Drop the publication lock when an entry is unloaded."""
    _PUBLISH_LOCKS.pop(entry_id, None)


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
            raise CanonicalHistoryError("Historia kanoniczna zawiera ujemną wartość godzinową.")
        if previous_start is not None and start <= previous_start:
            raise CanonicalHistoryError("Godziny historii kanonicznej nie są rosnące.")
        if previous_sum is not None and current_sum < previous_sum - 1e-9:
            raise CanonicalHistoryError("Suma historii kanonicznej nie jest monotoniczna.")
        previous_start = start
        previous_sum = current_sum


def _statistics_from_hours(hours: tuple[CanonicalHour, ...]) -> list[StatisticData]:
    statistics = [
        StatisticData(
            start=hour.start,
            state=round(max(0.0, hour.gas_m3), 9),
            sum=round(hour.cumulative_m3, 9),
        )
        for hour in hours
    ]
    _validate_monotonic(statistics)
    return statistics


def _component_statistics_from_hours(
    hours: tuple[CanonicalHour, ...],
    attribute: str,
) -> list[StatisticData]:
    """Build a cumulative volume component series from complete history."""
    cumulative = 0.0
    statistics: list[StatisticData] = []
    for hour in hours:
        value = float(getattr(hour, attribute))
        if not math.isfinite(value) or value < -_EPSILON:
            raise CanonicalHistoryError(
                "Historia kanoniczna zawiera nieprawidłowy składnik CO/CWU."
            )
        state = max(0.0, value)
        cumulative += state
        statistics.append(
            StatisticData(
                start=hour.start,
                state=round(state, 9),
                sum=round(cumulative, 9),
            )
        )
    _validate_monotonic(statistics)
    return statistics


def _cost_statistics_from_result(
    costs: CanonicalCostResult,
    attribute: str,
) -> list[StatisticData]:
    """Build one cumulative PLN component series from canonical costs."""
    cumulative = 0.0
    statistics: list[StatisticData] = []
    for hour in costs.hours:
        value = float(getattr(hour, attribute))
        if not math.isfinite(value) or value < -_EPSILON:
            raise CanonicalHistoryError(
                "Historia kosztów kanonicznych zawiera nieprawidłową wartość."
            )
        state = max(0.0, value)
        cumulative += state
        statistics.append(
            StatisticData(
                start=hour.start,
                state=round(state, 6),
                sum=round(cumulative, 6),
            )
        )
    _validate_monotonic(statistics)
    return statistics


def _zero_volume_statistics(hours: tuple[CanonicalHour, ...]) -> list[StatisticData]:
    """Build a zero-m3 carrier for fixed gas costs in Energy Dashboard."""
    statistics = [
        StatisticData(start=hour.start, state=0.0, sum=0.0)
        for hour in hours
    ]
    _validate_monotonic(statistics)
    return statistics


def _select_statistics_for_hours(
    statistics: list[StatisticData],
    hours: tuple[CanonicalHour, ...],
) -> list[StatisticData]:
    """Select already cumulative statistics matching a refresh-hour slice."""
    starts = {hour.start for hour in hours}
    selected = [row for row in statistics if row["start"] in starts]
    if len(selected) != len(hours):
        raise CanonicalHistoryError(
            "Nie udało się dopasować statystyk do odświeżanego ogona."
        )
    return selected


def _volume_metadata(
    statistic_id: str = CANONICAL_GAS_STATISTIC_ID,
    name: str = "DUON Gaz — historia kanoniczna",
) -> StatisticMetaData:
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class=VolumeConverter.UNIT_CLASS,
        unit_of_measurement=UnitOfVolume.CUBIC_METERS,
    )


def _cost_metadata(statistic_id: str, name: str) -> StatisticMetaData:
    """Return external monetary metadata accepted by Energy Dashboard."""
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class=None,
        unit_of_measurement="PLN",
    )


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


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is not None and parsed.tzinfo is not None:
            return parsed
    return None


def _as_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _validate_build(build: CanonicalBuild) -> None:
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

    unattributed = 0.0
    for hour in build.combined.hours:
        if abs(
            (hour.heating_m3 + hour.dhw_m3 + hour.unattributed_m3) - hour.gas_m3
        ) > 1e-6:
            raise CanonicalHistoryError(
                "Składniki CO/CWU historii kanonicznej nie sumują się do całkowitego gazu."
            )
        unattributed += max(0.0, float(hour.unattributed_m3))
    if unattributed > 1e-6:
        raise CanonicalHistoryError(
            "Historia zawiera gaz bez defensywnego przypisania do CO lub CWU; "
            "rozdzielone statystyki nie mogą zostać opublikowane."
        )


def _build_costs(runtime, build: CanonicalBuild) -> CanonicalCostResult:
    try:
        return build_canonical_costs(
            build.combined.hours,
            runtime.data.get("billing_periods", []),
            timezone=dt_util.DEFAULT_TIME_ZONE,
            conversion_factor_kwh_m3=runtime.conversion_factor,
            gas_rate_net_pln_kwh=runtime.gas_rate_net,
            distribution_variable_net_pln_kwh=runtime.dist_var_rate_net,
            subscription_net_pln_month=runtime.subscription_net,
            distribution_fixed_net_pln_month=runtime.dist_fixed_net,
            vat_rate=runtime.vat,
        )
    except CanonicalCostError as err:
        raise CanonicalHistoryError(str(err)) from err


async def _async_verify_statistic(
    runtime,
    statistic_id: str,
    expected_last_start: datetime,
    expected_last_sum: float,
) -> dict[str, Any]:
    """Verify the newest imported row for one external statistic."""
    recorder = get_instance(runtime.hass)
    result = await recorder.async_add_executor_job(
        get_last_statistics,
        runtime.hass,
        1,
        statistic_id,
        False,
        {"state", "sum"},
    )
    rows = result.get(statistic_id, [])
    if not rows:
        raise CanonicalHistoryError(
            f"Recorder nie zwrócił opublikowanej statystyki {statistic_id}."
        )

    row = rows[-1]
    last_sum = row.get("sum")
    if last_sum is None or not math.isfinite(float(last_sum)):
        raise CanonicalHistoryError(
            f"Recorder zwrócił nieprawidłową końcową sumę statystyki {statistic_id}."
        )
    if abs(float(last_sum) - expected_last_sum) > 1e-6:
        raise CanonicalHistoryError(
            f"Końcowa suma {statistic_id} w Recorder nie zgadza się z historią "
            f"kanoniczną: {last_sum} != {expected_last_sum}."
        )

    last_start_ts = _as_timestamp(row.get("start"))
    if last_start_ts is None:
        raise CanonicalHistoryError(
            f"Recorder nie zwrócił czasu końcowego rekordu {statistic_id}."
        )
    if abs(last_start_ts - expected_last_start.timestamp()) > 1.0:
        raise CanonicalHistoryError(
            f"Końcowy czas {statistic_id} w Recorder nie zgadza się z historią kanoniczną."
        )

    return {
        "last_start": row.get("start"),
        "last_state": row.get("state"),
        "last_sum": float(last_sum),
    }


async def _async_verify_publication(
    runtime,
    gas_statistics: list[StatisticData],
    heating_statistics: list[StatisticData],
    dhw_statistics: list[StatisticData],
    fixed_gas_statistics: list[StatisticData],
    heating_cost_statistics: list[StatisticData],
    dhw_cost_statistics: list[StatisticData],
    fixed_cost_statistics: list[StatisticData],
) -> dict[str, Any]:
    """Wait for Recorder and verify all volume and cost statistics."""
    recorder = get_instance(runtime.hass)
    await recorder.async_block_till_done()

    gas = await _async_verify_statistic(
        runtime,
        CANONICAL_GAS_STATISTIC_ID,
        gas_statistics[-1]["start"],
        float(gas_statistics[-1]["sum"]),
    )
    heating = await _async_verify_statistic(
        runtime,
        CANONICAL_HEATING_STATISTIC_ID,
        heating_statistics[-1]["start"],
        float(heating_statistics[-1]["sum"]),
    )
    dhw = await _async_verify_statistic(
        runtime,
        CANONICAL_DHW_STATISTIC_ID,
        dhw_statistics[-1]["start"],
        float(dhw_statistics[-1]["sum"]),
    )
    fixed_gas = await _async_verify_statistic(
        runtime,
        CANONICAL_FIXED_COST_GAS_STATISTIC_ID,
        fixed_gas_statistics[-1]["start"],
        0.0,
    )
    heating_cost = await _async_verify_statistic(
        runtime,
        CANONICAL_HEATING_COST_STATISTIC_ID,
        heating_cost_statistics[-1]["start"],
        float(heating_cost_statistics[-1]["sum"]),
    )
    dhw_cost = await _async_verify_statistic(
        runtime,
        CANONICAL_DHW_COST_STATISTIC_ID,
        dhw_cost_statistics[-1]["start"],
        float(dhw_cost_statistics[-1]["sum"]),
    )
    fixed_cost = await _async_verify_statistic(
        runtime,
        CANONICAL_FIXED_COST_STATISTIC_ID,
        fixed_cost_statistics[-1]["start"],
        float(fixed_cost_statistics[-1]["sum"]),
    )

    return {
        "verified_at": dt_util.utcnow().isoformat(),
        "last_start": gas["last_start"],
        "last_state_m3": gas["last_state"],
        "last_sum_m3": gas["last_sum"],
        "heating_last_sum_m3": heating["last_sum"],
        "dhw_last_sum_m3": dhw["last_sum"],
        "component_statistics_verified": True,
        "fixed_cost_gas_last_sum_m3": fixed_gas["last_sum"],
        "heating_cost_last_sum_pln": heating_cost["last_sum"],
        "dhw_cost_last_sum_pln": dhw_cost["last_sum"],
        "fixed_cost_last_sum_pln": fixed_cost["last_sum"],
        "cost_statistics_verified": True,
    }


def _cost_configuration_changed(runtime, publication: dict[str, Any]) -> bool:
    pairs = (
        (publication.get("cost_conversion_factor_kwh_m3"), runtime.conversion_factor),
        (publication.get("cost_gas_rate_net_pln_kwh"), runtime.gas_rate_net),
        (
            publication.get("cost_distribution_variable_net_pln_kwh"),
            runtime.dist_var_rate_net,
        ),
        (publication.get("cost_subscription_net_pln_month"), runtime.subscription_net),
        (
            publication.get("cost_distribution_fixed_net_pln_month"),
            runtime.dist_fixed_net,
        ),
        (publication.get("cost_vat_rate"), runtime.vat),
    )
    for previous, current in pairs:
        previous_value = _as_float(previous)
        if previous_value is None or abs(previous_value - float(current)) > 1e-12:
            return True
    return False


def _publication_requires_full_rebuild(
    runtime,
    publication: dict[str, Any],
    build: CanonicalBuild,
) -> str | None:
    """Return why an incremental refresh is unsafe, if anything."""
    if (
        publication.get("heating_statistic_id") != CANONICAL_HEATING_STATISTIC_ID
        or publication.get("dhw_statistic_id") != CANONICAL_DHW_STATISTIC_ID
        or not publication.get("component_statistics_verified", False)
    ):
        return "component_statistics_missing"

    if (
        publication.get("fixed_cost_gas_statistic_id")
        != CANONICAL_FIXED_COST_GAS_STATISTIC_ID
        or publication.get("heating_cost_statistic_id")
        != CANONICAL_HEATING_COST_STATISTIC_ID
        or publication.get("dhw_cost_statistic_id") != CANONICAL_DHW_COST_STATISTIC_ID
        or publication.get("fixed_cost_statistic_id") != CANONICAL_FIXED_COST_STATISTIC_ID
        or not publication.get("cost_statistics_verified", False)
    ):
        return "cost_statistics_missing"

    billing_periods = runtime.data.get("billing_periods", [])
    if publication.get("billing_period_fingerprint") != billing_period_fingerprint(
        billing_periods
    ):
        return "billing_periods_changed"

    if _cost_configuration_changed(runtime, publication):
        return "cost_configuration_changed"

    active_readings = runtime._readings()
    if active_anchor_set_changed(
        publication.get("active_anchor_fingerprint"),
        active_readings,
    ):
        return "physical_anchor_set_changed"

    if publication.get("settled_through") != build.summary.get("end"):
        return "physical_anchor_changed"

    published_heating = publication.get("heating_entity")
    published_dhw = publication.get("dhw_entity")
    if published_heating is not None and published_heating != runtime.heating_entity:
        return "heating_source_changed"
    if published_dhw is not None and published_dhw != runtime.dhw_entity:
        return "dhw_source_changed"

    current_co = _as_float(runtime.co_m3_per_kwh)
    current_dhw = _as_float(runtime.dhw_m3_per_kwh)
    published_co = _as_float(publication.get("calibration_co_m3_per_kwh"))
    published_dhw_coeff = _as_float(publication.get("calibration_dhw_m3_per_kwh"))

    if published_co is not None and current_co is not None:
        if abs(published_co - current_co) > 1e-12:
            return "calibration_changed"
    if published_dhw_coeff is not None and current_dhw is not None:
        if abs(published_dhw_coeff - current_dhw) > 1e-12:
            return "calibration_changed"

    if published_co is None or published_dhw_coeff is None:
        calibration = runtime.data.get("calibration", {})
        calibration_updated = _as_datetime(calibration.get("updated_at"))
        publication_requested = _as_datetime(publication.get("requested_at"))
        if (
            calibration_updated is None
            or publication_requested is None
            or calibration_updated > publication_requested
        ):
            return "calibration_fingerprint_unknown"

    return None


def _publication_record(
    runtime,
    build: CanonicalBuild,
    costs: CanonicalCostResult,
    *,
    mode: str,
    write_row_count: int,
    reason: str,
) -> dict[str, Any]:
    combined = build.combined.hours
    calibration = runtime.data.get("calibration", {})
    active_readings = runtime._readings()
    billing_periods = runtime.data.get("billing_periods", [])
    heating_total_m3 = sum(max(0.0, float(hour.heating_m3)) for hour in combined)
    dhw_total_m3 = sum(max(0.0, float(hour.dhw_m3)) for hour in combined)
    unattributed_total_m3 = sum(
        max(0.0, float(hour.unattributed_m3)) for hour in combined
    )
    canonical_consumption_m3 = sum(max(0.0, float(hour.gas_m3)) for hour in combined)
    split_total_m3 = heating_total_m3 + dhw_total_m3
    heating_cost_total = sum(hour.heating_pln for hour in costs.hours)
    dhw_cost_total = sum(hour.dhw_pln for hour in costs.hours)
    fixed_cost_total = sum(hour.fixed_pln for hour in costs.hours)
    cost_split_total = heating_cost_total + dhw_cost_total + fixed_cost_total
    return {
        "status": "publishing",
        "mode": mode,
        "refresh_reason": reason,
        "requested_at": dt_util.utcnow().isoformat(),
        "verified": False,
        "statistic_id": CANONICAL_GAS_STATISTIC_ID,
        "heating_statistic_id": CANONICAL_HEATING_STATISTIC_ID,
        "dhw_statistic_id": CANONICAL_DHW_STATISTIC_ID,
        "component_statistics_verified": False,
        "fixed_cost_gas_statistic_id": CANONICAL_FIXED_COST_GAS_STATISTIC_ID,
        "heating_cost_statistic_id": CANONICAL_HEATING_COST_STATISTIC_ID,
        "dhw_cost_statistic_id": CANONICAL_DHW_COST_STATISTIC_ID,
        "fixed_cost_statistic_id": CANONICAL_FIXED_COST_STATISTIC_ID,
        "cost_statistics_verified": False,
        "billing_period_fingerprint": billing_period_fingerprint(billing_periods),
        "cost_conversion_factor_kwh_m3": runtime.conversion_factor,
        "cost_gas_rate_net_pln_kwh": runtime.gas_rate_net,
        "cost_distribution_variable_net_pln_kwh": runtime.dist_var_rate_net,
        "cost_subscription_net_pln_month": runtime.subscription_net,
        "cost_distribution_fixed_net_pln_month": runtime.dist_fixed_net,
        "cost_vat_rate": runtime.vat,
        "canonical_consumption_m3": round(canonical_consumption_m3, 9),
        "heating_total_m3": round(heating_total_m3, 9),
        "dhw_total_m3": round(dhw_total_m3, 9),
        "unattributed_total_m3": round(unattributed_total_m3, 9),
        "split_total_m3": round(split_total_m3, 9),
        "split_closure_error_m3": round(split_total_m3 - canonical_consumption_m3, 9),
        "cost_billing_period_count": costs.billing_period_count,
        "cost_applied_invoice_count": costs.applied_invoice_count,
        "cost_skipped_outside_history_count": costs.skipped_outside_history_count,
        "cost_skipped_partial_history_count": costs.skipped_partial_history_count,
        "cost_unpriced_historical_hour_count": costs.unpriced_historical_hour_count,
        "cost_latest_applied_invoice_end": None
        if costs.latest_applied_invoice_end is None
        else costs.latest_applied_invoice_end.isoformat(),
        "cost_invoiced_gross_pln": round(costs.invoiced_gross_pln, 6),
        "cost_published_invoiced_pln": round(costs.published_invoiced_pln, 6),
        "cost_invoiced_closure_error_pln": round(
            costs.invoiced_closure_error_pln, 6
        ),
        "cost_provisional_pln": round(costs.provisional_pln, 6),
        "cost_total_pln": round(costs.total_pln, 6),
        "heating_cost_total_pln": round(heating_cost_total, 6),
        "dhw_cost_total_pln": round(dhw_cost_total, 6),
        "fixed_cost_total_pln": round(fixed_cost_total, 6),
        "cost_split_total_pln": round(cost_split_total, 6),
        "cost_split_closure_error_pln": round(cost_split_total - costs.total_pln, 6),
        "row_count": len(combined),
        "write_row_count": write_row_count,
        "settled_row_count": len(build.settled.hours),
        "provisional_row_count": len(build.provisional.hours),
        "overlap_hour_count": build.combined.overlap_hour_count,
        "start": combined[0].start.isoformat(),
        "end": combined[-1].start.isoformat(),
        "published_through": build.summary["combined_end"],
        "first_sum_m3": round(combined[0].cumulative_m3, 9),
        "last_sum_m3": round(combined[-1].cumulative_m3, 9),
        "settled_through": build.summary["end"],
        "active_anchor_count": len(active_readings),
        "active_anchor_fingerprint": active_anchor_fingerprint(active_readings),
        "heating_entity": runtime.heating_entity,
        "dhw_entity": runtime.dhw_entity,
        "calibration_co_m3_per_kwh": runtime.co_m3_per_kwh,
        "calibration_dhw_m3_per_kwh": runtime.dhw_m3_per_kwh,
        "calibration_updated_at": calibration.get("updated_at"),
    }


async def _async_store_verified_publication(
    runtime,
    build: CanonicalBuild,
    publication: dict[str, Any],
    verification: dict[str, Any],
) -> dict[str, Any]:
    publication.update(
        {
            "status": "verified",
            "verified": True,
            **verification,
        }
    )

    summary = build.summary
    summary["published_to_recorder"] = True
    summary["publication_requested_at"] = publication["requested_at"]
    summary["publication_verified_at"] = verification["verified_at"]
    summary["publication_statistic_id"] = CANONICAL_GAS_STATISTIC_ID
    summary["publication_heating_statistic_id"] = CANONICAL_HEATING_STATISTIC_ID
    summary["publication_dhw_statistic_id"] = CANONICAL_DHW_STATISTIC_ID
    summary["publication_component_statistics_verified"] = True
    summary["publication_fixed_cost_gas_statistic_id"] = (
        CANONICAL_FIXED_COST_GAS_STATISTIC_ID
    )
    summary["publication_heating_cost_statistic_id"] = (
        CANONICAL_HEATING_COST_STATISTIC_ID
    )
    summary["publication_dhw_cost_statistic_id"] = CANONICAL_DHW_COST_STATISTIC_ID
    summary["publication_fixed_cost_statistic_id"] = CANONICAL_FIXED_COST_STATISTIC_ID
    summary["publication_cost_statistics_verified"] = True
    summary["publication_cost_invoiced_closure_error_pln"] = publication[
        "cost_invoiced_closure_error_pln"
    ]
    summary["publication_cost_split_closure_error_pln"] = publication[
        "cost_split_closure_error_pln"
    ]
    summary["publication_row_count"] = publication["row_count"]
    summary["publication_write_row_count"] = publication["write_row_count"]
    summary["publication_mode"] = publication["mode"]
    summary["publication_refresh_reason"] = publication["refresh_reason"]

    runtime.data["canonical_preview"] = summary
    runtime.data["canonical_publication"] = publication
    await runtime.async_save()
    runtime.async_notify()
    return publication


def _all_component_statistics(
    build: CanonicalBuild,
) -> tuple[list[StatisticData], list[StatisticData]]:
    hours = build.combined.hours
    return (
        _component_statistics_from_hours(hours, "heating_m3"),
        _component_statistics_from_hours(hours, "dhw_m3"),
    )


def _all_cost_statistics(
    build: CanonicalBuild,
    costs: CanonicalCostResult,
) -> tuple[
    list[StatisticData],
    list[StatisticData],
    list[StatisticData],
    list[StatisticData],
]:
    return (
        _zero_volume_statistics(build.combined.hours),
        _cost_statistics_from_result(costs, "heating_pln"),
        _cost_statistics_from_result(costs, "dhw_pln"),
        _cost_statistics_from_result(costs, "fixed_pln"),
    )


def _publish_statistics(
    runtime,
    gas_statistics: list[StatisticData],
    heating_statistics: list[StatisticData],
    dhw_statistics: list[StatisticData],
    fixed_gas_statistics: list[StatisticData],
    heating_cost_statistics: list[StatisticData],
    dhw_cost_statistics: list[StatisticData],
    fixed_cost_statistics: list[StatisticData],
) -> None:
    """Queue all canonical volume and cost statistics from one coherent build."""
    async_add_external_statistics(runtime.hass, _volume_metadata(), gas_statistics)
    async_add_external_statistics(
        runtime.hass,
        _volume_metadata(CANONICAL_HEATING_STATISTIC_ID, "DUON Gaz — Ogrzewanie"),
        heating_statistics,
    )
    async_add_external_statistics(
        runtime.hass,
        _volume_metadata(CANONICAL_DHW_STATISTIC_ID, "DUON Gaz — Ciepła woda"),
        dhw_statistics,
    )
    async_add_external_statistics(
        runtime.hass,
        _volume_metadata(
            CANONICAL_FIXED_COST_GAS_STATISTIC_ID,
            "DUON Gaz — Koszt stały gazu",
        ),
        fixed_gas_statistics,
    )
    async_add_external_statistics(
        runtime.hass,
        _cost_metadata(
            CANONICAL_HEATING_COST_STATISTIC_ID,
            "DUON Gaz — Ogrzewanie koszt",
        ),
        heating_cost_statistics,
    )
    async_add_external_statistics(
        runtime.hass,
        _cost_metadata(
            CANONICAL_DHW_COST_STATISTIC_ID,
            "DUON Gaz — Ciepła woda koszt",
        ),
        dhw_cost_statistics,
    )
    async_add_external_statistics(
        runtime.hass,
        _cost_metadata(CANONICAL_FIXED_COST_STATISTIC_ID, "DUON Gaz — Koszt stały"),
        fixed_cost_statistics,
    )


async def _async_publish_full_locked(
    runtime,
    build: CanonicalBuild,
    *,
    reason: str,
) -> dict[str, Any]:
    """Publish complete settled + provisional volume and cost series."""
    _validate_build(build)
    costs = _build_costs(runtime, build)
    hours = build.combined.hours
    gas_statistics = _statistics_from_hours(hours)
    heating_statistics, dhw_statistics = _all_component_statistics(build)
    (
        fixed_gas_statistics,
        heating_cost_statistics,
        dhw_cost_statistics,
        fixed_cost_statistics,
    ) = _all_cost_statistics(build, costs)
    publication = _publication_record(
        runtime,
        build,
        costs,
        mode="settled_plus_provisional",
        write_row_count=len(gas_statistics),
        reason=reason,
    )

    _publish_statistics(
        runtime,
        gas_statistics,
        heating_statistics,
        dhw_statistics,
        fixed_gas_statistics,
        heating_cost_statistics,
        dhw_cost_statistics,
        fixed_cost_statistics,
    )
    verification = await _async_verify_publication(
        runtime,
        gas_statistics,
        heating_statistics,
        dhw_statistics,
        fixed_gas_statistics,
        heating_cost_statistics,
        dhw_cost_statistics,
        fixed_cost_statistics,
    )
    return await _async_store_verified_publication(
        runtime, build, publication, verification
    )


async def async_publish_canonical_statistics(runtime) -> dict[str, Any]:
    """Rebuild, publish and verify complete canonical volume and cost series."""
    async with _publish_lock(runtime):
        build = await async_build_canonical_bundle(runtime)
        return await _async_publish_full_locked(
            runtime,
            build,
            reason="manual_full_publish",
        )


async def async_refresh_canonical_tail_statistics(
    runtime,
    *,
    reason: str = "manual_tail_refresh",
) -> dict[str, Any]:
    """Refresh the open tail and fall back to full publication when required."""
    async with _publish_lock(runtime):
        previous_publication = runtime.data.get("canonical_publication")
        if not (
            isinstance(previous_publication, dict)
            and previous_publication.get("verified", False)
            and previous_publication.get("statistic_id") == CANONICAL_GAS_STATISTIC_ID
        ):
            return {
                "status": "skipped",
                "reason": "no_verified_canonical_publication",
            }

        build = await async_build_canonical_bundle(runtime)
        _validate_build(build)

        full_reason = _publication_requires_full_rebuild(
            runtime, previous_publication, build
        )
        if full_reason is not None:
            return await _async_publish_full_locked(
                runtime,
                build,
                reason=f"{reason}:{full_reason}",
            )

        refresh_hours = select_provisional_refresh_hours(
            build.combined.hours,
            build.provisional.hours,
        )
        if not refresh_hours:
            return {
                "status": "skipped",
                "reason": "no_provisional_tail",
            }

        costs = _build_costs(runtime, build)
        gas_statistics = _statistics_from_hours(refresh_hours)
        all_heating_statistics, all_dhw_statistics = _all_component_statistics(build)
        (
            all_fixed_gas_statistics,
            all_heating_cost_statistics,
            all_dhw_cost_statistics,
            all_fixed_cost_statistics,
        ) = _all_cost_statistics(build, costs)
        heating_statistics = _select_statistics_for_hours(
            all_heating_statistics,
            refresh_hours,
        )
        dhw_statistics = _select_statistics_for_hours(
            all_dhw_statistics,
            refresh_hours,
        )
        fixed_gas_statistics = _select_statistics_for_hours(
            all_fixed_gas_statistics,
            refresh_hours,
        )
        heating_cost_statistics = _select_statistics_for_hours(
            all_heating_cost_statistics,
            refresh_hours,
        )
        dhw_cost_statistics = _select_statistics_for_hours(
            all_dhw_cost_statistics,
            refresh_hours,
        )
        fixed_cost_statistics = _select_statistics_for_hours(
            all_fixed_cost_statistics,
            refresh_hours,
        )
        publication = _publication_record(
            runtime,
            build,
            costs,
            mode="provisional_tail_refresh",
            write_row_count=len(gas_statistics),
            reason=reason,
        )

        _publish_statistics(
            runtime,
            gas_statistics,
            heating_statistics,
            dhw_statistics,
            fixed_gas_statistics,
            heating_cost_statistics,
            dhw_cost_statistics,
            fixed_cost_statistics,
        )
        verification = await _async_verify_publication(
            runtime,
            gas_statistics,
            heating_statistics,
            dhw_statistics,
            fixed_gas_statistics,
            heating_cost_statistics,
            dhw_cost_statistics,
            fixed_cost_statistics,
        )
        return await _async_store_verified_publication(
            runtime, build, publication, verification
        )
