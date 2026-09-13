# 0014 — M3-E1 Operational Authority Semantics Freeze

- **Status:** FROZEN
- **Date:** 2026-09-13
- **Scope:** deterministic per-candidate operational-authority assessment after
  M3-C and M3-D, before any later APPLY boundary

## Context

M3-C already owns the exact bounded technical candidate universe, candidate
order, technical verdicts, impact facts, and bounded-search outcome. M3-D owns
the exact internal scheduled-labor cost consequence for each technically
`FEASIBLE` candidate. M3-E0 owns the issued Company Policy Profile, exact scoped
OWNER approvals, current worker-consent lineage, and authenticated human-action
capture provenance.

Those records do not by themselves answer whether each exact M3-C candidate is
operationally authorized. That answer must preserve the frozen separation of
truth, authority, and execution from ADR 0010. It must also preserve the policy
values in ADR 0013 without moving authority into M3-C, treating missing evidence
as permission, or turning a historical decision into a capability to mutate
state.

This ADR freezes the M3-E1 semantics required before implementation. It does
not constitute implementation evidence and does not authorize APPLY.

## 1. Responsibility and boundary

M3-E1 consumes the complete, exact existing M3-C candidate tuple and assesses
operational authority independently for every candidate. Its per-candidate
authority outcomes are:

- `ACT`
- `ASK`
- `BLOCK`
- `ABSTAIN`

When M3-C reports `NO_REPAIR_REQUIRED`, the M3-E1 top-level outcome is
`NO_ACTION_REQUIRED`; there are no candidate assessments.

M3-E1 preserves exact M3-C candidate order and cardinality. It does not filter,
reorder, score, rank, recommend, or select candidates. Its result does not
contain `selected_candidate_id`, including as a nullable field.

`ACT` means only:

> This exact candidate is authorized under this exact canonical evidence cut.

`ACT` is immutable decision-time audit evidence. It is not permission to
mutate state without later APPLY-time revalidation. Possession of an ACT result
ID, fingerprint, object, or serialized representation grants no authority.

M3-E1 does not APPLY, mutate a `PlanRevision`, mutate M2 state, reserve a worker
or vehicle, create a `HUMAN_TASK`, communicate, authenticate requests, or use an
LLM or Strands to decide authority.

## 2. Separate overtime evidence boundary

M3-E1 must not infer overtime from worker availability, scheduled-labor cost,
placement duration alone, or absence of an overtime record. A separate
canonical working-time evidence boundary is mandatory.

### `WorkingTimeRuleEvidence`

Immutable canonical evidence of the supported working-time rule used for an
exact worker and calculation period. It must bind:

- exact AUTH-0 root/fingerprint, company and worker-registry binding;
- authoritative rule-source identity, source revision/fingerprint, capture
  identity/generation, business-effective interval and provenance;
- an IANA timezone and a deterministic calculation-period definition;
- the rule's complete countable-work classification, threshold/overtime
  function, interval-splitting semantics at date/period boundaries, rounding
  semantics if any, and schema/rule/algorithm versions.

ADR 0014 does not choose jurisdiction-specific German, Polish or other rule
values. The selected authoritative rule revision must nevertheless contain all
values and algorithms needed to produce one answer. Two conforming consumers
of the same rule and work evidence must calculate the same period boundaries
and overtime quantity; M3-E1 may not invent a universal day, week, timezone,
threshold or payroll rule.

`WorkingTimeRuleEvidence` is produced only by a deployment-restricted,
AUTH-0/company-bound trusted source recorder or adaptor. The recorder/adaptor,
not the M3-E1 caller, authenticates and canonicalizes the source and records its
revision/content chronology. M3-E1 never accepts a caller-provided overtime
boolean, payroll calculation, rule, worked-time set or completeness assertion
as authority.

### `WorkingTimeEvidenceCut`

An immutable canonical cut binding the exact evaluation input, exact base
PlanRevision, exact C0 support, exact M3-C result and ordered candidate tuple to
the complete working-time comparison universe.

For candidate `C`, relevant workers are the union of the baseline
`ScheduledPlacementEvidence.worker_ids` and proposed
`ProposedWorkerPlacement.worker_id` values for every commitment in
`C.modified_commitment_ids`. Relevant calculation periods are every period,
as derived by the exact selected rule and timezone, intersected by a baseline
or proposed interval for any relevant worker. If the selected rule requires a
look-back, aggregation window, cross-date split or cross-period allocation,
that complete rule-derived range is part of the period subject.

For every relevant worker/period, the cut contains complete authoritative
countable-work evidence required by the selected rule, including:

- already completed or otherwise countable work;
- unchanged scheduled work;
- the exact baseline intervals replaced or removed by the candidate;
- the exact proposed candidate intervals; and
- every cross-date or cross-period portion produced by the rule.

Each work item binds its worker, source kind, source record/revision/fingerprint,
interval, countability classification and provenance. The cut also binds the
exact selected `WorkingTimeRuleEvidence` revision and source-head observations,
or an explicit missing/incomplete state. It proves the complete worker/period
and work-item universes; it is not a caller-selected subset. Historical replay
never performs a mutable latest lookup. The deployment-restricted,
AUTH-0/company-bound trusted recorder/adaptor derives and persists this cut from
its authoritative sources; M3-E1 cannot accept a caller-constructed cut.

### `CandidateOvertimeAssessment`

One canonical assessment per M3-C candidate, retaining the candidate ID and
fingerprint, the exact working-time cut ID and fingerprint, all relevant
worker/period comparisons, and one of:

- `CREATES_OVERTIME`
- `DOES_NOT_CREATE_OVERTIME`
- `UNKNOWN`

For each relevant worker/period, the selected rule is applied twice to the
same complete evidence universe:

- `baseline_overtime` uses completed/countable work, unchanged scheduled work
  and the exact unchanged base-plan intervals; and
- `candidate_overtime` uses the same completed/countable and unchanged work,
  replacing only `C.modified_commitment_ids` with the exact proposed intervals.

`CREATES_OVERTIME` means `candidate_overtime > baseline_overtime` for at least
one relevant worker/period. It does not mean merely that overtime exists
somewhere in the proposed plan. Comparisons are independent per worker and
period: a decrease for one worker or period never offsets newly created
overtime for another worker or period.

`DOES_NOT_CREATE_OVERTIME` is a negative proof and is legal only when every
relevant worker/period has complete authoritative rule and countable-work
coverage and `candidate_overtime <= baseline_overtime` for all comparisons.
Any missing rule, work interval, calculation-period coverage, worker binding,
source revision, baseline interval/set, proposed candidate consequence, or
required provenance makes the candidate assessment `UNKNOWN`.

The exact authority rule is:

| Overtime applicability | M3-E1 gate result |
|---|---|
| `DOES_NOT_CREATE_OVERTIME` | Passes P-OVERTIME. |
| `UNKNOWN` | `ABSTAIN`; unknown never means no overtime. |
| `CREATES_OVERTIME` | `ASK`; P-OVERTIME is `ASK_ALWAYS`. |

Current M3-E0 supplies no overtime-specific human-authority scope or evidence
path. Therefore `CREATES_OVERTIME` is not satisfiable by an M3-E0 OWNER approval
or worker-consent record. M3-E1 must not fabricate such a scope or convert this
ASK into ACT. Until the trusted rule and complete working-time source boundary
defined here exists, overtime applicability is `UNKNOWN` and the candidate
must `ABSTAIN`.

## 3. Separate OWNER_APPROVAL_REQUIRED marker boundary

Authority tags remain authority evidence and must not be moved into M3-C
technical feasibility.

### `AuthorityMarkerEvidence`

