# 0013 — OWNER Company Policy Profile v1 Freeze

- **Status:** FROZEN
- **Scope:** Gen2 operational-authority policy values for the future M3-E0 and M3-E atoms
- **Profile schema:** `company-policy-profile-v1`

## Context

Constitution v1.1 requires numerical and other P-* policy values to remain
proposals until the OWNER records them in a Company Policy Profile. The OWNER
has explicitly approved Company Policy Profile v1, including P-WINDOW-01 in the
exact form below.

This decision is the governance source for those approved values. It is not
runtime authentication or issuance evidence. A future M3-E0 atom must still
create immutable `PolicyIssuanceEvidence` bound to the exact AUTH-0 OWNER
`TrustedPrincipal`, `CompanyAuthorityRoot`, company and `CompanyPlan`. A document,
matching payload, fingerprint or caller assertion alone does not prove runtime
authority. No unsigned fallback profile exists.

## Decision

### P-ASSIGN v1 — same-operational-day worker reassignment

Value: `CONSTRAINED_AUTO`.

Same-operational-day reassignment may be autonomously allowed only if every
other applicable policy, evidence, feasibility, consent, cost, customer-window,
overtime, vehicle, tag, horizon and search gate also passes.

P-ASSIGN is not an independent license to ACT. A failure or `UNKNOWN` in another
required gate prevents autonomous authority.

### P-WINDOW-01 v1 — confirmed customer window

#### CONFIRMED

A customer window is `CONFIRMED` when it is captured in the authoritative C0
constraint knowledge and the relevant customer-window knowledge is neither
`ABSENT` nor `UNKNOWN`.

#### MOVEMENT_OUTSIDE_WINDOW

`MOVEMENT_OUTSIDE_WINDOW` means a proposed interval that is not a subset of the
recorded confirmed customer window.

- M3-C already treats this condition as `REJECTED`.
- M3-E must never convert such a `REJECTED` candidate into `FEASIBLE`.
- It must never ACT on that candidate.
- The later operational-authority outcome must be `ASK` / `ABSTAIN`.
- `selected_candidate_id` must be `null`.
- The reason is `OWNER_DECISION_REQUIRED_FOR_CUSTOMER_PROMISE`.
- A new customer promise requires new authoritative evidence followed by fresh
  evaluation from the appropriate upstream boundary.

#### INTRA_WINDOW_SLOT_SHIFT

`INTRA_WINDOW_SLOT_SHIFT` means a technically `FEASIBLE` change whose complete
proposed interval remains within the same recorded confirmed customer window.

For example, for a recorded window of 08:00–12:00, a candidate may shift from
08:00 to 09:30 only while the complete proposed interval remains within that
recorded window.

In v1, `INTRA_WINDOW_SLOT_SHIFT` is not a separate authority gate. Such a
candidate may remain eligible for autonomous authority if every other required
P-* gate passes. The policy does not introduce a new “confirmed exact start
time” fact that does not exist in C0.

`UNKNOWN` or `ABSENT` required window knowledge is not a PASS and must never
produce ACT.

### P-COST v1 — maximum autonomous additional internal labor cost

- `max_additional_internal_labor_cost = 50.00 EUR`.
- The boundary is inclusive: a delta less than or equal to `+50.00 EUR` may pass
  this policy gate.
- The exact M3-D cost consequence must be `COMPLETE`.
- `INCOMPLETE` or `UNKNOWN` cost is not zero and does not pass.
- A delta of `0.00 EUR` is allowed.
- A negative delta is allowed.
- A positive delta above `+50.00 EUR` does not pass autonomous authority.

The value uses the repository's exact `Decimal` and EUR conventions. Float
semantics are forbidden.

### P-OVERTIME v1

Value: `ASK_ALWAYS`.

Any candidate that creates authoritative overtime requires human authority. If
overtime status is required but `UNKNOWN` or incomplete, the dependent action
must `ABSTAIN`. Overtime must not be inferred from missing evidence. A worker's
refusal of overtime must never become a worker-risk or character trait.

### P-VEHICLE v1 — private vehicle

Value: `OWNER_APPROVAL_AND_WORKER_CONSENT`.

Private-vehicle use requires both:

1. exact scoped OWNER approval; and
2. exact affirmative `FREELY_GIVEN` worker consent.

Approval and consent are scoped and non-transferable. They must not be reused
for another worker, another operational day or another job unless the exact
future evidence model explicitly defines an equivalent scope. Silence, absence,
`UNKNOWN` or non-affirmative consent is not consent. OWNER approval cannot
manufacture worker consent.

