# M3-D Internal Scheduled-Labor Cost Consequence

## Responsibility

M3-D deterministically calculates the internal scheduled-labor cost consequence
for every technically `FEASIBLE` candidate in one exact M3-C result. It supplies
one monetary dimension for later policy/ranking work; it does not rank, recommend,
authorize, or apply a candidate.

## Authoritative inputs and binding

The calculation API accepts only an `InternalCostSupportCut` ID. The internal
repository reloads and validates, in one historical read snapshot, the bound
`M3EvaluationInput`, C0 `FeasibilitySupportSnapshot`, recomputed M3-C result,
M5-A cut, and only the immutable selected rate sources referenced by that cut.
M3-D independently checks their IDs/fingerprints, CompanyPlan, exact base
PlanRevision number/ID/fingerprint, candidate set/order, M5 rule, subject universe,
rate selections, and worker/source bindings. Callers cannot supply rates,
placements, candidates, totals, currency, rounding, or completeness.

## Exact consequence

For candidate `C`, the comparison scope is exactly
`C.modified_commitment_ids`. Each baseline line uses the matching placement from
the C0 schedule bound to the base PlanRevision. Each candidate line uses the
matching M3-C proposed placement. Baseline and candidate lines must bind the same
commitment, canonical job, and canonical task; worker and interval may differ.

For each placement:

```text
duration_hours = Decimal(exact_duration_seconds) / Decimal("3600")
line_amount = duration_hours * modeled_internal_labor_cost_rate
```

Each line is quantized to `0.01 EUR` using `ROUND_HALF_UP`; baseline and candidate
totals sum those rounded lines. The signed delta is candidate total minus baseline
total. Positive is more expensive internally, negative is cheaper, and zero is
equal under the captured rule. Candidate-versus-zero is forbidden.

## Unknown and replay semantics

`UNKNOWN != 0`. If any required baseline or candidate subject has `NO_SOURCE`,
the consequence is `INCOMPLETE`; verified line evidence remains visible, but all
candidate totals and the delta are absent rather than zero. M3-D never rereads a
current rate head: an old M5-A cut retains its historical selected source or
historical `NO_SOURCE` after later revisions.

The result is immutable, canonical-JSON content-addressed evidence using rule
`m3-internal-scheduled-labor-cost-consequence-v1`. Candidate consequences retain
M3-C order. Because M3-B, C0, M5-A, and the exact M3-C semantics are durable,
M3-D is pure recomputation and adds no migration or persistence table.

## Search qualification and non-goals

A feasible candidate may be costed when M3-C also reports
`search_envelope_exhausted`; the result explicitly remains scoped to the bounded
M3-C result and makes no global-optimum claim. Rejected candidates receive no
normal consequence.

This v1 result is `INTERNAL_ONLY` scheduled-labor consequence. It excludes
customer/public prices and estimates, vehicle/travel costs, materials, premiums,
overhead, risk, margin, VAT, revenue/profit, final ranking, M6 authority,
ACT/ASK/BLOCK, APPLY, publication, and directives. Excluded components are not
encoded as zero.