Immutable canonical evidence from a deployment-restricted,
AUTH-0/company-bound marker-registry recorder/adaptor. Each record is
revisioned and content-addressed and binds an unambiguous natural subject kind
and identity, its exact E0 `EvidenceSubjectType` and subject ID, job,
operational date, worker when applicable, explicit marker assignments,
authoritative source/revision/fingerprint and capture chronology, company/root,
and supported schema/rule versions. The producer must be capable of asserting
complete coverage for a bounded subject universe; a caller cannot assert
either a marker or registry completeness.

For a resource, the E0 `subject_id` is always the stable ID
`m3e1-authority-marker-resource-subject-<SHA-256(canonical semantic JSON)>`.
That semantic JSON contains exactly schema
`m3e1-authority-marker-resource-subject-v1`, rule
`m3e1-authority-marker-subject-v1`, `resource_kind` (`WORKER` or `VEHICLE`) and
the exact canonical `resource_id`. It contains no candidate. This mandatory
normalization prevents worker/vehicle namespace collision and gives
byte-identical natural scopes across candidates. A commitment instead uses
`EvidenceSubjectType.COMMITMENT` and its exact canonical `commitment_id`.

### `AuthorityMarkerSupportCut`

An immutable canonical cut binding the exact evaluation input, M3-C result,
ordered candidate tuple, complete required marker-subject universe, selected
marker evidence, and explicit absence states. Its subject universe is derived
only from the following production fields and exact C0 bindings. The same
deployment-restricted, AUTH-0/company-bound trusted marker recorder/adaptor
derives and persists the cut; callers cannot submit a cut or its coverage set.

For each candidate `C`, first form a canonical commitment set:

1. include every `C.modified_commitment_ids` member, bound to its matching
   `C.proposed_worker_placements` and matching baseline
   `FeasibilitySupportSnapshot.schedule.placements` record; and
2. include every exact `(commitment_id, job_id, task_id)` in
   `C.impact.candidate_induced_impact`, bound to its matching C0 schedule
   placement when it is not already modified.

From that set derive, with exact deduplication:

- one commitment subject for every affected baseline and proposed
  `(commitment_id, job_id, business_date)` slice;
- one worker-resource subject for every baseline
  `ScheduledPlacementEvidence.worker_ids` member and every proposed
  `ProposedWorkerPlacement.worker_id`, scoped to the corresponding exact
  job/date slice; and
- one vehicle-resource subject for every non-null baseline
  `ScheduledPlacementEvidence.vehicle_id` and proposed
  `ProposedWorkerPlacement.vehicle_id`, scoped to the corresponding exact
  job/date slice.

For an unmodified member of `candidate_induced_impact`, its C0 baseline
placement supplies the commitment date and its baseline worker/vehicle
resources. For a modified commitment, both distinct baseline and proposed
job/date slices and both baseline and proposed resources are included; moving a
commitment or resource across a date cannot erase the source or destination
scope.

The remaining `CandidateImpactEvidence` categories have these exact roles:

- `direct_current_impact` and `dependency_impact` are not included merely by
  membership: they describe incident-caused baseline impact, not necessarily a
  subject touched by this candidate. A member is included when it is also
  modified or candidate-induced under the rules above.
- `restored_impact` adds no separate subjects because it is a derived subset of
  the modified baseline impact already covered.
- `residual_unresolved_impact` adds no separate marker subjects because any
  non-empty value causes the earlier mandatory partial-repair ABSTAIN. If a
  residual member is independently candidate-induced it is still covered by
  the candidate-induced rule, but its marker can never cure partial repair.
- job and task IDs are not separate P-TAG subjects in v1. They are exact
  canonical bindings of each commitment/resource scope. ADR 0013 governs
  commitment and resource markers, and current M3-E0 defines no `JOB` or `TASK`
  `EvidenceSubjectType`; inventing either would change E0.

This is an ADR 0014 policy decision derived from the smallest exact operational
surface exposed by `M3RepairCandidate`, `CandidateImpactEvidence` and the C0
schedule. It does not add marker semantics to M3-C.

For every derived subject, the cut selects exactly one of:

- positive coverage: an explicit authoritative
  `OWNER_APPROVAL_REQUIRED` marker assignment; or
- negative coverage: an explicit authoritative `NO_MARKER` selection whose
  exact registry revision proves complete coverage for that subject.

Missing source, missing subject, a partial registry extract, an unbound
resource, an ambiguous source revision, or absence without complete-coverage
proof is `UNKNOWN`. A conclusion of no marker is authoritative only through the
negative-coverage rule above.

The marker source returns every marker assignment for one exact natural
subject/job/date, including its exact optional worker qualifier. Thus the
candidate universe is the natural subject/job/date set above; a positive
assignment supplies `AuthorityEvidenceScope.worker_id`, while complete negative
coverage proves absence across both unqualified and relevant worker-qualified
assignments. The caller never guesses whether a worker qualifier applies.

### `CandidateApprovalTagAssessment`

One canonical assessment per candidate, retaining exact candidate and marker
cut identities and the complete set of applicable marker subjects. Its
applicability is:

- `APPLICABLE`
- `NOT_APPLICABLE`
- `UNKNOWN`

`NOT_APPLICABLE` requires negative coverage for every subject in the exact
candidate universe. `APPLICABLE` retains the complete exact set of positively
marked subjects and their E0 scopes. Missing or incomplete coverage anywhere is
`UNKNOWN`, even if other subjects have positive or negative coverage.

The authority rule is:

| Marker applicability | M3-E1 gate result |
|---|---|
| `NOT_APPLICABLE` | Passes P-TAG. |
| `UNKNOWN` | `ABSTAIN`. |
| `APPLICABLE` with any exact approval missing | `ASK`. |
| `APPLICABLE` with every exact approval present | Gate may pass as an approved `SOFT_EXCEPTION`. |

## 4. P-COST above 50.00 EUR

M3-E1 consumes the exact M3-D consequence; it never recomputes cost. The
currency is EUR, comparison uses exact `Decimal` semantics, and the `50.00`
boundary is inclusive.

| Exact M3-D state | P-COST assessment | Authority consequence |
|---|---|---|
| `COMPLETE`, delta `<= 50.00 EUR` | `WITHIN_ENVELOPE` | No OWNER approval required by P-COST. |
| `COMPLETE`, delta `> 50.00 EUR` | `SOFT_EXCEPTION` | Every exact derived P-COST OWNER approval is required; missing any one produces `ASK`. |
| `INCOMPLETE` or otherwise unknown delta | Unknown | `ABSTAIN`; OWNER approval cannot cure a missing consequence. |

### `PCostApprovalSubject`

P-COST authority is approval of the exact reviewed consequence, never standing
approval for an arbitrary future cost. For every distinct affected job/date
slice, M3-E1 derives one immutable canonical content-addressed
`PCostApprovalSubject`. Its semantic document binds at minimum:

- `gate = P_COST`;
- exact M3-C `candidate_id` and `candidate_fingerprint`;
- exact M3-D `CandidateInternalLaborCostConsequence.consequence_id` and
  `consequence_fingerprint`;
- exact M3-D `M3InternalLaborCostConsequenceResult.result_id` and
  `result_fingerprint`;
- exact M5 `InternalCostSupportCut.support_id` and `support_fingerprint`, equal
  to the M3-D result's `source_internal_cost_support_id` and
  `source_internal_cost_support_fingerprint`;
- exact canonical `internal_labor_cost_delta` decimal string and `currency`;
- exact Company Policy Profile `profile_id`/`profile_fingerprint` and
  `PolicyIssuanceEvidence.issuance_id`/`issuance_fingerprint`;
