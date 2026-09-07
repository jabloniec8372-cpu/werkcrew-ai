# M2 to M3 Unavailable Historical Bridge

## Status

This is a read-only boundary from one completed M2 unavailable fact to an
immutable request for a future M3 feasibility evaluation. It does not perform
feasibility assessment, replan, dispatch, or any canonical mutation.

## Durable event-time authority

Future `UNAVAILABLE_TODAY_REPORTED` processing writes
`m2-reduction-input-proof-v2` in the same TX2 as the plan-day mutation,
receipt, result vector, and outbox effect. Before reduction, the repository
enumerates every assignment-plan link for the exact `plan_day_id`, resolves
the distinct job routes, verifies their immutable projections, and captures
the exact canonical job-root preimages in stable job-ID order. The proof also
contains the historical plan-day preimage and the explicit exhaustive-scope
certificate:

```text
complete_plan_day_assignment_scope_ids = [plan_day_id]
```

The extra job roots are historical read authority only. They do not expand
the reducer's mutation authority or change unavailable business behavior.

Existing proof v1 rows remain valid for ordinary M2 replay. A v1 unavailable
row is insufficient for this bridge and fails closed with
`M2InsufficientHistoricalEvidenceError`; it is never completed from current
roots and is not automatically backfilled.

## Single read-only reconstruction

The bridge makes one repository call for the identity
`FIELD_EVENT/{event_id}/effect/{ordinal}`. That operation:

- requires the database file to exist;
- opens SQLite through a URI with `mode=ro` and `PRAGMA query_only=ON`;
- never calls `initialize()` or the normal WAL-configuring connection path;
- starts one explicit read transaction before reading any evidence;
- verifies canonical input, hashes, proof v2 and completeness, receipt/result
  vectors, the exact outbox effect, and activation ancestry;
- reconstructs the post-unavailable plan and linked jobs only from the
  immutable event-bound proof.

It never queries current assignment-plan links or current job/plan roots to
complete, correct, or verify the historical operational scope. Missing,
corrupt, mismatched, or ambiguous evidence fails closed. A missing database is
not created and no database, WAL, SHM, or journal artifact is produced.

## Activation lineage

`ACTIVE` is not sufficient. Starting from the unavailable proof's exact
plan-day precondition revision/hash, reconstruction walks the unique immutable
completed-inbox transition chain backwards. It requires an ancestor applied
`DAY_PLAN_ACTIVATED` input, its receipt and server identity, and its exact
ordinal-zero `PLAN_DAY_ACTIVATED` outbox effect. Each intermediate transition
must connect adjacent revision/hash values. Missing, corrupt, mismatched, or
ambiguous ancestry is rejected.

## Immutable request and fingerprint

`OperationalImpactSnapshot` contains the source input/proof/effect hashes,
activation lineage and transition hashes, exact historical post-event plan,
plan preimage/result hashes, full linked assignment/task/crew facts, and exact
job preimage revision/hashes. All public collections are canonical tuples;
mutable nested lists, sets, or dictionaries do not escape.

`M3FeasibilityEvaluationRequest` uses schema
`m2-m3-feasibility-evaluation-request-v2`. Its SHA-256 fingerprint is computed
from deterministic canonical JSON covering every semantic request field.
Semantically unordered values have explicit stable ordering. There is no
timestamp generation, randomness, Python `hash()`, `repr()`, object identity,
or process-local authority.

Only a valid v2 exhaustive proof with a genuinely empty linked scope yields
`NO_LINKED_ASSIGNMENTS`. That status still requests fresh M3 evaluation and is
not a feasibility verdict.

## Explicit non-goals

- M3 feasibility or affected-commitment decisions;
- replacement selection or replan generation/application;
- M2/M3 state mutation or outbox dispatch;
- M1, M6, M7, agent, Strands, Bedrock, LLM, or network invocation;
- legacy proof backfill or inferred historical truth.

M2 PRODUCT/RUNTIME remains **NOT CLOSED**.
