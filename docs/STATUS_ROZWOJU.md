# Status rozwoju DUON Gaz

Dokument opisuje stan gałęzi rozwojowej `feature/store-v2-recorder-sums` i plan dojścia do stabilnego wydania. Jest przeznaczony jako techniczny punkt odniesienia dla dalszych prac nad integracją.

Aktualny etap: **0.3.4 — automatyczny import faktur z Microsoft Outlook / Graph**.

> [!IMPORTANT]
> Gałąź rozwojowa i PR #1 nadal są wersją roboczą. Nie należy scalać ich do `main`, dopóki nie zostaną zakończone testy parsera starszych faktur, bezpiecznika importu oraz końcowy audyt historii kanonicznej.

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
- przechodzi do pełnej przebudowy, gdy zmieni się fizyczna podstawa historii.

## Kalibracja CO/CWU

Kalibracja jest instalacyjna i nie zawiera publicznych wartości właściwych dla konkretnego kotła lub domu.

Nowa instalacja zaczyna od neutralnych współczynników technicznych. Po zgromadzeniu wystarczającej liczby dokładnych przedziałów integracja wyznacza osobne współczynniki CO i CWU metodą odpornej regresji dwóch składowych.

### Zasada dla faktur

Odczyty z faktury mają dokładność dzienną i nie znają fizycznej godziny odczytu. Dlatego:

- mogą służyć jako zaufane kotwice historii, jeżeli literalny typ odczytu jest `Rozliczeniowy`,
- **nie mogą uczestniczyć w uczeniu kalibracji CO/CWU**,
- zgodny ręczny odczyt w pobliżu ma pierwszeństwo i powoduje audytowe oznaczenie kotwicy fakturowej jako przesłoniętej.

Podczas testu 0.3.4 wykryto przypadek, w którym jedna kotwica dzienna weszła do kalibracji. Dane nie zostały utracone; błąd został rozpoznany, kalibracja przywrócona do stanu sprzed importu, a importer zmieniony tak, aby nowe kotwice fakturowe były wykluczane z kalibracji.

Przed stabilnym wydaniem należy dopilnować, aby odpowiadająca temu migracja/naprawa runtime znajdowała się również w kodzie repozytorium, a nie wyłącznie w instalacji testowej.

## Faktury PDF

Parser używa `pypdf`, bez OCR. Dane przed zapisem są walidowane między innymi przez:

- zgodność różnicy wskazań gazomierza ze zużyciem m³,
- zgodność m³ w pozycji gazowej z tabelą odczytów,
- zgodność energii rozliczeniowej ze współczynnikiem konwersji.

Ponowny import tego samego numeru faktury jest idempotentny.

Starsze faktury DUON występują w kilku wariantach układu tekstu. Aktualny parser rozpoznaje nowszy wariant, ale wymaga rozszerzenia o starsze warianty pozycji dystrybucyjnych. Nie należy rozluźniać walidacji tylko po to, aby zaakceptować nieznany dokument — każdy nowy wariant powinien dostać jawny test regresyjny.

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

Access token i refresh token są przechowywane w danych wpisu konfiguracji Home Assistanta. Refresh token jest aktualizowany po odświeżeniu tokenu. Gdy Microsoft wymaga ponownego logowania, integracja uruchamia reautoryzację Home Assistanta.

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
- pominięty dokument informacyjny nie powinien powodować statusu `partial`.

Dokumenty taryfowe można w przyszłości obsłużyć osobnym, opcjonalnym parserem. Nie należy mieszać ich z parserem faktur.

## Bezpiecznik zmiany układu faktury

Automatyczny import ma działać w trybie **fail-closed**.

Przed zapisaniem nowej paczki faktur wykonywany jest preflight. Jeżeli choć jeden nowy zaszyfrowany PDF nie przejdzie parsera lub walidacji:

- nowe faktury z tej synchronizacji nie są częściowo zatwierdzane,
- nie są zmieniane kotwice historii,
- nie jest przeliczana kalibracja,
- nie jest przebudowywana historia kanoniczna,
- problematyczna wiadomość nie jest uznawana za poprawnie przetworzoną,
- zapisywany jest bezpieczny stan diagnostyczny z nazwą pliku, czasem, wersją parsera i krótkim odciskiem dokumentu,
- treść faktury, hasło i tokeny nie są zapisywane diagnostycznie.

Po aktualizacji parsera dokument ma zostać automatycznie podjęty ponownie.

Kod bezpiecznika jest już na gałęzi rozwojowej, ale nie został jeszcze załadowany i zweryfikowany w działającej instalacji po ostatnim restarcie. Następny restart ma być wspólny dla poprawek parsera, runtime i synchronizatora Outlook.

## Reautoryzacja Microsoft

