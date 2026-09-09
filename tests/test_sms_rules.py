from __future__ import annotations

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
        self.assertEqual(build_sms_body(1803.214, "917474"), "1803 917474")

    def test_intent_otwiera_systemowy_edytor_sms_z_trescia(self) -> None:
        data = build_sms_intent_data("1803 917474", recipient="661000860")
        self.assertEqual(data["intent_action"], "android.intent.action.SENDTO")
        self.assertEqual(data["intent_uri"], "smsto:661000860")
        self.assertEqual(
            data["intent_extras"],
            "sms_body:1803%20917474:String.urlencoded",
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


if __name__ == "__main__":
    unittest.main()
