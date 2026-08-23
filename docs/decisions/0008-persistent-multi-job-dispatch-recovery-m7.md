# 0008 — Persistent Multi-Job Dispatch & Recovery M7

- **Status:** USTALONE dla M7 DEMO
- **Data:** 2026-08-23

## Kontekst

M6 utrwalał sesję Strands, ale `DemoWorkflowStore` był pojedynczym in-memory adapterem. M7 wymaga wielu izolowanych workflow, odzyskania business state po prawdziwym restarcie procesu oraz owner-controlled korekty kolejności istniejących przydziałów.

## Decyzja

1. SQLite jest jedynym source of truth dla business state M7. `FileSessionManager` zachowuje wyłącznie session/HITL continuity. Rozbieżność pending gate i sesji kończy recovery fail-safe.
2. Migracja `0001_m7_persistent_dispatch.sql` tworzy jobs, versioned workflow snapshots, session bindings, normalized calendar/material/route/proposal/trace, gates/decisions i idempotency records. Snapshot nie może zawierać autorytatywnych kopii normalized facts.
3. Każda mutacja ma explicit job/workflow/proposal scope. `revision` i assignment revisions są sprawdzane przez CAS w `BEGIN IMMEDIATE`; stale data nie jest nadpisywana.
4. Czas planowania (`now`, `planning_date`, workday bounds) jest jawny i timezone-aware w `Europe/Berlin`.
5. M7 ma strukturalny adres i wyłącznie preconfirmed fixture coordinates. Nie implementuje geocodingu. Public trace nie zapisuje adresu ani współrzędnych.
6. Material Readiness ma tylko READY/EXPECTED/BLOCKED. `EXPECTED` z blocking=true ustala najwcześniejszy start i conditional proposal; `BLOCKED` blokuje; blocking=false nie blokuje.
7. Amazon Location `geo-routes.CalculateRoutes` jest point-to-point fact providerem. Adapter zapisuje wyłącznie minimalny `RouteSnapshot`; planner nie widzi raw AWS response.
8. `DailyDispatchPlanner` porównuje maksymalnie sześć kolejności maksymalnie trzech istniejących zadań jednego pracownika. Nie zmienia worker/vehicle assignment, nie rusza IN_PROGRESS, odrzuca hard-deadline violation i rankuje wykonalne warianty leksykograficznie: changed commitments, worker idle time, travel time, stabilny job/task tie-breaker. Nie wprowadzono nowej klasy soft deadline.
9. `ReplanProposal` jest immutable, cross-job i revision-bound. Confirmed calendar zmienia się wyłącznie po realnym Strands interrupt/resume i decyzji OWNER z Coordinator UI. Atomic apply zapisuje decision, calendar, proposal, workflow revisions, idempotency i skorelowany trace.
10. Retry po commicie nie tworzy drugiej decyzji ani business effect. Restart nie auto-resumuje gate.

## Ownership

- `workflow_instances.snapshot_json`: wyłącznie job-local requirements/report/plans/pricing/status metadata,
- `calendar_assignments`: confirmed operational schedule,
- `material_readiness`: readiness fact per job/task,
- `route_snapshots`: minimalne persisted provider facts,
- `replan_proposals` + association tables: immutable cross-job proposal,
- `pending_gates` / `owner_decisions`: gate-scoped human effects,
- `agent_trace_events`: append-only public facts.

## Granice

M7 nie jest globalnym optimizerem, crew plannerem, inventory/procurement, geocoderem, Route Matrix, waypoint optimizerem, traffic monitor, distributed transaction system ani HA/multi-worker deployment. Jeden `ScheduledTask` nadal oznacza jednego pracownika. Confirmed schedule nie jest autonomicznie zmieniany.
