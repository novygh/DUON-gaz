"""Config flow for DUON Gaz."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_HEATING_ENTITY,
    CONF_DHW_ENTITY,
    CONF_CONVERSION_FACTOR,
    CONF_GAS_RATE_NET,
    CONF_DIST_VAR_RATE_NET,
    CONF_SUBSCRIPTION_NET,
    CONF_DIST_FIXED_NET,
    CONF_VAT,
    DEFAULT_CONVERSION_FACTOR,
    DEFAULT_GAS_RATE_NET,
    DEFAULT_DIST_VAR_RATE_NET,
    DEFAULT_SUBSCRIPTION_NET,
    DEFAULT_DIST_FIXED_NET,
    DEFAULT_VAT,
)


class DuonGazConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for DUON Gaz."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Create the integration entry."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors = {}
        if user_input is not None:
            if user_input[CONF_HEATING_ENTITY] == user_input[CONF_DHW_ENTITY]:
                errors["base"] = "same_entity"
            else:
                return self.async_create_entry(title="DUON Gaz", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_HEATING_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(CONF_DHW_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(
                    CONF_CONVERSION_FACTOR, default=DEFAULT_CONVERSION_FACTOR
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=8.0, max=14.0, step=0.001, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_GAS_RATE_NET, default=DEFAULT_GAS_RATE_NET
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.0, max=5.0, step=0.00001, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_DIST_VAR_RATE_NET, default=DEFAULT_DIST_VAR_RATE_NET
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.0, max=5.0, step=0.0001, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_SUBSCRIPTION_NET, default=DEFAULT_SUBSCRIPTION_NET
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.0, max=500.0, step=0.01, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_DIST_FIXED_NET, default=DEFAULT_DIST_FIXED_NET
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.0, max=500.0, step=0.01, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(CONF_VAT, default=DEFAULT_VAT): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.0, max=1.0, step=0.01, mode=selector.NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
