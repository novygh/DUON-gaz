# DUON Gaz

Niestandardowa integracja Home Assistant do rekonstrukcji i bieżącego śledzenia zużycia gazu na podstawie fizycznych odczytów gazomierza, skumulowanych statystyk CO/CWU zapisanych w Recorder oraz danych rozliczeniowych DUON.

## Aktualna wersja

**0.5.0** — stabilne wydanie dodające przygotowanie SMS z odczytem gazomierza na właściwym telefonie użytkownika oraz upraszczające konfigurację numeru licznika.

Szczegóły wydania: [`RELEASE_NOTES_0.5.0.md`](RELEASE_NOTES_0.5.0.md).

## Główne możliwości

- dowolne dwa sensory Home Assistanta jako źródła CO i CWU,
- skumulowane statystyki `sum` z Recorder jako źródło profilu zużycia,
- dokładne ręczne fizyczne odczyty gazomierza jako nadrzędne kotwice,
- osobna kalibracja CO i CWU w m³/kWh,
- rekonstrukcja brakujących godzin,
- obsługa korekt/rollbacków źródła bez tworzenia ujemnego zużycia,
- dokładne domykanie rozliczonych okresów do fizycznego gazomierza,
- bieżący szacowany ogon po ostatniej kotwicy,
- import zaszyfrowanych faktur PDF DUON przez `pypdf`, bez OCR,
- automatyczny import faktur z Microsoft Outlook / Graph przez Device Code Flow,
- atomowy import paczki faktur i fail-closed przy nierozpoznanym zaszyfrowanym PDF,
- rozdzielone statystyki CO/CWU i koszty przeznaczone do Energy Dashboard,
- diagnostyczny audyt współczynnika konwersji i jego historyczny backfill w Recorder,
- przygotowanie SMS z odczytem gazomierza na Androidzie użytkownika, który nacisnął przycisk.

## Statystyki kanoniczne

Objętość:

```text
duon_gaz:canonical_gas
duon_gaz:canonical_heating
duon_gaz:canonical_dhw
duon_gaz:canonical_fixed_cost_gas
```

Koszty:

```text
duon_gaz:canonical_heating_cost
duon_gaz:canonical_dhw_cost
duon_gaz:canonical_fixed_cost
```

`canonical_gas` pozostaje statystyką całkowitą i audytową. Jeżeli Energy Dashboard korzysta z rozdziału CO/CWU, nie należy dodawać jej jako czwartego źródła gazu, ponieważ spowodowałoby to podwójne liczenie zużycia.

## Energy Dashboard

Zweryfikowany model źródeł:

- CO: `duon_gaz:canonical_heating` + `duon_gaz:canonical_heating_cost`,
- CWU: `duon_gaz:canonical_dhw` + `duon_gaz:canonical_dhw_cost`,
- koszty stałe: `duon_gaz:canonical_fixed_cost_gas` + `duon_gaz:canonical_fixed_cost`.

`duon_gaz:canonical_fixed_cost_gas` zawsze ma zużycie `0 m³`; służy wyłącznie jako nośnik kosztu stałego.

Na działającej instalacji potwierdzono poprawne rozdzielenie CO/CWU, koszt stały przy zerowym nośniku oraz zgodność całego toru z historią kanoniczną i fakturami.

## SMS z odczytem — od 0.5.0

Encje do ręcznego odczytu:

- `Stan gazomierza` — wpisanie dokładnego fizycznego wskazania,
- `Zapisz i wyślij SMS` — zapis kotwicy i przygotowanie wiadomości.

Przebieg:

1. użytkownik wpisuje dokładny stan gazomierza,
2. przycisk zapisuje go jako ręczną kotwicę,
3. do SMS wartość jest zaokrąglana do pełnych m³ metodą `ROUND_HALF_UP`,
4. integracja identyfikuje użytkownika przez `context.user_id`,
5. znajduje dokładnie jedno urządzenie Android Home Assistant Mobile App przypisane do tego użytkownika,
6. otwiera na nim systemowy edytor SMS z gotowym numerem odbiorcy i treścią,
7. użytkownik sam zatwierdza wysłanie wiadomości.

