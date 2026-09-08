# DUON Gaz

Niestandardowa integracja dla Home Assistanta do rekonstrukcji i bieżącego śledzenia zużycia gazu na podstawie fizycznych odczytów gazomierza, skumulowanych statystyk CO/CWU zapisanych w Recorder oraz danych rozliczeniowych DUON.

## Aktualna wersja

**0.4.1** — stabilne wydanie dodające informacyjny audyt współczynnika konwersji względem lokalnej relacji Ariston + gazomierz.

Szczegółowe informacje o wydaniu: [`RELEASE_NOTES_0.4.1.md`](RELEASE_NOTES_0.4.1.md).

## Co potrafi 0.4.1

- wybiera dowolne dwa sensory Home Assistanta jako źródła CO i CWU,
- korzysta ze skumulowanych statystyk `sum` z Recorder,
- zapisuje dokładne fizyczne odczyty gazomierza,
- osobno kalibruje CO i CWU w m³/kWh,
- rekonstruuje brakujące godziny,
- obsługuje korekty/rollbacki źródła bez tworzenia ujemnego zużycia,
- dokładnie domyka rozliczone okresy do fizycznego gazomierza,
- buduje bieżący szacowany ogon po ostatnim odczycie,
- publikuje kanoniczne statystyki objętości:

```text
duon_gaz:canonical_gas
duon_gaz:canonical_heating
duon_gaz:canonical_dhw
duon_gaz:canonical_fixed_cost_gas
```

- publikuje kanoniczne statystyki kosztowe:

```text
duon_gaz:canonical_heating_cost
duon_gaz:canonical_dhw_cost
duon_gaz:canonical_fixed_cost
```

- wymusza zgodność `CO + CWU = całkowity gaz`,
- księguje koszty historyczne według literalnego okresu `Za okres` z faktury,
- domyka zastosowane okresy kosztowe do autorytatywnej kwoty brutto faktury,
- dzieli koszt zmienny pomiędzy CO i CWU według kanonicznego zużycia,
- pozostawia częściowo pokryte okresy historyczne bez sztucznej ekstrapolacji,
- wycenia bieżący ogon z aktualnej konfiguracji taryfowej,
- obsługuje pełne lokalne dni rozliczeniowe i zmiany czasu DST,
- automatycznie odświeża bieżący ogon po wygenerowaniu nowych godzinowych statystyk Recorder,
- wykonuje pełny rebuild po zmianie aktywnego zestawu kotwic, kalibracji, faktur lub konfiguracji kosztowej,
- importuje zaszyfrowane faktury PDF DUON przez parser `pypdf`, bez OCR,
- obsługuje starsze i nowsze układy faktur oraz zmiany stawek w okresie rozliczeniowym,
- automatycznie pobiera faktury z Microsoft Outlook / Graph,
- działa fail-closed: nierozpoznana zaszyfrowana faktura blokuje całą nową paczkę bez częściowego zapisu,
- zapisuje paczkę faktur atomowo i weryfikuje Store po zapisie,
- nie modyfikuje surowych statystyk źródłowych CO/CWU,
- udostępnia informacyjny sensor `Audyt współczynnika konwersji`, który porównuje współczynnik DUON z lokalną historyczną relacją Ariston + gazomierz,
- w audycie używa tylko faktur z dwiema dokładnie dopasowanymi ręcznymi granicami odczytu; pozostałych okresów nie zgaduje,
- pokazuje podpisane saldo w PLN z perspektywy użytkownika: `+` oznacza korzyść użytkownika, `-` oznacza koszt wyższy niż lokalna referencja.

## Audyt współczynnika konwersji

Sensor audytu jest wyłącznie diagnostyczny. Nie uczestniczy w rozliczeniach, historii kanonicznej, kalibracji CO/CWU ani Energy Dashboard.

Audyt korzysta z dokładnych ręcznych odczytów gazomierza jako granic okresów, istniejącej niezależnej kalibracji CO/CWU oraz relacji `provisional_m3 / physical_m3` z kanonicznych przedziałów przed normalizacją do gazomierza. Stała referencja kWh/m³ jest wyznaczana jako mediana ważona zużyciem.

Stan encji jest skumulowanym odchyleniem kosztu zmiennego w PLN liczonym z perspektywy użytkownika:

- wartość dodatnia — korzystniej dla użytkownika niż lokalna referencja,
- wartość ujemna — mniej korzystnie dla użytkownika niż lokalna referencja.

