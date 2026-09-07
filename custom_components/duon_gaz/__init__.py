"""DUON Gaz integration."""
from __future__ import annotations

from typing import TypeAlias

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .history_import import async_import_history_file
from .runtime import DuonGazRuntime

DuonGazConfigEntry: TypeAlias = ConfigEntry[DuonGazRuntime]

SERVICE_IMPORT_HISTORY = "import_history"

_IMPORT_HISTORY_SCHEMA = vol.Schema(
    {
        vol.Optional("path", default="duon_gaz_history_seed.json"): cv.string,
        vol.Optional("replace", default=False): cv.boolean,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: DuonGazConfigEntry) -> bool:
    """Set up DUON Gaz from a config entry."""
    runtime = DuonGazRuntime(hass, entry.entry_id, dict(entry.data))
    await runtime.async_load()
    entry.runtime_data = runtime
    runtime.async_start()

    async def _handle_import_history(call: ServiceCall) -> None:
        try:
            await async_import_history_file(
                runtime,
                call.data["path"],
                replace=call.data["replace"],
            )
        except (OSError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_HISTORY,
        _handle_import_history,
        schema=_IMPORT_HISTORY_SCHEMA,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DuonGazConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.services.async_remove(DOMAIN, SERVICE_IMPORT_HISTORY)
        await entry.runtime_data.async_unload()
    return unload_ok
