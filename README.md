# DUON Gaz

Niestandardowa integracja dla Home Assistanta, która łączy fizyczne odczyty gazomierza ze skumulowanymi statystykami CO/CWU i buduje jedną skorygowaną historię zużycia gazu w Recorder.

Aktualna wersja rozwojowa: **0.3.3**.

> [!IMPORTANT]
> Wersja 0.3.3 jest nadal testowana na gałęzi `feature/store-v2-recorder-sums`. Gałąź `main` pozostaje starszą wersją kodu do czasu scalenia bieżących zmian.

## Co robi

DUON Gaz korzysta z trzech warstw danych:

1. **fizyczne odczyty gazomierza** — nadrzędne kotwice w m³,
2. **skumulowane statystyki CO/CWU z Recorder** — godzinowy profil zużycia,
3. **dane rozliczeniowe DUON** — współczynnik konwersji, taryfy i dane z faktur.

Integracja potrafi:

- wybrać dowolne dwa sensory Home Assistanta jako źródła CO i CWU,
- odczytywać ich skumulowane statystyki `sum` z Recorder,
- zapisywać dokładne fizyczne odczyty gazomierza wraz z czasem,
- przechowywać zaufane odczyty z faktur jako kotwice o niższej precyzji,
- osobno kalibrować CO i CWU w m³/kWh,
- rekonstruować brakujące dane godzinowe,
- obsługiwać ujemne korekty źródła bez tworzenia ujemnego zużycia gazu,
- dokładnie domykać rozliczone przedziały do fizycznych odczytów gazomierza,
- budować bieżący szacowany ogon po najnowszym fizycznym odczycie,
- publikować jedną monotoniczną statystykę zewnętrzną `duon_gaz:canonical_gas`,
- automatycznie odświeżać bieżący ogon po nowych godzinowych statystykach Recorder,
- przyrostowo zapisywać tylko część od ostatniej kotwicy,
- automatycznie przechodzić do pełnej przebudowy po zmianie kotwicy, kalibracji lub źródła,
- pozwalać na zmianę źródeł i parametrów rozliczeniowych przez **Konfiguruj**,
- ręcznie importować zaszyfrowane faktury PDF DUON znajdujące się w `/config`.

Surowe statystyki źródłowe nie są modyfikowane.

## Kanoniczna statystyka Recorder

Głównym długoterminowym wynikiem integracji jest:

```text
duon_gaz:canonical_gas
```

To zewnętrzna statystyka Home Assistant Recorder w **m³** z monotoniczną sumą narastającą `sum`.

Historia ma dwie części:

- **rozliczoną** — przedziały zamknięte fizycznymi lub zaufanymi kotwicami gazomierza i dokładnie znormalizowane do zmierzonej różnicy m³,
- **bieżącą szacowaną** — otwarty przedział po najnowszej kotwicy, estymowany ze statystyk CO/CWU w Recorder.

Gdy najnowszy fizyczny odczyt wypada wewnątrz godziny, DUON Gaz scala część rozliczoną i bieżącą w jeden rekord Recorder dla tej godziny.

## Kalibracja

Nowa instalacja zaczyna od neutralnej wartości **0,1 m³/kWh** osobno dla CO i CWU. Jest to wyłącznie techniczny punkt startowy, a nie wartość pochodząca z konkretnego kotła, taryfy lub instalacji.

Po zebraniu wystarczającej liczby wiarygodnych przedziałów między odczytami gazomierza integracja wyznacza osobne współczynniki CO i CWU dla danej instalacji. Fizyczny gazomierz pozostaje nadrzędnym źródłem prawdy, więc zamknięte przedziały są zawsze domykane do rzeczywistej różnicy m³.

## Wymagania Recorder

- Home Assistant z włączonym **Recorder**,
- dwa sensory źródłowe reprezentujące skumulowane zużycie CO i CWU i posiadające statystyki `sum`,
- fizyczne lub zaufane odczyty gazomierza do rozliczania i uczenia kalibracji.

Jeżeli konfiguracja `recorder:` korzysta z `include:`, wybrane sensory CO i CWU muszą się tam znaleźć.

Przykład:

```yaml
recorder:
  include:
    entities:
      - sensor.twoj_kociol_zuzycie_gazu_co
      - sensor.twoj_kociol_zuzycie_gazu_cwu
```

`duon_gaz:canonical_gas` jest **statystyką zewnętrzną**, a nie stanem encji, dlatego nie trzeba dodawać jej do `recorder.include.entities`.

## Instalacja wersji rozwojowej

Skopiuj katalog:

```text
custom_components/duon_gaz
```

z gałęzi:

```text
feature/store-v2-recorder-sums
```

do:

```text
/config/custom_components/duon_gaz
```

i uruchom ponownie Home Assistanta.

Następnie:

**Ustawienia → Urządzenia i usługi → Dodaj integrację → DUON Gaz**

Repozytorium zawiera `hacs.json`. Normalna instalacja HACS z gałęzi domyślnej będzie właściwa po scaleniu linii 0.3.x do `main`.

## Konfiguracja

Formularz wymaga wpisania wartości właściwych dla własnej instalacji:

- sensora źródłowego CO,
- sensora źródłowego CWU,
- rozliczeniowego współczynnika konwersji w kWh/m³,
- ceny gazu netto za kWh,
- zmiennej stawki dystrybucyjnej netto za kWh,
- miesięcznego abonamentu netto,
- miesięcznej stałej opłaty dystrybucyjnej netto,
- VAT jako liczby dziesiętnej (`0.23` = 23%).

**Integracja nie podpowiada taryf z instalacji używanej podczas tworzenia projektu.**

Po instalacji wartości można zmienić przez:

**Ustawienia → Urządzenia i usługi → DUON Gaz → Konfiguruj**

