# M2 / MobileWC Behavioral Contract v1.0 — FINAL FREEZE

**Status:** FINAL FREEZE

**Data freeze:** 2026-08-31

**Zakres:** M2 / MobileWC — kontrakt zachowania człowiek <-> WERKcrew

## Zasady governance

- A–L są zamrożone. Nie otwieramy ich ponownie dla „ulepszeń”.

- Zmiana v1.0 wymaga prawdziwej sprzeczności ujawnionej przez implementację/test, a nie nowego pomysłu produktowego.

- Transport, prywatne auto i kilometrówka -> M3.

- Fallback SMS/telefon i runtime orkiestracji -> M4.

- Authority / HUMAN_DECISION -> M4/M6.

- Wygląd ekranów -> po kontrakcie eventów i testach.

## Inwarianty nadrzędne

1. MobileWC pokazuje żywy plan dnia i przyjmuje fakty / wyjątki. Nie jest time trackerem ani systemem nadzoru.

2. **Cisza jest legalna.** Brak eventu w środku dnia nie znaczy problemu ani braku pracy.

3. UI może mówić ON MY WAY, ale backend zapisuje neutralny fakt DAY_PLAN_ACTIVATED.

4. Brak sygnału startu ≠ choroba ≠ udowodniona nieobecność.

5. START_DELAY_REPORTED, START_EXCEPTION_REPORTED i UNAVAILABLE_TODAY_REPORTED są rozłączne.

6. Zdjęcie/głos/pomiar są evidence obserwacji, nie automatyczną ekspertyzą.

7. ACK ≠ zgoda na wykonanie. Worker ma WORKER_ACTION_EXCEPTION: „widziałem, ale jest nowy fakt”.

8. Bez ACKED nie twierdzimy, że worker został poinformowany.

9. WORKER_ACTION_EXCEPTION nigdy nie zmusza do kontynuacji działania zgłoszonego jako niewykonalne lub niebezpieczne.

10. SAFE_HOLD / BLOCKED_PENDING_RESOLUTION jest trwały: nie znika po restarcie procesu, cold starcie, synchronizacji ani rozpoczęciu nowego dnia. Zdejmuje go tylko jawny, autoryzowany exit/replan.

11. DAY_CLOSE_REPORTED jest intencją workera, nie stanem DAY_CLOSED.

12. Crew assignment bez jawnego lead_worker_id jest invalid/fail-closed.

13. Brak worker reliability score, rankingów, m²/h i „karnego joba” za wcześniejsze skończenie.

## Koperta wspólna

Każdy event MobileWC -> backend:

| Pole | Zasada |
| --- | --- |
| event_id | UUID z urządzenia. To jest klucz idempotencji. |
| schema_version | np. 1 |
| event_type | z słownika poniżej |
| actor_id | worker / owner w trybie field |
| occurred_at | zegar urządzenia w chwili gestu |
| plan_day_id | dzień + worker |
| job_id / task_id / assignment_id | to, czego event dotyczy; puste tylko gdy naprawdę nie dotyczy tasku |
| offline_origin | bool |
| attachments[] | zdjęcie / głos / tekst surowy; oryginał zostaje evidence |
| client_context | opcjonalnie: app_build, net_state — nie do logiki biznesowej |

Backend nie tworzy drugiego eventu, jeśli dostanie ten sam event_id.

Retry synchronizacji z tym samym event_id zwraca istniejący wynik i nie tworzy nowego skutku.

Zegar serwera (received_at) służy do kolejki i audytu. Do porządku dnia workera liczy się occurred_at, z twardym limitem sanity (np. nie z przyszłości > 15 min, nie ze starszego niż 36 h bez flagi replay).

## Kto może emitować

| Aktor | Może |
| --- | --- |
| Worker | intenty z tabeli 1, ACK, action exception, completion swojego assignmentu |
| Lead ekipy | dodatkowo completion całego CREW_ASSIGNMENT |
| Inny członek ekipy | wyjątki i COMPLETION_DISPUTED, **nie** completion ekipy |
| Owner-as-worker | to samo co worker |
| Owner-as-owner | ACK/decyzje/HUMAN_TASK — tabela 3, nie field-eventy „za kogoś” |
| System | eskalacje, ASSIGNMENT_INVALID, zmiany evidence doręczenia |
| Agent | **nie klika MobileWC**. Pisze dyrektywy tabela 2 |

Worker nie wybiera technicznego stanu firmy. Wybiera intent.

