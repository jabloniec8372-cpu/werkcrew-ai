# M3-B - Fresh EvaluationInput + Impact Analysis

## Responsibility

M3-B records the exact canonical state evaluated for one historical worker
unavailability incident. It consumes the immutable proof-v2
`M3FeasibilityEvaluationRequest` and uses the trusted M3-B0 producer to resolve
the currently applicable company plan. The bridge request remains historical
cause and evidence; none of the current facts below are written back into it.

The atom answers: **what exact current state was evaluated, under which source
preconditions, and what impact scope followed deterministically?** It does not
choose a replacement or decide full feasibility.

## Authority and ownership

The public writer accepts only the historical bridge request. It does not accept
a caller-created current scope, authority pin, plan revision, impact list or
completeness assertion. `CurrentPlanRepository.produce_current_snapshot()` first
selects the current immutable `PlanRevision` from shared SQLite and derives the
M3-A scope and authority from persisted M3 state plus canonical M2 execution
state. M3-B opens a write transaction and independently repeats that selection
and validation before recording evidence. A stale snapshot therefore fails
without an evaluation row.

Ownership remains explicit:

- M1 publication hashes prove the canonical job/task handoff provenance.
- M2 job-root, task-route and assignment-route evidence describes execution
  reality. `TaskStatus.DONE` removes a commitment from current active scope but
  never removes it from planning or historical evidence.
- M3 `CompanyPlan` and immutable `PlanRevision` describe planning intent,
  placements and explicit dependency constraints.

There is no global cross-module revision and no second operational assignment
truth in M3.

## Canonical EvaluationInput

Schema `m3-evaluation-input-v1` and rule
`m3-unavailability-impact-analysis-v1` cover:

- the source request fingerprint, event/effect proof identities, historical
  assignments and historical M2 job-root preimages;
- company-plan ID, base revision number, deterministic revision ID and revision
  fingerprint;
- the current B0 planning-scope fingerprint and exact plan-day associations;
- every current non-DONE planning commitment, including canonical M1 handoff
  hash and explicit M2 assignment lineage;
- M2 job-root revisions/hashes, task-route hashes/statuses and assignment-route
  hashes/plan-day identities used in the evaluation;
- explicit B0 dependency edges and the four impact scopes.

Collections have canonical ordering. The semantic document excludes only its
derived identity fields. Its SHA-256 is the evaluation-input fingerprint and the
stable ID is `m3-evaluation-input-<fingerprint>`. No clock, UUID, process value,
Python `hash()` or SQL row order contributes to identity.

## Four distinct impact scopes

1. **Historical source scope** reproduces what the immutable M2 bridge proved at
   its processing boundary. It is provenance, not current liveness.
2. **Direct current impact** records the unchanged M3-A result and the current
   active commitments still assigned to the unavailable worker.
3. **Dependency impact** traverses forward only through explicit
   `FINISH_BEFORE_START` edges in the selected B0 revision. DONE tasks are absent
   from active traversal and stop propagation. M2 task or assignment
   supersession is never interpreted as a dependency.
4. **Candidate-induced impact** is canonically
   `NO_CANDIDATE_PROVIDED`, with no candidate fingerprint and no commitments.
   M3-B does not accept or manufacture a planning candidate.

Direct and dependency commitment references are disjoint, current, and ordered
by `(job_id, task_id, commitment_id)`. Dependency origins are ordered task IDs.

## Persistence and replay

Migration `0008_m3_evaluation_input.sql` adds the append-only
`m3_evaluation_inputs` evidence table to the shared SQLite database. Stored JSON
must be canonical, its hash and metadata must agree, and it must reference the
persisted base plan revision. A database trigger and repository validation bind
the company-plan ID, revision number, revision ID and revision fingerprint to one
exact immutable B0 row. Repository writes and reads also require the persisted
dependency-edge tuple to equal that exact revision's complete canonical
dependency tuple. Update, delete and replace are rejected.

Recording is idempotent: an identical semantic input reconstructs the same row
and stable ID; a relevant M2 or M3 state change produces a different ID. Reads
recompute and validate the canonical content, fingerprint, identity and metadata
after restart. This is replay-safe idempotency, not a claim of exactly-once
execution.

## Freshness preconditions for later APPLY

M3-B records, but does not enforce APPLY-time freshness. A later atom can compare
the exact base PlanRevision ID/fingerprint, current planning-scope fingerprint,
M1 handoff hashes, M2 job-root revisions/hashes, task-route hashes and
assignment-route hashes captured here with then-current authoritative sources.
Only source evidence actually used by this evaluation is recorded.

## Non-goals

M3-B provides no candidate generation, replacement search, feasibility solver,
ranking, pricing, authority decision, ACT/ASK/BLOCK, escalation, APPLY, repair
revision, publication, delivery, ACK, human task or Strands/Bedrock integration.
It exposes no API and creates no production snapshot source beyond committed
M3-B0.
