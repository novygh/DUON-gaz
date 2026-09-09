# Status rozwoju DUON Gaz

Dokument jest technicznym punktem odniesienia dla dalszych prac nad integracją. Opisuje architekturę i decyzje obowiązujące w stabilnym wydaniu **0.5.0**.

Aktualny etap: **0.5.0 — stabilna historia kanoniczna gazu, rozdział CO/CWU, koszty fakturowe, audyt współczynnika konwersji, Energy Dashboard, automatyczny Outlook oraz przygotowanie SMS z ręcznym odczytem gazomierza**.

> [!IMPORTANT]
> Stabilnym punktem bazowym dalszego rozwoju jest `main` w wersji 0.5.0. Starsze PR-y i gałęzie developerskie mają znaczenie wyłącznie historyczne. Dokumentacja wydań pozostaje rozdzielona w `RELEASE_NOTES_0.3.4.md`, `RELEASE_NOTES_0.4.0.md`, `RELEASE_NOTES_0.4.1.md`, `RELEASE_NOTES_0.4.2.md` i `RELEASE_NOTES_0.5.0.md`.

## Niezmienne zasady architektury

DUON Gaz rozdziela źródła danych i nie miesza ich ról:

1. **dokładne ręczne odczyty gazomierza** — nadrzędne fizyczne kotwice,
2. **zaufane odczyty z faktur** — kotwice o niższej precyzji czasu,
3. **godzinowe skumulowane statystyki CO/CWU z Recorder** — profil zużycia potrzebny do rekonstrukcji,
4. **dane rozliczeniowe z faktur** — okresy, współczynnik konwersji, stawki, VAT i kwoty brutto,
5. **bieżąca konfiguracja taryfowa** — wyłącznie do prowizorycznej wyceny okresu po ostatniej zamkniętej fakturze.

Surowe statystyki źródłowe CO/CWU są danymi wejściowymi i **nigdy nie są modyfikowane**. Integracja nie zapisuje bezpośrednio do SQL. Publikacja korzysta z publicznych mechanizmów Home Assistant Recorder.

## Historia kanoniczna gazu

Podstawowa statystyka audytowa całego zużycia:

```text
duon_gaz:canonical_gas
```

Rozdzielone statystyki zużycia:

```text
duon_gaz:canonical_heating
duon_gaz:canonical_dhw
```

Algorytm:

- waliduje kotwice i ich monotoniczność,
- wylicza przyrosty CO/CWU ze skumulowanych statystyk `sum`,
- obsługuje rollbacki przez wycofanie wcześniejszego nadmiaru zamiast tworzenia ujemnego zużycia,
- rekonstruuje brakujące godziny z lokalnego profilu historycznego,
- przelicza CO i CWU osobnymi współczynnikami m³/kWh,
- domyka każdy rozliczony przedział dokładnie do fizycznej różnicy gazomierza,
- zachowuje rozdział CO/CWU,
- buduje prowizoryczny ogon po ostatniej fizycznej kotwicy,
- scala część rozliczoną i ogon w jedną spójną serię,
- odświeża ogon po nowych godzinowych statystykach Recorder,
- wykonuje pełny rebuild po zmianie aktywnych kotwic, źródeł CO/CWU, kalibracji, faktur lub konfiguracji kosztowej.

Publikacja defensywnie wymaga:

```text
CO + CWU = canonical_gas
```

Nieprzypisany gaz blokuje publikację rozdzielonej historii zamiast uruchamiać heurystykę.

### Fingerprint aktywnych kotwic

Publikacja przechowuje odcisk całego aktywnego zestawu kotwic. Dodanie, usunięcie lub zmiana kotwicy wewnątrz istniejącej historii wymusza pełną przebudowę nawet wtedy, gdy ostatnia kotwica i współczynniki kalibracji pozostają bez zmian.

## Kalibracja CO/CWU