Obecny kod potrafi automatycznie rozpocząć reauth po błędzie tokenu lub autoryzacji Graph. Docelowo przed wydaniem 0.3.4 należy dodatkowo zapewnić użytkownikowi prostą, jednoznaczną ścieżkę **Połącz ponownie Outlook** bez terminala, plików i wchodzenia do Microsoft Entra.

## Stan testów 0.3.4

Potwierdzono na działającej instalacji:

- Device Code Flow dla osobistego konta Microsoft,
- zgodę wyłącznie `Mail.Read` + `offline_access`,
- zapis tokenu i przeładowanie wpisu integracji bez restartu całego HA,
- dostęp Graph do skonfigurowanego folderu,
- pobieranie wielu PDF-ów z wiadomości,
- import nowszych faktur,
- idempotencję numeru faktury,
- pierwszeństwo dokładnego ręcznego odczytu nad zgodną kotwicą z faktury,
- przywrócenie kalibracji po wykrytym błędzie kotwicy dziennej.

Przed migracją Outlook 9 istniejących testów jednostkowych przechodziło poprawnie. Zestaw wymaga rozszerzenia o testy Device Code, preflight Outlook, pomijanie niezabezpieczonych PDF-ów, migrację flag kalibracji i starsze warianty faktur.

## Ważna różnica: repozytorium a instalacja testowa

W czasie testów część naprawy runtime została wdrożona bezpośrednio do instalacji testowej, aby natychmiast przywrócić poprawną kalibrację. Przed dalszym wydaniem trzeba sprawdzić gałąź i przenieść do niej komplet tych zmian:

- automatyczne oznaczenie istniejących kotwic fakturowych jako wykluczonych z kalibracji,
- przeliczenie kalibracji po tej migracji,
- budowanie przedziałów kalibracyjnych po odfiltrowaniu kotwic wykluczonych, tak aby wykluczona kotwica nie rozcinała poprawnego przedziału ręcznego,
- bezpieczna domyślna wartość `exclude_from_calibration=True` dla kotwic fakturowych.

Nie wolno zakładać, że sam fakt poprawnego działania instalacji testowej oznacza, że wszystkie hotfixy są już zapisane w repozytorium.

## Plan dalszych prac

1. **Zsynchronizować runtime z naprawą kalibracji** i dodać test regresyjny.
2. **Poznać starsze warianty układu faktur** bez ujawniania hasła do PDF; poprzednia próba diagnostyki z powłoki dodatku SSH zakończyła się brakiem `pypdf` w tym środowisku, mimo że biblioteka jest zależnością integracji w Home Assistant Core.
3. Rozszerzyć parser o jawnie rozpoznane starsze warianty `distribution_variable_rate` / `distribution_fixed_rate` i dodać fixture/test dla każdego wariantu.
4. Zweryfikować bezpiecznik preflight: nierozpoznana faktura ma zablokować zatwierdzenie całej nowej paczki bez naruszenia istniejących danych.
5. Załadować najnowszy kod Outlook do działającej instalacji i ponowić synchronizację.
6. Sprawdzić, że niezabezpieczone dokumenty informacyjne są pomijane bez błędu, a wszystkie prawdziwe faktury są poprawnie importowane.
7. Wykonać pełny audyt DUON Store: brak duplikatów, spójne okresy rozliczeniowe, prawidłowe klasyfikacje kotwic.
8. Ponownie opublikować i zweryfikować pełną historię `duon_gaz:canonical_gas` po ustabilizowaniu importu. Jest to konieczne, ponieważ wcześniejsza testowa synchronizacja mogła uruchomić przebudowę w czasie, gdy kalibracja była chwilowo nieprawidłowa.
9. Dodać prostą ręczną ścieżkę **Połącz ponownie Outlook** w UI Home Assistanta.
10. Rozszerzyć testy jednostkowe i uruchomić `ha core check`.
11. Uzupełnić README/release notes, zakończyć przegląd PR #1 i dopiero wtedy rozważyć scalenie do `main`.
12. Kolejne osobne etapy: SMS oraz finalna konfiguracja Energy Dashboard.

## Zasady bezpieczeństwa dalszych prac

- żadnych bezpośrednich zapisów SQL,
- nie modyfikować surowych statystyk CO/CWU,
- nie kodować publicznie identyfikatorów punktu odbioru, encji, taryf, odczytów ani dat konkretnej instalacji,
- nie logować hasła PDF, access tokenu ani refresh tokenu,
- nie prosić użytkownika o wklejanie hasła PDF do zgłoszeń lub rozmów,
- nie akceptować nowego formatu faktury przez nadmiernie szerokie regexy bez testu regresyjnego,
- ograniczać restarty Home Assistanta i grupować zmiany przed jednym restartem,
- zachować polskie komunikaty, dokumentację, opisy UI i nowe komunikaty commitów.
