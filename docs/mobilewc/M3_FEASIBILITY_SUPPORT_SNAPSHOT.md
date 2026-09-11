# M3-C0 authoritative feasibility support

M3-C0 persists immutable facts for later feasibility work. It does not create,
evaluate, reject, rank, approve or apply repair candidates.

## Authority and identity

`M3FeasibilitySupportRepository.record_feasibility_support` accepts only a
persisted `M3EvaluationInput` ID. It reloads that input, its exact immutable
`PlanRevision`, the current authoritative M2 worker registry and host-owned
source records inside one `BEGIN IMMEDIATE` transaction. The registry's exact
canonical JSON, revision and content fingerprint are captured as immutable,
content-addressed M3-C0 provenance in that transaction. The first capture of
that exact provenance also appends a typed worker-registry capture entry to the
same feasibility ledger chronology used by source records. A caller cannot
submit a composed support snapshot, a skill map, a placement subset or a scope
credential.

The final identity is the SHA-256 of canonical semantic JSON. It contains no
clock value or random UUID. Source records keep their per-kind, per-subject
revision chains and also receive an atomic generation in a ledger local to M3
feasibility evidence. Source records and worker-registry capture entries share
one unique, gap-free generation sequence. Snapshot creation captures an
immutable source-selection cut at the current ledger generation and binds the
registry provenance ID, capture ID and authentic capture generation. Missing
evidence is an explicit no-source selection at that cut and is emitted as
`UNKNOWN`.

## Authoritative sources

| Target evidence | Source and binding |
| --- | --- |
| Evaluation cause | Exact durable M3-B `M3EvaluationInput` ID and fingerprint |
| Plan | Exact `CompanyPlan` and `(revision, revision_id, fingerprint)` tuple from B0 |
| Task/job identity | Exact M2 task route and immutable definition lineage already bound by the EvaluationInput and PlanRevision |
| Task SKU/skill/vehicle requirement | Privileged `TaskConstraintSource`; exact SKU is explicit, while required skill, minimum level and vehicle class are bound to an immutable persisted M8 feasibility-configuration fingerprint. Historical sources restore that evidence rather than consulting the currently selected M8 configuration. |
| Placement intervals | Privileged `PlanScheduleSource`, bound to one exact PlanRevision and required to enumerate every commitment in that revision |
| Worker identity and skill | Exact historical M2 worker-registry provenance (canonical content, revision and fingerprint) plus exact-ID M8 skill rows; an M2 worker absent from M8 has unknown technical skill, not inferred skill |
| Worker/vehicle availability | Per-subject host records with explicit coverage and canonical UTC windows; no row means `UNKNOWN` |
| Readiness | Per-task host record with `READY`, `EXPECTED`, `UNKNOWN` or `BLOCKED`; elapsed time and empty blockers do not change the state |
| Vehicle capability | Frozen M8 vehicle ID/kind/capabilities; private authority evidence is explicitly not included |
| Route/travel/buffer | Persisted offline route record bound to exact location fingerprints, transport mode, route version and buffer-rule version; no row means `UNKNOWN` |
| Customer window/deadline | Exact-task `TaskConstraintSource` values with `KNOWN`, `ABSENT` or `UNKNOWN` knowledge state |

## Gen1 reuse decision

No Gen1 dispatch DTO or table is imported. Its calendar assignments lack the
exact Gen2 task-definition and PlanRevision binding required here, and its
readiness, availability and route rows are owned by a separate dispatch model.
Treating those rows directly as Gen2 planning truth would not be a proven
semantics-preserving adapter. M3-C0 therefore uses narrow Gen2 source contracts
and does not alter Gen1 or M2 semantics.

## Durability

Migration `0009_m3_feasibility_support.sql` adds append-only source records,
historical M8 and worker-registry provenance, immutable typed worker-registry
ledger captures, source-selection cuts, support snapshots and snapshot-to-source
selection bindings. Every identity-bearing table uses `WITHOUT ROWID`, so
`INSERT OR REPLACE` cannot bypass append-only history through a hidden physical
row identity even when recursive triggers are disabled. Generation allocation
across both source and registry-capture rows is gap-free and backdating-resistant
under the repository's `BEGIN IMMEDIATE` boundary; the registry capture, cut and
support are recorded in that transaction.

On reconstruction the repository independently derives the required subject
universe from the exact EvaluationInput, PlanRevision, captured historical
worker registry and historical M8 configuration. It does not consult the
current M2 worker-registry singleton, so a later registry revision cannot
reinterpret old evidence. It validates the preserved gap-free chronology, loads
the exact immutable capture row, proves that row binds the referenced provenance
and requires its generation to be no later than the cut. A later valid capture,
an uncaptured provenance row or a different capture therefore cannot be
re-fingerprinted into an older cut. For each subject it then recomputes the
authoritative head from source rows whose generation is no later than the
captured cut. It rejects forged no-source selections, older valid revisions,
omitted subjects and sources created after the cut, while later revisions do
not invalidate a historical support snapshot. Semantic evidence is compared
with the exact selected records as a separate validation layer.