- exact AUTH-0 `authority_root_id`/`authority_root_fingerprint`, `company_id`,
  `company_plan_id` and company-plan provenance reference;
- exact `base_plan_revision`, `base_plan_revision_id` and
  `base_plan_revision_fingerprint`;
- the exact represented `(job_id, operational_date)` slice; and
- the complete canonical ordered set of affected `(job_id, operational_date)`
  slices for the candidate.

The complete affected slice set is the sorted unique union, for every
`candidate.modified_commitment_ids` member, of the matching baseline C0
`ScheduledPlacementEvidence.(job_id, business_date)` and proposed
`ProposedWorkerPlacement.(job_id, business_date)` values. A move across dates
therefore records both slices. The represented slice must be a member of that
complete set.

The stable identity is
`m3e1-p-cost-approval-subject-<SHA-256(canonical semantic JSON)>`. The existing
M3-E0 scope uses:

- `scope_kind = OWNER_APPROVAL_REQUIRED`;
- `subject_type = DECISION`;
- `subject_id = PCostApprovalSubject.subject_id`;
- the subject's represented `job_id` and `operational_date`; and
- `worker_id = null`.

Changing any bound candidate, consequence, M3-D result, M5 cut, delta,
currency, profile/issuance, root/company/plan/revision, represented slice or
complete slice universe creates a different subject ID and therefore a
different `AuthorityEvidenceScope`. This is compatible with M3-E0's immutable
`UNIQUE(authority_root_id, scope_id)` approval constraint and permits a fresh
human approval without replacing history. An old P-COST approval does not
authorize a materially different M3-D/M5 consequence. M3-E0 signing, capture
consumption and uniqueness semantics remain unchanged.

A candidate above `50.00 EUR` with all exact P-COST approvals may reach:

- `ACT`
- `PolicyRelation.SOFT_EXCEPTION`
- `OverrideStatus.APPROVED`
- `ExecutionAuthorization.ALLOWED`
- `Disposition.EXECUTE_WITH_RECORDED_RISK`

It reaches that state only when every other applicable technical, evidence,
policy, consent, horizon, currentness, and authority gate also passes. An OWNER
approval never changes the M3-D consequence or makes an incomplete consequence
complete.

## 5. Exact approval requirement set and scopes

For each candidate, M3-E1 derives an immutable canonical SET of exact
gate-specific `AuthorityRequirement` values. Set membership and canonical order
are deterministic. Each requirement binds the policy gate, the candidate whose
assessment derived it, the exact M3-E0 `AuthorityEvidenceScope`, required
principal type, authority-subject classification, and all supporting IDs and
fingerprints. Duplicate requirements within one candidate collapse only when
every semantic field is identical.

Evidence matching is exact `AuthorityEvidenceScope` value equality, equivalently
byte equality of `authority_evidence_scope_semantic_json` plus matching scope ID
and fingerprint, under the exact AUTH-0 root. Wildcards, prefix matching,
approximate dates, inferred workers, similar subjects, overlapping time ranges,
and partial scope comparison are forbidden.

Authority subjects are classified as follows.

### Natural-subject authority

P-VEHICLE and P-TAG authorize a natural human action whose full meaning is the
existing E0 scope; candidate identity is not an additional hidden dimension.
If two candidates derive a byte-identical complete E0 scope for the genuinely
same natural worker/private-vehicle/job/date action or the same natural marked
subject/job/date/worker action, the same verified OWNER approval—and for
P-VEHICLE the same exact current worker-consent lineage head—may satisfy both
candidate-local requirements. This is reuse of one exact human action, not
cross-candidate substitution. Any scope byte difference prevents reuse.

### Candidate/consequence-specific authority

P-COST authorizes a candidate-specific economic consequence. Its
`PCostApprovalSubject.subject_id` content-addresses the candidate, consequence,
support, policy, authority and slice semantics frozen in section 4. Candidate
or consequence identity is therefore part of the natural E0 scope itself.
Evidence for another candidate or materially different consequence cannot
match, even if job and date happen to be equal. P-COST always uses
`EvidenceSubjectType.DECISION`; it can never be satisfied by a P-TAG commitment
or resource scope.

The v1 scope rules are:

### P-COST

For a candidate with delta above `50.00 EUR`, derive the exact
`PCostApprovalSubject` and E0 scope frozen in section 4 for every distinct
affected job/date slice. Every distinct scope must be approved. Multiple
affected commitments in the same exact consequence/job/date slice produce one
requirement; a different subject semantic document produces a different scope.

### P-TAG

Derive one OWNER approval scope per exact positively marked natural
subject/job/date and include the exact worker when the authoritative marker is
worker-specific. It uses `scope_kind = OWNER_APPROVAL_REQUIRED`; the subject
type and namespace-safe subject ID are supplied by the verified marker
evidence, not a candidate ID or candidate-wide wildcard. Byte-identical P-TAG
scopes may be reused across candidates under the natural-subject rule. P-TAG
uses `EvidenceSubjectType.COMMITMENT` or `EvidenceSubjectType.RESOURCE` and can
never satisfy a P-COST `DECISION` scope.

### P-VEHICLE

Use the existing M3-E0 `PRIVATE_VEHICLE_USE` scope: exact worker, exact private
vehicle, exact job, and exact operational date. OWNER approval and current
`FREELY_GIVEN` consent must resolve against that same byte-identical scope.
Candidate ID is not added. Byte-identical P-VEHICLE scopes may be reused across
candidates under the natural-subject rule; a different worker, vehicle, job or
date cannot reuse them.

### P-OVERTIME

Do not fabricate an M3-E0 scope. M3-E0 has no overtime human-authority path.

Each distinct E0 scope requiring approval is one authoritative human action.
One human-action capture identity, `(capture_key_id, capture_reference)`, must
never be fanned out into multiple approval records or distinct scopes. Reusing
the same already-recorded evidence while assessing multiple candidates that
derive the same byte-identical natural scope is not fan-out: the recorder made
one record for one action. Multiple distinct scopes require independently
captured actions.

## 6. Partial repair

A candidate for which
`candidate.impact.residual_unresolved_impact` is non-empty must never receive
ACT in M3-E1 v1, even if the candidate itself is technically `FEASIBLE` and
every other known gate passes. No undefined subset or reclassification of that
production field is permitted.

The frozen result is:

- authority outcome: `ABSTAIN`
- `ExecutionAuthorization.BLOCKED`
- `Disposition.ABSTAIN`
- reason: `PARTIAL_REPAIR_UNRESOLVED_IMPACT`

Partial repair is not `BLOCK`, not `ASK`, and not recorded-risk ACT. OWNER
approval cannot cure it.

## 7. `M3AuthorityEvidenceCut`

M3-E1 requires one immutable, durable, canonical `M3AuthorityEvidenceCut`.
This cut is the only replay input for the authority assessment and captures
together:

- exact `M3EvaluationInput` semantic document, ID, fingerprint, schema and rule
  version, including its complete historical-source, planning-scope and M2/M1
  precondition fields;
- exact C0 `FeasibilitySupportSnapshot` and `SourceSelectionCut` IDs,
  fingerprints, generations, schema/rule versions, complete source selections,
  worker-registry provenance/capture and explicit missing selections;
- exact recomputed M3-C result ID/fingerprint, search outcome, exhaustion flag,
  ordered complete candidate documents and candidate IDs/fingerprints;
- exact M5 `InternalCostSupportCut`, rule, subject, source selection and capture
  chain, and exact recomputed M3-D result and candidate consequences, including
  completeness and all IDs/fingerprints;
