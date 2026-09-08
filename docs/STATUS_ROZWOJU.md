# Status rozwoju DUON Gaz

Dokument opisuje stan gałęzi rozwojowej `feature/store-v2-recorder-sums` i plan dojścia do stabilnego wydania. Jest przeznaczony jako techniczny punkt odniesienia dla dalszych prac nad integracją.

Aktualny etap: **0.3.4 — historia kanoniczna, faktury PDF i automatyczny import z Microsoft Outlook / Graph**.

> [!IMPORTANT]
> Gałąź rozwojowa i PR #1 nadal pozostają wersją roboczą. Główna ścieżka danych została zweryfikowana na działającej instalacji, ale przed scaleniem do `main` pozostaje dokończenie obsługi reautoryzacji Outlook w UI, przegląd dokumentacji oraz końcowy przegląd PR.

## Architektura danych

DUON Gaz rozdziela cztery rodzaje danych:

1. **dokładne ręczne odczyty gazomierza** — nadrzędne fizyczne kotwice,
2. **zaufane odczyty z faktur** — kotwice o niższej precyzji czasu i wskazania,
3. **godzinowe statystyki CO/CWU z Recorder** — profil potrzebny do rekonstrukcji przebiegów pomiędzy kotwicami,
4. **dane rozliczeniowe z faktur** — okresy, współczynnik konwersji, stawki i kwoty.

Surowe statystyki CO/CWU nie są modyfikowane. Wynikowa historia gazu jest publikowana jako zewnętrzna statystyka Recorder:

```text
duon_gaz:canonical_gas
```

Integracja nie zapisuje bezpośrednio do SQL.

## Historia kanoniczna

Obecny algorytm:

- waliduje punkty źródłowe i ich monotoniczność,
- wylicza przyrosty CO i CWU ze skumulowanych statystyk `sum`,
- obsługuje ujemne korekty źródła przez rejestr wycofań,
- rekonstruuje brakujące godziny na podstawie profilu tej samej lokalnej godziny,
- przelicza CO i CWU osobnymi współczynnikami m³/kWh,
- domyka każdy rozliczony przedział dokładnie do fizycznej różnicy gazomierza,
- zachowuje proporcję CO/CWU,
- buduje bieżący szacowany ogon po ostatniej kotwicy,
- scala część rozliczoną i bieżącą w jedną monotoniczną serię,
- odświeża ogon po nowych godzinowych statystykach Recorder,
- przechodzi do pełnej przebudowy, gdy zmieni się ostatnia fizyczna kotwica, źródło, kalibracja albo cały zestaw aktywnych kotwic.

### Fingerprint aktywnych kotwic

Publikacja historii przechowuje odcisk całego zestawu aktywnych kotwic fizycznych. Dzięki temu dodanie lub usunięcie kotwicy wewnątrz już rozliczonej historii wymusza pełną przebudowę, nawet jeśli ostatnia kotwica i współczynniki kalibracji nie uległy zmianie.

Starsza publikacja bez fingerprintu wykonuje jednorazowo pełny rebuild i zapisuje fingerprint. Kolejne aktualizacje mogą ponownie korzystać z przyrostowego odświeżania ogona.

## Kalibracja CO/CWU

Kalibracja jest instalacyjna i nie zawiera publicznych wartości właściwych dla konkretnego kotła lub domu.

Nowa instalacja zaczyna od neutralnych współczynników technicznych. Po zgromadzeniu wystarczającej liczby dokładnych przedziałów integracja wyznacza osobne współczynniki CO i CWU metodą odpornej regresji dwóch składowych.

### Zasada dla faktur

Odczyty z faktury mają dokładność dzienną i nie znają fizycznej godziny odczytu. Dlatego:

- mogą służyć jako zaufane kotwice historii, jeżeli literalny typ odczytu jest `Rozliczeniowy`,
- **nie uczestniczą w uczeniu kalibracji CO/CWU**,
- zgodny ręczny odczyt w pobliżu ma pierwszeństwo i powoduje audytowe oznaczenie kotwicy fakturowej jako przesłoniętej,
- kotwica niemonotoniczna jest zachowywana audytowo, ale wykluczana z estymacji,
- historyczna kotwica może zostać zachowana bez lokalnego snapshotu Recorder wyłącznie wtedy, gdy jest wykluczona z kalibracji i istnieje późniejsza zaufana kotwica; najnowsza kotwica nadal wymaga Recorder.

Migracja Store automatycznie oznacza istniejące kotwice fakturowe jako wykluczone z kalibracji, zachowując ich niezależną flagę udziału w estymacji. Przedziały kalibracyjne są budowane dopiero po odfiltrowaniu kotwic wykluczonych, więc taka kotwica nie rozcina poprawnego przedziału ręcznego.

## Faktury PDF

