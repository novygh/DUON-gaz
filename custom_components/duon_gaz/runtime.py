"""Runtime model for DUON Gaz v0.1."""
from __future__ import annotations

from dataclasses import dataclass, field
import calendar
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    STORAGE_KEY,
    STORAGE_VERSION,
    CONF_HEATING_ENTITY,
    CONF_DHW_ENTITY,
    CONF_CONVERSION_FACTOR,
    CONF_GAS_RATE_NET,
    CONF_DIST_VAR_RATE_NET,
    CONF_SUBSCRIPTION_NET,
    CONF_DIST_FIXED_NET,
    CONF_VAT,
)


def _float_state(hass: HomeAssistant, entity_id: str) -> float | None:
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


@dataclass
class DuonGazRuntime:
    """Owns persistent readings and current calculations."""

    hass: HomeAssistant
    entry_id: str
    config: dict[str, Any]
    store: Store = field(init=False)
    listeners: list = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.store = Store(self.hass, STORAGE_VERSION, STORAGE_KEY)

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        self.data = stored or {
            "pending_meter_m3": None,
            "readings": [],
            "calibration_factor": 1.0,
            "cost_offset_gross": 0.0,
        }

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

    def source_totals(self) -> tuple[float | None, float | None]:
        return (
            _float_state(self.hass, self.heating_entity),
            _float_state(self.hass, self.dhw_entity),
        )

    @property
    def pending_meter_m3(self) -> float | None:
        value = self.data.get("pending_meter_m3")
        return None if value is None else float(value)

    async def async_set_pending_meter(self, value: float) -> None:
        self.data["pending_meter_m3"] = round(float(value), 3)
        await self.async_save()
        self.async_notify()

    def _last_reading(self) -> dict[str, Any] | None:
        readings = self.data.get("readings", [])
        return readings[-1] if readings else None

    async def async_confirm_meter(self) -> None:
        """Save pending meter value as a confirmed physical meter point."""
        pending = self.pending_meter_m3
        if pending is None:
            raise ValueError("Najpierw wpisz stan gazomierza.")

        last = self._last_reading()
        if last and pending < float(last["meter_m3"]):
            raise ValueError("Nowy stan gazomierza nie może być mniejszy od poprzedniego.")

        heat, dhw = self.source_totals()
        if heat is None or dhw is None:
            raise ValueError("Sensory Ariston są obecnie niedostępne.")

        now = dt_util.utcnow().isoformat()
        reading = {
            "timestamp": now,
            "meter_m3": pending,
            "ariston_heat_kwh": heat,
            "ariston_dhw_kwh": dhw,
            "source": "manual",
        }

        if last:
            actual_delta = pending - float(last["meter_m3"])
            ariston_delta = (heat - float(last["ariston_heat_kwh"])) + (
                dhw - float(last["ariston_dhw_kwh"])
            )
            if actual_delta >= 0 and ariston_delta > 0:
                factor = actual_delta / (ariston_delta / self.conversion_factor)
                if 0.25 <= factor <= 4.0:
                    self.data["calibration_factor"] = factor

            variable_gross = (
                actual_delta
                * self.conversion_factor
                * (self.gas_rate_net + self.dist_var_rate_net)
                * (1 + self.vat)
            )
            self.data["cost_offset_gross"] = (
                float(self.data.get("cost_offset_gross", 0.0)) + variable_gross
            )

        self.data.setdefault("readings", []).append(reading)
        self.data["pending_meter_m3"] = pending
        await self.async_save()
        self.async_notify()

    def estimated_current_delta_m3(self) -> float:
        last = self._last_reading()
        if last is None:
            return 0.0
        heat, dhw = self.source_totals()
        if heat is None or dhw is None:
            return 0.0
        delta_kwh = max(
            0.0,
            (heat - float(last["ariston_heat_kwh"]))
            + (dhw - float(last["ariston_dhw_kwh"])),
        )
        return delta_kwh / self.conversion_factor * float(
            self.data.get("calibration_factor", 1.0)
        )

    def estimated_meter_m3(self) -> float | None:
        last = self._last_reading()
        if last is None:
            return self.pending_meter_m3
        return float(last["meter_m3"]) + self.estimated_current_delta_m3()

    def current_split(self) -> tuple[float, float]:
        """Return current estimated CO/CWU m3 since last physical reading."""
        last = self._last_reading()
        if last is None:
            return 0.0, 0.0
        heat, dhw = self.source_totals()
        if heat is None or dhw is None:
            return 0.0, 0.0
        heat_delta = max(0.0, heat - float(last["ariston_heat_kwh"]))
        dhw_delta = max(0.0, dhw - float(last["ariston_dhw_kwh"]))
        total = heat_delta + dhw_delta
        m3 = self.estimated_current_delta_m3()
        if total <= 0:
            return 0.0, 0.0
        return m3 * heat_delta / total, m3 * dhw_delta / total

    def estimated_energy_kwh(self) -> float | None:
        meter = self.estimated_meter_m3()
        if meter is None:
            return None
        return meter * self.conversion_factor

    def estimated_cost_gross(self) -> float:
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
            float(self.data.get("cost_offset_gross", 0.0))
            + current_variable
            + fixed_accrual
        )

    def status(self) -> str:
        if self._last_reading() is None:
            return "Oczekuje na pierwszy odczyt"
        return "Szacowane"

    def async_start(self) -> None:
        @callback
        def _source_changed(event) -> None:
            self.async_notify()

        self.listeners.append(
            async_track_state_change_event(
                self.hass,
                [self.heating_entity, self.dhw_entity],
                _source_changed,
            )
        )

    def async_notify(self) -> None:
        self.hass.bus.async_fire(f"{self.entry_id}_duon_gaz_update")

    async def async_unload(self) -> None:
        for unsub in self.listeners:
            unsub()
        self.listeners.clear()