## Tabela 1 — człowiek -> backend

### Start dnia

**DAY_PLAN_ACTIVATED**

UI: ON MY WAY

Minimum: actor_id, plan_day_id, occurred_at

Opcjonalnie: nic. Bez GPS, bez „gdzie jesteś”.

Wolno offline.

Postcondition: plan dnia tego workera przechodzi draft|issued -> active. Ponowna aktywacja tego samego dnia = no-op.

Agent **może:** uznać, że realizacja planu wystartowała; zdjąć reminder startu; ruszyć zależności „worker w drodze / w pracy”.

Agent **nie może:** uznać lokalizacji; uznać obecności u klienta; zamknąć UNKNOWN_START jako winę.

**START_DELAY_REPORTED**

UI: Spóźnię się

Minimum: plan_day_id, reason_class?, eta?

reason_class: TRAFFIC | TRANSPORT | PERSONAL | OTHER

eta miękkie (godzina albo „ok. 30 min”), nie obowiązkowe.

Agent **może:** policzyć wpływ na okna klienta / następców; ewentualnie INFO do ownera jeśli psuje obietnicę.

Agent **nie może:** oznaczyć absencji; oddać zadania komu innemu bez polityki / progu.

**START_EXCEPTION_REPORTED**

UI: Nie dojadę / mam problem ze startem

Minimum: plan_day_id, reason_class

reason_class: TRANSPORT_BLOCKED | PERSONAL_EMERGENCY | SICK | OTHER

Opcjonalnie: głos, „mogę później innym transportem”, „potrzebuję odbioru”.

To jest **problem startu**, nie wyrok o dostępności.

Agent **może:** sprawdzić transport, odbiór przez kolegę, przesunięcie pierwszego punktu; zapytać workera jednym pytaniem, jeśli brakuje jednego faktu.

Agent **nie może:** samemu wystawić UNAVAILABLE_TODAY; rozebrać całego dnia na części, jeśli da się uratować start.

**UNAVAILABLE_TODAY_REPORTED**

UI: Dzisiaj nie pracuję

Minimum: plan_day_id, reason_class (SICK | PERSONAL_EMERGENCY | OTHER)

Tylko z jawnego gestu. Nigdy inferowane z ciszy ani z TRANSPORT_BLOCKED.

Agent **może:** zdjąć workera z dzisiejszych assignmentów; przeplanować według polityki; jeśli psuje obietnicę klienta / koszt / bezpieczeństwo -> ASK.

Agent **nie może:** pytać codziennie „czy na pewno”.

### Blokada i braki

**WORK_START_BLOCKED**

UI: Nie mogę zacząć

Minimum: task_id, reason_class

reason_class: NO_ACCESS | CLIENT_ABSENT | PREDECESSOR_NOT_READY | UNSAFE | WEATHER | OTHER

Opcjonalnie: evidence.

Agent **może:** rozpoznać, czy to dostęp, poprzednik, klient, bezpieczeństwo; wstrzymać task; nie ruszać workera w ślepy dojazd do tego samego miejsca.

Agent **nie może:** uznać, że worker „nie chce pracować”.

**RESOURCE_MISSING_REPORTED**

UI: Czegoś brakuje

Minimum: task_id albo plan_day_id, resource_kind

resource_kind: MATERIAL | TOOL | VEHICLE | KEY | DRAWING | OTHER

Opcjonalnie: nazwa/ilość głosem, zdjęcie.

Agent **może:** uruchomić ścieżkę zasobową (dostawa, przeniesienie z innej budowy, zakup w limicie polityki, zmiana kolejności tasków).

Agent **nie może:** kazać workerowi „jakoś dawaj”.

**SITE_PROBLEM_REPORTED**

UI: Problem na budowie

Minimum: task_id/job_id + (tekst **lub** głos **lub** zdjęcie)

Opcjonalnie: severity_hint od workera (BLOCKS_WORK | UNSAFE | QUALITY | OTHER) — hint, nie werdykt.

Agent **może:** zatrzymać dalsze kroki na tym froncie; zaproponować STOP jeśli polityka/safety; wezwać specjalistę według skill matrix; spisać obserwację z actor.skill.

Agent **nie może:** zrobić ze zdjęcia ekspertyzy; zamknąć sporu jakości winą workera.

**SCOPE_FACT_REPORTED**

UI: Dodatkowa praca / nowy fakt

Minimum: opis (głos/tekst)

