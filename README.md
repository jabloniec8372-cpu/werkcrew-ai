# WERKcrew AI — deterministyczny vertical slice M1–M8.0 + Decision Semantics Freeze

Aplikacja FastAPI udostępnia deterministyczną ocenę M1, oględziny M2, planner M3, pricing M5, realny owner interrupt/resume M6 oraz persistent multi-job dispatch M7. M8.0 dodaje wyłącznie zamrożoną, wersjonowaną konfigurację biznesową przyszłego symulatora. M7 przechowuje business state w SQLite, a Strands `FileSessionManager` nadal odpowiada wyłącznie za ciągłość sesji i HITL. LLM orkiestruje tools; nie wylicza harmonogramu, nie zmienia pracownika/pojazdu i nie stosuje replanu bez decyzji właściciela.

Decision Semantics Freeze dodaje nadrzędne kontrakty dla prawdy, władzy OWNER, `FORCED`, recovery, sprzeciwu i `NO_DECEPTION`. Deterministyczny moduł `werkcrew_ai.domain.decision_semantics` oraz testy blokujące chronią te reguły przed późniejszym „doprecyzowaniem” w promptach. Dokumenty źródłowe znajdują się w [`docs/governance`](docs/governance/README.md), a decyzję architektoniczną zapisuje [ADR-0010](docs/decisions/0010-decision-semantics-freeze.md).

## Uruchomienie w PowerShell na Windows

Wymagany jest Python 3.11 lub nowszy. W katalogu repozytorium:

```powershell
cd C:\WERKcrew_AI
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
$env:WERKCREW_BEDROCK_MODEL_ID = "<model-id-lub-inference-profile-dostępny-na-koncie>"
$env:WERKCREW_AWS_REGION = "<region>"
$env:WERKCREW_BEDROCK_MAX_TOKENS = "1400"
# Opcjonalny katalog sesji. Na Windows domyślnie:
# %LOCALAPPDATA%\WERKcrew_AI\strands-sessions
$env:WERKCREW_STRANDS_SESSION_DIR = "$PWD\var\strands-sessions"
# Opcjonalna baza business state M7; domyślnie w %LOCALAPPDATA%\WERKcrew_AI:
$env:WERKCREW_DB_PATH = "$PWD\var\db\werkcrew-business.db"
# Opcjonalnie, jeżeli nie jest używany profil default:
$env:AWS_PROFILE = "<profil>"
python -m uvicorn werkcrew_ai.api.app:app --reload --port 8010
```

Projekt używa dokładnie `strands-agents==1.50.2` i `strands.models.BedrockModel`. Model ID oraz region nie mają fikcyjnych wartości domyślnych: muszą wskazywać rzeczywiście dostępny model lub inference profile na skonfigurowanym koncie AWS. Nazwy zmiennych znajdują się również w `.env.example`; aplikacja nie wczytuje automatycznie pliku `.env`.

Ekran koordynatora jest dostępny pod ścieżką:

```text
/demo/coordinator
```

WERKcrew Field jest dostępny po utworzeniu zadania oględzin pod ścieżką:

```text
/demo/field
```

Na ekranie koordynatora użyj „Uruchom WERKcrew Agent”. Model powinien wywołać tools M1/M2, przydzielić oględziny według reguł i zatrzymać się na `WAITING_FOR_FIELD_REPORT`. Po raporcie człowieka agent uruchamia planner M3 i pricing M5. Przy kompletnych wynikach M6 wywołuje `request_owner_decision`; Strands zwraca `stop_reason=interrupt`, a UI pokazuje wyłącznie legalne decyzje. Kliknięcie właściciela tworzy świeżą instancję Agent, odtwarza tę samą sesję plikową i wznawia dokładny interrupt. Flow kończy się na `PLAN_APPROVED` albo `PLANS_REJECTED`, bez utworzenia lub wysłania oferty.

Ręczne przyciski M2/M3/M5 pozostają dostępne do diagnostyki deterministycznych funkcji bez wywołania modelu. Brak konfiguracji lub błąd Bedrock jest pokazywany jako status agenta `ERROR`; aplikacja nie przełącza się na fake model.

Canonical demo M1–M6 zachowuje dotychczasowy in-memory adapter. M7 używa SQLite jako source of truth dla wielu workflow, kalendarza, material readiness, route snapshots, propozycji, gate/decisions i publicznego trace. `FileSessionManager` nie duplikuje business state; przy rozbieżności SQLite i pliku sesji recovery kończy się fail-safe.

