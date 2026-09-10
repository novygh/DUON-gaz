"""Czyste reguły przygotowania SMS z odczytem DUON."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from urllib.parse import quote

DUON_SMS_RECIPIENT = "661000860"
SMS_RETRY_WINDOW_SECONDS = 300


def submitted_meter_value(value: float) -> int:
    """Zaokrąglij stan gazomierza do pełnych m³ metodą half-up."""
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def normalize_meter_number(value: str) -> str:
    """Zwróć numer licznika jako niepusty ciąg cyfr, zachowując zera wiodące."""
    meter_number = str(value).strip()
    if not meter_number:
        raise ValueError("Podaj numer licznika DUON.")
    if not meter_number.isdigit():
        raise ValueError("Numer licznika DUON powinien zawierać wyłącznie cyfry.")
    return meter_number


def build_sms_body(meter_m3: float, meter_number: str) -> str:
    """Zbuduj treść SMS w formacie: <stan> <numer licznika>."""
    return f"{submitted_meter_value(meter_m3)} {normalize_meter_number(meter_number)}"


def build_sms_intent_data(
    body: str,
    *,
    recipient: str = DUON_SMS_RECIPIENT,
) -> dict[str, str]:
    """Zbuduj dane command_activity otwierające systemowy edytor SMS na Androidzie."""
    return {
        "intent_action": "android.intent.action.SENDTO",
        "intent_uri": f"smsto:{recipient}",
        "intent_extras": f"sms_body:{quote(body, safe='')}:String.urlencoded",
    }


def matching_android_registrations(
    registrations: Sequence[Mapping[str, Any]],
    user_id: str,
) -> list[Mapping[str, Any]]:
    """Znajdź rejestracje Android Mobile App przypisane dokładnie do użytkownika."""
    result: list[Mapping[str, Any]] = []
    for registration in registrations:
        if str(registration.get("user_id") or "") != user_id:
            continue
        if str(registration.get("os_name") or "").strip().lower() != "android":
            continue
        if not str(registration.get("webhook_id") or "").strip():
            continue
        result.append(registration)
    return result


def _parse_aware_iso(value: Any) -> datetime | None:
    """Parsuj ISO timestamp wyłącznie, gdy zawiera informację o strefie czasowej."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def recent_same_reading_for_sms_retry(
    meter_m3: float,
    now: datetime,
    last_manual_reading: Mapping[str, Any] | None,
    *,
    retry_window_seconds: int = SMS_RETRY_WINDOW_SECONDS,
) -> bool:
    """Rozpoznaj wyłącznie krótkie ponowienie SMS dla tej samej kotwicy.

    Taka sama wartość licznika może być prawidłową nową kotwicą później, nawet
    bez ponownej edycji pola Number. Dlatego deduplikujemy tylko identyczny
    odczyt zapisany bardzo niedawno — typowy przypadek ponownego kliknięcia po
    ekranie uprawnienia Android. Po upływie okna kolejne kliknięcie zawsze może
    utworzyć nową fizyczną kotwicę, również z niezmienionym stanem licznika.
    """
    if not last_manual_reading:
        return False
    if now.tzinfo is None or now.utcoffset() is None:
        return False

    last_value = last_manual_reading.get(
        "meter_m3_exact", last_manual_reading.get("meter_m3")
    )
    try:
        same_value = abs(float(last_value) - float(meter_m3)) <= 1e-9
    except (TypeError, ValueError):
        return False
    if not same_value:
        return False

    saved_at = _parse_aware_iso(last_manual_reading.get("timestamp"))
    if saved_at is None:
        return False

    age_seconds = (now - saved_at).total_seconds()
    return 0.0 <= age_seconds <= max(0, int(retry_window_seconds))


def pending_meter_sms_clear_deadline(
    pending_meter_m3: float | None,
    pending_entered_at: Any,
    last_manual_reading: Mapping[str, Any] | None,
    *,
    retry_window_seconds: int = SMS_RETRY_WINDOW_SECONDS,
) -> datetime | None:
    """Odtwórz termin czyszczenia starego pola po SMS zapisanym przez 0.5.0.

    0.5.0 pozostawiał wartość w polu po przygotowaniu SMS i nie zapisywał
    ``pending_clear_at``. Po aktualizacji wolno automatycznie wyczyścić tylko
    wartość, którą można jednoznacznie powiązać z ostatnią kotwicą mającą
    ``composer_requested``. Jeżeli pole zostało ponownie edytowane po tym SMS,
    jest traktowane jako świeży odczyt i nie jest czyszczone.
    """
    if pending_meter_m3 is None or not last_manual_reading:
        return None

    last_value = last_manual_reading.get(
        "meter_m3_exact", last_manual_reading.get("meter_m3")
    )
    try:
        if abs(float(last_value) - float(pending_meter_m3)) > 1e-9:
            return None
    except (TypeError, ValueError):
        return None

    sms = last_manual_reading.get("sms")
    if not isinstance(sms, Mapping) or sms.get("status") != "composer_requested":
        return None

    requested_at = _parse_aware_iso(sms.get("requested_at"))
    if requested_at is None:
        requested_at = _parse_aware_iso(sms.get("prepared_at"))
    if requested_at is None:
        return None

    entered_at = _parse_aware_iso(pending_entered_at)
    if entered_at is not None and entered_at > requested_at:
        return None

    return requested_at + timedelta(seconds=max(0, int(retry_window_seconds)))