Kalibracja jest lokalna dla instalacji. Po zgromadzeniu wystarczającej liczby zaufanych przedziałów integracja wyznacza osobne współczynniki CO i CWU metodą odpornej regresji dwóch składowych.

Reguły kotwic fakturowych:

- tylko literalny typ `Rozliczeniowy` może być zaufaną kotwicą historii,
- kotwice fakturowe są domyślnie wykluczone z uczenia kalibracji,
- zgodny dokładny odczyt ręczny ma pierwszeństwo (`shadowed_by_manual`),
- kotwice niemonotoniczne są zachowywane audytowo, ale wyłączane z estymacji,
- historyczna kotwica bez lokalnego snapshotu Recorder jest dopuszczalna tylko jako niekalibracyjna i przy istnieniu późniejszej zaufanej kotwicy,
- najnowsza baza bieżącej estymacji nadal wymaga danych Recorder.

Kotwica wykluczona z kalibracji nie rozcina poprawnego ręcznego przedziału kalibracyjnego.

## Faktury PDF

Parser używa `pypdf`, bez OCR. Waliduje między innymi:

- zgodność różnicy wskazań gazomierza ze zużyciem m³,
- zgodność m³ pozycji gazowej z tabelą odczytów,
- zgodność energii rozliczeniowej ze współczynnikiem konwersji,
- zgodność energii pozycji dystrybucyjnych z energią rozliczeniową,
- zgodność współczynnika konwersji pomiędzy wieloma pozycjami gazowymi.

Obsługiwane są zweryfikowane starsze i nowsze warianty faktur oraz wiele stawek w jednym okresie. Niespójność dokumentu powoduje błąd fail-closed. Ponowny import tej samej faktury jest idempotentny.

## Automatyczny Outlook / Microsoft Graph

Automatyczny import używa Microsoft Graph wyłącznie do odczytu wiadomości i załączników.

Uwierzytelnianie:

- Device Code Flow dla publicznego klienta,
- delegowane `Mail.Read`,
- zakres `offline_access Mail.Read`,
- brak `client_secret`,
- brak `Mail.ReadWrite`,
- brak wysyłania, przenoszenia i usuwania wiadomości.

Access token i refresh token są przechowywane w danych wpisu konfiguracji HA. Integracja obsługuje odświeżanie tokenu i reautoryzację przez UI.

Niezabezpieczone PDF-y informacyjne są ignorowane. Zaszyfrowany dokument, którego parser nie potrafi bezpiecznie zweryfikować, blokuje całą nową paczkę przed częściowym zapisem.

## Numer licznika — od 0.5.0

Konfiguracja Outlook/PDF używa jednego jawnego pola:

```text
meter_number
```

Ta sama wartość służy:

- jako hasło do zaszyfrowanych faktur PDF,
- jako identyfikator licznika w treści SMS.

Numer jest przechowywany jako tekst, aby zachować zera wiodące. Nie trafia do DUON Store ani publicznego kodu. Stare pole `invoice_pdf_password` nie jest automatycznie migrowane; po aktualizacji z 0.4.2 użytkownik wykonuje `Przekonfiguruj`, podaje numer licznika i ponownie kończy Device Code Flow Microsoft.

## SMS z odczytem — od 0.5.0

Przycisk **Zapisz i wyślij SMS** zachowuje istniejący `unique_id` przycisku zapisu odczytu.

Przebieg:

1. dokładny stan gazomierza jest zapisywany jako ręczna fizyczna kotwica,
2. wartość do SMS jest zaokrąglana do pełnych m³ metodą `ROUND_HALF_UP`,
3. treść ma format `<stan> <numer licznika>`,
4. pierwszeństwo przy identyfikacji ma `context.user_id` osoby naciskającej przycisk,
5. awaryjnie używany jest użytkownik, który wpisał stan,
6. rejestracje Mobile App są filtrowane po `user_id`, systemie Android i obecności `webhook_id`,
7. brak dopasowania albo więcej niż jedno urządzenie Android dla użytkownika zatrzymuje operację przed losowym wyborem,
8. po `webhook_id` ustalana jest dokładna usługa powiadomień Mobile App,
9. `command_activity` uruchamia `android.intent.action.SENDTO` z URI `smsto:` i przygotowaną treścią,
10. faktyczne wysłanie SMS pozostaje ręcznym działaniem użytkownika.

