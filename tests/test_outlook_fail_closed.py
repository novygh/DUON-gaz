from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest


PACKAGE = "duon_gaz_outlook_test"
MODULE_NAME = f"{PACKAGE}.outlook_sync"
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "duon_gaz"
    / "outlook_sync.py"
)


class DuonGraphError(RuntimeError):
    pass


class DuonInvoiceParseError(ValueError):
    pass


class CanonicalHistoryError(RuntimeError):
    pass


def _install_stubs() -> None:
    package = ModuleType(PACKAGE)
    package.__path__ = []
    sys.modules[PACKAGE] = package

    homeassistant = ModuleType("homeassistant")
    homeassistant_util = ModuleType("homeassistant.util")
    homeassistant_dt = ModuleType("homeassistant.util.dt")
    homeassistant_dt.utcnow = lambda: datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    homeassistant_util.dt = homeassistant_dt
    homeassistant.util = homeassistant_util
    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.util"] = homeassistant_util
    sys.modules["homeassistant.util.dt"] = homeassistant_dt

    canonical_history = ModuleType(f"{PACKAGE}.canonical_history")
    canonical_history.CanonicalHistoryError = CanonicalHistoryError
    sys.modules[canonical_history.__name__] = canonical_history

    canonical_statistics = ModuleType(f"{PACKAGE}.canonical_statistics")

    async def async_refresh_canonical_tail_statistics(*_args, **_kwargs):
        return {"status": "ok"}

    canonical_statistics.async_refresh_canonical_tail_statistics = (
        async_refresh_canonical_tail_statistics
    )
    sys.modules[canonical_statistics.__name__] = canonical_statistics

    const = ModuleType(f"{PACKAGE}.const")
    const.CONF_METER_NUMBER = "meter_number"
    const.CONF_OUTLOOK_FOLDER = "outlook_folder"
    const.CONF_OUTLOOK_SENDER = "outlook_sender"
    const.CONF_OUTLOOK_SUBJECT = "outlook_subject"
    sys.modules[const.__name__] = const

    graph = ModuleType(f"{PACKAGE}.graph")
    graph.DuonGraphClient = object
    graph.DuonGraphError = DuonGraphError
    sys.modules[graph.__name__] = graph

    invoice_import = ModuleType(f"{PACKAGE}.invoice_import")

    async def async_import_invoice(*_args, **_kwargs):
        raise AssertionError("Test musi podmienić async_import_invoice")

    invoice_import.async_import_invoice = async_import_invoice
    sys.modules[invoice_import.__name__] = invoice_import

    invoice_parser = ModuleType(f"{PACKAGE}.invoice_parser")
    invoice_parser.DuonInvoiceParseError = DuonInvoiceParseError

    def parse_invoice_pdf_bytes(*_args, **_kwargs):
        raise AssertionError("Test musi podmienić parse_invoice_pdf_bytes")

    invoice_parser.parse_invoice_pdf_bytes = parse_invoice_pdf_bytes
    sys.modules[invoice_parser.__name__] = invoice_parser


