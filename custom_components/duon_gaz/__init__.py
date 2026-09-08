"""DUON Gaz integration."""
from __future__ import annotations

import logging
from typing import TypeAlias

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_RECORDER_HOURLY_STATISTICS_GENERATED
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .canonical_history import CanonicalHistoryError
from .canonical_statistics import (
    async_refresh_canonical_tail_statistics,
    forget_publication_lock,
)
from .const import DOMAIN, PLATFORMS
from .history_import import async_import_history_file
from .runtime import DuonGazRuntime

_LOGGER = logging.getLogger(__name__)

DuonGazConfigEntry: TypeAlias = ConfigEntry[DuonGazRuntime]

SERVICE_IMPORT_HISTORY = "import_history"
SERVICE_REFRESH_TAIL = "refresh_tail"

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

    async def _handle_refresh_tail(_call: ServiceCall) -> None:
        try:
            await async_refresh_canonical_tail_statistics(
                runtime,
                reason="manual_service",
            )
        except (CanonicalHistoryError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err

    async def _handle_hourly_statistics(_event: Event) -> None:
        try:
            result = await async_refresh_canonical_tail_statistics(
                runtime,
                reason="recorder_hourly_statistics_generated",
            )
        except (CanonicalHistoryError, ValueError, RuntimeError) as err:
            _LOGGER.warning("DUON Gaz automatic Recorder tail refresh failed: %s", err)
            return

        if result.get("status") == "skipped":
            _LOGGER.debug(
                "DUON Gaz automatic Recorder tail refresh skipped: %s",
                result.get("reason"),
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_HISTORY,
        _handle_import_history,
        schema=_IMPORT_HISTORY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_TAIL,
        _handle_refresh_tail,
        schema=vol.Schema({}),
    )

    runtime.listeners.append(
        hass.bus.async_listen(
            EVENT_RECORDER_HOURLY_STATISTICS_GENERATED,
            _handle_hourly_statistics,
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DuonGazConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.services.async_remove(DOMAIN, SERVICE_IMPORT_HISTORY)
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH_TAIL)
        await entry.runtime_data.async_unload()
        forget_publication_lock(entry.entry_id)
    return unload_ok
