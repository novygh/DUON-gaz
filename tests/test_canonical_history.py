from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
import sys
import unittest

MODULE = (
    Path(__file__).parents[1]
    / "custom_components"
    / "duon_gaz"
    / "canonical_history.py"
)
spec = importlib.util.spec_from_file_location("canonical_history", MODULE)
canonical_history = importlib.util.module_from_spec(spec)
sys.modules["canonical_history"] = canonical_history
assert spec.loader is not None
spec.loader.exec_module(canonical_history)

build_canonical_history = canonical_history.build_canonical_history
CanonicalHistorySettings = canonical_history.CanonicalHistorySettings
PhysicalAnchor = canonical_history.PhysicalAnchor
SourcePoint = canonical_history.SourcePoint


class CanonicalHistoryTests(unittest.TestCase):
    def test_normalizes_interval_exactly_to_meter_delta(self) -> None:
        points = [
            SourcePoint(datetime(2026, 1, 1, hour, tzinfo=UTC), float(hour), 0.0)
            for hour in range(4)
        ]
        anchors = [
            PhysicalAnchor(datetime(2026, 1, 1, 0, tzinfo=UTC), 100.0),
            PhysicalAnchor(datetime(2026, 1, 1, 3, tzinfo=UTC), 106.0),
        ]
        result = build_canonical_history(
            points,
            anchors,
            heating_m3_per_kwh=1.0,
            dhw_m3_per_kwh=1.0,
            timezone=UTC,
        )
        self.assertAlmostEqual(sum(row.gas_m3 for row in result.hours), 6.0)
        self.assertAlmostEqual(result.hours[-1].cumulative_m3, 106.0)
        self.assertAlmostEqual(result.intervals[0].scale_factor or 0.0, 2.0)

    def test_rollback_retracts_prior_overcount_instead_of_negative_usage(self) -> None:
        points = [
            SourcePoint(datetime(2026, 1, 1, 0, tzinfo=UTC), 0.0, 0.0),
            SourcePoint(datetime(2026, 1, 1, 1, tzinfo=UTC), 3.0, 0.0),
            SourcePoint(datetime(2026, 1, 1, 2, tzinfo=UTC), 0.0, 0.0),
            SourcePoint(datetime(2026, 1, 1, 3, tzinfo=UTC), 1.0, 0.0),
        ]
        anchors = [
            PhysicalAnchor(datetime(2026, 1, 1, 0, tzinfo=UTC), 10.0),
            PhysicalAnchor(datetime(2026, 1, 1, 3, tzinfo=UTC), 11.0),
        ]
        result = build_canonical_history(
            points,
            anchors,
            heating_m3_per_kwh=1.0,
            dhw_m3_per_kwh=1.0,
            timezone=UTC,
        )
        self.assertTrue(all(row.gas_m3 >= 0 for row in result.hours))
        self.assertAlmostEqual(result.intervals[0].rollback_correction_kwh, 3.0)
        self.assertAlmostEqual(result.intervals[0].rollback_retracted_kwh, 3.0)
        self.assertIn("rollback_event", result.intervals[0].quality)

    def test_missing_recorder_hours_are_reconstructed_from_local_profile(self) -> None:
        start = datetime(2026, 1, 1, 0, tzinfo=UTC)
        points: list[SourcePoint] = []
        cumulative = 0.0
        for hour in range(25):
            if hour in {10, 11, 12, 13}:
                continue
            if points:
                cumulative += 1.0
            points.append(SourcePoint(start + timedelta(hours=hour), cumulative, 0.0))

        anchors = [
            PhysicalAnchor(start, 100.0),
            PhysicalAnchor(start + timedelta(hours=24), 124.0),
        ]
        result = build_canonical_history(
            points,
            anchors,
            heating_m3_per_kwh=1.0,
            dhw_m3_per_kwh=1.0,
            timezone=UTC,
            settings=CanonicalHistorySettings(
                gap_profile_window=timedelta(days=30),
                min_profile_samples=1,
            ),
        )
        interval = result.intervals[0]
        self.assertEqual(interval.reconstructed_gap_hours, 4)
        self.assertIn("reconstructed_gap", interval.quality)
        self.assertAlmostEqual(interval.provisional_m3, 24.0)
        self.assertAlmostEqual(interval.scale_factor or 0.0, 1.0)
        self.assertAlmostEqual(result.hours[-1].cumulative_m3, 124.0)

    def test_day_precision_anchor_is_kept_as_uncertain_boundary(self) -> None:
        points = [
            SourcePoint(datetime(2026, 1, 1, hour, tzinfo=UTC), float(hour), 0.0)
            for hour in range(4)
        ]
        anchors = [
            PhysicalAnchor(
                datetime(2026, 1, 1, 0, tzinfo=UTC),
                10.0,
                source="invoice_billing",
                timestamp_precision="day",
            ),
            PhysicalAnchor(datetime(2026, 1, 1, 3, tzinfo=UTC), 13.0),
        ]
        result = build_canonical_history(
            points,
            anchors,
            heating_m3_per_kwh=1.0,
            dhw_m3_per_kwh=1.0,
            timezone=UTC,
        )
        self.assertIn("uncertain_anchor_time", result.intervals[0].quality)


if __name__ == "__main__":
    unittest.main()
