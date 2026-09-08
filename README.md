# DUON Gaz

Niestandardowa integracja dla Home Assistanta, która łączy fizyczne odczyty gazomierza ze skumulowanymi statystykami CO/CWU, danymi rozliczeniowymi DUON i buduje jedną skorygowaną historię zużycia gazu w Recorder.

Aktualna wersja rozwojowa: **0.3.4**.

> [!IMPORTANT]
> Wersja 0.3.4 jest nadal testowana na gałęzi `feature/store-v2-recorder-sums`. Gałąź `main` pozostaje starszą wersją kodu do czasu zakończenia testów i scalenia bieżących zmian.

Szczegółowy bieżący status, znane problemy i plan dalszych prac: [`docs/STATUS_ROZWOJU.md`](docs/STATUS_ROZWOJU.md).

## Co robi

DUON Gaz korzysta z czterech warstw danych:

1. **fizyczne odczyty gazomierza** — nadrzędne kotwice w m³,
2. **skumulowane statystyki CO/CWU z Recorder** — godzinowy profil zużycia,
3. **zaufane odczyty z faktur** — kotwice o niższej precyzji,
4. **dane rozliczeniowe DUON** — współczynnik konwersji, taryfy i kwoty z faktur.

Integracja potrafi:

- wybrać dowolne dwa sensory Home Assistanta jako źródła CO i CWU,
- odczytywać ich skumulowane statystyki `sum` z Recorder,
- zapisywać dokładne fizyczne odczyty gazomierza wraz z czasem,
- osobno kalibrować CO i CWU w m³/kWh,
- rekonstruować brakujące dane godzinowe,
- obsługiwać ujemne korekty źródła bez tworzenia ujemnego zużycia gazu,
- dokładnie domykać rozliczone przedziały do fizycznych odczytów gazomierza,
- budować bieżący szacowany ogon po najnowszym fizycznym odczycie,
- publikować monotoniczną statystykę zewnętrzną `duon_gaz:canonical_gas`,
- automatycznie odświeżać bieżący ogon po nowych godzinowych statystykach Recorder,
- ręcznie importować zaszyfrowane faktury PDF DUON,
- automatycznie pobierać faktury z Microsoft Outlook / Graph,
- przechowywać dane rozliczeniowe niezależnie od fizycznych kotwic,
- wykrywać sytuacje, w których parser faktury nie rozumie nowego układu PDF i bezpiecznie wstrzymywać import.

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

Po zebraniu wystarczającej liczby wiarygodnych przedziałów między dokładnymi odczytami gazomierza integracja wyznacza osobne współczynniki CO i CWU dla danej instalacji.

Fizyczny gazomierz pozostaje nadrzędnym źródłem prawdy. Odczyt z faktury może być użyty jako kotwica historii, jeżeli literalny typ odczytu to `Rozliczeniowy`, ale ze względu na dzienną precyzję czasu **nie powinien uczestniczyć w uczeniu kalibracji CO/CWU**.

## Wymagania Recorder

- Home Assistant z włączonym **Recorder**,
- dwa sensory źródłowe reprezentujące skumulowane zużycie CO i CWU i posiadające statystyki `sum`,
- fizyczne lub zaufane odczyty gazomierza do rozliczania historii.

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

## Konfiguracja podstawowa

Formularz wymaga wpisania wartości właściwych dla własnej instalacji:

- sensora źródłowego CO,
- sensora źródłowego CWU,
- rozliczeniowego współczynnika konwersji w kWh/m³,
- ceny gazu netto za kWh,
- zmiennej stawki dystrybucyjnej netto za kWh,
- miesięcznego abonamentu netto,
- miesięcznej stałej opłaty dystrybucyjnej netto,
- VAT jako liczby dziesiętnej (`0.23` = 23%).

**Integracja nie zawiera taryf, encji, odczytów ani identyfikatorów konkretnej instalacji.**

Po instalacji wartości można zmienić przez:

**Ustawienia → Urządzenia i usługi → DUON Gaz → Konfiguruj**

## Dodawanie fizycznego odczytu gazomierza

1. Wpisz dokładny bieżący stan gazomierza w encji liczbowej DUON Gaz.
2. Naciśnij **Zapisz odczyt gazomierza**.
3. DUON Gaz zapisze odczyt wraz z czasem i spójną migawką źródeł Recorder.
4. Kalibracja zostanie przeliczona, gdy liczba wiarygodnych przedziałów pozwoli rozdzielić wpływ CO i CWU.

Nowe ręczne odczyty są zapisywane z precyzją 0,001 m³.

## Ręczny import faktury PDF

Integracja posiada usługę:

```text
duon_gaz.import_invoice
```