### P-TAG v1 — OWNER_APPROVAL_REQUIRED

Relevant tag: `OWNER_APPROVAL_REQUIRED`.

Value: `ASK_ALWAYS` / `SOFT_EXCEPTION`.

Touching a commitment or resource requiring OWNER approval requires exact
scoped OWNER approval. Without valid approval there is no autonomous ACT. This
is a `SOFT_EXCEPTION` authority path only.

OWNER approval must never override a `HARD_BLOCK`, safety boundary, legal
boundary, required qualification, required consent, or impossible / `REJECTED`
feasibility.

### P-HORIZON v1

Value: `INCIDENT_DAY_ONLY`.

Autonomous repair authority following `UNAVAILABLE_TODAY` is limited to the
operational day of the incident. M3-C may technically generate and evaluate
`D+1` / `D+2` candidates inside its bounded search envelope. That technical
search ability does not grant autonomous authority for later days. Later-day
candidates require a separate authority path. The M3-C `D..D+2` technical search
envelope remains unchanged.

### P-SEARCH v1 — bounded-search exhaustion

#### Case A — exhausted with no FEASIBLE candidate

When no `FEASIBLE` candidate exists and the search envelope is exhausted:

- preserve `SEARCH_ENVELOPE_EXHAUSTED`;
- internal authority behavior is `ABSTAIN`;
- reason is `ENVELOPE_EXHAUSTED`;
- external/customer-facing orchestration may expose a blocking state requiring
  further evidence, policy, search scope or human intervention; and
- do not claim global impossibility.

#### Case B — exhausted with a FEASIBLE candidate

When at least one `FEASIBLE` candidate exists while
`search_envelope_exhausted = true`, exhaustion or truncation alone must not
block or penalize that candidate. Existing M3-C semantics—where feasible
candidates take precedence over truncation as an authority input—remain intact.

## Global v1 rules

- `UNKNOWN != 0`.
- `UNKNOWN != false`.
- Missing policy is not permission.
- Missing evidence is not consent.
- Silence is not consent.
- Absence of rejection is not approval.
- Technical `FEASIBILITY` is not authority.
- An M3-C `REJECTED` candidate must never be upgraded by M3-E.
- OWNER approval cannot override a `HARD_BLOCK`.
- Policy authority must be exact, scoped, versioned and replayable.
- No unsigned fallback profile exists.
- Without an authoritative signed/issued profile, the state is
  `POLICY_UNSIGNED`.
- `POLICY_UNSIGNED` grants zero autonomous ACT authority.

## Compatibility with frozen contracts

- **Constitution v1.1:** the profile supplies the OWNER-approved P-* values
  required for `CONSTRAINED_AUTO` without changing facts, feasibility, consent,
  law, safety or any hard boundary. Missing issuance remains fail-safe.
- **State Transition Matrix v1.1:** `ABSTAIN` remains distinct from
  `FORBIDDEN`; only `FREELY_GIVEN` satisfies a required consent gate; unknown or
  missing critical evidence does not become a PASS.
- **M2 Behavioral Contract v1.0:** the policy retains explicit
  `UNAVAILABLE_TODAY` semantics, permits replanning only through policy, and
  requires escalation when a customer promise, cost or safety boundary is
  affected.
- **M3-C:** the profile does not alter technical feasibility, candidate order,
  the `D..D+2` search envelope, customer-window subset validation, private-
  vehicle authority flags or bounded-search meanings. `REJECTED` remains
  `REJECTED`.
- **M3-D:** the profile consumes only the exact signed internal labor-cost delta
  and completeness semantics. It does not recalculate cost, reinterpret
  `INCOMPLETE`, rank candidates or mutate M3-D evidence.
- **AUTH-0:** this governance record does not authenticate an OWNER, issue the
  profile or create approval/consent evidence. Future runtime issuance must
  resolve and bind the exact repository-restored authority root and OWNER
  trusted principal.

## Consequences and non-goals

This freeze resolves the Company Policy Profile v1 values for later evidence
and authority atoms. It does not implement M3-E0 or M3-E, authenticate runtime
authority, create policy issuance, OWNER approval or worker consent evidence,
evaluate or filter candidates, rank or select a winner, produce a runtime
ACT/ASK/BLOCK result, apply a plan, mutate a `PlanRevision`, create a
`HUMAN_TASK`, or change M2, M3-C, M3-D or AUTH-0 behavior.
