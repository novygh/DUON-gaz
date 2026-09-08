"""Formularz konfiguracji integracji DUON Gaz."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, override

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_CONVERSION_FACTOR,
    CONF_DHW_ENTITY,
    CONF_DIST_FIXED_NET,
    CONF_DIST_VAR_RATE_NET,
    CONF_GAS_RATE_NET,
    CONF_HEATING_ENTITY,
    CONF_INVOICE_PDF_PASSWORD,
    CONF_MICROSOFT_CLIENT_ID,
    CONF_MICROSOFT_TOKEN,
    CONF_OUTLOOK_CHECK_HOUR,
    CONF_OUTLOOK_FOLDER,
    CONF_OUTLOOK_SENDER,
    CONF_OUTLOOK_SUBJECT,
    CONF_SUBSCRIPTION_NET,
    CONF_VAT,
    DEFAULT_OUTLOOK_CHECK_HOUR,
    DEFAULT_OUTLOOK_SENDER,
    DEFAULT_OUTLOOK_SUBJECT,
    DOMAIN,
)
from .microsoft_auth import (
    DuonMicrosoftAuthError,
    async_poll_device_code,
    async_request_device_code,
)


def _configuration_schema() -> vol.Schema:
    """Zbuduj wspólny schemat konfiguracji bez instalacyjnych wartości domyślnych."""
    return vol.Schema(
        {
            vol.Required(CONF_HEATING_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_DHW_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_CONVERSION_FACTOR): vol.All(
                vol.Coerce(float), vol.Range(min=8.0, max=14.0)
            ),
            vol.Required(CONF_GAS_RATE_NET): vol.All(
                vol.Coerce(float), vol.Range(min=0.0, max=5.0)
            ),
            vol.Required(CONF_DIST_VAR_RATE_NET): vol.All(
                vol.Coerce(float), vol.Range(min=0.0, max=5.0)
            ),
            vol.Required(CONF_SUBSCRIPTION_NET): vol.All(
                vol.Coerce(float), vol.Range(min=0.0, max=500.0)
            ),
            vol.Required(CONF_DIST_FIXED_NET): vol.All(
                vol.Coerce(float), vol.Range(min=0.0, max=500.0)
            ),
            vol.Required(CONF_VAT): vol.All(
                vol.Coerce(float), vol.Range(min=0.0, max=1.0)
            ),
        }
    )


def _outlook_schema(current: Mapping[str, Any]) -> vol.Schema:
    """Zbuduj formularz jednorazowego połączenia Outlook i ustawień faktur."""
    password_selector = TextSelector(
        TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
    )
    schema: dict[Any, Any] = {
        vol.Required(
            CONF_MICROSOFT_CLIENT_ID,
            default=str(current.get(CONF_MICROSOFT_CLIENT_ID) or ""),
        ): str,
        vol.Required(
            CONF_OUTLOOK_FOLDER,
            default=str(current.get(CONF_OUTLOOK_FOLDER) or ""),
        ): str,
        vol.Required(
            CONF_OUTLOOK_SENDER,
            default=str(current.get(CONF_OUTLOOK_SENDER) or DEFAULT_OUTLOOK_SENDER),
        ): str,
        vol.Required(
            CONF_OUTLOOK_SUBJECT,
            default=str(current.get(CONF_OUTLOOK_SUBJECT) or DEFAULT_OUTLOOK_SUBJECT),
        ): str,
        vol.Required(
            CONF_OUTLOOK_CHECK_HOUR,
            default=int(current.get(CONF_OUTLOOK_CHECK_HOUR, DEFAULT_OUTLOOK_CHECK_HOUR)),
        ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
    }
    if current.get(CONF_INVOICE_PDF_PASSWORD):
        schema[vol.Optional(CONF_INVOICE_PDF_PASSWORD)] = password_selector
    else:
        schema[vol.Required(CONF_INVOICE_PDF_PASSWORD)] = password_selector
    return vol.Schema(schema)


def _validate_sources(user_input: dict[str, Any]) -> dict[str, str]:
    """Sprawdź podstawowe zależności między źródłami."""
    if user_input[CONF_HEATING_ENTITY] == user_input[CONF_DHW_ENTITY]:
        return {"base": "same_entity"}
    return {}


class DuonGazConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Obsługa konfiguracji DUON Gaz i Device Code Flow Microsoft."""

    VERSION = 1

    def __init__(self) -> None:
        self._outlook_settings: dict[str, Any] = {}
        self._device_info: dict[str, Any] = {}
        self._login_task: asyncio.Task[dict[str, Any]] | None = None
        self._token_result: dict[str, Any] = {}
        self._auth_error = ""

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Utwórz podstawowy wpis integracji bez wymuszania Outlooka."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        schema = _configuration_schema()

        if user_input is not None:
            errors = _validate_sources(user_input)
            if not errors:
                return self.async_create_entry(title="DUON Gaz", data=user_input)
            schema = self.add_suggested_values_to_schema(schema, user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def _async_start_device_auth(self, client_id: str) -> FlowResult:
        """Pobierz kod urządzenia i uruchom oczekiwanie na logowanie."""
        try:
            self._device_info = await async_request_device_code(self.hass, client_id)
        except DuonMicrosoftAuthError as err:
            self._auth_error = str(err)
            return self.async_show_form(
                step_id="outlook_auth_error",
                description_placeholders={"error": self._auth_error},
            )

        self._login_task = self.hass.async_create_task(
            async_poll_device_code(self.hass, client_id, self._device_info)
        )
        return await self.async_step_outlook_authorize()

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Skonfiguruj Outlook i rozpocznij logowanie kodem urządzenia."""
        entry = self._get_reconfigure_entry()
        current = dict(entry.data)

        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_outlook_schema(current),
            )

        settings = dict(user_input)
        errors: dict[str, str] = {}

        client_id = str(settings.get(CONF_MICROSOFT_CLIENT_ID) or "").strip()
        if not client_id:
            errors[CONF_MICROSOFT_CLIENT_ID] = "required"
        else:
            settings[CONF_MICROSOFT_CLIENT_ID] = client_id

        folder = str(settings.get(CONF_OUTLOOK_FOLDER) or "").strip()
        if not folder:
            errors[CONF_OUTLOOK_FOLDER] = "required"
        else:
            settings[CONF_OUTLOOK_FOLDER] = folder

        password = str(settings.get(CONF_INVOICE_PDF_PASSWORD) or "")
        if not password:
            old_password = str(current.get(CONF_INVOICE_PDF_PASSWORD) or "")
            if old_password:
                settings[CONF_INVOICE_PDF_PASSWORD] = old_password
            else:
                errors[CONF_INVOICE_PDF_PASSWORD] = "required"

        if errors:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_outlook_schema({**current, **settings}),
                errors=errors,
            )

        self._outlook_settings = settings
        return await self._async_start_device_auth(client_id)

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> FlowResult:
        """Rozpocznij ponowne uwierzytelnienie Microsoft."""
        del entry_data
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Potwierdź ponowne połączenie konta Microsoft."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")

        entry = self._get_reauth_entry()
        client_id = str(entry.data.get(CONF_MICROSOFT_CLIENT_ID) or "").strip()
        if not client_id:
            return self.async_abort(reason="missing_client_id")
        self._outlook_settings = dict(entry.data)
        return await self._async_start_device_auth(client_id)

    async def async_step_outlook_authorize(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Pokaż kod urządzenia i poczekaj na potwierdzenie Microsoft."""
        del user_input
        if self._login_task is None:
            return self.async_abort(reason="auth_session_missing")

        if self._login_task.done():
            error = self._login_task.exception()
            if error is not None:
                self._auth_error = str(error)
                return self.async_show_progress_done(
                    next_step_id="outlook_auth_error"
                )
            self._token_result = self._login_task.result()
            return self.async_show_progress_done(next_step_id="outlook_finish")

        return self.async_show_progress(
            step_id="outlook_authorize",
            progress_action="wait_for_microsoft",
            description_placeholders={
                "url": str(self._device_info.get("verification_uri") or ""),
                "code": str(self._device_info.get("user_code") or ""),
            },
            progress_task=self._login_task,
        )

    async def async_step_outlook_finish(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Zapisz token Device Code i przeładuj tylko wpis DUON Gaz."""
        del user_input
        if not self._token_result:
            return self.async_abort(reason="auth_session_missing")

        if self.source == SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            updated = dict(entry.data)
            updated.update(self._outlook_settings)
        elif self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            updated = dict(entry.data)
        else:
            return self.async_abort(reason="single_instance_allowed")

        updated[CONF_MICROSOFT_TOKEN] = self._token_result
        # Usuń dane starego modelu Application Credentials przy migracji 0.3.4.
        updated.pop("auth_implementation", None)
        updated.pop("token", None)
        return self.async_update_reload_and_abort(entry, data=updated)

    async def async_step_outlook_auth_error(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Pokaż błąd logowania i pozwól spróbować ponownie."""
        if user_input is None:
            return self.async_show_form(
                step_id="outlook_auth_error",
                description_placeholders={"error": self._auth_error or "Nieznany błąd."},
            )

        self._login_task = None
        self._device_info = {}
        self._token_result = {}

        if self.source == SOURCE_RECONFIGURE:
            client_id = str(
                self._outlook_settings.get(CONF_MICROSOFT_CLIENT_ID) or ""
            ).strip()
        elif self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            client_id = str(entry.data.get(CONF_MICROSOFT_CLIENT_ID) or "").strip()
        else:
            return self.async_abort(reason="single_instance_allowed")

        if not client_id:
            return self.async_abort(reason="missing_client_id")
        return await self._async_start_device_auth(client_id)

    @staticmethod
    @callback
    @override
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "DuonGazOptionsFlow":
        """Zwróć formularz edycji źródeł i parametrów rozliczeniowych."""
        del config_entry
        return DuonGazOptionsFlow()


class DuonGazOptionsFlow(config_entries.OptionsFlow):
    """Edycja źródeł i parametrów rozliczeniowych istniejącego wpisu."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Zmień konfigurację rozliczeniową i zachowaj dane Outlook."""
        errors: dict[str, str] = {}
        current = dict(self.config_entry.data)
        schema = _configuration_schema()

        if user_input is not None:
            errors = _validate_sources(user_input)
            if not errors:
                updated = dict(self.config_entry.data)
                updated.update(user_input)
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data=updated,
                )
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self.config_entry.entry_id)
                )
                return self.async_create_entry(title="", data={})
            current.update(user_input)

        schema = self.add_suggested_values_to_schema(schema, current)
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