Opcjonalnie: zdjęcie, kontakt, source (SAME_CLIENT | NEIGHBOR | OTHER)

Agent **może:** utworzyć LEAD albo CHANGE_REQUEST w stanie **non-binding**.

Agent **nie może:** wycenić, obiecać terminu, wcisnąć tego w dzisiejszy plan jako zobowiązanie.

### Completion

Wspólne reguły completion:

- typ wynika z tasku, nie z menu workera;

- postcondition niespełnione -> event przyjmowany jako reported, task **nie** przechodzi na done, MobileWC mówi czego brakuje jednym gestem;

- CREW_ASSIGNMENT bez lead_worker_id -> system ASSIGNMENT_INVALID, completion odrzucone;

- zamyka lead; inny członek może tylko COMPLETION_DISPUTED.

**TASK_COMPLETION_REPORTED**

1-osobowe / jednoznaczne zadanie.

Minimum: task_id + postconditions z definicji tasku (często nic).

Agent **może:** zamknąć task, zwolnić zasób, odblokować następców.

Agent **nie może:** auto-assign kolejnego joba.

**STAGE_COMPLETION_REPORTED**

Minimum: task_id, stage_id

Opcjonalnie: ilość **tylko** gdy task.requires_quantity.

Agent **może:** zamknąć etap dnia, zostawić job żywy.

Agent **nie może:** uznać całego joba za skończony.

**TECHNICAL_WAIT_REPORTED**

Minimum: task_id, wait_condition albo wait_until

Agent **może:** uwolnić workera, task zostaje w waiting.

Agent **nie może:** traktować tego jako failure ani jako wolny slot na karny job.

**VISIT_COMPLETION_REPORTED**

Minimum: task_id + evidence pack wymagany przez brief wizyty.

Agent **może:** zamknąć wizytę, jeśli pack kompletny; jeśli nie — zostawić open i powiedzieć czego brak.

Agent **nie może:** uznać „byłem” bez evidence, gdy brief go wymaga.

**DAY_CLOSE_REPORTED**

Minimum: plan_day_id

Opcjonalnie: stage_incomplete_reason jeśli etap żyje.

Znaczenie: worker zgłasza intencję *kończę dziś swoją pracę*. To nie jest stan DAY_CLOSED.

Agent **może:** uruchomić company.policy.end_of_day i zwrócić RETURN_BASE | PREP_NEXT_DAY | OFFER_SOFT_OPTIONS | END_ON_SITE | ASK_OWNER. Dopiero po spełnieniu wyniku polityki system może przejść do DAY_CLOSED i zdjąć dostępność.

Agent **nie może:** zamknąć dnia tylko dlatego, że worker wysłał intent; zamknąć tasków „przy okazji”.

**COMPLETION_DISPUTED**

Kto: członek assignmentu inny niż autor completion.

Minimum: task_id/stage_id, against_event_id, powód/evidence.

Agent **może:** cofnąć milczące zamknięcie do disputed; dać kartę człowiekowi.

Agent **nie może:** głosować, kto ma rację.

### Dyrektywa agent -> worker

**WORKER_ACKNOWLEDGED**

Minimum: directive_id

Znaczenie: *widziałem i rozumiem*. Nie oznacza „wykonam bez przeszkód”.

Agent **może:** podnieść evidence do ACKED; uznać, że worker_informed = true dla tej dyrektywy.

**WORKER_ACTION_EXCEPTION**

UI przy ACTION/STOP: „Widziałem, ale jest problem”

Minimum: directive_id, reason_class (VEHICLE_UNSAFE | PHYSICALLY_UNABLE | CONFLICTING_FACT | ALREADY_COMMITTED | OTHER)

Opcjonalnie: głos/zdjęcie.

Agent **musi:** nie wymuszać dyrektywy i wrócić do oceny.

- wyjątek wobec ACTION_REQUIRED -> obowiązuje ostatni **bezpieczny, potwierdzony** stan; nie wracamy w ciemno do akcji, której dotyczy wyjątek; M4 replanuje,

- wyjątek wobec STOP_DIRECTIVE -> STOP nadal obowiązuje; affected task -> SAFE_HOLD / BLOCKED_PENDING_RESOLUTION,

- fakt VEHICLE_UNSAFE -> nie realizuje się części planu wymagającej tego pojazdu.

Inwariant: WORKER_ACTION_EXCEPTION nigdy nie zmusza człowieka do działania, które właśnie zgłosił jako niewykonalne albo niebezpieczne.