Nowy trwały stan M1/M2 korzysta ze wspólnej granicy `SqlitePersistence` i tej samej bazy SQLite co pozostały business state. Migracje są odkrywane z `migrations/` jako ciągłe pliki `0001_*.sql`, `0002_*.sql`, ...; ich identyfikatory i sumy kontrolne zapisuje `schema_migrations`. `DemoWorkflowStore` pozostaje wyłącznie adapterem zgodności istniejącego Gen1 DEMO i nie jest dopuszczalnym źródłem trwałych mutacji nowego M1/M2.

Migracja `0003_m1_canonical_job.sql` dodaje trwałą tożsamość nowego JOB, dokładny zapis początkowego intake oraz append-only historię faktów z provenance i stanem weryfikacji. Brak faktu nie jest zastępowany placeholderem, a jawne `UNKNOWN` pozostaje odróżnialne od wartości `KNOWN`. `CanonicalJobRepository` zapisuje ten model wyłącznie przez `SqlitePersistence`; istniejący `JobRequest` i `DemoWorkflowStore` nadal obsługują tylko dotychczasowy Gen1 DEMO.

Migracja `0004_m1_lifecycle.sql` realizuje [Frozen M1 Lifecycle Contract v1](docs/decisions/0011-frozen-m1-lifecycle-v1.md): globalnie idempotentne jawne operacje, deterministyczną korelację przez canonical `job_id` lub conversation binding, osobny `activity_state`, append-only follow-up evidence oraz sekwencyjną historię DORMANT/wake tego samego JOB. M1 v1 nie uruchamia timerów nieaktywności, fuzzy matching ani mutacji sterowanych przez LLM.

Migracja `0006_m2_durable_inbox.sql` oraz `M2DurableRepository` tworzą trwałą granicę SQLite dla canonical M2. Implementacja obejmuje durable inbox z rozdzielonymi transakcjami przyjęcia i przetwarzania, deterministyczny replay historycznego wyniku, CAS oparty na rewizji i hashu, trwałe reguły własności evidence oraz transactional outbox. Czysty deterministyczny reducer pozostaje jedynym autorytetem przejść stanu; repozytorium utrwala i weryfikuje jego wynik, lecz nie zastępuje reguł domenowych.

Pierwszy wąski GEN2 M2 runtime slice podłącza wyłącznie `DAY_PLAN_ACTIVATED` przez `POST /api/m2/field-events/day-plan-activated` do `M2DurableRepository` i czystego reducera. Poprawny event trwale aktywuje istniejący `PlanDayRoot`, zapisuje receipt oraz efekt outbox ze statusem `RECORDED`; retry tego samego device `event_id` zwraca historyczny wynik bez ponownej mutacji i bez drugiego efektu. Urządzenie tworzy `event_id`, a serwer tworzy odrębny `server_event_id`.

Fresh-process durable HTTP restart/retry proof wykonuje pierwszy request w procesie A, kończy ten proces, a następnie w procesie B otwiera tę samą bazę i ponawia identyczny device `event_id`. Wynik zachowuje oryginalny `server_event_id`, zwraca `replayed=true` oraz dokładnie jedną canonical mutation, jeden receipt i jeden efekt outbox. Jest to proof trwałości granicy HTTP, a nie deklaracja produkcyjnej infrastruktury deployment/recovery. Nagłówek `X-WERKcrew-Worker-ID` jest kontrolowanym placeholderem identity, a nie produkcyjnym uwierzytelnieniem. Endpoint nie tworzy workera ani plan day.

Drugi wąski atom `POST /api/m2/field-events/unavailable-today-reported` przyjmuje wyłącznie jawny `UNAVAILABLE_TODAY_REPORTED` z frozen reason `SICK`, `PERSONAL_EMERGENCY` albo `OTHER`. Istniejący reducer ustawia tylko `PlanDayRoot.worker_available=false`, pozostawia status planu i `day_close_reported` bez zmian oraz zapisuje receipt i efekt `UNAVAILABLE_TODAY_RECORDED` w outboxie jako `RECORDED`. Nie mutuje tasków ani assignmentów, nie inferuje choroby z ciszy/delay/exception i nie uruchamia M3, reassignmentu ani external delivery. Idempotentny retry i fresh-process retry zwracają historyczny wynik bez drugiego skutku; szczegóły opisuje [M2 Unavailable Today Runtime Slice](docs/mobilewc/M2_UNAVAILABLE_TODAY_RUNTIME_SLICE.md).

