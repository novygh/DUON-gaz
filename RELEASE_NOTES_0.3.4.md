# DUON Gaz 0.3.4

Wydanie 0.3.4 jest stabilnym snapshotem integracji zweryfikowanym na działającej instalacji Home Assistant 2026.9.1.

## Najważniejsze zmiany

### Historia kanoniczna gazu

- dodano zewnętrzną statystykę Recorder `duon_gaz:canonical_gas`,
- historia jest budowana z fizycznych odczytów gazomierza oraz godzinowych statystyk Ariston CO/CWU,
- brakujące godziny Recorder są rekonstruowane bez modyfikowania surowych statystyk źródłowych,
- historia jest normalizowana dokładnie do kolejnych fizycznych odczytów gazomierza,
- suma narastająca pozostaje monotoniczna, bez ujemnego zużycia godzinowego,
- obsługiwane są rollbacki i korekty źródłowych sum energii,
- bieżący nierozliczony ogon jest odświeżany przyrostowo,
- zmiana zestawu aktywnych kotwic wymusza pełną przebudowę historii dzięki fingerprintowi kotwic.

### Kalibracja CO/CWU

- rozdzielono współczynniki m3/kWh dla CO i CWU,
- kalibracja używa wyłącznie przeznaczonych do tego fizycznych odczytów,
- kotwice pochodzące z faktur są domyślnie wyłączone z uczenia kalibracji,
- wykluczona kotwica fakturowa nie rozcina przedziału manualny -> manualny.

### Faktury DUON PDF

- import wykorzystuje `pypdf`, bez OCR,
- parser v3 obsługuje zarówno nowsze, jak i starsze układy faktur,
- obsługiwane są wielokrotne pozycje oraz zmiany stawek wewnątrz okresu rozliczeniowego,
- poprawiono polski zapis separatora tysięcy w energii, np. `1.496 KWH` = `1496 kWh`,
- walidowane są wskazania gazomierza, m3, energia oraz współczynnik konwersji,
- import jest idempotentny po numerze faktury,
- ręczny odczyt gazomierza ma pierwszeństwo nad zgodną kotwicą fakturową,
- historyczna kotwica fakturowa może być zachowana bez snapshotu Recorder tylko wtedy, gdy istnieje późniejsza zaufana kotwica i punkt nie wpływa na kalibrację.

### Outlook / Microsoft Graph

- automatyczny import faktur z wybranego folderu Outlook,
- Device Code Flow dla publicznego klienta Microsoft,
- zakres uprawnień ograniczony do `offline_access Mail.Read`,
- brak `client_secret`, `Mail.ReadWrite`, wysyłania, przenoszenia i usuwania wiadomości,
- niezabezpieczone dokumenty informacyjne PDF są ignorowane przez importer faktur,
- obsługiwane jest automatyczne odświeżanie tokenu Microsoft,
- synchronizacja może być uruchamiana ręcznie oraz według harmonogramu.

### Fail-closed i atomowość

- wszystkie nowe zaszyfrowane PDF-y przechodzą preflight przed rozpoczęciem zatwierdzania paczki,
- błąd pojedynczej faktury blokuje całą nową paczkę bez częściowego importu,
- importy są etapowane w pamięci,
- Store korzysta z `atomic_writes=True`,
- po zapisie synchronizator ponownie odczytuje Store i porównuje go z zamierzonym stanem,
- niepotwierdzony zapis jest traktowany jako błąd i powoduje wycofanie etapowanej paczki.

## Zweryfikowane na działającej instalacji

W trakcie walidacji 0.3.4 potwierdzono m.in.:

- pełną synchronizację 34 historycznych wiadomości Outlook,
- 34 zapisane okresy rozliczeniowe i 34 przetworzone faktury,
- parser v3 na rzeczywistych starszych i nowszych fakturach,
- poprawne pomijanie niezabezpieczonych dokumentów informacyjnych,
- poprawne działanie fail-closed bez częściowego zapisu,
- `invoice_import_guard: ok`,
- 34 kotwice fakturowe wyłączone z kalibracji,
- niezmienność istniejącej kalibracji po pełnym imporcie faktur,
- 41 aktywnych fizycznych kotwic, z czego 38 mieści się w dostępnym zakresie Recorder,
- dokładne domknięcie historii: `physical_total_m3 == canonical_total_m3`,
- `closure_error_m3 = 0.0`,
- brak nierozliczonych rollbacków w historii rozliczonej i bieżącym ogonie,
- zweryfikowaną publikację historii kanonicznej do Recorder.

## Testy

Dla snapshotu wydania działa zestaw CI obejmujący historię kanoniczną, parser faktur, fail-closed Outlook, atomowość Store, kalibrację, historyczne kotwice bez Recorder oraz fingerprint zestawu kotwic publikacji.

## Poza zakresem 0.3.4

Do tego wydania nie należą późniejsze zmiany rozwijane po zweryfikowanym snapshotcie, w szczególności:

- osobne kanoniczne statystyki CO i CWU,
- kanoniczne statystyki kosztowe,
- finalna konfiguracja Dashboardu Energii,
- SMS.

Te elementy są rozwijane osobno po ustabilizowaniu 0.3.4.
