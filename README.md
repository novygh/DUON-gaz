# DUON Gaz

Niestandardowa integracja dla Home Assistanta, która łączy fizyczzne odczyty gazomierza ze skumulowanymi statystykami CO/CWU z integracji kotła i buduje jedną skorygowaną historię zużycia gazu w Recorder.

Aktualna wersja rozwojowa: **0.3.1**.

> [!IMPORTANT]
> Wersja 0.3.1 jest nadal testowana na gałęzi rozwojowej `feature/store-v2-recorder-sums`. Gałąź `main` pozostaje starszą wersją testową do czasu scalenia bieżących zmian.

## Co robi

DUON Gaz korzysta z trzech warstw danych:

1. **fizyczne odczyty gazomierza** — nadrzędne kotwice w m³,
2. **skumulowane statystyki CO/CWU z Recorder** — godzinowy profil zużycia,
3. **dane rozliczeniowe DUON** — współczynnik konwersji i taryfy używane do obliczeń energii i kosztów.

Integracja potrafi:

- wybrać dowolne dwa sensory Home Assistanta jako źródła CO i CWU,
- odczytywać ich skumulowane statystyki `sum` z Recorder,
- zapisywać dokładne fizyczne odczyty gazomierza wraz z czasem,
- przechowywać zaufane odczyty z faktur jako kotwice o niższej precyzji,
- osobno kalibrować CO i CWU w m³/kWh,
- rekonstruować brakujące dane godzinowe źródła,
- obsługiwać ujemne korekty/rollbacki źródła bez tworzenia ujemnego zużycia gazu,
- dokładnie domykać rozliczone przedziały do fizycznych odczytów gazomierza,
- budować bieżący szacowany ogon po najnowszym fizycznym odczycie,
- publikować jedną monotoniczną statystykę zewnętrzną: `duon_gaz:canonical_gas`,
- automatycznie odświeżać bieżący ogon po wygenerowaniu nowych godzinowych statystyk Recorder,
- obliczać szacowany bieżący stan gazomierza, podział CO/CWU, energię i diagnostykę kosztów.

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

Od wersji 0.3.1 bieżący ogon jest odświeżany automatycznie po zdarzeniu wygenerowania godzinowych statystyk przez Home Assistanta. Pełna przebudowa jest wykonywana, gdy zmieni się kotwica, kalibracja albo źródło.

## Wymagania

- Home Assistant z włączonym **Recorder**.
- Dwa sensory źródłowe reprezentujące skumulowane zużycie CO i CWU i posiadające statystyki `sum` w Recorder.
- Co najmniej dwie zaufane kotwice gazomierza do rozliczonej rekonstrukcji i kalibracji.

### Konfiguracja `recorder.include`

Jeżeli konfiguracja `recorder:` korzysta z `include:`, wybrane sensory źródłowe CO i CWU muszą się tam znaleźć. W przeciwnym razie Home Assistant nie będzie miał wymaganej historii źródłowej.

Przykład:

```yaml
recorder:
  include:
    entities:
      - sensor.twoj_kociol_zuzycie_gazu_co
      - sensor.twoj_kociol_zuzycie_gazu_cwu
```

`duon_gaz:canonical_gas` jest **statystyką zewnętrzną**, a nie stanem encji, dlatego nie trzeba jej dodawać do `recorder.include.entities`.

## Instalacja

### Aktualna gałąź rozwojowa

Do czasu scalenia 0.3.x do `main` należy używać katalogu:

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

i uruchom ponownie Home Assistanta po wymianie plików integracji.

Następnie otwórz:

**Ustawienia → Urządzenia i usługi → Dodaj integrację → DUON Gaz**

### HACS

Repozytorium zawiera już `hacs.json`. Normalnej instalacji przez HACS z gałęzi domyślnej należy używać po scaleniu bieżącej gałęzi rozwojowej do `main`.

## Konfiguracja początkowa

Formularz konfiguracji pyta o:

- sensor źródłowy CO,
- sensor źródłowy CWU,
- rozliczeniowy współczynnik konwersji w kWh/m³,
- cenę gazu netto za kWh,
- zmienną stawkę dystrybucyjną netto za kWh,
- miesięczny abonament netto,
- miesięczną stałą opłatę dystrybucyjną netto,
- VAT jako wartość dziesiętną (`0.23` = 23%).

Encje źródłowe są konfigurowalne; silnik rekonstrukcji nie ma na stałe wpisanych identyfikatorów encji Ariston.

> [!WARNING]
> Wartości domyślne w aktualnej wersji rozwojowej są jedynie wartościami startowymi/przykładowymi z instalacji używanej podczas tworzenia integracji. Należy wpisać wartości właściwe dla własnej umowy i faktur. Nie są to uniwersalne taryfy DUON.

## Dodawanie fizycznego odczytu gazomierza

1. Wpisz dokładny bieżący stan gazomierza w encji liczbowej DUON Gaz.
2. Naciśnij **Zapisz odczyt gazomierza**.
3. DUON Gaz zapisze odczyt wraz z czasem i spójnym punktem źródłowym z Recorder.
4. Gdy dostępna będzie wystarczająca liczba zaufanych odczytów, kalibracja CO/CWU zostanie przeliczona.

