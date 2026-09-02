# WERKcrew State & Transition Matrix v1.1

*Normatywna macierz osi, bramek i wyników wykonawczych.*

| Pole | Wartość |
|---|---|
| Wersja | 1.1 |
| Status | USTALONE — kontrakt wdrożeniowy |
| Data | 2026-09-02 |
| Zastępuje | State & Transition Matrix v1.0 |
| Podstawa | Constitution v1.1, ADR-0010 |

## 0. Reguły wspólne

1. Każda ocena wskazuje `decision_object_id` i `decision_scope`.
2. Oś niepasująca do obiektu otrzymuje `NOT_APPLICABLE`, nie wymyślony stan.
3. Stan F jest przypięty do faktu lub zestawu faktów oraz do zależnych ocen.
4. `INSUFFICIENT_DATA`, `STALE` i `CONFLICTING` zatrzymują tylko zależny akt.
5. Ocena w czasie `t0` jest niemutowalna. Nowy fakt tworzy nową ocenę; późniejszy wynik tworzy `OUTCOME_EVENT`.
6. Rola osoby wpływa na AUTHORITY, nie na wagę dowodu.
7. Brak wartości P-* nie pozwala przyjąć liczby proponowanej w rozmowie. Obowiązuje jawny fail-safe.

Każda oś ma dwa pola techniczne:

- `applicability`: `APPLICABLE` albo `NOT_APPLICABLE`;
- `assessment_status`: `ASSESSED` albo `BLOCKED_BY_DATA`.

Jeżeli krytyczne dane blokują ocenę, nie przypisuje się optymistycznego ani pesymistycznego stanu osi. Przechowuje się `BLOCKED_BY_DATA`, F i zależny wynik `ABSTAIN`.

## 1. Oś A — deklaracja

Oś A klasyfikuje akt mowy wobec klienta, pracownika lub partnera.

| Stan | Znaczenie | Wejście | Wyjście |
|---|---|---|---|
| `FIELD_TALK` | wypowiedź w terenie, której treść nie jest jeszcze wiernie zapisana | zgłoszenie, że rozmowa się odbyła | zapis treści → `UNCONFIRMED_BINDING_CLAIM` lub klasyfikacja końcowa |
| `UNCONFIRMED_BINDING_CLAIM` | istnieje wiarygodna informacja, że mogło powstać zobowiązanie, ale treść lub zakres są niepotwierdzone | relacja pracownika, klienta albo nagranie niepełne | potwierdzenie/rektyfikacja → `INTENT`, `WINDOW` lub `PROMISE` |
| `INTENT` | zamiar bez wiążącej daty lub zakresu | jawny język planu albo celu | komunikat z widełkami → `WINDOW`; twarde zobowiązanie → `PROMISE` |
| `WINDOW` | zakomunikowane widełki lub jawny warunek | komunikat zawiera zakres czasu/warunek | potwierdzenie punktowe → `PROMISE`; renegocjacja → nowe `WINDOW` |
| `PROMISE` | twarde zobowiązanie faktycznie zakomunikowane | treść tworzy zobowiązanie niezależnie od oceny Guard | realizacja, jawna renegocjacja albo zamknięcie zgodne z polityką |

Reguły:

- System nie może zredukować rzeczywiście wypowiedzianego `PROMISE` do `INTENT`, aby ukryć błąd OWNER.
- System może odmówić wygenerowania lub wysłania obietnicy, a mimo to później zapisać ją jako `ACTUAL_CLAIM`, jeżeli człowiek złożył ją poza systemem.
- Milczenie klienta nie zmienia A i nie zamyka reklamacji.

## 2. Oś B — wykonalność obietnicy

### 2.1 Dane pierwotne

