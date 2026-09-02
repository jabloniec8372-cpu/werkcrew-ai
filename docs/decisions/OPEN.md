# Otwarte decyzje

Poniższe tematy nie zostały jeszcze rozstrzygnięte. Lista zapobiega przypadkowemu utrwalaniu założeń jako wymagań.

## Agent, autonomia i role

- **USTALONE dla M4 DEMO:** ograniczony katalog sześciu narzędzi, dwa zatrzymania przy człowieku i publiczny activity timeline opisuje decyzja [0005](0005-orkiestracja-agenta-strands-m4.md).
- **OPEN poza M4:** docelowy katalog narzędzi agenta i dozwolonych działań.
- **USTALONE semantycznie:** OWNER może zatwierdzać wyłącznie miękkie wyjątki bez zmiany faktów; granicę opisuje [0010](0010-decision-semantics-freeze.md). **OPEN:** pełna lista operacji wymagających jego potwierdzenia.
- **USTALONE dla M6 DEMO:** owner gate, realny Strands interrupt/resume i granice persistence opisuje decyzja [0007](0007-real-owner-decision-interrupt-resume-m6.md).
- **OPEN:** progi kwotowe, ryzyka i wyjątki wymagające eskalacji.
- **USTALONE semantycznie:** każdy tekst LLM jest `DRAFT_TEXT`, a wysyłka podlega `NO_DECEPTION`. **OPEN:** które klasy poprawnie zwalidowanych wiadomości agent może wysłać bez dodatkowej akceptacji.
- **USTALONE jako kontrakt:** minimalne pola śladu opisuje Decision Assurance Pack v0.1. **OPEN:** produkcyjna trwałość, migracja i powiązanie pełnego `decision_record` z publicznym `AgentTraceEvent`, retencja oraz sposób cofania działań.
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
- **USTALONE semantycznie:** `ABSTAIN`, `FORBIDDEN`, miękki override i `FORCED` opisuje Matrix v1.1. **OPEN:** mapowanie wszystkich operacji produktu na te bramki oraz osobny typ gate dla miękkiego wyjątku.

## Pracownicy i zasoby

- **USTALONE dla M3 DEMO:** minimalne reguły skillów, aktywności, dostępności, konfliktów i pojazdów opisuje decyzja [0004](0004-deterministyczne-warianty-planowania-demo.md).
- **USTALONE dla M8.0 DEMO:** dokładna macierz 6×24, skala 0–3, synthetic vehicle capabilities i preference policy opisuje decyzja [0009](0009-frozen-m8-business-configuration.md).
- **OPEN:** schemat profilu pracownika i źródło danych.
- **OPEN:** model dostępności, czasu, nieobecności i rezerwacji.
- **OPEN poza M8.0 DEMO:** produkcyjna taksonomia oraz poziomy kompetencji i uprawnień.
- **OPEN:** model pojazdów, transportu, narzędzi i konfliktów zasobów.
- **OPEN:** reguły przypisywania i ręcznego nadpisania planu.

## Wycena i warunki pracy

- **USTALONE dla M5 DEMO:** jawne syntetyczne stawki, wzory, kategorie, rounding, snapshoty i tax boundary opisuje decyzja [0006](0006-deterministic-plan-pricing-m5.md).
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

- **USTALONE dla M4 DEMO:** `strands-agents==1.50.2`; sposób konfiguracji providera opisuje decyzja [0005](0005-orkiestracja-agenta-strands-m4.md).
- **OPEN poza M4:** docelowe wersje Pythona i pozostałych bibliotek oraz konkretny wariant Claude Sonnet.
- **OPEN:** konfiguracja, region, uprawnienia i limity Amazon Bedrock.
- **USTALONE dla M7 DEMO:** schemat SQLite, ownership business facts, migracja v1 i relacja do sesji Strands opisuje ADR [0008](0008-persistent-multi-job-dispatch-recovery-m7.md).
- **OPEN poza M7:** produkcyjna multi-process persistence oraz transakcyjne powiązanie SQLite z zewnętrzną trwałością sesji.
- **OPEN:** retencja, backup, szyfrowanie, współbieżność i usuwanie danych.
- **OPEN:** autoryzacja, role techniczne, hosting i topologia produkcyjna.
- **OPEN:** integracje z systemami zewnętrznymi.
