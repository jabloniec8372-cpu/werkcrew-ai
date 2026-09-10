# M3-A — Deterministic Unavailability Operational-Impact Evaluator

Status: pure domain slice implemented; no production M3 snapshot producer,
persistence, API, replan, or hero-flow orchestration is provided by this atom.

## Responsibility

At one explicitly authorized current company-plan revision, identify active
commitments in the supplied worker-day scope that still depend on the worker
whose unavailability was recorded by M2. The evaluator does not establish full
feasibility or the existence of a replacement.

## Authority and distinct identities

- `plan_day_id` is M2's worker-day identity.
- `confirmed_plan_reference` is M2's optional recorded operational confirmation
  reference. It is historical provenance, not `company_plan_id` or a membership
  certificate. It may be null or differ from the current company-plan ID.
- `company_plan_id` and `company_plan_revision` identify the M3 snapshot being
  evaluated. Its revision is independent of the M2 plan-day revision.

The public entry point is:

```python
evaluate_unavailability_impact(request, scope, *, authority=trusted_association)
```

There are two sources of authority, supplied through three explicit values:

1. `request`: the immutable `M3FeasibilityEvaluationRequest` v2 produced by the
   existing verified M2 historical bridge. The evaluator checks its supported
   semantics, shape, scope coherence, and existing fingerprint. It does not
   duplicate durable proof verification or read current M2 roots.
2. `scope`: an immutable `M3CurrentPlanningScope`, containing the company-plan
   ID/revision, exact M2 plan-day ID, unavailable worker, business date, source
   request fingerprint, `coverage_complete`, current commitments, and computed
   snapshot fingerprint.
3. `authority`: a mandatory `M3PlanningScopeAuthority` selected independently by
   trusted host application code. It pins those same association fields and the
   exact expected scope fingerprint. The evaluator compares both the scope and
   M2 request against this trusted value before making any impact decision.

Authority comes from the host's trusted M3 planning producer and its authoritative
plan selection, not from the fingerprint, class name, constructor, or client
claim. The host must never deserialize this authority from the same untrusted
submission as the scope or automatically authorize a submitted scope by hashing
it. M3-A provides no such automatic factory. Tests explicitly supply fixture-owned
authority; those fixtures do not prove production provenance.

A self-consistent replacement of both scope and trusted authority by an attacker
is outside this pure function's trust boundary. The function cannot authenticate
its host. A future production adapter must resolve and validate the association
from canonical M3 state and inject the expected snapshot independently. That
adapter, durable storage, company/tenant authorization, and freshness selection
are separate work. M3-A evaluates the supplied authorized revision; it does not
discover whether a newer revision exists or authorize applying an old result.

The trusted scope producer must certify current commitment liveness and the
exhaustive union of (a) current active commitments assigned to this worker for
the business date and (b) current active commitments deriving from historical
M2 assignments in the request. Commitment records have no scheduling intervals;
their inclusion in this operational scope is a producer assertion. The evaluator
cannot prove enumeration completeness from a filtered tuple alone. The coverage
flag and all membership data are included in the independently pinned hash.

## Current commitments and historical evidence

`M3CurrentCommitment` carries stable commitment/job/task IDs, assigned-worker IDs,
and source M2 assignment lineage IDs. All collections are defensive canonical
tuples; duplicate identities are rejected. An unassigned active commitment may
have an empty worker set; M3-A makes no general claim about its executability.

A supplied commitment is affected exactly when its assigned-worker set includes
the unavailable worker. One affected record is returned per unique commitment,
including its explicit lineage and the subset matching the historical request.
Historical `DONE`, `WAITING`, released workers, and supersession references are
never used to infer current liveness or override an authoritative current M3
assignment. A replacement commitment can retain historical lineage but use a
different worker, producing no affected record for that commitment.

A current worker-day commitment without matching historical lineage remains
affected and is flagged `M2_ASSIGNMENT_LINEAGE_MISSING`. Missing lineage must not
hide current worker dependence. A commitment with neither worker dependence nor
matching historical lineage is outside the declared scope and fails validation.
Matching historical lineage from another job also fails. Task IDs may change
through succession; old and current task IDs need not be equal.

## Outcomes