Parser używa `pypdf`, bez OCR. Dane przed zapisem są walidowane między innymi przez:

- zgodność różnicy wskazań gazomierza ze zużyciem m³,
- zgodność m³ w pozycji gazowej z tabelą odczytów,
- zgodność energii rozliczeniowej ze współczynnikiem konwersji,
- zgodność energii pozycji dystrybucyjnych z energią rozliczeniową,
- zgodność współczynnika konwersji pomiędzy wieloma pozycjami gazowymi.

Ponowny import tego samego numeru faktury jest idempotentny.

Parser obsługuje nowsze i starsze warianty faktur, w tym:

- dziesiętne ilości energii w pozycjach dystrybucyjnych,
- okresy rozliczeniowe podzielone na kilka stawek,
- wiele pozycji gazowych i dystrybucyjnych,
- polski zapis separatora tysięcy w energii kWh.

Przy kilku stawkach wyliczana jest efektywna stawka ważona. Współczynnik konwersji nie jest uśredniany — niezgodność pomiędzy pozycjami powoduje błąd parsera.

Każdy rozpoznany wariant ma jawny test regresyjny. Nie należy rozluźniać walidacji tylko po to, aby zaakceptować nieznany dokument.

## Automatyczny Outlook / Microsoft Graph — 0.3.4

Automatyzacja korzysta z Microsoft Graph wyłącznie do odczytu wiadomości i załączników.

### Uwierzytelnianie

Zastosowany jest **Device Code Flow dla publicznego klienta**, bez sekretu aplikacji:

- endpoint kont osobistych Microsoft (`consumers`),
- uprawnienia delegowane: `Mail.Read`,
- zakres OAuth: `offline_access Mail.Read`,
- brak `Mail.ReadWrite`,
- brak wysyłania, przenoszenia i usuwania wiadomości,
- brak okresowo wygasającego `client_secret`.

Access token i refresh token są przechowywane w danych wpisu konfiguracji Home Assistanta. Refresh token jest aktualizowany po odświeżeniu tokenu. Gdy Microsoft wymaga ponownego logowania, integracja potrafi rozpocząć reautoryzację Home Assistanta.

### Wyszukiwanie wiadomości

Folder jest konfigurowalny. Wiadomości są dodatkowo filtrowane po skonfigurowanym nadawcy i dokładnym temacie. Integracja nie zapisuje na stałe identyfikatorów folderów lub danych konkretnej instalacji w kodzie publicznym.

### Hasło do faktur

Automatyczny import wymaga hasła do zaszyfrowanych faktur PDF. Hasło jest podawane w formularzu Home Assistanta i przechowywane w danych wpisu konfiguracji, aby import mógł działać bezobsługowo.

Hasło:

- jest maskowane w formularzu,
- nie trafia do DUON Store,
- nie powinno trafiać do logów ani diagnostyki,
- **nie jest osobno szyfrowane przez integrację na dysku** — bezpieczeństwo opiera się na ochronie konfiguracji Home Assistanta.

Ręczna usługa importu może nadal przyjmować hasło tylko dla bieżącego wywołania.

## Niezabezpieczone PDF-y

Wiadomości DUON mogą zawierać również dokumenty informacyjne, np. komunikaty taryfowe. W obecnym modelu automatycznego importu:

- zaszyfrowany PDF jest kandydatem na fakturę,
- niezabezpieczony PDF jest ignorowany przez importer faktur,
- pominięty dokument informacyjny nie powoduje statusu `partial`.

Dokumenty taryfowe można w przyszłości obsłużyć osobnym, opcjonalnym parserem. Nie należy mieszać ich z parserem faktur.

## Bezpiecznik zmiany układu faktury

Automatyczny import działa w trybie **fail-closed**.

Przed zapisaniem nowej paczki faktur wykonywany jest pełny preflight. Jeżeli choć jeden nowy zaszyfrowany PDF nie przejdzie parsera lub walidacji:

- nowe faktury z tej synchronizacji nie są częściowo zatwierdzane,
- nie są zmieniane kotwice historii,
- nie jest przeliczana kalibracja,
- nie jest przebudowywana historia kanoniczna,
- problematyczna wiadomość nie jest uznawana za poprawnie przetworzoną,
- zapisywany jest bezpieczny stan diagnostyczny z nazwą pliku, czasem, wersją parsera i krótkim odciskiem dokumentu,
- treść faktury, hasło i tokeny nie są zapisywane diagnostycznie.

Po przejściu preflight wszystkie importy są etapowane w pamięci. `billing_periods`, `processed_invoices`, `invoice_readings`, `processed_messages` i stan guard są zapisywane jednym zapisem Store. Store używa `atomic_writes=True`, a synchronizator po zapisie ponownie odczytuje dane i porównuje je z zamierzonym stanem. Niepotwierdzony zapis powoduje wycofanie stanu w pamięci i status `blocked`.

