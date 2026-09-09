# DUON Gaz 0.5.0

## Zakres wydania

0.5.0 dodaje przygotowanie SMS z ręcznym odczytem gazomierza na właściwym telefonie użytkownika oraz upraszcza konfigurację numeru licznika.

## SMS z odczytem

Po wpisaniu dokładnego stanu gazomierza przycisk **Zapisz i wyślij SMS**:

1. zapisuje odczyt jako ręczną fizyczną kotwicę,
2. zaokrągla stan do pełnych m³ metodą `ROUND_HALF_UP`,
3. buduje treść `<stan> <numer licznika>`,
4. identyfikuje użytkownika Home Assistanta przez `context.user_id`,
5. znajduje dokładnie jeden Android Home Assistant Mobile App przypisany do tego użytkownika,
6. otwiera systemowy edytor SMS z gotowym odbiorcą i treścią,
7. pozostawia faktyczne wysłanie do ręcznego zatwierdzenia.

Integracja nie wysyła SMS samodzielnie.

Przy pierwszym użyciu Android może poprosić o uprawnienie „wyświetlanie nad innymi aplikacjami”. Ponowne kliknięcie z identycznym odczytem w ciągu 5 minut jest traktowane jako retry SMS i nie tworzy drugiej kotwicy. Identyczny stan po upływie 5 minut jest traktowany jako nowy rzeczywisty odczyt, co pozwala zapisać także poprawny przedział z zerowym fizycznym zużyciem.

## Numer licznika

Konfiguracja Outlook/PDF używa od 0.5.0 jednego jawnego pola `meter_number`.

Ta sama wartość jest używana:

- jako hasło do zaszyfrowanych faktur PDF DUON,
- jako identyfikator licznika w treści SMS.

Numer jest przechowywany jako tekst, aby zachować ewentualne zera wiodące. Stare pole `invoice_pdf_password` nie jest automatycznie migrowane. Po aktualizacji z 0.4.2 należy wykonać **Przekonfiguruj**, wpisać numer licznika i ponownie zakończyć Device Code Flow Microsoft.

## Walidacja na działającej instalacji

Potwierdzono:

- rekonfigurację jawnego numeru licznika,
- ponowne uwierzytelnienie Outlook,
- jednoznaczne przypisanie Android Mobile App do użytkownika,
- zapis rzeczywistej ręcznej kotwicy,
- domknięcie bieżącej estymacji do fizycznego gazomierza,
- przeliczenie kalibracji po nowej kotwicy,
- poprawne zaokrąglenie stanu do pełnych m³,
- otwarcie właściwego wątku SMS na telefonie użytkownika,
- poprawną treść wiadomości bez automatycznego wysłania,
- naprawę duplikowania kotwicy przy technicznym ponowieniu po ekranie uprawnienia Android.

## Bez zmian w warstwie rozliczeniowej

Funkcja SMS nie zmienia algorytmu historii kanonicznej, warstwy kosztowej, Energy Dashboard ani audytu współczynnika konwersji. Nadal obowiązują wszystkie zasady bezpieczeństwa 0.4.2: brak bezpośrednich zapisów SQL, surowe statystyki CO/CWU pozostają nietknięte, a publikacja odbywa się przez publiczne API Recorder.