| Outcome | Meaning |
| --- | --- |
| `NO_LINKED_COMMITMENTS` | Complete current scope is empty and the certified historical assignment scope is empty. |
| `NO_REPLAN_REQUIRED` | Complete current scope contains no commitment still depending on this worker; historical links may exist. This event alone requires no replan at this revision. |
| `REPLAN_REQUIRED` | Complete scope contains at least one active commitment still assigned to this worker. No replacement is asserted to exist. |
| `INSUFFICIENT_INFORMATION` | Otherwise valid and authorized scope explicitly declares incomplete commitment coverage. |

Incomplete coverage takes precedence: observed affected records are retained as
partial findings, but the result remains `INSUFFICIENT_INFORMATION`. Empty
partial coverage never becomes `NO_LINKED_COMMITMENTS` or `NO_REPLAN_REQUIRED`.
Binding and structural validation runs before this classification.

Deterministic reason codes are `HISTORICAL_AND_CURRENT_SCOPE_EMPTY`,
`NO_CURRENT_WORKER_DEPENDENCE`, `UNAVAILABLE_WORKER_STILL_ASSIGNED`,
`CURRENT_SCOPE_INCOMPLETE`, and the supplementary
`M2_ASSIGNMENT_LINEAGE_MISSING`.

## Typed failures

All domain boundary errors inherit `M3ImpactBoundaryError` and carry stable
`code` and `field_name` values; messages do not include arbitrary input data.

- `M3ImpactValidationError`: invalid DTO, identity, schema, duplicate, or request
  semantics (`INVALID_VALUE`).
- `M3ImpactFingerprintError`: request/scope content fingerprint mismatch or a
  scope differing from the independently trusted snapshot pin.
- `M3ImpactAssociationError`: missing authority; mismatched company-plan ID or
  revision, worker, date, plan day, or source request; cross-job lineage; or a
  commitment outside the declared scope.

None of these failures becomes `INSUFFICIENT_INFORMATION`. Frozen DTOs are
revalidated on consumption; Python reflection is not treated as a security
boundary. Hashes provide integrity and content identity, not authentication.

## Determinism and replay

- Scope schema: `m3-current-planning-scope-v1`.
- Trusted association schema: `m3-planning-scope-authority-v1`.
- Result schema: `m3-unavailability-impact-result-v1`.
- Rule version: `m3-unavailability-impact-v1`.
- Commitments and affected records sort by `(job_id, task_id, commitment_id)`.
- Nested IDs and reason codes sort lexicographically; duplicates are invalid.
- Scope SHA-256 covers its full canonical JSON document except its own hash.
- Result SHA-256 covers every semantic output field, including both input
  fingerprints and schema/rule versions, except its own hash and derived ID.
- `evaluation_id` is `m3-impact-` followed by the full result SHA-256 digest.

There is no generated timestamp, UUID, process identity, Python `hash()`, or
random input. Public serializers return deterministic canonical JSON. Replay
against the same request and authorized scope reconstructs the identical result
and identity across processes and `PYTHONHASHSEED` values. Different authorized
plan revisions or semantic inputs produce different identities.

This is recomputation, not a durable M3 inbox or saved-result repository. Repeating
evaluation produces no durable effects, including no duplicate effects.

## M2 compatibility and side effects

M2 models, reducers, runtime, persistence, proof v2, bridge semantics, migration
0006, and frozen behavioral documents remain unchanged. Null historical
confirmation references are neither rejected as company-plan errors nor
backfilled. A later ACK changes current M2 confirmation state but cannot change
the older unavailable request or fingerprint.

The evaluator has no repository/service/orchestrator dependency or I/O calls.
It consumes the existing bridge DTO and canonical serialization helpers; the
existing bridge module itself imports M2 persistence types, but the evaluator
never constructs or invokes a repository. No SQLite, filesystem, environment,
network, clock generation, Strands, Bedrock, or LLM calls occur in evaluation.

Tests prove real HTTP activation/unavailability with a null reference, read-only
bridge construction, successful independent M3 binding, later ACK isolation,
unchanged database bytes and logical rows/roots/revisions/receipts/outbox, stable
recomputation, and fresh-process evaluation using test-owned authority.

## Non-goals and next boundary

No replacement selection, full feasibility, scheduling, replan generation or
application, skills/vehicles/routes/materials/cost/customer-promise evaluation,
owner escalation, directives, API endpoint, migration, persistence, dispatch,
Gen1 wrapping, or orchestration is introduced.

The next separate atom may implement canonical M3 snapshot persistence and its
trusted producer. It must establish scope authority and completeness from real
M3 state before this evaluator is connected to a production runtime.
