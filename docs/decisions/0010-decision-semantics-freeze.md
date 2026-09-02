# 0010 — Decision Semantics Freeze

- **Status:** USTALONE
- **Data:** 2026-09-02
- **Zakres:** prawda, władza OWNER, wyjątki, sprzeciw, recovery i komunikacja

## Kontekst

Test DOM/KLINIKA/LOFT o 10:17 ujawnił, że wcześniejsze kontrakty mogły mieszać ocenę faktów z decyzją OWNER. Szczególnie niebezpieczne były dwa skróty:

- `FORCED` mogło zostać odczytane jako zmiana niewykonalnego działania w wykonalne;
- `RECOVERY_REQUIRED` mogło oznaczać jednocześnie pomiar zdrowia firmy i decyzję OWNER o uruchomieniu recovery.

Historia sprawdziła również sprzeciw pracownika oraz sytuację, w której osiągnięcie celu wymagałoby fałszywej obietnicy albo pozornej zgody.

## Decyzja

1. `TRUTH`, `AUTHORITY` i `EXECUTION` są rozdzielone. Uprawnienie do wyboru działania nie zmienia faktów ani oceny wykonalności.
2. Ocena obietnicy przechowuje osobno:
   - wykonalność techniczną,
   - relację do polityki,
   - status zgody na miękki wyjątek,
   - wynik wykonawczy.
3. `FORCED` jest wyłącznie wynikiem pochodnym: działanie jest wykonalne, wychodzi poza miękką politykę i otrzymało jawny override OWNER. `FORCED` nie legalizuje BHP, braku wymaganej zgody, braku uprawnień, twardego zakazu ani fizycznej niewykonalności.
4. Pomiar zdrowia, sygnał potrzeby recovery, odpowiedź OWNER i postawa firmy są zapisywane osobno. OWNER może odrzucić recovery, ale nie może przemalować obserwacji.
5. Sprzeciw klasyfikuje się według treści, nie stanowiska osoby. Preferencja nie daje veta. Nowy fakt uruchamia ponowną ocenę. Limit dostępności blokuje zależne działanie. Niezweryfikowana obawa techniczna wstrzymuje konkretną metodę do sprawdzenia. Znany zakaz BHP blokuje wariant.
6. Brak danych ma zakres. Zatrzymuje tylko ocenę lub działanie zależne od brakującego faktu.
7. LLM kończy na `DRAFT_TEXT`. Silnik deterministyczny stosuje `NO_DECEPTION`: nie wysyła fałszywego faktu, fałszywej pewności, pozornej zgody, nieprawdziwego uprawnienia ani komunikatu z istotnym pominięciem.
8. System odróżnia tekst proponowany od wypowiedzi rzeczywiście złożonej poza systemem. Nie może wysłać niedopuszczalnej obietnicy, ale ma obowiązek zapisać, że OWNER ją faktycznie złożył.
9. `ABSTAIN` dotyczy zależnego działania, którego nie można rzetelnie ocenić. `FORBIDDEN` dotyczy znanego twardego zakazu. Globalny `HALT` jest zarezerwowany dla awarii kontroli lub krytycznego zagrożenia całego przepływu.

## Konsekwencje

- OWNER może świadomie wybrać niepopularny lub błędny wariant w granicach swojej władzy.
- Override pozostaje częścią śladu i nigdy nie nadpisuje wcześniejszej oceny.
- Późniejszy sukces lub porażka nie zmienia oceny dostępnej o 10:17; jest osobnym `OUTCOME_EVENT`.
- Brak zatwierdzonych parametrów P-* uruchamia fail-safe, ale nie usuwa znanych faktów lokalnych.
- Constitution v1.1, State & Transition Matrix v1.1 i Decision Assurance Pack v0.1 są kontraktami wdrożeniowymi tej decyzji.
- Istniejące decyzje M6/M7 zatwierdzające plan lub replan nie są automatycznie override'em miękkiego wyjątku. Taki override wymaga jawnego zakresu i ponownej walidacji semantycznej.
- Obecny publiczny `AgentTraceEvent` pozostaje timeline'em M7. Pełny `decision_record` ma wersjonowany JSON Schema; jego produkcyjna migracja i powiązanie z timeline'em pozostają jawnie OPEN.

## Poza zakresem

Ten ADR nie ustala progów pieniężnych, limitów WIP, długości histerezy, czasu odpowiedzi ani delegacji OWNER. Te wartości należą do Company Policy Profile.
