"""Parser faktur PDF DUON."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
import re
from typing import Any, BinaryIO, Callable


class DuonInvoiceParseError(ValueError):
    """Błąd bezpiecznego przetwarzania faktury DUON."""


@dataclass(frozen=True, slots=True)
class DuonInvoiceReading:
    """Jedno wskazanie gazomierza wydrukowane na fakturze."""

    reading_type: str
    date: date
    meter_m3: float


@dataclass(frozen=True, slots=True)
class DuonInvoice:
    """Znormalizowane dane faktury DUON używane przez integrację."""

    invoice_number: str
    period_start: date
    period_end: date
    issue_date: date
    due_date: date
    point_id: str
    previous_reading: DuonInvoiceReading
    current_reading: DuonInvoiceReading
    consumption_m3: float
    conversion_factor_kwh_m3: float
    billed_energy_kwh: float
    gas_rate_net_pln_kwh: float
    subscription_net_pln: float
    distribution_fixed_net_pln: float
    distribution_variable_net_pln_kwh: float
    vat_rate: float
    net_total_pln: float
    vat_total_pln: float
    gross_total_pln: float

    def as_dict(self) -> dict[str, Any]:
        """Zwróć reprezentację zgodną z JSON i magazynem danych."""
        data = asdict(self)
        for key in ("period_start", "period_end", "issue_date", "due_date"):
            data[key] = data[key].isoformat()
        for key in ("previous_reading", "current_reading"):
            data[key]["date"] = data[key]["date"].isoformat()
        return data


def normalize_reading_type(value: str) -> str:
    """Znormalizuj literalny typ odczytu DUON bez zmiany jego znaczenia."""
    return " ".join(value.strip().lower().split())


def is_trusted_billing_reading(value: str) -> bool:
    """Sprawdź, czy typ odczytu może być użyty jako kotwica gazomierza.

    „Rozliczeniowy” jest literalnym określeniem widocznym na fakturach DUON.
    Nie zamieniamy go na „inkasencki”, ponieważ faktura nie podaje, kto
    fizycznie wykonał odczyt.
    """
    return normalize_reading_type(value) == "rozliczeniowy"


def _date(value: str) -> date:
    return datetime.strptime(value, "%d.%m.%Y").date()


def _number(value: str) -> float:
    normalized = value.strip().replace("\xa0", "").replace(" ", "")
    if "," in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    try:
        return float(Decimal(normalized))
    except (InvalidOperation, ValueError) as err:
        raise DuonInvoiceParseError(
            f"Nieprawidłowa liczba na fakturze: {value!r}"
        ) from err


def _kwh_number(value: str) -> float:
    """Odczytaj ilość kWh z polskim separatorem dziesiętnym lub tysięcy.

    W tekstach PDF DUON wartości energii całkowitej mogą występować jako
    ``1.500`` KWH, gdzie kropka jest separatorem tysięcy. Nie stosujemy tej
    reguły do stawek jednostkowych, aby nie zmieniać znaczenia liczb
    dziesiętnych zapisywanych z kropką w danych testowych.
    """
    normalized = value.strip().replace("\xa0", "").replace(" ", "")
    if "," not in normalized and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", normalized):
        return _number(normalized.replace(".", ""))
    return _number(value)


def _one(pattern: str, text: str, field: str, flags: int = 0) -> re.Match[str]:
    match = re.search(pattern, text, flags)
    if match is None:
        raise DuonInvoiceParseError(f"Nie znaleziono pola faktury: {field}")
    return match


def _many(pattern: str, text: str, field: str, flags: int = 0) -> list[re.Match[str]]:
    matches = list(re.finditer(pattern, text, flags))
    if not matches:
        raise DuonInvoiceParseError(f"Nie znaleziono pola faktury: {field}")
    return matches


def _weighted_rate(
    rows: list[re.Match[str]],
    *,
    weight_group: str,
    rate_group: str,
    field: str,
    weight_parser: Callable[[str], float] = _number,
) -> float:
    """Zwróć efektywną stawkę jednostkową dla jednej lub wielu pozycji."""
    total_weight = 0.0
    weighted_sum = 0.0
    for row in rows:
        weight = weight_parser(row.group(weight_group))
        rate = _number(row.group(rate_group))
        if weight < 0:
            raise DuonInvoiceParseError(f"Ujemna podstawa stawki: {field}")
        total_weight += weight
        weighted_sum += weight * rate

    if total_weight <= 0:
        raise DuonInvoiceParseError(f"Zerowa podstawa stawki: {field}")
    return weighted_sum / total_weight


def _extract_invoice_text_from_reader(source: str | Path | BinaryIO, password: str) -> str:
    """Odszyfruj fakturę DUON i pobierz tekst bez użycia OCR."""
    try:
        from pypdf import PdfReader
    except ImportError as err:
        raise DuonInvoiceParseError("Brak biblioteki pypdf do odczytu faktur.") from err

    reader = PdfReader(source)
    if reader.is_encrypted:
        result = reader.decrypt(password)
        if result == 0:
            raise DuonInvoiceParseError("Nieprawidłowe hasło do faktury PDF.")

    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if "Faktura VAT nr" not in text or "DUON Dystrybucja" not in text:
        raise DuonInvoiceParseError("PDF nie wygląda jak faktura DUON Dystrybucja.")
    return text


def extract_invoice_text(path: str | Path, password: str) -> str:
    """Odszyfruj fakturę DUON z pliku i pobierz jej tekst."""
    return _extract_invoice_text_from_reader(str(path), password)


def extract_invoice_text_from_bytes(content: bytes, password: str) -> str:
    """Odszyfruj fakturę DUON pobraną do pamięci i pobierz tekst."""
    return _extract_invoice_text_from_reader(BytesIO(content), password)


def parse_invoice_text(text: str) -> DuonInvoice:
    """Przetwórz tekst z obsługiwanych układów faktury DUON."""
    invoice_number = _one(
        r"Faktura VAT nr\s+(\d+)", text, "invoice_number"
    ).group(1)

    period = _one(
        r"Za okres:\s*(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})",
        text,
        "period",
    )
    issue = _one(
        r"Data wystawienia:\s*(\d{2}\.\d{2}\.\d{4})",
        text,
        "issue_date",
    )
    due = _one(
        r"TERMIN PŁATNOŚCI:\s*(\d{2}\.\d{2}\.\d{4})",
        text,
        "due_date",
    )
    point = _one(r"Punkt odbioru\s*\((\d+)\)", text, "point_id")

    table = _one(
        r"Układ pomiarowy nr\s+Rodzaj odczytu\s+Data\s+Wskazanie.*?\n"
        r"(?P<row>.*?)\nSuma:",
        text,
        "meter_readings",
        re.DOTALL,
    ).group("row")
    row = _one(
        r"(?P<prev_type>[^\s]+)\s+"
        r"(?P<prev_date>\d{2}\.\d{2}\.\d{4})\s+"
        r"(?P<prev_meter>[\d.,]+)\s+"
        r".*?\s+"
        r"(?P<curr_type>[^\s]+)\s+"
        r"(?P<curr_date>\d{2}\.\d{2}\.\d{4})\s+"
        r"(?P<curr_meter>[\d.,]+)\s+"
        r"(?P<consumption>[\d.,]+)\s*$",
        table.strip(),
        "meter_readings_row",
        re.DOTALL,
    )

    gas_rows = _many(
        r"Należność za gaz E\s*\(\s*(?P<factor>[\d.,]+)\s*KWH/M3\s*\*\s*"
        r"(?P<m3>[\d.,]+)\s*M3\s*\)\s*(?P<kwh>[\d.,]+)\s*KWH\s*"
        r"(?P<rate>[\d.,]+)",
        text,
        "gas_charge",
        re.IGNORECASE,
    )
    subscription_rows = _many(
        r"Opłata abonamentowa\s*-\s*gaz\s+(?P<quantity>[\d.,]+)\s+SZT\s+"
        r"(?P<rate>[\d.,]+)",
        text,
        "subscription_rate",
        re.IGNORECASE,
    )
    fixed_rows = _many(
        r"Opłata dystrybucyjna stała\s*-\s*gaz\s+(?P<quantity>[\d.,]+)\s+SZT\s+"
        r"(?P<rate>[\d.,]+)",
        text,
        "distribution_fixed_rate",
        re.IGNORECASE,
    )
    variable_rows = _many(
        r"Opłata dystrybucyjna zmienna\s*\([^\n]+\)\s*"
        r"(?P<kwh>[\d.,]+)\s*KWH\s+(?P<rate>[\d.,]+)",
        text,
        "distribution_variable_rate",
        re.IGNORECASE,
    )
    totals = _one(
        r"PUNKT ODBIORU\s+\d+\s+([\d.,]+)\s+([\d.,]+)\s+"
        r"([\d.,]+)\s+(\d+)%",
        text,
        "invoice_totals",
        re.IGNORECASE,
    )

    previous = DuonInvoiceReading(
        reading_type=row.group("prev_type"),
        date=_date(row.group("prev_date")),
        meter_m3=_number(row.group("prev_meter")),
    )
    current = DuonInvoiceReading(
        reading_type=row.group("curr_type"),
        date=_date(row.group("curr_date")),
        meter_m3=_number(row.group("curr_meter")),
    )
    consumption = _number(row.group("consumption"))

    gas_m3 = [_number(item.group("m3")) for item in gas_rows]
    gas_kwh = [_kwh_number(item.group("kwh")) for item in gas_rows]
    gas_factors = [_number(item.group("factor")) for item in gas_rows]
    charge_m3 = sum(gas_m3)
    billed_kwh = sum(gas_kwh)
    if charge_m3 <= 0:
        raise DuonInvoiceParseError("Zerowe zużycie w pozycjach gazowych faktury.")
    factor = gas_factors[0]
    if any(abs(value - factor) > 0.000001 for value in gas_factors[1:]):
        raise DuonInvoiceParseError(
            "Pozycje gazowe mają różne współczynniki konwersji kWh/m3."
        )
    gas_rate = _weighted_rate(
        gas_rows,
        weight_group="kwh",
        rate_group="rate",
        field="gas_rate",
        weight_parser=_kwh_number,
    )
    subscription_rate = _weighted_rate(
        subscription_rows,
        weight_group="quantity",
        rate_group="rate",
        field="subscription_rate",
    )
    fixed_rate = _weighted_rate(
        fixed_rows,
        weight_group="quantity",
        rate_group="rate",
        field="distribution_fixed_rate",
    )
    variable_rate = _weighted_rate(
        variable_rows,
        weight_group="kwh",
        rate_group="rate",
        field="distribution_variable_rate",
        weight_parser=_kwh_number,
    )
    variable_kwh = sum(_kwh_number(item.group("kwh")) for item in variable_rows)

    if abs((current.meter_m3 - previous.meter_m3) - consumption) > 0.01:
        raise DuonInvoiceParseError(
            "Zużycie na fakturze nie zgadza się z różnicą stanów licznika."
        )
    if abs(charge_m3 - consumption) > 0.01:
        raise DuonInvoiceParseError(
            "Zużycie w pozycjach gazowych nie zgadza się z tabelą odczytów."
        )
    expected_kwh = factor * consumption
    if abs(billed_kwh - round(expected_kwh)) > 1.01:
        raise DuonInvoiceParseError(
            "Energia rozliczeniowa nie zgadza się ze współczynnikiem konwersji."
        )
    if abs(variable_kwh - billed_kwh) > 0.01:
        raise DuonInvoiceParseError(
            "Energia w opłacie dystrybucyjnej zmiennej nie zgadza się z pozycją gazową."
        )

    return DuonInvoice(
        invoice_number=invoice_number,
        period_start=_date(period.group(1)),
        period_end=_date(period.group(2)),
        issue_date=_date(issue.group(1)),
        due_date=_date(due.group(1)),
        point_id=point.group(1),
        previous_reading=previous,
        current_reading=current,
        consumption_m3=consumption,
        conversion_factor_kwh_m3=factor,
        billed_energy_kwh=billed_kwh,
        gas_rate_net_pln_kwh=gas_rate,
        subscription_net_pln=subscription_rate,
        distribution_fixed_net_pln=fixed_rate,
        distribution_variable_net_pln_kwh=variable_rate,
        vat_rate=_number(totals.group(4)) / 100.0,
        net_total_pln=_number(totals.group(1)),
        vat_total_pln=_number(totals.group(2)),
        gross_total_pln=_number(totals.group(3)),
    )


def parse_invoice_pdf(path: str | Path, password: str) -> DuonInvoice:
    """Odszyfruj i przetwórz jedną fakturę PDF DUON z pliku."""
    return parse_invoice_text(extract_invoice_text(path, password))


def parse_invoice_pdf_bytes(content: bytes, password: str) -> DuonInvoice:
    """Odszyfruj i przetwórz jedną fakturę PDF DUON pobraną z Outlooka."""
    return parse_invoice_text(extract_invoice_text_from_bytes(content, password))
