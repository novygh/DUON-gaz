# DUON Gaz

Niestandardowa integracja dla Home Assistanta do rekonstrukcji i bieżącego śledzenia zużycia gazu na podstawie fizycznych odczytów gazomierza, skumulowanych statystyk CO/CWU zapisanych w Recorder oraz danych rozliczeniowych DUON.

## Aktualna wersja

**0.3.4** — stabilny snapshot zweryfikowany na działającej instalacji Home Assistant 2026.9.1.

Szczegółowe informacje o wydaniu: [`RELEASE_NOTES_0.3.4.md`](RELEASE_NOTES_0.3.4.md).

## Co potrafi 0.3.4

- wybiera dowolne dwa sensory Home Assistanta jako źródła CO i CWU,
- korzysta ze skumulowanych statystyk `sum` z Recorder,
- zapisuje dokładne fizyczne odczyty gazomierza,
- osobno kalibruje CO i CWU w m³/kWh,
- rekonstruuje brakujące godziny,
- obsługuje korekty/rollbacki źródła bez tworzenia ujemnego zużycia,
- dokładnie domyka rozliczone okresy do fizycznego gazomierza,
- buduje bieżący szacowany ogon po ostatnim odczycie,
- publikuje monotoniczną statystykę Recorder:

```text
duon_gaz:canonical_gas
```

- automatycznie odświeża bieżący ogon po wygenerowaniu nowych godzinowych statystyk Recorder,
- wykonuje pełny rebuild po zmianie aktywnego zestawu kotwic,
- importuje zaszyfrowane faktury PDF DUON przez parser `pypdf`, bez OCR,
- obsługuje starsze i nowsze układy faktur oraz zmiany stawek w okresie rozliczeniowym,
- automatycznie pobiera faktury z Microsoft Outlook / Graph,
- działa fail-closed: nierozpoznana zaszyfrowana faktura blokuje całą nową paczkę bez częściowego zapisu,
- zapisuje paczkę faktur atomowo i weryfikuje Store po zapisie,
- nie modyfikuje surowych statystyk źródłowych CO/CWU.

## Wymagania

- Home Assistant z włączonym Recorder,
- dwa sensory źródłowe CO/CWU posiadające statystyki `sum`,
- fizyczne lub zaufane odczyty gazomierza,
- dla importu PDF: wymaganie `pypdf` jest instalowane z `manifest.json`.

Jeżeli `recorder:` korzysta z `include:`, wybrane sensory CO i CWU muszą znajdować się na liście dozwolonych encji.

`duon_gaz:canonical_gas` jest statystyką zewnętrzną, a nie stanem encji, dlatego nie trzeba dodawać jej do `recorder.include.entities`.

## Instalacja ręczna

Skopiuj katalog:

```text
custom_components/duon_gaz
```

do:

```text
/config/custom_components/duon_gaz
```

i uruchom ponownie Home Assistanta.

Następnie:

**Ustawienia → Urządzenia i usługi → Dodaj integrację → DUON Gaz**

## Outlook / Microsoft Graph

Automatyczny import korzysta z Device Code Flow dla publicznego klienta Microsoft.

Zakres dostępu:

```text
offline_access Mail.Read
```

Integracja nie wymaga `client_secret`, nie prosi o `Mail.ReadWrite`, nie wysyła wiadomości, nie przenosi ich i nie usuwa.

Niezabezpieczone PDF-y informacyjne są ignorowane przez importer faktur. Zaszyfrowany PDF, którego parser nie potrafi bezpiecznie zweryfikować, blokuje całą nową paczkę.

Ręczna synchronizacja jest dostępna jako:

```text
duon_gaz.sync_outlook
```

## Priorytet źródeł danych

1. dokładny ręczny fizyczny odczyt gazomierza,
2. zaufane wskazanie gazomierza z faktury,
3. statystyki CO/CWU z Recorder jako profil zużycia,
4. dane rozliczeniowe z faktur,
5. bieżące szacunki po najnowszej fizycznej kotwicy.

Kotwice fakturowe są domyślnie wyłączone z uczenia kalibracji CO/CWU. Zgodny ręczny odczyt ma pierwszeństwo przed kotwicą z faktury.

## Bezpieczeństwo danych historycznych

DUON Gaz korzysta z oficjalnych interfejsów Home Assistant Recorder. Nie zapisuje bezpośrednio do SQL i nie nadpisuje oryginalnych statystyk CO/CWU.

Historia kanoniczna jest przechowywana pod własnym identyfikatorem:

```text
duon_gaz:canonical_gas
```

## Stan walidacji 0.3.4

Na działającej instalacji potwierdzono m.in.:

- pełną synchronizację historycznych wiadomości Outlook,
- parser v3 na rzeczywistych starszych i nowszych fakturach,
- poprawne pomijanie niezabezpieczonych dokumentów informacyjnych,
- fail-closed bez częściowego importu,
- atomowy zapis paczki i `invoice_import_guard: ok`,
- brak wpływu kotwic fakturowych na istniejącą kalibrację,
- dokładne domknięcie historii kanonicznej do fizycznego gazomierza,
- brak ujemnego zużycia i nierozliczonych rollbacków,
- zweryfikowaną publikację historii kanonicznej do Recorder.

## Dalszy rozwój

Po wydaniu 0.3.4 rozwijane są osobno:

- kanoniczne statystyki CO i CWU,
- kanoniczne statystyki kosztowe,
- finalna konfiguracja Dashboardu Energii,
- SMS.
