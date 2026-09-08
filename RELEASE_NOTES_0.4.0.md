# DUON Gaz 0.4.0

Wydanie 0.4.0 rozszerza stabilną historię kanoniczną z linii 0.3.4 o rozdział zużycia i kosztów przeznaczony do wykorzystania w Home Assistant Energy Dashboard.

## Kanoniczny podział gazu

- dodano osobną statystykę `duon_gaz:canonical_heating` dla ogrzewania,
- dodano osobną statystykę `duon_gaz:canonical_dhw` dla ciepłej wody,
- CO i CWU są wyprowadzane z tej samej historii kanonicznej co `duon_gaz:canonical_gas`,
- suma CO + CWU jest defensywnie weryfikowana względem całkowitego gazu,
- publikacja zostaje zablokowana, jeżeli pojawi się gaz, którego nie da się jednoznacznie przypisać do CO lub CWU.

## Kanoniczne koszty

Dodano trzy zewnętrzne statystyki kosztowe Recorder:

- `duon_gaz:canonical_heating_cost`,
- `duon_gaz:canonical_dhw_cost`,
- `duon_gaz:canonical_fixed_cost`.

Dodatkowo `duon_gaz:canonical_fixed_cost_gas` jest zerowym nośnikiem m³ umożliwiającym przypięcie kosztów stałych jako osobnego źródła gazu w Energy Dashboard bez zwiększania zużycia.

## Koszty z faktur

- dla okresów objętych pełną fakturą autorytatywna jest kwota brutto faktury,
- koszt zmienny jest dzielony pomiędzy CO i CWU według kanonicznego zużycia w okresie rozliczeniowym,
- pozostała część brutto trafia do kosztów stałych / pozostałych opłat,
- każdy zastosowany okres domyka się dokładnie do kwoty brutto faktury przed zaokrągleniem rekordów Recorder,
- nakładające się okresy faktur blokują publikację fail-closed,
- faktura częściowo wykraczająca poza dostępną historię nie jest sztucznie ekstrapolowana.

## Granice okresu rozliczeniowego

Koszty są księgowane według literalnego pola `Za okres` z faktury (`period_start` / `period_end`), a nie według dat odczytów gazomierza.

Okres obejmuje pełne lokalne dni kalendarzowe od `period_start 00:00` do początku dnia następującego po `period_end`. Liczenie godzin odbywa się w UTC po zbudowaniu lokalnych granic, dzięki czemu prawidłowo obsługiwane są zmiany czasu DST.

Daty odczytów gazomierza nadal służą jako kotwice fizycznej historii m³, lecz nie wyznaczają granic kosztów faktury.

## Bieżący ogon kosztów

Po ostatnim zamkniętym okresie fakturowym koszt jest szacowany z bieżących ustawień integracji:

- współczynnika konwersji kWh/m³,
- ceny gazu,
- zmiennej opłaty dystrybucyjnej,
- abonamentu,
- stałej opłaty dystrybucyjnej,
- VAT.

Koszt zmienny jest naliczany osobno dla kanonicznego CO i CWU. Koszty stałe są rozkładane godzinowo z uwzględnieniem rzeczywistej liczby godzin lokalnego miesiąca.

## Publikacja i rebuild

Pełna przebudowa jest wymuszana, gdy zmieni się którekolwiek z danych wpływających na historię:

- zestaw aktywnych kotwic fizycznych,
- źródła CO/CWU,
- kalibracja,
- okresy i dane kosztowe faktur,
- bieżąca konfiguracja taryfowa,
- brak wymaganych statystyk składowych lub kosztowych.

Po zweryfikowanej pełnej publikacji zwykły `refresh_tail` aktualizuje tylko prowizoryczny ogon.

## Energy Dashboard

Home Assistant obsługuje zewnętrzną statystykę zużycia gazu wraz z osobną zewnętrzną statystyką kosztu. 0.4.0 przygotowuje następujący model:

- CO: `canonical_heating` + `canonical_heating_cost`,
- CWU: `canonical_dhw` + `canonical_dhw_cost`,
- koszty stałe: `canonical_fixed_cost_gas` + `canonical_fixed_cost`.

`canonical_gas` pozostaje statystyką całkowitą i nie powinien być dodawany równolegle jako kolejne źródło gazu, jeżeli w Energy Dashboard używany jest rozdział CO/CWU, ponieważ prowadziłoby to do podwójnego liczenia zużycia.

## Walidacja

Przed wydaniem zweryfikowano na działającej instalacji Home Assistant m.in.:

- pełną publikację wszystkich statystyk objętościowych i kosztowych,
- dokładne domknięcie CO + CWU do całkowitego gazu,
- dokładne domknięcie kosztu opublikowanego do kwot brutto zastosowanych faktur,
- miesięczne porównanie faktura vs Recorder dla wszystkich w pełni pokrytych okresów,
- pozostawienie niepełnego historycznego fragmentu bez sztucznej wyceny,
- poprawny przyrostowy refresh bieżącego ogona po pełnej publikacji,
- zachowanie istniejącej kalibracji i guardu importu Outlook.

## Kompatybilność

Kod pozostaje oparty na publicznych mechanizmach Recorder i nie modyfikuje surowych statystyk Ariston ani danych Recorder przez bezpośredni SQL.