- exact `CompanyPlan` ID/provenance and base `PlanRevision` number, ID,
  fingerprint and canonical content;
- exact AUTH-0 root/fingerprint, company, plan provenance, principal and worker
  registry bindings used by the decision;
- exact policy resolution status, profile and issuance IDs/fingerprints, or an
  explicit unsigned/unsupported state;
- exact derived `AuthorityRequirement` set for every candidate;
- exact `PCostApprovalSubject` semantic documents, IDs and fingerprints and
  their complete affected job/date universes;
- exact selected OWNER approval IDs/fingerprints for every requirement, with
  explicit `NO_APPROVAL` states;
- exact current worker-consent lineage head IDs/fingerprints and status for
  every required consent scope, with explicit absence states;
- exact `WorkingTimeRuleEvidence`, `WorkingTimeEvidenceCut`, and candidate
  overtime assessment identities, including explicit absence/unknown states;
- exact `AuthorityMarkerEvidence`, `AuthorityMarkerSupportCut`, and candidate
  approval-tag assessment identities, including completeness and explicit
  absence/unknown states;
- the complete decision-time currentness vector frozen in section 8; and
- all supported schema/rule versions and every required record ID and
  fingerprint.

The cut must prove that its required subject and requirement universes are
complete. It cannot silently omit a candidate, job/date slice, worker, vehicle,
marker subject, working-time subject, approval, consent head, or explicit
absence selection.

A mutable `latest` lookup is never an invisible replay input. Historical cuts
are immutable. Historical replay reads only the exact records selected by the
cut and reproduces the same ordered result without contacting an LLM or changing
state.

A relevant new approval, worker-consent head or revocation, working-time source,
marker source, C0 or M5 selected source, policy/profile/issuance,
AUTH-0/root/registry binding, CompanyPlan/base revision, M1/M2/plan-day
precondition, or exact recomputation identity creates a new cut and result
identity. It never edits an old cut or result.

## 8. Decision-time and APPLY-time freshness

Before producing ACT, M3-E1 validates one exhaustive canonical
`M3DecisionTimeCurrentnessVector` in one coherent read transaction. Trusted
working-time and marker adaptors must persist their evidence before that
transaction. Caller assertions are not vector entries.

The vector has no open-ended "other mutable heads" category. The following is
the complete v1 vector, in canonical kind/subject order:

For M2 root and immutable-route/publication comparisons, the authoritative
exact-match semantics are those of
`M2DurableRepository._recheck_preconditions()` and
`M2DurableRepository._recheck_immutable_preconditions()`. A future E1
implementation must factor or reuse those comparisons where applicable.
`M3EvaluationInput` is not itself an M2 `PreconditionVector`, so the explicit
M3 field mapping below remains required; invoking an unrelated M2 reduction
check is not a substitute.

1. **CompanyPlan and PlanRevision head.** Reload exact
   `CompanyPlan.(company_plan_id, provenance_reference)`. Reconstruct the full
   immutable `m3_plan_revisions` chain using `CurrentPlanRepository._history`
   semantics and require its last member to equal
   `M3EvaluationInput.(base_plan_revision, base_plan_revision_id,
   base_plan_revision_fingerprint)` and canonical PlanRevision content.
2. **Current planning scope.** Re-run the trusted semantics of
   `CurrentPlanRepository.produce_current_snapshot()` and
   `M3EvaluationRepository._build_input()` for the exact historical bridge
   request. Require equality of `current_planning_scope_fingerprint`,
   `plan_day_id`, the complete `plan_day_associations`,
   `current_active_commitments`, `direct_current_impact`,
   `dependency_impact`, unavailable worker and business date. A later
   implementation must factor or call those production semantics; it must not
   create a looser second derivation or accept a caller scope.
3. **M1 handoff preconditions.** For every
   `CurrentCommitmentEvidence`, require the current authoritative publication
   selected by `(job_id, source_revision, source_handoff_id)` to retain exact
   `source_handoff_sha256`, and require its task/version binding to remain
   exact. The immutable historical source request's payload, reduction proof,
   effect and result hashes are also reconstructed and verified through the
   existing M2-to-M3 bridge.
4. **M2 job/task preconditions.** For every
   `CurrentM2TaskPrecondition`, reconstruct the current canonical M2 job root
   and require exact equality of `job_execution_revision`, `job_root_sha256`,
   `task_id`, `task_definition_version`, `task_route_sha256`,
   `source_handoff_id`, `source_revision` and `task_status`. The complete set of
   task preconditions must still cover the exact current PlanRevision and active
   commitment set. A missing, added, superseded or changed required task fails
   currentness.
5. **M2 assignment preconditions.** For every
   `CurrentM2AssignmentPrecondition`, require exact current equality of
   `assignment_id`, `job_id`, `task_id`, `assignment_route_sha256` and the
   canonical `plan_day_ids`; require the assignment to remain bound in the
   exact current job root and in each referenced commitment's
   `source_m2_assignment_ids`. Missing, added, superseded or changed required
   assignment lineage fails currentness.
6. **Plan-day preconditions.** Require the exact
   `PlanDayAssociationEvidence.(plan_day_id, worker_id, business_date)` set to
   equal the current PlanRevision set and each current `m2_plan_day_roots`
   identity binding. For every such root, record its decision-time
   `plan_day_revision`, `content_sha256` and `status` as an exact APPLY
   precondition. `M3EvaluationInput` does not contain an earlier plan-day
   revision/hash/status, so E1 must not invent one; it requires the production
   identity/membership equality above and freezes the exact decision-time root
   observation for later revalidation.
7. **EvaluationInput binding.** Reconstruct and validate the complete stored
   `M3EvaluationInput` semantic document and require its ID/fingerprint and all
   fields above to equal the trusted current derivation. A newly derived
   EvaluationInput identity means the candidate universe is stale and ACT is
   forbidden.
8. **C0 support and selected sources.** Reconstruct the exact
   `FeasibilitySupportSnapshot`, its `SourceSelectionCut`, worker-registry
   provenance/capture, schedule, task constraints, availability, readiness,
   worker/vehicle technical evidence and route evidence. For each exact bounded
   source subject, record and require the current authoritative source-head
   selection to equal the cut's selected source ID/revision/fingerprint or
   explicit no-source state. A later relevant source that changes any required
   selection makes the cut stale. Recompute C0 identity from the exact records.
9. **M3-C recomputation.** Recompute M3-C from only the exact validated
   EvaluationInput and C0 snapshot and require exact result ID/fingerprint,
   outcome, search trace/exhaustion, candidate order and every candidate
   document/ID/fingerprint.
10. **M5/M3-D chain.** Reconstruct the exact M5
    `InternalCostSupportCut`, its rule, complete subjects, selections and source
    captures. For each required cost source subject, record and require the
    current applicable source-head selection to remain the exact selected
    revision or explicit `NO_SOURCE`. Recompute M3-D and require exact
    `M3InternalLaborCostConsequenceResult` ID/fingerprint, consequence order,
    consequence IDs/fingerprints, completeness and deltas.
11. **AUTH-0 binding.** Resolve the exact
    `CompanyAuthorityRoot` and every referenced `TrustedPrincipal` through
    `TrustedAuthorityRepository`; require exact root/fingerprint, company,
    CompanyPlan/provenance and worker-registry revision/fingerprint bindings,
    and current durable worker identity membership required by AUTH-0.
12. **Policy binding.** `resolve_policy_issuance(authority_root_id)` must return
    the exact supported `CompanyPolicyProfile` and `PolicyIssuanceEvidence`
    IDs/fingerprints captured by the cut. `POLICY_UNSIGNED`, another profile,
    another issuance or an unsupported version cannot ACT.
