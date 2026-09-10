# DUON Gaz 0.5.2

## Zakres wydania

0.5.2 naprawia przypadek aktualizacji z 0.5.0/0.5.1, w którym stare pole `Stan gazomierza` mogło pozostać wypełnione po restarcie mimo że odpowiadało już wcześniej przygotowanemu SMS.

Nie zmienia historii kanonicznej, kalibracji CO/CWU, kosztów, Outlook ani Energy Dashboard.

## Przyczyna

0.5.1 potrafiła odtworzyć automatyczne czyszczenie po restarcie tylko wtedy, gdy w Store istniało `pending_clear_at`. Wartości zapisane jeszcze przez 0.5.0 nie miały tego pola, więc po aktualizacji pozostawały widoczne bezterminowo.

## Poprawka

Przy starcie integracja obsługuje także starszy stan bez `pending_clear_at`:

- porównuje oczekującą wartość z ostatnią ręczną kotwicą,
- wymaga audytu `sms.status = composer_requested`,
- używa `requested_at` (awaryjnie `prepared_at`) do odtworzenia końca 5-minutowego okna retry,
- jeśli termin już minął, czyści pole od razu,
- jeśli termin jeszcze trwa, zapisuje `pending_clear_at` i kończy czyszczenie po jego upływie,
- jeśli pole zostało ponownie edytowane po przygotowaniu SMS, nie jest czyszczone,
- jeśli wartość różni się od ostatniej kotwicy SMS, nie jest czyszczona.

Mechanizm jest więc fail-closed i nie usuwa świeżego, niewysłanego odczytu.

## Zachowanie bieżące

- puste pole + `Zapisz i wyślij SMS` = brak działania,
- nowy odczyt tworzy kotwicę i otwiera edytor SMS na Androidzie bieżącego użytkownika,
- po pierwszej próbie wartość pozostaje maksymalnie 5 minut na techniczny retry,
- poprawny retry tej samej kotwicy czyści pole od razu,
- bez retry pole czyści się automatycznie po 5 minutach,
- wpisanie nowej wartości anuluje oczekujące czyszczenie.

Dialog potwierdzenia Lovelace nie jest wymagany do tego mechanizmu.
