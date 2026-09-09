# SMS z odczytem — 0.5.0-dev.1

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

Rejestracje `mobile_app` są filtrowane jednocześnie po:

- dokładnym `user_id`,
- systemie `Android`,
- obecności `webhook_id`.

Brak dopasowania albo więcej niż jedno urządzenie Android dla tego samego użytkownika powoduje zatrzymanie operacji przed zapisaniem nowego odczytu. Integracja nie wybiera telefonu losowo.

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

Ponowne kliknięcie bez ponownego wpisania wartości nie zapisuje drugiej kotwicy. Integracja rozpoznaje, że bieżąca wartość pola `number` została już zapisana po czasie jej ostatniego wpisania, i jedynie ponawia otwarcie edytora SMS.

Jeżeli użytkownik świadomie ponownie wpisze nawet tę samą wartość, `pending_entered_at` zostaje odświeżone i nowa rzeczywista kotwica może zostać zapisana.

## Numer licznika

Od 0.5.0 konfiguracja Outlook/PDF używa jednego jawnego pola:

```text
meter_number
```

Numer licznika jest przechowywany jako tekst, aby zachować ewentualne zera wiodące, i musi składać się wyłącznie z cyfr.

Ta sama wartość jest używana:

- jako hasło do zaszyfrowanych faktur PDF DUON,
- jako drugi człon treści SMS z odczytem.

Publiczny kod i dokumentacja nie zawierają numeru konkretnej instalacji.

## Przejście z 0.4.2

Nie ma automatycznej migracji starego pola `invoice_pdf_password` do `meter_number`.

Po instalacji 0.5.0 użytkownik wykonuje **Przekonfiguruj**, jawnie wpisuje numer licznika i kończy konfigurację Outlook. Po poprawnym zapisaniu stare pole `invoice_pdf_password` jest usuwane z wpisu konfiguracji.

Do czasu wykonania tej rekonfiguracji automatyczna synchronizacja Outlook nie jest uruchamiana, a przycisk SMS zgłasza brak numeru licznika zamiast zgadywać lub używać starej wartości.

## Spójność z warstwą kanoniczną

Funkcja SMS nie zmienia algorytmu historii kanonicznej, kalibracji, kosztów ani Energy Dashboard. Zapis odczytu korzysta z dotychczasowego `async_confirm_meter()` i dopiero po poprawnym zapisaniu kotwicy uruchamia żądanie otwarcia edytora SMS.

Jeżeli zapis odczytu powiedzie się, ale otwarcie aplikacji SMS nie powiedzie się, odczyt pozostaje zapisany. Rekord odczytu otrzymuje diagnostyczny status błędu SMS; nie jest wycofywana poprawna fizyczna kotwica.

## Testy

Reguły niezależne od runtime Home Assistanta mają testy jednostkowe obejmujące:

- zaokrąglenie stanu do pełnych m³,
- zachowanie zer wiodących numeru licznika,
- odrzucenie niecyfrowego numeru,
- format treści SMS,
- parametry intentu Android,
- wybór wyłącznie urządzenia Android właściwego użytkownika,
- ponowienie SMS bez duplikowania już zapisanej wartości,
- świadome ponowne wpisanie tej samej wartości jako nowej kotwicy.

## Walidacja na działającej instalacji

Potwierdzono:

- rekonfigurację jawnego numeru licznika,
- ponowne uwierzytelnienie Outlook po rekonfiguracji,
- jednoznaczne przypisanie Android Mobile App do użytkownika,
- zapis rzeczywistej ręcznej kotwicy,
- domknięcie bieżącej estymacji do fizycznego gazomierza,
- przeliczenie kalibracji po nowej kotwicy,
- poprawne zaokrąglenie stanu do pełnych m³,
- otwarcie właściwego wątku SMS na telefonie użytkownika,
- przygotowanie poprawnej treści bez automatycznego wysłania.

Pierwszy test ujawnił, że ponowienie przycisku po ekranie nadania uprawnienia Android tworzyło drugą kotwicę o tej samej wartości. Błąd został poprawiony przez idempotentne ponawianie SMS opisane wyżej.
