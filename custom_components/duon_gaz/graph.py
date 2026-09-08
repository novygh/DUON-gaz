"""Minimalny klient Microsoft Graph używany przez DUON Gaz."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from aiohttp import ClientError

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .microsoft_auth import MicrosoftTokenSession

_GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


class DuonGraphError(RuntimeError):
    """Błąd komunikacji z Microsoft Graph."""


class DuonGraphAuthError(DuonGraphError):
    """Błąd autoryzacji Microsoft Graph."""


@dataclass(frozen=True, slots=True)
class GraphPdfAttachment:
    """Pobrany plik PDF z wiadomości Graph."""

    message_id: str
    attachment_id: str
    name: str
    content: bytes


class DuonGraphClient:
    """Klient tylko do odczytu poczty potrzebnej do faktur DUON."""

    def __init__(self, hass: HomeAssistant, auth_session: MicrosoftTokenSession) -> None:
        self._auth_session = auth_session
        self._session = async_get_clientsession(hass)

    async def _headers(self) -> dict[str, str]:
        token = await self._auth_session.async_get_access_token()
        if not token:
            raise DuonGraphAuthError("Brak tokenu dostępu Microsoft Graph.")
        return {"Authorization": f"Bearer {token}"}

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            async with self._session.get(
                url,
                headers=await self._headers(),
                params=params,
            ) as response:
                if response.status in (401, 403):
                    detail = (await response.text())[:500]
                    raise DuonGraphAuthError(
                        f"Microsoft Graph odrzucił autoryzację ({response.status}): {detail}"
                    )
                if response.status >= 400:
                    detail = (await response.text())[:500]
                    raise DuonGraphError(
                        f"Microsoft Graph zwrócił HTTP {response.status}: {detail}"
                    )
                value = await response.json()
        except ClientError as err:
            raise DuonGraphError(f"Błąd połączenia z Microsoft Graph: {err}") from err

        if not isinstance(value, dict):
            raise DuonGraphError("Microsoft Graph zwrócił nieprawidłową odpowiedź JSON.")
        return value

    async def _list_collection(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        max_items: int = 500,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = url
        next_params = params

        while next_url and len(items) < max_items:
            data = await self._get_json(next_url, params=next_params)
            next_params = None
            raw = data.get("value", [])
            if not isinstance(raw, list):
                raise DuonGraphError("Nieprawidłowa lista elementów Microsoft Graph.")
            items.extend(item for item in raw if isinstance(item, dict))
            next_value = data.get("@odata.nextLink")
            next_url = next_value if isinstance(next_value, str) else None

        return items[:max_items]

    async def async_find_mail_folder(self, query: str) -> dict[str, Any]:
        """Znajdź folder po dokładnej nazwie albo ścieżce, także zagnieżdżony."""
        wanted = query.strip().strip("/").casefold()
        if not wanted:
            raise ValueError("Nazwa folderu Outlook nie może być pusta.")

        roots = await self._list_collection(
            f"{_GRAPH_ROOT}/me/mailFolders",
            params={
                "$top": "100",
                "$select": "id,displayName,parentFolderId,childFolderCount",
            },
            max_items=200,
        )

        queue: list[tuple[dict[str, Any], str]] = []
        for folder in roots:
            display = str(folder.get("displayName") or "")
            queue.append((folder, display))

        seen: set[str] = set()
        candidates: list[tuple[dict[str, Any], str]] = []

        while queue and len(seen) < 500:
            folder, path = queue.pop(0)
            folder_id = str(folder.get("id") or "")
            if not folder_id or folder_id in seen:
                continue
            seen.add(folder_id)

            display = str(folder.get("displayName") or "")
            if path.casefold() == wanted:
                return {**folder, "path": path}
            if display.casefold() == wanted:
                candidates.append((folder, path))

            child_count = int(folder.get("childFolderCount") or 0)
            if child_count <= 0:
                continue

            children = await self._list_collection(
                f"{_GRAPH_ROOT}/me/mailFolders/{quote(folder_id, safe='')}/childFolders",
                params={
                    "$top": "100",
                    "$select": "id,displayName,parentFolderId,childFolderCount",
                },
                max_items=200,
            )
            for child in children:
                child_name = str(child.get("displayName") or "")
                child_path = f"{path}/{child_name}" if path else child_name
                queue.append((child, child_path))

        if len(candidates) == 1:
            folder, path = candidates[0]
            return {**folder, "path": path}
        if len(candidates) > 1:
            paths = ", ".join(path for _folder, path in candidates[:10])
            raise DuonGraphError(
                f"Nazwa folderu Outlook jest niejednoznaczna. Użyj pełnej ścieżki: {paths}"
            )
        raise DuonGraphError(f"Nie znaleziono folderu Outlook: {query}")

    async def async_list_matching_messages(
        self,
        folder_id: str,
        *,
        sender: str,
        subject: str,
        max_messages: int = 250,
    ) -> list[dict[str, Any]]:
        """Pobierz wiadomości z folderu i odfiltruj DUON po nadawcy i temacie."""
        messages = await self._list_collection(
            f"{_GRAPH_ROOT}/me/mailFolders/{quote(folder_id, safe='')}/messages",
            params={
                "$top": "50",
                "$orderby": "receivedDateTime desc",
                "$select": "id,subject,from,receivedDateTime,hasAttachments,internetMessageId",
            },
            max_items=max_messages,
        )

        wanted_sender = sender.strip().casefold()
        wanted_subject = subject.strip().casefold()
        matched: list[dict[str, Any]] = []

        for message in messages:
            if not bool(message.get("hasAttachments")):
                continue
            message_subject = str(message.get("subject") or "").strip().casefold()
            sender_obj = message.get("from")
            address = ""
            if isinstance(sender_obj, dict):
                email_obj = sender_obj.get("emailAddress")
                if isinstance(email_obj, dict):
                    address = str(email_obj.get("address") or "").strip().casefold()
            if message_subject != wanted_subject or address != wanted_sender:
                continue
            matched.append(message)

        matched.sort(key=lambda item: str(item.get("receivedDateTime") or ""))
        return matched

    async def async_get_pdf_attachments(self, message_id: str) -> list[GraphPdfAttachment]:
        """Pobierz nieosadzone załączniki PDF jednej wiadomości."""
        encoded_message = quote(message_id, safe="")
        attachments = await self._list_collection(
            f"{_GRAPH_ROOT}/me/messages/{encoded_message}/attachments",
            params={"$top": "100"},
            max_items=100,
        )

        result: list[GraphPdfAttachment] = []
        for item in attachments:
            if item.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue
            if bool(item.get("isInline")):
                continue
            name = str(item.get("name") or "")
            if not name.lower().endswith(".pdf"):
                continue
            size = int(item.get("size") or 0)
            if size > _MAX_ATTACHMENT_BYTES:
                raise DuonGraphError(
                    f"Załącznik {name} jest większy niż {_MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB."
                )

            attachment_id = str(item.get("id") or "")
            if not attachment_id:
                continue
            detail = item
            if not detail.get("contentBytes"):
                detail = await self._get_json(
                    f"{_GRAPH_ROOT}/me/messages/{encoded_message}/attachments/{quote(attachment_id, safe='')}"
                )
            encoded = detail.get("contentBytes")
            if not isinstance(encoded, str) or not encoded:
                raise DuonGraphError(f"Załącznik {name} nie zawiera danych pliku.")
            try:
                content = base64.b64decode(encoded, validate=True)
            except ValueError as err:
                raise DuonGraphError(
                    f"Nieprawidłowe dane base64 załącznika {name}."
                ) from err
            if len(content) > _MAX_ATTACHMENT_BYTES:
                raise DuonGraphError(f"Załącznik {name} przekracza dozwolony rozmiar.")
            result.append(
                GraphPdfAttachment(
                    message_id=message_id,
                    attachment_id=attachment_id,
                    name=name,
                    content=content,
                )
            )

        return result