Wąski atom `POST /api/m2/field-events/worker-acknowledged` przyjmuje jawny `WORKER_ACKNOWLEDGED` dla dokładnie jednej canonical dyrektywy pracownika. Istniejący reducer zapisuje `ACKED`, receipt i efekt `DIRECTIVE_ACKED`; dla aktualnej dyrektywy `ACTION_REQUIRED` może w tej samej transakcji potwierdzić jej canonical plan reference. Późny ACK starszej dyrektywy nie cofa nowszego planu, a ACK dyrektywy STOP nie usuwa STOP ani safe hold. Retry pozostaje trwały i idempotentny również po restarcie procesu. Endpoint nie implementuje produkcyjnego auth, discovery/delivery dyrektyw, `APP_OBSERVED`, Flutter UI ani wykonania M3; szczegóły opisuje [M2 Worker Acknowledged Runtime Slice](docs/mobilewc/M2_WORKER_ACKNOWLEDGED_RUNTIME_SLICE.md).

Read-only [M2→M3 Unavailable Bridge](docs/mobilewc/M2_M3_UNAVAILABLE_BRIDGE.md) weryfikuje ukończony durable input, proof v2 z wyczerpującym event-time zakresem operacyjnym, dokładny `UNAVAILABLE_TODAY_RECORDED` i historyczną linię aktywacji planu. Z jednego fizycznie read-only snapshotu SQLite buduje głęboko niemutowalny `M3FeasibilityEvaluationRequest` ze stabilnym fingerprintem; nie korzysta z bieżących rootów do uzupełniania historii. Legacy proof v1 pozostaje poprawny dla replay M2, lecz bridge odrzuca go jako niewystarczający. Bridge nie ocenia wykonalności, nie wybiera affected commitments ani replacementu, niczego nie mutuje i nie wywołuje M3 planera, M6/M7, dispatchera, Strands lub LLM. Jest to przygotowana granica requestu do przyszłej świeżej oceny M3, a nie ukończony hero flow ani replan.

Canonical [M3-A Unavailability Impact Evaluator](docs/mobilewc/M3_UNAVAILABILITY_IMPACT_EVALUATOR.md) jest czystą deterministyczną funkcją nad historycznym requestem M2 i bieżącym snapshotem M3, sprawdzanym względem niezależnie zaufanego powiązania M3. `plan_day_id`, opcjonalny `confirmed_plan_reference` i `company_plan_id` pozostają różnymi tożsamościami; hash nie nadaje autorytetu. Wyniki to `NO_LINKED_COMMITMENTS`, `NO_REPLAN_REQUIRED`, `REPLAN_REQUIRED` albo `INSUFFICIENT_INFORMATION` wyłącznie dla jawnie niepełnego pokrycia commitments. `NO_REPLAN_REQUIRED` dotyczy tylko wpływu tej niedostępności na wskazaną rewizję planu, nie pełnej wykonalności. Atom nie zmienia M2, nie wybiera replacementu i nie tworzy replanu. Produkcyjny trusted snapshot producer, persistence i runtime M3 pozostają osobnym krokiem.

Canonical [M3-B Fresh EvaluationInput + Impact Analysis](docs/mobilewc/M3_EVALUATION_INPUT_IMPACT_ANALYSIS.md) zapisuje niezmienną historyczną przyczynę M2 oddzielnie od świeżego, autorytatywnie odczytanego stanu M3/M2. Deterministyczne evidence wskazuje dokładną bazową `PlanRevision`, snapshot M3, bieżące preconditions M2 i cztery rozłączne zakresy wpływu: historyczny, bezpośredni, zależnościowy oraz jawnie nieobecny candidate-induced. Atom nie generuje kandydatów, nie ocenia replacementu i nie wykonuje APPLY.

