# M3-B0 - Authoritative Current-Plan Snapshot Bootstrap

This prerequisite supplies the durable canonical planning source that M3-A
intentionally did not implement. It does not implement M3-B EvaluationInput or
repair execution. The M3-A contract and M2 contracts remain unchanged.

## Ownership and identities

`CompanyPlan` has a stable explicitly assigned identity and immutable import
provenance. `PlanRevision` is a complete immutable snapshot of planning intent:
plan-day associations, intended task placements, and explicit dependency edges.
Its nonnegative revision number, exact parent identity and content fingerprint
identify the imported baseline. All preceding revisions remain readable.

The identities stay separate:

- M2 `plan_day_id` identifies an existing canonical worker-day.
- M2 `confirmed_plan_reference` remains optional, opaque historical operational
  confirmation. It is neither a CompanyPlan identity nor a membership certificate.
- M3 `company_plan_id` + `revision` identify a canonical planning baseline.
  `revision_id` additionally identifies the entire immutable revision content.

M3 stores intended placements, not another M2 execution calendar. Commitments
reference existing canonical M1/M2 jobs/tasks and exact task-definition versions,
M1 handoff IDs and source revisions. Optional M2 assignment IDs are provenance.
There are no copied execution statuses, receipts, released-worker sets, directive
states, delivery states, ACKs, availability records or operational assignments.
Only M2 owns execution truth. An M3 import does not change any M2 root or route.

Current commitment intent comes from the selected complete M3 revision. Before
certifying active membership, the trusted producer excludes a task when canonical
M2 execution reality definitively reports `DONE`. It does not infer broader
planning meaning from WAITING, blocked, released or superseded state. The planning
commitment and its history remain intact; only the current active scope omits the
completed work.

## Authority: persisted source, not a submitted hash

`CurrentPlanRepository` uses the existing shared `SqlitePersistence` database.
There are two deliberately different boundaries:

1. `import_revision(company, revision)` is a **privileged trusted-host baseline
   import**. Like existing canonical root bootstrap operations, it must not be
   exposed as an unauthenticated client operation. The host selects the actual
   complete baseline and explicit provenance. A provenance string, DTO constructor
   or content hash does not authenticate the importer or prove real-world truth.
2. `produce_current_snapshot(request)` is a **read-only trusted producer**. It
   accepts only the historical M2 request. It accepts no CompanyPlan selector,
   caller scope, authority DTO, proposed commitments or coverage flag.

The producer first validates the request and reconstructs the exact unavailable
cause through the existing durable proof-v2 bridge. A caller re-hashing a changed
request is rejected. It then resolves persisted plan-day ownership and the latest
contiguous PlanRevision history in one SQLite read transaction. It validates
association and canonical task/publication/assignment identity bindings and
enumerates the complete applicable scope from that revision.

Only after independently selecting that persisted state does it create the
M3-A scope and `M3PlanningScopeAuthority` pin. This is not authorization of a
caller-submitted scope by hashing it. `evaluate_current_unavailability(request)`
is the convenience path that produces the snapshot and passes the independently
derived authority directly to unchanged M3-A.

`CurrentPlanSnapshot` exposes the verified request, CompanyPlan, full PlanRevision
(including its dependency edges), M3-A scope and authority. It is producer output,
not a credential to deserialize back from a client. The pure M3-A function retains
its existing trusted-host contract; a malicious host replacing both of its inputs
or a privileged database writer is not authenticated by Python DTOs or hashes.
Host access control, importer UI/API and tenant authentication remain outside B0.
The host also pins the shared database path; a client-selected database is not an
independent authority source. B0 does not authenticate an arbitrary SQLite file.

## Complete applicable scope

The producer enumerates the union of:

1. Every non-DONE current placement for the unavailable worker on the request's
   business date, including placements with no historical M2 assignment lineage.
2. Every non-DONE current placement explicitly deriving from an assignment in the
   verified historical request, including placements now intended for another worker.

`coverage_complete=True` is derived by exhaustive enumeration of the persisted
complete revision. It is not an import field or producer caller assertion.
An empty current scope is valid when the association still exists in the current
revision. A missing association, a removed current association, corrupt history
or invalid canonical binding fails closed rather than becoming incomplete coverage.
M3-A's four outcomes and their deliberately limited meaning are unchanged.

## Minimal bootstrap constraints

- One CompanyPlan permanently owns each associated plan day and worker/date.
  Duplicate worker-day ownership and cross-company transfers fail closed.
- There is one unambiguous dated placement per canonical task in a revision.
  Commitment ID, task/job/version, source handoff/revision and business date retain
  their identity across history, including after removal. A task cannot silently
  migrate to another CompanyPlan or acquire a renamed commitment identity.
- All intended workers have an associated existing M2 worker-day for that date.
  An empty intended-worker set is allowed but makes no feasibility claim.
