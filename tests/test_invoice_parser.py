from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


# Parser faktury jest czystym modułem Pythona. Ładujemy go bez importowania
# pakietu integracji, aby test nie wymagał środowiska Home Assistanta.
PARSER_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "duon_gaz"
    / "invoice_parser.py"
)
SPEC = importlib.util.spec_from_file_location("duon_gaz_invoice_parser_test", PARSER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Nie można załadować parsera faktur z {PARSER_PATH}")

MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

is_trusted_billing_reading = MODULE.is_trusted_billing_reading
parse_invoice_text = MODULE.parse_invoice_text


FAKTURA_TESTOWA = """
DUON Dystrybucja
Faktura VAT nr 0123456789
Za okres: 01.08.2026 - 31.08.2026
Data wystawienia: 03.09.2026
TERMIN PŁATNOŚCI: 18.09.2026
Punkt odbioru (123456)
Układ pomiarowy nr Rodzaj odczytu Data Wskazanie
Rozliczeniowy 31.07.2026 1000 000000 Rozliczeniowy 31.08.2026 1009 9
Suma:
Należność za gaz E ( 11,43 KWH/M3 * 9 M3 ) 103 KWH 0,22684
Opłata abonamentowa - gaz 1 SZT 8,00
Opłata dystrybucyjna stała - gaz 1 SZT 8,39
Opłata dystrybucyjna zmienna (test) 103 KWH 0,0854
PUNKT ODBIORU 123456 48,54 11,17 59,71 23%
"""


class TestParserFakturyDuon(unittest.TestCase):
    def test_poprawna_faktura(self) -> None:
        invoice = parse_invoice_text(FAKTURA_TESTOWA)

        self.assertEqual(invoice.invoice_number, "0123456789")
        self.assertEqual(invoice.point_id, "123456")
        self.assertEqual(invoice.previous_reading.meter_m3, 1000.0)
        self.assertEqual(invoice.current_reading.meter_m3, 1009.0)
        self.assertEqual(invoice.consumption_m3, 9.0)
        self.assertEqual(invoice.billed_energy_kwh, 103.0)
        self.assertAlmostEqual(invoice.conversion_factor_kwh_m3, 11.43)
        self.assertAlmostEqual(invoice.gas_rate_net_pln_kwh, 0.22684)
        self.assertAlmostEqual(invoice.distribution_variable_net_pln_kwh, 0.0854)
        self.assertAlmostEqual(invoice.vat_rate, 0.23)
        self.assertAlmostEqual(invoice.gross_total_pln, 59.71)

    def test_tylko_rozliczeniowy_jest_zaufana_kotwica(self) -> None:
        self.assertTrue(is_trusted_billing_reading("Rozliczeniowy"))
        self.assertTrue(is_trusted_billing_reading("  ROZLICZENIOWY  "))
        self.assertFalse(is_trusted_billing_reading("Szacowany"))
        self.assertFalse(is_trusted_billing_reading("Prognoza"))


if __name__ == "__main__":
    unittest.main()
