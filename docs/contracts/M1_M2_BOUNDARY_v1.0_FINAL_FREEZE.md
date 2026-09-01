# Stable M1 → M2 Boundary v1.0

**Status:** FINAL FREEZE

**Freeze date:** 2026-09-01

**Purpose:** Stable canonical boundary between durable M1 and canonical M2.

## 1. STATUS AND GOVERNANCE

- This contract is FINAL FREEZE.
- M1 and M2 boundary semantics are frozen.
- Implementation may now follow this contract.
- M3 remains outside this boundary.
- Gen1, M7, and M8 identities are not automatically canonical M1/M2 identities.
- Any future change to this contract requires a demonstrated implementation/test contradiction, not a new product idea.

## 2. OWNERSHIP

- M1 permanently owns canonical `job_id`.
- M1 owns monotonic per-job `source_revision`.
- M1 publishes immutable versioned handoffs.
- Canonical M2 task materialization owns `task_id`.
- M2 scheduling/execution owns `assignment_id`, `plan_day_id`, worker/crew selection, `lead_worker_id`, `stage_id`, and `directive_id`.

## 3. M1 SOURCE REVISION

- JOB creation initializes `source_revision = 1`.
- One committed canonical fact mutation batch increments `source_revision` once.
- A change to fact value, knowledge state, verification state, or provenance counts as a fact mutation.
- `ACTIVE → DORMANT` increments `source_revision` once.
- `DORMANT → ACTIVE` increments `source_revision` once.
- A future lifecycle state included in the handoff projection increments `source_revision` once.
- A waking follow-up with fact changes increments `source_revision` only once.
- A stale fact batch that nevertheless wakes the JOB increments `source_revision` once because activity changed.
- An evidence-only follow-up without a fact or activity change does not increment `source_revision`.
- Conversation binding, retry, duplicate/no-op operation, persisted rejection, timestamps, and audit-row creation alone do not increment `source_revision`.

`source_revision` advancement must be atomic with the publication-relevant M1 mutation and durable across restart.

## 4. IMMUTABLE HANDOFF

Each `(job_id, source_revision)` identifies exactly one immutable handoff projection.

The handoff contains at minimum:

- `handoff_id`;
- canonical `job_id`;
- M1 `source_revision`;
- projection schema version;
- current lifecycle/activity metadata;
- selected canonical fact revisions preserving:
  - fact name;
  - value;
  - absent versus `UNKNOWN`;
  - `KNOWN`;
  - `VERIFIED` / `UNVERIFIED`;
  - provenance;
  - relevant evidence reference.

Raw intake, complete evidence, conversation bindings, and complete fact/lifecycle history may remain reachable by canonical references instead of being duplicated in the projection.

A same-version retry must return the original publication. The same `(job_id, source_revision)` with different content is a conflict and must fail closed. A newer handoff never mutates an older handoff.

## 5. UNSCHEDULED HANDOFF EXCLUSIONS

An unscheduled M1 handoff does not require:

- `task_id`;
- `assignment_id`;
- `plan_day_id`;
- worker selection;
- crew selection;
- `lead_worker_id`;
- `stage_id`;
- `directive_id`;
- scheduling time or order;
- vehicle or resource selection.

## 6. TASK MATERIALIZATION

- Canonical M2 task materialization creates `task_id`.
- Task identity does not come from list order, planner output, worker selection, availability, or chunk sequence.
- Before execution/completion semantics are usable, a durable task definition must include:
  - `task_id`;
  - `job_id`;
  - source `handoff_id` / `source_revision`;
  - immutable business meaning;
  - completion type;
  - postconditions;
  - evidence requirements;
  - quantity/unit semantics where required;
  - stable stages and `stage_id` where applicable;
  - single-worker versus crew requirements;
  - a task-definition version or replacement/supersession relation.
- A materially replaced task receives a new `task_id`.
- Old task definitions remain immutable and addressable.

## 7. ASSIGNMENT IMMUTABILITY

One `assignment_id` represents one durable execution context.

The following cannot silently change beneath an `assignment_id`:

- `job_id`;
- `task_id` and the applicable immutable task-definition version;
- single-worker versus crew kind;
- assigned worker or crew membership;
- `lead_worker_id`;
- completion/exception authority;
- source handoff/task-materialization reference;
- applicable plan-day/business-date occurrence;
- supersession/replacement relation.

A material change creates a new `assignment_id`. Specifically:

- reassignment creates a new `assignment_id`;
- crew-membership change creates a new `assignment_id`;
- lead change creates a new `assignment_id`;
- moving work to another plan day creates a new `assignment_id`;
- task replacement creates a new `task_id` and a corresponding new execution context.

