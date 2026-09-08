"""Czyste reguły wykrywania zmian historii kanonicznej DUON Gaz."""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any


def active_anchor_fingerprint(readings: list[dict[str, Any]]) -> str:
    """Zwróć stabilny odcisk zestawu aktywnych kotwic fizycznych."""
    normalized: list[dict[str, str]] = []

    for reading in readings:
        if not isinstance(reading, dict):
            continue

        timestamp = reading.get("timestamp")
        meter = reading.get("meter_m3")
        if not timestamp or meter is None:
            continue

        try:
            meter_text = format(float(meter), ".9f").rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            continue

        normalized.append(
            {
                "timestamp": str(timestamp),
                "meter_m3": meter_text,
                "source": str(reading.get("source") or "unknown"),
                "timestamp_precision": str(
                    reading.get("timestamp_precision") or "exact"
                ),
            }
        )

    normalized.sort(
        key=lambda item: (
            item["timestamp"],
            item["meter_m3"],
            item["source"],
            item["timestamp_precision"],
        )
    )
    payload = json.dumps(
        normalized,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def active_anchor_set_changed(
    published_fingerprint: Any,
    readings: list[dict[str, Any]],
) -> bool:
    """Sprawdź, czy publikacja nie odpowiada bieżącemu zestawowi kotwic."""
    if not isinstance(published_fingerprint, str) or not published_fingerprint:
        return True
    return published_fingerprint != active_anchor_fingerprint(readings)