- Explicit M2 lineage must reference the **same canonical task**. Every referenced
  assignment's plan day must be associated in the revision on the placement date.
  Cross-date lineage, successor-task lineage interpretation, ownership transfers
  and multi-date task placement need later explicit contracts; B0 does not guess.
- A trusted new complete baseline may retain, remove or add placements and change
  intended worker sets. Import does not select replacements or authorize/execute
  repairs. It is not the future APPLY mechanism and must not be used as one.

These fail-closed restrictions keep bootstrap ownership unambiguous. They are not
new M2 invariants and do not narrow the already-audited pure M3-A DTO contract.

## Dependencies: explicit canonical constraints only

Gen1 `PlanningWorkItem.predecessor_ids` resolve Gen1 work-item identities inside
`crew_planner.py`; no proven semantics-preserving mapping to canonical M2 task
identities exists in that code. They are not copied into this model.

`TaskDependency` contains canonical predecessor/successor task IDs, the single
relation `FINISH_BEFORE_START`, and explicit provenance. Its meaning is that the
predecessor must finish before the successor may start, not that one task replaces
the other. Both endpoints must have placements in the same applicable revision;
cross-job edges are permitted when both canonical identities are present.
Duplicate edges, self-edges, unknown endpoints, unsupported relations and cycles
are rejected. No scheduling times, transitive impact closure or solver are computed.

TaskDefinition and Assignment supersession remain provenance about replacement of
identities, **never dependency semantics**. A regression creates real superseding
M2 tasks and assignments and proves an explicitly empty M3 edge set stays empty.

## Persistence, immutability and replay

Migration `0007_m3_current_plan_bootstrap.sql` adds only:

- `m3_company_plans`;
- `m3_plan_day_associations`;
- `m3_plan_revisions`.

Existing tables and migrations 0001-0006 are unchanged. The existing sequential
migration runner installs and verifies the new schema. Existing migration tests
only advance expected migration IDs; M1/M2 behavioral assertions are unchanged.

The shared `BEGIN IMMEDIATE` transaction imports a CompanyPlan, associations and
revision atomically. Revision zero has no parent; each subsequent import requires
the exact current parent and next number. Conflicting/stale imports fail without
partial rows. Exact replay returns the original immutable revision even after
later imports; it cannot move the current selection backwards. Current selection
is the greatest revision in a verified gap-free parent chain, not an editable head.

SQL guards prohibit update, delete and INSERT OR REPLACE of identity/history rows,
including with recursive triggers disabled. Foreign keys bind plan-day identities;
canonical JSON, SHA-256, metadata and parent checks protect revision rows. Repository
writes and reads additionally validate nested semantic content and canonical
M1/M2 references. Reads reject checksum, schema, metadata or association corruption.
This is integrity checking within the shared trusted database, not a signature.

Revision schema is `m3-plan-revision-v1`. IDs are `m3-plan-revision-` followed by
the SHA-256 of the canonical semantic revision document, including schema, company,
number, parent, provenance, all associations, placements and dependency edges.
Dates use ISO form; plan days sort by ID, commitments by `(job_id, task_id,
commitment_id)`, edges by `(predecessor_task_id, successor_task_id)`, nested identity
sets lexically. Duplicate identities are rejected. Frozen DTOs defensively
normalize constructor input and recursively revalidate before serialization;
reflectively mutated collections are rejected, not silently normalized.
No clock, UUID, randomness or process identity contributes to plan identities.

Producer reads use SQLite `mode=ro`, `query_only=ON` and an explicit read transaction.
They do not initialize/migrate/create a database. The immutable historical bridge
read precedes the current-state transaction; history is never backfilled from
current state. Reader coordination may affect SQLite `-shm` metadata, not durable
business rows or WAL content. A concurrent append cannot mix two current revisions
inside one producer result. A returned snapshot is evidence of the revision read,
not permission to apply it after the database changes.

## Validation and next boundary

Focused unit/integration tests cover canonical ordering and process fingerprints,
recursive validation, exact canonical task/publication/assignment binding,
explicit dependency validation and no supersession inference, migration from 0006,
immutable SQL history, replay/conflict rollback, removed identity ownership,
current selection during a concurrent append, corruption rejection, real null and
opaque M2 confirmation references, re-hashed caller forgery rejection, M3-A using
persisted producer output, unchanged M2 tables, read-only connections and fresh
processes with different PYTHONHASHSEED values.

Later M3-B can consume the verified historical cause, full revision/dependency
snapshot and deterministic IDs to build fresh EvaluationInput and source
preconditions. B0 does not persist EvaluationInput or derive four impact scopes.
Candidate generation, feasibility, ranking, M5/M6, ACT/ASK/BLOCK, APPLY,
repair-created revisions, PublicationBinding execution, M2 publication, delivery,
ACK, HUMAN_TASK, Strands/Bedrock and hero E2E remain outside this atom.
