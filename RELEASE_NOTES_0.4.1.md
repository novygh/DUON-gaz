# DUON Gaz 0.4.1

## Najważniejsza zmiana

Wydanie 0.4.1 dodaje jedną encję informacyjną: **Audyt współczynnika konwersji**. Jej zadaniem jest wykrywanie długoterminowego dryfu relacji pomiędzy współczynnikiem kWh/m³ z faktur DUON a lokalnym profilem Ariston + gazomierz.

Encja jest wyłącznie diagnostyczna. Nie uczestniczy w rozliczeniach, historii kanonicznej, kalibracji CO/CWU ani Energy Dashboard.

## Metoda audytu

Audyt:

- używa wyłącznie faktur, których oba wskazania gazomierza można powiązać z dokładnymi ręcznymi odczytami,
- nie używa arbitralnej godziny dla odczytów podanych na fakturze tylko z dokładnością do dnia,
- korzysta z istniejącej niezależnej kalibracji CO/CWU,
- wykorzystuje relację `provisional_m3 / physical_m3` z kanonicznych przedziałów przed normalizacją do gazomierza,
- wyznacza stałą historyczną referencję kWh/m³ jako medianę ważoną zużyciem,
- pomija okresy bez dwóch dokładnych lokalnych granic zamiast zgadywać,
- zachowuje pełną historię punktów audytu w atrybucie encji.

## Znaczenie znaku

Stan encji jest skumulowanym odchyleniem kosztu zmiennego w PLN liczonym z perspektywy użytkownika:

- wartość dodatnia oznacza korzyść użytkownika względem lokalnej referencji,
- wartość ujemna oznacza koszt wyższy niż lokalna referencja.

Ta sama konwencja znaku jest stosowana do różnicy PLN i różnicy procentowej w historii audytu.

## Ograniczenia interpretacji

Audyt nie jest laboratoryjnym pomiarem ciepła spalania i nie stanowi dowodu nieprawidłowego rozliczenia. Bez niezależnego kalorymetru nie można wyznaczyć bezwzględnego błędu współczynnika DUON. Wskaźnik służy do wykrywania odchyleń i dryfu względem własnej historycznej relacji lokalnej.

## Walidacja

Przed wydaniem stabilnym potwierdzono na działającej instalacji, że:

- używane okresy dokładnie domykają `m³ faktura = m³ lokalne`,
- okresy bez dwóch dokładnych ręcznych granic są pomijane,
- znikają sztuczne skoki powodowane przez arbitralne granice czasu odczytu,
- saldo może rosnąć i maleć zgodnie z kolejnymi odchyleniami,
- encja nie wpływa na istniejące obliczenia i publikację danych kanonicznych.

## Zgodność

Wydanie 0.4.1 zachowuje architekturę i zachowanie warstwy kanonicznej oraz kosztowej z 0.4.0. Zmiana jest dodatkiem diagnostycznym.