Historical assignment records remain addressable for late/offline events.

## 8. LATE/OFFLINE EVENTS

- Late events continue to target their historical references.
- Late events cannot mutate successor assignments or tasks.
- Superseded historical events remain auditable.
- Ambiguous, stale, cross-scope, or missing identity fails closed.
- A newer M1 handoff never changes historical task or assignment meaning.

## 9. WORKER / CREW / LEAD

- Canonical M2 requires one durable worker identity namespace.
- Crew execution is represented by `assignment_id`.
- A separate `crew_id` is not required by v1.
- A crew assignment requires durable membership.
- Every crew assignment requires `lead_worker_id`.
- `lead_worker_id` must be one of the assignment members.
- Only the lead can complete the whole crew assignment.
- An invalid, missing, or non-member lead fails closed.

## 10. PLAN DAY

`plan_day_id` represents:

- one canonical worker;
- one business day;
- zero or more assignments/tasks;
- potentially multiple JOBs.

`plan_day_id` exists only after scheduling/plan issuance. It is not part of M1 intake or an unscheduled M1→M2 handoff.

This contract does not freeze a concrete business timezone because the M2 Behavioral Contract does not define the authoritative business timezone. The authoritative business timezone remains an M2 implementation/configuration decision that must be made before plan-day implementation.

## 11. DIRECTIVES AND ACK

- `directive_id` identifies one immutable backend-issued directive.
- `WORKER_ACKNOWLEDGED` applies only to the referenced `directive_id`.
- ACK means seen and understood, not unconditional execution consent.
- A late ACK cannot acknowledge or authorize a newer directive, assignment, or plan.
- `WORKER_ACTION_EXCEPTION` applies only to the referenced `ACTION_REQUIRED` or `STOP_DIRECTIVE` directive.
- Directive ordering follows durable issuance/supersession order, not network arrival order.
- A STOP exception preserves the safe-hold semantics required by the frozen MobileWC contract.

## 12. FAIL-CLOSED IDENTITY RULE

MobileWC/M2 effects require existing canonical references. Missing, stale, ambiguous, cross-job, cross-task, cross-stage, cross-assignment, or otherwise incompatible references produce no state-changing effect.

Where present, `against_event_id` must resolve to the exact existing event being disputed within a compatible canonical scope. It does not authorize inference or retargeting.

Legacy lookalike IDs from Gen1, M7, or M8 are not accepted as canonical mappings without explicit durable mapping.

## 13. IMPLEMENTATION GAPS — NOT CONTRACT GAPS

The following are implementation work only:

- canonical aggregate M1 `source_revision`;
- append-only handoff-publication persistence and recovery;
- canonical M2 task definitions/materialization;
- canonical worker registry;
- assignment, membership, lead, and supersession persistence;
- plan-day persistence;
- stable stage definitions;
- directive persistence and ACK/evidence relationships;
- MobileWC event inbox, idempotency, and rejection persistence;
- explicit authorized legacy mappings where needed.

These gaps do not reopen the boundary contract.

## 14. REQUIRED TEST FAMILIES

Future implementation must provide three test families. They are recorded here but are not implemented by this freeze.

### A. Boundary publication tests

- restart durability and deterministic recovery;
- same-version retry returns the original immutable handoff;
- same-version different-content conflict fails closed;
- atomic publication-relevant mutation and revision advancement;
- correct revision relevance for fact batches, dormant/wake transitions, stale fact batches, and evidence-only follow-ups;
- stale/newer and two-process publication ordering;
- canonical identity preservation and rejection of unmapped legacy lookalikes.

### B. Task-materialization tests

- explicit durable task definition before execution semantics;
- task identity independent of order, planner output, worker, availability, and chunk sequence;
- idempotent materialization from the same handoff/task definition;
- stable task meaning across replanning;
- new identity and immutable history for materially replaced work;
- stage, quantity, evidence, and completion requirements are scoped to the correct task definition.

### C. Later MobileWC runtime tests

- durable event inbox/idempotency and offline/late-event handling;
- assignment replacement, lead change, crew change, plan-day move, and historical-context preservation;
- plan-day worker/business-day scoping;
- lead membership and completion authority;
- directive identity, ACK, exception, delivery evidence, and supersession ordering;
- fail-closed missing, stale, ambiguous, and cross-scope references;
- STOP exception and durable safe-hold behavior.

## 15. FINAL FREEZE STATEMENT

M1→M2 BOUNDARY v1.0 — FINAL FREEZE
