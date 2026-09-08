from __future__ import annotations

from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

MODULE = (
    Path(__file__).parents[1]
    / "custom_components"
    / "duon_gaz"
    / "conversion_audit.py"
)
spec = importlib.util.spec_from_file_location("conversion_audit", MODULE)
conversion_audit = importlib.util.module_from_spec(spec)
sys.modules["conversion_audit"] = conversion_audit
assert spec.loader is not None
spec.loader.exec_module(conversion_audit)

ConversionAuditError = conversion_audit.ConversionAuditError
build_conversion_audit = conversion_audit.build_conversion_audit


TZ = ZoneInfo("Europe/Warsaw")


def _hours(start: datetime, period_days: int, mixes: list[tuple[float, float]]):
    rows = []
    current = start
    for co_total, dhw_total in mixes:
        count = period_days * 24
        for _ in range(count):
            rows.append(
                SimpleNamespace(
                    start=current,
                    heating_kwh=co_total / count,
                    dhw_kwh=dhw_total / count,
                    quality=(),
                )
            )
            current += timedelta(hours=1)
    return rows


def _periods(
    start: datetime,
    period_days: int,
    mixes: list[tuple[float, float]],
    *,
    co_multiplier: float,
    dhw_multiplier: float,
    last_extra_kwh: float = 0.0,
):
    result = []
    cursor = start.date()
    for index, (co_kwh, dhw_kwh) in enumerate(mixes):
        next_day = cursor + timedelta(days=period_days)
        billed = co_multiplier * co_kwh + dhw_multiplier * dhw_kwh
        if index == len(mixes) - 1:
            billed += last_extra_kwh
        consumption = 10.0 + index
        result.append(
            {
                "invoice_number": f"TEST-{index + 1}",
                "period_start": cursor.isoformat(),
                "period_end": (next_day - timedelta(days=1)).isoformat(),
                "previous_reading": {"date": cursor.isoformat()},
                "current_reading": {"date": next_day.isoformat()},
                "consumption_m3": consumption,
                "billed_energy_kwh": billed,
                "conversion_factor_kwh_m3": billed / consumption,
                "gas_rate_net_pln_kwh": 0.20,
                "distribution_variable_net_pln_kwh": 0.05,
                "vat_rate": 0.23,
            }
        )
        cursor = next_day
    return result


class ConversionAuditTests(unittest.TestCase):
    def test_model_kroczacy_nie_uczy_sie_na_ocenianej_fakturze(self):
        start = datetime(2026, 1, 1, 12, tzinfo=TZ)
        mixes = [
            (120.0, 20.0),
            (100.0, 35.0),
            (80.0, 50.0),
            (60.0, 65.0),
            (40.0, 80.0),
            (90.0, 30.0),
            (70.0, 55.0),
            (50.0, 75.0),
        ]
        hours = _hours(start, 10, mixes)
        periods = _periods(
            start,
            10,
            mixes,
            co_multiplier=1.10,
            dhw_multiplier=1.25,
            last_extra_kwh=20.0,
        )

        result = build_conversion_audit(hours, periods, timezone=TZ).data

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["method"],
            "walk_forward_robust_two_component_local_energy_model",
        )
        self.assertEqual(result["sample_count"], 8)
        self.assertEqual(result["baseline_sample_count"], 6)
        self.assertEqual(result["evaluated_count"], 2)
        self.assertGreater(result["last_difference_pln"], 0.0)
        self.assertGreater(result["last_factor_difference_percent"], 0.0)
        self.assertAlmostEqual(
            result["model_co_billed_kwh_per_ariston_kwh"], 1.10, delta=0.08
        )
        self.assertAlmostEqual(
            result["model_dhw_billed_kwh_per_ariston_kwh"], 1.25, delta=0.08
        )
        self.assertEqual(len(result["history"]), 2)
        self.assertAlmostEqual(result["history"][0]["difference_pln"], 0.0, delta=1e-6)
        self.assertGreater(result["history"][1]["difference_pln"], 0.0)

    def test_za_malo_okresow_nie_udaje_wiarygodnego_modelu(self):
        start = datetime(2026, 1, 1, 12, tzinfo=TZ)
        mixes = [(100.0, 20.0)] * 5
        hours = _hours(start, 10, mixes)
        periods = _periods(
            start,
            10,
            mixes,
            co_multiplier=1.10,
            dhw_multiplier=1.25,
        )

        with self.assertRaises(ConversionAuditError):
            build_conversion_audit(hours, periods, timezone=TZ)


if __name__ == "__main__":
    unittest.main()