Integracja nie wysyła SMS samodzielnie.

Przy pierwszym użyciu Android może poprosić Home Assistant Mobile App o uprawnienie „wyświetlanie nad innymi aplikacjami”. Ponowne kliknięcie z tym samym odczytem w ciągu 5 minut jest traktowane jako techniczne ponowienie SMS i nie tworzy duplikatu kotwicy. Ten sam stan po upływie 5 minut jest traktowany jako nowy rzeczywisty odczyt — także gdy gazomierz się nie zmienił.

## Numer licznika

Od 0.5.0 konfiguracja Outlook/PDF używa jednego jawnego pola:

```text
Numer licznika
```

Ta sama wartość jest używana:

- jako hasło do zaszyfrowanych faktur PDF DUON,
- jako identyfikator w treści SMS z odczytem.

Numer jest przechowywany jako tekst, aby zachować ewentualne zera wiodące. Dane konkretnej instalacji nie są zakodowane w publicznym repozytorium.

Po przejściu z 0.4.2 należy użyć **Przekonfiguruj**, wpisać numer licznika i ponownie zakończyć Device Code Flow Microsoft. Stare pole `invoice_pdf_password` nie jest automatycznie migrowane.

## Outlook / Microsoft Graph

Automatyczny import korzysta z Device Code Flow dla publicznego klienta Microsoft.

Zakres dostępu:

```text
offline_access Mail.Read
```

Integracja nie wymaga `client_secret`, nie prosi o `Mail.ReadWrite`, nie wysyła wiadomości, nie przenosi ich i nie usuwa.

Ręczna synchronizacja:

```text
duon_gaz.sync_outlook
```

Niezabezpieczone PDF-y informacyjne są ignorowane. Nierozpoznany zaszyfrowany PDF blokuje całą nową paczkę przed częściowym zapisem.

## Priorytet źródeł danych

1. dokładny ręczny fizyczny odczyt gazomierza,
2. zaufane wskazanie gazomierza z faktury,
3. statystyki CO/CWU z Recorder jako profil zużycia,
4. dane rozliczeniowe z faktur,
5. bieżące szacunki po najnowszej fizycznej kotwicy.

Kotwice fakturowe są domyślnie wyłączone z uczenia kalibracji CO/CWU. Zgodny ręczny odczyt ma pierwszeństwo przed kotwicą z faktury.

## Audyt współczynnika konwersji

Sensor audytu jest wyłącznie diagnostyczny. Nie uczestniczy w rozliczeniach, historii kanonicznej, kalibracji CO/CWU ani Energy Dashboard.

Audyt wykorzystuje dokładne ręczne granice odczytu, istniejącą kalibrację CO/CWU oraz relację `provisional_m3 / physical_m3`. Historyczna referencja kWh/m³ jest wyznaczana jako mediana ważona zużyciem. Od 0.4.2 przebieg salda jest publikowany także do długoterminowych statystyk Recorder.

To nie jest laboratoryjny pomiar ciepła spalania ani dowód błędnego rozliczenia.

## Wymagania

- Home Assistant z Recorder,
- dwa sensory źródłowe CO/CWU posiadające statystyki `sum`,
- fizyczne lub zaufane odczyty gazomierza,
- Android Home Assistant Mobile App dla funkcji SMS,
- `pypdf` jest instalowany automatycznie z `manifest.json`.

Jeżeli `recorder:` korzysta z `include:`, sensory źródłowe CO/CWU i encja audytu muszą być objęte zapisem. Statystyki zewnętrzne `duon_gaz:*` nie wymagają wpisów w `recorder.include.entities`.

## Instalacja ręczna

Skopiuj:

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

## Bezpieczeństwo danych historycznych

DUON Gaz korzysta z publicznych interfejsów Home Assistant Recorder. Nie zapisuje bezpośrednio do SQL i nie modyfikuje surowych statystyk źródłowych CO/CWU.

Szczegółowy stan architektury i decyzji projektowych znajduje się w [`docs/STATUS_ROZWOJU.md`](docs/STATUS_ROZWOJU.md).