Plik PDF musi znajdować się wewnątrz katalogu `/config`. Integracja nie pozwala tej usłudze czytać plików spoza `/config`.

Parser korzysta z `pypdf` i nie używa OCR. Odczytane dane są przed zapisem sprawdzane pod kątem zgodności zużycia m³, wskazań licznika i energii rozliczeniowej.

Jeżeli bieżący odczyt na fakturze ma literalny typ **Rozliczeniowy**, może zostać użyty jako zaufana kotwica o precyzji dziennej. Odczyty szacowane pozostają wyłącznie danymi rozliczeniowymi.

Jeżeli w pobliżu istnieje zgodny ręczny odczyt gazomierza, odczyt z faktury zostaje zapisany audytowo, ale nie zastępuje dokładniejszej ręcznej kotwicy.

Ponowny import tego samego numeru faktury jest idempotentny.

## Automatyczny import Outlook / Microsoft Graph — 0.3.4

Wersja 0.3.4 dodaje automatyczny import wiadomości i załączników PDF z Microsoft Outlook.

Integracja korzysta z **Device Code Flow** dla publicznego klienta Microsoft. Nie wymaga `client_secret` i nie ma okresowego obowiązku odnawiania sekretu aplikacji.

Zakres dostępu:

```text
offline_access Mail.Read
```

Integracja nie prosi o `Mail.ReadWrite`, nie wysyła wiadomości, nie przenosi ich i nie usuwa.

Folder Outlook jest konfigurowalny. Wiadomości są dodatkowo filtrowane po skonfigurowanym nadawcy i dokładnym temacie.

### Hasło do PDF w trybie automatycznym

Automatyczny import wymaga bezobsługowego odszyfrowania faktury. Hasło jest wpisywane w formularzu Home Assistanta i przechowywane w danych wpisu konfiguracji.

Hasło:

- jest maskowane w UI,
- nie trafia do DUON Store,
- nie powinno trafiać do logów ani diagnostyki,
- nie jest przez integrację osobno szyfrowane na dysku.

### Dokumenty informacyjne

Wiadomości DUON mogą zawierać również niezabezpieczone PDF-y informacyjne, np. komunikaty taryfowe. Automatyczny importer faktur ignoruje takie pliki. Nie powinny one powodować błędu synchronizacji.

### Bezpiecznik zmiany wyglądu faktury

Automatyczny import ma działać w trybie **fail-closed**. Przed zatwierdzeniem nowej paczki faktur parser wykonuje preflight. Jeżeli nowy zaszyfrowany PDF nie przejdzie parsera lub walidacji, nowa paczka nie powinna zostać częściowo zapisana ani zmienić kotwic, kalibracji lub historii kanonicznej.

Szczegóły i aktualny stan testów tego mechanizmu opisuje [`docs/STATUS_ROZWOJU.md`](docs/STATUS_ROZWOJU.md).

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

Jeżeli zmieni się fizyczna podstawa historii, integracja automatycznie przechodzi do pełnej przebudowy zamiast ryzykować pozostawienie niespójnej serii.

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

Na instalacji testowej potwierdzono między innymi:

- pełną publikację ponad 22 tys. godzin historii,
- monotoniczną sumę m³,
- brak ujemnych godzin zużycia,
- dokładne domknięcie części rozliczonej do fizycznego gazomierza,
- poprawne scalenie godziny granicznej,
- przyrostowe odświeżenie tylko bieżącego ogona,
- automatyczne uruchomienie odświeżenia po zdarzeniu godzinowym Recorder,
- Device Code Flow Microsoft bez sekretu aplikacji,
- dostęp Graph wyłącznie do odczytu poczty,
- automatyczne pobieranie i import nowszych faktur PDF,
- pierwszeństwo dokładnego ręcznego odczytu nad zgodną kotwicą z faktury,
- przywrócenie poprawnej kalibracji po wykrytym błędzie testowym.

Dane liczbowe z instalacji testowej nie są zakodowane w integracji.

## Jeszcze do zrobienia przed stabilnym wydaniem

- rozszerzyć parser o starsze warianty faktur DUON i dodać testy regresyjne,
- dokończyć i zweryfikować bezpiecznik preflight całej paczki,
- zsynchronizować wszystkie naprawy runtime z repozytorium,
- wykonać końcowy audyt i ponowną publikację historii kanonicznej,
- dodać prostą ręczną opcję **Połącz ponownie Outlook** w UI Home Assistanta,
- rozszerzyć testy Device Code / Outlook / migracji danych,
- przygotować i przetestować wysyłanie SMS,
- wykonać finalną migrację konfiguracji Energy Dashboard,
- zakończyć przegląd PR #1, scalić do `main` i przygotować stabilne wydanie HACS.
