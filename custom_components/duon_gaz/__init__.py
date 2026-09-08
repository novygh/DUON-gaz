"""Integracja DUON Gaz."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TypeAlias

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, EVENT_RECORDER_HOURLY_STATISTICS_GENERATED
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    HomeAssistantError,
    OAuth2TokenRequestError,
    OAuth2TokenRequestReauthError,
)
from homeassistant.helpers import config_entry_oauth2_flow, config_validation as cv
from homeassistant.helpers.event import async_call_later, async_track_time_change

from .canonical_history import CanonicalHistoryError
from .canonical_statistics import (
    async_refresh_canonical_tail_statistics,
    forget_publication_lock,
)
from .const import (
    CONF_INVOICE_PDF_PASSWORD,
    CONF_OUTLOOK_CHECK_HOUR,
    CONF_OUTLOOK_FOLDER,
    CONF_OUTLOOK_SENDER,
    CONF_OUTLOOK_SUBJECT,
    DEFAULT_OUTLOOK_CHECK_HOUR,
    DOMAIN,
    PLATFORMS,
)
from .graph import DuonGraphAuthError, DuonGraphClient, DuonGraphError
from .history_import import async_import_history_file
from .invoice_import import async_import_invoice
from .invoice_parser import DuonInvoiceParseError, parse_invoice_pdf
from .outlook_sync import DuonOutlookSynchronizer
from .runtime import DuonGazRuntime

_LOGGER = logging.getLogger(__name__)

DuonGazConfigEntry: TypeAlias = ConfigEntry[DuonGazRuntime]

SERVICE_IMPORT_HISTORY = "import_history"
SERVICE_IMPORT_INVOICE = "import_invoice"
SERVICE_REFRESH_TAIL = "refresh_tail"
SERVICE_SYNC_OUTLOOK = "sync_outlook"

_IMPORT_HISTORY_SCHEMA = vol.Schema(
    {
        vol.Optional("path", default="duon_gaz_history_seed.json"): cv.string,
        vol.Optional("replace", default=False): cv.boolean,
    }
)

_IMPORT_INVOICE_SCHEMA = vol.Schema(
    {
        vol.Required("path"): cv.string,
        vol.Optional("password"): cv.string,
        vol.Optional("source_message_id"): cv.string,
    }
)


def _config_file_path(hass: HomeAssistant, value: str) -> Path:
    """Zwróć bezpieczną ścieżkę do pliku znajdującego się w /config."""
    root = Path(hass.config.config_dir).resolve()
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as err:
        raise ValueError("Plik faktury musi znajdować się w katalogu /config.") from err
    if not candidate.is_file():
        raise ValueError(f"Nie znaleziono pliku faktury: {value}")
    return candidate


def _outlook_is_configured(entry: ConfigEntry) -> bool:
    """Sprawdź, czy wpis zawiera komplet danych potrzebnych do automatyzacji."""
    return bool(
        entry.data.get("auth_implementation")
        and entry.data.get(CONF_TOKEN)
        and entry.data.get(CONF_OUTLOOK_FOLDER)
        and entry.data.get(CONF_OUTLOOK_SENDER)
        and entry.data.get(CONF_OUTLOOK_SUBJECT)
        and entry.data.get(CONF_INVOICE_PDF_PASSWORD)
    )


async def async_setup_entry(hass: HomeAssistant, entry: DuonGazConfigEntry) -> bool:
    """Skonfiguruj DUON Gaz z wpisu integracji."""
    runtime = DuonGazRuntime(hass, entry.entry_id, dict(entry.data))
    await runtime.async_load()
    entry.runtime_data = runtime
    runtime.async_start()

    synchronizer: DuonOutlookSynchronizer | None = None
    if _outlook_is_configured(entry):
        try:
            implementation = (
                await config_entry_oauth2_flow.async_get_config_entry_implementation(
                    hass, entry
                )
            )
        except ValueError as err:
            # HA 2026.8 zgłasza ValueError, a nowsze wersje używają
            # UnknownImplementationError dziedziczącego po ValueError.
            _LOGGER.warning(
                "Nie można uruchomić automatyzacji Outlook DUON Gaz: %s", err
            )
            entry.async_start_reauth(hass)
        else:
            oauth_session = config_entry_oauth2_flow.OAuth2Session(
                hass,
                entry,
                implementation,
            )
            synchronizer = DuonOutlookSynchronizer(
                runtime,
                DuonGraphClient(hass, oauth_session),
                dict(entry.data),
            )

    async def _handle_import_history(call: ServiceCall) -> None:
        try:
            await async_import_history_file(
                runtime,
                call.data["path"],
                replace=call.data["replace"],
            )
        except (OSError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err

    async def _handle_import_invoice(call: ServiceCall) -> None:
        password = str(
            call.data.get("password")
            or entry.data.get(CONF_INVOICE_PDF_PASSWORD)
            or ""
        )
        if not password:
            raise HomeAssistantError(
                "Brak hasła do PDF. Skonfiguruj Outlook albo podaj hasło w wywołaniu usługi."
            )

        try:
            path = _config_file_path(hass, call.data["path"])
            invoice = await hass.async_add_executor_job(
                parse_invoice_pdf,
                path,
                password,
            )
            result = await async_import_invoice(
                runtime,
                invoice,
                source_message_id=call.data.get("source_message_id"),
                source_attachment_name=path.name,
            )
        except (DuonInvoiceParseError, OSError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err

        refresh_result = None
        if result.get("status") == "imported" and result.get("anchor_added", False):
            try:
                refresh_result = await async_refresh_canonical_tail_statistics(
                    runtime,
                    reason="invoice_import",
                )
            except (CanonicalHistoryError, ValueError, RuntimeError) as err:
                refresh_result = {
                    "status": "error",
                    "reason": str(err),
                }
                _LOGGER.warning(
                    "Faktura DUON została zaimportowana, ale przebudowa historii kanonicznej nie powiodła się: %s",
                    err,
                )

        runtime.data["last_invoice_import"] = {
            **result,
            "path": call.data["path"],
            "canonical_refresh": refresh_result,
        }
        await runtime.async_save()
        runtime.async_notify()

    async def _handle_refresh_tail(_call: ServiceCall) -> None:
        try:
            await async_refresh_canonical_tail_statistics(
                runtime,
                reason="manual_service",
            )
        except (CanonicalHistoryError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err

    async def _run_outlook_sync(
        *,
        reason: str,
        raise_service_error: bool,
    ) -> None:
        if synchronizer is None:
            if raise_service_error:
                raise HomeAssistantError(
                    "Outlook nie jest jeszcze połączony z DUON Gaz. Użyj akcji Przekonfiguruj / Połącz Outlook w integracji."
                )
            return

        try:
            result = await synchronizer.async_sync(reason=reason)
        except DuonGraphAuthError as err:
            entry.async_start_reauth(hass)
            _LOGGER.warning("Autoryzacja Outlook DUON Gaz wymaga odnowienia: %s", err)
            if raise_service_error:
                raise HomeAssistantError(str(err)) from err
            return
        except (DuonGraphError, ValueError, OAuth2TokenRequestError) as err:
            _LOGGER.warning("Synchronizacja Outlook DUON Gaz nie powiodła się: %s", err)
            if isinstance(err, OAuth2TokenRequestReauthError):
                entry.async_start_reauth(hass)
            if raise_service_error:
                raise HomeAssistantError(str(err)) from err
            return

        if result.get("status") != "ok":
            _LOGGER.warning(
                "Synchronizacja Outlook DUON Gaz zakończyła się częściowo: %s",
                result.get("errors"),
            )

    async def _handle_sync_outlook(_call: ServiceCall) -> None:
        await _run_outlook_sync(reason="manual_service", raise_service_error=True)

    async def _handle_hourly_statistics(_event: Event) -> None:
        try:
            result = await async_refresh_canonical_tail_statistics(
                runtime,
                reason="recorder_hourly_statistics_generated",
            )
        except (CanonicalHistoryError, ValueError, RuntimeError) as err:
            _LOGGER.warning(
                "Automatyczne odświeżenie bieżącego ogona DUON Gaz nie powiodło się: %s",
                err,
            )
            return

        if result.get("status") == "skipped":
            _LOGGER.debug(
                "Automatyczne odświeżenie bieżącego ogona DUON Gaz pominięto: %s",
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
        SERVICE_IMPORT_INVOICE,
        _handle_import_invoice,
        schema=_IMPORT_INVOICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_TAIL,
        _handle_refresh_tail,
        schema=vol.Schema({}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SYNC_OUTLOOK,
        _handle_sync_outlook,
        schema=vol.Schema({}),
    )

    runtime.listeners.append(
        hass.bus.async_listen(
            EVENT_RECORDER_HOURLY_STATISTICS_GENERATED,
            _handle_hourly_statistics,
        )
    )

    if synchronizer is not None:
        check_hour = int(
            entry.data.get(CONF_OUTLOOK_CHECK_HOUR, DEFAULT_OUTLOOK_CHECK_HOUR)
        )

        async def _scheduled_outlook_sync(_now) -> None:
            await _run_outlook_sync(
                reason="daily_schedule",
                raise_service_error=False,
            )

        async def _startup_outlook_sync(_now) -> None:
            await _run_outlook_sync(
                reason="startup",
                raise_service_error=False,
            )

        runtime.listeners.append(
            async_track_time_change(
                hass,
                _scheduled_outlook_sync,
                hour=check_hour,
                minute=0,
                second=0,
            )
        )
        runtime.listeners.append(
            async_call_later(hass, 30, _startup_outlook_sync)
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DuonGazConfigEntry) -> bool:
    """Wyładuj wpis integracji."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.services.async_remove(DOMAIN, SERVICE_IMPORT_HISTORY)
        hass.services.async_remove(DOMAIN, SERVICE_IMPORT_INVOICE)
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH_TAIL)
        hass.services.async_remove(DOMAIN, SERVICE_SYNC_OUTLOOK)
        await entry.runtime_data.async_unload()
        forget_publication_lock(entry.entry_id)
    return unload_ok
