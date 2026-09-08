from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import sys
import types
import unittest

PACKAGE = Path(__file__).parents[1] / "custom_components" / "duon_gaz"

package = types.ModuleType("duon_gaz")
package.__path__ = [str(PACKAGE)]
sys.modules.setdefault("duon_gaz", package)

for name in ("canonical_history", "canonical_series"):
    module_path = PACKAGE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"duon_gaz.{name}", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"duon_gaz.{name}"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)

CanonicalHour = sys.modules["duon_gaz.canonical_history"].CanonicalHour
merge_canonical_hours = sys.modules["duon_gaz.canonical_series"].merge_canonical_hours
select_provisional_refresh_hours = sys.modules[
    "duon_gaz.canonical_series"
].select_provisional_refresh_hours


def hour(start: datetime, gas_m3: float, cumulative_m3: float, quality=()):
    return CanonicalHour(
        start=start,
        heating_kwh=gas_m3,
        dhw_kwh=0.0,
        heating_m3=gas_m3,
        dhw_m3=0.0,
        unattributed_m3=0.0,
        gas_m3=gas_m3,
        cumulative_m3=cumulative_m3,
        quality=quality,
    )


class CanonicalSeriesTests(unittest.TestCase):
    def test_merges_partial_boundary_hour(self) -> None:
        h0 = datetime(2026, 1, 1, 10, tzinfo=UTC)
        h1 = datetime(2026, 1, 1, 11, tzinfo=UTC)
        settled = [
            hour(h0, 1.0, 101.0, ("normalized_to_meter",)),
            hour(h1, 0.3, 101.3, ("normalized_to_meter",)),
        ]
        provisional = [
            hour(h1, 0.2, 101.5, ("provisional_after_meter_anchor",)),
        ]

        result = merge_canonical_hours(settled, provisional)

        self.assertEqual(result.overlap_hour_count, 1)
        self.assertEqual(len(result.hours), 2)
        self.assertAlmostEqual(result.hours[-1].gas_m3, 0.5)
        self.assertAlmostEqual(result.hours[-1].cumulative_m3, 101.5)
        self.assertIn("normalized_to_meter", result.hours[-1].quality)
        self.assertIn("provisional_after_meter_anchor", result.hours[-1].quality)

    def test_exact_hour_anchor_needs_no_overlap(self) -> None:
        h0 = datetime(2026, 1, 1, 10, tzinfo=UTC)
        h1 = datetime(2026, 1, 1, 11, tzinfo=UTC)
        settled = [hour(h0, 1.0, 101.0)]
        provisional = [hour(h1, 0.4, 101.4)]

        result = merge_canonical_hours(settled, provisional)

        self.assertEqual(result.overlap_hour_count, 0)
        self.assertEqual(len(result.hours), 2)
        self.assertAlmostEqual(result.hours[-1].cumulative_m3, 101.4)

    def test_tail_refresh_includes_merged_boundary_hour(self) -> None:
        h0 = datetime(2026, 1, 1, 10, tzinfo=UTC)
        h1 = datetime(2026, 1, 1, 11, tzinfo=UTC)
        h2 = datetime(2026, 1, 1, 12, tzinfo=UTC)
        settled = [
            hour(h0, 1.0, 101.0),
            hour(h1, 0.3, 101.3),
        ]
        provisional = [
            hour(h1, 0.2, 101.5),
            hour(h2, 0.4, 101.9),
        ]
        combined = merge_canonical_hours(settled, provisional)

        refresh = select_provisional_refresh_hours(combined.hours, provisional)

        self.assertEqual([row.start for row in refresh], [h1, h2])
        self.assertAlmostEqual(refresh[0].gas_m3, 0.5)
        self.assertAlmostEqual(refresh[-1].cumulative_m3, 101.9)


if __name__ == "__main__":
    unittest.main()
