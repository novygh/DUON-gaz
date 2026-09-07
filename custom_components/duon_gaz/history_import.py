"""Validated one-time historical seed import for DUON Gaz."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from homeassistant.util import dt as dt_util

from .runtime import DuonGazRuntime


def _as_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as err:
        raise ValueError(f"Nieprawidłowa wartość {field}: {value!r}") from err


def _validate_readings(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list) or not items:
        raise ValueError("Plik importu nie zawiera manual_readings.")

    result: list[dict[str, Any]] = []
    previous_time = None
    previous_meter = None

    for index, raw in enumerate(items, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Odczyt #{index} nie jest obiektem.")

        timestamp_raw = raw.get("timestamp")
        timestamp = dt_util.parse_datetime(timestamp_raw) if isinstance(timestamp_raw, str) else None
        if timestamp is None or timestamp.tzinfo is None:
            raise ValueError(f"Odczyt #{index} ma nieprawidłowy timestamp.")

        meter = _as_float(raw.get("meter_m3"), f"manual_readings[{index}].meter_m3")
        if previous_time is not None and timestamp <= previous_time:
            raise ValueError("Odczyty gazomierza nie są ściśle rosnące w czasie.")
        if previous_meter is not None and meter < previous_meter:
            raise ValueError("Stan gazomierza maleje w historii importu.")

        item = deepcopy(raw)
        item["timestamp"] = timestamp.isoformat()
        item["meter_m3"] = meter
        item.setdefault("source", "manual_history")
        item.setdefault(
            "quality",
            {"state": "historical", "exclude_from_calibration": False},
        )
        result.append(item)
        previous_time = timestamp
        previous_meter = meter

    return result


async def async_import_history_file(
    runtime: DuonGazRuntime,
    relative_path: str,
    *,
    replace: bool = False,
) -> int:
    """Import a user-specific history seed through the integration's Store API."""
    config_root = Path(runtime.hass.config.config_dir).resolve()
    path = (config_root / relative_path).resolve()
    if path != config_root and config_root not in path.parents:
        raise ValueError("Plik importu musi znajdować się w katalogu /config.")
    if not path.is_file():
        raise ValueError(f"Nie znaleziono pliku importu: {relative_path}")

    def _read() -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError("Główny element pliku importu musi być obiektem JSON.")
        return data

    payload = await runtime.hass.async_add_executor_job(_read)
    if int(payload.get("schema_version", 0)) != 1:
        raise ValueError("Nieobsługiwana wersja pliku historii.")

    if runtime._readings() and not replace:
        raise ValueError(
            "DUON Gaz ma już zapisane odczyty. Import wymaga replace=true."
        )

    readings = _validate_readings(payload.get("manual_readings"))
    runtime.data["manual_readings"] = readings
    runtime.data["pending_meter_m3"] = readings[-1]["meter_m3"]

    corrections = payload.get("corrections")
    if isinstance(corrections, list):
        runtime.data["corrections"] = deepcopy(corrections)

    runtime.data["history_import"] = {
        "schema_version": 1,
        "source_file": path.name,
        "reading_count": len(readings),
        "imported_at": dt_util.utcnow().isoformat(),
    }

    runtime._recalculate_calibration()
    await runtime.async_save()
    await runtime.async_refresh_source_snapshot(notify=False)
    runtime.async_notify()
    return len(readings)