13. **OWNER approval selections.** For the complete freshly rederived
    `AuthorityRequirement` set, call `resolve_owner_approval` with the exact
    root and complete expected scope. Persist the exact approval ID/fingerprint
    or explicit `NO_APPROVAL` result for every requirement. Natural-scope
    evidence may appear in more than one candidate selection only under section
    5's byte-identical-scope rule.
14. **WORKER consent heads.** For every required P-VEHICLE scope, resolve the
    exact worker and scope through `resolve_worker_consent`; persist the complete
    lineage head ID/fingerprint/status and explicit absence. Only an exact
    current `FREELY_GIVEN` head passes.
15. **Working-time and marker heads.** For every exact rule/work/marker subject,
    require the selected `WorkingTimeEvidenceCut` and
    `AuthorityMarkerSupportCut` source revisions and head observations to be
    current and complete under sections 2 and 3. Persist every selected head or
    explicit missing/incomplete state and both cut IDs/fingerprints.
16. **Derived universes and result inputs.** Re-derive and require equality of
    the complete P-TAG subject universe, overtime worker/period universe,
    `PCostApprovalSubject` set, `AuthorityRequirement` sets, approval selections,
    consent subjects and all explicit absence states. Any omitted, additional
    or differently ordered semantic member fails the vector.

Every vector entry persists its precondition kind, canonical subject key,
expected identity/revision/fingerprint/status or explicit absence, observed
identity/revision/fingerprint/status, and exact-match result. An ACT result is
legal only when all applicable entries are exact matches and every required
negative assertion is backed by authoritative complete coverage. In
particular, a stale required task route, assignment route, job root, handoff,
PlanRevision, selected evidence head or authority lineage can never produce
ACT.

This check does not turn the result into a lease or bearer capability. State may
change immediately after the decision.

A later APPLY boundary must atomically revalidate every relevant current
condition in this exact currentness vector in the same transaction as any
mutation, plus APPLY's separately frozen safety and concurrency preconditions.
A mismatch requires refusal or fresh evaluation; APPLY must not silently reuse
the historical ACT.

Historical ACT remains immutable audit evidence. It cannot be applied solely
by possessing its result ID, fingerprint, serialized form, database row, or
object instance.

## 9. Final authority decision table and precedence

Boundary validation occurs before classification. Invalid canonical content,
unsupported schemas/rules, fingerprint or binding mismatches, incomplete cut
universes, cross-root evidence, or corrupted history fail closed and do not
produce ACT.

### Canonical top-level shape

The top-level result has an `evaluation_status`, exact upstream `search_outcome`,
exact `search_envelope_exhausted`, ordered `candidate_assessments`, a
`search_authority_projection`, and canonical `top_level_reason_codes`:

- M3-C `NO_REPAIR_REQUIRED` produces
  `evaluation_status = NO_ACTION_REQUIRED`, an empty candidate-assessment tuple,
  `search_authority_projection = NOT_APPLICABLE`, and no top-level reason.
- Every other valid M3-C result produces
  `evaluation_status = CANDIDATES_ASSESSED`. Every returned candidate has one
  assessment in exact M3-C order; zero returned candidates produces the
  canonical empty tuple without inventing a candidate.
- If `search_envelope_exhausted = true` and no returned candidate is
  `FEASIBLE`, then `search_authority_projection = ABSTAIN` and the exact
  top-level reason tuple is `("ENVELOPE_EXHAUSTED",)`. All returned rejected or
  abstaining candidate assessments remain present.
- If any returned candidate is `FEASIBLE`, including when search is exhausted,
  `search_authority_projection = NOT_APPLICABLE`; exhaustion remains true as
  provenance and does not downgrade any candidate outcome.
- For non-exhausted `INSUFFICIENT_CRITICAL_EVIDENCE` and
  `CREW_REPAIR_UNSUPPORTED`, the search projection is `ABSTAIN` and the
  one-element top-level reason tuple is the exact M3-C outcome value. Returned
  candidate assessments, if any, remain present.
- Otherwise the search projection is `NOT_APPLICABLE` and top-level reasons are
  empty.

There is no aggregate `AuthorityOutcome` field. `ACT`, `ASK`, `BLOCK` and
`ABSTAIN` are per-candidate outcomes only. Mixed candidate outcomes are retained
as the ordered tuple and are never collapsed by severity, majority, first ACT,
or any selection rule.

For valid inputs, the following precedence is frozen:

1. `NO_REPAIR_REQUIRED` produces top-level `NO_ACTION_REQUIRED`.
2. A separately proven policy/legal/safety hard prohibition, or an ordinary
   known non-window technical rejection, prevents ACT before any soft-exception
   approval is considered.
3. The confirmed-customer-window case retains ADR 0013's special ASK/ABSTAIN
   reporting and requires fresh upstream customer-promise evidence.
4. Unknown required technical support or authority evidence produces ABSTAIN.
5. Partial repair produces ABSTAIN.
6. Unsigned or unsupported policy produces ABSTAIN.
7. Incomplete consequence, marker, working-time, consent-currentness, or other
   required evidence produces ABSTAIN before any missing-approval ASK.
8. A satisfiable missing OWNER approval or a separate required human-authority
   path produces ASK only after dependent facts are sufficient.
9. ACT is possible only after every applicable gate passes.

Thus, when several conditions coexist, a hard boundary wins over ASK; an
unknown, non-affirmative consent, partial repair, or incomplete consequence wins
over a soft-exception approval request; and approval never converts an earlier
failure into a pass.

