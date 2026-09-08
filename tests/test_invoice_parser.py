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


# Syntetyczny odpowiednik starszego układu, w którym energia w wierszu
# dystrybucji ma część dziesiętną.
FAKTURA_STARSZA_JEDNA_STAWKA = """
DUON Dystrybucja
Faktura VAT nr 1111111111
Za okres: 01.01.2030-31.01.2030
Data wystawienia: 02.02.2030
TERMIN PŁATNOŚCI: 16.02.2030
Punkt odbioru (999002)
Układ pomiarowy nr Rodzaj odczytu Data Wskazanie Układ pomiarowy nr Rodzaj odczytu Data Wskazanie ZUŻYCIE
TEST 00000000001 Rozliczeniowy 31.12.2029 100,00 TEST 00000000001 Rozliczeniowy 31.01.2030 110,00 10,00
Suma: 10,00
Opłata abonamentowa - gaz 1 SZT 4,00 4,00 0,92 4,92 23%
Opłata dystrybucyjna stała - gaz 1 SZT 6,00 6,00 1,38 7,38 23%
Należność za gaz E (10,00 KWH/M3 * 10 M3) 100,000 KWH 0,12345 12,35 2,84 15,19 23%
Opłata dystrybucyjna zmienna (10,00 KWH/M3 * 10 M3) 100,000 KWH 0,06789 6,79 1,56 8,35 23%
PUNKT ODBIORU 999002 29,14 6,70 35,84 23%
"""


# Syntetyczny odpowiednik faktury obejmującej zmianę taryfy w trakcie okresu.
# Pozycje gazowe i dystrybucyjne są wtedy rozbite na dwa wiersze.
FAKTURA_DWIE_STAWKI = """
DUON Dystrybucja
Faktura VAT nr 2222222222
Za okres: 01.02.2030-28.02.2030
Data wystawienia: 02.03.2030
TERMIN PŁATNOŚCI: 16.03.2030
Punkt odbioru (999003)
Układ pomiarowy nr Rodzaj odczytu Data Wskazanie Układ pomiarowy nr Rodzaj odczytu Data Wskazanie ZUŻYCIE
TEST 00000000002 Rozliczeniowy 31.01.2030 200,00 TEST 00000000002 Rozliczeniowy 28.02.2030 220,00 20,00
Suma: 20,00
Opłata abonamentowa - gaz 1 SZT 3,00 3,00 0,69 3,69 23%
Należność za gaz E (10,00 KWH/M3 * 5 M3) 50,000 KWH 0,10 5,00 1,15 6,15 23%
Należność za gaz E (10,00 KWH/M3 * 15 M3) 150,000 KWH 0,20 30,00 6,90 36,90 23%
Opłata dystrybucyjna stała - gaz 0,25 SZT 4,00 1,00 0,23 1,23 23%
Opłata dystrybucyjna stała - gaz 0,75 SZT 8,00 6,00 1,38 7,38 23%
Opłata dystrybucyjna zmienna (10,00 KWH/M3 * 5 M3) 50,000 KWH 0,01 0,50 0,12 0,62 23%
Opłata dystrybucyjna zmienna (10,00 KWH/M3 * 15 M3) 150,000 KWH 0,03 4,50 1,04 5,54 23%
PUNKT ODBIORU 999003 50,00 11,50 61,50 23%
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

    def test_starsza_faktura_z_dziesietna_energia_dystrybucji(self) -> None:
        invoice = parse_invoice_text(FAKTURA_STARSZA_JEDNA_STAWKA)

        self.assertEqual(invoice.consumption_m3, 10.0)
        self.assertEqual(invoice.billed_energy_kwh, 100.0)
        self.assertAlmostEqual(invoice.conversion_factor_kwh_m3, 10.0)
        self.assertAlmostEqual(invoice.gas_rate_net_pln_kwh, 0.12345)
        self.assertAlmostEqual(invoice.distribution_fixed_net_pln, 6.0)
        self.assertAlmostEqual(invoice.distribution_variable_net_pln_kwh, 0.06789)

    def test_faktura_z_dwiema_stawkami_w_okresie(self) -> None:
        invoice = parse_invoice_text(FAKTURA_DWIE_STAWKI)

        self.assertEqual(invoice.consumption_m3, 20.0)
        self.assertEqual(invoice.billed_energy_kwh, 200.0)
        self.assertAlmostEqual(invoice.conversion_factor_kwh_m3, 10.0)
        self.assertAlmostEqual(invoice.gas_rate_net_pln_kwh, 0.175)
        self.assertAlmostEqual(invoice.distribution_fixed_net_pln, 7.0)
        self.assertAlmostEqual(invoice.distribution_variable_net_pln_kwh, 0.025)

    def test_tylko_rozliczeniowy_jest_zaufana_kotwica(self) -> None:
        self.assertTrue(is_trusted_billing_reading("Rozliczeniowy"))
        self.assertTrue(is_trusted_billing_reading("  ROZLICZENIOWY  "))
        self.assertFalse(is_trusted_billing_reading("Szacowany"))
        self.assertFalse(is_trusted_billing_reading("Prognoza"))


if __name__ == "__main__":
    unittest.main()
