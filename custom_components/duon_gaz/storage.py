"""Trwały magazyn danych integracji DUON Gaz."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from homeassistant.helpers.storage import Store

from .const import (
    DEFAULT_CO_M3_PER_KWH,
    DEFAULT_DHW_M3_PER_KWH,
    STORAGE_KEY,
    STORAGE_VERSION,
)


def default_store_data() -> dict[str, Any]:
    """Zwróć pusty model danych v2."""
    return {
        "schema_version": 2,
        "pending_meter_m3": None,
        "pending_entered_by_user_id": None,
        "pending_entered_at": None,
        # Dokładne/ręczne kotwice. Import historyczny również trafia tutaj.
        "manual_readings": [],
        # Zaufane kotwice z faktur są przechowywane oddzielnie, aby zachować
        # informację o ich niższej precyzji czasu i wskazania gazomierza.
        "invoice_readings": [],
        "calibration": {
            "co_m3_per_kwh": DEFAULT_CO_M3_PER_KWH,
            "dhw_m3_per_kwh": DEFAULT_DHW_M3_PER_KWH,
            "effective_m3_per_kwh": None,
            "method": "neutral_bootstrap",
            "sample_count": 0,
            "mae_m3": None,
            "updated_at": None,
        },
        "billing_periods": [],
        "corrections": [],
        "processed_invoices": [],
        "processed_messages": [],
        "last_outlook_sync": None,
        "totals": {
            "provisional_energy_kwh": 0.0,
            "provisional_variable_cost_gross": 0.0,
        },
        "legacy": {},
    }


def _migrate_v1(data: dict[str, Any]) -> dict[str, Any]:
    """Migruj pierwotny model v0.1 bez ufania surowym stanom Ariston."""
    migrated = default_store_data()
    migrated["pending_meter_m3"] = data.get("pending_meter_m3")

    for item in data.get("readings", []):
        migrated["manual_readings"].append(
            {
                "timestamp": item.get("timestamp"),
                "meter_m3": item.get("meter_m3"),
                "source": item.get("source", "manual"),
                "recorder": None,
                "quality": {
                    "state": "legacy_raw_state",
                    "exclude_from_calibration": True,
                },
                "legacy": {
                    "ariston_heat_kwh_raw_state": item.get("ariston_heat_kwh"),
                    "ariston_dhw_kwh_raw_state": item.get("ariston_dhw_kwh"),
                },
            }
        )

    migrated["totals"]["provisional_variable_cost_gross"] = float(
        data.get("cost_offset_gross", 0.0) or 0.0
    )
    migrated["legacy"] = {
        "calibration_factor": data.get("calibration_factor"),
        "source_schema": 1,
    }
    return migrated


class DuonGazStore(Store[dict[str, Any]]):
    """Wersjonowany magazyn danych DUON Gaz."""

    def __init__(self, hass) -> None:
        super().__init__(hass, STORAGE_VERSION, STORAGE_KEY)

    async def _async_migrate_func(
        self,
        old_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        del old_minor_version
        if old_version == 1:
            return _migrate_v1(deepcopy(old_data))
        raise NotImplementedError
