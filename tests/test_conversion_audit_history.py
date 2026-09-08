from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import unittest

MODULE = (
    Path(__file__).parents[1]
    / "custom_components"
    / "duon_gaz"
    / "conversion_audit_history.py"
)
spec = importlib.util.spec_from_file_location("conversion_audit_history", MODULE)
conversion_audit_history = importlib.util.module_from_spec(spec)
sys.modules["conversion_audit_history"] = conversion_audit_history
assert spec.loader is not None
spec.loader.exec_module(conversion_audit_history)

build_audit_statistics_rows = conversion_audit_history.build_audit_statistics_rows


class ConversionAuditHistoryTests(unittest.TestCase):
    def test_prawdziwe_saldo_jest_backfillowane_od_zera(self):
        history = [
            {
                "start": "2024-01-15T12:37:00+01:00",
                "end": "2024-02-15T13:41:00+01:00",
                "cumulative_pln": 4.25,
            },
            {
                "start": "2024-02-15T13:41:00+01:00",
                "end": "2024-03-15T09:12:00+01:00",
                "cumulative_pln": -1.75,
            },
            {
                "start": "2024-03-15T09:12:00+01:00",
                "end": "2024-04-15T18:58:00+02:00",
                "cumulative_pln": 8.35,
            },
        ]

        rows = build_audit_statistics_rows(history)

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["state"], 0.0)
        self.assertEqual(rows[0]["sum"], 0.0)
        self.assertEqual(rows[1]["state"], 4.25)
        self.assertEqual(rows[2]["state"], -1.75)
        self.assertEqual(rows[3]["state"], 8.35)
        self.assertEqual(rows[3]["sum"], 8.35)
        self.assertEqual(rows[0]["start"].tzinfo, timezone.utc)
        self.assertEqual(rows[0]["start"].minute, 0)
        self.assertEqual(rows[3]["start"].minute, 0)

    def test_nie_wstawia_wartosci_spoza_historii_audytu(self):
        history = [
            {
                "start": "2026-07-01T12:00:00+02:00",
                "end": "2026-08-01T12:00:00+02:00",
                "cumulative_pln": 8.35,
            },
            {
                "start": "zly-czas",
                "end": "2026-09-01T12:00:00+02:00",
                "cumulative_pln": 41.55,
            },
        ]

        rows = build_audit_statistics_rows(history)

        self.assertEqual([row["state"] for row in rows], [0.0, 8.35])

    def test_kolizja_w_tejsamej_godzinie_zostawia_ostatni_punkt(self):
        history = [
            {
                "start": "2026-01-01T10:00:00+00:00",
                "end": "2026-02-01T10:10:00+00:00",
                "cumulative_pln": 1.0,
            },
            {
                "start": "2026-02-01T10:10:00+00:00",
                "end": "2026-02-01T10:50:00+00:00",
                "cumulative_pln": 2.0,
            },
        ]

        rows = build_audit_statistics_rows(history)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["state"], 2.0)


if __name__ == "__main__":
    unittest.main()