Agent **nie może:** traktować tego jako buntu albo reliability minus; wrócić automatycznie do niebezpiecznego starego kroku.

## Tabela 2 — WERKcrew -> MobileWC

Agent nie emituje field-eventów workera. Emituje **dyrektywy**.

| Dyrektywa | Klasa | ACK | Evidence start | Treść minimum |
| --- | --- | --- | --- | --- |
| INFO_NOTICE | INFO | nie | CHANNEL_ACCEPTED wystarczy | tekst, odniesienie do job/plan |
| ACTION_REQUIRED | ACTION | tak | idzie QUEUED | co się zmienia: adres / pojazd / task / okno |
| STOP_DIRECTIVE | STOP | tak, natychmiast | QUEUED | czego nie robić + dlaczego krótko |
| END_OF_DAY_OPTIONS | INFO albo ACTION\* | ACK tylko gdy polityka wymaga konkretnego wyboru (np. zwrot auta) | — | opcje z policy tree, nigdy goły nowy job |
| WHY_EXPLAINED | INFO | nie | — | fakty, zero chain-of-thought |

\*Jeśli end-of-day to RETURN_BASE z autem firmowym -> to ACTION. Jeśli „możesz skończyć” -> INFO.

Timeouty (haki polityki, default freeze):

| Klasa | R1 | E1 |
| --- | --- | --- |
| ACTION | 10 min | 25 min -> ACK_MISSING_ESCALATED |
| STOP | 2 min | 5 min -> STOP_UNCONFIRMED + fallback kanał + owner |

Fallback jest **obowiązkiem orkiestracji (M4)**, nie MobileWC. MobileWC musi tylko raportować evidence level, który naprawdę ma.

## Tabela 3 — owner w tej samej apce

Nie są to eventy field workera. Osobna szyna, ten sam klient.

| Event | Kto | Minimum | Skutek |
| --- | --- | --- | --- |
| HUMAN_DECISION_REQUESTED | system/agent | pytanie, opcje, rekomendacja, deadline, skutki | karta ASK |
| HUMAN_DECISION_RECORDED | owner | request_id, wybrana opcja | odblokowuje agentowi dalszy ruch |
| HUMAN_TASK_ASSIGNED | system/agent | co owner ma zrobić (np. zadzwonić) | karta HUMAN_TASK |
| HUMAN_TASK_COMPLETED | owner | task_id, wynik | agent wraca do procesu |
| OWNER_POLICY_OVERRIDE | owner | świadoma zmiana jednorazowa | nie uczy modelu „od teraz zawsze tak” bez zapisu polityki |

Owner w terenie emituje tabelę 1 pod swoim actor_id. Bez świętej krowy.

Owner nie emituje DAY_PLAN_ACTIVATED za pracownika.

## Tabela 4 — system, nie UI

| Event | Kiedy | Skutek |
| --- | --- | --- |
| START_UNKNOWN_ESCALATED | cisza po oknie startu | karta człowieka; przyczyna = unknown |
| ACK_MISSING_ESCALATED | brak ACK na ACTION | człowiek; worker nadal na starym planie |
| STOP_UNCONFIRMED | STOP bez ACKED | firma wie, że nie wie |
| ASSIGNMENT_INVALID | crew bez lead_worker_id albo completion od nie-leada | fail-closed, nic się nie zamyka |
| DELIVERY_EVIDENCE_UPDATED | zmiana poziomu doręczenia | tylko evidence, bez inferencji „poinformowany” przed ACKED |
| COMPLETION_REJECTED | brak postcondition / brak uprawnienia | event zachowany, stan tasku bez zmian |

Cisza workera **nie** emituje UNAVAILABLE_TODAY_REPORTED.

## Mapowanie 5 ludzkich przycisków -> event

To jest cały UI wyjątków. Backend dopytuje maximally jednym krokiem, nie drzewem 20 pól.

| Przycisk | Pierwszy event | Doprecyzowanie |
| --- | --- | --- |
| Spóźnię się / nie dojadę | najpierw wybór: delay vs problem startu vs „dziś nie pracuję” | START_DELAY_REPORTED / START_EXCEPTION_REPORTED / UNAVAILABLE_TODAY_REPORTED |
| Nie mogę zacząć | WORK_START_BLOCKED | powód z krótkiej listy + głos |
| Czegoś brakuje | RESOURCE_MISSING_REPORTED | kind + głos/zdjęcie |
| Problem na budowie | SITE_PROBLEM_REPORTED | evidence first-class |
| Dodatkowa praca / nowy fakt | SCOPE_FACT_REPORTED | zero ceny/terminu |

