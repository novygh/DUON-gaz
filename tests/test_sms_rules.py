from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import unittest


RULES_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "duon_gaz"
    / "sms_rules.py"
)
SPEC = importlib.util.spec_from_file_location("duon_gaz_sms_rules_test", RULES_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Nie można załadować reguł SMS z {RULES_PATH}")

MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

build_sms_body = MODULE.build_sms_body
build_sms_intent_data = MODULE.build_sms_intent_data
matching_android_registrations = MODULE.matching_android_registrations
normalize_meter_number = MODULE.normalize_meter_number
pending_meter_sms_clear_deadline = MODULE.pending_meter_sms_clear_deadline
recent_same_reading_for_sms_retry = MODULE.recent_same_reading_for_sms_retry
submitted_meter_value = MODULE.submitted_meter_value


class TestSmsRules(unittest.TestCase):
    def test_stan_sms_jest_zaokraglany_half_up(self) -> None:
        self.assertEqual(submitted_meter_value(1803.49), 1803)
        self.assertEqual(submitted_meter_value(1803.50), 1804)

    def test_numer_licznika_zachowuje_zera_wiodace(self) -> None:
        self.assertEqual(normalize_meter_number("001234"), "001234")

    def test_numer_licznika_musi_byc_cyfrowy(self) -> None:
        with self.assertRaises(ValueError):
            normalize_meter_number("12A34")

    def test_tresc_sms_ma_format_stan_spacja_numer(self) -> None:
        self.assertEqual(build_sms_body(1803.214, "123456"), "1803 123456")

    def test_intent_otwiera_systemowy_edytor_sms_z_trescia(self) -> None:
        data = build_sms_intent_data("1803 123456", recipient="661000860")
        self.assertEqual(data["intent_action"], "android.intent.action.SENDTO")
        self.assertEqual(data["intent_uri"], "smsto:661000860")
        self.assertEqual(
            data["intent_extras"],
            "sms_body:1803%20123456:String.urlencoded",
        )

    def test_wybierany_jest_tylko_android_biezacego_uzytkownika(self) -> None:
        registrations = [
            {"user_id": "u1", "os_name": "Android", "webhook_id": "phone-u1"},
            {"user_id": "u1", "os_name": "iOS", "webhook_id": "iphone-u1"},
            {"user_id": "u2", "os_name": "Android", "webhook_id": "phone-u2"},
            {"user_id": "u1", "os_name": "Android", "webhook_id": ""},
        ]
        self.assertEqual(
            matching_android_registrations(registrations, "u1"),
            [registrations[0]],
        )

    def test_ten_sam_odczyt_w_ciagu_5_minut_jest_retry_sms(self) -> None:
        last = {
            "timestamp": "2026-09-09T09:28:10+00:00",
            "meter_m3": 1807.25,
            "meter_m3_exact": 1807.25,
        }
        now = datetime(2026, 9, 9, 9, 28, 28, tzinfo=timezone.utc)
        self.assertTrue(recent_same_reading_for_sms_retry(1807.25, now, last))

    def test_ten_sam_odczyt_po_5_minutach_jest_nowa_kotwica(self) -> None:
        last = {
            "timestamp": "2026-09-09T09:28:10+00:00",
            "meter_m3": 1807.25,
            "meter_m3_exact": 1807.25,
        }
        now = datetime(2026, 9, 9, 9, 34, 0, tzinfo=timezone.utc)
        self.assertFalse(recent_same_reading_for_sms_retry(1807.25, now, last))

    def test_ten_sam_odczyt_nastepnego_dnia_jest_nowa_kotwica(self) -> None:
        last = {
            "timestamp": "2026-09-09T09:28:10+00:00",
            "meter_m3": 1807.25,
            "meter_m3_exact": 1807.25,
        }
        now = datetime(2026, 9, 10, 9, 28, 10, tzinfo=timezone.utc)
        self.assertFalse(recent_same_reading_for_sms_retry(1807.25, now, last))

    def test_inna_wartosc_zawsze_jest_nowa_kotwica(self) -> None:
        last = {
            "timestamp": "2026-09-09T09:28:10+00:00",
            "meter_m3": 1807.25,
        }
        now = datetime(2026, 9, 9, 9, 28, 28, tzinfo=timezone.utc)
        self.assertFalse(recent_same_reading_for_sms_retry(1807.26, now, last))

    def test_stary_pending_z_050_dostaje_deadline_z_requested_at(self) -> None:
        last = {
            "meter_m3": 1807.25,
            "meter_m3_exact": 1807.25,
            "sms": {
                "status": "composer_requested",
                "requested_at": "2026-09-09T10:38:13+00:00",
            },
        }
        deadline = pending_meter_sms_clear_deadline(
            1807.25,
            "2026-09-09T09:27:50+00:00",
            last,
        )
        self.assertEqual(
            deadline,
            datetime(2026, 9, 9, 10, 43, 13, tzinfo=timezone.utc),
        )

    def test_stary_pending_nie_jest_czyszczony_gdy_wartosc_nie_pasuje(self) -> None:
        last = {
            "meter_m3": 1807.25,
            "sms": {
                "status": "composer_requested",
                "requested_at": "2026-09-09T10:38:13+00:00",
            },
        }
        self.assertIsNone(
            pending_meter_sms_clear_deadline(
                1808.0,
                "2026-09-09T10:40:00+00:00",
                last,
            )
        )

    def test_stary_pending_nie_jest_czyszczony_po_ponownej_edycji(self) -> None:
        last = {
            "meter_m3": 1807.25,
            "sms": {
                "status": "composer_requested",
                "requested_at": "2026-09-09T10:38:13+00:00",
            },
        }
        self.assertIsNone(
            pending_meter_sms_clear_deadline(
                1807.25,
                "2026-09-09T10:40:00+00:00",
                last,
            )
        )

    def test_stary_pending_bez_poprawnego_sms_nie_jest_czyszczony(self) -> None:
        last = {
            "meter_m3": 1807.25,
            "sms": {
                "status": "error",
                "requested_at": "2026-09-09T10:38:13+00:00",
            },
        }
        self.assertIsNone(
            pending_meter_sms_clear_deadline(
                1807.25,
                "2026-09-09T09:27:50+00:00",
                last,
            )
        )


if __name__ == "__main__":
    unittest.main()
