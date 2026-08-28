"""Button platform for DUON Gaz."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .runtime import DuonGazRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[DuonGazRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([DuonConfirmMeterButton(entry.runtime_data)])


class DuonConfirmMeterButton(ButtonEntity):
    """Confirm pending physical meter reading."""

    _attr_has_entity_name = True
    _attr_name = "Zapisz odczyt gazomierza"
    _attr_unique_id = "duon_gaz_confirm_meter"
    _attr_icon = "mdi:content-save-check"

    def __init__(self, runtime: DuonGazRuntime) -> None:
        self.runtime = runtime

    async def async_press(self) -> None:
        try:
            await self.runtime.async_confirm_meter()
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