To nie jest laboratoryjny pomiar ciepła spalania ani dowód błędnego rozliczenia. Bez niezależnego kalorymetru audyt może wykrywać dryf i odchylenie względem własnej historii, ale nie bezwzględny błąd dostawcy.

## Wymagania

- Home Assistant z włączonym Recorder,
- dwa sensory źródłowe CO/CWU posiadające statystyki `sum`,
- fizyczne lub zaufane odczyty gazomierza,
- dla importu PDF: wymaganie `pypdf` jest instalowane z `manifest.json`.

Jeżeli `recorder:` korzysta z `include:`, wybrane sensory CO i CWU muszą znajdować się na liście dozwolonych encji.

Jeżeli historia stanu sensora `Audyt współczynnika konwersji` ma być zapisywana przez Recorder przy konfiguracji `include:`, również należy dodać tę encję do listy dozwolonych encji.

Statystyki `duon_gaz:*` są statystykami zewnętrznymi, a nie stanami encji, dlatego nie trzeba dodawać ich do `recorder.include.entities`.

## Instalacja ręczna

Skopiuj katalog:

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

## Outlook / Microsoft Graph

Automatyczny import korzysta z Device Code Flow dla publicznego klienta Microsoft.

Zakres dostępu:

```text
offline_access Mail.Read
```

Integracja nie wymaga `client_secret`, nie prosi o `Mail.ReadWrite`, nie wysyła wiadomości, nie przenosi ich i nie usuwa.

Niezabezpieczone PDF-y informacyjne są ignorowane przez importer faktur. Zaszyfrowany PDF, którego parser nie potrafi bezpiecznie zweryfikować, blokuje całą nową paczkę.

Ręczna synchronizacja jest dostępna jako:

```text
duon_gaz.sync_outlook
```

## Priorytet źródeł danych

1. dokładny ręczny fizyczny odczyt gazomierza,
2. zaufane wskazanie gazomierza z faktury,
3. statystyki CO/CWU z Recorder jako profil zużycia,
4. dane rozliczeniowe z faktur,
5. bieżące szacunki po najnowszej fizycznej kotwicy.

Kotwice fakturowe są domyślnie wyłączone z uczenia kalibracji CO/CWU. Zgodny ręczny odczyt ma pierwszeństwo przed kotwicą z faktury.

## Energy Dashboard

Dla rozdzielonego widoku gazu konfiguracja jest projektowana jako trzy źródła:

- CO: `duon_gaz:canonical_heating` + `duon_gaz:canonical_heating_cost`,
- CWU: `duon_gaz:canonical_dhw` + `duon_gaz:canonical_dhw_cost`,
- koszty stałe: `duon_gaz:canonical_fixed_cost_gas` + `duon_gaz:canonical_fixed_cost`.

`duon_gaz:canonical_fixed_cost_gas` zawsze ma zużycie `0 m³`; służy tylko jako nośnik kosztu stałego.

Jeżeli w Energy Dashboard używany jest rozdział CO/CWU, nie należy dodawać równolegle `duon_gaz:canonical_gas` jako kolejnego źródła gazu, ponieważ podwoiłoby to zużycie.

## Bezpieczeństwo danych historycznych

DUON Gaz korzysta z oficjalnych interfejsów Home Assistant Recorder. Nie zapisuje bezpośrednio do SQL i nie nadpisuje oryginalnych statystyk CO/CWU.

## Stan walidacji 0.4.1

Na działającej instalacji potwierdzono m.in.:

- pełną publikację statystyk gazu, CO, CWU i kosztów,
- dokładne domknięcie CO + CWU do całkowitego gazu,
- dokładne domknięcie zastosowanych kosztów historycznych do brutto faktur,
- miesięczne porównanie pełnych okresów fakturowych z danymi Recorder,
- prawidłowe pozostawienie częściowo pokrytego historycznego fragmentu bez sztucznej wyceny,
- poprawny przyrostowy refresh bieżącego ogona po pełnym rebuildzie,
- niezmienność istniejącej kalibracji,
- poprawny guard importu Outlook,
- brak ujemnego zużycia i nierozliczonych rollbacków,
- dokładne domknięcie używanych okresów audytu `m³ faktura = m³ lokalne`,
- pomijanie faktur bez dwóch dokładnych ręcznych granic,
- poprawną, odwróconą z perspektywy użytkownika konwencję znaku salda audytu.

## Dalszy rozwój

Po wydaniu 0.4.1 pozostają osobno m.in.:

- finalna konfiguracja Energy Dashboard na działającej instalacji,
- SMS.
