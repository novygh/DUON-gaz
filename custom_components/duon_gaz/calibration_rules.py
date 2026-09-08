"""Czyste reguły kalibracji dla kotwic DUON Gaz."""
from __future__ import annotations

from typing import Any

DEFAULT_INVOICE_EXCLUDE_FROM_CALIBRATION = True


def reading_excluded_from_calibration(reading: dict[str, Any]) -> bool:
    """Sprawdź, czy kotwica jest wykluczona z uczenia kalibracji CO/CWU."""
    quality = reading.get("quality")
    return bool(
        isinstance(quality, dict) and quality.get("exclude_from_calibration", False)
    )


def calibration_readings(
    readings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Odfiltruj kotwice wykluczone zanim powstaną przedziały kalibracyjne."""
    return [
        reading
        for reading in readings
        if not reading_excluded_from_calibration(reading)
    ]


def migrate_invoice_calibration_flags(data: dict[str, Any]) -> bool:
    """Wyklucz istniejące kotwice fakturowe z kalibracji.

    Zwraca True wyłącznie wtedy, gdy magazyn wymagał zmiany.
    Flaga exclude_from_estimation pozostaje nietknięta, ponieważ zaufana
    kotwica fakturowa nadal może uczestniczyć w historii fizycznej.
    """
    readings = data.get("invoice_readings")
    if not isinstance(readings, list):
        return False

    changed = False
    for reading in readings:
        if not isinstance(reading, dict):
            continue
        quality = reading.get("quality")
        if not isinstance(quality, dict):
            quality = {}
            reading["quality"] = quality
        if quality.get("exclude_from_calibration") is not True:
            quality["exclude_from_calibration"] = True
            changed = True

    return changed