| Case | Frozen M3-E1 result | Execution/disposition and notes |
|---|---|---|
| M3-C `NO_REPAIR_REQUIRED` | `evaluation_status = NO_ACTION_REQUIRED` | Empty candidate assessments, search projection not applicable, and no action. |
| M3-C `INSUFFICIENT_CRITICAL_EVIDENCE` | `evaluation_status = CANDIDATES_ASSESSED`; search projection `ABSTAIN` | Preserve all returned assessments and exact missing-evidence IDs; top-level reason `INSUFFICIENT_CRITICAL_EVIDENCE`; approval cannot cure it. |
| M3-C `CREW_REPAIR_UNSUPPORTED` | `evaluation_status = CANDIDATES_ASSESSED`; search projection `ABSTAIN` | Preserve all returned assessments and unsupported IDs; top-level reason `CREW_REPAIR_UNSUPPORTED`; do not claim global impossibility or invent a candidate. |
| M3-C `ALL_EVALUATED_CANDIDATES_REJECTED` | Assess every returned rejected candidate in exact order | No candidate may ACT; each receives BLOCK, ASK, or ABSTAIN from its exact rejection evidence and this precedence table. |
| Ordinary known non-window M3-C technical rejection, with no separately proven policy/legal/safety hard prohibition | `BLOCK` | `TechnicalFeasibility.INFEASIBLE`, `ExecutionAuthorization.BLOCKED`, `Disposition.ABSTAIN`; `PolicyRelation` and `OverrideStatus` retain their independently derived exact values and cannot alter the BLOCK. Reason codes are the exact `candidate.rejection_trace` reason-code values in stored order. Technical infeasibility is not relabeled `FORBIDDEN`. |
| Separately proven policy, legal, safety or required-permission hard prohibition | `BLOCK` | `PolicyRelation.HARD_BLOCK`, `OverrideStatus.NOT_REQUIRED`, `ExecutionAuthorization.BLOCKED`, `Disposition.FORBIDDEN`, with the exact authoritative hard-boundary reason. This row is unreachable from an ordinary technical rejection alone. |
| Movement outside an authoritative confirmed customer window | `ASK` | Never ACT. `ExecutionAuthorization.BLOCKED`, `Disposition.ABSTAIN`, reason `OWNER_DECISION_REQUIRED_FOR_CUSTOMER_PROMISE`; fresh upstream customer-promise evidence and reevaluation are required. An OWNER approval is not a cure. |
| Required M3-C technical support is `UNKNOWN` or insufficient | `ABSTAIN` | `ExecutionAuthorization.BLOCKED`, `Disposition.ABSTAIN`; approval cannot supply the missing fact. |
| Technically feasible candidate with non-empty `candidate.impact.residual_unresolved_impact` | `ABSTAIN` | `ExecutionAuthorization.BLOCKED`, `Disposition.ABSTAIN`, reason `PARTIAL_REPAIR_UNRESOLVED_IMPACT`. |
| Policy resolution is `POLICY_UNSIGNED` | `ABSTAIN` | Zero autonomous authority; no fallback profile. |
| Policy/profile/issuance schema or rule is unsupported | `ABSTAIN` | Fail closed; no reinterpretation under another version. |
| M3-D `COMPLETE`, delta `<= 50.00 EUR` | P-COST passes | `WITHIN_ENVELOPE`; no P-COST approval required. Other gates still decide the candidate outcome. |
| M3-D `INCOMPLETE` or delta unknown | `ABSTAIN` | Unknown is not zero; approval cannot cure it. |
| M3-D `COMPLETE`, delta `> 50.00 EUR`, any exact P-COST approval missing | `ASK` | `SOFT_EXCEPTION`, pending exact OWNER authority, execution blocked and disposition ABSTAIN. |
| M3-D `COMPLETE`, delta `> 50.00 EUR`, every exact P-COST approval present | P-COST may pass | `SOFT_EXCEPTION` / `APPROVED`; final ACT remains conditional on every other gate. |
| Overtime `DOES_NOT_CREATE_OVERTIME` | P-OVERTIME passes | No overtime authority required. |
| Overtime `UNKNOWN` | `ABSTAIN` | Unknown never means no overtime. |
| Overtime `CREATES_OVERTIME` | `ASK` | Not currently satisfiable by M3-E0; no fabricated scope and no ACT. |
| Approval marker `NOT_APPLICABLE` with complete coverage | P-TAG passes | No P-TAG approval required. |
| Approval marker `UNKNOWN` or incomplete coverage | `ABSTAIN` | Missing registry data is not absence. |
| Approval marker `APPLICABLE`, any exact P-TAG approval missing | `ASK` | `SOFT_EXCEPTION`; execution blocked pending all exact approvals. |
| Approval marker `APPLICABLE`, all exact P-TAG approvals present | P-TAG may pass | Approved soft exception; all other gates remain required. |
| Private vehicle required, exact OWNER approval missing, exact current worker consent otherwise `FREELY_GIVEN` | `ASK` | Execution blocked pending exact OWNER approval. |
| Private vehicle required, worker consent missing, `UNKNOWN`, `REQUESTED`, `DECLINED`, `PRESSURED`, `ABSENT`, `REVOKED`, or superseded | `ABSTAIN` | `ExecutionAuthorization.BLOCKED`, `Disposition.ABSTAIN`; OWNER approval cannot substitute. |
| Private vehicle required, exact OWNER approval and exact current `FREELY_GIVEN` worker consent present | P-VEHICLE passes | Only that worker/vehicle/job/date scope passes; all other gates remain required. |
| Candidate outside the incident operational day | `ASK` | P-HORIZON requires a separate authority path. M3-C's D..D+2 technical envelope grants no authority; no current M3-E0 evidence is fabricated. |
| Search exhausted and no feasible candidate exists | `CANDIDATES_ASSESSED`; search projection `ABSTAIN` | Preserve all ordered candidate assessments, `search_envelope_exhausted = true`, and exact top-level reason `ENVELOPE_EXHAUSTED`; do not claim global impossibility. |
| Search exhausted but one or more feasible candidates exist | `CANDIDATES_ASSESSED`; search projection `NOT_APPLICABLE` | Preserve every candidate assessment in M3-C order and `search_envelope_exhausted = true`. Exhaustion neither blocks nor penalizes any candidate and does not prevent final ACT. |
| Feasible same-day candidate, complete evidence, all gates `WITHIN_ENVELOPE`, no approval/consent outstanding | `ACT` | `PolicyRelation.WITHIN_ENVELOPE`, `OverrideStatus.NOT_REQUIRED`, `ExecutionAuthorization.ALLOWED`, `Disposition.EXECUTE`. |
| Feasible same-day candidate with one or more approved soft exceptions and every other gate passing | `ACT` | `PolicyRelation.SOFT_EXCEPTION`, `OverrideStatus.APPROVED`, `ExecutionAuthorization.ALLOWED`, `Disposition.EXECUTE_WITH_RECORDED_RISK`. |

Every row is evaluated per candidate except the expressly top-level rows. No
row selects a candidate. A final ACT row is unreachable if any higher-precedence
condition applies.

## 10. Fail-closed invariants

The following are mandatory invariants:

1. `UNKNOWN` is never equivalent to `NOT_APPLICABLE`, false, zero, consent,
   approval, or permission.
2. `ABSENT` is not affirmative evidence unless an authoritative complete cut
   explicitly gives absence that exact semantic meaning.
3. Technical feasibility is not operational authority.
4. An M3-C `REJECTED` candidate is never upgraded to feasible or ACT.
5. Wrong company, AUTH-0 root, policy, job, date, subject, worker, vehicle,
   scope, version, ID, or fingerprint never approximately matches. Wrong
   candidate or consequence never matches candidate/consequence-specific
   authority.
6. Candidate identity is not an unstated dimension of a natural P-VEHICLE or
   P-TAG scope. The same verified evidence may satisfy multiple candidate-local
   requirements only when the complete natural E0 scope is byte-identical.
7. Wildcard, prefix, nearest-date, overlapping-range, partial-field, and
   similarity matching are forbidden for authority evidence.
8. OWNER approval never substitutes for WORKER consent.
9. Only the exact current lineage head with status `FREELY_GIVEN` satisfies a
   required worker-consent gate.
10. Historical or superseded `FREELY_GIVEN` consent is not current authority.
11. `REQUESTED`, `DECLINED`, `PRESSURED`, `ABSENT`, `UNKNOWN`, and `REVOKED`
    never satisfy consent.
12. OWNER approval cannot override a hard boundary, rejected technical
    feasibility, unknown required fact, missing or non-affirmative worker
    consent, unsupported/unsigned policy, or partial repair.
13. OWNER approval cannot turn `INCOMPLETE` M3-D consequence into `COMPLETE`.
14. A P-COST approval authorizes only its exact content-addressed candidate,
    M3-D consequence/result, M5 cut, delta/currency, policy/issuance,
    root/company/plan/revision and job/date slice universe. Changed consequence
    semantics require a new subject, E0 scope and human action.
15. Availability and scheduled-labor cost are not overtime evidence.
16. Overtime creation is an increase relative to the exact unchanged base-plan
    baseline for at least one worker/period, never mere overtime presence.
17. Overtime reductions do not offset creation for another worker or period,
    and `DOES_NOT_CREATE_OVERTIME` requires complete authoritative negative
    proof for every relevant worker/period.
18. Missing marker registry data without completeness proof is not
    `NOT_APPLICABLE`.
19. The P-TAG universe is exactly the modified/candidate-induced commitment and
    baseline/proposed resource universe frozen in section 3; incident impact
    alone cannot add or remove a subject.
20. M3-E0 evidence is used only for its exact supported scope kinds; an
    overtime or horizon scope is not fabricated.
