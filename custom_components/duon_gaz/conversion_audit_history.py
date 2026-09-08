"""Publikacja historycznego salda audytu konwersji do Recorder."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Iterable


def _aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _hour_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def build_audit_statistics_rows(
    history: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Zbuduj godzinowe punkty LTS z rzeczywistej historii salda audytu.

    Pierwszy punkt jest jawnym zerem na początku pierwszego ocenianego okresu.
    Dzięki temu dla sensora ``state_class: total`` zarówno ``state`` jak i ``sum``
    reprezentują to samo podpisane saldo PLN.
    """
    usable: list[tuple[datetime, datetime, float]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        start = _aware_datetime(item.get("start"))
        end = _aware_datetime(item.get("end"))
        try:
            balance = float(item.get("cumulative_pln"))
        except (TypeError, ValueError):
            continue
        if start is None or end is None or end <= start or not math.isfinite(balance):
            continue
        usable.append((start, end, balance))

    if not usable:
        return []

    usable.sort(key=lambda item: item[1])
    points: dict[datetime, float] = {_hour_utc(usable[0][0]): 0.0}
    for _start, end, balance in usable:
        points[_hour_utc(end)] = balance

    return [
        {
            "start": start,
            "state": round(balance, 6),
            "sum": round(balance, 6),
        }
        for start, balance in sorted(points.items())
    ]


def publish_audit_statistics(
    hass: Any,
    entity_id: str,
    audit: dict[str, Any],
) -> int:
    """Zaplanuj import historycznego salda pod statistic_id encji audytu."""
    history = audit.get("history")
    if not isinstance(history, list):
        return 0

    rows = build_audit_statistics_rows(history)
    if not rows:
        return 0

    # Importy Home Assistanta są celowo lokalne, aby moduł z budową punktów
    # pozostawał testowalny bez uruchamiania całego Core.
    from homeassistant.components.recorder.models import (  # noqa: PLC0415
        StatisticMeanType,
        StatisticMetaData,
    )
    from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
        async_import_statistics,
    )

    metadata: StatisticMetaData = {
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": None,
        "source": "recorder",
        "statistic_id": entity_id,
        "unit_class": None,
        "unit_of_measurement": "PLN",
    }
    async_import_statistics(hass, metadata, rows)
    return len(rows)
