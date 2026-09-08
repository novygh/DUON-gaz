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


def _manual(timestamp: datetime, meter: float):
    return {
        "timestamp": timestamp.isoformat(),
        "meter_m3": meter,
        "timestamp_precision": "exact",
        "source": "manual",
        "quality": {
            "state": "good",
            "exclude_from_estimation": False,
        },
    }


def _period(
    number: str,
    start: datetime,
    end: datetime,
    previous_meter: float,
    current_meter: float,
    factor: float,
):
    consumption = current_meter - previous_meter
    return {
        "invoice_number": number,
        "previous_reading": {
            "date": start.date().isoformat(),
            "meter_m3": round(previous_meter),
        },
        "current_reading": {
            "date": end.date().isoformat(),
            "meter_m3": round(current_meter),
        },
        "consumption_m3": round(current_meter) - round(previous_meter),
        "conversion_factor_kwh_m3": factor,
        "billed_energy_kwh": consumption * factor,
        "gas_rate_net_pln_kwh": 0.20,
        "distribution_variable_net_pln_kwh": 0.05,
        "vat_rate": 0.23,
    }


def _interval(start: datetime, end: datetime, physical_m3: float, yield_ratio: float):
    return SimpleNamespace(
        start=start,
        end=end,
        physical_delta_m3=physical_m3,
        provisional_m3=physical_m3 * yield_ratio,
        reconstructed_gap_hours=0,
        unresolved_rollback_kwh=0.0,
        quality=("normalized_to_meter",),
    )


class ConversionAuditTests(unittest.TestCase):
    def test_dokladne_reczne_granice_usuwaja_sztuczne_poludnie(self):
        start = datetime(2026, 1, 3, 18, 17, tzinfo=TZ)
        manuals = []
        intervals = []
        periods = []
        meter = 1000.2
        reference = 11.40
        ratios = [1.00, 1.01, 0.99, 1.02, 0.98, 1.00, 1.01, 1.00]

        points = [start + timedelta(days=30 * index) for index in range(9)]
        meters = [meter]
        for _index in range(8):
            meter += 40.0
            meters.append(meter)

        for timestamp, value in zip(points, meters, strict=True):
            manuals.append(_manual(timestamp, value))

        for index, ratio in enumerate(ratios):
            intervals.append(
                _interval(points[index], points[index + 1], 40.0, ratio)
            )
            factor = reference * ratio
            if index == len(ratios) - 1:
                factor *= 1.05
            periods.append(
                _period(
                    f"TEST-{index + 1}",
                    points[index],
                    points[index + 1],
                    meters[index],
                    meters[index + 1],
                    factor,
                )
            )

        result = build_conversion_audit(
            intervals,
            periods,
            manuals,
            timezone=TZ,
            calibration_co_m3_per_kwh=0.10,
            calibration_dhw_m3_per_kwh=0.11,
            calibration_mae_m3=0.5,
        ).data

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sample_count"], 8)
        self.assertAlmostEqual(result["reference_factor_kwh_m3"], reference, delta=0.02)
        self.assertLess(result["last_difference_pln"], 0.0)
        self.assertLess(result["last_factor_difference_percent"], -4.5)
        self.assertGreater(result["last_factor_difference_percent"], -5.5)
        self.assertEqual(len(result["history"]), 8)
        self.assertEqual(result["skipped_without_exact_manual_bounds_count"], 0)

    def test_faktura_bez_dwoch_dokladnych_granic_jest_pominieta(self):
        start = datetime(2026, 1, 3, 18, 17, tzinfo=TZ)
        points = [start + timedelta(days=30 * index) for index in range(8)]
        manuals = [_manual(timestamp, 1000.2 + 40 * index) for index, timestamp in enumerate(points)]
        intervals = [
            _interval(points[index], points[index + 1], 40.0, 1.0)
            for index in range(7)
        ]
        periods = [
            _period(
                f"TEST-{index + 1}",
                points[index],
                points[index + 1],
                1000.2 + 40 * index,
                1000.2 + 40 * (index + 1),
                11.40,
            )
            for index in range(7)
        ]

        # Zerwij dokładne dopasowanie jednej granicy faktury do ręcznego gazomierza.
        periods[-1]["current_reading"]["meter_m3"] += 5

        result = build_conversion_audit(
            intervals,
            periods,
            manuals,
            timezone=TZ,
            calibration_co_m3_per_kwh=0.10,
            calibration_dhw_m3_per_kwh=0.11,
        ).data

        self.assertEqual(result["sample_count"], 6)
        self.assertEqual(result["skipped_without_exact_manual_bounds_count"], 1)

    def test_za_malo_dokladnych_okresow_nie_tworz_referencji(self):
        start = datetime(2026, 1, 3, 18, 17, tzinfo=TZ)
        points = [start + timedelta(days=30 * index) for index in range(6)]
        manuals = [_manual(timestamp, 1000.2 + 40 * index) for index, timestamp in enumerate(points)]
        intervals = [
            _interval(points[index], points[index + 1], 40.0, 1.0)
            for index in range(5)
        ]
        periods = [
            _period(
                f"TEST-{index + 1}",
                points[index],
                points[index + 1],
                1000.2 + 40 * index,
                1000.2 + 40 * (index + 1),
                11.40,
            )
            for index in range(5)
        ]

        with self.assertRaises(ConversionAuditError):
            build_conversion_audit(
                intervals,
                periods,
                manuals,
                timezone=TZ,
                calibration_co_m3_per_kwh=0.10,
                calibration_dhw_m3_per_kwh=0.11,
            )


if __name__ == "__main__":
    unittest.main()