21. One human-action capture identity is consumed once and never fanned out
    into distinct evidence records, human actions, scopes, principals, roots or
    action kinds. Referencing its one verified evidence record from several
    candidates with the same byte-identical natural scope is not fan-out.
22. Every distinct required approval scope must be present; a partial set does
    not satisfy the set.
23. Replay uses the exact immutable evidence cut, never mutable latest state or
    later evidence.
24. Decision-time ACT requires every entry in the exhaustive section 8
    currentness vector to pass; stale task, assignment, handoff, plan, source or
    authority lineage is never ignored.
25. ACT authorizes only the exact candidate under the exact evidence cut.
26. Historical ACT is never a bearer capability.
27. A relevant currentness change creates a new cut/result identity and cannot
    edit history.
28. Search-envelope exhaustion never implies global impossibility and never
    removes returned candidate assessments.
29. Mixed per-candidate outcomes have no synthetic aggregate
    `AuthorityOutcome`.
30. Candidate order and identity are inherited exactly from M3-C and never
    changed by authority assessment.
31. Invalid, corrupted, cross-boundary, unsupported-version, or incomplete-cut
    input fails closed and never degrades to ACT.

## 11. Testable implementation acceptance matrix

Any later M3-E1 implementation is acceptable only if automated tests directly
prove every row below.

| ID | Acceptance case | Required assertion |
|---|---|---|
| `E1-BND-01` | Exact M3-C tuple consumption | Output candidate IDs/fingerprints equal the complete input tuple in exact order and cardinality. |
| `E1-BND-02` | No selection semantics | Result schema has no `selected_candidate_id`; no rank, recommendation, score, or winner is produced. |
| `E1-BND-03` | Pure authority boundary | Evaluation performs no PlanRevision/M2 mutation, reservation, APPLY, HUMAN_TASK, message, authentication, LLM, or Strands call. |
| `E1-NONE-01` | M3-C `NO_REPAIR_REQUIRED` | `evaluation_status=NO_ACTION_REQUIRED`, empty candidate assessments, not-applicable search projection, empty reasons, no side effect. |
| `E1-NONE-02` | M3-C `INSUFFICIENT_CRITICAL_EVIDENCE` | `CANDIDATES_ASSESSED`, search projection ABSTAIN, exact outcome reason and missing-evidence IDs; preserve every returned assessment. |
| `E1-NONE-03` | M3-C `CREW_REPAIR_UNSUPPORTED` | `CANDIDATES_ASSESSED`, search projection ABSTAIN, exact outcome reason and unsupported IDs; no invented candidate or global impossibility claim. |
| `E1-TECH-01` | Ordinary known technical rejection without separately proven hard policy/legal/safety prohibition | Candidate is `BLOCK`, execution blocked, `Disposition.ABSTAIN`, exact ordered M3-C rejection reasons; no approval can make it ACT and no `FORBIDDEN` label is invented. |
| `E1-TECH-03` | Separately proven hard policy/legal/safety/permission prohibition | Candidate is `BLOCK`, policy relation hard block, execution blocked and `Disposition.FORBIDDEN` with exact hard reason. |
| `E1-WINDOW-01` | Outside confirmed customer window | `ASK`/blocked/ABSTAIN with `OWNER_DECISION_REQUIRED_FOR_CUSTOMER_PROMISE`; existing OWNER approval cannot cure it. |
| `E1-WINDOW-02` | Complete interval remains inside confirmed window | No separate P-WINDOW authority requirement is invented. |
| `E1-TECH-02` | Required technical support unknown | `ABSTAIN`; approval does not cure it. |
| `E1-PARTIAL-01` | Non-empty `candidate.impact.residual_unresolved_impact` | Exact partial-repair ABSTAIN result and reason; never BLOCK, ASK, or ACT. |
| `E1-POLICY-01` | Issued supported profile | Exact profile/issuance/root/company bindings are retained in the cut. |
| `E1-POLICY-02` | Unsigned policy | `ABSTAIN`; zero ACT authority and no fallback. |
| `E1-POLICY-03` | Unsupported schema/rule or mismatched profile/issuance | Fail closed without reinterpretation. |
| `E1-COST-01` | Complete delta below, equal to, zero, or below zero relative to `50.00 EUR` | P-COST is `WITHIN_ENVELOPE`; boundary equality passes and no cost approval is required. |
| `E1-COST-02` | Complete delta above `50.00 EUR`, approval missing | `ASK`, soft exception pending, execution blocked. |
| `E1-COST-03` | Complete delta above `50.00 EUR`, all exact approvals present | P-COST passes as approved soft exception; ACT only when all other gates pass. |
| `E1-COST-04` | M3-D incomplete/unknown | `ABSTAIN`; amount is absent, never zero; approval cannot cure it. |
| `E1-COST-05` | Candidate affects multiple baseline/proposed job/date slices | Complete sorted union is bound; exactly one content-addressed P-COST subject/scope per distinct represented slice and every scope is required. |
| `E1-COST-06` | Candidate/consequence/M3-D result/M5 cut/delta/policy/root/plan/slice semantic changes | P-COST subject ID and E0 scope change; old approval does not resolve and a fresh approval can be recorded without replacing E0 history. |
| `E1-COST-07` | Attempted standing approval by candidate ID alone | Rejected as an invalid P-COST subject; consequence and source lifetime cannot be omitted. |
| `E1-OT-01` | Proposed plan contains overtime but no worker/period overtime increases from baseline | `DOES_NOT_CREATE_OVERTIME`; P-OVERTIME passes only with complete negative proof. |
| `E1-OT-02` | At least one worker/period overtime amount increases | `CREATES_OVERTIME` even if another worker/period decreases by more; no cross-worker/period netting. |
| `E1-OT-03` | Overtime `UNKNOWN` or any rule/period/work/baseline/candidate/source binding is missing | `ABSTAIN`; availability or cost cannot be substituted. |
| `E1-OT-04` | `CREATES_OVERTIME` under complete trusted evidence | `ASK`, but no M3-E0 scope is resolved or fabricated and ACT is impossible. |
| `E1-OT-05` | Rule timezone/calculation period or interval split crosses date/period boundary | Exact selected rule controls both baseline and candidate calculations; implementation-independent result. |
| `E1-OT-06` | Caller supplies overtime boolean, rule, payroll result or worked-time subset | Rejected as non-authoritative; until trusted complete evidence exists result is UNKNOWN. |
| `E1-OT-07` | Relevant working-time source/rule head changes | New cut/result identity; historical replay remains unchanged. |
| `E1-TAG-01` | Exact universe derivation | Subjects equal the canonical union of modified and candidate-induced commitments plus baseline/proposed worker/vehicle resources; no others. |
| `E1-TAG-02` | Direct/dependency member not modified or candidate-induced | It is not added solely by incident impact membership. |
| `E1-TAG-03` | Restored or residual member | Restored adds no duplicate; residual adds no subject solely by residual membership and non-empty residual still forces partial-repair ABSTAIN. |
| `E1-TAG-04` | Baseline/proposed date or resource differs | Both exact source/destination slices and both resource identities are in the universe. |
| `E1-TAG-05` | Complete authoritative negative coverage for every subject | `NOT_APPLICABLE`; no P-TAG approval required. |
| `E1-TAG-06` | Positive marker assignment | `APPLICABLE`, retaining every exact marked natural subject and E0 scope. |
| `E1-TAG-07` | Marker registry/source missing, caller-provided, ambiguous or incomplete | `UNKNOWN` and `ABSTAIN`, never `NOT_APPLICABLE`. |
| `E1-TAG-08` | Applicable marker approval missing or partial set | Exact requirements are present and result is `ASK`. |
| `E1-TAG-09` | All exact tag approvals present | P-TAG passes as approved soft exception, subject to every other gate. |
| `E1-SCOPE-01` | Two candidates derive byte-identical natural P-TAG scope | The same verified OWNER approval may satisfy both candidate-local requirements. |
| `E1-SCOPE-02` | Two candidates derive byte-identical natural P-VEHICLE scope | The same verified OWNER approval and same exact current `FREELY_GIVEN` consent head may satisfy both. |
| `E1-SCOPE-03` | Natural scope differs by root/job/date/subject/worker/vehicle or any byte | Evidence does not match; no wildcard, approximate or cross-field fallback. |
| `E1-SCOPE-04` | P-COST candidate or consequence differs while job/date is equal | Content-addressed subject and scope differ; old evidence does not match. |
| `E1-SCOPE-05` | Semantically identical duplicate requirement within one candidate | Canonical set contains it once; any semantic difference remains a distinct requirement. |
| `E1-CAPTURE-01` | One capture identity offered to create records for multiple actions/scopes | Fail closed; no fan-out and no second authoritative evidence record. |
| `E1-CAPTURE-02` | One existing natural-scope evidence record selected for several matching candidates | Allowed; no new record or human action is created and the global capture identity remains consumed once. |
| `E1-VEH-01` | Private vehicle, exact OWNER approval and current `FREELY_GIVEN` consent | P-VEHICLE passes only for the exact worker/vehicle/job/date scope. |
| `E1-VEH-02` | Private vehicle OWNER approval missing with otherwise valid consent | `ASK`; execution blocked. |
| `E1-VEH-03` | Consent absent, unknown, requested, declined, pressured, revoked, or non-current | `ABSTAIN`; OWNER approval cannot substitute. |
| `E1-VEH-04` | Later consent revocation/head | New cut/result identity; old ACT remains audit-only and cannot APPLY. |
| `E1-HORIZON-01` | Candidate on incident day | P-HORIZON may pass, subject to other gates. |
| `E1-HORIZON-02` | Candidate on D+1 or D+2 | `ASK`; technical horizon does not grant authority and no M3-E0 scope is invented. |
| `E1-SEARCH-01` | Exhausted with no feasible candidate | `CANDIDATES_ASSESSED`, search projection ABSTAIN, exact reason `ENVELOPE_EXHAUSTED`, exhaustion true, and all ordered returned assessments preserved. |
| `E1-SEARCH-02` | Exhausted with feasible candidates | `CANDIDATES_ASSESSED`, not-applicable search projection, exhaustion true; every assessment remains normal and ACT is not suppressed. |
| `E1-SEARCH-03` | All evaluated candidates rejected | Preserve every rejected candidate in exact order; none may ACT and there is no aggregate candidate authority outcome. |
| `E1-SEARCH-04` | Mixed ACT/ASK/BLOCK/ABSTAIN assessments | Preserve exact ordered tuple; result has no aggregate `AuthorityOutcome`, ranking or selection. |
| `E1-ACT-01` | Feasible same-day candidate, complete evidence, every gate within envelope | Exact ACT / within-envelope / not-required / allowed / execute projection. |
| `E1-ACT-02` | Feasible same-day candidate, all exact soft exceptions approved and all other gates pass | Exact ACT / soft-exception / approved / allowed / execute-with-recorded-risk projection. |
| `E1-PREC-01` | Hard boundary plus otherwise approvable soft exception | BLOCK wins; approval is irrelevant. |
| `E1-PREC-02` | Unknown evidence or non-affirmative consent plus missing OWNER approval | ABSTAIN wins over ASK. |
| `E1-PREC-03` | Partial repair plus all approvals | Partial-repair ABSTAIN wins. |
| `E1-CUT-01` | Fresh canonical cut | Contains every section 7 chain and every one of the 16 exhaustive section 8 currentness categories with expected/observed identities and exact-match results. |
| `E1-CUT-02` | Omitted candidate/subject/requirement/absence/head | Cut is rejected as incomplete. |
| `E1-CUT-03` | Cross-company/root or mismatched upstream chain | Cut and assessment fail closed. |
| `E1-CUT-04` | Direct database refingerprinting or replacement | Canonical/binding/signature reconstruction rejects it; no authoritative ACT. |
| `E1-CUT-05` | Current task status/route, assignment route/plan days, job root or M1 handoff differs from `M3EvaluationInput` | Currentness fails and ACT is unreachable. |
| `E1-CUT-06` | Current PlanRevision/planning scope/evaluation identity differs | Exact M3-B derivation fails equality; candidate universe is stale and ACT is unreachable. |
| `E1-CUT-07` | Relevant C0/M5/working-time/marker head selection changes | Currentness fails; fresh cut/recomputation is required while historical replay remains unchanged. |
| `E1-REPLAY-01` | Restart/offline replay of historical cut | Identical ordered result and identity without mutable latest reads or LLM. |
| `E1-REPLAY-02` | Relevant evidence changes after a cut | Old result is unchanged; a fresh assessment has a new cut/result identity. |
| `E1-FRESH-01` | Stale decision-time root/policy/plan/approval/consent/working-time/marker observation | ACT is not produced. |
| `E1-APPLY-01` | APPLY attempted using historical ACT after relevant state changes | Atomic revalidation refuses mutation. |
| `E1-APPLY-02` | Result ID/fingerprint/serialized ACT presented alone | It grants no capability and cannot authorize mutation. |
| `E1-FAIL-01` | Any `UNKNOWN` substituted as false, zero, absence, approval, consent, or permission | Validation/test fails; ACT is unreachable. |
| `E1-DET-01` | Same exact cut across restart/process/hash seed | Byte-identical canonical result, IDs, candidate order and reason ordering. |

