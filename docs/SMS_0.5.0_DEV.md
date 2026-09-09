# SMS z odczytem — 0.5.0-dev.3

Dokument opisuje funkcję rozwijaną na gałęzi `feature/sms-compose`. Stabilnym wydaniem pozostaje 0.4.2 do czasu walidacji funkcji na działającej instalacji.

## Cel

Po ręcznym wpisaniu dokładnego stanu gazomierza przycisk **Zapisz i wyślij SMS** ma:

1. zapisać dokładny odczyt jako fizyczną kotwicę historii DUON Gaz,
2. przygotować wartość zgłaszaną dostawcy jako pełne m³ z zaokrągleniem `ROUND_HALF_UP`,
3. zbudować treść SMS w formacie `<stan> <numer licznika>`,
4. ustalić użytkownika Home Assistanta, który nacisnął przycisk,
5. znaleźć dokładnie jedno urządzenie Android Home Assistant Mobile App przypisane do tego użytkownika,
6. otworzyć na tym telefonie systemowy edytor SMS z gotowym odbiorcą i treścią,
7. pozostawić faktyczne wysłanie SMS do świadomego zatwierdzenia przez użytkownika.

Integracja nie wysyła SMS samodzielnie i nie wymaga uprawnienia Android do cichego wysyłania wiadomości.

## Identyfikacja użytkownika i telefonu

Pierwszeństwo ma `context.user_id` osoby naciskającej przycisk. Jeżeli kontekst przycisku nie zawiera użytkownika, awaryjnie używany jest identyfikator użytkownika zapisany przy ręcznym wpisaniu stanu gazomierza.

Rejestracje `mobile_app` są filtrowane jednocześnie po dokładnym `user_id`, systemie `Android` i obecności `webhook_id`. Brak dopasowania albo więcej niż jedno urządzenie Android dla tego samego użytkownika powoduje zatrzymanie operacji przed zapisaniem nowego odczytu. Integracja nie wybiera telefonu losowo.

Po wybraniu rejestracji używana jest usługa powiadomień Mobile App odpowiadająca dokładnie jej `webhook_id`. Dzięki temu polecenie nie jest rozsyłane do innych telefonów.

## Otwieranie SMS na Androidzie

Do Home Assistant Mobile App przekazywane jest polecenie `command_activity` z:

```text
intent_action = android.intent.action.SENDTO
intent_uri    = smsto:<numer odbiorcy>
intent_extras = sms_body:<zakodowana treść>:String.urlencoded
```

Pierwsze użycie `command_activity` może wymagać zezwolenia aplikacji Home Assistant na wyświetlanie nad innymi aplikacjami.

### Ponowienie po ekranie uprawnienia

Żądanie `command_activity` może zostać przyjęte przez Home Assistanta, mimo że Android zamiast edytora SMS pokaże najpierw ekran nadania uprawnienia „wyświetlanie nad innymi aplikacjami”. Użytkownik może wtedy ponownie nacisnąć **Zapisz i wyślij SMS**.

Aby taki retry nie tworzył drugiej kotwicy, integracja deduplikuje wyłącznie **identyczny odczyt zapisany w ciągu ostatnich 5 minut**. W tym krótkim oknie ponowne kliknięcie tylko ponawia otwarcie edytora SMS.

Po upływie 5 minut kolejne kliknięcie jest traktowane jako nowy rzeczywisty odczyt i może utworzyć nową kotwicę również wtedy, gdy stan licznika się nie zmienił i użytkownik nie edytował pola. To ważne, ponieważ niezmieniony fizyczny licznik w późniejszym czasie nadal jest wartościową informacją ograniczającą model.

## Numer licznika

Od 0.5.0 konfiguracja Outlook/PDF używa jednego jawnego pola `meter_number`. Numer licznika jest przechowywany jako tekst, aby zachować ewentualne zera wiodące, i musi składać się wyłącznie z cyfr.

Ta sama wartość jest używana jako hasło do zaszyfrowanych faktur PDF DUON oraz jako drugi człon treści SMS z odczytem. Publiczny kod i dokumentacja nie zawierają numeru konkretnej instalacji.

## Przejście z 0.4.2

Nie ma automatycznej migracji starego pola `invoice_pdf_password` do `meter_number`. Po instalacji 0.5.0 użytkownik wykonuje **Przekonfiguruj**, jawnie wpisuje numer licznika i kończy konfigurację Outlook. Po poprawnym zapisaniu stare pole `invoice_pdf_password` jest usuwane z wpisu konfiguracji.

Do czasu wykonania tej rekonfiguracji automatyczna synchronizacja Outlook nie jest uruchamiana, a przycisk SMS zgłasza brak numeru licznika zamiast zgadywać lub używać starej wartości.

## Spójność z warstwą kanoniczną

Funkcja SMS nie zmienia algorytmu historii kanonicznej, kalibracji, kosztów ani Energy Dashboard. Zapis odczytu korzysta z dotychczasowego `async_confirm_meter()` i dopiero po poprawnym zapisaniu kotwicy uruchamia żądanie otwarcia edytora SMS.

Jeżeli zapis odczytu powiedzie się, ale otwarcie aplikacji SMS nie powiedzie się, odczyt pozostaje zapisany. Rekord odczytu otrzymuje diagnostyczny status błędu SMS; nie jest wycofywana poprawna fizyczna kotwica.

## Testy

Reguły niezależne od runtime Home Assistanta mają testy jednostkowe obejmujące zaokrąglenie stanu do pełnych m³, zachowanie zer wiodących numeru licznika, odrzucenie niecyfrowego numeru, format treści SMS, parametry intentu Android, wybór właściwego Androida, retry tej samej wartości w krótkim oknie bez duplikatu oraz utworzenie nowej kotwicy dla tej samej wartości po upływie okna.

## Walidacja na działającej instalacji

Potwierdzono rekonfigurację jawnego numeru licznika, ponowne uwierzytelnienie Outlook, jednoznaczne przypisanie Android Mobile App do użytkownika, zapis rzeczywistej ręcznej kotwicy, domknięcie bieżącej estymacji do fizycznego gazomierza, przeliczenie kalibracji po nowej kotwicy, poprawne zaokrąglenie stanu do pełnych m³, otwarcie właściwego wątku SMS na telefonie użytkownika oraz przygotowanie poprawnej treści bez automatycznego wysłania.

Pierwszy test ujawnił duplikowanie kotwicy przy ponownym kliknięciu po ekranie uprawnienia Android. W 0.5.0-dev.2 deduplikacja była związana z ponowną edycją pola Number, co okazało się zbyt szerokie: późniejsze kliknięcie z niezmienionym fizycznym stanem również powinno utworzyć nową kotwicę. W 0.5.0-dev.3 deduplikacja została ograniczona do 5-minutowego okna retry.
