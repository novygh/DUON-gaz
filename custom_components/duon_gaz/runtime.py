"""Runtime model for DUON Gaz."""
from __future__ import annotations

import asyncio
import calendar
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from statistics import median
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CONVERSION_FACTOR,
    CONF_DHW_ENTITY,
    CONF_DIST_FIXED_NET,
    CONF_DIST_VAR_RATE_NET,
    CONF_GAS_RATE_NET,
    CONF_HEATING_ENTITY,
    CONF_SUBSCRIPTION_NET,
    CONF_VAT,
)
from .recorder_stats import RecorderSnapshot, async_get_recorder_snapshot
from .storage import DuonGazStore, default_store_data

_LOGGER = logging.getLogger(__name__)

_REFRESH_INTERVAL = timedelta(minutes=5)
_MAX_CONFIRM_SNAPSHOT_AGE = timedelta(minutes=20)
_MIN_CALIBRATION_COEFF = 0.04
_MAX_CALIBRATION_COEFF = 0.20


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _reading_recorder_values(
    reading: dict[str, Any],
) -> tuple[float, float] | None:
    recorder = reading.get("recorder")
    if not isinstance(recorder, dict):
        return None
    co = _as_float(recorder.get("co_sum_kwh"))
    dhw = _as_float(recorder.get("dhw_sum_kwh"))
    if co is None or dhw is None:
        return None
    return co, dhw


def _reading_excluded(reading: dict[str, Any]) -> bool:
    quality = reading.get("quality")
    return bool(
        isinstance(quality, dict) and quality.get("exclude_from_calibration", False)
    )


def _solve_two_component(
    rows: list[tuple[float, float, float]],
    weights: list[float] | None = None,
) -> tuple[float, float] | None:
    if len(rows) < 2:
        return None
    if weights is None:
        weights = [1.0] * len(rows)

    s_cc = s_dd = s_cd = s_cm = s_dm = 0.0
    for (co_kwh, dhw_kwh, meter_m3), weight in zip(rows, weights, strict=True):
        s_cc += weight * co_kwh * co_kwh
        s_dd += weight * dhw_kwh * dhw_kwh
        s_cd += weight * co_kwh * dhw_kwh
        s_cm += weight * co_kwh * meter_m3
        s_dm += weight * dhw_kwh * meter_m3

    determinant = s_cc * s_dd - s_cd * s_cd
    scale = max(s_cc * s_dd, 1.0)
    if determinant <= scale * 1e-12:
        return None

    co_coeff = (s_cm * s_dd - s_dm * s_cd) / determinant
    dhw_coeff = (s_dm * s_cc - s_cm * s_cd) / determinant
    return co_coeff, dhw_coeff


def _robust_two_component_fit(
    rows: list[tuple[float, float, float]],
) -> tuple[float, float, float] | None:
    """Fit m3 = a*CO_kWh + b*CWU_kWh with one Huber-like reweighting pass."""
    solution = _solve_two_component(rows)
    if solution is None:
        return None

    co_coeff, dhw_coeff = solution
    residuals = [
        meter_m3 - (co_coeff * co_kwh + dhw_coeff * dhw_kwh)
        for co_kwh, dhw_kwh, meter_m3 in rows
    ]
    center = median(residuals)
    abs_deviation = [abs(value - center) for value in residuals]
    mad = median(abs_deviation)

    if mad > 1e-9:
        sigma = 1.4826 * mad
        threshold = max(1.0, 1.5 * sigma)
        weights = [
            1.0 if abs(value) <= threshold else threshold / abs(value)
            for value in residuals
        ]
        refined = _solve_two_component(rows, weights)
        if refined is not None:
            co_coeff, dhw_coeff = refined

    residuals = [
        meter_m3 - (co_coeff * co_kwh + dhw_coeff * dhw_kwh)
        for co_kwh, dhw_kwh, meter_m3 in rows
    ]
    mae = sum(abs(value) for value in residuals) / len(residuals)
    return co_coeff, dhw_coeff, mae


