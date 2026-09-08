"""Platforma sensorów integracji DUON Gaz."""
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
from homeassistant.util import dt as dt_util

from .canonical_builder import async_build_canonical_bundle
from .canonical_costs import billing_period_fingerprint
from .const import DOMAIN
from .conversion_audit import ConversionAuditError, build_conversion_audit
from .publication_rules import active_anchor_fingerprint
from .runtime import DuonGazRuntime


def _status_pl(runtime: DuonGazRuntime) -> str:
    """Zwróć czytelny polski status danych."""
    status = runtime.status()
    calibration = runtime.data.get("calibration", {})
    if calibration.get("method") == "neutral_bootstrap" and status == "Szacowane":
        return "Szacowane — kalibracja startowa"
    return status


def _publication_status_pl(value):
    """Przetłumacz techniczny status publikacji do interfejsu."""
    return {
        "publishing": "publikowanie",
        "verified": "zweryfikowano",
        "skipped": "pominięto",
    }.get(value, value)


def _publication_mode_pl(value):
    """Przetłumacz techniczny tryb publikacji do interfejsu."""
    return {
        "settled_plus_provisional": "pełna historia z bieżącym ogonem",
        "provisional_tail_refresh": "odświeżenie bieżącego ogona",
    }.get(value, value)


def _publication_reason_pl(value):
    """Przetłumacz znane powody przebudowy, zachowując nieznane identyfikatory."""
    if not isinstance(value, str):
        return value
    mapping = {
        "manual_full_publish": "ręczna pełna publikacja",
        "manual_service": "ręczne odświeżenie usługi",
        "manual_tail_refresh": "ręczne odświeżenie ogona",
        "recorder_hourly_statistics_generated": "nowe godzinowe statystyki Recorder",
        "physical_anchor_changed": "zmiana fizycznej kotwicy",
        "heating_source_changed": "zmiana źródła CO",
        "dhw_source_changed": "zmiana źródła CWU",
        "calibration_changed": "zmiana kalibracji",
        "calibration_fingerprint_unknown": "nieznany odcisk kalibracji",
        "no_verified_canonical_publication": "brak zweryfikowanej publikacji kanonicznej",
        "no_provisional_tail": "brak bieżącego ogona",
    }
    return ": ".join(mapping.get(part, part) for part in value.split(":"))


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
            DuonConversionAuditSensor(runtime),
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
            "status": _status_pl(self.runtime),
            "ostatni_potwierdzony_odczyt": None if last is None else last["meter_m3"],
            "ostatni_potwierdzony_czas": None if last is None else last["timestamp"],
            "migawka_recorder": None
            if snapshot is None
            else snapshot.timestamp.isoformat(),
            "kalibracja_co_m3_kwh": self.runtime.co_m3_per_kwh,
            "kalibracja_cwu_m3_kwh": self.runtime.dhw_m3_per_kwh,
            "kalibracja_metoda": calibration.get("method"),
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
            "status": "szacowany",
            "wspolczynnik_rozliczeniowy_kwh_m3": self.runtime.conversion_factor,
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
            "status": "szacowany",
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


