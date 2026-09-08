# Status rozwoju DUON Gaz

Dokument jest technicznym punktem odniesienia dla dalszych prac nad integracją. Opisuje aktualną architekturę i decyzje obowiązujące w stabilnym wydaniu **0.4.2** na gałęzi `main`.

Aktualny etap: **0.4.2 — historia kanoniczna gazu, rozdział CO/CWU, koszty fakturowe, audyt współczynnika konwersji i historyczny wykres audytu w Recorderze**.

> [!IMPORTANT]
> Starsze odniesienia do gałęzi `feature/store-v2-recorder-sums`, PR #1 i etapu 0.3.4 są historyczne. Stabilnym punktem bazowym dalszego rozwoju jest obecnie `main` w wersji 0.4.2. Dokumentacja wydań pozostaje rozdzielona w `RELEASE_NOTES_0.3.4.md`, `RELEASE_NOTES_0.4.0.md`, `RELEASE_NOTES_0.4.1.md` i `RELEASE_NOTES_0.4.2.md`.

## Niezmienne zasady architektury

DUON Gaz rozdziela źródła danych i nie miesza ich ról:

1. **dokładne ręczne odczyty gazomierza** — nadrzędne fizyczne kotwice,
2. **zaufane odczyty z faktur** — kotwice o niższej precyzji czasu,
3. **godzinowe skumulowane statystyki CO/CWU z Recorder** — profil zużycia potrzebny do rekonstrukcji,
4. **dane rozliczeniowe z faktur** — okresy, współczynnik konwersji, stawki, VAT i kwoty brutto,
5. **bieżąca konfiguracja taryfowa** — wyłącznie do prowizorycznej wyceny okresu po ostatniej zamkniętej fakturze.

Surowe statystyki źródłowe CO/CWU są traktowane jako dane wejściowe i **nigdy nie są modyfikowane**. Integracja nie zapisuje bezpośrednio do SQL. Publikacja korzysta z publicznych mechanizmów Home Assistant Recorder.

## Historia kanoniczna gazu

Podstawową statystyką audytową całego zużycia pozostaje:

```text
duon_gaz:canonical_gas
```

Z tej samej historii wyprowadzane są osobne statystyki przeznaczone do rozdzielenia zużycia:

```text
duon_gaz:canonical_heating
duon_gaz:canonical_dhw
```

Algorytm historii:

- waliduje kotwice i ich monotoniczność,
- wylicza przyrosty CO i CWU ze skumulowanych statystyk `sum`,
- obsługuje korekty i rollbacki źródła przez wycofanie wcześniejszego nadmiaru zamiast tworzenia ujemnego zużycia,
- rekonstruuje brakujące godziny z lokalnego profilu historycznego,
- przelicza CO i CWU osobnymi współczynnikami m³/kWh,
- domyka każdy rozliczony przedział dokładnie do fizycznej różnicy gazomierza,
- zachowuje rozdział CO/CWU,
- buduje prowizoryczny ogon po ostatniej fizycznej kotwicy,
- scala część rozliczoną i ogon w jedną spójną serię,
- odświeża ogon po pojawieniu się nowych godzinowych statystyk Recorder,
- wykonuje pełny rebuild, gdy zmieni się zestaw aktywnych kotwic, źródła CO/CWU, kalibracja, dane fakturowe albo konfiguracja kosztowa.

Publikacja defensywnie sprawdza zgodność:

```text
CO + CWU = canonical_gas
```

Jeżeli gazu nie można jednoznacznie przypisać do CO lub CWU, publikacja rozdzielonej historii jest blokowana zamiast zgadywać.

### Fingerprint aktywnych kotwic

Publikacja przechowuje odcisk całego aktywnego zestawu kotwic. Dodanie, usunięcie lub zmiana kotwicy wewnątrz już rozliczonej historii wymusza pełną przebudowę, nawet jeżeli ostatnia kotwica i współczynniki kalibracji nie zmieniły się.

## Kalibracja CO/CWU

Kalibracja jest lokalna dla instalacji i nie zawiera publicznie zakodowanych wartości konkretnego kotła lub domu.

Po zgromadzeniu wystarczającej liczby zaufanych przedziałów integracja wyznacza osobne współczynniki CO i CWU metodą odpornej regresji dwóch składowych.

### Reguły dla kotwic fakturowych

Odczyt na fakturze ma zwykle dokładność dnia, a nie dokładnego czasu fizycznego odczytu. Dlatego:

