"""Button platform for DUON Gaz."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .canonical_history import CanonicalHistoryError
from .canonical_preview import async_rebuild_canonical_preview
from .canonical_statistics import async_publish_canonical_statistics
from .const import CONF_METER_NUMBER, DOMAIN
from .runtime import DuonGazRuntime
from .sms_rules import (
    build_sms_body,
    build_sms_intent_data,
    matching_android_registrations,
    submitted_meter_value,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[DuonGazRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(
        [
            DuonConfirmMeterButton(entry.runtime_data),
            DuonCanonicalPreviewButton(entry.runtime_data),
            DuonCanonicalPublishButton(entry.runtime_data),
        ]
    )


def _device_info(runtime: DuonGazRuntime) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, runtime.entry_id)},
        name="DUON Gaz",
        manufacturer="DUON",
        model="Rozliczenie gazu",
    )


class DuonConfirmMeterButton(ButtonEntity):
    """Confirm pending physical meter reading and open the DUON SMS composer."""

    _attr_has_entity_name = True
    _attr_name = "Zapisz i wyślij SMS"
    _attr_unique_id = "duon_gaz_confirm_meter"
    _attr_icon = "mdi:message-arrow-right-outline"

    def __init__(self, runtime: DuonGazRuntime) -> None:
        self.runtime = runtime

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self.runtime)

    def _target_user_id(self) -> tuple[str | None, str | None, str | None]:
        context = getattr(self, "_context", None)
        confirmed_by_user_id = context.user_id if context is not None else None
        entered_by_user_id = self.runtime.data.get("pending_entered_by_user_id")
        target_user_id = confirmed_by_user_id or entered_by_user_id
        return confirmed_by_user_id, entered_by_user_id, target_user_id

    def _target_mobile_registration(self, user_id: str) -> Mapping[str, Any]:
        registrations = [
            entry.data
            for entry in self.hass.config_entries.async_entries("mobile_app")
        ]
        matches = matching_android_registrations(registrations, user_id)
        if not matches:
            raise HomeAssistantError(
                "Nie znaleziono telefonu Android z Home Assistant Mobile App przypisanego do bieżącego użytkownika."
            )
        if len(matches) > 1:
            raise HomeAssistantError(
                "Znaleziono więcej niż jedno urządzenie Android przypisane do bieżącego użytkownika. Nie otwieram SMS na losowym urządzeniu."
            )
        return matches[0]

    async def _async_open_sms_composer(
        self,
        registration: Mapping[str, Any],
        body: str,
    ) -> None:
        if not self.hass.services.has_service("notify", "mobile_app"):
            raise HomeAssistantError(
                "Usługa notify.mobile_app nie jest dostępna. Sprawdź integrację Home Assistant Mobile App na telefonie."
            )

        webhook_id = str(registration.get("webhook_id") or "").strip()
        if not webhook_id:
            raise HomeAssistantError("Rejestracja Mobile App nie ma identyfikatora webhook.")

        context = getattr(self, "_context", None)
        await self.hass.services.async_call(
            "notify",
            "mobile_app",
            {
                "message": "command_activity",
                "target": [webhook_id],
                "data": build_sms_intent_data(body),
            },
            blocking=True,
            context=context,
        )

    async def async_press(self) -> None:
        exact = self.runtime.pending_meter_m3
        if exact is None:
            raise HomeAssistantError("Najpierw wpisz stan gazomierza.")

        meter_number = str(self.runtime.config.get(CONF_METER_NUMBER) or "")
        try:
            sms_body = build_sms_body(float(exact), meter_number)
        except ValueError as err:
            raise HomeAssistantError(
                "Brak poprawnego numeru licznika. Użyj Przekonfiguruj w DUON Gaz."
            ) from err

        confirmed_by_user_id, entered_by_user_id, target_user_id = self._target_user_id()
        if not target_user_id:
            raise HomeAssistantError(
                "Nie udało się ustalić użytkownika. Odczyt i SMS muszą zostać uruchomione z zalogowanego interfejsu Home Assistanta."
            )

        registration = self._target_mobile_registration(target_user_id)

        try:
            await self.runtime.async_confirm_meter()
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err

        reading = self.runtime._last_reading()
        if reading is None:
            raise HomeAssistantError("Odczyt został zapisany, ale nie można odnaleźć jego rekordu.")

        reading["meter_m3_exact"] = float(exact)
        reading["meter_m3_submitted"] = submitted_meter_value(float(exact))
        reading["entered_by_user_id"] = entered_by_user_id
        reading["confirmed_by_user_id"] = confirmed_by_user_id
        reading["sms"] = {
            "status": "prepared",
            "meter_m3": reading["meter_m3_submitted"],
            "target_user_id": target_user_id,
            "prepared_at": dt_util.utcnow().isoformat(),
        }
        await self.runtime.async_save()
        self.runtime.async_notify()

        try:
            await self._async_open_sms_composer(registration, sms_body)
        except HomeAssistantError as err:
            reading["sms"].update(
                {
                    "status": "error",
                    "error": str(err),
                    "failed_at": dt_util.utcnow().isoformat(),
                }
            )
            await self.runtime.async_save()
            self.runtime.async_notify()
            raise HomeAssistantError(
                f"Odczyt zapisano, ale nie udało się otworzyć aplikacji SMS: {err}"
            ) from err

        reading["sms"].update(
            {
                "status": "composer_requested",
                "device_name": registration.get("device_name"),
                "requested_at": dt_util.utcnow().isoformat(),
            }
        )
        await self.runtime.async_save()
        self.runtime.async_notify()


class DuonCanonicalPreviewButton(ButtonEntity):
    """Rebuild canonical history as a dry-run without publishing statistics."""

    _attr_has_entity_name = True
    _attr_name = "Przelicz historię DUON (podgląd)"
    _attr_unique_id = "duon_gaz_canonical_preview"
    _attr_icon = "mdi:chart-timeline-variant-shimmer"

    def __init__(self, runtime: DuonGazRuntime) -> None:
        self.runtime = runtime

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self.runtime)

    async def async_press(self) -> None:
        try:
            await async_rebuild_canonical_preview(self.runtime)
        except (CanonicalHistoryError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err


class DuonCanonicalPublishButton(ButtonEntity):
    """Publish settled canonical gas history through Recorder's statistics API."""

    _attr_has_entity_name = True
    _attr_name = "Opublikuj historię DUON do Recorder"
    _attr_unique_id = "duon_gaz_canonical_publish"
    _attr_icon = "mdi:database-import-outline"

    def __init__(self, runtime: DuonGazRuntime) -> None:
        self.runtime = runtime

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self.runtime)

    async def async_press(self) -> None:
        try:
            await async_publish_canonical_statistics(self.runtime)
        except (CanonicalHistoryError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err
