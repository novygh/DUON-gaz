# DUON Gaz 0.4.2

## Historyczny wykres audytu współczynnika konwersji

Wydanie 0.4.2 uzupełnia encję **Audyt współczynnika konwersji** o prawdziwą historię długoterminową w Recorderze.

Wbudowany wykres Home Assistant może teraz pokazywać rzeczywiste fluktuacje salda audytu od pierwszego wiarygodnego okresu historycznego, zamiast wyłącznie zmian bieżącego stanu encji.

## Publikacja historii

- źródłem backfillu jest wyłącznie historia wygenerowana przez finalny algorytm audytu,
- pierwszy punkt jest publikowany jako `0 PLN` na początku pierwszego ocenianego okresu,
- każdy kolejny punkt zawiera rzeczywiste skumulowane saldo audytu,
- publikacja używa publicznego API Recorder `async_import_statistics`, bez bezpośredniego SQL,
- statystyka jest zapisywana pod `statistic_id` równym bieżącemu `entity_id` sensora,
- błąd publikacji historii nie powoduje niedostępności samej encji audytu.

## Walidacja na działającej instalacji

Przed wydaniem stabilnym potwierdzono:

- 15 rzeczywistych punktów historycznych audytu oraz dodatkowy punkt startowy `0 PLN`,
- `historia_recorder_punkty: 16`,
- `historia_recorder_blad: None`,
- poprawne odtworzenie przebiegu od 2024 r. do bieżącego salda,
- usunięcie sztucznych skoków powstałych podczas testów wersji developerskich,
- brak błędów DUON/Recorder w logu.

## Bez zmian w rozliczeniach

0.4.2 nie zmienia algorytmu audytu ani żadnej warstwy rozliczeniowej. Nie wpływa na historię kanoniczną, kalibrację CO/CWU, koszty ani Energy Dashboard.
