# Otwarte decyzje

Poniższe tematy nie zostały jeszcze rozstrzygnięte. Lista zapobiega przypadkowemu utrwalaniu założeń jako wymagań.

## Agent, autonomia i role

- **OPEN:** katalog narzędzi agenta i dozwolonych działań.
- **OPEN:** działania wymagające potwierdzenia właściciela firmy.
- **OPEN:** progi kwotowe, ryzyka i wyjątki wymagające eskalacji.
- **OPEN:** możliwość kontaktu z klientem, rezerwacji zasobów i wysłania oferty przez agenta.
- **OPEN:** ślad audytowy i sposób cofania działań agenta.
- **OPEN:** pełna rola właściciela oraz innych użytkowników systemu.

## Peter i oględziny

- **USTALONE dla M2 DEMO:** minimalną rolę wykonawcy oględzin, przydział po skillu i dostępności, zakres raportu oraz przejście do `READY_FOR_PLANNING` opisuje decyzja [0003](0003-minimalna-petla-ogledzin-demo.md).
- **OPEN poza M2:** docelowa rola Petera, jego uprawnienia, ręczny przydział oraz zastępstwo.
- **OPEN poza M2:** kryteria wymagające oględzin inne niż wynik `job-assessment-v1`.
- **OPEN poza M2:** zdjęcia, podpisy, sposób akceptacji i pełna zawartość protokołu oględzin.
- **OPEN poza M2:** wpływ oględzin na plan, wycenę i harmonogram.

## Przebieg zlecenia i Plan A/B

- **USTALONE dla M3 DEMO:** deterministyczne kryteria tworzenia wykonalnych Planów A/B i przejście do `PLANS_READY_FOR_REVIEW` opisuje decyzja [0004](0004-deterministyczne-warianty-planowania-demo.md).
- **OPEN:** zatwierdzona kolejność etapów i możliwe przejścia.
- **OPEN:** kanały przyjęcia zlecenia oraz wymagane dane.
- **OPEN poza M3:** docelowa semantyka, ranking i kryteria generowania Planów A/B.
- **OPEN:** osoba wybierająca wariant i warunki przełączenia planu.
- **OPEN:** bramki akceptacji planu, oferty, realizacji i zamknięcia.

## Pracownicy i zasoby

- **USTALONE dla M3 DEMO:** minimalne reguły skillów, aktywności, dostępności, konfliktów i pojazdów opisuje decyzja [0004](0004-deterministyczne-warianty-planowania-demo.md).
- **OPEN:** schemat profilu pracownika i źródło danych.
- **OPEN:** model dostępności, czasu, nieobecności i rezerwacji.
- **OPEN:** taksonomia oraz poziomy kompetencji i uprawnień.
- **OPEN:** model pojazdów, transportu, narzędzi i konfliktów zasobów.
- **OPEN:** reguły przypisywania i ręcznego nadpisania planu.

## Wycena i warunki pracy

- **OPEN:** stawki, waluta, źródła cen i wersjonowanie cenników.
- **OPEN:** formuły robocizny, materiałów, kosztów, VAT i marży.
- **OPEN:** definicja marży, rabaty, minima i zaokrąglenia.
- **OPEN:** definicja pracy wieczornej, weekendowej i świątecznej.
- **OPEN:** dodatki, mnożniki, limity oraz wymagane zgody.

## WERKcrew Field i Nachtrag

- **USTALONE dla M2 DEMO:** responsywny widok briefu i raportu oględzin jest funkcjonalnym wycinkiem Field opisanym w decyzji [0003](0003-minimalna-petla-ogledzin-demo.md).
- **OPEN poza M2:** nazwa, cel i zawartość każdego z czterech docelowych ekranów MVP.
- **OPEN:** role, uprawnienia, stany błędów oraz wymagania offline.
- **OPEN:** miejsce zgłaszania dodatkowego zakresu w Field.
- **OPEN:** dowody, wycena, zgody i wersjonowanie Nachtrag.
- **OPEN:** możliwość rozpoczęcia pilnej pracy przed formalną akceptacją.

## MVP i jury

- **OPEN:** zamknięty backlog oraz kryteria ukończenia MVP.
- **OPEN:** elementy faktycznie odłożone poza MVP.
- **OPEN:** scenariusz, dane, czas i kryteria sukcesu prezentacji dla jury.
- **OPEN:** wymagany poziom interaktywności demonstracji.

## Technologia i eksploatacja

- **OPEN:** wersje Pythona, bibliotek i konkretny wariant Claude Sonnet.
- **OPEN:** konfiguracja, region, uprawnienia i limity Amazon Bedrock.
- **OPEN:** schemat SQLite, migracje i relacja danych biznesowych do sesji agenta.
- **OPEN:** retencja, backup, szyfrowanie, współbieżność i usuwanie danych.
- **OPEN:** autoryzacja, role techniczne, hosting i topologia produkcyjna.
- **OPEN:** integracje z systemami zewnętrznymi.
