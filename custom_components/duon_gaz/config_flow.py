"""Formularz konfiguracji integracji DUON Gaz."""
from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any, override

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_entry_oauth2_flow
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

_LOGGER = logging.getLogger(__name__)

# Minimalny zakres delegowany: tylko odczyt poczty oraz token odświeżania.
OAUTH2_SCOPES = ["offline_access", "Mail.Read"]


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


class DuonGazConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    """Obsługa konfiguracji DUON Gaz i opcjonalnego OAuth Microsoft Graph."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._outlook_settings: dict[str, Any] = {}

    @property
    @override
    def logger(self) -> logging.Logger:
        """Zwróć logger przepływu OAuth."""
        return _LOGGER

    @property
    @override
    def extra_authorize_data(self) -> dict[str, Any]:
        """Zażądaj tylko odczytu poczty i długotrwałego tokenu odświeżania."""
        return {
            "scope": " ".join(OAUTH2_SCOPES),
            "prompt": "consent",
        }

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

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Skonfiguruj Outlook i rozpocznij autoryzację Microsoft Graph."""
        entry = self._get_reconfigure_entry()
        current = dict(entry.data)

        if user_input is not None:
            settings = dict(user_input)
            password = str(settings.get(CONF_INVOICE_PDF_PASSWORD) or "")
            if not password:
                old_password = str(current.get(CONF_INVOICE_PDF_PASSWORD) or "")
                if old_password:
                    settings[CONF_INVOICE_PDF_PASSWORD] = old_password
                else:
                    return self.async_show_form(
                        step_id="reconfigure",
                        data_schema=_outlook_schema(current),
                        errors={CONF_INVOICE_PDF_PASSWORD: "required"},
                    )
            if not str(settings.get(CONF_OUTLOOK_FOLDER) or "").strip():
                return self.async_show_form(
                    step_id="reconfigure",
                    data_schema=_outlook_schema({**current, **settings}),
                    errors={CONF_OUTLOOK_FOLDER: "required"},
                )
            self._outlook_settings = settings
            return await self.async_step_pick_implementation()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_outlook_schema(current),
        )

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
        implementation = entry.data.get("auth_implementation")
        return await self.async_step_pick_implementation(
            {"implementation": implementation}
        )

    @override
    async def async_oauth_create_entry(self, data: dict[str, Any]) -> FlowResult:
        """Dołącz token OAuth do istniejącego wpisu DUON Gaz."""
        if self.source == SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            updated = dict(entry.data)
            updated.update(self._outlook_settings)
            updated.update(data)
            return self.async_update_reload_and_abort(entry, data=updated)

        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            updated = dict(entry.data)
            updated.update(data)
            return self.async_update_reload_and_abort(entry, data=updated)

        return self.async_abort(reason="single_instance_allowed")

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "DuonGazOptionsFlow":
        """Zwróć formularz edycji źródeł i parametrów rozliczeniowych."""
        return DuonGazOptionsFlow()


class DuonGazOptionsFlow(config_entries.OptionsFlow):
    """Edycja źródeł i parametrów rozliczeniowych istniejącego wpisu."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Zmień konfigurację rozliczeniową i zachowaj dane OAuth/Outlook."""
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