Canonical [M3-C Bounded Feasibility + Candidate Generation](docs/mobilewc/M3_BOUNDED_FEASIBILITY_CANDIDATE_GENERATION.md) odczytuje dokładnie jeden utrwalony `M3EvaluationInput` i powiązany snapshot C0, po czym czysto i deterministycznie ocenia maksymalnie 12 hipotetycznych wariantów w horyzoncie D..D+2. Strumieniowe wyszukiwanie sonduje najwyżej 13 kanonicznych par zobowiązanie/pracownik, a jedna ścieżka rozszerzeń naprawia pierwszy konflikt nakładania lub jawną relację `FINISH_BEFORE_START`. Kandydat może zmieniać najwyżej trzy zobowiązania udowodnione jako SINGLE; wymagany czwarty ruch jawnie wyczerpuje search envelope. Wynik zachowuje candidate-induced i residual impact oraz jawne ślady odrzuceń/UNKNOWN. CREW pozostaje unsupported. Wynik nie jest finalnym rankingiem, nie używa M5/M6 i nie mutuje planu ani M2; jest content-addressed recomputation bez nowej migracji.

Pozostałe eventy MobileWC, pełny Worker App/UI, M1→M2 materialization, produkcyjne auth, kompletna polityka temporal/offline, dispatcher outboxa oraz integracja Strands/Bedrock nie są zaimplementowane. Sam zapis outbox nie dowodzi external delivery, a odpowiedź HTTP nie zmienia statusu efektu na `DISPATCHED`. M2 product/runtime nadal nie jest zamknięty. Granice opisują [M2 Durable Persistence Checkpoint](docs/mobilewc/M2_PERSISTENCE_CHECKPOINT.md) i [M2 Plan Activation Runtime Slice](docs/mobilewc/M2_RUNTIME_SLICE.md).

Na Windows domyślny storage sesji znajduje się w zapisywalnym katalogu użytkownika `%LOCALAPPDATA%\WERKcrew_AI\strands-sessions`. `WERKCREW_STRANDS_SESSION_DIR` pozostaje jawnym override, np. dla repo-local storage, jeżeli wskazany katalog ma odpowiednie uprawnienia. Pliki sesji nie są częścią repozytorium.

Endpoint M1 pozostaje dostępny pod `/api/demo/job-assessment`, a dokumentacja FastAPI pod `/docs`. Wszystkie linki i redirecty korzystają z bieżącego hosta i portu serwera.

## M7 — ograniczony dispatch

Widok `/demo/m7` pokazuje trzy jawne zlecenia DEMO, calendar/material facts, immutable `ReplanProposal` i per-job public trace. `DailyDispatchPlanner` porównuje najwyżej sześć jawnych kolejności maksymalnie trzech istniejących `ScheduledTask` tego samego pracownika. Nie tworzy crew, nie zmienia worker/vehicle assignment, nie rusza `IN_PROGRESS` i odrzuca naruszenia hard deadline.

`READY`, `EXPECTED` i `BLOCKED` odpowiadają wyłącznie na pytanie, czy zadanie może zacząć się w proponowanym czasie. Routing to zapisany `RouteSnapshot` z point-to-point Amazon Location lub jawnego fixture; planner nigdy nie konsumuje surowej odpowiedzi AWS. M7 nie implementuje geocodingu, Route Matrix, OptimizeWaypoints, globalnego schedulera, procurement/inventory, worker reassignment ani automatycznej zmiany confirmed calendar.

## M8.0 — Business Rule Data Freeze

M8.0 koduje dokładnie 24 SKU, ich `SkuPlanningProfile v1`, sześciu syntetycznych pracowników z kompletną macierzą 6×24 exact-SKU skills, pojazdy i deterministyczne preferencje oraz etykiety EN/DE. WERKcrew DEMO productivity profiles v1 are deterministic simulator parameters. They are not universal construction productivity standards.

Zakres jest jawny: jedno żądane SKU nigdy nie tworzy innego SKU. Skill 1 oznacza pomoc i nie spełnia minimum schedulable w M8. `predecessorIfPresent` obowiązuje tylko wtedy, gdy odpowiedni JobItem istnieje jawnie w tym samym jobie; nie inferuje zakresu. Chunki mają najwyżej 8 godzin, nie dzielą pojedynczego `PIECE`/`ROOM` i tworzą ścisły łańcuch. Invariant jednego pracownika na `ScheduledTask` pozostaje bez zmian.

Wszystkie profile załogi i pojazdów są oznaczone `DEMO_SYNTHETIC`. Import canonical configuration wykonuje deterministyczną walidację fail-fast. M8.0 nie udostępnia jeszcze Jury Lab, New Job UI, dynamicznego adaptera M3, runtime pricingu katalogowego, geocodingu ani innych funkcji M8.1+.

## Testy

Po aktywowaniu środowiska:

```powershell
python -m pytest
```