Completion nie siedzi w „15 eventach”. Siedzi kontekstowo na karcie tasku, etykieta z typu tasku.

## Payload — enumeracje zamknięte w v1.0

Nie rozrastamy tego przy ekranach.

```text
start_reason:        TRANSPORT_BLOCKED | PERSONAL_EMERGENCY | SICK | OTHER
delay_reason:        TRAFFIC | TRANSPORT | PERSONAL | OTHER
block_reason:        NO_ACCESS | CLIENT_ABSENT | PREDECESSOR_NOT_READY | UNSAFE | WEATHER | OTHER
resource_kind:       MATERIAL | TOOL | VEHICLE | KEY | DRAWING | OTHER
problem_hint:        BLOCKS_WORK | UNSAFE | QUALITY | OTHER
action_exception:    VEHICLE_UNSAFE | PHYSICALLY_UNABLE | CONFLICTING_FACT | ALREADY_COMMITTED | OTHER
unavailable_reason:  SICK | PERSONAL_EMERGENCY | OTHER
scope_source:        SAME_CLIENT | NEIGHBOR | OTHER
directive_class:     INFO | ACTION | STOP
delivery_evidence:   QUEUED | CHANNEL_ACCEPTED | APP_OBSERVED | ACKED
completion_type:     TASK | STAGE | WAITING | VISIT | DAY_CLOSE
```

Tekst wolny i głos zawsze dozwolone obok enum. Enum jest do routingu. Głos jest do prawdy.

## Offline, kolejność, konflikty

1. Wszystkie eventy tabeli 1 wolno zapisać offline oprócz niczego — **wszystkie**. Dyrektywy STOP po odzyskaniu sieci renderują się przed INFO.

2. Idempotencja = event_id.

3. Dwa różne eventy o tym samym świecie (DONE + DISPUTED, ACTION + ACTION_EXCEPTION) — wygrywa **fail-closed**: stan nie idzie do „sukces”, idzie do wyjątku.

4. Worker działa według **ostatniej dyrektywy, którą zACK-ował**. Dyrektywa tylko CHANNEL_ACCEPTED nie zmienia jego obowiązującego planu.

5. Evidence (głos, zdjęcie) jest niemutowalne. Agent może dodać interpretację obok, nie zamiast.

## Dozwolone reakcje agenta — krótki kaganiec

Bez człowieka agent **wolno** mu:

- aktywować plan po DAY_PLAN_ACTIVATED;

- przeliczyć skutek spóźnienia;

- wstrzymać task przy bloku / braku / problemie;

- utworzyć non-binding lead/change;

- zamknąć task/stage/visit, gdy typ, uprawnienie i postcondition się zgadzają;

- uwolnić workera przy TECHNICAL_WAIT; przy DAY_CLOSE dopiero po spełnieniu company.policy.end_of_day i przejściu do DAY_CLOSED;

- wykonać end-of-day **wyłącznie** z drzewa polityki;

- pokazać soft-options early finish, nigdy auto-job poza limitami;

- podnieść ASK, gdy pęka obietnica, koszt, safety, albo brak ACK/STOP.

Bez człowieka agent **nie wolno** mu:

- uznać ciszy za chorobę albo absencję;

- uznać START_EXCEPTION za UNAVAILABLE_TODAY;

- twierdzić worker_informed bez ACKED;

- wymusić ACTION po WORKER_ACTION_EXCEPTION;

- zamknąć ekipę bez leada;

- robić ekspertyzy ze zdjęcia;

- obiecywać klientowi cenę/termin z faktu workera;

- wystawić worker score;

- dosypać karnej roboty za wcześniejsze skończenie.

## Co jest w M2, a co już nie

**M2 / MobileWC v1.0 obejmuje:** intenty, eventy, uprawnienia, offline/idempotencję, postconditions completion, evidence doręczenia, kaganiec reakcji, role owner-as-worker vs ASK.

**Nie obejmuje (zostaje jawne):** profil transportu i kilometrówka (M3), progi Est vs Act (M3/M4), wykonanie fallbacku SMS/telefon (M4), authority ASK (M4/M6), skill matrix jako dane (M3), wygląd ekranów.

