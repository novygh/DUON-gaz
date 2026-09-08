"""Poświadczenia aplikacji Microsoft dla DUON Gaz."""
from __future__ import annotations

from homeassistant.components.application_credentials import AuthorizationServer
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_entry_oauth2_flow import (
    AUTH_CALLBACK_PATH,
    MY_AUTH_CALLBACK_PATH,
)

_AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Zwróć serwer OAuth Microsoft."""
    return AuthorizationServer(_AUTHORIZE_URL, _TOKEN_URL)


async def async_get_description_placeholders(hass: HomeAssistant) -> dict[str, str]:
    """Zwróć odnośniki używane w formularzu poświadczeń aplikacji."""
    if "my" in hass.config.components:
        redirect_url = MY_AUTH_CALLBACK_PATH
    else:
        ha_host = hass.config.external_url or "https://TWOJ_HOME_ASSISTANT"
        redirect_url = f"{ha_host}{AUTH_CALLBACK_PATH}"

    return {
        "redirect_url": redirect_url,
        "app_registration_url": "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
        "graph_permissions_url": "https://learn.microsoft.com/graph/permissions-reference#mailread",
    }
