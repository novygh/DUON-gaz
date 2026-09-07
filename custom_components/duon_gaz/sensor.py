"""Sensor platform for DUON Gaz."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .runtime import DuonGazRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[DuonGazRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    async_add_entities(
        [
            DuonMeterSensor(runtime),
            DuonEnergySensor(runtime),
            DuonCostSensor(runtime),
            DuonCurrentHeatingSensor(runtime),
            DuonCurrentDhwSensor(runtime),
            DuonConversionSensor(runtime),
            DuonCalibrationSensor(runtime),
            DuonHeatingCalibrationSensor(runtime),
            DuonDhwCalibrationSensor(runtime),
            DuonStatusSensor(runtime),
        ]
    )


class DuonBaseSensor(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, runtime: DuonGazRuntime) -> None:
        self.runtime = runtime
        self._unsub = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.runtime.entry_id)},
            name="DUON Gaz",
            manufacturer="DUON",
            model="Rozliczenie gazu",
        )

    async def async_added_to_hass(self) -> None:
        @callback
        def _refresh(_event: Event) -> None:
            self.async_write_ha_state()

        self._unsub = self.hass.bus.async_listen(
            f"{self.runtime.entry_id}_duon_gaz_update", _refresh
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None


class DuonMeterSensor(DuonBaseSensor):
    _attr_name = "Gaz zużycie"
    _attr_unique_id = "duon_gaz_meter"
    _attr_icon = "mdi:meter-gas"
    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS

    @property
    def native_value(self):
        value = self.runtime.estimated_meter_m3()
        return None if value is None else round(value, 3)

    @property
    def extra_state_attributes(self):
        last = self.runtime._last_reading()
        snapshot = self.runtime.current_snapshot
        calibration = self.runtime.data.get("calibration", {})
        return {
            "status": self.runtime.status(),
            "ostatni_potwierdzony_odczyt": None if last is None else last["meter_m3"],
            "ostatni_potwierdzony_czas": None if last is None else last["timestamp"],
            "recorder_snapshot": None
            if snapshot is None
            else snapshot.timestamp.isoformat(),
            "kalibracja_co_m3_kwh": self.runtime.co_m3_per_kwh,
            "kalibracja_cwu_m3_kwh": self.runtime.dhw_m3_per_kwh,
            "kalibracja_probki": calibration.get("sample_count", 0),
            "kalibracja_mae_m3": calibration.get("mae_m3"),
        }


class DuonEnergySensor(DuonBaseSensor):
    _attr_name = "Gaz energia"
    _attr_unique_id = "duon_gaz_energy"
    _attr_icon = "mdi:fire"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    @property
    def native_value(self):
        value = self.runtime.estimated_energy_kwh()
        return None if value is None else round(value, 2)

    @property
    def extra_state_attributes(self):
        return {
            "status": "provisional",
            "wspolczynnik_billingowy_kwh_m3": self.runtime.conversion_factor,
        }


class DuonCostSensor(DuonBaseSensor):
    _attr_name = "Gaz koszt całkowity"
    _attr_unique_id = "duon_gaz_total_cost"
    _attr_icon = "mdi:cash"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "PLN"

    @property
    def native_value(self):
        value = self.runtime.estimated_cost_gross()
        return None if value is None else round(value, 2)

    @property
    def extra_state_attributes(self):
        return {
            "status": "provisional",
            "cena_gazu_netto_pln_kwh": self.runtime.gas_rate_net,
            "dystrybucja_zmienna_netto_pln_kwh": self.runtime.dist_var_rate_net,
            "abonament_netto_pln_miesiac": self.runtime.subscription_net,
            "dystrybucja_stala_netto_pln_miesiac": self.runtime.dist_fixed_net,
            "vat": self.runtime.vat,
        }


class DuonCurrentHeatingSensor(DuonBaseSensor):
    _attr_name = "Gaz CO od odczytu"
    _attr_unique_id = "duon_gaz_current_heating"
    _attr_icon = "mdi:radiator"
    _attr_device_class = SensorDeviceClass.GAS
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS

    @property
    def native_value(self):
        return round(self.runtime.current_split()[0], 3)


class DuonCurrentDhwSensor(DuonBaseSensor):
    _attr_name = "Gaz CWU od odczytu"
    _attr_unique_id = "duon_gaz_current_dhw"
    _attr_icon = "mdi:water-boiler"
    _attr_device_class = SensorDeviceClass.GAS
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS

    @property
    def native_value(self):
        return round(self.runtime.current_split()[1], 3)


class DuonConversionSensor(DuonBaseSensor):
    _attr_name = "Współczynnik konwersji"
    _attr_unique_id = "duon_gaz_conversion_factor"
    _attr_icon = "mdi:calculator"

    @property
    def native_value(self):
        return self.runtime.conversion_factor

    @property
    def native_unit_of_measurement(self):
        return "kWh/m³"


class DuonCalibrationSensor(DuonBaseSensor):
    """Compatibility aggregate for the original single-factor entity."""

    _attr_name = "Korekta Ariston"
    _attr_unique_id = "duon_gaz_ariston_calibration"
    _attr_icon = "mdi:tune"

    @property
    def native_value(self):
        value = self.runtime.legacy_calibration_factor()
        return None if value is None else round(value, 5)

    @property
    def extra_state_attributes(self):
        return {
            "diagnostyczny": True,
            "uzywany_do_obliczen": False,
            "co_m3_kwh": self.runtime.co_m3_per_kwh,
            "cwu_m3_kwh": self.runtime.dhw_m3_per_kwh,
        }


class DuonHeatingCalibrationSensor(DuonBaseSensor):
    _attr_name = "Kalibracja Ariston CO"
    _attr_unique_id = "duon_gaz_ariston_heating_m3_per_kwh"
    _attr_icon = "mdi:radiator"
    _attr_native_unit_of_measurement = "m³/kWh"

    @property
    def native_value(self):
        value = self.runtime.co_m3_per_kwh
        return None if value is None else round(value, 6)


class DuonDhwCalibrationSensor(DuonBaseSensor):
    _attr_name = "Kalibracja Ariston CWU"
    _attr_unique_id = "duon_gaz_ariston_dhw_m3_per_kwh"
    _attr_icon = "mdi:water-boiler"
    _attr_native_unit_of_measurement = "m³/kWh"

    @property
    def native_value(self):
        value = self.runtime.dhw_m3_per_kwh
        return None if value is None else round(value, 6)


class DuonStatusSensor(DuonBaseSensor):
    _attr_name = "Status danych"
    _attr_unique_id = "duon_gaz_status"
    _attr_icon = "mdi:check-decagram-outline"

    @property
    def native_value(self):
        return self.runtime.status()

    @property
    def extra_state_attributes(self):
        attributes = {
            "blad_snapshotu": self.runtime.snapshot_error,
            "liczba_odczytow": len(self.runtime._readings()),
            "liczba_korekt": len(self.runtime.data.get("corrections", [])),
            "liczba_faktur": len(self.runtime.data.get("billing_periods", [])),
        }
        preview = self.runtime.data.get("canonical_preview")
        if isinstance(preview, dict):
            attributes.update(
                {
                    "historia_kanoniczna_wygenerowana": preview.get("generated_at"),
                    "historia_opublikowana_do_recorder": preview.get(
                        "published_to_recorder", False
                    ),
                    "historia_punkty_zrodlowe": preview.get("source_point_count"),
                    "historia_kotwice_wszystkie": preview.get(
                        "anchor_count_total"
                    ),
                    "historia_kotwice": preview.get("anchor_count"),
                    "historia_kotwice_bez_recorder": preview.get(
                        "anchor_count_without_recorder"
                    ),
                    "historia_przedzialy": preview.get("interval_count"),
                    "historia_godziny": preview.get("canonical_hour_count"),
                    "historia_zrodlo_od": preview.get("source_start"),
                    "historia_zrodlo_do": preview.get("source_end"),
                    "historia_od": preview.get("start"),
                    "historia_do": preview.get("end"),
                    "historia_suma_fizyczna_m3": preview.get("physical_total_m3"),
                    "historia_suma_kanoniczna_m3": preview.get(
                        "canonical_total_m3"
                    ),
                    "historia_blad_domkniecia_m3": preview.get(
                        "closure_error_m3"
                    ),
                    "historia_luki_godziny": preview.get(
                        "reconstructed_gap_hours"
                    ),
                    "historia_rollback_kwh": preview.get(
                        "rollback_correction_kwh"
                    ),
                    "historia_rollback_nierozliczony_kwh": preview.get(
                        "unresolved_rollback_kwh"
                    ),
                    "historia_przedzialy_niska_pewnosc": preview.get(
                        "low_confidence_interval_count"
                    ),
                    "historia_przedzialy_niepewny_czas": preview.get(
                        "uncertain_anchor_interval_count"
                    ),
                    "historia_skala_min": preview.get("scale_factor_min"),
                    "historia_skala_max": preview.get("scale_factor_max"),
                    "historia_audyt": preview.get("audit_intervals", []),
                }
            )
        return attributes
