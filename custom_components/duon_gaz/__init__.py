"""DUON Gaz integration."""
from __future__ import annotations

from typing import TypeAlias

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .runtime import DuonGazRuntime

DuonGazConfigEntry: TypeAlias = ConfigEntry[DuonGazRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: DuonGazConfigEntry) -> bool:
    """Set up DUON Gaz from a config entry."""
    runtime = DuonGazRuntime(hass, entry.entry_id, dict(entry.data))
    await runtime.async_load()
    entry.runtime_data = runtime
    runtime.async_start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DuonGazConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_unload()
    return unload_ok