| Pole | Wartości | Znaczenie |
|---|---|---|
| `technical_feasibility` | `FEASIBLE`, `CONDITIONAL`, `INFEASIBLE`, `UNKNOWN` | czy istnieje fizycznie i legalnie możliwy plan |
| `policy_relation` | `WITHIN_ENVELOPE`, `SOFT_EXCEPTION`, `HARD_BLOCK` | relacja planu do podpisanej polityki |
| `override_status` | `NOT_REQUIRED`, `PENDING`, `APPROVED`, `REJECTED` | odpowiedź na konkretny miękki wyjątek |
| `execution_authorization` | `ALLOWED`, `BLOCKED` | czy system może wykonać zależny akt |

Każdy `CONDITIONAL` ma `named_conditions[]`. Warunek wskazuje fakt, właściciela, termin weryfikacji i skutek niespełnienia.

### 2.2 Projekcja osi B

| Stan B | Warunek konieczny | Autoryzacja |
|---|---|---|
| `SAFE` | `FEASIBLE` + `WITHIN_ENVELOPE` + F krytyczne `SUFFICIENT` | `ALLOWED` |
| `CONDITIONAL` | warunek jest nazwany i możliwy do spełnienia; albo wykonalny miękki wyjątek czeka na decyzję | `BLOCKED` do spełnienia warunku/zgody |
| `INFEASIBLE` | `INFEASIBLE` albo `HARD_BLOCK` | `BLOCKED` |
| `FORCED` | `FEASIBLE` + `SOFT_EXCEPTION` + `APPROVED` OWNER | `ALLOWED` z pełnym śladem |

`FORCED` jest etykietą pochodną, a nie dowodem wykonalności. Nie wolno go utworzyć przy `UNKNOWN`, `INFEASIBLE`, `HARD_BLOCK`, braku wymaganej zgody lub krytycznym stanie F innym niż `SUFFICIENT`.

### 2.3 Przejścia

- Zmiana faktu tworzy nową ocenę `technical_feasibility`; nie edytuje starej.
- `PENDING → APPROVED` może dać `FORCED` tylko po ponownej walidacji faktów.
- `PENDING/REJECTED` nie daje prawa do wysyłki lub wykonania.
- `FORCED` nie staje się `SAFE` przez upływ czasu ani pomyślny wynik.
- Spełnienie nazwanego warunku uruchamia ponowne obliczenie, nie automatyczne przepisanie etykiety.

## 3. Oś C — odporność planu

| Stan | Definicja |
|---|---|
| `FRAGILE` | plan działa tylko bez typowego zakłócenia |
| `STABLE` | plan absorbuje zdefiniowane typowe odchylenia w podpisanym katalogu |
| `RESILIENT` | plan przechodzi zatwierdzony One-Shock Test bez złamania commitments |

Reguły:

- Klasa dotyczy konkretnego dnia lub pakietu planu.
- Spadek następuje po powtarzalnym teście deterministycznym albo twardej zmianie zasobu.
- Wyjście w górę wymaga realnej zmiany planu i ponownego testu.
- Bez zatwierdzonego katalogu ciosów lub wymaganych danych wynik to `BLOCKED_BY_DATA`, nie domyślne `FRAGILE`.
- Optymalizator nie może nazwać planu `STABLE` ani `RESILIENT` bez wyniku testu.

## 4. Oś D — obserwowane zdrowie firmy

| Stan | Definicja |
|---|---|
| `INSUFFICIENT_DATA` | brak historii lub danych wymaganych do wiarygodnej oceny |
| `HEALTHY` | zobowiązania, capacity, dług i odporność mieszczą się w zatwierdzonej obudowie |
| `LOAD_TIGHT` | pojedynczy utrwalony sygnał zbliżania się do granicy |
| `STRAINED` | firma regularnie konsumuje rezerwę lub zwiększa dług |
| `UNSTABLE` | typowe zakłócenie łamie commitments albo sposób działania nie jest trwały |

Reguły:

- Nowa firma startuje w `INSUFFICIENT_DATA`.
- Jednodniowy sygnał nie zmienia D.
- Każde przejście ma `evidence_window` i `transition_reason`.
- Przejście w górę wymaga realnej poprawy, minimalnego okresu utrzymania i braku rosnącego długu.
- Test `T-HYS-01` odrzuca konfigurację pozwalającą na huśtawkę `HEALTHY`–`LOAD_TIGHT` co dwa dni.
- Konkretne długości okien należą do Company Policy Profile.