Zmiana źródła lub kalibracji powoduje bezpieczne przejście z przyrostowego odświeżenia ogona do pełnej przebudowy historii kanonicznej.

## Dodawanie fizycznego odczytu gazomierza

1. Wpisz dokładny bieżący stan gazomierza w encji liczbowej DUON Gaz.
2. Naciśnij **Zapisz odczyt gazomierza**.
3. DUON Gaz zapisze odczyt wraz z czasem i spójną migawką źródeł Recorder.
4. Kalibracja zostanie przeliczona, gdy liczba wiarygodnych przedziałów pozwoli rozdzielić wpływ CO i CWU.

Nowe ręczne odczyty są zapisywane z precyzją 0,001 m³. Osobno może być przechowywana wartość zaokrąglona do pełnych m³ na potrzeby przyszłego mechanizmu SMS.

## Import faktury PDF

Od wersji 0.3.3 integracja posiada ręczną usługę:

```text
duon_gaz.import_invoice
```

Plik PDF musi znajdować się wewnątrz katalogu `/config`. Integracja nie pozwala tej usłudze czytać plików spoza `/config`.

Przykładowy plik:

```text
/config/duon/faktura.pdf
```

W wywołaniu usługi podaje się:

- `path` — ścieżkę względem `/config`, np. `duon/faktura.pdf`,
- `password` — hasło do zaszyfrowanego PDF,
- opcjonalnie `source_message_id` — identyfikator wiadomości źródłowej, przygotowany z myślą o późniejszym imporcie z Microsoft Graph.

Hasło do PDF jest używane tylko podczas bieżącego odczytu i **nie jest zapisywane przez integrację**.

Parser korzysta z `pypdf` i nie używa OCR. Odczytane dane są przed zapisem sprawdzane pod kątem zgodności zużycia m³, wskazań licznika i energii rozliczeniowej.

Jeżeli bieżący odczyt na fakturze ma literalny typ **Rozliczeniowy**, może zostać użyty jako zaufana kotwica o precyzji dziennej. Odczyty szacowane pozostają wyłącznie danymi rozliczeniowymi.

Jeżeli w pobliżu istnieje zgodny ręczny odczyt gazomierza, odczyt z faktury zostaje zapisany audytowo, ale nie zastępuje dokładniejszej ręcznej kotwicy.

Dodanie nowej zaufanej kotwicy powoduje automatyczną przebudowę odpowiedniej historii kanonicznej. Ponowny import tego samego numeru faktury jest idempotentny.

## Rekonstrukcja historii

Dla każdego zamkniętego przedziału gazomierza integracja:

- wylicza godzinowe przyrosty CO/CWU ze skumulowanych sum Recorder,
- wycofuje ujemne korekty źródła z ostatniego dodatniego zużycia,
- rekonstruuje brakujące godziny na podstawie profili tej samej lokalnej godziny,
- niezależnie przelicza CO i CWU za pomocą współczynników m³/kWh,
- skaluje przedział do zmierzonej fizycznej różnicy m³,
- zachowuje proporcję CO/CWU,
- sprawdza dokładne domknięcie.

Algorytm jest ogólny i nie zawiera dat, odczytów, encji ani taryf konkretnej instalacji.

## Automatyczne odświeżanie bieżącego ogona

Integracja nasłuchuje zdarzenia wygenerowania godzinowych statystyk Recorder. Jeżeli podstawa historii się nie zmieniła, zapisuje wyłącznie bieżący ogon od ostatniej fizycznej kotwicy.

Jeżeli zmieni się:

- fizyczna kotwica,
- źródło CO,
- źródło CWU,
- kalibracja,

integracja automatycznie wykonuje pełną publikację zamiast ryzykować pozostawienie niespójnej historii.

Ręczne wymuszenie odświeżenia jest dostępne jako usługa:

```text
duon_gaz.refresh_tail
```

## Priorytet źródeł danych

1. dokładny ręczny fizyczny odczyt gazomierza,
2. zaufane wskazanie gazomierza z faktury,
3. statystyki CO/CWU z Recorder używane jako profil zużycia,
4. dane rozliczeniowe z faktur,
5. bieżące szacunki po najnowszej fizycznej kotwicy.

Kolejny fizyczny odczyt zamienia poprzedni okres szacowany w przedział rozliczony.

## Bezpieczeństwo danych historycznych

DUON Gaz korzysta z interfejsów Home Assistant Recorder. **Nie zapisuje bezpośrednio do SQL** i nie nadpisuje oryginalnych statystyk CO/CWU.

Historia kanoniczna jest przechowywana pod własnym identyfikatorem:

```text
duon_gaz:canonical_gas
```

## Zweryfikowane zachowanie

Na instalacji testowej potwierdzono:

- pełną publikację ponad 22 tys. godzin historii,
- monotoniczną sumę m³,
- brak ujemnych godzin zużycia,
- dokładne domknięcie części rozliczonej do fizycznego gazomierza,
- poprawne scalenie godziny granicznej,
- przyrostowe odświeżenie tylko bieżącego ogona,
- automatyczne uruchomienie odświeżenia po zdarzeniu godzinowym Recorder,
- weryfikację końcowego czasu i sumy przez API Recorder,
- zachowanie istniejącej wyuczonej kalibracji podczas migracji 0.3.2.

Dane liczbowe z instalacji testowej nie są zakodowane w integracji.

## Jeszcze do zrobienia

- automatyczne pobieranie faktur z Outlook/Microsoft Graph,
- bezpieczna konfiguracja danych potrzebnych do automatycznego odszyfrowywania faktur,
- przygotowanie i wysyłanie SMS,
- finalna migracja konfiguracji Energy Dashboard,
- scalenie linii 0.3.x do `main` i stabilne wydanie HACS.
