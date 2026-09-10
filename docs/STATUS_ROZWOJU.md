# Status rozwoju DUON Gaz

Dokument jest technicznym punktem odniesienia dla dalszych prac nad integracją. Opisuje architekturę i decyzje obowiązujące w stabilnym wydaniu **0.5.2**.

Aktualny etap: **0.5.2 — stabilna historia kanoniczna gazu, rozdział CO/CWU, koszty fakturowe, audyt współczynnika konwersji, Energy Dashboard, automatyczny Outlook oraz bezpieczne przygotowanie SMS z ręcznym odczytem gazomierza**.

> [!IMPORTANT]
> Stabilnym punktem bazowym dalszego rozwoju jest `main` w wersji 0.5.2. Starsze PR-y i gałęzie developerskie mają znaczenie wyłącznie historyczne. Dokumentacja wydań pozostaje rozdzielona w plikach `RELEASE_NOTES_*.md`.

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

Algorytm waliduje monotoniczność kotwic, wylicza przyrosty CO/CWU ze statystyk `sum`, obsługuje rollbacki bez tworzenia ujemnego zużycia, rekonstruuje brakujące godziny, przelicza CO i CWU osobnymi współczynnikami m³/kWh, domyka rozliczone przedziały do fizycznego gazomierza i buduje bieżący prowizoryczny ogon.

Publikacja defensywnie wymaga:

```text
CO + CWU = canonical_gas
```

Nieprzypisany gaz blokuje publikację rozdzielonej historii zamiast uruchamiać heurystykę.

Fingerprint aktywnego zestawu kotwic wymusza pełną przebudowę po dodaniu, usunięciu lub zmianie kotwicy wewnątrz historii.

## Kalibracja CO/CWU

Kalibracja jest lokalna dla instalacji i używa odpornej regresji dwóch składowych. Do uczenia trafiają wyłącznie odpowiednie, niewykluczone fizyczne przedziały z danymi Recorder.

Reguły kotwic fakturowych:

- tylko literalny typ `Rozliczeniowy` może być zaufaną kotwicą historii,
- kotwice fakturowe są domyślnie wykluczone z uczenia kalibracji,
- zgodny dokładny odczyt ręczny ma pierwszeństwo (`shadowed_by_manual`),
- kotwice niemonotoniczne są zachowywane audytowo, ale wyłączane z estymacji,
- historyczna kotwica bez lokalnego snapshotu Recorder jest dopuszczalna tylko jako niekalibracyjna i przy istnieniu późniejszej zaufanej kotwicy.

Kotwica wykluczona z kalibracji nie rozcina poprawnego ręcznego przedziału kalibracyjnego.

## Faktury PDF i Outlook

Parser używa `pypdf`, bez OCR. Waliduje spójność wskazań, m³, energii, współczynnika konwersji i pozycji dystrybucyjnych. Niespójny dokument powoduje błąd fail-closed, a ponowny import tej samej faktury jest idempotentny.

Automatyczny import przez Microsoft Graph używa Device Code Flow dla publicznego klienta i zakresu:

```text
offline_access Mail.Read
```

Integracja nie używa `client_secret`, `Mail.ReadWrite` ani operacji wysyłania, przenoszenia czy usuwania wiadomości. Nowa paczka faktur przechodzi pełny preflight przed zapisem; błąd jednego nowego zaszyfrowanego dokumentu blokuje częściowy import.

## Numer licznika — od 0.5.0

Konfiguracja używa jednego jawnego pola:

```text
meter_number
```

Ta sama wartość służy jako hasło do zaszyfrowanych faktur PDF oraz identyfikator licznika w treści SMS. Numer jest przechowywany jako tekst, aby zachować zera wiodące. Dane konkretnej instalacji nie trafiają do publicznego kodu.

## SMS z odczytem

Przycisk **Zapisz i wyślij SMS** zachowuje istniejący `unique_id` przycisku zapisu odczytu.

Przebieg:

1. dokładny stan gazomierza jest zapisywany jako ręczna fizyczna kotwica,
2. wartość do SMS jest zaokrąglana do pełnych m³ metodą `ROUND_HALF_UP`,
3. treść ma format `<stan> <numer licznika>`,
4. pierwszeństwo przy identyfikacji ma `context.user_id` osoby naciskającej przycisk,
5. awaryjnie używany jest użytkownik, który wpisał stan,
6. rejestracje Mobile App są filtrowane po `user_id`, Androidzie i obecności `webhook_id`,
7. brak dopasowania albo więcej niż jedno urządzenie Android zatrzymuje operację,
8. po `webhook_id` ustalana jest dokładna usługa powiadomień Mobile App,
9. `command_activity` uruchamia `android.intent.action.SENDTO` z URI `smsto:` i przygotowaną treścią,
10. faktyczne wysłanie SMS pozostaje ręcznym działaniem użytkownika.