_install_stubs()
SPEC = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Nie można załadować synchronizatora z {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[MODULE_NAME] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


class FakeStore:
    def __init__(self, initial: dict, *, fail_commit: bool = False) -> None:
        self.persisted = deepcopy(initial)
        self.fail_commit = fail_commit

    async def async_load(self):
        return deepcopy(self.persisted)


class FakeRuntime:
    def __init__(self, initial: dict, *, fail_commit: bool = False) -> None:
        self.data = deepcopy(initial)
        self.hass = FakeHass()
        self.store = FakeStore(initial, fail_commit=fail_commit)
        self.notifications = 0
        self.snapshot_refreshes = 0

    async def async_save(self) -> None:
        if not self.store.fail_commit:
            self.store.persisted = deepcopy(self.data)

    def async_notify(self) -> None:
        self.notifications += 1

    async def async_refresh_source_snapshot(self, *, notify: bool = True) -> None:
        self.snapshot_refreshes += 1


class FakeGraph:
    def __init__(self, messages, attachments) -> None:
        self.messages = messages
        self.attachments = attachments

    async def async_find_mail_folder(self, _name):
        return {"id": "folder", "displayName": "Faktury", "path": "Faktury"}

    async def async_list_matching_messages(self, _folder_id, *, sender, subject):
        return deepcopy(self.messages)

    async def async_get_pdf_attachments(self, message_id):
        return list(self.attachments.get(message_id, []))


def _message(message_id: str, when: str) -> dict:
    return {
        "id": message_id,
        "subject": "Faktura",
        "receivedDateTime": when,
        "internetMessageId": f"<{message_id}@example.invalid>",
    }


def _attachment(name: str, content: bytes):
    return SimpleNamespace(name=name, content=content)


def _config() -> dict:
    return {
        "outlook_folder": "Faktury",
        "outlook_sender": "sender@example.invalid",
        "outlook_subject": "Faktura",
        "meter_number": "123456",
    }


def _initial_data() -> dict:
    return {
        "billing_periods": [{"invoice_number": "BAZOWA"}],
        "processed_invoices": [{"invoice_number": "BAZOWA"}],
        "processed_messages": [{"message_id": "stara"}],
    }


class TestOutlookFailClosed(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_probe = MODULE._pdf_is_encrypted
        self.original_parser = MODULE.parse_invoice_pdf_bytes
        self.original_import = MODULE.async_import_invoice

    def tearDown(self) -> None:
        MODULE._pdf_is_encrypted = self.original_probe
        MODULE.parse_invoice_pdf_bytes = self.original_parser
        MODULE.async_import_invoice = self.original_import

    async def test_niezabezpieczony_pdf_jest_ignorowany(self) -> None:
        runtime = FakeRuntime(_initial_data())
        graph = FakeGraph(
            [_message("nowa", "2026-09-08T10:00:00Z")],
            {
                "nowa": [
                    _attachment("informacja.pdf", b"plain-info"),
                    _attachment("faktura.pdf", b"enc-good"),
                ]
            },
        )
        MODULE._pdf_is_encrypted = lambda content: content.startswith(b"enc-")
        MODULE.parse_invoice_pdf_bytes = lambda content, password: SimpleNamespace(
            invoice_number="NOWA"
        )

        async def fake_import(runtime, invoice, **kwargs):
            runtime.data.setdefault("processed_invoices", []).append(
                {"invoice_number": invoice.invoice_number}
            )
            return {"status": "imported", "anchor_added": False}

        MODULE.async_import_invoice = fake_import
        sync = MODULE.DuonOutlookSynchronizer(runtime, graph, _config())
        result = await sync.async_sync(reason="test")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["ignored_unprotected_pdfs"], 1)
        self.assertEqual(result["imported_invoices"], 1)
        self.assertIn(
            "NOWA",
            {item["invoice_number"] for item in runtime.data["processed_invoices"]},
        )
        self.assertIn(
            "nowa",
            {item["message_id"] for item in runtime.data["processed_messages"]},
        )

    async def test_bledny_zaszyfrowany_pdf_blokuje_cala_paczke(self) -> None:
        baseline = _initial_data()
        runtime = FakeRuntime(baseline)
        graph = FakeGraph(
            [
                _message("pierwsza", "2026-09-08T10:00:00Z"),
                _message("druga", "2026-09-08T11:00:00Z"),
            ],
            {
                "pierwsza": [_attachment("dobra.pdf", b"enc-good")],
                "druga": [_attachment("zla.pdf", b"enc-bad")],
            },
        )
        MODULE._pdf_is_encrypted = lambda _content: True

        def fake_parser(content, password):
            if content == b"enc-bad":
                raise DuonInvoiceParseError("Nieobsługiwany układ faktury.")
            return SimpleNamespace(invoice_number="NIE-MOZE-BYC-ZAPISANA")

        MODULE.parse_invoice_pdf_bytes = fake_parser
        import_calls = 0

        async def fake_import(*_args, **_kwargs):
            nonlocal import_calls
            import_calls += 1
            return {"status": "imported", "anchor_added": False}

        MODULE.async_import_invoice = fake_import
        sync = MODULE.DuonOutlookSynchronizer(runtime, graph, _config())
        result = await sync.async_sync(reason="test")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["guard"]["stage"], "invoice_preflight")
        self.assertEqual(result["imported_invoices"], 0)
        self.assertEqual(import_calls, 0)
        self.assertEqual(runtime.data["billing_periods"], baseline["billing_periods"])
        self.assertEqual(
            runtime.data["processed_invoices"], baseline["processed_invoices"]
        )
        self.assertEqual(
            runtime.data["processed_messages"], baseline["processed_messages"]
        )

    async def test_niepotwierdzony_zapis_store_cofa_etapowana_paczke(self) -> None:
        baseline = _initial_data()
        runtime = FakeRuntime(baseline, fail_commit=True)
        graph = FakeGraph(
            [_message("nowa", "2026-09-08T10:00:00Z")],
            {"nowa": [_attachment("faktura.pdf", b"enc-good")]},
        )
        MODULE._pdf_is_encrypted = lambda _content: True
        MODULE.parse_invoice_pdf_bytes = lambda content, password: SimpleNamespace(
            invoice_number="NOWA"
        )

        async def fake_import(runtime, invoice, **kwargs):
            runtime.data.setdefault("billing_periods", []).append(
                {"invoice_number": invoice.invoice_number}
            )
            runtime.data.setdefault("processed_invoices", []).append(
                {"invoice_number": invoice.invoice_number}
            )
            return {"status": "imported", "anchor_added": False}

        MODULE.async_import_invoice = fake_import
        sync = MODULE.DuonOutlookSynchronizer(runtime, graph, _config())
        result = await sync.async_sync(reason="test")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["guard"]["stage"], "store_commit")
        self.assertEqual(runtime.data["billing_periods"], baseline["billing_periods"])
        self.assertEqual(
            runtime.data["processed_invoices"], baseline["processed_invoices"]
        )
        self.assertEqual(
            runtime.data["processed_messages"], baseline["processed_messages"]
        )


if __name__ == "__main__":
    unittest.main()