- może być zaufaną kotwicą historii, jeżeli literalny typ odczytu jest `Rozliczeniowy`,
- jest domyślnie **wykluczony z uczenia kalibracji CO/CWU**,
- zgodny ręczny odczyt ma pierwszeństwo i może przesłonić kotwicę fakturową,
- kotwica niemonotoniczna pozostaje zachowana audytowo, ale nie uczestniczy w estymacji,
- historyczna kotwica bez lokalnego snapshotu Recorder jest dopuszczalna tylko wtedy, gdy nie uczestniczy w kalibracji i istnieje późniejsza zaufana kotwica,
- najnowsza baza bieżącej estymacji nadal wymaga danych Recorder.

Kotwica wykluczona z kalibracji nie rozcina poprawnego ręcznego przedziału kalibracyjnego.

## Faktury PDF

Parser używa `pypdf`, bez OCR. Dane są walidowane przed zapisem, między innymi przez sprawdzanie:

- zgodności różnicy wskazań gazomierza ze zużyciem m³,
- zgodności m³ w pozycji gazowej z tabelą odczytów,
- zgodności energii rozliczeniowej ze współczynnikiem konwersji,
- zgodności energii pozycji dystrybucyjnych z energią rozliczeniową,
- zgodności współczynnika konwersji pomiędzy wieloma pozycjami gazowymi.

Parser obsługuje zweryfikowane starsze i nowsze warianty faktur, w tym wiele pozycji i stawek w okresie. Przy kilku stawkach wyliczana jest efektywna stawka ważona. Współczynnik konwersji nie jest arbitralnie uśredniany — niespójność dokumentu powoduje błąd fail-closed.

Ponowny import tej samej faktury jest idempotentny. Nowy format dokumentu powinien być akceptowany dopiero po dodaniu jawnego testu regresyjnego.

## Automatyczny Outlook / Microsoft Graph

Automatyczny import korzysta z Microsoft Graph wyłącznie do odczytu wiadomości i załączników.

Uwierzytelnianie:

- Device Code Flow dla publicznego klienta,
- delegowane `Mail.Read`,
- zakres `offline_access Mail.Read`,
- brak `client_secret`,
- brak `Mail.ReadWrite`,
- brak wysyłania, przenoszenia i usuwania wiadomości.

Access token i refresh token są przechowywane w danych wpisu konfiguracji Home Assistanta. Integracja obsługuje automatyczne odświeżanie tokenu oraz reautoryzację przez UI.

Folder, nadawca i temat są konfigurowalne. Publiczny kod nie powinien zawierać identyfikatorów folderów ani danych konkretnej instalacji.

### Hasło do PDF

Hasło do zaszyfrowanych faktur:

- jest maskowane w formularzu,
- nie trafia do DUON Store,
- nie powinno trafiać do logów ani diagnostyki,
- nie jest dodatkowo szyfrowane przez integrację na dysku; jego ochrona opiera się na zabezpieczeniu konfiguracji Home Assistanta.

Niezabezpieczone PDF-y informacyjne są ignorowane przez importer faktur i nie powodują częściowego sukcesu ani błędu paczki.

## Fail-closed i atomowość importu

Przed zatwierdzeniem nowej paczki faktur wykonywany jest pełny preflight. Jeżeli choć jeden nowy zaszyfrowany dokument nie przejdzie parsera lub walidacji:

- nowe faktury z tej synchronizacji nie są częściowo zatwierdzane,
- kotwice historii nie są zmieniane,
- kalibracja nie jest przeliczana,
- historia kanoniczna nie jest przebudowywana na podstawie częściowej paczki,
- zapisywany jest tylko bezpieczny stan diagnostyczny bez treści faktury, hasła i tokenów.

Po pomyślnym preflight dane są etapowane w pamięci i zapisywane atomowo do Store. Po zapisie następuje ponowny odczyt i weryfikacja. Niepotwierdzony zapis powoduje wycofanie stanu w pamięci i status blokady.

## Warstwa kosztowa — od 0.4.0

Koszty są publikowane jako osobne zewnętrzne statystyki Recorder:

```text
duon_gaz:canonical_heating_cost
duon_gaz:canonical_dhw_cost
duon_gaz:canonical_fixed_cost
```

Dodatkowo istnieje zerowy nośnik objętości dla kosztów stałych:

```text
duon_gaz:canonical_fixed_cost_gas
```

Jego zużycie wynosi `0 m³`; służy wyłącznie do przypięcia kosztu stałego jako osobnego źródła w Energy Dashboard.