Pierwsze użycie może wymagać uprawnienia Android „wyświetlanie nad innymi aplikacjami”.

### Ochrona przed technicznym duplikatem

Ten sam odczyt ponowiony w ciągu **5 minut** od ostatniej kotwicy jest technicznym retry SMS i nie tworzy duplikatu. Ten sam odczyt po upływie 5 minut może być prawidłową nową fizyczną kotwicą, również przy zerowym zużyciu.

### Puste pole i automatyczne czyszczenie — 0.5.1/0.5.2

- puste `Stan gazomierza` + naciśnięcie przycisku = brak działania,
- po pierwszym poprawnym wywołaniu edytora SMS wartość pozostaje przez maksymalnie 5 minut na retry,
- retry tej samej kotwicy czyści pole od razu po poprawnym ponowieniu,
- bez retry pole jest czyszczone po upływie okna,
- `pending_clear_at` jest przechowywane w Store, więc restart nie gubi aktywnego timera,
- wpisanie nowej wartości anuluje oczekujące czyszczenie.

W 0.5.1 wykryto lukę migracyjną: wartość pozostawiona wcześniej przez 0.5.0 nie miała `pending_clear_at`, dlatego sam restart po aktualizacji nie mógł jej wyczyścić. **0.5.2 naprawia ten przypadek.** Przy starcie termin jest rekonstruowany wyłącznie wtedy, gdy oczekująca wartość dokładnie odpowiada ostatniej kotwicy z `sms.status = composer_requested` i pole nie zostało ponownie edytowane po przygotowaniu SMS. Mechanizm pozostaje fail-closed wobec świeżych lub niejednoznacznych wartości.

Dialog potwierdzenia Lovelace nie jest częścią wymaganego przepływu.

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

Daty odczytów gazomierza nie są granicami kosztowymi. Autorytatywny jest literalny okres `Za okres` z faktury. DST jest liczone z rzeczywistego czasu UTC. Dla pełnego okresu autorytatywna jest kwota brutto faktury, a prowizoryczny ogon po ostatnim zamkniętym okresie korzysta z bieżącej konfiguracji taryfowej.

## Energy Dashboard

Zweryfikowany model:

- CO: `duon_gaz:canonical_heating` + `duon_gaz:canonical_heating_cost`,
- CWU: `duon_gaz:canonical_dhw` + `duon_gaz:canonical_dhw_cost`,
- koszty stałe: `duon_gaz:canonical_fixed_cost_gas` + `duon_gaz:canonical_fixed_cost`.

`canonical_fixed_cost_gas` pozostaje zerowym nośnikiem objętości. `canonical_gas` pozostaje statystyką całkowitą/audytową i nie jest dodawana jako czwarte źródło, gdy Dashboard używa CO/CWU.

## Audyt współczynnika konwersji — od 0.4.1

Sensor audytu jest wyłącznie diagnostyczny. Nie wpływa na historię kanoniczną, kalibrację, rozliczenia, koszty ani Energy Dashboard. Historyczny przebieg audytu jest od 0.4.2 publikowany do długoterminowych statystyk Recorder.

Nie jest to laboratoryjny pomiar ciepła spalania ani dowód błędnego rozliczenia.

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
- zachowanie surowych statystyk źródłowych,
- publikację kosztów i finalny Energy Dashboard z trzema źródłami,
- audyt współczynnika konwersji i jego historyczny backfill,
- routing SMS do właściwego telefonu dwóch różnych użytkowników,
- poprawne zaokrąglenie i treść SMS,
- ręczne zatwierdzanie wysłania,
- ochronę przed szybkim technicznym duplikatem kotwicy.

## Historia wydań będących punktami architektonicznymi

- **0.3.4** — stabilizacja historii kanonicznej, parsera faktur i Outlook / Graph,
- **0.4.0** — rozdział CO/CWU i pełna warstwa kosztowa,
- **0.4.1** — diagnostyczny audyt współczynnika konwersji,
- **0.4.2** — historyczny backfill audytu do Recorder,
- **0.5.0** — jawny numer licznika oraz przygotowanie SMS na właściwym Androidzie użytkownika,
- **0.5.1** — puste pole i opóźnione czyszczenie z 5-minutowym retry,
- **0.5.2** — poprawka migracyjna czyszczenia starego pola pozostawionego przez 0.5.0.

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