Mechanizm został zweryfikowany na działającej instalacji zarówno przy błędzie parsera, jak i przy błędzie fazy zapisu kotwicy: w obu przypadkach istniejące dane pozostały niezmienione.

## Historyczne faktury poza zakresem Recorder

Starsza faktura może zawierać poprawny odczyt fizyczny z okresu, dla którego lokalne godzinowe statystyki Recorder nie są już dostępne.

Taka kotwica jest dopuszczona bez snapshotu Recorder tylko wtedy, gdy:

- jest wykluczona z kalibracji,
- istnieje późniejsza zaufana kotwica.

Dzięki temu może pozostać częścią audytu i historii fizycznej, ale nie może zostać najnowszą bazą bieżącej estymacji. Builder historii i tak ogranicza aktywne kotwice do zakresu faktycznie dostępnej serii Recorder.

## Reautoryzacja Microsoft

Kod potrafi automatycznie rozpocząć reauth po błędzie tokenu lub autoryzacji Graph.

Pozostałym zadaniem przed wydaniem 0.3.4 jest zapewnienie użytkownikowi prostej, jednoznacznej ścieżki **Połącz ponownie Outlook** bez terminala, plików i wchodzenia do Microsoft Entra.

## Stan testów 0.3.4

Aktualny zestaw CI zawiera **23 testy jednostkowe** i przechodzi w całości.

Zakres obejmuje między innymi:

- historię kanoniczną i dokładne domknięcie do kotwic,
- brakujące godziny i rollbacki Recorder,
- scalenie historii rozliczonej z bieżącym ogonem,
- starsze i nowsze warianty faktur,
- separator tysięcy w energii kWh,
- różne współczynniki konwersji jako błąd fail-closed,
- pomijanie niezabezpieczonych PDF-ów,
- zablokowanie całej paczki przy błędnej fakturze,
- rollback przy niepotwierdzonym zapisie Store,
- migrację flag kalibracji,
- brak rozcinania przedziału kalibracyjnego przez kotwicę wykluczoną,
- regułę historycznej kotwicy bez Recorder,
- fingerprint aktywnych kotwic i wymuszenie pełnego rebuilda po zmianie zestawu kotwic.

### Zweryfikowane na działającej instalacji

Potwierdzono:

- pełną synchronizację historycznych wiadomości Outlook,
- parser faktur v3 na rzeczywistych nowszych i starszych dokumentach,
- pomijanie niezabezpieczonych dokumentów informacyjnych,
- idempotencję wcześniej przetworzonych faktur,
- fail-closed bez częściowego zapisu,
- atomowy zapis całej paczki i poprawny `invoice_import_guard`,
- klasyfikację kotwic fakturowych jako aktywnych, przesłoniętych, niemonotonicznych lub historycznych bez Recorder,
- brak udziału kotwic fakturowych w kalibracji,
- niezmienność współczynników kalibracji po pełnym imporcie,
- pełny rebuild historii po zmianie zestawu aktywnych kotwic,
- zapis i weryfikację całej historii `duon_gaz:canonical_gas` w Recorder,
- dokładne domknięcie części rozliczonej do gazomierza,
- brak ujemnego zużycia i nierozliczonych rollbacków,
- zachowanie surowych statystyk CO/CWU bez modyfikacji.

## Plan dalszych prac

1. Dodać prostą ręczną ścieżkę **Połącz ponownie Outlook** w UI Home Assistanta.
2. Dodać lub uzupełnić testy przepływu reautoryzacji Device Code tam, gdzie można to zrobić bez zależności od prawdziwego konta Microsoft.
3. Uzupełnić README i release notes o finalne zachowanie 0.3.4.
4. Wykonać końcowy przegląd PR #1 i pozostawić go jako Draft do zakończenia powyższych punktów.
5. Po stabilizacji rozważyć scalenie do `main`.
6. Kolejne osobne etapy: SMS oraz finalna konfiguracja Energy Dashboard.

## Zasady bezpieczeństwa dalszych prac

- żadnych bezpośrednich zapisów SQL,
- nie modyfikować surowych statystyk CO/CWU,
- nie kodować publicznie identyfikatorów punktu odbioru, encji, taryf, odczytów ani dat konkretnej instalacji,
- nie logować hasła PDF, access tokenu ani refresh tokenu,
- nie prosić użytkownika o wklejanie hasła PDF do zgłoszeń lub rozmów,
- nie akceptować nowego formatu faktury przez nadmiernie szerokie regexy bez testu regresyjnego,
- ograniczać restarty Home Assistanta i grupować zmiany przed jednym restartem,
- zachować polskie komunikaty, dokumentację, opisy UI i nowe komunikaty commitów.
