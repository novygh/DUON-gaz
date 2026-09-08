"""Stałe integracji DUON Gaz."""
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

# Neutralny punkt startowy kalibracji fizycznej. Nie pochodzi z żadnej
# konkretnej instalacji, kotła ani faktury. Po zebraniu wystarczającej liczby
# kotwic gazomierza jest zastępowany kalibracją wyuczoną dla danej instalacji.
DEFAULT_CO_M3_PER_KWH = 0.1
DEFAULT_DHW_M3_PER_KWH = 0.1

STORAGE_VERSION = 2
STORAGE_KEY = "duon_gaz.data"
