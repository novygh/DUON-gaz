from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


RULES_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "duon_gaz"
    / "calibration_rules.py"
)
SPEC = importlib.util.spec_from_file_location("duon_gaz_calibration_rules_test", RULES_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Nie można załadować reguł kalibracji z {RULES_PATH}")

MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

DEFAULT_INVOICE_EXCLUDE_FROM_CALIBRATION = MODULE.DEFAULT_INVOICE_EXCLUDE_FROM_CALIBRATION
calibration_readings = MODULE.calibration_readings
migrate_invoice_calibration_flags = MODULE.migrate_invoice_calibration_flags


class TestRegulyKalibracjiRuntime(unittest.TestCase):
    def test_kotwica_fakturowa_jest_domyslnie_wykluczona_z_kalibracji(self) -> None:
        self.assertIs(DEFAULT_INVOICE_EXCLUDE_FROM_CALIBRATION, True)

    def test_migracja_wyklucza_stare_kotwice_bez_usuwania_ich_z_estymacji(self) -> None:
        data = {
            "invoice_readings": [
                {
                    "quality": {
                        "state": "invoice_billing",
                        "exclude_from_calibration": False,
                        "exclude_from_estimation": False,
                    }
                },
                {
                    "quality": {
                        "state": "shadowed_by_manual",
                        "exclude_from_calibration": True,
                        "exclude_from_estimation": True,
                    }
                },
                {"quality": {"state": "invoice_billing"}},
            ]
        }

        self.assertTrue(migrate_invoice_calibration_flags(data))
        self.assertTrue(
            all(
                reading["quality"]["exclude_from_calibration"] is True
                for reading in data["invoice_readings"]
            )
        )
        self.assertIs(
            data["invoice_readings"][0]["quality"]["exclude_from_estimation"],
            False,
        )
        self.assertFalse(migrate_invoice_calibration_flags(data))

    def test_wykluczona_kotwica_nie_rozcina_recznego_przedzialu(self) -> None:
        manual_before = {
            "timestamp": "2026-01-01T10:00:00+00:00",
            "source": "manual",
            "quality": {"exclude_from_calibration": False},
        }
        invoice = {
            "timestamp": "2026-01-15T12:00:00+00:00",
            "source": "invoice_billing",
            "quality": {"exclude_from_calibration": True},
        }
        manual_after = {
            "timestamp": "2026-02-01T10:00:00+00:00",
            "source": "manual",
            "quality": {"exclude_from_calibration": False},
        }

        filtered = calibration_readings([manual_before, invoice, manual_after])
        intervals = list(zip(filtered, filtered[1:]))

        self.assertEqual(filtered, [manual_before, manual_after])
        self.assertEqual(intervals, [(manual_before, manual_after)])


if __name__ == "__main__":
    unittest.main()