Nowe odczyty ręczne są zapisywane z precyzją 0,001 m³. Osobno przechowywana jest wartość zaokrąglona do pełnych m³ na potrzeby przyszłego mechanizmu SMS.

Fizyczne odczyty są źródłem o najwyższym priorytecie. Odczyty z faktur mogą być używane wyłącznie wtedy, gdy są sklasyfikowane jako zaufane odczyty rozliczeniowe; wartości szacowane na fakturze nie są fizycznymi kotwicami.

## Rekonstrukcja historii

Integracja zawiera ogólny silnik rekonstrukcji. Nie opiera się on na datach ani stanach gazomierza właściwych dla jednej instalacji.

Dla każdego zamkniętego przedziału gazomierza:

- wylicza godzinowe przyrosty CO/CWU ze skumulowanych sum Recorder,
- wycofuje rollbacki źródła z ostatniego dodatniego zużycia,
- rekonstruuje brakujące godziny na podstawie profili tej samej lokalnej godziny,
- niezależnie przelicza CO i CWU za pomocą skalibrowanych współczynników m³/kWh,
- skaluje cały przedział do zmierzonej fizycznej różnicy m³,
- zachowuje proporcję CO/CWU,
- sprawdza dokładne domknięcie przedziału.

Oryginalne statystyki kotła pozostają nietknięte.

## Usługi

### `duon_gaz.refresh_tail`

Ręcznie odświeża wyłącznie otwartą, bieżącą część `duon_gaz:canonical_gas`.

Zwykle nie jest to potrzebne, ponieważ od wersji 0.3.1 integracja nasłuchuje godzinowych statystyk Recorder i automatycznie odświeża ogon.

Jeżeli zmieniła się fizyczna kotwica, źródło albo kalibracja, integracja automatycznie przechodzi do pełnej przebudowy historii kanonicznej.

### `duon_gaz.import_history`

Opcjonalne narzędzie migracyjne/rozwojowe do importu zweryfikowanej historycznej paczki JSON z `/config`.

Nie jest wymagane w nowej instalacji.

## Główne encje

W zależności od języka Home Assistanta i nazw encji integracja udostępnia odpowiedniki:

- zużycia gazu,
- energii gazu,
- całkowitego kosztu gazu,
- CO od ostatniego odczytu,
- CWU od ostatniego odczytu,
- współczynnika konwersji,
- zbiorczej diagnostyki korekty Ariston,
- kalibracji CO,
- kalibracji CWU,
- statusu danych,
- pola wprowadzania stanu gazomierza,
- przycisku zapisu odczytu gazomierza,
- przycisku podglądu historii kanonicznej,
- przycisku publikacji historii kanonicznej.

Sensor **Status danych** udostępnia również diagnostykę historii rozliczonej, bieżącego ogona, publikacji do Recorder i jej weryfikacji.

## Model danych i priorytet źródeł

Zalecana interpretacja jakości danych:

1. dokładny ręczny fizyczny odczyt gazomierza,
2. zaufane wskazanie gazomierza z faktury,
3. statystyki CO/CWU z Recorder używane jako profil zużycia,
4. dane rozliczeniowe z faktur do walidacji energii i kosztów,
5. bieżące szacunki po najnowszej fizycznej kotwicy.

Kolejny fizyczny odczyt zamienia poprzedni bieżący okres szacowany w przedział rozliczony.

## Bezpieczeństwo danych historycznych

DUON Gaz korzysta z interfejsów Home Assistant Recorder. **Nie zapisuje bezpośrednio do SQL** i nie nadpisuje oryginalnych statystyk CO/CWU.

Historia kanoniczna jest przechowywana pod własnym identyfikatorem statystyki DUON:

```text
duon_gaz:canonical_gas
```

## Jeszcze niezaimplementowane

W wersji 0.3.1 nie są jeszcze ukończone:

- automatyczne pobieranie faktur z Outlook/Microsoft Graph,
- pełny mechanizm pobierania i odszyfrowywania zaszyfrowanych faktur PDF ze skrzynki,
- automatyczne przygotowanie/wysyłanie SMS,
- końcowe usunięcie instalacyjnych wartości startowych kalibracji i taryf,
- automatyczna migracja/zastąpienie konfiguracji Energy Dashboard.

## Stan rozwoju

Aktualne prace nad linią 0.3.x są prowadzone w roboczym PR #1. Obejmują magazyn danych v2, historię źródłową opartą na Recorder, osobną kalibrację CO/CWU, kanoniczną rekonstrukcję historyczną, publikację do zewnętrznej statystyki Recorder i automatyczne odświeżanie bieżącego ogona.

Projekt jest przygotowywany tak, aby nadawał się do ponownego użycia w innych instalacjach z kompatybilnymi skumulowanymi sensorami CO/CWU. Odczyty gazomierza, daty, taryfy i współczynniki kalibracji właściwe dla konkretnej instalacji muszą pozostawać poza ogólnym kodem rekonstrukcji.
