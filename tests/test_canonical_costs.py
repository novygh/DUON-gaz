from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

MODULE = (
    Path(__file__).parents[1]
    / "custom_components"
    / "duon_gaz"
    / "canonical_costs.py"
)
spec = importlib.util.spec_from_file_location("canonical_costs", MODULE)
canonical_costs = importlib.util.module_from_spec(spec)
sys.modules["canonical_costs"] = canonical_costs
assert spec.loader is not None
spec.loader.exec_module(canonical_costs)

build_canonical_costs = canonical_costs.build_canonical_costs
billing_period_fingerprint = canonical_costs.billing_period_fingerprint
CanonicalCostError = canonical_costs.CanonicalCostError


def _hours(start: datetime, count: int):
    return [
        SimpleNamespace(
            start=start + timedelta(hours=index),
            heating_m3=1.0,
            dhw_m3=0.5,
        )
        for index in range(count)
    ]


def _invoice(number: str, start: str, end: str, gross: float = 123.0):
    return {
        "invoice_number": number,
        "previous_reading": {"date": start},
        "current_reading": {"date": end},
        "billed_energy_kwh": 360.0,
        "gas_rate_net_pln_kwh": 0.10,
        "distribution_variable_net_pln_kwh": 0.02,
        "net_total_pln": 100.0,
        "gross_total_pln": gross,
    }


class CanonicalCostTests(unittest.TestCase):
    def test_invoice_cost_closes_exactly_to_gross_and_keeps_fixed_remainder(self) -> None:
        start = datetime(2026, 1, 1, 12, tzinfo=UTC)
        rows = _hours(start, 48)
        invoice = _invoice("A", "2026-01-01", "2026-01-02")

        result = build_canonical_costs(
            rows,
            [invoice],
            timezone=UTC,
            conversion_factor_kwh_m3=11.0,
            gas_rate_net_pln_kwh=0.11,
            distribution_variable_net_pln_kwh=0.03,
            subscription_net_pln_month=12.0,
            distribution_fixed_net_pln_month=18.0,
            vat_rate=0.23,
        )

        invoiced = result.hours[:24]
        self.assertAlmostEqual(sum(row.total_pln for row in invoiced), 123.0, places=6)
        self.assertAlmostEqual(result.invoiced_closure_error_pln, 0.0, places=6)
        self.assertGreater(sum(row.fixed_pln for row in invoiced), 0.0)
        self.assertGreater(sum(row.heating_pln for row in invoiced), 0.0)
        self.assertGreater(sum(row.dhw_pln for row in invoiced), 0.0)
        self.assertGreater(result.provisional_pln, 0.0)

    def test_variable_cost_split_follows_canonical_co_cwu_ratio(self) -> None:
        start = datetime(2026, 1, 1, 12, tzinfo=UTC)
        rows = _hours(start, 24)
        result = build_canonical_costs(
            rows,
            [_invoice("A", "2026-01-01", "2026-01-02")],
            timezone=UTC,
            conversion_factor_kwh_m3=11.0,
            gas_rate_net_pln_kwh=0.11,
            distribution_variable_net_pln_kwh=0.03,
            subscription_net_pln_month=12.0,
            distribution_fixed_net_pln_month=18.0,
            vat_rate=0.23,
        )
        co = sum(row.heating_pln for row in result.hours)
        dhw = sum(row.dhw_pln for row in result.hours)
        self.assertAlmostEqual(co / dhw, 2.0, places=6)

    def test_overlapping_invoices_fail_closed(self) -> None:
        start = datetime(2026, 1, 1, 12, tzinfo=UTC)
        rows = _hours(start, 72)
        with self.assertRaises(CanonicalCostError):
            build_canonical_costs(
                rows,
                [
                    _invoice("A", "2026-01-01", "2026-01-03"),
                    _invoice("B", "2026-01-02", "2026-01-04"),
                ],
                timezone=UTC,
                conversion_factor_kwh_m3=11.0,
                gas_rate_net_pln_kwh=0.11,
                distribution_variable_net_pln_kwh=0.03,
                subscription_net_pln_month=12.0,
                distribution_fixed_net_pln_month=18.0,
                vat_rate=0.23,
            )

    def test_billing_fingerprint_is_order_independent(self) -> None:
        a = _invoice("A", "2026-01-01", "2026-01-02")
        b = _invoice("B", "2026-01-02", "2026-01-03", gross=140.0)
        self.assertEqual(
            billing_period_fingerprint([a, b]),
            billing_period_fingerprint([b, a]),
        )


if __name__ == "__main__":
    unittest.main()
