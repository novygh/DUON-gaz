from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


RULES_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "duon_gaz"
    / "publication_rules.py"
)
SPEC = importlib.util.spec_from_file_location("duon_gaz_publication_rules_test", RULES_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Nie można załadować reguł publikacji z {RULES_PATH}")

MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

active_anchor_fingerprint = MODULE.active_anchor_fingerprint
active_anchor_set_changed = MODULE.active_anchor_set_changed


class TestRegulyPublikacji(unittest.TestCase):
    def test_fingerprint_nie_zalezy_od_kolejnosci_kotwic(self) -> None:
        first = {
            "timestamp": "2026-01-01T10:00:00+00:00",
            "meter_m3": 100.0,
            "source": "manual",
            "timestamp_precision": "exact",
        }
        second = {
            "timestamp": "2026-02-01T10:00:00+00:00",
            "meter_m3": 120.0,
            "source": "invoice_billing",
            "timestamp_precision": "day",
        }

        self.assertEqual(
            active_anchor_fingerprint([first, second]),
            active_anchor_fingerprint([second, first]),
        )

    def test_dodanie_kotwicy_wewnatrz_historii_wymusza_pelny_rebuild(self) -> None:
        base = [
            {
                "timestamp": "2026-01-01T10:00:00+00:00",
                "meter_m3": 100.0,
                "source": "manual",
                "timestamp_precision": "exact",
            },
            {
                "timestamp": "2026-03-01T10:00:00+00:00",
                "meter_m3": 140.0,
                "source": "manual",
                "timestamp_precision": "exact",
            },
        ]
        published = active_anchor_fingerprint(base)
        with_internal_anchor = [
            base[0],
            {
                "timestamp": "2026-02-01T10:00:00+00:00",
                "meter_m3": 120.0,
                "source": "invoice_billing",
                "timestamp_precision": "day",
            },
            base[1],
        ]

        self.assertFalse(active_anchor_set_changed(published, base))
        self.assertTrue(
            active_anchor_set_changed(published, with_internal_anchor)
        )

    def test_stara_publikacja_bez_fingerprint_wymusza_jednorazowy_rebuild(self) -> None:
        readings = [
            {
                "timestamp": "2026-01-01T10:00:00+00:00",
                "meter_m3": 100.0,
                "source": "manual",
                "timestamp_precision": "exact",
            }
        ]

        self.assertTrue(active_anchor_set_changed(None, readings))
        self.assertTrue(active_anchor_set_changed("", readings))


if __name__ == "__main__":
    unittest.main()
