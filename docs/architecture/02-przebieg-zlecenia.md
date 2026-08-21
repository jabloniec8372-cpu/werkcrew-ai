# Przebieg obsługi zlecenia

Poniższa mapa opisuje komplet etapów, które dokumentacja produktu musi objąć. **Nie jest jeszcze zatwierdzonym workflow wykonawczym**: kolejność, bramki akceptacji, automatyzacje i wyjątki mają status **OPEN**, o ile nie zaznaczono inaczej.

## Mapa procesu end-to-end

1. **Przyjęcie zlecenia** — zapis danych klienta, miejsca, opisu potrzeby, oczekiwanego terminu i załączników. Kanały wejścia oraz wymagane pola są **OPEN**.
2. **Interpretacja i kompletność** — agent porządkuje opis i wskazuje brakujące informacje. To, czy może kontaktować się z klientem bez akceptacji, jest **OPEN**.
3. **Decyzja o oględzinach** — system ustala, czy potrzebna jest wizyta na miejscu. Kryteria i rola Petera są **OPEN**.
4. **Oględziny** — zebranie ustalonego protokołu, zdjęć, pomiarów, warunków dostępu i ryzyk. Zakres danych oraz osoba zatwierdzająca są **OPEN**.
5. **Budowa wariantów** — powstają Plan A i Plan B z uwzględnieniem zasobów, terminu, transportu, narzędzi i ograniczeń. Znaczenie obu wariantów jest **OPEN**.
6. **Walidacja zasobów** — deterministyczne reguły sprawdzają dostępność, kompetencje i konflikty. Model danych i reguły konfliktów są **OPEN**.
7. **Wycena** — deterministyczny moduł oblicza robociznę, materiały, pozostałe koszty, VAT i marżę według zapisanych reguł. Konkretne formuły i stawki są **OPEN**.
8. **Akceptacja wewnętrzna** — człowiek zatwierdza lub koryguje wariant i ofertę. Osoba, progi oraz wyjątki są **OPEN**.
9. **Przekazanie oferty i potwierdzenie** — klient otrzymuje zatwierdzoną propozycję, a system zapisuje jej status. Kanał, treść i sposób akceptacji są **OPEN**.
10. **Przygotowanie realizacji** — rezerwacja ludzi, materiałów, transportu i narzędzi oraz przekazanie danych do WERKcrew Field. Reguły rezerwacji są **OPEN**.
11. **Realizacja terenowa** — zespół korzysta z WERKcrew Field, aktualizuje postęp i zgłasza problemy lub zmianę zakresu. Konkretne cztery ekrany są **OPEN**.
12. **Praca dodatkowa / Nachtrag** — zmiana zakresu jest rejestrowana, wyceniana deterministycznie i poddawana ustalonej akceptacji przed realizacją. Szczegółowa ścieżka jest **OPEN**.
13. **Odbiór i zamknięcie** — zapis wykonania, ewentualnych uwag, dokumentacji końcowej oraz danych do rozliczenia. Kryteria zamknięcia są **OPEN**.
14. **Rozliczenie i analiza** — porównanie planu z wykonaniem oraz kosztów planowanych z rzeczywistymi. Zakres MVP tego etapu jest **OPEN**.

## Plan A / Plan B

**USTALONE:** proces ma uwzględniać dwa warianty planu: Plan A i Plan B.

**OPEN:** nie ustalono semantyki wariantów. W szczególności dokumentacja nie zakłada, że Plan A jest automatycznie tańszy, szybszy lub preferowany, ani że Plan B jest planem awaryjnym. Przed implementacją trzeba ustalić:

- cel i różnicę między wariantami,
- kryteria generowania obu planów,
- wymagane elementy każdego planu,
- sposób porównania kosztu, terminu, ryzyka i wykorzystania zasobów,
- kto wybiera wariant i kiedy,
- czy wybrany plan może się automatycznie przełączyć na drugi.

## Praca dodatkowa i Nachtrag

**USTALONE:** system ma uwzględniać dodatkowy zakres prac oraz proces Nachtrag. Kwoty wynikające ze zmiany zakresu muszą pochodzić z deterministycznego modułu wyceny.

**OPEN:** nie ustalono:

- kto może zgłosić zmianę zakresu,
- jakie dowody są obowiązkowe,
- czy prace mogą zacząć się przed akceptacją,
- kto i w jakiej formie zatwierdza zmianę po stronie firmy i klienta,
- jak wersjonowane są plan, wycena i harmonogram,
- jak Nachtrag wpływa na VAT, marżę, termin i dostępność zespołu,
- jak obsłużyć pracę pilną lub wymaganą ze względów bezpieczeństwa.

Do czasu rozstrzygnięcia tych zasad agent może jedynie pomóc zebrać i opisać zmianę; nie należy zakładać, że ma prawo ją zaakceptować.
