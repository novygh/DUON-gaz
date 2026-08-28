"""Constants for DUON Gaz."""
from __future__ import annotations

DOMAIN = "duon_gaz"
PLATFORMS = ["sensor", "number", "button"]

CONF_HEATING_ENTITY = "heating_entity"
CONF_DHW_ENTITY = "dhw_entity"
CONF_CONVERSION_FACTOR = "conversion_factor"
CONF_GAS_RATE_NET = "gas_rate_net"
CONF_DIST_VAR_RATE_NET = "dist_var_rate_net"
CONF_SUBSCRIPTION_NET = "subscription_net"
CONF_DIST_FIXED_NET = "dist_fixed_net"
CONF_VAT = "vat"

DEFAULT_CONVERSION_FACTOR = 11.334
DEFAULT_GAS_RATE_NET = 0.22684
DEFAULT_DIST_VAR_RATE_NET = 0.0854
DEFAULT_SUBSCRIPTION_NET = 8.00
DEFAULT_DIST_FIXED_NET = 8.39
DEFAULT_VAT = 0.23

STORAGE_VERSION = 1
STORAGE_KEY = "duon_gaz.data"