To jest moment na przybicie:

**M2 / MobileWC behavioral contract v1.0 — FINAL FREEZE.**

Następny artefakt, wciąż bez Figma: JSON Schema / TypeSpec tych eventów (koperta + 1 plik na event) albo krótki test suite zachowania w formie given/when/then — to samo, tylko wykonywalne. Ekrany z tego wyjdą same: TODAY, 5 kafli, kontekstowe completion, karta ACTION/STOP z ACK | PROBLEM, owner: ASK.

# Appendix A — Contract behavior tests

## Testy zachowania — Given / When / Then

Konwencja: **Given** = stan świata i polityki. **When** = intent z MobileWC albo cisza albo sync. **Then** = jedyny legalny skutek. Jeśli implementacja robi coś innego, kontrakt jest złamany.

### S1. Cisza nie jest chorobą

**Given** plan dnia Marka, start_at = 07:30, brak jakiegokolwiek eventu

**When** jest 08:20, Marek milczy

**Then**

- nie istnieje UNAVAILABLE_TODAY_REPORTED

- nie istnieje absencja

- istnieje najwyżej START_UNKNOWN_ESCALATED

- plan nie został aktywowany

### S2. Reminder nie zgaduje przyczyny

**Given** jak S1, minął start_at

**When** system wysyła R1

**Then** Marek dostaje tylko: *Tak / Spóźnię się / Problem ze startem / Dzisiaj nie pracuję*

**And** żadna z odpowiedzi nie jest wstępnie zaznaczona

### S3. ON MY WAY aktywuje plan, nie lokalizację

**Given** plan dnia Ani w stanie issued

**When** Ania tapnie ON MY WAY -> DAY_PLAN_ACTIVATED

**Then**

- plan_day.status = active

- brak pola lokalizacji wymaganego do skutku

- reminder startu zdjęty

**And** agent nie twierdzi „Ania jest u klienta”

### S4. Transport blocked ≠ unavailable

**Given** plan dnia Piotra aktywny albo jeszcze nie

**When** START_EXCEPTION_REPORTED(reason=TRANSPORT_BLOCKED) + głos „kolega nie przyjechał, mogę później autobusem”

**Then**

- nie powstaje UNAVAILABLE_TODAY

- agent może szukać transportu / przesunąć start

- Piotr pozostaje zasobem dnia, dopóki sam nie powie, że nie pracuje

### S5. Spóźnienie ma osobny event

**Given** plan Oli, start_at = 08:00

**When** START_DELAY_REPORTED(reason=TRAFFIC, eta≈08:40)

**Then**

- event type ≠ START_EXCEPTION_REPORTED

- brak absencji

- agent liczy wpływ na okno klienta

- Ola nadal realizuje ten plan

### S6. Unavailable tylko z jawnego gestu

**Given** cisza albo TRANSPORT_BLOCKED

**When** brak UNAVAILABLE_TODAY_REPORTED

**Then** nikt nie zdejmuje workera z dnia jako „nie pracuje”

**When** pada UNAVAILABLE_TODAY_REPORTED(SICK)

**Then** dopiero wtedy assignmenty dnia są zdejmowane według polityki

### S7. Retry tego samego event_id = ten sam skutek

**Given** telefon był offline; worker tapnął ON MY WAY jeden raz; lokalny DAY_PLAN_ACTIVATED został już poprawnie przetworzony przez backend

**When** klient ponawia wysyłkę **tego samego eventu** z identycznym event_id (retry/sync po niepewnej odpowiedzi)

**Then**

- backend nie tworzy drugiego eventu ani drugiego skutku biznesowego

- nie powstaje nowy server_event_id / nowa tożsamość skutku

- odpowiedź jest semantycznie tym samym wynikiem pierwszego przetworzenia

- stan plan_day pozostaje active bez ponownego wykonania logiki

### S8. Dwa różne event_id tego samego intentu

**Given** plan już active po pierwszym DAY_PLAN_ACTIVATED

**When** drugi, nowy event_id z tym samym typem tego samego dnia

**Then** stan się nie dubluje; drugi event zapisany, skutek = no-op

### S9. Crew bez leada nic nie zamyka

**Given** CREW_ASSIGNMENT bez lead_worker_id

**When** ktokolwiek emituje STAGE_COMPLETION_REPORTED

**Then**

- ASSIGNMENT_INVALID

- COMPLETION_REJECTED albo równoważny fail-closed

- stage/task nie jest DONE

