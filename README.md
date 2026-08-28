# DUON Gaz v0.1.0 — równoległy test

Wersja 0.1 nie zastępuje obecnych helperów. Ma działać równolegle.

## Co robi
- wybiera dwa sensory Ariston: CO i CWU (kWh),
- pozwala wpisać fizyczny stan gazomierza w m³,
- zapisuje potwierdzone odczyty z timestampem,
- po dwóch odczytach wylicza korektę Aristona,
- estymuje bieżący stan gazomierza,
- estymuje rozdział CO/CWU,
- estymuje koszt zmienny na ostatnich znanych stawkach,
- dolicza bieżącą, proporcjonalną część miesięcznych opłat stałych.

## Czego jeszcze nie robi
- nie wysyła SMS,
- nie czyta Gmaila,
- nie importuje faktur PDF,
- nie poprawia statystyk historycznych,
- nie zastępuje obecnego Energy Dashboard.

## Instalacja
Skopiuj katalog `custom_components/duon_gaz` do `/config/custom_components/duon_gaz`
i uruchom ponownie Home Assistant.

Następnie:
Ustawienia → Urządzenia i usługi → Dodaj integrację → DUON Gaz

Domyślne parametry w formularzu odpowiadają ostatniej przeanalizowanej fakturze:
- 11.334 kWh/m³
- 0.22684 PLN/kWh netto
- 0.0854 PLN/kWh netto
- 8.00 PLN/m-c netto abonament
- 8.39 PLN/m-c netto dystrybucja stała
- VAT 23%

## Pierwszy test
1. Nie usuwaj żadnych obecnych helperów.
2. Wpisz aktualny fizyczny stan gazomierza do `number.duon_gaz_stan_gazomierza_do_wyslania`.
3. Naciśnij `button.duon_gaz_zapisz_odczyt_gazomierza`.
4. Sprawdź, czy zapisany odczyt i timestamp pojawiły się w atrybutach sensora `Gaz zużycie`.
5. Na razie NIE dodawaj nowych sensorów do Energy Dashboard.

Po drugim rzeczywistym odczycie integracja będzie mogła policzyć pierwszy rzeczywisty współczynnik korekcyjny Aristona.
