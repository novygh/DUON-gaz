# DUON Gaz

Niestandardowa integracja dla Home Assistanta do rekonstrukcji i bieżącego śledzenia zużycia gazu na podstawie fizycznych odczytów gazomierza oraz skumulowanych statystyk CO/CWU zapisanych w Recorder.

## Aktualny stan projektu

Aktualna wersja rozwojowa: **0.3.1**.

Prace nad linią 0.3.x są prowadzone na gałęzi:

```text
feature/store-v2-recorder-sums
```

oraz w roboczym PR #1.

Gałąź `main` nadal zawiera starszą wersję kodu do czasu zakończenia testów i scalenia bieżących zmian.

## Co potrafi wersja 0.3.1

- wybiera dowolne dwa sensory Home Assistanta jako źródła CO i CWU,
- korzysta ze skumulowanych statystyk `sum` z Recorder,
- zapisuje dokładne fizyczne odczyty gazomierza,
- osobno kalibruje CO i CWU w m³/kWh,
- rekonstruuje brakujące godziny,
- obsługuje ujemne korekty/rollbacki źródła bez tworzenia ujemnego zużycia,
- dokładnie domyka rozliczone okresy do fizycznego gazomierza,
- buduje bieżący szacowany ogon po ostatnim odczycie,
- publikuje jedną monotoniczną statystykę Recorder:

```text
duon_gaz:canonical_gas
```

- automatycznie odświeża bieżący ogon po wygenerowaniu nowych godzinowych statystyk Recorder,
- nie modyfikuje surowych statystyk źródłowych CO/CWU.

## Wymagania

- Home Assistant z włączonym Recorder,
- dwa sensory źródłowe CO/CWU posiadające statystyki `sum`,
- co najmniej dwa zaufane odczyty gazomierza do pełnej rekonstrukcji i kalibracji.

Jeżeli `recorder:` korzysta z `include:`, wybrane sensory CO i CWU muszą znajdować się na liście dozwolonych encji.

`duon_gaz:canonical_gas` jest statystyką zewnętrzną, a nie stanem encji, dlatego nie trzeba dodawać jej do `recorder.include.entities`.

## Instalacja wersji rozwojowej

Do czasu scalenia 0.3.x używaj katalogu:

```text
custom_components/duon_gaz
```

z gałęzi:

```text
feature/store-v2-recorder-sums
```

Skopiuj go do:

```text
/config/custom_components/duon_gaz
```

i uruchom ponownie Home Assistanta po wymianie plików.

Następnie:

**Ustawienia → Urządzenia i usługi → Dodaj integrację → DUON Gaz**

## Priorytet źródeł danych

1. dokładny ręczny fizyczny odczyt gazomierza,
2. zaufane wskazanie gazomierza z faktury,
3. statystyki CO/CWU z Recorder jako profil zużycia,
4. dane rozliczeniowe z faktur,
5. bieżące szacunki po najnowszej fizycznej kotwicy.

## Bezpieczeństwo danych

DUON Gaz korzysta z interfejsów Home Assistant Recorder. Nie zapisuje bezpośrednio do SQL i nie nadpisuje oryginalnych statystyk CO/CWU.

## Jeszcze do zrobienia

- automatyczne pobieranie faktur z Outlook/Microsoft Graph,
- pełna obsługa zaszyfrowanych faktur PDF,
- przygotowanie/wysyłanie SMS,
- usunięcie instalacyjnych wartości startowych kalibracji i taryf,
- finalna migracja konfiguracji Energy Dashboard,
- scalenie linii 0.3.x do `main` i stabilne wydanie HACS.
