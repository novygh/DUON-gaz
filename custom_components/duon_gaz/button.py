"""Button platform for DUON Gaz."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .runtime import DuonGazRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[DuonGazRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([DuonConfirmMeterButton(entry.runtime_data)])


def _submitted_meter_value(value: float) -> int:
    """Round to whole m3 for the DUON SMS, with .5 rounded up."""
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class DuonConfirmMeterButton(ButtonEntity):
    """Confirm pending physical meter reading."""

    _attr_has_entity_name = True
    _attr_name = "Zapisz odczyt gazomierza"
    _attr_unique_id = "duon_gaz_confirm_meter"
    _attr_icon = "mdi:content-save-check"

    def __init__(self, runtime: DuonGazRuntime) -> None:
        self.runtime = runtime

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.runtime.entry_id)},
            name="DUON Gaz",
            manufacturer="DUON",
            model="Rozliczenie gazu",
        )

    async def async_press(self) -> None:
        exact = self.runtime.pending_meter_m3
        if exact is None:
            raise HomeAssistantError("Najpierw wpisz stan gazomierza.")

        context = getattr(self, "_context", None)
        confirmed_by_user_id = context.user_id if context is not None else None
        entered_by_user_id = self.runtime.data.get("pending_entered_by_user_id")

        try:
            await self.runtime.async_confirm_meter()
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err

        reading = self.runtime._last_reading()
        if reading is not None:
            reading["meter_m3_exact"] = float(exact)
            reading["meter_m3_submitted"] = _submitted_meter_value(float(exact))
            reading["entered_by_user_id"] = entered_by_user_id
            reading["confirmed_by_user_id"] = confirmed_by_user_id
            reading["sms"] = {
                "status": "pending",
                "meter_m3": reading["meter_m3_submitted"],
                "target_user_id": entered_by_user_id or confirmed_by_user_id,
            }
            await self.runtime.async_save()
            self.runtime.async_notify()
