# DUON Gaz 0.5.1

## Zakres wydania

0.5.1 dopracowuje bezpieczeństwo obsługi ręcznego odczytu gazomierza i przygotowania SMS wprowadzonego w 0.5.0. Nie zmienia historii kanonicznej, kalibracji CO/CWU, kosztów ani Energy Dashboard.

## Puste pole jest ignorowane

Naciśnięcie **Zapisz i wyślij SMS** przy pustym polu `Stan gazomierza` kończy się bez działania:

- nie powstaje kotwica,
- nie jest przygotowywany SMS,
- nie jest wybierany telefon,
- Home Assistant nie zgłasza błędu użytkownikowi.

## Automatyczne czyszczenie pola po przygotowaniu SMS

Po poprawnym wywołaniu systemowego edytora SMS integracja nie pozostawia starego odczytu w polu bezterminowo.

Ze względu na zachowanie Androida przy pierwszym użyciu `command_activity` wartość nie jest czyszczona natychmiast po pierwszym wywołaniu. Android może wtedy pokazać ekran uprawnienia „wyświetlanie nad innymi aplikacjami” zamiast edytora SMS, mimo że Home Assistant uzna wywołanie za wykonane.

Dlatego:

- po pierwszym poprawnym wywołaniu edytora wartość pozostaje przez istniejące 5-minutowe okno technicznego retry,
- po upływie 5 minut pole `Stan gazomierza` jest automatycznie czyszczone,
- jeżeli użytkownik wykona retry w tym oknie i ta sama kotwica zostanie ponownie użyta, pole jest czyszczone od razu po drugim poprawnym wywołaniu,
- termin czyszczenia jest zapisywany w Store i odtwarzany po restarcie Home Assistanta,
- wpisanie nowej wartości anuluje oczekujące czyszczenie, aby stary timer nie mógł usunąć świeżego odczytu.

Czyszczenie dotyczy wyłącznie edytowalnego pola oczekującego odczytu. Zapisane ręczne kotwice i ich dane audytowe pozostają bez zmian.

## Potwierdzenie przed przypadkowym kliknięciem

Dialog potwierdzenia jest funkcją Lovelace, a nie właściwością backendowej `ButtonEntity`. 0.5.1 dokumentuje zalecane skonfigurowanie `confirmation` na karcie/wierszu dashboardu uruchamiającym `button.press`.

Przykład:

```yaml
type: button
entity: button.TWOJ_PRZYCISK_DUON
name: Zapisz i wyślij SMS
tap_action:
  action: perform-action
  perform_action: button.press
  target:
    entity_id: button.TWOJ_PRZYCISK_DUON
  confirmation:
    title: Potwierdź odczyt
    text: Zapisać stan gazomierza i przygotować SMS?
    confirm_text: Wyślij
    dismiss_text: Anuluj
```

Takie potwierdzenie chroni przed przypadkowym tapnięciem podczas przewijania interfejsu. Bezpośrednie wywołanie `button.press` poza kartą Lovelace nie korzysta z tego dialogu.

## Zachowane reguły 0.5.0

- dokładny ręczny odczyt pozostaje fizyczną kotwicą,
- wartość SMS jest zaokrąglana `ROUND_HALF_UP`,
- SMS ma format `<pełne m³> <numer licznika>`,
- `context.user_id` osoby naciskającej przycisk wybiera właściwy Android,
- szybki identyczny odczyt w ciągu 5 minut jest retry i nie tworzy duplikatu,
- identyczny stan po 5 minutach może być nową rzeczywistą kotwicą,
- integracja tylko otwiera edytor SMS; użytkownik nadal sam zatwierdza wysłanie.
