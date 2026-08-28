"""Number platform for DUON Gaz."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .runtime import DuonGazRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[DuonGazRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([DuonPendingMeterNumber(entry.runtime_data)])


class DuonPendingMeterNumber(NumberEntity):
    """Pending physical gas meter reading."""

    _attr_has_entity_name = True
    _attr_name = "Stan gazomierza do wysłania"
    _attr_unique_id = "duon_gaz_pending_meter"
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_native_min_value = 0
    _attr_native_max_value = 999999
    _attr_native_step = 0.001
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:meter-gas"

    def __init__(self, runtime: DuonGazRuntime) -> None:
        self.runtime = runtime

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.runtime.entry_id)},
            name="DUON Gaz",
            manufacturer="DUON",
            model="Rozliczenie gazu",
        )

    @property
    def native_value(self) -> float | None:
        return self.runtime.pending_meter_m3

    async def async_set_native_value(self, value: float) -> None:
        await self.runtime.async_set_pending_meter(value)
        self.async_write_ha_state()