The acceptance suite must also retain the existing M3-C, M3-D, AUTH-0, M3-E0,
ADR 0010 decision-semantics, revocation, global capture-consumption, canonical
refingerprinting, and migration-history guarantees. Passing only happy-path ACT
cases is insufficient.

## 12. Non-goals

M3-E1 does not implement or own:

- ranking;
- recommendation;
- selection;
- `selected_candidate_id`;
- APPLY;
- `PlanRevision` mutation;
- M2 mutation;
- worker or vehicle reservation;
- `HUMAN_TASK` persistence;
- notifications or messages;
- OWNER, worker, or customer contact;
- request, session, or HTTP authentication;
- LLM or Strands decision logic;
- customer-promise evidence creation;
- OWNER approval creation;
- WORKER consent creation;
- private-key access;
- policy issuance; or
- global optimality claims.

## Compatibility and consequences

This decision preserves ADR 0010's separation of truth, authority, and
execution; ADR 0013's issued policy semantics; M3-C's technical candidate
identity, order, rejection, partial-impact, horizon and search-exhaustion
semantics; M3-D's exact complete/unknown cost consequence; AUTH-0's root and
principal authority; and M3-E0's exact signed, scoped, current human-action
evidence.

In particular, natural P-TAG/P-VEHICLE reuse follows M3-E0's exact scope-value
resolution and does not add candidate identity to those human actions. P-COST's
candidate/consequence identity is carried inside its content-addressed
`DECISION` subject, so it works with—rather than weakens—M3-E0's immutable
`UNIQUE(authority_root_id, scope_id)` and global capture-consumption rules. No
M3-E0 model, signature, uniqueness constraint or resolver is changed by this
decision.

The necessary consequences are a later durable canonical cut, separate
working-time and marker evidence boundaries, deterministic per-candidate
authority results, and a separate APPLY-time transaction. This ADR introduces
none of those implementations.