class DuonConversionAuditSensor(DuonBaseSensor):
    """Informacyjny bilans dryfu faktury względem lokalnej relacji Ariston."""

    _attr_name = "Audyt współczynnika konwersji"
    _attr_unique_id = "duon_gaz_conversion_audit"
    _attr_icon = "mdi:scale-balance"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "PLN"
    _attr_should_poll = False

    def __init__(self, runtime: DuonGazRuntime) -> None:
        super().__init__(runtime)
        self._audit: dict | None = None
        self._audit_fingerprint = None
        self._audit_unsub = None
        self._audit_task = None

    def _current_audit_fingerprint(self):
        return (
            billing_period_fingerprint(self.runtime.data.get("billing_periods", [])),
            active_anchor_fingerprint(self.runtime._readings()),
            self.runtime.heating_entity,
            self.runtime.dhw_entity,
            self.runtime.co_m3_per_kwh,
            self.runtime.dhw_m3_per_kwh,
        )

    @callback
    def _schedule_audit_refresh(self) -> None:
        fingerprint = self._current_audit_fingerprint()
        if fingerprint == self._audit_fingerprint:
            return
        if self._audit_task is not None and not self._audit_task.done():
            return
        self._audit_task = self.hass.async_create_task(
            self._async_refresh_audit(fingerprint)
        )

    async def _async_refresh_audit(self, fingerprint) -> None:
        try:
            build = await async_build_canonical_bundle(self.runtime)
            calibration = self.runtime.data.get("calibration", {})
            result = build_conversion_audit(
                build.settled.intervals,
                self.runtime.data.get("billing_periods", []),
                self.runtime._manual_readings(),
                timezone=dt_util.DEFAULT_TIME_ZONE,
                calibration_co_m3_per_kwh=self.runtime.co_m3_per_kwh,
                calibration_dhw_m3_per_kwh=self.runtime.dhw_m3_per_kwh,
                calibration_mae_m3=calibration.get("mae_m3"),
            )
            self._audit = result.data
        except (ConversionAuditError, ValueError, RuntimeError) as err:
            self._audit = {
                "status": "unavailable",
                "error": str(err),
            }
        self._audit_fingerprint = fingerprint
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _recalculate(_event: Event) -> None:
            self._schedule_audit_refresh()

        self._audit_unsub = self.hass.bus.async_listen(
            f"{self.runtime.entry_id}_duon_gaz_update", _recalculate
        )
        self._schedule_audit_refresh()

    async def async_will_remove_from_hass(self) -> None:
        if self._audit_unsub:
            self._audit_unsub()
            self._audit_unsub = None
        await super().async_will_remove_from_hass()

    @property
    def available(self) -> bool:
        return bool(self._audit and self._audit.get("status") == "ok")

    @property
    def native_value(self):
        if not self.available:
            return None
        return round(float(self._audit["cumulative_difference_pln"]), 2)

    @property
    def extra_state_attributes(self):
        if not isinstance(self._audit, dict):
            return {
                "informacyjny": True,
                "status": "oczekiwanie na obliczenie",
            }
        if self._audit.get("status") != "ok":
            return {
                "informacyjny": True,
                "status": "niedostępny",
                "blad": self._audit.get("error"),
            }

        history = [
            {
                "czas": item["end"],
                "saldo_pln": item["cumulative_pln"],
                "roznica_pln": item["difference_pln"],
                "duon_kwh_m3": item["duon_kwh_m3"],
                "lokalny_model_kwh_m3": item["local_model_kwh_m3"],
                "referencja_kwh_m3": item["reference_kwh_m3"],
                "lokalny_wskaznik_proc": item["local_yield_percent"],
                "roznica_proc": item["factor_difference_percent"],
                "zuzycie_faktura_m3": item["billed_consumption_m3"],
                "zuzycie_lokalne_m3": item["physical_consumption_m3"],
                "luki_godziny": item["reconstructed_gap_hours"],
            }
            for item in self._audit.get("history", [])
        ]
        return {
            "informacyjny": True,
            "uzywany_do_rozliczen": False,
            "interpretacja": (
                "Wartość dodatnia oznacza korzyść użytkownika: współczynnik DUON "
                "dał niższy koszt zmienny niż wynika z historycznej relacji lokalnego "
                "profilu Ariston do gazomierza; wartość ujemna oznacza koszt wyższy. "
                "Audyt wykrywa dryf względem własnej historii. Nie mierzy bezwzględnego "
                "ciepła spalania i nie jest dowodem nieprawidłowego rozliczenia."
            ),
            "metoda": self._audit.get("method"),
            "liczba_okresow": self._audit.get("sample_count"),
            "liczba_okresow_referencji": self._audit.get("reference_sample_count"),
            "referencja_kwh_m3": self._audit.get("reference_factor_kwh_m3"),
            "referencja_min_kwh_m3": self._audit.get(
                "reference_candidate_min_kwh_m3"
            ),
            "referencja_max_kwh_m3": self._audit.get(
                "reference_candidate_max_kwh_m3"
            ),
            "kalibracja_co_m3_kwh": self._audit.get(
                "calibration_co_m3_per_kwh"
            ),
            "kalibracja_cwu_m3_kwh": self._audit.get(
                "calibration_dhw_m3_per_kwh"
            ),
            "kalibracja_mae_m3": self._audit.get("calibration_mae_m3"),
            "ostatni_odczyt_od": self._audit.get("last_start"),
            "ostatni_odczyt_do": self._audit.get("last_end"),
            "ostatni_duon_kwh_m3": self._audit.get("last_duon_kwh_m3"),
            "ostatni_lokalny_model_kwh_m3": self._audit.get(
                "last_local_model_kwh_m3"
            ),
            "ostatni_lokalny_wskaznik_proc": self._audit.get(
                "last_local_yield_percent"
            ),
            "ostatnia_roznica_proc": self._audit.get(
                "last_factor_difference_percent"
            ),
            "ostatnia_roznica_pln": self._audit.get("last_difference_pln"),
            "saldo_pln": self._audit.get("cumulative_difference_pln"),
            "pominiete_bez_dwoch_dokladnych_odczytow": self._audit.get(
                "skipped_without_exact_manual_bounds_count"
            ),
            "pominiete_nieprawidlowe": self._audit.get("skipped_invalid_count"),
            "historia": history,
        }