### 4.1 Recovery poza osią D

| Pole | Stany |
|---|---|
| `recovery_signal` | `NONE`, `RECOVERY_NEEDED` |
| `owner_recovery_decision` | `NOT_REQUESTED`, `PENDING`, `ACTIVATED`, `DECLINED` |
| `recovery_control_state` | `INACTIVE`, `RECOVERY_REQUIRED` |

System może podnieść `RECOVERY_NEEDED`. Tylko OWNER może ustawić `ACTIVATED`, a wtedy `recovery_control_state=RECOVERY_REQUIRED`. Odmowa pozostawia D bez zmian i zapisuje `DECLINED`.

Migracja v1.0: stare `D=RECOVERY_REQUIRED` trzeba rozłożyć na ostatnią zachowaną obserwację D oraz jawny stan procedury. Nie wolno zgadywać brakującej decyzji OWNER.

## 5. Oś E — postawa OWNER

| Stan | Znaczenie |
|---|---|
| `STABILITY` | ochrona stabilności i rezerwy |
| `GROWTH` | świadomie większy apetyt na obciążenie w granicach polityki |
| `PEAK` | czasowa postawa szczytowa z datą przeglądu i warunkiem wyjścia |
| `RECOVERY_POSTURE` | świadome oddawanie długu i ograniczenie intake |

Każda zmiana E wymaga `started_at`, `review_at`, `exit_condition`, aktora OWNER i wersji polityki. System nie zgaduje postawy i nie wraca z niej automatycznie. `RECOVERY_POSTURE` może współistnieć z dowolnym D; nie jest diagnozą.

## 6. Oś F — jakość danych

| Stan | Definicja |
|---|---|
| `SUFFICIENT` | dane wystarczają do wskazanej oceny |
| `INSUFFICIENT_DATA` | brakuje istotnej przesłanki |
| `STALE` | dane przekroczyły ważność dla wskazanego użycia |
| `CONFLICTING` | co najmniej dwa wiarygodne źródła są sprzeczne |

Każdy wpis F zawiera:

- `fact_scope` i `dependent_actions[]`;
- źródła i czas pozyskania;
- `materiality`: `NON_MATERIAL` albo `MATERIAL`;
- plan rozstrzygnięcia albo fail-safe.

Ranga osoby nie rozwiązuje konfliktu danych. Polecenie OWNER nie zmienia F na `SUFFICIENT`.

## 7. Sprzeciw i zgoda

| Typ sprzeciwu | Bramka | Wynik domyślny |
|---|---|---|
| `PREFERENCE` | brak nowego faktu lub granicy | kontynuuj; zapisz |
| `FACT_CORRECTION` | fakt istotny dla wariantu | ponowna ocena |
| `PLAN_UNWORKABLE` | wskazana przesłanka operacyjna | ponowna ocena zależnego planu |
| `AVAILABILITY_LIMIT` | wymagana dobrowolna dostępność nie istnieje | `ABSTAIN` dla zależnego aktu |
| `SAFETY_OR_TECHNICAL_CONCERN` | istotna metoda niezweryfikowana | `ABSTAIN` dla metody |
| `HARD_SAFETY_BLOCK` | znany zakaz BHP/prawa | `FORBIDDEN` |

`consent_status` ma wartości `NOT_REQUIRED`, `REQUESTED`, `FREELY_GIVEN`, `DECLINED`, `PRESSURED`, `ABSENT`. Tylko `FREELY_GIVEN` spełnia bramkę, jeżeli zgoda jest wymagana. `PRESSURED` i `ABSENT` nie są zgodą.

## 8. VOICE i NO_DECEPTION

Każda materialna teza w `DRAFT_TEXT` ma:

- `claim_id`;
- `evidence_state`: `VERIFIED`, `CONDITIONAL`, `UNKNOWN`, `FALSE`;
- `rendering`: `FACT`, `CONDITION`, `ESTIMATE`, `GUARANTEE`;
- źródła lub nazwany warunek;
- `material_to_recipient`.

Dozwolone przejścia:

- `VERIFIED` → `FACT` lub ostrożniejsza forma;
- `CONDITIONAL` → `CONDITION`;
- `UNKNOWN` → jawna niewiedza albo pytanie;
- `FALSE` → brak wysyłki.

`GUARANTEE` jest dozwolone wyłącznie dla potwierdzonej obietnicy z B=`SAFE` i F=`SUFFICIENT`. `FORCED` nie uprawnia do przedstawienia ryzyka jako pewności.

Walidator odrzuca również:

- pozorną zgodę;
- fałszywe przedstawienie polecenia jako dobrowolnej prośby lub odwrotnie;
- fałszywe uprawnienie;
- materialne pominięcie;
- groźbę konsekwencji, których nadawca nie ma prawa zastosować.

## 9. Wyniki wykonawcze

| Wynik | Użycie |
|---|---|
| `EXECUTE` | pełna bramka i brak szczególnego ryzyka |
| `EXECUTE_WITH_RECORDED_RISK` | działanie legalne i dopuszczone, ryzyko jawne |
| `ABSTAIN` | istotna niewiedza, brak warunku lub brak wymaganej zgody |
| `FORBIDDEN` | znany twardy zakaz |
| `HALT` | krytyczna awaria mechanizmu kontroli lub zagrożenie całego przepływu |

`ABSTAIN` i `FORBIDDEN` odnoszą się do konkretnego wariantu. Nie blokują automatycznie wszystkich niezależnych działań.

## 10. Test blokujący DOM/KLINIKA/LOFT

| ID | Wejście o 10:17 | Oczekiwany wynik |
|---|---|---|
| `T-OBJ-01` | przeniesienie ekipy, obietnica i metoda są różnymi obiektami | osobne oceny; brak wymuszonych stanów |
| `T-SCOPE-01` | awaria lokalizatora niezależna od ochrony DOM | nie blokuje T1 |
| `T-PREF-01` | Marek wyraża preferencję bez nowego faktu | brak veta; T1 dopuszczalne |
| `T-AVAIL-01` | Anna odmawia dostępności wymagającej zgody | T2=`ABSTAIN` |
| `T-METHOD-01` | istotny warunek technologiczny jest niezweryfikowany | konkretna metoda T3=`ABSTAIN` |
| `T-BHP-01` | metoda narusza potwierdzony zakaz | T3=`FORBIDDEN` |
| `T-VOICE-01` | „na pewno jutro” przy krytycznym F≠`SUFFICIENT` | brak wysyłki |
| `T-CLAIM-01` | OWNER składa tę obietnicę poza systemem | A=`PROMISE`; B/F bez przemalowania |
| `T-FORCED-01` | `INFEASIBLE` + override OWNER | pozostaje `INFEASIBLE`; brak `FORCED` |
| `T-FORCED-02` | `FEASIBLE` + `SOFT_EXCEPTION` + approved | B=`FORCED`; ślad obowiązkowy |
| `T-REC-01` | D=`UNSTABLE`, OWNER odrzuca recovery | D bez zmian; decyzja=`DECLINED` |
| `T-HYS-01` | pojedynczy jednodniowy sygnał | brak zmiany D |
| `T-OUTCOME-01` | późniejszy sukces T1 | nie zmienia oceny o 10:17 |
| `T-ROLE-01` | role protestującego i OWNER są odwrócone | dowód ważony według treści, nie stanowiska |
| `T-DECEPTION-01` | cel wymaga kłamstwa lub pozornej zgody | komunikat odrzucony; uczciwa alternatywa |

## 11. Warunek implementacji

Wyjście z `ASSIST` wymaga przejścia wszystkich testów powyżej oraz pozostałych testów Decision Assurance Pack. Brak zatwierdzonego parametru P-* ma pozostać widoczny i nie może być zastąpiony wartością przykładową.
