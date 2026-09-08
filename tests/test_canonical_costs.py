from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


def _invoice(
    number: str,
    period_start: str,
    period_end: str,
    gross: float = 123.0,
    *,
    previous_reading: str | None = None,
    current_reading: str | None = None,
):
    return {
        "invoice_number": number,
        "period_start": period_start,
        "period_end": period_end,
        "previous_reading": {"date": previous_reading or period_start},
        "current_reading": {"date": current_reading or period_end},
        "billed_energy_kwh": 360.0,
        "gas_rate_net_pln_kwh": 0.10,
        "distribution_variable_net_pln_kwh": 0.02,
        "net_total_pln": 100.0,
        "gross_total_pln": gross,
    }


class CanonicalCostTests(unittest.TestCase):
    def test_invoice_cost_closes_exactly_to_gross_and_keeps_fixed_remainder(self) -> None:
        start = datetime(2026, 1, 1, 0, tzinfo=UTC)
        rows = _hours(start, 48)
        invoice = _invoice("A", "2026-01-01", "2026-01-01")

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
        start = datetime(2026, 1, 1, 0, tzinfo=UTC)
        rows = _hours(start, 24)
        result = build_canonical_costs(
            rows,
            [_invoice("A", "2026-01-01", "2026-01-01")],
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

    def test_accounting_period_not_meter_reading_dates_controls_allocation(self) -> None:
        start = datetime(2026, 5, 1, 0, tzinfo=UTC)
        rows = _hours(start, 31 * 24)
        invoice = _invoice(
            "MAY",
            "2026-05-01",
            "2026-05-31",
            previous_reading="2026-04-28",
            current_reading="2026-05-28",
        )

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

        self.assertEqual(result.applied_invoice_count, 1)
        self.assertTrue(all(row.invoiced for row in result.hours))
        self.assertAlmostEqual(sum(row.total_pln for row in result.hours), 123.0, places=6)
        self.assertEqual(
            result.latest_applied_invoice_end,
            datetime(2026, 6, 1, 0, tzinfo=UTC),
        )

    def test_dst_spring_forward_uses_real_utc_hour_count(self) -> None:
        warsaw = ZoneInfo("Europe/Warsaw")
        start_local = datetime(2026, 3, 28, 0, tzinfo=warsaw)
        end_local = datetime(2026, 3, 30, 0, tzinfo=warsaw)
        elapsed_hours = int(
            (
                end_local.astimezone(UTC) - start_local.astimezone(UTC)
            ).total_seconds()
            / 3600
        )
        self.assertEqual(elapsed_hours, 47)

        result = build_canonical_costs(
            _hours(start_local.astimezone(UTC), elapsed_hours),
            [_invoice("DST-SPRING", "2026-03-28", "2026-03-29")],
            timezone=warsaw,
            conversion_factor_kwh_m3=11.0,
            gas_rate_net_pln_kwh=0.11,
            distribution_variable_net_pln_kwh=0.03,
            subscription_net_pln_month=12.0,
            distribution_fixed_net_pln_month=18.0,
            vat_rate=0.23,
        )

        self.assertEqual(result.applied_invoice_count, 1)
        self.assertAlmostEqual(result.invoiced_closure_error_pln, 0.0, places=6)

    def test_dst_fall_back_uses_real_utc_hour_count(self) -> None:
        warsaw = ZoneInfo("Europe/Warsaw")
        start_local = datetime(2026, 10, 24, 0, tzinfo=warsaw)
        end_local = datetime(2026, 10, 26, 0, tzinfo=warsaw)
        elapsed_hours = int(
            (
                end_local.astimezone(UTC) - start_local.astimezone(UTC)
            ).total_seconds()
            / 3600
        )
        self.assertEqual(elapsed_hours, 49)

        result = build_canonical_costs(
            _hours(start_local.astimezone(UTC), elapsed_hours),
            [_invoice("DST-FALL", "2026-10-24", "2026-10-25")],
            timezone=warsaw,
            conversion_factor_kwh_m3=11.0,
            gas_rate_net_pln_kwh=0.11,
            distribution_variable_net_pln_kwh=0.03,
            subscription_net_pln_month=12.0,
            distribution_fixed_net_pln_month=18.0,
            vat_rate=0.23,
        )

        self.assertEqual(result.applied_invoice_count, 1)
        self.assertAlmostEqual(result.invoiced_closure_error_pln, 0.0, places=6)

    def test_overlapping_invoices_fail_closed(self) -> None:
        start = datetime(2026, 1, 1, 0, tzinfo=UTC)
        rows = _hours(start, 96)
        with self.assertRaises(CanonicalCostError):
            build_canonical_costs(
                rows,
                [
                    _invoice("A", "2026-01-01", "2026-01-02"),
                    _invoice("B", "2026-01-02", "2026-01-03"),
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
        a = _invoice("A", "2026-01-01", "2026-01-31")
        b = _invoice("B", "2026-02-01", "2026-02-28", gross=140.0)
        self.assertEqual(
            billing_period_fingerprint([a, b]),
            billing_period_fingerprint([b, a]),
        )

    def test_billing_fingerprint_ignores_meter_reading_dates(self) -> None:
        a = _invoice(
            "A",
            "2026-05-01",
            "2026-05-31",
            previous_reading="2026-04-28",
            current_reading="2026-05-28",
        )
        b = _invoice(
            "A",
            "2026-05-01",
            "2026-05-31",
            previous_reading="2026-04-30",
            current_reading="2026-05-31",
        )
        self.assertEqual(
            billing_period_fingerprint([a]),
            billing_period_fingerprint([b]),
        )


if __name__ == "__main__":
    unittest.main()