@dataclass
class DuonGazRuntime:
    """Own persistent reference points and current calculations."""

    hass: HomeAssistant
    entry_id: str
    config: dict[str, Any]
    store: DuonGazStore = field(init=False)
    listeners: list = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    current_snapshot: RecorderSnapshot | None = field(default=None, init=False)
    snapshot_error: str | None = field(default=None, init=False)
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self) -> None:
        self.store = DuonGazStore(self.hass)

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        self.data = stored or default_store_data()
        self._ensure_v2_shape()
        await self.async_refresh_source_snapshot(notify=False)

    def _ensure_v2_shape(self) -> None:
        defaults = default_store_data()
        for key, value in defaults.items():
            if key not in self.data:
                self.data[key] = value
        for section in ("calibration", "totals"):
            if not isinstance(self.data.get(section), dict):
                self.data[section] = defaults[section]
                continue
            for key, value in defaults[section].items():
                self.data[section].setdefault(key, value)

    async def async_save(self) -> None:
        await self.store.async_save(self.data)

    @property
    def heating_entity(self) -> str:
        return self.config[CONF_HEATING_ENTITY]

    @property
    def dhw_entity(self) -> str:
        return self.config[CONF_DHW_ENTITY]

    @property
    def conversion_factor(self) -> float:
        """Return current billing kWh/m3 factor."""
        periods = self.data.get("billing_periods", [])
        if periods:
            for period in reversed(periods):
                value = _as_float(period.get("conversion_factor"))
                if value is not None:
                    return value
        return float(self.config[CONF_CONVERSION_FACTOR])

    @property
    def gas_rate_net(self) -> float:
        return float(self.config[CONF_GAS_RATE_NET])

    @property
    def dist_var_rate_net(self) -> float:
        return float(self.config[CONF_DIST_VAR_RATE_NET])

    @property
    def subscription_net(self) -> float:
        return float(self.config[CONF_SUBSCRIPTION_NET])

    @property
    def dist_fixed_net(self) -> float:
        return float(self.config[CONF_DIST_FIXED_NET])

    @property
    def vat(self) -> float:
        return float(self.config[CONF_VAT])

    @property
    def pending_meter_m3(self) -> float | None:
        return _as_float(self.data.get("pending_meter_m3"))

    @property
    def co_m3_per_kwh(self) -> float | None:
        return _as_float(self.data.get("calibration", {}).get("co_m3_per_kwh"))

    @property
    def dhw_m3_per_kwh(self) -> float | None:
        return _as_float(self.data.get("calibration", {}).get("dhw_m3_per_kwh"))

    @property
    def effective_m3_per_kwh(self) -> float | None:
        return _as_float(
            self.data.get("calibration", {}).get("effective_m3_per_kwh")
        )

    async def async_set_pending_meter(self, value: float) -> None:
        self.data["pending_meter_m3"] = round(float(value), 3)
        await self.async_save()
        self.async_notify()

    def _readings(self) -> list[dict[str, Any]]:
        readings = self.data.get("manual_readings")
        return readings if isinstance(readings, list) else []

    def _last_reading(self) -> dict[str, Any] | None:
        readings = self._readings()
        return readings[-1] if readings else None

    async def async_refresh_source_snapshot(
        self,
        *,
        notify: bool = True,
        raise_on_error: bool = False,
    ) -> RecorderSnapshot | None:
        """Refresh the cached coherent Recorder sum snapshot."""
        async with self._refresh_lock:
            try:
                snapshot = await async_get_recorder_snapshot(
                    self.hass,
                    self.heating_entity,
                    self.dhw_entity,
                )
            except (ValueError, RuntimeError) as err:
                self.snapshot_error = str(err)
                _LOGGER.debug("Could not refresh DUON Gaz Recorder snapshot: %s", err)
                if notify:
                    self.async_notify()
                if raise_on_error:
                    raise ValueError(str(err)) from err
                return None

            self.current_snapshot = snapshot
            self.snapshot_error = None
            if notify:
                self.async_notify()
            return snapshot

    def _interval_rows(self) -> list[tuple[float, float, float]]:
        """Return valid calibration intervals as (CO kWh, CWU kWh, physical m3)."""
        rows: list[tuple[float, float, float]] = []
        readings = self._readings()

        for previous, current in zip(readings, readings[1:]):
            if _reading_excluded(previous) or _reading_excluded(current):
                continue

            previous_meter = _as_float(previous.get("meter_m3"))
            current_meter = _as_float(current.get("meter_m3"))
            if previous_meter is None or current_meter is None:
                continue
            meter_delta = current_meter - previous_meter
            if meter_delta <= 0:
                continue

            curated = current.get("interval")
            if isinstance(curated, dict):
                co_delta = _as_float(curated.get("co_kwh"))
                dhw_delta = _as_float(curated.get("dhw_kwh"))
            else:
                previous_values = _reading_recorder_values(previous)
                current_values = _reading_recorder_values(current)
                if previous_values is None or current_values is None:
                    continue
                co_delta = current_values[0] - previous_values[0]
                dhw_delta = current_values[1] - previous_values[1]

            if co_delta is None or dhw_delta is None:
                continue
            if co_delta < 0 or dhw_delta < 0 or co_delta + dhw_delta <= 0:
                continue

            rows.append((co_delta, dhw_delta, meter_delta))

        return rows

    def _recalculate_calibration(self) -> None:
        rows = self._interval_rows()
        calibration = self.data.setdefault("calibration", {})
        calibration["sample_count"] = len(rows)

        if len(rows) < 2:
            return

        fit = _robust_two_component_fit(rows)
        if fit is None:
            return

        co_coeff, dhw_coeff, mae = fit
        if not (
            _MIN_CALIBRATION_COEFF <= co_coeff <= _MAX_CALIBRATION_COEFF
            and _MIN_CALIBRATION_COEFF <= dhw_coeff <= _MAX_CALIBRATION_COEFF
        ):
            _LOGGER.warning(
                "Ignoring implausible DUON Gaz calibration: CO=%s CWU=%s",
                co_coeff,
                dhw_coeff,
            )
            return

        total_ariston = sum(co + dhw for co, dhw, _meter in rows)
        total_meter = sum(meter for _co, _dhw, meter in rows)
        effective = total_meter / total_ariston if total_ariston > 0 else None

        calibration.update(
            {
                "co_m3_per_kwh": co_coeff,
                "dhw_m3_per_kwh": dhw_coeff,
                "effective_m3_per_kwh": effective,
                "method": "robust_two_component_least_squares",
                "mae_m3": mae,
                "updated_at": dt_util.utcnow().isoformat(),
            }
        )

    async def async_confirm_meter(self) -> None:
        """Save pending meter value with a coherent Recorder statistics snapshot."""
        pending = self.pending_meter_m3
        if pending is None:
            raise ValueError("Najpierw wpisz stan gazomierza.")

        last = self._last_reading()
        if last and pending < float(last["meter_m3"]):
            raise ValueError("Nowy stan gazomierza nie może być mniejszy od poprzedniego.")

        snapshot = await self.async_refresh_source_snapshot(
            notify=False, raise_on_error=True
        )
        if snapshot is None:
            raise ValueError("Nie udało się pobrać statystyk Recorder.")

        now = dt_util.utcnow()
        age = now - snapshot.timestamp.astimezone(now.tzinfo)
        if age > _MAX_CONFIRM_SNAPSHOT_AGE:
            raise ValueError(
                "Najnowsze wspólne statystyki Ariston są starsze niż 20 minut."
            )

        reading = {
            "timestamp": now.isoformat(),
            "meter_m3": pending,
            "source": "manual",
            "recorder": {
                "timestamp": snapshot.timestamp.isoformat(),
                "co_sum_kwh": snapshot.heating_sum_kwh,
                "dhw_sum_kwh": snapshot.dhw_sum_kwh,
            },
            "quality": {
                "state": "good",
                "exclude_from_calibration": False,
            },
        }

        if last:
            previous_meter = _as_float(last.get("meter_m3"))
            if previous_meter is not None:
                actual_delta = pending - previous_meter
                if actual_delta >= 0:
                    totals = self.data.setdefault("totals", {})
                    totals["provisional_energy_kwh"] = float(
                        totals.get("provisional_energy_kwh", 0.0)
                    ) + actual_delta * self.conversion_factor
                    variable_gross = (
                        actual_delta
                        * self.conversion_factor
                        * (self.gas_rate_net + self.dist_var_rate_net)
                        * (1 + self.vat)
                    )
                    totals["provisional_variable_cost_gross"] = float(
                        totals.get("provisional_variable_cost_gross", 0.0)
                    ) + variable_gross

        self.data.setdefault("manual_readings", []).append(reading)
        self.data["pending_meter_m3"] = pending
        self._recalculate_calibration()
        await self.async_save()
        self.async_notify()

    def current_source_delta_kwh(self) -> tuple[float, float]:
        """Return cached Recorder CO/CWU kWh since the last physical anchor."""
        last = self._last_reading()
        snapshot = self.current_snapshot
        if last is None or snapshot is None:
            return 0.0, 0.0
        values = _reading_recorder_values(last)
        if values is None:
            return 0.0, 0.0
        return (
            max(0.0, snapshot.heating_sum_kwh - values[0]),
            max(0.0, snapshot.dhw_sum_kwh - values[1]),
        )

    def current_split(self) -> tuple[float, float]:
        """Return estimated CO/CWU m3 since the last physical reading."""
        co_coeff = self.co_m3_per_kwh
        dhw_coeff = self.dhw_m3_per_kwh
        if co_coeff is None or dhw_coeff is None:
            return 0.0, 0.0
        co_kwh, dhw_kwh = self.current_source_delta_kwh()
        return co_kwh * co_coeff, dhw_kwh * dhw_coeff

    def estimated_current_delta_m3(self) -> float:
        co_m3, dhw_m3 = self.current_split()
        return co_m3 + dhw_m3

    def estimated_meter_m3(self) -> float | None:
        last = self._last_reading()
        if last is None:
            return self.pending_meter_m3
        return float(last["meter_m3"]) + self.estimated_current_delta_m3()

    def estimated_energy_kwh(self) -> float | None:
        if self._last_reading() is None:
            return None
        settled = float(
            self.data.get("totals", {}).get("provisional_energy_kwh", 0.0)
        )
        return settled + self.estimated_current_delta_m3() * self.conversion_factor

    def estimated_cost_gross(self) -> float | None:
        """Return provisional gross cost since the first physical anchor."""
        if self._last_reading() is None:
            return None

        current_variable = (
            self.estimated_current_delta_m3()
            * self.conversion_factor
            * (self.gas_rate_net + self.dist_var_rate_net)
            * (1 + self.vat)
        )
        now = dt_util.now()
        days = calendar.monthrange(now.year, now.month)[1]
        fixed_month_gross = (
            self.subscription_net + self.dist_fixed_net
        ) * (1 + self.vat)
        fixed_accrual = fixed_month_gross * (now.day / days)
        return (
            float(
                self.data.get("totals", {}).get(
                    "provisional_variable_cost_gross", 0.0
                )
            )
            + current_variable
            + fixed_accrual
        )

    def legacy_calibration_factor(self) -> float | None:
        """Return old-style aggregate calibration only as a compatibility diagnostic."""
        effective = self.effective_m3_per_kwh
        if effective is None:
            return None
        return effective * self.conversion_factor

    def status(self) -> str:
        if self._last_reading() is None:
            return "Oczekuje na pierwszy odczyt"
        if self.co_m3_per_kwh is None or self.dhw_m3_per_kwh is None:
            return "Oczekuje na kalibrację"
        if self.current_snapshot is None:
            return "Brak statystyk Ariston"
        return "Szacowane"

    def async_start(self) -> None:
        async def _scheduled_refresh(_now) -> None:
            await self.async_refresh_source_snapshot()

        self.listeners.append(
            async_track_time_interval(
                self.hass,
                _scheduled_refresh,
                _REFRESH_INTERVAL,
            )
        )

    def async_notify(self) -> None:
        self.hass.bus.async_fire(f"{self.entry_id}_duon_gaz_update")

    async def async_unload(self) -> None:
        for unsub in self.listeners:
            unsub()
        self.listeners.clear()