### Granice księgowania kosztów

Daty odczytów gazomierza **nie wyznaczają granic kosztowych**. Autorytatywne jest literalne pole `Za okres` z faktury:

- początek: `period_start 00:00` czasu lokalnego,
- koniec: początek dnia następującego po `period_end`,
- rzeczywista liczba godzin jest liczona w UTC po zbudowaniu lokalnych granic, dzięki czemu DST jest obsługiwany poprawnie.

Dla pełnego okresu fakturowego autorytatywna jest kwota brutto faktury. Koszt zmienny jest dzielony pomiędzy CO i CWU według kanonicznego zużycia w tym samym okresie, a pozostała część trafia do kosztów stałych / pozostałych opłat.

Każdy zastosowany pełny okres kosztowy musi domknąć się do brutto faktury przed publikacyjnym zaokrągleniem rekordów Recorder. Nakładające się okresy blokują publikację fail-closed. Częściowo pokryta historyczna faktura nie jest sztucznie ekstrapolowana.

### Prowizoryczny ogon kosztów

Po ostatnim zamkniętym okresie fakturowym koszt jest szacowany z bieżącej konfiguracji taryfowej. Zmiana danych fakturowych lub taryfowych wpływających na historię wymusza pełny rebuild; zwykłe pojawienie się nowych godzin może odświeżyć tylko ogon.

## Energy Dashboard

Docelowy rozdzielony model źródeł gazu jest następujący:

- CO: `duon_gaz:canonical_heating` + `duon_gaz:canonical_heating_cost`,
- CWU: `duon_gaz:canonical_dhw` + `duon_gaz:canonical_dhw_cost`,
- koszty stałe: `duon_gaz:canonical_fixed_cost_gas` + `duon_gaz:canonical_fixed_cost`.

`duon_gaz:canonical_gas` pozostaje statystyką całkowitą i audytową. Nie należy dodawać jej równolegle jako kolejnego źródła gazu, jeżeli Energy Dashboard korzysta już z rozdziału CO/CWU, ponieważ spowodowałoby to podwójne liczenie zużycia.

Warstwa danych potrzebna do Energy Dashboard jest gotowa i zweryfikowana. Finalna konfiguracja samego Dashboardu na działającej instalacji pozostaje osobnym etapem operacyjnym.

## Audyt współczynnika konwersji — od 0.4.1

Encja **Audyt współczynnika konwersji** jest wyłącznie diagnostyczna. Nie wpływa na:

- historię kanoniczną,
- kalibrację CO/CWU,
- rozliczenia fakturowe,
- statystyki kosztowe,
- Energy Dashboard.

Audyt służy do wykrywania długoterminowego dryfu relacji pomiędzy współczynnikiem kWh/m³ z faktur DUON a lokalnym profilem Ariston + gazomierz.

Metoda:

- używa wyłącznie okresów, których obie granice można powiązać z dokładnymi ręcznymi odczytami gazomierza,
- nie używa arbitralnej godziny dla odczytów znanych tylko z dokładnością do dnia,
- korzysta z istniejącej niezależnej kalibracji CO/CWU,
- wykorzystuje relację `provisional_m3 / physical_m3` z kanonicznych przedziałów przed normalizacją do gazomierza,
- wyznacza stałą historyczną referencję kWh/m³ jako medianę ważoną zużyciem,
- okresy bez dwóch dokładnych granic pomija zamiast zgadywać.

Stan sensora jest skumulowanym odchyleniem kosztu zmiennego w PLN liczonym z perspektywy użytkownika:

- wartość dodatnia — wynik korzystniejszy dla użytkownika niż lokalna referencja,
- wartość ujemna — wynik mniej korzystny dla użytkownika niż lokalna referencja.

Audyt nie jest laboratoryjnym pomiarem ciepła spalania ani dowodem błędnego rozliczenia. Bez niezależnego kalorymetru wykrywa odchylenie i dryf względem lokalnej historii, a nie bezwzględny błąd dostawcy.

## Historyczny wykres audytu — od 0.4.2

0.4.2 publikuje prawdziwy historyczny przebieg audytu również do długoterminowych statystyk Recorder pod `statistic_id` równym bieżącemu `entity_id` sensora.

Zasady backfillu:

- źródłem jest wyłącznie historia wygenerowana przez finalny algorytm audytu,
- pierwszy punkt to `0 PLN` na początku pierwszego ocenianego okresu,
- każdy kolejny punkt zawiera rzeczywiste skumulowane saldo po zakończeniu ocenianego okresu,
- publikacja korzysta z `async_import_statistics`, bez bezpośredniego SQL,
- błąd backfillu nie powoduje niedostępności samej encji audytu.