class DuonCalibrationSensor(DuonBaseSensor):
    """Zbiorcza diagnostyka zgodna z pierwotną encją jednego współczynnika."""

    _attr_name = "Korekta źródła"
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
    _attr_name = "Kalibracja CO"
    _attr_unique_id = "duon_gaz_ariston_heating_m3_per_kwh"
    _attr_icon = "mdi:radiator"
    _attr_native_unit_of_measurement = "m³/kWh"

    @property
    def native_value(self):
        value = self.runtime.co_m3_per_kwh
        return None if value is None else round(value, 6)


class DuonDhwCalibrationSensor(DuonBaseSensor):
    _attr_name = "Kalibracja CWU"
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
        return _status_pl(self.runtime)

    @property
    def extra_state_attributes(self):
        publication = self.runtime.data.get("canonical_publication")
        publication_verified = bool(
            isinstance(publication, dict) and publication.get("verified", False)
        )
        attributes = {
            "blad_migawki": self.runtime.snapshot_error,
            "liczba_odczytow": len(self.runtime._readings()),
            "liczba_korekt": len(self.runtime.data.get("corrections", [])),
            "liczba_faktur": len(self.runtime.data.get("billing_periods", [])),
        }
        preview = self.runtime.data.get("canonical_preview")
        if isinstance(preview, dict):
            attributes.update(
                {
                    "historia_kanoniczna_wygenerowana": preview.get("generated_at"),
                    "historia_opublikowana_do_recorder": publication_verified,
                    "historia_punkty_zrodlowe": preview.get("source_point_count"),
                    "historia_kotwice_wszystkie": preview.get("anchor_count_total"),
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
                    "historia_suma_kanoniczna_m3": preview.get("canonical_total_m3"),
                    "historia_blad_domkniecia_m3": preview.get("closure_error_m3"),
                    "historia_luki_godziny": preview.get("reconstructed_gap_hours"),
                    "historia_rollback_kwh": preview.get("rollback_correction_kwh"),
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
                    "historia_ogon_godziny": preview.get("provisional_hour_count"),
                    "historia_ogon_od": preview.get("provisional_start"),
                    "historia_ogon_do": preview.get("provisional_end"),
                    "historia_ogon_m3": preview.get("provisional_m3"),
                    "historia_ogon_stan_gazomierza_m3": preview.get(
                        "provisional_meter_m3"
                    ),
                    "historia_ogon_luki_godziny": preview.get(
                        "provisional_reconstructed_gap_hours"
                    ),
                    "historia_ogon_rollback_kwh": preview.get(
                        "provisional_rollback_correction_kwh"
                    ),
                    "historia_ogon_rollback_przeniesiony_kwh": preview.get(
                        "provisional_rollback_retracted_kwh"
                    ),
                    "historia_ogon_rollback_nierozliczony_kwh": preview.get(
                        "provisional_unresolved_rollback_kwh"
                    ),
                    "historia_polaczona_godziny": preview.get("combined_hour_count"),
                    "historia_polaczona_nakladajace_godziny": preview.get(
                        "combined_overlap_hour_count"
                    ),
                    "historia_polaczona_od": preview.get("combined_start"),
                    "historia_polaczona_do": preview.get("combined_end"),
                    "historia_polaczona_stan_gazomierza_m3": preview.get(
                        "combined_meter_m3"
                    ),
                }
            )
        if isinstance(publication, dict):
            attributes.update(
                {
                    "historia_statystyka_id": publication.get("statistic_id"),
                    "historia_publikacja_status": _publication_status_pl(
                        publication.get("status")
                    ),
                    "historia_publikacja_tryb": _publication_mode_pl(
                        publication.get("mode")
                    ),
                    "historia_publikacja_powod": _publication_reason_pl(
                        publication.get("refresh_reason")
                    ),
                    "historia_publikacja_zlecona": publication.get("requested_at"),
                    "historia_publikacja_zweryfikowana": publication.get(
                        "verified", False
                    ),
                    "historia_publikacja_wiersze": publication.get("row_count"),
                    "historia_publikacja_wiersze_zapisane": publication.get(
                        "write_row_count"
                    ),
                    "historia_publikacja_od": publication.get("start"),
                    "historia_publikacja_do": publication.get("end"),
                    "historia_publikacja_suma_pierwsza_m3": publication.get(
                        "first_sum_m3"
                    ),
                    "historia_publikacja_suma_ostatnia_m3": publication.get(
                        "last_sum_m3"
                    ),
                    "historia_rozliczona_do": publication.get("settled_through"),
                }
            )
        return attributes