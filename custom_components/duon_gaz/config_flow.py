"""Formularz konfiguracji integracji DUON Gaz."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .const import (
    CONF_CONVERSION_FACTOR,
    CONF_DHW_ENTITY,
    CONF_DIST_FIXED_NET,
    CONF_DIST_VAR_RATE_NET,
    CONF_GAS_RATE_NET,
    CONF_HEATING_ENTITY,
    CONF_SUBSCRIPTION_NET,
    CONF_VAT,
    DOMAIN,
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


def _validate_sources(user_input: dict[str, Any]) -> dict[str, str]:
    """Sprawdź podstawowe zależności między źródłami."""
    if user_input[CONF_HEATING_ENTITY] == user_input[CONF_DHW_ENTITY]:
        return {"base": "same_entity"}
    return {}


class DuonGazConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Obsługa początkowej konfiguracji DUON Gaz."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Utwórz wpis integracji."""
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

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "DuonGazOptionsFlow":
        """Zwróć formularz edycji konfiguracji."""
        return DuonGazOptionsFlow()


class DuonGazOptionsFlow(config_entries.OptionsFlow):
    """Edycja źródeł i parametrów rozliczeniowych istniejącego wpisu."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Zmień konfigurację i przeładuj integrację."""
        errors: dict[str, str] = {}
        current = dict(self.config_entry.data)
        schema = _configuration_schema()

        if user_input is not None:
            errors = _validate_sources(user_input)
            if not errors:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data=user_input,
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
