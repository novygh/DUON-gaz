"""Uwierzytelnianie Microsoft Device Code dla DUON Gaz."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from aiohttp import ClientError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_MICROSOFT_CLIENT_ID, CONF_MICROSOFT_TOKEN

_DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
_SCOPE = "offline_access Mail.Read"
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_TOKEN_EXPIRY_MARGIN = 120


class DuonMicrosoftAuthError(RuntimeError):
    """Błąd uwierzytelniania Microsoft."""


class DuonMicrosoftReauthRequired(DuonMicrosoftAuthError):
    """Token Microsoft wymaga ponownego logowania użytkownika."""


def _error_detail(data: Any, fallback: str) -> str:
    """Zwróć krótki, niesekretny opis błędu Microsoft."""
    if not isinstance(data, dict):
        return fallback
    code = str(data.get("error") or "").strip()
    description = str(data.get("error_description") or "").strip()
    if description:
        description = description.splitlines()[0][:300]
    if code and description:
        return f"{code}: {description}"
    return code or description or fallback


def _normalize_token(
    data: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ujednolić odpowiedź tokenową i wyliczyć czas wygaśnięcia."""
    token = dict(previous or {})
    token.update(data)
    if not token.get("refresh_token") and previous:
        token["refresh_token"] = previous.get("refresh_token")
    try:
        expires_in = int(token.get("expires_in") or 3600)
    except (TypeError, ValueError):
        expires_in = 3600
    token["expires_in"] = expires_in
    token["expires_at"] = time.time() + expires_in
    return token


async def async_request_device_code(
    hass: HomeAssistant,
    client_id: str,
) -> dict[str, Any]:
    """Rozpocznij Device Code Flow dla osobistego konta Microsoft."""
    session = async_get_clientsession(hass)
    try:
        async with session.post(
            _DEVICE_CODE_URL,
            data={"client_id": client_id, "scope": _SCOPE},
        ) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise DuonMicrosoftAuthError(
                    _error_detail(data, f"Microsoft zwrócił HTTP {response.status}.")
                )
    except ClientError as err:
        raise DuonMicrosoftAuthError(
            f"Błąd połączenia z usługą logowania Microsoft: {err}"
        ) from err

    if not isinstance(data, dict):
        raise DuonMicrosoftAuthError("Microsoft zwrócił nieprawidłową odpowiedź.")
    required = ("device_code", "user_code", "verification_uri", "expires_in")
    if any(not data.get(key) for key in required):
        raise DuonMicrosoftAuthError(
            "Odpowiedź Microsoft nie zawiera kompletu danych Device Code."
        )
    return data


async def async_poll_device_code(
    hass: HomeAssistant,
    client_id: str,
    device_info: dict[str, Any],
) -> dict[str, Any]:
    """Czekaj na zatwierdzenie kodu przez użytkownika i odbierz tokeny."""
    session = async_get_clientsession(hass)
    device_code = str(device_info["device_code"])
    try:
        expires_in = int(device_info.get("expires_in") or 900)
    except (TypeError, ValueError):
        expires_in = 900
    try:
        interval = max(5, int(device_info.get("interval") or 5))
    except (TypeError, ValueError):
        interval = 5
    deadline = time.monotonic() + expires_in

    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        try:
            async with session.post(
                _TOKEN_URL,
                data={
                    "grant_type": _DEVICE_GRANT,
                    "client_id": client_id,
                    "device_code": device_code,
                },
            ) as response:
                data = await response.json(content_type=None)
        except ClientError as err:
            raise DuonMicrosoftAuthError(
                f"Błąd połączenia podczas logowania Microsoft: {err}"
            ) from err

        if response.status < 400:
            if not isinstance(data, dict) or not data.get("access_token"):
                raise DuonMicrosoftAuthError("Microsoft nie zwrócił tokenu dostępu.")
            token = _normalize_token(data)
            if not token.get("refresh_token"):
                raise DuonMicrosoftAuthError(
                    "Microsoft nie zwrócił tokenu odświeżania. "
                    "Sprawdź zgodę na offline_access."
                )
            return token

        code = str(data.get("error") if isinstance(data, dict) else "")
        if code == "authorization_pending":
            continue
        if code == "slow_down":
            interval += 5
            continue
        if code in {"authorization_declined", "expired_token", "bad_verification_code"}:
            raise DuonMicrosoftAuthError(
                _error_detail(data, "Logowanie Microsoft nie zostało zatwierdzone.")
            )
        raise DuonMicrosoftAuthError(
            _error_detail(data, f"Microsoft zwrócił HTTP {response.status}.")
        )

    raise DuonMicrosoftAuthError("Kod logowania Microsoft wygasł.")


class MicrosoftTokenSession:
    """Sesja publicznego klienta Microsoft bez sekretu aplikacji."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._session = async_get_clientsession(hass)
        self._lock = asyncio.Lock()

    @property
    def token(self) -> dict[str, Any]:
        """Zwróć aktualnie zapisany token."""
        value = self.entry.data.get(CONF_MICROSOFT_TOKEN)
        return dict(value) if isinstance(value, dict) else {}

    async def async_get_access_token(self) -> str:
        """Zwróć ważny access token, odświeżając go w razie potrzeby."""
        token = self.token
        access_token = str(token.get("access_token") or "")
        try:
            expires_at = float(token.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if access_token and expires_at > time.time() + _TOKEN_EXPIRY_MARGIN:
            return access_token

        async with self._lock:
            token = self.token
            access_token = str(token.get("access_token") or "")
            try:
                expires_at = float(token.get("expires_at") or 0)
            except (TypeError, ValueError):
                expires_at = 0
            if access_token and expires_at > time.time() + _TOKEN_EXPIRY_MARGIN:
                return access_token
            token = await self._async_refresh(token)
            access_token = str(token.get("access_token") or "")
            if not access_token:
                raise DuonMicrosoftReauthRequired(
                    "Microsoft nie zwrócił nowego tokenu dostępu."
                )
            return access_token

    async def _async_refresh(self, previous: dict[str, Any]) -> dict[str, Any]:
        """Odśwież token bez sekretu klienta i zapisz rotowany refresh token."""
        client_id = str(self.entry.data.get(CONF_MICROSOFT_CLIENT_ID) or "").strip()
        refresh_token = str(previous.get("refresh_token") or "")
        if not client_id or not refresh_token:
            raise DuonMicrosoftReauthRequired(
                "Brak danych potrzebnych do odnowienia logowania Microsoft."
            )

        try:
            async with self._session.post(
                _TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                    "scope": _SCOPE,
                },
            ) as response:
                data = await response.json(content_type=None)
        except ClientError as err:
            raise DuonMicrosoftAuthError(
                f"Błąd połączenia podczas odnawiania tokenu Microsoft: {err}"
            ) from err

        if response.status >= 400:
            code = str(data.get("error") if isinstance(data, dict) else "")
            detail = _error_detail(
                data,
                f"Microsoft zwrócił HTTP {response.status} podczas odnawiania tokenu.",
            )
            if code in {
                "invalid_grant",
                "interaction_required",
                "invalid_client",
                "unauthorized_client",
            }:
                raise DuonMicrosoftReauthRequired(detail)
            raise DuonMicrosoftAuthError(detail)

        if not isinstance(data, dict):
            raise DuonMicrosoftAuthError(
                "Microsoft zwrócił nieprawidłową odpowiedź przy odnawianiu tokenu."
            )

        token = _normalize_token(data, previous)
        updated = dict(self.entry.data)
        updated[CONF_MICROSOFT_TOKEN] = token
        self.hass.config_entries.async_update_entry(self.entry, data=updated)
        return token
