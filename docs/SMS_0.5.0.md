# SMS z odczytem — 0.5.0

Dokument opisuje stabilną funkcję SMS w wydaniu 0.5.0.

## Cel

Po ręcznym wpisaniu dokładnego stanu gazomierza przycisk **Zapisz i wyślij SMS**:

1. zapisuje dokładny odczyt jako fizyczną kotwicę historii DUON Gaz,
2. przygotowuje wartość zgłaszaną dostawcy jako pełne m³ z zaokrągleniem `ROUND_HALF_UP`,
3. buduje treść SMS w formacie `<stan> <numer licznika>`,
4. ustala użytkownika Home Assistanta, który nacisnął przycisk,
5. znajduje dokładnie jedno urządzenie Android Home Assistant Mobile App przypisane do tego użytkownika,
6. otwiera na tym telefonie systemowy edytor SMS z gotowym odbiorcą i treścią,
7. pozostawia faktyczne wysłanie SMS do świadomego zatwierdzenia przez użytkownika.

Integracja nie wysyła SMS samodzielnie.

## Identyfikacja użytkownika i telefonu

Pierwszeństwo ma `context.user_id` osoby naciskającej przycisk. Jeżeli kontekst przycisku nie zawiera użytkownika, awaryjnie używany jest identyfikator użytkownika zapisany przy ręcznym wpisaniu stanu gazomierza.

Rejestracje `mobile_app` są filtrowane po dokładnym `user_id`, systemie `Android` i obecności `webhook_id`. Brak dopasowania albo więcej niż jedno urządzenie Android dla tego samego użytkownika powoduje zatrzymanie operacji przed losowym wyborem.

Po wybraniu rejestracji używana jest usługa powiadomień Mobile App odpowiadająca dokładnie jej `webhook_id`.

## Otwieranie SMS na Androidzie

Do Home Assistant Mobile App przekazywane jest `command_activity` z:

```text
intent_action = android.intent.action.SENDTO
intent_uri    = smsto:<numer odbiorcy>
intent_extras = sms_body:<zakodowana treść>:String.urlencoded
```

Pierwsze użycie może wymagać zezwolenia Home Assistant Mobile App na „wyświetlanie nad innymi aplikacjami”.

## Ochrona przed technicznym duplikatem

Jeżeli Android przy pierwszym użyciu pokaże ekran uprawnienia zamiast edytora SMS, użytkownik może ponownie nacisnąć przycisk.

Finalna reguła 0.5.0:

- identyczny odczyt ponowiony w ciągu 5 minut od ostatniej kotwicy jest technicznym retry SMS i nie tworzy duplikatu,
- identyczny odczyt po upływie 5 minut jest normalnym nowym fizycznym odczytem i tworzy nową kotwicę,
- inna wartość zawsze jest normalnym nowym odczytem.

Pozwala to zachować wartościowe późniejsze kotwice nawet wtedy, gdy fizyczny licznik przez pewien czas się nie zmienił.

## Numer licznika

Od 0.5.0 konfiguracja Outlook/PDF używa jednego jawnego pola `meter_number`. Wartość jest przechowywana jako tekst, aby zachować ewentualne zera wiodące.

Ta sama wartość służy jako hasło do zaszyfrowanych faktur PDF DUON oraz jako drugi człon treści SMS z odczytem. Publiczny kod i dokumentacja nie zawierają numeru konkretnej instalacji.

## Przejście z 0.4.2

Nie ma automatycznej migracji starego `invoice_pdf_password`. Po aktualizacji użytkownik wykonuje **Przekonfiguruj**, wpisuje numer licznika i ponownie kończy konfigurację Outlook przez Microsoft Device Code Flow.

## Spójność z warstwą kanoniczną

Funkcja SMS nie zmienia algorytmu historii kanonicznej, kosztów, Energy Dashboard ani audytu. Zapis odczytu korzysta z istniejącego `async_confirm_meter()`. Jeżeli zapis odczytu powiedzie się, ale otwarcie SMS nie, poprawna fizyczna kotwica pozostaje zapisana, a rekord otrzymuje diagnostyczny status błędu SMS.

## Walidacja

Na działającej instalacji potwierdzono:

- rekonfigurację jawnego numeru licznika,
- ponowne uwierzytelnienie Outlook,
- jednoznaczne przypisanie Android Mobile App do użytkownika,
- zapis rzeczywistej ręcznej kotwicy,
- domknięcie estymacji do fizycznego gazomierza,
- przeliczenie kalibracji po nowej kotwicy,
- poprawne zaokrąglenie stanu,
- otwarcie właściwego wątku SMS,
- poprawną treść bez automatycznego wysłania,
- ochronę przed szybkim technicznym duplikatem kotwicy.