Dzięki temu wbudowany wykres Home Assistanta może pokazywać historyczne fluktuacje audytu od początku wiarygodnej historii, zamiast tylko zmian stanu od momentu utworzenia encji.

## Stan testów 0.4.2

Aktualny CI:

- kompiluje cały katalog `custom_components/duon_gaz`,
- uruchamia `python -m unittest discover -s tests -v`,
- zawiera obecnie **38 testów jednostkowych** i przechodzi w całości.

Zakres testów obejmuje między innymi:

- historię kanoniczną, brakujące godziny i rollbacki,
- dokładne domknięcie przedziałów do gazomierza,
- rozdział CO/CWU,
- scalenie granicznych godzin i odświeżanie ogona,
- księgowanie kosztów według literalnego okresu faktury,
- DST,
- dokładne domknięcie brutto,
- nakładające się faktury fail-closed,
- parser starszych i nowszych faktur,
- atomowy import Outlook i rollback przy niepotwierdzonym Store,
- reguły kotwic i kalibracji,
- fingerprint aktywnych kotwic,
- dokładne ręczne granice audytu współczynnika konwersji,
- pomijanie niepewnych okresów audytu,
- historyczny backfill salda audytu i kolizje punktów w tej samej godzinie.

## Zweryfikowane na działającej instalacji

Potwierdzono między innymi:

- synchronizację i reautoryzację Outlook przez UI,
- parser rzeczywistych wariantów faktur,
- fail-closed bez częściowego zapisu,
- atomowy zapis Store,
- klasyfikację i priorytet kotwic,
- brak udziału kotwic fakturowych w uczeniu kalibracji,
- pełny rebuild po zmianie zestawu aktywnych kotwic,
- publikację i weryfikację historii gazu, CO i CWU,
- dokładne domknięcie CO + CWU do całkowitego gazu,
- brak ujemnego zużycia i nierozliczonych rollbacków,
- zachowanie surowych statystyk źródłowych bez modyfikacji,
- publikację kosztów i domknięcie pełnych okresów do kwot brutto faktur,
- poprawne zachowanie częściowo pokrytej historii bez sztucznej ekstrapolacji,
- działanie przyrostowego odświeżania prowizorycznego ogona,
- audyt współczynnika konwersji oparty na dokładnych ręcznych granicach,
- poprawną konwencję znaku audytu z perspektywy użytkownika,
- historyczny wykres audytu z rzeczywistymi punktami zamiast wartości z testowych wersji developerskich.

## Historia wydań będących punktami architektonicznymi

- **0.3.4** — stabilizacja historii kanonicznej, parsera faktur oraz automatycznego importu Outlook / Graph,
- **0.4.0** — rozdział CO/CWU i pełna warstwa kosztowa przeznaczona do Energy Dashboard,
- **0.4.1** — diagnostyczny audyt współczynnika konwersji względem lokalnej relacji Ariston + gazomierz,
- **0.4.2** — historyczny backfill audytu do długoterminowych statystyk Recorder.

## Dalszy rozwój

Najbliższe osobne etapy po 0.4.2:

1. finalna konfiguracja i kontrola Energy Dashboard na działającej instalacji,
2. SMS jako osobna funkcja, bez mieszania jej z warstwą kanoniczną i rozliczeniową.

Nowe prace powinny wychodzić z aktualnego `main` na osobnych gałęziach i przechodzić przez CI przed scaleniem.

## Zasady bezpieczeństwa dalszych prac

- żadnych bezpośrednich zapisów SQL,
- nie modyfikować surowych statystyk CO/CWU,
- nie publikować danych konkretnej instalacji: identyfikatorów punktu odbioru, prywatnych identyfikatorów encji, odczytów, numerów faktur ani innych danych użytkownika,
- nie logować hasła PDF, access tokenu ani refresh tokenu,
- nie akceptować nowego formatu faktury przez nadmiernie szerokie regexy bez testu regresyjnego,
- zachować fail-closed tam, gdzie częściowy zapis mógłby uszkodzić spójność historii lub rozliczeń,
- używać publicznych API Home Assistanta zamiast bezpośrednich modyfikacji bazy,
- ograniczać restarty Home Assistanta i grupować zmiany przed restartem,
- zachować polskie komunikaty, dokumentację i opisy UI.