### S10. Lead zamyka, reszta nie

**Given** ekipa Peter=lead, Marek, Ola

**When** Peter -> STAGE_COMPLETION_REPORTED i postcondition OK

**Then** stage zamknięty

**When** zamiast tego Marek emituje completion

**Then** nic nie zamknięte; event odrzucony uprawnieniem

### S11. Spór ekipy

**Given** Peter zamknął stage

**When** Ola -> COMPLETION_DISPUTED

**Then** stage nie zostaje milcząco DONE; stan disputed; karta człowieka

**And** agent nie wybiera, kto kłamie

### S12. Completion bez evidence

**Given** wizyta wymaga zdjęcia albo głosu

**When** VISIT_COMPLETION_REPORTED bez evidence

**Then**

- event istnieje (reported)

- visit **nie** jest DONE

- MobileWC żąda jednego gestu evidence, nie formularza

### S13. STAGE nie zamyka joba

**Given** płytki 3 dni, stage = dzień 1

**When** STAGE_COMPLETION_REPORTED OK

**Then** stage zamknięty, job żywy, worker może podlegać polityce końca dnia / early finish

### S14. WAITING uwalnia człowieka, nie task

**Given** hydroizolacja

**When** TECHNICAL_WAIT_REPORTED(wait_until=jutro)

**Then** worker wolny, task waiting, to nie failure, nie karny job

### S15. ACTION bez ACK = stary potwierdzony plan

**Given** Marek zACK-ował plan: Job A, auto 1

**When** agent wysyła ACTION_REQUIRED: jedź do Job B

**And** brak WORKER_ACKNOWLEDGED

**Then**

- evidence ≠ ACKED

- nie wolno twierdzić worker_informed

- obowiązujący plan Marka = Job A

- po E1: ACK_MISSING_ESCALATED

### S16. STOP bez ACK = UNCONFIRMED, nie sukces

**Given** STOP_DIRECTIVE: nie ruszaj instalacji

**When** push przyjęty przez kanał, brak ACK

**Then**

- delivery_evidence najwyżej CHANNEL_ACCEPTED

- stan firmy: STOP_UNCONFIRMED

- nikt nie zapisuje „Marek poinformowany”

### S17. STOP + exception = SAFE HOLD

**Given** task „podłącz rozdzielnicę” był planem; padł STOP_DIRECTIVE

**When** WORKER_ACTION_EXCEPTION(directive=STOP, reason=CONFLICTING_FACT)

**Then**

- STOP **nadal obowiązuje**

- task -> SAFE_HOLD / BLOCKED_PENDING_RESOLUTION

- **nie** wraca uprawnienie ruszać instalację

- M4 resolvuje, nie worker

### S18. ACTION + exception nie wraca w niebezpieczny stary krok

**Given** zACK-owany plan: dojedź Caddy na Job B

**When** ACTION_REQUIRED: weź Caddy, jedź do Job C

**And** WORKER_ACTION_EXCEPTION(VEHICLE_UNSAFE)

**Then**

- Caddy nie jest używany

- Marek nie jest zmuszony jechać do Job C tym autem

- Marek nie jest zmuszony udawać, że wykonuje akcję

- obowiązuje ostatni **bezpieczny** potwierdzony stan + replan M4

### S19. Exception nie jest buntem

**Given** S17 albo S18

**When** wyjątek zapisany

**Then** brak worker score, brak kary, event jest nowym faktem

### S20. Zdjęcie problemu to evidence

**Given** glazurnik zgłasza mokro pod wanną + 2 zdjęcia

**When** SITE_PROBLEM_REPORTED

**Then**

- oryginał zdjęć i aktora (skill=tiler) zostaje

- brak werdyktu „instalacja nieszczelna”

- agent może wstrzymać front / wezwać specjalistę / dać STOP według polityki

- nie zamyka sprawy jako ekspertyza

### S21. Early finish bez karnego joba

**Given** etap dnia skończony 13:40, polityka early finish = on, najbliższy obcy job 40 km

**When** system liczy opcje

**Then**

- brak auto-assign „GO NOW”

- opcje tylko z drzewa polityki i kagańca (dojazd/czas, zero nowej obietnicy)

- „kończę” jest legalne i kończy dostępność według H, nie według chęci agenta

### S22. Intencja końca dnia ≠ DAY_CLOSED

**Given** auto firmowe musi nocować w bazie

**When** DAY_CLOSE_REPORTED