Pierwsze użycie może wymagać uprawnienia Android „wyświetlanie nad innymi aplikacjami”.

### Ochrona przed technicznym duplikatem

Pierwsza walidacja developerska ujawniła, że ponowne kliknięcie po ekranie nadania uprawnienia Android zapisywało drugą kotwicę o tej samej wartości.

Finalna reguła 0.5.0:

- ten sam odczyt ponowiony w ciągu **5 minut** od ostatniej kotwicy jest traktowany jako techniczne retry SMS i nie tworzy duplikatu,
- ten sam odczyt po upływie 5 minut jest normalnym nowym fizycznym odczytem i tworzy nową kotwicę,
- inna wartość zawsze jest normalnym nowym odczytem.

Dzięki temu kolejne realne odczyty o identycznym stanie gazomierza pozostają wartościowymi kotwicami, a przypadkowe szybkie ponowienie SMS nie zaśmieca historii.

## Fail-closed i atomowość importu

Przed zatwierdzeniem nowej paczki faktur wykonywany jest pełny preflight. Błąd dowolnego nowego zaszyfrowanego dokumentu blokuje całą paczkę bez częściowego importu, zmiany kotwic i przebudowy historii.

Po pomyślnym preflight dane są etapowane w pamięci i zapisywane jednym zapisem Store. Po zapisie następuje ponowny odczyt i weryfikacja; niepotwierdzony zapis powoduje rollback stanu w pamięci.

## Warstwa kosztowa — od 0.4.0

Statystyki kosztowe:

```text
duon_gaz:canonical_heating_cost
duon_gaz:canonical_dhw_cost
duon_gaz:canonical_fixed_cost
```

Zerowy nośnik kosztu stałego:

```text
duon_gaz:canonical_fixed_cost_gas
```

Daty odczytów gazomierza nie są granicami kosztowymi. Autorytatywny jest literalny okres `Za okres` z faktury, od lokalnej północy `period_start` do początku dnia po `period_end`. DST jest liczone z rzeczywistego czasu UTC.

Dla pełnego okresu autorytatywna jest kwota brutto faktury. Koszt zmienny jest dzielony między CO/CWU według kanonicznego zużycia, a pozostała część trafia do kosztów stałych/pozostałych. Pełny zastosowany okres musi domknąć się do brutto faktury przed publikacyjnym zaokrągleniem.

Prowizoryczny ogon po ostatnim zamkniętym okresie jest wyceniany z bieżącej konfiguracji taryfowej.

## Energy Dashboard

Zweryfikowany model:

- CO: `duon_gaz:canonical_heating` + `duon_gaz:canonical_heating_cost`,
- CWU: `duon_gaz:canonical_dhw` + `duon_gaz:canonical_dhw_cost`,
- koszty stałe: `duon_gaz:canonical_fixed_cost_gas` + `duon_gaz:canonical_fixed_cost`.

`canonical_fixed_cost_gas` pozostaje zerowym nośnikiem objętości. `canonical_gas` pozostaje statystyką całkowitą/audytową i nie może być dodawana jako czwarte źródło, jeżeli Dashboard używa już CO/CWU.

Finalny układ został zweryfikowany na działającej instalacji.

## Audyt współczynnika konwersji — od 0.4.1

Sensor jest wyłącznie diagnostyczny i nie wpływa na historię kanoniczną, kalibrację, rozliczenia, koszty ani Energy Dashboard.

Audyt:

- używa wyłącznie faktur z dwiema dokładnie dopasowanymi ręcznymi granicami,
- korzysta z istniejącej niezależnej kalibracji CO/CWU,
- wykorzystuje `provisional_m3 / physical_m3` przed normalizacją do gazomierza,
- wyznacza referencję kWh/m³ jako medianę ważoną zużyciem,
- pomija niepewne okresy zamiast zgadywać.

Stan sensora jest skumulowanym odchyleniem kosztu zmiennego w PLN z perspektywy użytkownika: `+` oznacza wynik korzystniejszy, `-` mniej korzystny niż lokalna referencja.

Nie jest to laboratoryjny pomiar ciepła spalania ani dowód błędnego rozliczenia.

## Historyczny wykres audytu — od 0.4.2

Przebieg audytu jest publikowany do długoterminowych statystyk Recorder przez `async_import_statistics`. Pierwszy punkt to `0 PLN` na początku pierwszego ocenianego okresu, a kolejne punkty pokazują rzeczywiste skumulowane saldo po zakończeniu ocenianych okresów.

## Zweryfikowane na działającej instalacji

Potwierdzono między innymi:

- synchronizację i reautoryzację Outlook przez UI,
- parser rzeczywistych wariantów faktur,
- fail-closed bez częściowego zapisu,
- atomowy zapis Store,
- klasyfikację i priorytet kotwic,
- brak udziału kotwic fakturowych w uczeniu kalibracji,
- pełny rebuild po zmianie zestawu aktywnych kotwic,
- publikację historii gazu, CO i CWU,
- dokładne domknięcie CO + CWU do całkowitego gazu,
- brak ujemnego zużycia i nierozliczonych rollbacków,
- zachowanie surowych statystyk źródłowych,
- publikację kosztów i domknięcie pełnych okresów do brutto faktur,
- poprawne zachowanie częściowo pokrytej historii,
- przyrostowy refresh prowizorycznego ogona,
- audyt współczynnika konwersji i jego historyczny backfill,
- finalny Energy Dashboard z trzema źródłami,
- usunięcie starego równoległego toru helperów i osieroconych statystyk,
- rekonfigurację numeru licznika 0.5.0,
- routing SMS do właściwego telefonu użytkownika,
- poprawne zaokrąglenie i treść SMS,
- ręczne zatwierdzanie wysłania,
- domknięcie estymacji po nowej ręcznej kotwicy,
- ochronę przed szybkim technicznym duplikatem kotwicy.

## Historia wydań będących punktami architektonicznymi

- **0.3.4** — stabilizacja historii kanonicznej, parsera faktur i Outlook / Graph,
- **0.4.0** — rozdział CO/CWU i pełna warstwa kosztowa,
- **0.4.1** — diagnostyczny audyt współczynnika konwersji,
- **0.4.2** — historyczny backfill audytu do Recorder,
- **0.5.0** — jawny numer licznika oraz przygotowanie SMS na właściwym Androidzie użytkownika.

## Dalszy rozwój

Nowe prace powinny wychodzić z aktualnego `main` na osobnych gałęziach i przechodzić przez CI przed scaleniem. Funkcja SMS jest od 0.5.0 częścią stabilnego zakresu i nie jest już osobnym elementem roadmapy.

## Zasady bezpieczeństwa dalszych prac

- żadnych bezpośrednich zapisów SQL,
- nie modyfikować surowych statystyk CO/CWU,
- nie publikować danych konkretnej instalacji,
- nie logować access tokenu ani refresh tokenu,
- nie akceptować nowego formatu faktury przez nadmiernie szerokie regexy bez testu regresyjnego,
- zachować fail-closed tam, gdzie częściowy zapis mógłby uszkodzić spójność historii lub rozliczeń,
- używać publicznych API Home Assistanta,
- ograniczać restarty Home Assistanta i grupować zmiany,
- zachować polskie komunikaty, dokumentację i opisy UI.