**Then**

- nie ma jeszcze DAY_CLOSED

- wypada END_OF_DAY_OPTIONS / ACTION RETURN_BASE **When** polityka spełniona (powrót zgłoszony albo owner zamyka wyjątek)

**Then** dopiero DAY_CLOSED i zdjęta dostępność

### S23. Koniec dnia bez auta

**Given** polityka END_ON_SITE, brak obowiązkowego zwrotu

**When** DAY_CLOSE_REPORTED

**Then** wolno przejść do DAY_CLOSED bez nowej pracy

### S24. Scope fact nie jest obietnicą

**Given** klient pyta o malowanie kuchni

**When** SCOPE_FACT_REPORTED

**Then**

- powstaje non-binding LEAD albo CHANGE_REQUEST

- brak ceny, braku terminu, braku wpięcia w dzisiejsze zobowiązanie

### S25. Owner w terenie nie jest świętą krową

**Given** Stefan owner jedzie na oględziny

**When** emituje DAY_PLAN_ACTIVATED i VISIT_COMPLETION_REPORTED

**Then** te same reguły co worker

**And** nie może emitować DAY_PLAN_ACTIVATED za Marka

### S26. Owner ASK jest inną szyną

**Given** odchylenie psuje termin klienta

**When** agent potrzebuje decyzji

**Then** HUMAN_DECISION_REQUESTED, nie ping „jak idzie u Müller”

**And** MobileWC workera nie dostaje KPI

### S27. sent ≠ informed

**Given** dowolna dyrektywa ACTION/STOP

**When** delivery_evidence ∈ {QUEUED, CHANNEL_ACCEPTED, APP_OBSERVED}

**Then** worker_informed = false

**When** WORKER_ACKNOWLEDGED

**Then** worker_informed = true wyłącznie dla tego directive_id

### S28. STOP po powrocie sieci pierwszy

**Given** telefon offline; w kolejce INFO i STOP

**When** sieć wraca

**Then** STOP renderuje się i blokuje zanim worker zobaczy INFO

### S29. Cisza w środku dnia jest legalna

**Given** plan aktywny, nic nadzwyczajnego

**When** 6 godzin bez eventu

**Then** brak pytań „jak idzie”, brak auto-statusów, plan pozostaje aktywny

### S30. Fail-closed przy konflikcie eventów

**Given** STAGE_COMPLETION_REPORTED i niemal równolegle SITE_PROBLEM_REPORTED(UNSAFE) z tym samym stage

**Then** stage nie zostaje DONE; wygrywa wstrzymanie / dispute / SAFE_HOLD, nie sukces

### S31. SAFE_HOLD jest trwały przez restart i nowy dzień

**Given** task jest w SAFE_HOLD / BLOCKED_PENDING_RESOLUTION z powodu STOP lub safety exception

**When** następuje restart procesu/backendu, cold start aplikacji/urządzenia, retry/sync albo worker następnego dnia emituje DAY_PLAN_ACTIVATED dla nowego plan_day

**Then**

- affected task nadal jest SAFE_HOLD / BLOCKED_PENDING_RESOLUTION

- rozpoczęcie nowego dnia nie przywraca prawa wykonania zablokowanego kroku

- rollover dnia, restart, sync i flaky network nie są exitami z blokady

- blokadę zdejmuje wyłącznie jawny, autoryzowany resolution/replan zgodny z kontraktem

## Minimalny zestaw, bez którego nie ma implementacji

Jeśli ktoś ma czas napisać tylko część, te 13 muszą przejść pierwsze:

1. S1 cisza ≠ chory

2. S4 transport ≠ unavailable

3. S5 delay rozłączny z exception

4. S7 retry tego samego event_id

5. S9 crew bez leada

6. S12 completion bez evidence

7. S15 ACTION bez ACK

8. S16 STOP bez ACK

9. S17 STOP + exception -> SAFE HOLD

10. S20 zdjęcie ≠ ekspertyza

11. S21 brak karnego joba

12. S22 DAY_CLOSE_REPORTED ≠ DAY_CLOSED

13. S31 SAFE_HOLD przetrwa restart / nowy dzień

Filozofowanie o MobileWC jest skończone. Kolejny suchy krok to przepisanie tych 31 scenariuszy na testy w repo (nazwy eventów już stabilne) — JSON Schema dopiero z zielonych testów, żeby schemat nie zalegalizował dziury, której test jeszcze nie złapał.
