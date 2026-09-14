from __future__ import annotations

from dataclasses import fields, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tests.unit.planning.test_bounded_feasibility import (
    ANNA,
    CAR,
    D,
    DAY_START,
    PETER,
    STEFAN,
    _commitment,
    _evaluation,
    _support,
)
from tests.unit.pricing.test_internal_labor_cost_consequence_service import (
    build_evidence,
    calculate,
)
from werkcrew_ai.authority.models import (
    CompanyAuthorityRoot,
    PrincipalType,
    ProvisioningSource,
    TrustedPrincipal,
)
from werkcrew_ai.authority.operational_authority import (
    ApprovalMarkerApplicability,
    AuthorityGateCode,
    AuthorityMarkerEvidence,
    AuthorityMarkerKind,
    AuthorityMarkerSubjectCoverage,
    AuthorityMarkerSupportCut,
    AuthorityOutcome,
    CandidateApprovalTagAssessment,
    CandidateHardBoundaryAssessment,
    CandidateMarkerSubjectUniverse,
    CandidateWorkingTimeEvidence,
    CostApplicability,
    CurrentnessEvidenceState,
    CurrentnessKind,
    CurrentnessObservation,
    EvidenceBinding,
    GateDecision,
    HardBoundaryApplicability,
    HardBoundaryKind,
    M3AuthorityEvaluationStatus,
    M3AuthorityEvidenceCut,
    M3DecisionTimeCurrentnessVector,
    MarkerCoverageStatus,
    OperationalAuthorityBindingError,
    OperationalAuthorityValidationError,
    OvertimeApplicability,
    OwnerApprovalSelection,
    PCostApprovalSubjectState,
    PolicySupportStatus,
    PlanDayRootHeadEvidence,
    SearchAuthorityProjection,
    SelectionStatus,
    WorkerConsentHeadSelection,
    WorkerPeriodOvertimeComparison,
    WorkingTimeCommonManifestEntry,
    WorkingTimeEvidenceCut,
    WorkingTimeRuleEvidence,
    WorkingTimeSourceClassState,
    WorkingTimeWorkItem,
    WorkingTimeWorkKind,
    WorkingTimeSourceKind,
    WorkingTimeSourceManifest,
    WorkingTimeSourceSelection,
    assess_candidate_approval_tags,
    assess_candidate_cost,
    assess_candidate_overtime,
    calculate_rule_overtime,
    derive_authority_marker_subjects,
    derive_authority_requirements,
    derive_required_currentness_observations,
    evaluate_operational_authority,
    plan_day_root_head_evidence,
    pcost_approval_subject_semantic_json,
    serialize_operational_authority_result,
)
from werkcrew_ai.authority.policy_evidence import (
    AuthorityEvidenceScope,
    CompanyPolicyProfile,
    EvidenceScopeKind,
    EvidenceSubjectType,
    PolicyIssuanceEvidence,
    RecordedConsentStatus,
)
from werkcrew_ai.domain.decision_semantics import (
    Disposition,
    ExecutionAuthorization,
    OverrideStatus,
    PolicyRelation,
    TechnicalFeasibility,
)
from werkcrew_ai.field.models import PlanDayRoot, PlanDayStatus, WorkerIdentityRegistry
from werkcrew_ai.field.serialization import canonical_json, serialize_worker_registry, sha256_text
from werkcrew_ai.planning.bounded_feasibility import (
    CandidateImpactEvidence,
    CandidateVerdict,
    ReasonCode,
    SearchOutcome,
    SearchTraceEntry,
    generate_bounded_repair_candidates,
)
from werkcrew_ai.planning.current_plan import (
    CompanyPlan,
    PlanDayAssociation,
    PlanRevision,
    PlanningCommitment,
    TaskDependency,
)
from werkcrew_ai.planning.feasibility_support import (
    AvailabilityKnowledge,
    ConstraintKnowledge,
    source_semantic_json as feasibility_source_semantic_json,
)
from werkcrew_ai.pricing.internal_labor_cost_consequence import (
    LaborCostCompleteness,
)


UTC = timezone.utc
COMPANY_ID = "company-werkcrew"
PLAN_PROVENANCE = "deployment-company-plan"
REGISTRY_WORKERS = (ANNA, PETER, STEFAN, "thomas-becker", "andreas-hoffmann")


def _digest(label: str) -> str:
    return sha256_text(label)


def _plan_revision_for(evaluation) -> PlanRevision:
    return PlanRevision(
        company_plan_id=evaluation.company_plan_id,
        revision=evaluation.base_plan_revision,
        previous_revision_id=None,
        provenance_reference="plan-revision-zero",
        plan_days=tuple(
            PlanDayAssociation(
                plan_day_id=item.plan_day_id,
                worker_id=item.worker_id,
                business_date=item.business_date,
            )
            for item in evaluation.plan_day_associations
        ),
        commitments=tuple(
            PlanningCommitment(
                commitment_id=item.commitment_id,
                job_id=item.job_id,
                task_id=item.task_id,
                task_definition_version=item.task_definition_version,
                source_handoff_id=item.source_handoff_id,
                source_revision=item.source_revision,
                business_date=item.business_date,
                planned_worker_ids=item.planned_worker_ids,
                source_m2_assignment_ids=item.source_m2_assignment_ids,
            )
            for item in evaluation.current_active_commitments
        ),
        dependencies=tuple(
            TaskDependency(
                predecessor_task_id=item.predecessor_task_id,
                successor_task_id=item.successor_task_id,
                provenance_reference=item.provenance_reference,
            )
            for item in evaluation.dependency_impact.edges
        ),
    )


def _authority() -> tuple[
    CompanyPlan,
    CompanyAuthorityRoot,
    TrustedPrincipal,
    dict[str, TrustedPrincipal],
    CompanyPolicyProfile,
    PolicyIssuanceEvidence,
]:
    company_plan = CompanyPlan(
        company_plan_id="company-plan",
        provenance_reference=PLAN_PROVENANCE,
    )
    registry = WorkerIdentityRegistry(
        worker_ids=tuple(sorted(REGISTRY_WORKERS)),
        registry_revision=1,
    )
    registry_json = serialize_worker_registry(registry)
    registry_fingerprint = sha256_text(registry_json)
    root = CompanyAuthorityRoot(
        company_id=COMPANY_ID,
        company_plan_id=company_plan.company_plan_id,
        company_plan_provenance_reference=company_plan.provenance_reference,
        owner_principal_subject_id="owner-subject",
        provisioning_reference="deployment-auth0",
        worker_registry_revision=registry.registry_revision,
        worker_registry_fingerprint=registry_fingerprint,
        canonical_worker_registry_json=registry_json,
    )
    assert root.provisioning_source is ProvisioningSource.DEPLOYMENT_BOOTSTRAP
    owner = TrustedPrincipal(
        authority_root_id=root.authority_root_id,
        authority_root_fingerprint=root.authority_root_fingerprint,
        company_id=root.company_id,
        company_plan_id=root.company_plan_id,
        principal_subject_id="owner-subject",
        principal_type=PrincipalType.OWNER,
        worker_id=None,
        worker_registry_revision=root.worker_registry_revision,
        worker_registry_fingerprint=root.worker_registry_fingerprint,
    )
    workers = {
        worker_id: TrustedPrincipal(
            authority_root_id=root.authority_root_id,
            authority_root_fingerprint=root.authority_root_fingerprint,
            company_id=root.company_id,
            company_plan_id=root.company_plan_id,
            principal_subject_id="worker-subject-" + worker_id,
            principal_type=PrincipalType.WORKER,
            worker_id=worker_id,
            worker_registry_revision=root.worker_registry_revision,
            worker_registry_fingerprint=root.worker_registry_fingerprint,
        )
        for worker_id in REGISTRY_WORKERS
    }
    profile = CompanyPolicyProfile(
        company_id=root.company_id,
        company_plan_id=root.company_plan_id,
        company_plan_provenance_reference=root.company_plan_provenance_reference,
        authority_root_id=root.authority_root_id,
        authority_root_fingerprint=root.authority_root_fingerprint,
    )
    issuance = PolicyIssuanceEvidence(
        profile_id=profile.profile_id,
        profile_fingerprint=profile.profile_fingerprint,
        authority_root_id=root.authority_root_id,
        authority_root_fingerprint=root.authority_root_fingerprint,
        owner_principal_id=owner.principal_id,
        owner_principal_fingerprint=owner.principal_fingerprint,
        company_id=root.company_id,
        company_plan_id=root.company_plan_id,
        company_plan_provenance_reference=root.company_plan_provenance_reference,
    )
    return company_plan, root, owner, workers, profile, issuance


def _m3_bundle(
    *,
    evaluation_kwargs: dict | None = None,
    support_kwargs: dict | None = None,
    private_vehicle: bool = False,
    candidate_transform=None,
    force_exhausted: bool = False,
    cost_delta: Decimal | None = None,
    incomplete_cost: bool = False,
):
    company_plan, root, owner, workers, profile, issuance = _authority()
    evaluation = _evaluation(**(evaluation_kwargs or {}))
    revision = _plan_revision_for(evaluation)
    evaluation = replace(
        evaluation,
        base_plan_revision_id=revision.revision_id,
        base_plan_revision_fingerprint=revision.fingerprint,
    )
    support = _support(evaluation, **(support_kwargs or {}))
    vehicle_evidence = support.vehicle_technical_evidence
    if private_vehicle:
        vehicle_evidence = tuple(
            replace(item, kind="PRIVATE") if item.vehicle_id == CAR else item
            for item in vehicle_evidence
        )
    support = replace(
        support,
        worker_registry_revision=root.worker_registry_revision,
        worker_registry_fingerprint=root.worker_registry_fingerprint,
        vehicle_technical_evidence=vehicle_evidence,
    )
    m3 = generate_bounded_repair_candidates(evaluation, support)
    if candidate_transform is not None:
        candidates = tuple(
            candidate_transform(position, candidate)
            for position, candidate in enumerate(m3.candidates)
        )
        m3 = replace(
            m3,
            candidates=candidates,
            feasible_candidate_ids=tuple(
                item.candidate_id for item in candidates if item.verdict is CandidateVerdict.FEASIBLE
            ),
            rejected_candidate_ids=tuple(
                item.candidate_id for item in candidates if item.verdict is CandidateVerdict.REJECTED
            ),
        )
    if force_exhausted:
        trace = m3.search_trace + (
            SearchTraceEntry(
                reason_code=ReasonCode.SEARCH_ENVELOPE_LIMIT,
                detail="authority test bounded exhaustion",
            ),
        )
        outcome = (
            SearchOutcome.FEASIBLE_CANDIDATES_FOUND
            if m3.feasible_candidate_ids
            else SearchOutcome.SEARCH_ENVELOPE_EXHAUSTED
        )
        m3 = replace(
            m3,
            outcome=outcome,
            search_envelope_exhausted=True,
            search_trace=trace,
        )
    cost_bundle = build_evidence(evaluation, support, m3)
    if incomplete_cost and cost_bundle[3].subjects:
        cost_bundle = build_evidence(
            evaluation,
            support,
            m3,
            no_source_subject_ids=(cost_bundle[3].subjects[0].subject_id,),
        )
    m3d = calculate(cost_bundle)
    if cost_delta is not None and m3d.consequences:
        consequence = m3d.consequences[0]
        baseline = consequence.baseline_lines[0]
        candidate_line = replace(
            consequence.candidate_lines[0],
            rate_amount=baseline.rate_amount + cost_delta,
        )
        consequence = replace(consequence, candidate_lines=(candidate_line,))
        m3d = replace(
            m3d,
            consequences=(consequence,) + m3d.consequences[1:],
        )
    return {
        "company_plan": company_plan,
        "revision": revision,
        "root": root,
        "owner": owner,
        "workers": workers,
        "profile": profile,
        "issuance": issuance,
        "evaluation": evaluation,
        "support": support,
        "m3": m3,
        "cost_support": cost_bundle[3],
        "m3d": m3d,
    }


def _local_periods(intervals, timezone_name="Europe/Berlin"):
    zone = ZoneInfo(timezone_name)
    values = set()
    for start, end in intervals:
        current = start.astimezone(zone).date()
        final = (end - timedelta(seconds=1)).astimezone(zone).date()
        while current <= final:
            local_start = datetime.combine(current, datetime.min.time(), tzinfo=zone)
            local_end = datetime.combine(
                current + timedelta(days=1), datetime.min.time(), tzinfo=zone
            )
            values.add((local_start.astimezone(UTC), local_end.astimezone(UTC)))
            current += timedelta(days=1)
    return tuple(sorted(values))


def _working_rule(bundle, worker_id, period_start, period_end):
    return WorkingTimeRuleEvidence(
        authority_root_id=bundle["root"].authority_root_id,
        authority_root_fingerprint=bundle["root"].authority_root_fingerprint,
        company_id=bundle["root"].company_id,
        company_plan_id=bundle["root"].company_plan_id,
        worker_id=worker_id,
        worker_registry_revision=bundle["root"].worker_registry_revision,
        worker_registry_fingerprint=bundle["root"].worker_registry_fingerprint,
        calculation_period_start=period_start,
        calculation_period_end=period_end,
        source_record_id="working-rule-" + worker_id,
        source_revision=1,
        source_fingerprint=_digest("working-rule-" + worker_id),
        source_capture_id="working-rule-capture-" + worker_id,
        source_capture_generation=1,
        source_capture_fingerprint=_digest("working-rule-capture-" + worker_id),
        business_effective_from=DAY_START - timedelta(days=365),
        business_effective_until=None,
        provenance_reference="working-rule-registry",
        timezone_name="Europe/Berlin",
        calculation_period_semantics="LOCAL_CALENDAR_DAY",
        countable_work_semantics="ALL_BOUND_WORK_ITEMS_COUNTABLE",
        threshold_overtime_semantics="MAX_ZERO_COUNTABLE_SECONDS_MINUS_THRESHOLD",
        interval_splitting_semantics="INTERSECT_LOCAL_PERIOD_BOUNDARIES",
        rounding_semantics="EXACT_SECONDS",
        algorithm_version="working-time-algorithm-v1",
        canonical_rule_payload_json=canonical_json(
            {
                "algorithm_version": "working-time-algorithm-v1",
                "calculation_period_kind": "LOCAL_CALENDAR_DAY",
                "countable_work_mode": "ALL_BOUND_WORK_ITEMS_COUNTABLE",
                "interval_aggregation": "UNION",
                "overtime_formula": "MAX_ZERO_COUNTABLE_SECONDS_MINUS_THRESHOLD",
                "overtime_threshold_seconds": 28800,
                "rounding_mode": "EXACT_SECONDS",
            }
        ),
    )


def _working_time_cut(
    bundle,
    *,
    incomplete_positions=(),
    common_intervals_by_worker=None,
    ledger_source_state=None,
    ledger_reason_code="WORKING_TIME_LEDGER_UNAVAILABLE",
):
    incomplete_positions = set(incomplete_positions)
    common_intervals_by_worker = common_intervals_by_worker or {}
    ledger_source_items = []
    for worker_id in sorted(common_intervals_by_worker):
        for period_start, period_end in _local_periods(
            common_intervals_by_worker[worker_id]
        ):
            for interval_start, interval_end in common_intervals_by_worker[worker_id]:
                start = max(interval_start, period_start)
                end = min(interval_end, period_end)
                if end <= start:
                    continue
                ledger_source_items.append(
                    {
                        "commitment_id": None,
                        "countability_classification": "COUNTABLE",
                        "interval_end": end.astimezone(UTC).isoformat().replace(
                            "+00:00", "Z"
                        ),
                        "interval_start": start.astimezone(UTC).isoformat().replace(
                            "+00:00", "Z"
                        ),
                        "job_id": None,
                        "operational_date": None,
                        "provenance_reference": "working-time-ledger",
                        "task_id": None,
                        "work_kind": "UNCHANGED_SCHEDULED",
                        "worker_id": worker_id,
                    }
                )
    if ledger_source_state is None:
        ledger_source_state = (
            WorkingTimeSourceClassState.PRESENT
            if ledger_source_items
            else WorkingTimeSourceClassState.AUTHORITATIVELY_EMPTY
        )
    assert type(ledger_source_state) is WorkingTimeSourceClassState
    if ledger_source_state is WorkingTimeSourceClassState.UNKNOWN:
        assert not common_intervals_by_worker
        incomplete_positions.update(range(len(bundle["m3"].candidates)))
    ledger_source_content_json = canonical_json(
        {
            "complete_common_work_items": sorted(
                ledger_source_items, key=canonical_json
            ),
            "provenance_reference": "working-time-ledger",
            "schema_version": "m3e1-working-time-ledger-source-v1",
            "source_capture_generation": 1,
            "source_capture_id": "working-time-head",
            "source_head_id": "working-time-ledger-head",
            "source_revision": 1,
        }
    )
    ledger_source_fingerprint = sha256_text(ledger_source_content_json)
    rules = {}
    work_items = {}
    candidate_evidence = []
    c0_manifest_entries = []
    ledger_manifest_entries = []
    baseline_by_id = {
        item.commitment_id: item for item in bundle["support"].schedule.placements
    }
    for position, candidate in enumerate(bundle["m3"].candidates):
        proposed_by_id = {
            item.commitment_id: item for item in candidate.proposed_worker_placements
        }
        intervals_by_worker = {}
        for commitment_id in candidate.modified_commitment_ids:
            baseline = baseline_by_id[commitment_id]
            proposed = proposed_by_id[commitment_id]
            for worker_id in baseline.worker_ids:
                intervals_by_worker.setdefault(worker_id, []).append(
                    (baseline.planned_start, baseline.planned_end)
                )
            intervals_by_worker.setdefault(proposed.worker_id, []).append(
                (proposed.proposed_start, proposed.proposed_end)
            )
        comparisons = []
        for worker_id in sorted(intervals_by_worker):
            for period_start, period_end in _local_periods(
                intervals_by_worker[worker_id]
            ):
                rule = _working_rule(
                    bundle, worker_id, period_start, period_end
                )
                rules[rule.rule_id] = rule
                baseline_items = []
                candidate_items = []
                c0_common_items = []
                ledger_common_items = []
                for commitment_id in candidate.modified_commitment_ids:
                    baseline = baseline_by_id[commitment_id]
                    proposed = proposed_by_id[commitment_id]
                    if worker_id in baseline.worker_ids:
                        start = max(baseline.planned_start, period_start)
                        end = min(baseline.planned_end, period_end)
                        if end > start:
                            item = WorkingTimeWorkItem(
                                worker_id=worker_id,
                                work_kind=WorkingTimeWorkKind.BASELINE_CHANGED,
                                commitment_id=baseline.commitment_id,
                                job_id=baseline.job_id,
                                task_id=baseline.task_id,
                                operational_date=baseline.business_date,
                                candidate_id=None,
                                candidate_fingerprint=None,
                                interval_start=start,
                                interval_end=end,
                                countability_classification="COUNTABLE",
                                source_record_id=bundle["support"].schedule.source_record_id,
                                source_revision=bundle["support"].schedule.source_revision,
                                source_fingerprint=bundle["support"].schedule.source_fingerprint,
                                source_capture_id=bundle["support"].source_cut_id,
                                source_capture_generation=bundle["support"].source_cut_generation,
                                source_capture_fingerprint=bundle["support"].source_cut_fingerprint,
                                provenance_reference=bundle["support"].schedule.provenance_reference,
                            )
                            work_items[item.work_item_id] = item
                            baseline_items.append(item)
                    if worker_id == proposed.worker_id:
                        start = max(proposed.proposed_start, period_start)
                        end = min(proposed.proposed_end, period_end)
                        if end > start:
                            item = WorkingTimeWorkItem(
                                worker_id=worker_id,
                                work_kind=WorkingTimeWorkKind.PROPOSED_CANDIDATE,
                                commitment_id=proposed.commitment_id,
                                job_id=proposed.job_id,
                                task_id=proposed.task_id,
                                operational_date=proposed.business_date,
                                candidate_id=candidate.candidate_id,
                                candidate_fingerprint=candidate.candidate_fingerprint,
                                interval_start=start,
                                interval_end=end,
                                countability_classification="COUNTABLE",
                                source_record_id=candidate.candidate_id,
                                source_revision=0,
                                source_fingerprint=candidate.candidate_fingerprint,
                                source_capture_id=bundle["m3"].result_id,
                                source_capture_generation=0,
                                source_capture_fingerprint=bundle["m3"].result_fingerprint,
                                provenance_reference="M3_C_CANONICAL_PROPOSED_PLACEMENT",
                            )
                            work_items[item.work_item_id] = item
                            candidate_items.append(item)
                for unchanged in bundle["support"].schedule.placements:
                    if (
                        unchanged.commitment_id in candidate.modified_commitment_ids
                        or worker_id not in unchanged.worker_ids
                    ):
                        continue
                    start = max(unchanged.planned_start, period_start)
                    end = min(unchanged.planned_end, period_end)
                    if end <= start:
                        continue
                    item = WorkingTimeWorkItem(
                        worker_id=worker_id,
                        work_kind=WorkingTimeWorkKind.UNCHANGED_SCHEDULED,
                        commitment_id=unchanged.commitment_id,
                        job_id=unchanged.job_id,
                        task_id=unchanged.task_id,
                        operational_date=unchanged.business_date,
                        candidate_id=None,
                        candidate_fingerprint=None,
                        interval_start=start,
                        interval_end=end,
                        countability_classification="COUNTABLE",
                        source_record_id=bundle["support"].schedule.source_record_id,
                        source_revision=bundle["support"].schedule.source_revision,
                        source_fingerprint=bundle["support"].schedule.source_fingerprint,
                        source_capture_id=bundle["support"].source_cut_id,
                        source_capture_generation=bundle["support"].source_cut_generation,
                        source_capture_fingerprint=bundle["support"].source_cut_fingerprint,
                        provenance_reference=bundle["support"].schedule.provenance_reference,
                    )
                    work_items[item.work_item_id] = item
                    baseline_items.append(item)
                    candidate_items.append(item)
                    c0_common_items.append(item)
                for interval_start, interval_end in common_intervals_by_worker.get(
                    worker_id, ()
                ):
                    start = max(interval_start, period_start)
                    end = min(interval_end, period_end)
                    if end <= start:
                        continue
                    item = WorkingTimeWorkItem(
                        worker_id=worker_id,
                        work_kind=WorkingTimeWorkKind.UNCHANGED_SCHEDULED,
                        commitment_id=None,
                        job_id=None,
                        task_id=None,
                        operational_date=None,
                        candidate_id=None,
                        candidate_fingerprint=None,
                        interval_start=start,
                        interval_end=end,
                        countability_classification="COUNTABLE",
                        source_record_id="working-time-ledger-head",
                        source_revision=1,
                        source_fingerprint=ledger_source_fingerprint,
                        source_capture_id="working-time-head",
                        source_capture_generation=1,
                        source_capture_fingerprint=_digest("working-time-head"),
                        provenance_reference="working-time-ledger",
                    )
                    work_items[item.work_item_id] = item
                    baseline_items.append(item)
                    candidate_items.append(item)
                    ledger_common_items.append(item)
                c0_manifest_entries.append(
                    WorkingTimeCommonManifestEntry(
                        candidate_position=position,
                        candidate_id=candidate.candidate_id,
                        candidate_fingerprint=candidate.candidate_fingerprint,
                        worker_id=worker_id,
                        calculation_period_start=period_start,
                        calculation_period_end=period_end,
                        timezone_name=rule.timezone_name,
                        common_work_item_ids=tuple(
                            item.work_item_id for item in c0_common_items
                        ),
                        common_work_item_fingerprints=tuple(
                            item.work_item_fingerprint for item in c0_common_items
                        ),
                        coverage_complete=True,
                    )
                )
                if ledger_source_state is not WorkingTimeSourceClassState.UNKNOWN:
                    ledger_manifest_entries.append(
                        WorkingTimeCommonManifestEntry(
                            candidate_position=position,
                            candidate_id=candidate.candidate_id,
                            candidate_fingerprint=candidate.candidate_fingerprint,
                            worker_id=worker_id,
                            calculation_period_start=period_start,
                            calculation_period_end=period_end,
                            timezone_name=rule.timezone_name,
                            common_work_item_ids=tuple(
                                item.work_item_id for item in ledger_common_items
                            ),
                            common_work_item_fingerprints=tuple(
                                item.work_item_fingerprint for item in ledger_common_items
                            ),
                            coverage_complete=True,
                        )
                    )
                complete = position not in incomplete_positions
                baseline_amount = (
                    calculate_rule_overtime(
                        rule,
                        baseline_items,
                        calculation_period_start=period_start,
                        calculation_period_end=period_end,
                    )
                    if complete
                    else None
                )
                candidate_amount = (
                    calculate_rule_overtime(
                        rule,
                        candidate_items,
                        calculation_period_start=period_start,
                        calculation_period_end=period_end,
                    )
                    if complete
                    else None
                )
                comparisons.append(
                    WorkerPeriodOvertimeComparison(
                        worker_id=worker_id,
                        calculation_period_start=period_start,
                        calculation_period_end=period_end,
                        timezone_name=rule.timezone_name,
                        rule_id=rule.rule_id,
                        rule_fingerprint=rule.rule_fingerprint,
                        baseline_work_item_ids=tuple(
                            item.work_item_id for item in baseline_items
                        ),
                        baseline_work_item_fingerprints=tuple(
                            item.work_item_fingerprint for item in baseline_items
                        ),
                        candidate_work_item_ids=tuple(
                            item.work_item_id for item in candidate_items
                        ),
                        candidate_work_item_fingerprints=tuple(
                            item.work_item_fingerprint for item in candidate_items
                        ),
                        coverage_complete=complete,
                        baseline_overtime=baseline_amount,
                        candidate_overtime=candidate_amount,
                        overtime_unit="SECONDS" if complete else None,
                    )
                )
        candidate_evidence.append(
            CandidateWorkingTimeEvidence(
                candidate_position=position,
                candidate_id=candidate.candidate_id,
                candidate_fingerprint=candidate.candidate_fingerprint,
                relevant_period_keys=tuple(item.period_key for item in comparisons),
                comparisons=tuple(reversed(comparisons)),
                universe_complete=position not in incomplete_positions,
            )
        )
    c0_manifest = WorkingTimeSourceManifest(
        authority_root_id=bundle["root"].authority_root_id,
        authority_root_fingerprint=bundle["root"].authority_root_fingerprint,
        company_id=bundle["root"].company_id,
        company_plan_id=bundle["root"].company_plan_id,
        worker_registry_revision=bundle["root"].worker_registry_revision,
        worker_registry_fingerprint=bundle["root"].worker_registry_fingerprint,
        source_kind=WorkingTimeSourceKind.C0_PLAN_SCHEDULE,
        source_head_id=bundle["support"].schedule.source_record_id,
        source_revision=bundle["support"].schedule.source_revision,
        source_fingerprint=bundle["support"].schedule.source_fingerprint,
        source_capture_id=bundle["support"].source_cut_id,
        source_capture_generation=bundle["support"].source_cut_generation,
        source_capture_fingerprint=bundle["support"].source_cut_fingerprint,
        provenance_reference=bundle["support"].schedule.provenance_reference,
        canonical_source_content_json=feasibility_source_semantic_json(
            bundle["support"].schedule
        ),
        entries=tuple(c0_manifest_entries),
        coverage_complete=True,
    )
    manifests = [c0_manifest]
    ledger_manifest = None
    if ledger_source_state is not WorkingTimeSourceClassState.UNKNOWN:
        ledger_manifest = WorkingTimeSourceManifest(
            authority_root_id=bundle["root"].authority_root_id,
            authority_root_fingerprint=bundle["root"].authority_root_fingerprint,
            company_id=bundle["root"].company_id,
            company_plan_id=bundle["root"].company_plan_id,
            worker_registry_revision=bundle["root"].worker_registry_revision,
            worker_registry_fingerprint=bundle["root"].worker_registry_fingerprint,
            source_kind=WorkingTimeSourceKind.WORKING_TIME_LEDGER,
            source_head_id="working-time-ledger-head",
            source_revision=1,
            source_fingerprint=ledger_source_fingerprint,
            source_capture_id="working-time-head",
            source_capture_generation=1,
            source_capture_fingerprint=_digest("working-time-head"),
            provenance_reference="working-time-ledger",
            canonical_source_content_json=ledger_source_content_json,
            entries=tuple(ledger_manifest_entries),
            coverage_complete=True,
        )
        manifests.append(ledger_manifest)
    selections = [
        WorkingTimeSourceSelection(
            authority_root_id=bundle["root"].authority_root_id,
            authority_root_fingerprint=bundle["root"].authority_root_fingerprint,
            company_id=bundle["root"].company_id,
            company_plan_id=bundle["root"].company_plan_id,
            worker_registry_revision=bundle["root"].worker_registry_revision,
            worker_registry_fingerprint=bundle["root"].worker_registry_fingerprint,
            source_kind=WorkingTimeSourceKind.C0_PLAN_SCHEDULE,
            state=WorkingTimeSourceClassState.PRESENT,
            source_head_id=c0_manifest.source_head_id,
            source_revision=c0_manifest.source_revision,
            source_fingerprint=c0_manifest.source_fingerprint,
            source_capture_id=c0_manifest.source_capture_id,
            source_capture_generation=c0_manifest.source_capture_generation,
            source_capture_fingerprint=c0_manifest.source_capture_fingerprint,
            provenance_reference=c0_manifest.provenance_reference,
            manifest_id=c0_manifest.manifest_id,
            manifest_fingerprint=c0_manifest.manifest_fingerprint,
            reason_code=None,
        )
    ]
    if ledger_manifest is None:
        selections.append(
            WorkingTimeSourceSelection(
                authority_root_id=bundle["root"].authority_root_id,
                authority_root_fingerprint=bundle["root"].authority_root_fingerprint,
                company_id=bundle["root"].company_id,
                company_plan_id=bundle["root"].company_plan_id,
                worker_registry_revision=bundle["root"].worker_registry_revision,
                worker_registry_fingerprint=bundle["root"].worker_registry_fingerprint,
                source_kind=WorkingTimeSourceKind.WORKING_TIME_LEDGER,
                state=WorkingTimeSourceClassState.UNKNOWN,
                source_head_id=None,
                source_revision=None,
                source_fingerprint=None,
                source_capture_id=None,
                source_capture_generation=None,
                source_capture_fingerprint=None,
                provenance_reference=None,
                manifest_id=None,
                manifest_fingerprint=None,
                reason_code=ledger_reason_code,
            )
        )
    else:
        selections.append(
            WorkingTimeSourceSelection(
                authority_root_id=bundle["root"].authority_root_id,
                authority_root_fingerprint=bundle["root"].authority_root_fingerprint,
                company_id=bundle["root"].company_id,
                company_plan_id=bundle["root"].company_plan_id,
                worker_registry_revision=bundle["root"].worker_registry_revision,
                worker_registry_fingerprint=bundle["root"].worker_registry_fingerprint,
                source_kind=WorkingTimeSourceKind.WORKING_TIME_LEDGER,
                state=ledger_source_state,
                source_head_id=ledger_manifest.source_head_id,
                source_revision=ledger_manifest.source_revision,
                source_fingerprint=ledger_manifest.source_fingerprint,
                source_capture_id=ledger_manifest.source_capture_id,
                source_capture_generation=ledger_manifest.source_capture_generation,
                source_capture_fingerprint=ledger_manifest.source_capture_fingerprint,
                provenance_reference=ledger_manifest.provenance_reference,
                manifest_id=ledger_manifest.manifest_id,
                manifest_fingerprint=ledger_manifest.manifest_fingerprint,
                reason_code=None,
            )
        )
    return WorkingTimeEvidenceCut(
        authority_root_id=bundle["root"].authority_root_id,
        authority_root_fingerprint=bundle["root"].authority_root_fingerprint,
        company_id=bundle["root"].company_id,
        company_plan_id=bundle["root"].company_plan_id,
        worker_registry_revision=bundle["root"].worker_registry_revision,
        worker_registry_fingerprint=bundle["root"].worker_registry_fingerprint,
        base_plan_revision=bundle["revision"].revision,
        base_plan_revision_id=bundle["revision"].revision_id,
        base_plan_revision_fingerprint=bundle["revision"].fingerprint,
        evaluation_input_id=bundle["evaluation"].evaluation_input_id,
        evaluation_input_fingerprint=bundle["evaluation"].evaluation_input_fingerprint,
        feasibility_support_snapshot_id=bundle["support"].support_snapshot_id,
        feasibility_support_snapshot_fingerprint=bundle["support"].support_fingerprint,
        m3_result_id=bundle["m3"].result_id,
        m3_result_fingerprint=bundle["m3"].result_fingerprint,
        rule_evidence=tuple(rules.values()),
        work_items=tuple(work_items.values()),
        source_selections=tuple(selections),
        common_work_manifests=tuple(manifests),
        candidate_evidence=tuple(candidate_evidence),
        source_head_observations=tuple(
            EvidenceBinding(
                kind="WORKING_TIME_SOURCE_MANIFEST",
                record_id=item.manifest_id,
                fingerprint=item.manifest_fingerprint,
            )
            for item in manifests
        ),
    )


def _marker_cut(bundle, *, marked_subject_ids=(), unknown_subject_ids=()):
    marked_subject_ids = set(marked_subject_ids)
    unknown_subject_ids = set(unknown_subject_ids)
    universes = tuple(
        CandidateMarkerSubjectUniverse(
            candidate_position=position,
            candidate_id=candidate.candidate_id,
            candidate_fingerprint=candidate.candidate_fingerprint,
            subjects=derive_authority_marker_subjects(candidate, bundle["support"]),
        )
        for position, candidate in enumerate(bundle["m3"].candidates)
    )
    subjects = {
        subject.marker_subject_id: subject
        for universe in universes
        for subject in universe.subjects
    }
    evidence = []
    coverages = []
    source_fingerprint = _digest("marker-source-head")
    capture_fingerprint = _digest("marker-capture-head")
    for marker_subject_id in sorted(subjects):
        subject = subjects[marker_subject_id]
        if marker_subject_id in unknown_subject_ids:
            coverages.append(
                AuthorityMarkerSubjectCoverage(
                    subject=subject,
                    evidence=(),
                    coverage_complete=False,
                )
            )
            continue
        item = AuthorityMarkerEvidence(
            authority_root_id=bundle["root"].authority_root_id,
            authority_root_fingerprint=bundle["root"].authority_root_fingerprint,
            company_id=bundle["root"].company_id,
            company_plan_id=bundle["root"].company_plan_id,
            worker_registry_revision=bundle["root"].worker_registry_revision,
            worker_registry_fingerprint=bundle["root"].worker_registry_fingerprint,
            subject=subject,
            worker_id=None,
            marker_assignments=(
                (AuthorityMarkerKind.OWNER_APPROVAL_REQUIRED,)
                if marker_subject_id in marked_subject_ids
                else ()
            ),
            complete_subject_coverage=True,
            source_record_id="marker-source-head",
            source_revision=1,
            source_fingerprint=source_fingerprint,
            source_capture_id="marker-capture-head",
            source_capture_generation=1,
            source_capture_fingerprint=capture_fingerprint,
            provenance_reference="marker-registry",
        )
        evidence.append(item)
        coverages.append(
            AuthorityMarkerSubjectCoverage(
                subject=subject,
                evidence=(item,),
                coverage_complete=True,
            )
        )
    cut = AuthorityMarkerSupportCut(
        authority_root_id=bundle["root"].authority_root_id,
        authority_root_fingerprint=bundle["root"].authority_root_fingerprint,
        company_id=bundle["root"].company_id,
        company_plan_id=bundle["root"].company_plan_id,
        worker_registry_revision=bundle["root"].worker_registry_revision,
        worker_registry_fingerprint=bundle["root"].worker_registry_fingerprint,
        evaluation_input_id=bundle["evaluation"].evaluation_input_id,
        evaluation_input_fingerprint=bundle["evaluation"].evaluation_input_fingerprint,
        feasibility_support_snapshot_id=bundle["support"].support_snapshot_id,
        feasibility_support_snapshot_fingerprint=bundle["support"].support_fingerprint,
        m3_result_id=bundle["m3"].result_id,
        m3_result_fingerprint=bundle["m3"].result_fingerprint,
        candidate_universes=universes,
        coverages=tuple(coverages),
        source_head_observations=(
            ()
            if not subjects
            else (
                EvidenceBinding(
                    kind="AUTHORITY_MARKER_SOURCE_HEAD",
                    record_id="marker-source-head",
                    fingerprint=source_fingerprint,
                ),
            )
        ),
    )
    return cut, tuple(evidence)


def _hard_boundaries(bundle, overrides=None):
    overrides = overrides or {}
    values = []
    for position, candidate in enumerate(bundle["m3"].candidates):
        applicability, kind, reason = overrides.get(
            position,
            (HardBoundaryApplicability.NOT_APPLICABLE, None, None),
        )
        values.append(
            CandidateHardBoundaryAssessment(
                candidate_position=position,
                candidate_id=candidate.candidate_id,
                candidate_fingerprint=candidate.candidate_fingerprint,
                applicability=applicability,
                boundary_kind=kind,
                reason_code=reason,
                evidence_bindings=(
                    (
                        EvidenceBinding(
                            kind="HARD_BOUNDARY",
                            record_id="hard-boundary-evidence-" + str(position),
                            fingerprint=_digest("hard-boundary-evidence-" + str(position)),
                        ),
                    )
                    if applicability is HardBoundaryApplicability.APPLICABLE
                    else ()
                ),
            )
        )
    return tuple(values)


def _owner_selections(requirement_sets, bundle, approved_gates):
    approved_gates = set(approved_gates)
    selections = []
    for requirement_set in requirement_sets:
        for requirement in requirement_set.requirements:
            if requirement.required_principal_type is not PrincipalType.OWNER:
                continue
            selected = requirement.gate in approved_gates
            approval_fingerprint = _digest(
                "approval|"
                + bundle["root"].authority_root_id
                + "|"
                + requirement.scope.scope_id
            )
            selections.append(
                OwnerApprovalSelection(
                    requirement_id=requirement.requirement_id,
                    requirement_fingerprint=requirement.requirement_fingerprint,
                    authority_root_id=bundle["root"].authority_root_id,
                    authority_root_fingerprint=bundle["root"].authority_root_fingerprint,
                    owner_principal_id=bundle["owner"].principal_id if selected else None,
                    owner_principal_fingerprint=bundle["owner"].principal_fingerprint if selected else None,
                    scope=requirement.scope,
                    status=SelectionStatus.SELECTED if selected else SelectionStatus.ABSENT,
                    approval_id="m3e0-owner-approval-" + approval_fingerprint if selected else None,
                    approval_fingerprint=approval_fingerprint if selected else None,
                    recorded_scope_id=requirement.scope.scope_id if selected else None,
                    recorded_scope_fingerprint=requirement.scope.scope_fingerprint if selected else None,
                )
            )
    return tuple(selections)


def _consent_selections(
    requirement_sets,
    bundle,
    consent_status,
    *,
    current=True,
):
    selections = []
    for requirement_set in requirement_sets:
        for requirement in requirement_set.requirements:
            if requirement.required_principal_type is not PrincipalType.WORKER:
                continue
            selected = consent_status is not None
            consent_fingerprint = _digest(
                "consent|"
                + bundle["root"].authority_root_id
                + "|"
                + requirement.required_worker_id
                + "|"
                + requirement.scope.scope_id
            )
            principal = bundle["workers"][requirement.required_worker_id]
            selections.append(
                WorkerConsentHeadSelection(
                    requirement_id=requirement.requirement_id,
                    requirement_fingerprint=requirement.requirement_fingerprint,
                    authority_root_id=bundle["root"].authority_root_id,
                    authority_root_fingerprint=bundle["root"].authority_root_fingerprint,
                    worker_id=requirement.required_worker_id,
                    scope=requirement.scope,
                    status=SelectionStatus.SELECTED if selected else SelectionStatus.ABSENT,
                    worker_principal_id=principal.principal_id if selected else None,
                    worker_principal_fingerprint=principal.principal_fingerprint if selected else None,
                    consent_id="m3e0-worker-consent-" + consent_fingerprint if selected else None,
                    consent_fingerprint=consent_fingerprint if selected else None,
                    recorded_scope_id=requirement.scope.scope_id if selected else None,
                    recorded_scope_fingerprint=requirement.scope.scope_fingerprint if selected else None,
                    consent_status=consent_status,
                    lineage_sequence=1 if selected else None,
                    previous_consent_id=None,
                    previous_consent_fingerprint=None,
                    is_current_lineage_head=current if selected else False,
                )
            )
    return tuple(selections)


def _plan_day_roots(bundle):
    return tuple(
        PlanDayRoot(
            plan_day_id=item.plan_day_id,
            worker_id=item.worker_id,
            business_date=item.business_date,
            start_at=DAY_START + timedelta(days=(item.business_date - D).days),
            status=PlanDayStatus.ISSUED,
            plan_day_revision=0,
        )
        for item in bundle["evaluation"].plan_day_associations
    )


def _plan_day_root_heads(bundle, roots=None):
    roots = _plan_day_roots(bundle) if roots is None else roots
    return tuple(plan_day_root_head_evidence(item) for item in roots)


def _currentness_vector(
    bundle,
    plan_day_heads,
    policy_status,
    profile,
    issuance,
    requirement_sets,
    owner_selections,
    consent_selections,
    working_cut,
    marker_cut,
    hard_boundaries,
    overtime_assessments,
    approval_tag_assessments,
    cost_assessments,
    *,
    stale_kind=None,
):
    observations = derive_required_currentness_observations(
        company_plan=bundle["company_plan"],
        base_plan_revision=bundle["revision"],
        plan_day_root_heads=plan_day_heads,
        evaluation_input=bundle["evaluation"],
        feasibility_support_snapshot=bundle["support"],
        m3_result=bundle["m3"],
        internal_cost_support=bundle["cost_support"],
        m3d_result=bundle["m3d"],
        authority_root=bundle["root"],
        trusted_principals=(bundle["owner"],) + tuple(bundle["workers"].values()),
        policy_status=policy_status,
        policy_profile=profile,
        policy_issuance=issuance,
        hard_boundaries=hard_boundaries,
        working_time_cut=working_cut,
        overtime_assessments=overtime_assessments,
        marker_support_cut=marker_cut,
        approval_tag_assessments=approval_tag_assessments,
        cost_assessments=cost_assessments,
        requirement_sets=requirement_sets,
        owner_approval_selections=owner_selections,
        worker_consent_head_selections=consent_selections,
    )
    if stale_kind is not None:
        changed = False
        stale = []
        for item in observations:
            if item.kind is not stale_kind or changed:
                stale.append(item)
                continue
            changed = True
            if item.observed_state is CurrentnessEvidenceState.ABSENT:
                stale.append(replace(item, observed_state=CurrentnessEvidenceState.UNKNOWN))
            else:
                stale_semantic = canonical_json(
                    {"subject_key": item.subject_key, "state": "STALE"}
                )
                stale.append(
                    replace(
                        item,
                        observed_fingerprint=_digest(
                            "stale-currentness|" + item.kind.value + "|" + item.subject_key
                        ),
                        observed_status="STALE",
                        observed_semantic_json=stale_semantic,
                    )
                )
        observations = tuple(stale)
    return M3DecisionTimeCurrentnessVector(
        company_plan_id=bundle["company_plan"].company_plan_id,
        company_plan_provenance_reference=bundle["company_plan"].provenance_reference,
        base_plan_revision=bundle["revision"].revision,
        base_plan_revision_id=bundle["revision"].revision_id,
        base_plan_revision_fingerprint=bundle["revision"].fingerprint,
        current_planning_scope_fingerprint=bundle["evaluation"].current_planning_scope_fingerprint,
        evaluation_input_id=bundle["evaluation"].evaluation_input_id,
        evaluation_input_fingerprint=bundle["evaluation"].evaluation_input_fingerprint,
        c0_source_cut_id=bundle["support"].source_cut_id,
        c0_source_cut_fingerprint=bundle["support"].source_cut_fingerprint,
        feasibility_support_snapshot_id=bundle["support"].support_snapshot_id,
        feasibility_support_snapshot_fingerprint=bundle["support"].support_fingerprint,
        m3_result_id=bundle["m3"].result_id,
        m3_result_fingerprint=bundle["m3"].result_fingerprint,
        internal_cost_support_id=bundle["cost_support"].support_id,
        internal_cost_support_fingerprint=bundle["cost_support"].support_fingerprint,
        m3d_result_id=bundle["m3d"].result_id,
        m3d_result_fingerprint=bundle["m3d"].result_fingerprint,
        authority_root_id=bundle["root"].authority_root_id,
        authority_root_fingerprint=bundle["root"].authority_root_fingerprint,
        company_id=bundle["root"].company_id,
        worker_registry_revision=bundle["root"].worker_registry_revision,
        worker_registry_fingerprint=bundle["root"].worker_registry_fingerprint,
        trusted_principal_ids=tuple(
            item.principal_id
            for item in (bundle["owner"],) + tuple(bundle["workers"].values())
        ),
        trusted_principal_fingerprints=tuple(
            item.principal_fingerprint
            for item in (bundle["owner"],) + tuple(bundle["workers"].values())
        ),
        policy_status=policy_status,
        policy_profile_id=profile.profile_id if profile is not None else None,
        policy_profile_fingerprint=profile.profile_fingerprint if profile is not None else None,
        policy_issuance_id=issuance.issuance_id if issuance is not None else None,
        policy_issuance_fingerprint=issuance.issuance_fingerprint if issuance is not None else None,
        authority_requirement_set_ids=tuple(
            item.requirement_set_id for item in requirement_sets
        ),
        authority_requirement_set_fingerprints=tuple(
            item.requirement_set_fingerprint for item in requirement_sets
        ),
        owner_approval_selection_ids=tuple(item.selection_id for item in owner_selections),
        owner_approval_selection_fingerprints=tuple(item.selection_fingerprint for item in owner_selections),
        worker_consent_selection_ids=tuple(item.selection_id for item in consent_selections),
        worker_consent_selection_fingerprints=tuple(item.selection_fingerprint for item in consent_selections),
        working_time_cut_id=working_cut.cut_id,
        working_time_cut_fingerprint=working_cut.cut_fingerprint,
        marker_cut_id=marker_cut.cut_id,
        marker_cut_fingerprint=marker_cut.cut_fingerprint,
        observations=observations,
    )


def _build_cut(
    bundle=None,
    *,
    policy_status=PolicySupportStatus.ISSUED_SUPPORTED,
    marked_subject_ids=(),
    unknown_marker_subject_ids=(),
    common_work_intervals_by_worker=None,
    ledger_source_state=None,
    ledger_reason_code="WORKING_TIME_LEDGER_UNAVAILABLE",
    incomplete_overtime_positions=(),
    approved_gates=(),
    consent_status=RecordedConsentStatus.FREELY_GIVEN,
    consent_current=True,
    hard_boundary_overrides=None,
    stale_kind=None,
    plan_day_roots=None,
):
    bundle = bundle or _m3_bundle()
    profile = bundle["profile"] if policy_status is PolicySupportStatus.ISSUED_SUPPORTED else None
    issuance = bundle["issuance"] if policy_status is PolicySupportStatus.ISSUED_SUPPORTED else None
    policy_reason = (
        None
        if policy_status is PolicySupportStatus.ISSUED_SUPPORTED
        else policy_status.value
    )
    working_cut = _working_time_cut(
        bundle,
        incomplete_positions=incomplete_overtime_positions,
        common_intervals_by_worker=common_work_intervals_by_worker,
        ledger_source_state=ledger_source_state,
        ledger_reason_code=ledger_reason_code,
    )
    overtime = tuple(
        assess_candidate_overtime(position, candidate, working_cut)
        for position, candidate in enumerate(bundle["m3"].candidates)
    )
    marker_cut, marker_evidence = _marker_cut(
        bundle,
        marked_subject_ids=marked_subject_ids,
        unknown_subject_ids=unknown_marker_subject_ids,
    )
    tags = tuple(
        assess_candidate_approval_tags(position, candidate, marker_cut)
        for position, candidate in enumerate(bundle["m3"].candidates)
    )
    costs = tuple(
        assess_candidate_cost(
            candidate_position=position,
            candidate=candidate,
            support_snapshot=bundle["support"],
            internal_cost_support=bundle["cost_support"],
            m3d_result=bundle["m3d"],
            policy_profile=profile,
            policy_issuance=issuance,
            authority_root=bundle["root"],
            company_plan=bundle["company_plan"],
            base_plan_revision=bundle["revision"],
        )
        for position, candidate in enumerate(bundle["m3"].candidates)
        if candidate.verdict is CandidateVerdict.FEASIBLE
    )
    cost_by_candidate = {item.candidate_id: item for item in costs}
    tag_by_candidate = {item.candidate_id: item for item in tags}
    requirement_sets = tuple(
        derive_authority_requirements(
            candidate_position=position,
            candidate=candidate,
            support_snapshot=bundle["support"],
            cost_assessment=cost_by_candidate.get(candidate.candidate_id),
            tag_assessment=tag_by_candidate[candidate.candidate_id],
        )
        for position, candidate in enumerate(bundle["m3"].candidates)
    )
    owner_selections = _owner_selections(requirement_sets, bundle, approved_gates)
    consent_selections = _consent_selections(
        requirement_sets,
        bundle,
        consent_status,
        current=consent_current,
    )
    hard_boundaries = _hard_boundaries(bundle, hard_boundary_overrides)
    plan_day_heads = _plan_day_root_heads(bundle, plan_day_roots)
    currentness = _currentness_vector(
        bundle,
        plan_day_heads,
        policy_status,
        profile,
        issuance,
        requirement_sets,
        owner_selections,
        consent_selections,
        working_cut,
        marker_cut,
        hard_boundaries,
        overtime,
        tags,
        costs,
        stale_kind=stale_kind,
    )
    cut = M3AuthorityEvidenceCut(
        company_plan=bundle["company_plan"],
        base_plan_revision=bundle["revision"],
        plan_day_root_heads=plan_day_heads,
        evaluation_input=bundle["evaluation"],
        feasibility_support_snapshot=bundle["support"],
        m3_result=bundle["m3"],
        internal_cost_support=bundle["cost_support"],
        m3d_result=bundle["m3d"],
        authority_root=bundle["root"],
        trusted_principals=(bundle["owner"],) + tuple(bundle["workers"].values()),
        policy_status=policy_status,
        policy_profile=profile,
        policy_issuance=issuance,
        policy_reason_code=policy_reason,
        hard_boundaries=hard_boundaries,
        working_time_rule_evidence=working_cut.rule_evidence,
        working_time_cut=working_cut,
        overtime_assessments=overtime,
        marker_evidence=marker_evidence,
        marker_support_cut=marker_cut,
        approval_tag_assessments=tags,
        cost_assessments=costs,
        requirement_sets=requirement_sets,
        owner_approval_selections=owner_selections,
        worker_consent_head_selections=consent_selections,
        currentness_vector=currentness,
    )
    return cut, bundle


def _assessment(cut, position=0):
    return evaluate_operational_authority(cut).candidate_assessments[position]


def _gate(assessment, code):
    return next(item for item in assessment.gate_assessments if item.gate is code)


def _rebuild_authority_cut_with_working_time(cut, working_cut):
    overtime = tuple(
        assess_candidate_overtime(position, candidate, working_cut)
        for position, candidate in enumerate(cut.m3_result.candidates)
    )
    observations = derive_required_currentness_observations(
        company_plan=cut.company_plan,
        base_plan_revision=cut.base_plan_revision,
        plan_day_root_heads=cut.plan_day_root_heads,
        evaluation_input=cut.evaluation_input,
        feasibility_support_snapshot=cut.feasibility_support_snapshot,
        m3_result=cut.m3_result,
        internal_cost_support=cut.internal_cost_support,
        m3d_result=cut.m3d_result,
        authority_root=cut.authority_root,
        trusted_principals=cut.trusted_principals,
        policy_status=cut.policy_status,
        policy_profile=cut.policy_profile,
        policy_issuance=cut.policy_issuance,
        hard_boundaries=cut.hard_boundaries,
        working_time_cut=working_cut,
        overtime_assessments=overtime,
        marker_support_cut=cut.marker_support_cut,
        approval_tag_assessments=cut.approval_tag_assessments,
        cost_assessments=cut.cost_assessments,
        requirement_sets=cut.requirement_sets,
        owner_approval_selections=cut.owner_approval_selections,
        worker_consent_head_selections=cut.worker_consent_head_selections,
    )
    vector = replace(
        cut.currentness_vector,
        working_time_cut_id=working_cut.cut_id,
        working_time_cut_fingerprint=working_cut.cut_fingerprint,
        observations=observations,
    )
    return replace(
        cut,
        working_time_rule_evidence=working_cut.rule_evidence,
        working_time_cut=working_cut,
        overtime_assessments=overtime,
        currentness_vector=vector,
    )


def _refingerprinted_work_item_cut(working_cut, predicate, **changes):
    old = next(item for item in working_cut.work_items if predicate(item))
    changed = replace(old, **changes)
    items_by_id = {
        (changed.work_item_id if item.work_item_id == old.work_item_id else item.work_item_id): (
            changed if item.work_item_id == old.work_item_id else item
        )
        for item in working_cut.work_items
    }
    rules_by_id = {item.rule_id: item for item in working_cut.rule_evidence}

    def side(ids):
        return tuple(
            changed.work_item_id if item_id == old.work_item_id else item_id
            for item_id in ids
        )

    evidence = []
    for candidate in working_cut.candidate_evidence:
        comparisons = []
        for comparison in candidate.comparisons:
            baseline_ids = side(comparison.baseline_work_item_ids)
            candidate_ids = side(comparison.candidate_work_item_ids)
            baseline_items = tuple(items_by_id[item_id] for item_id in baseline_ids)
            candidate_items = tuple(items_by_id[item_id] for item_id in candidate_ids)
            rule = rules_by_id[comparison.rule_id]
            comparisons.append(
                replace(
                    comparison,
                    baseline_work_item_ids=baseline_ids,
                    baseline_work_item_fingerprints=tuple(
                        item.work_item_fingerprint for item in baseline_items
                    ),
                    candidate_work_item_ids=candidate_ids,
                    candidate_work_item_fingerprints=tuple(
                        item.work_item_fingerprint for item in candidate_items
                    ),
                    baseline_overtime=(
                        calculate_rule_overtime(
                            rule,
                            baseline_items,
                            calculation_period_start=comparison.calculation_period_start,
                            calculation_period_end=comparison.calculation_period_end,
                        )
                        if comparison.coverage_complete
                        else None
                    ),
                    candidate_overtime=(
                        calculate_rule_overtime(
                            rule,
                            candidate_items,
                            calculation_period_start=comparison.calculation_period_start,
                            calculation_period_end=comparison.calculation_period_end,
                        )
                        if comparison.coverage_complete
                        else None
                    ),
                )
            )
        evidence.append(
            replace(
                candidate,
                comparisons=tuple(comparisons),
                relevant_period_keys=tuple(item.period_key for item in comparisons),
            )
        )
    return replace(
        working_cut,
        work_items=tuple(items_by_id.values()),
        candidate_evidence=tuple(evidence),
    )


def _omit_working_time_comparison(working_cut, candidate_position, predicate):
    evidence = list(working_cut.candidate_evidence)
    selected = evidence[candidate_position]
    retained = tuple(item for item in selected.comparisons if not predicate(item))
    assert len(retained) < len(selected.comparisons)
    evidence[candidate_position] = replace(
        selected,
        relevant_period_keys=tuple(item.period_key for item in retained),
        comparisons=retained,
        universe_complete=False,
    )
    referenced_item_ids = {
        item_id
        for candidate in evidence
        for comparison in candidate.comparisons
        for item_id in (
            comparison.baseline_work_item_ids + comparison.candidate_work_item_ids
        )
    }
    referenced_rule_ids = {
        comparison.rule_id
        for candidate in evidence
        for comparison in candidate.comparisons
    }
    comparison_keys = {
        (candidate.candidate_position, comparison.period_key)
        for candidate in evidence
        for comparison in candidate.comparisons
    }
    manifests = tuple(
        replace(
            manifest,
            entries=tuple(
                entry
                for entry in manifest.entries
                if (entry.candidate_position, entry.period_key) in comparison_keys
            ),
        )
        for manifest in working_cut.common_work_manifests
    )
    selections = _rebind_working_time_source_selections(working_cut, manifests)
    return replace(
        working_cut,
        rule_evidence=tuple(
            item for item in working_cut.rule_evidence if item.rule_id in referenced_rule_ids
        ),
        work_items=tuple(
            item for item in working_cut.work_items if item.work_item_id in referenced_item_ids
        ),
        source_selections=selections,
        common_work_manifests=manifests,
        candidate_evidence=tuple(evidence),
        source_head_observations=tuple(
            EvidenceBinding(
                kind="WORKING_TIME_SOURCE_MANIFEST",
                record_id=item.manifest_id,
                fingerprint=item.manifest_fingerprint,
            )
            for item in manifests
        ),
    )


def _omit_common_work_items_for_candidate(
    working_cut,
    candidate_position,
    predicate,
):
    items_by_id = {item.work_item_id: item for item in working_cut.work_items}
    rules_by_id = {item.rule_id: item for item in working_cut.rule_evidence}
    removed_ids = {
        item.work_item_id
        for item in working_cut.work_items
        if item.work_kind
        in (
            WorkingTimeWorkKind.COMPLETED_COUNTABLE,
            WorkingTimeWorkKind.UNCHANGED_SCHEDULED,
        )
        and predicate(item)
    }
    assert removed_ids
    evidence = []
    for candidate in working_cut.candidate_evidence:
        if candidate.candidate_position != candidate_position:
            evidence.append(candidate)
            continue
        comparisons = []
        for comparison in candidate.comparisons:
            baseline_ids = tuple(
                item_id
                for item_id in comparison.baseline_work_item_ids
                if item_id not in removed_ids
            )
            candidate_ids = tuple(
                item_id
                for item_id in comparison.candidate_work_item_ids
                if item_id not in removed_ids
            )
            baseline_items = tuple(items_by_id[item_id] for item_id in baseline_ids)
            candidate_items = tuple(items_by_id[item_id] for item_id in candidate_ids)
            rule = rules_by_id[comparison.rule_id]
            comparisons.append(
                replace(
                    comparison,
                    baseline_work_item_ids=baseline_ids,
                    baseline_work_item_fingerprints=tuple(
                        item.work_item_fingerprint for item in baseline_items
                    ),
                    candidate_work_item_ids=candidate_ids,
                    candidate_work_item_fingerprints=tuple(
                        item.work_item_fingerprint for item in candidate_items
                    ),
                    baseline_overtime=calculate_rule_overtime(
                        rule,
                        baseline_items,
                        calculation_period_start=comparison.calculation_period_start,
                        calculation_period_end=comparison.calculation_period_end,
                    ),
                    candidate_overtime=calculate_rule_overtime(
                        rule,
                        candidate_items,
                        calculation_period_start=comparison.calculation_period_start,
                        calculation_period_end=comparison.calculation_period_end,
                    ),
                )
            )
        evidence.append(replace(candidate, comparisons=tuple(comparisons)))
    manifests = []
    for manifest in working_cut.common_work_manifests:
        entries = []
        for entry in manifest.entries:
            if entry.candidate_position != candidate_position:
                entries.append(entry)
                continue
            pairs = tuple(
                (item_id, fingerprint)
                for item_id, fingerprint in zip(
                    entry.common_work_item_ids,
                    entry.common_work_item_fingerprints,
                    strict=True,
                )
                if item_id not in removed_ids
            )
            entries.append(
                replace(
                    entry,
                    common_work_item_ids=tuple(item[0] for item in pairs),
                    common_work_item_fingerprints=tuple(item[1] for item in pairs),
                )
            )
        manifests.append(replace(manifest, entries=tuple(entries)))
    referenced_ids = {
        item_id
        for candidate in evidence
        for comparison in candidate.comparisons
        for item_id in comparison.baseline_work_item_ids
        + comparison.candidate_work_item_ids
    }
    manifests = tuple(manifests)
    selections = _rebind_working_time_source_selections(working_cut, manifests)
    return replace(
        working_cut,
        work_items=tuple(
            item for item in working_cut.work_items if item.work_item_id in referenced_ids
        ),
        source_selections=selections,
        common_work_manifests=manifests,
        candidate_evidence=tuple(evidence),
        source_head_observations=tuple(
            EvidenceBinding(
                kind="WORKING_TIME_SOURCE_MANIFEST",
                record_id=item.manifest_id,
                fingerprint=item.manifest_fingerprint,
            )
            for item in manifests
        ),
    )


def _rebind_working_time_source_selections(working_cut, manifests):
    manifests_by_kind = {item.source_kind: item for item in manifests}
    return tuple(
        selection
        if selection.state is WorkingTimeSourceClassState.UNKNOWN
        else replace(
            selection,
            source_head_id=manifests_by_kind[selection.source_kind].source_head_id,
            source_revision=manifests_by_kind[selection.source_kind].source_revision,
            source_fingerprint=manifests_by_kind[selection.source_kind].source_fingerprint,
            source_capture_id=manifests_by_kind[selection.source_kind].source_capture_id,
            source_capture_generation=manifests_by_kind[
                selection.source_kind
            ].source_capture_generation,
            source_capture_fingerprint=manifests_by_kind[
                selection.source_kind
            ].source_capture_fingerprint,
            provenance_reference=manifests_by_kind[
                selection.source_kind
            ].provenance_reference,
            manifest_id=manifests_by_kind[selection.source_kind].manifest_id,
            manifest_fingerprint=manifests_by_kind[
                selection.source_kind
            ].manifest_fingerprint,
        )
        for selection in working_cut.source_selections
    )


def _working_time_source_selection(working_cut, source_kind):
    return next(
        item
        for item in working_cut.source_selections
        if item.source_kind is source_kind
    )


def _working_time_source_manifest(working_cut, source_kind):
    return next(
        item
        for item in working_cut.common_work_manifests
        if item.source_kind is source_kind
    )


def _ledger_eight_hours_then_candidate_cut():
    bundle = _m3_bundle()
    baseline_by_id = {
        item.commitment_id: item for item in bundle["support"].schedule.placements
    }
    position, candidate = next(
        (position, candidate)
        for position, candidate in enumerate(bundle["m3"].candidates)
        if any(item.worker_id == STEFAN for item in candidate.proposed_worker_placements)
        and any(
            PETER in baseline_by_id[commitment_id].worker_ids
            for commitment_id in candidate.modified_commitment_ids
        )
    )
    proposal = next(
        item for item in candidate.proposed_worker_placements if item.worker_id == STEFAN
    )
    period_start, period_end = _local_periods(
        ((proposal.proposed_start, proposal.proposed_end),)
    )[0]
    if proposal.proposed_start - period_start >= timedelta(hours=8):
        ledger_interval = (
            proposal.proposed_start - timedelta(hours=8),
            proposal.proposed_start,
        )
    else:
        assert period_end - proposal.proposed_end >= timedelta(hours=8)
        ledger_interval = (
            proposal.proposed_end,
            proposal.proposed_end + timedelta(hours=8),
        )
    cut, _ = _build_cut(
        bundle,
        common_work_intervals_by_worker={STEFAN: (ledger_interval,)},
    )
    return cut, position


def _without_ledger_source_class(working_cut):
    ledger = _working_time_source_manifest(
        working_cut,
        WorkingTimeSourceKind.WORKING_TIME_LEDGER,
    )
    ledger_item_ids = {
        item_id for entry in ledger.entries for item_id in entry.common_work_item_ids
    }
    items_by_id = {item.work_item_id: item for item in working_cut.work_items}
    rules_by_id = {item.rule_id: item for item in working_cut.rule_evidence}
    evidence = []
    for candidate in working_cut.candidate_evidence:
        comparisons = []
        for comparison in candidate.comparisons:
            baseline_ids = tuple(
                item_id
                for item_id in comparison.baseline_work_item_ids
                if item_id not in ledger_item_ids
            )
            candidate_ids = tuple(
                item_id
                for item_id in comparison.candidate_work_item_ids
                if item_id not in ledger_item_ids
            )
            baseline_items = tuple(items_by_id[item_id] for item_id in baseline_ids)
            candidate_items = tuple(items_by_id[item_id] for item_id in candidate_ids)
            rule = rules_by_id[comparison.rule_id]
            comparisons.append(
                replace(
                    comparison,
                    baseline_work_item_ids=baseline_ids,
                    baseline_work_item_fingerprints=tuple(
                        item.work_item_fingerprint for item in baseline_items
                    ),
                    candidate_work_item_ids=candidate_ids,
                    candidate_work_item_fingerprints=tuple(
                        item.work_item_fingerprint for item in candidate_items
                    ),
                    baseline_overtime=calculate_rule_overtime(
                        rule,
                        baseline_items,
                        calculation_period_start=comparison.calculation_period_start,
                        calculation_period_end=comparison.calculation_period_end,
                    ),
                    candidate_overtime=calculate_rule_overtime(
                        rule,
                        candidate_items,
                        calculation_period_start=comparison.calculation_period_start,
                        calculation_period_end=comparison.calculation_period_end,
                    ),
                )
            )
        evidence.append(replace(candidate, comparisons=tuple(comparisons)))
    return {
        "work_items": tuple(
            item
            for item in working_cut.work_items
            if item.work_item_id not in ledger_item_ids
        ),
        "source_selections": tuple(
            item
            for item in working_cut.source_selections
            if item.source_kind is not WorkingTimeSourceKind.WORKING_TIME_LEDGER
        ),
        "common_work_manifests": tuple(
            item
            for item in working_cut.common_work_manifests
            if item.source_kind is not WorkingTimeSourceKind.WORKING_TIME_LEDGER
        ),
        "candidate_evidence": tuple(evidence),
        "source_head_observations": tuple(
            item
            for item in working_cut.source_head_observations
            if item.record_id != ledger.manifest_id
        ),
    }


def _revised_empty_ledger_working_cut(working_cut):
    ledger = _working_time_source_manifest(
        working_cut,
        WorkingTimeSourceKind.WORKING_TIME_LEDGER,
    )
    selection = _working_time_source_selection(
        working_cut,
        WorkingTimeSourceKind.WORKING_TIME_LEDGER,
    )
    assert selection.state is WorkingTimeSourceClassState.AUTHORITATIVELY_EMPTY
    assert not tuple(
        item_id for entry in ledger.entries for item_id in entry.common_work_item_ids
    )
    source_content = canonical_json(
        {
            "complete_common_work_items": [],
            "provenance_reference": ledger.provenance_reference,
            "schema_version": "m3e1-working-time-ledger-source-v1",
            "source_capture_generation": 2,
            "source_capture_id": ledger.source_capture_id,
            "source_head_id": ledger.source_head_id,
            "source_revision": 2,
        }
    )
    changed_manifest = replace(
        ledger,
        source_revision=2,
        source_fingerprint=sha256_text(source_content),
        source_capture_generation=2,
        source_capture_fingerprint=_digest("working-time-head-generation-2"),
        canonical_source_content_json=source_content,
    )
    manifests = tuple(
        changed_manifest if item.source_kind is ledger.source_kind else item
        for item in working_cut.common_work_manifests
    )
    selections = _rebind_working_time_source_selections(working_cut, manifests)
    return replace(
        working_cut,
        source_selections=selections,
        common_work_manifests=manifests,
        source_head_observations=tuple(
            EvidenceBinding(
                kind="WORKING_TIME_SOURCE_MANIFEST",
                record_id=item.manifest_id,
                fingerprint=item.manifest_fingerprint,
            )
            for item in manifests
        ),
    )


def test_canonical_identity_is_deterministic_and_result_bytes_repeat():
    first_cut, _ = _build_cut()
    second_cut, _ = _build_cut()
    first = evaluate_operational_authority(first_cut)
    second = evaluate_operational_authority(second_cut)

    assert first_cut.cut_id == second_cut.cut_id
    assert first.result_id == second.result_id
    assert serialize_operational_authority_result(first) == serialize_operational_authority_result(second)


def test_reflective_identity_substitution_is_rejected_before_evaluation():
    cut, _ = _build_cut()
    object.__setattr__(cut.m3_result.candidates[0], "candidate_id", "m3-candidate-" + _digest("forged"))

    with pytest.raises(OperationalAuthorityBindingError, match="INVALID_AUTHORITY_CUT"):
        evaluate_operational_authority(cut)


def test_every_candidate_is_retained_in_exact_m3_order():
    bundle = _m3_bundle(
        support_kwargs={"workers": (ANNA, STEFAN), "sku_by_letter": {"A": "waterproof.bath"}}
    )
    cut, _ = _build_cut(bundle)
    result = evaluate_operational_authority(cut)

    assert tuple(item.candidate_id for item in result.candidate_assessments) == tuple(
        item.candidate_id for item in bundle["m3"].candidates
    )


def test_result_and_assessments_have_no_ranking_recommendation_or_selection_fields():
    cut, _ = _build_cut()
    result = evaluate_operational_authority(cut)
    forbidden = {"selected_candidate_id", "rank", "score", "recommendation", "winner"}

    assert forbidden.isdisjoint(item.name for item in fields(result))
    assert all(forbidden.isdisjoint(item.name for item in fields(value)) for value in result.candidate_assessments)
    assert not hasattr(result, "authority_outcome")


def test_no_repair_required_has_exact_no_action_shape():
    bundle = _m3_bundle(evaluation_kwargs={"direct_letters": ()})
    cut, _ = _build_cut(bundle)
    result = evaluate_operational_authority(cut)

    assert result.evaluation_status is M3AuthorityEvaluationStatus.NO_ACTION_REQUIRED
    assert result.candidate_assessments == ()
    assert result.search_authority_projection is SearchAuthorityProjection.NOT_APPLICABLE
    assert result.top_level_reason_codes == ()


def test_ordinary_known_technical_rejection_blocks_without_forbidden():
    bundle = _m3_bundle(support_kwargs={"worker_windows": {STEFAN: ()}})
    cut, _ = _build_cut(bundle)
    assessment = _assessment(cut)

    assert assessment.authority_outcome is AuthorityOutcome.BLOCK
    assert assessment.technical_feasibility is TechnicalFeasibility.INFEASIBLE
    assert assessment.execution_authorization is ExecutionAuthorization.BLOCKED
    assert assessment.disposition is Disposition.ABSTAIN
    assert assessment.policy_relation is not PolicyRelation.HARD_BLOCK


def test_confirmed_customer_window_violation_is_special_ask():
    bundle = _m3_bundle(
        support_kwargs={
            "window": {
                "A": (
                    ConstraintKnowledge.KNOWN,
                    DAY_START + timedelta(hours=5),
                    DAY_START + timedelta(hours=7),
                )
            }
        }
    )
    cut, _ = _build_cut(bundle)
    assessment = _assessment(cut)

    assert assessment.authority_outcome is AuthorityOutcome.ASK
    assert assessment.reason_codes == ("OWNER_DECISION_REQUIRED_FOR_CUSTOMER_PROMISE",)
    assert assessment.disposition is Disposition.ABSTAIN


def test_unknown_technical_support_abstains():
    bundle = _m3_bundle(
        support_kwargs={"worker_knowledge": {STEFAN: AvailabilityKnowledge.UNKNOWN}}
    )
    cut, _ = _build_cut(bundle)
    assessment = _assessment(cut)

    assert assessment.authority_outcome is AuthorityOutcome.ABSTAIN
    assert assessment.technical_feasibility is TechnicalFeasibility.UNKNOWN


def test_residual_unresolved_impact_is_partial_repair_abstain():
    def add_residual(_position, candidate):
        return replace(
            candidate,
            impact=replace(
                candidate.impact,
                residual_unresolved_impact=candidate.impact.direct_current_impact,
            ),
        )

    bundle = _m3_bundle(candidate_transform=add_residual)
    cut, _ = _build_cut(bundle)
    assessment = _assessment(cut)

    assert assessment.authority_outcome is AuthorityOutcome.ABSTAIN
    assert assessment.execution_authorization is ExecutionAuthorization.BLOCKED
    assert assessment.disposition is Disposition.ABSTAIN
    assert assessment.reason_codes == ("PARTIAL_REPAIR_UNRESOLVED_IMPACT",)
    assert _gate(assessment, AuthorityGateCode.PARTIAL_REPAIR).decision is GateDecision.INDETERMINATE


@pytest.mark.parametrize(
    "status",
    [PolicySupportStatus.POLICY_UNSIGNED, PolicySupportStatus.UNSUPPORTED],
)
def test_unsigned_or_unsupported_policy_abstains(status):
    cut, _ = _build_cut(policy_status=status)
    assessment = _assessment(cut)

    assert assessment.authority_outcome is AuthorityOutcome.ABSTAIN
    assert assessment.reason_codes == (status.value,)


def test_unsupported_policy_above_cost_threshold_abstains_without_fabricated_scope():
    bundle = _m3_bundle(cost_delta=Decimal("50.01"))
    cut, _ = _build_cut(bundle, policy_status=PolicySupportStatus.POLICY_UNSIGNED)
    assessment = _assessment(cut)

    assert cut.cost_assessments[0].applicability is CostApplicability.SOFT_EXCEPTION
    assert (
        cut.cost_assessments[0].approval_subject_state
        is PCostApprovalSubjectState.UNAVAILABLE_UNSUPPORTED_POLICY
    )
    assert cut.cost_assessments[0].approval_subjects == ()
    assert assessment.authority_outcome is AuthorityOutcome.ABSTAIN
    assert assessment.policy_relation is PolicyRelation.SOFT_EXCEPTION
    assert assessment.override_status is OverrideStatus.PENDING
    assert assessment.reason_codes == (PolicySupportStatus.POLICY_UNSIGNED.value,)


@pytest.mark.parametrize(
    "delta",
    [Decimal("-0.01"), Decimal("0.00"), Decimal("50.00")],
)
def test_pcost_inclusive_threshold_is_within_envelope(delta):
    bundle = _m3_bundle(cost_delta=delta)
    cut, _ = _build_cut(bundle)
    assessment = _assessment(cut)
    cost = cut.cost_assessments[0]

    assert cost.internal_labor_cost_delta == delta
    assert cost.applicability is CostApplicability.WITHIN_ENVELOPE
    assert not tuple(
        item for item in assessment.authority_requirements if item.gate is AuthorityGateCode.P_COST
    )
    assert assessment.authority_outcome is AuthorityOutcome.ACT


def test_pcost_above_threshold_missing_exact_approval_asks():
    bundle = _m3_bundle(cost_delta=Decimal("50.01"))
    cut, _ = _build_cut(bundle)
    assessment = _assessment(cut)

    assert cut.cost_assessments[0].applicability is CostApplicability.SOFT_EXCEPTION
    assert assessment.authority_outcome is AuthorityOutcome.ASK
    assert assessment.policy_relation is PolicyRelation.SOFT_EXCEPTION
    assert assessment.override_status is OverrideStatus.PENDING
    assert _gate(assessment, AuthorityGateCode.P_COST).decision is GateDecision.NEEDS_HUMAN_AUTHORITY


def test_pcost_above_threshold_exact_approval_can_act_with_recorded_risk():
    bundle = _m3_bundle(cost_delta=Decimal("50.01"))
    cut, _ = _build_cut(bundle, approved_gates=(AuthorityGateCode.P_COST,))
    assessment = _assessment(cut)

    assert assessment.authority_outcome is AuthorityOutcome.ACT
    assert assessment.policy_relation is PolicyRelation.SOFT_EXCEPTION
    assert assessment.override_status is OverrideStatus.APPROVED
    assert assessment.execution_authorization is ExecutionAuthorization.ALLOWED
    assert assessment.disposition is Disposition.EXECUTE_WITH_RECORDED_RISK


def test_incomplete_cost_abstains_even_when_approval_is_requested_as_input():
    bundle = _m3_bundle(incomplete_cost=True)
    cut, _ = _build_cut(bundle, approved_gates=(AuthorityGateCode.P_COST,))
    assessment = _assessment(cut)

    assert cut.cost_assessments[0].applicability is CostApplicability.UNKNOWN
    assert cut.cost_assessments[0].internal_labor_cost_delta is None
    assert assessment.authority_outcome is AuthorityOutcome.ABSTAIN
    assert assessment.reason_codes == ("INCOMPLETE_INTERNAL_LABOR_COST_CONSEQUENCE",)


def test_changed_consequence_changes_pcost_subject_and_scope():
    first_cut, _ = _build_cut(_m3_bundle(cost_delta=Decimal("50.01")))
    second_cut, _ = _build_cut(_m3_bundle(cost_delta=Decimal("50.02")))
    first = first_cut.cost_assessments[0].approval_subjects[0]
    second = second_cut.cost_assessments[0].approval_subjects[0]

    assert first.subject_id != second.subject_id
    assert first.authority_scope != second.authority_scope
    assert first.internal_cost_support_id == second.internal_cost_support_id
    assert first.m3d_result_id != second.m3d_result_id
    assert '"currency":"EUR"' in pcost_approval_subject_semantic_json(first)


def test_changed_m5_support_identity_changes_pcost_subject_and_scope():
    cut, _ = _build_cut(_m3_bundle(cost_delta=Decimal("50.01")))
    original = cut.cost_assessments[0].approval_subjects[0]
    changed_fingerprint = _digest("replacement-m5-support")
    changed = replace(
        original,
        internal_cost_support_id="m5-internal-cost-support-" + changed_fingerprint,
        internal_cost_support_fingerprint=changed_fingerprint,
    )

    assert changed.subject_id != original.subject_id
    assert changed.authority_scope != original.authority_scope


def test_old_pcost_approval_cannot_match_changed_consequence_subject():
    first_cut, _ = _build_cut(
        _m3_bundle(cost_delta=Decimal("50.01")),
        approved_gates=(AuthorityGateCode.P_COST,),
    )
    second_cut, _ = _build_cut(_m3_bundle(cost_delta=Decimal("50.02")))
    old_selection = next(
        item
        for item in first_cut.owner_approval_selections
        if item.status is SelectionStatus.SELECTED
    )
    new_requirement = next(
        item
        for item in second_cut.requirement_sets[0].requirements
        if item.gate is AuthorityGateCode.P_COST
    )

    assert old_selection.scope != new_requirement.scope
    with pytest.raises(OperationalAuthorityValidationError):
        OwnerApprovalSelection(
            requirement_id=new_requirement.requirement_id,
            requirement_fingerprint=new_requirement.requirement_fingerprint,
            authority_root_id=second_cut.authority_root.authority_root_id,
            authority_root_fingerprint=second_cut.authority_root.authority_root_fingerprint,
            owner_principal_id=old_selection.owner_principal_id,
            owner_principal_fingerprint=old_selection.owner_principal_fingerprint,
            scope=new_requirement.scope,
            status=SelectionStatus.SELECTED,
            approval_id=old_selection.approval_id,
            approval_fingerprint=old_selection.approval_fingerprint,
            recorded_scope_id=old_selection.recorded_scope_id,
            recorded_scope_fingerprint=old_selection.recorded_scope_fingerprint,
        )


def test_same_exact_private_vehicle_natural_scope_is_reused_across_candidates():
    bundle = _m3_bundle(
        private_vehicle=True,
        support_kwargs={"workers": (ANNA,), "sku_by_letter": {"A": "waterproof.bath"}},
    )
    assert len(bundle["m3"].candidates) == 2
    cut, _ = _build_cut(
        bundle,
        approved_gates=(AuthorityGateCode.P_VEHICLE,),
        consent_status=RecordedConsentStatus.FREELY_GIVEN,
    )
    owner = tuple(
        item for item in cut.owner_approval_selections if item.status is SelectionStatus.SELECTED
    )
    consents = tuple(
        item for item in cut.worker_consent_head_selections if item.status is SelectionStatus.SELECTED
    )
    result = evaluate_operational_authority(cut)

    assert len({item.scope.scope_id for item in owner}) == 1
    assert len({item.approval_id for item in owner}) == 1
    assert len({item.scope.scope_id for item in consents}) == 1
    assert len({item.consent_id for item in consents}) == 1
    assert all(item.authority_outcome is AuthorityOutcome.ACT for item in result.candidate_assessments)


@pytest.mark.parametrize(
    "field_name,replacement_value",
    [
        ("subject_id", "other-private-vehicle"),
        ("job_id", "other-job"),
        ("operational_date", D + timedelta(days=1)),
        ("worker_id", "other-worker"),
    ],
)
def test_private_vehicle_natural_scope_differs_on_every_frozen_dimension(
    field_name, replacement_value
):
    scope = AuthorityEvidenceScope(
        scope_kind=EvidenceScopeKind.PRIVATE_VEHICLE_USE,
        subject_type=EvidenceSubjectType.PRIVATE_VEHICLE,
        subject_id=CAR,
        operational_date=D,
        job_id="job-hero",
        worker_id=ANNA,
    )
    changed = replace(scope, **{field_name: replacement_value})

    assert changed.scope_id != scope.scope_id
    assert changed != scope


def test_same_exact_tag_natural_scope_is_reused_across_candidates():
    bundle = _m3_bundle()
    common_subjects = set(
        item.marker_subject_id
        for item in derive_authority_marker_subjects(bundle["m3"].candidates[0], bundle["support"])
    ).intersection(
        item.marker_subject_id
        for item in derive_authority_marker_subjects(bundle["m3"].candidates[1], bundle["support"])
    )
    marked = (sorted(common_subjects)[0],)
    cut, _ = _build_cut(
        bundle,
        marked_subject_ids=marked,
        approved_gates=(AuthorityGateCode.P_TAG,),
    )
    selections = tuple(
        item for item in cut.owner_approval_selections if item.status is SelectionStatus.SELECTED
    )

    assert len(selections) == 2
    assert len({item.scope.scope_id for item in selections}) == 1
    assert len({item.approval_id for item in selections}) == 1
    assert all(
        item.authority_outcome is AuthorityOutcome.ACT
        for item in evaluate_operational_authority(cut).candidate_assessments
    )


def test_tag_scope_change_by_subject_job_or_date_never_matches():
    bundle = _m3_bundle()
    subject = derive_authority_marker_subjects(bundle["m3"].candidates[0], bundle["support"])[0]
    scope = AuthorityEvidenceScope(
        scope_kind=EvidenceScopeKind.OWNER_APPROVAL_REQUIRED,
        subject_type=subject.evidence_subject_type,
        subject_id=subject.subject_id,
        operational_date=subject.operational_date,
        job_id=subject.job_id,
        worker_id=None,
    )

    assert replace(scope, subject_id="different-subject") != scope
    assert replace(scope, job_id="different-job") != scope
    assert replace(scope, operational_date=D + timedelta(days=1)) != scope


def _two_commitment_private_vehicle_candidate():
    commitments = (
        _commitment("A", worker=PETER),
        _commitment("B", worker=PETER),
    )
    bundle = _m3_bundle(
        evaluation_kwargs={"commitments": commitments, "direct_letters": ("A", "B")},
        support_kwargs={
            "workers": (ANNA,),
            "sku_by_letter": {"A": "waterproof.bath", "B": "waterproof.bath"},
        },
        private_vehicle=True,
    )
    left = next(
        item for item in bundle["m3"].candidates if item.modified_commitment_ids == ("commitment-A",)
    )
    right = next(
        item for item in bundle["m3"].candidates if item.modified_commitment_ids == ("commitment-B",)
    )

    def combined_impact(name):
        values = getattr(left.impact, name) + getattr(right.impact, name)
        return tuple(
            {
                (item.job_id, item.task_id, item.commitment_id): item
                for item in values
            }.values()
        )

    impact = CandidateImpactEvidence(
        direct_current_impact=combined_impact("direct_current_impact"),
        dependency_impact=combined_impact("dependency_impact"),
        candidate_induced_impact=combined_impact("candidate_induced_impact"),
        restored_impact=combined_impact("restored_impact"),
        residual_unresolved_impact=combined_impact("residual_unresolved_impact"),
    )
    candidate = replace(
        left,
        modified_commitment_ids=("commitment-A", "commitment-B"),
        canonical_job_ids=("job-hero",),
        canonical_task_ids=("task-A", "task-B"),
        proposed_worker_placements=(
            left.proposed_worker_placements[0],
            right.proposed_worker_placements[0],
        ),
        impact=impact,
        technical_constraint_results=(
            left.technical_constraint_results + right.technical_constraint_results
        ),
        rejection_trace=(),
        authority_impacts=tuple(set(left.authority_impacts + right.authority_impacts)),
        technical_ordering=replace(
            left.technical_ordering,
            restored_commitment_count=2,
            modified_commitment_count=2,
        ),
    )
    assert all(item.vehicle_id == CAR for item in candidate.proposed_worker_placements)
    return bundle, candidate


def test_duplicate_private_vehicle_derivation_paths_collapse_to_one_natural_scope():
    bundle, candidate = _two_commitment_private_vehicle_candidate()
    marker_fingerprint = _digest("empty-marker-cut")
    tag = CandidateApprovalTagAssessment(
        candidate_position=0,
        candidate_id=candidate.candidate_id,
        candidate_fingerprint=candidate.candidate_fingerprint,
        marker_cut_id="m3e1-authority-marker-cut-" + marker_fingerprint,
        marker_cut_fingerprint=marker_fingerprint,
        subject_ids=(),
        subject_fingerprints=(),
        applicable_scopes=(),
        coverage_complete=True,
    )
    requirements = derive_authority_requirements(
        candidate_position=0,
        candidate=candidate,
        support_snapshot=bundle["support"],
        cost_assessment=None,
        tag_assessment=tag,
    ).requirements
    vehicle = tuple(item for item in requirements if item.gate is AuthorityGateCode.P_VEHICLE)

    assert len(vehicle) == 2
    assert {item.required_principal_type for item in vehicle} == {
        PrincipalType.OWNER,
        PrincipalType.WORKER,
    }
    assert len({item.scope.scope_id for item in vehicle}) == 1


def test_duplicate_tag_subject_paths_collapse_before_requirement_derivation():
    bundle = _m3_bundle()
    candidate = bundle["m3"].candidates[0]
    scope = AuthorityEvidenceScope(
        scope_kind=EvidenceScopeKind.OWNER_APPROVAL_REQUIRED,
        subject_type=EvidenceSubjectType.RESOURCE,
        subject_id="m3e1-authority-marker-resource-subject-" + _digest("tag-worker"),
        operational_date=D,
        job_id="job-hero",
        worker_id=STEFAN,
    )
    marker_fingerprint = _digest("duplicate-tag-marker-cut")
    tag = CandidateApprovalTagAssessment(
        candidate_position=0,
        candidate_id=candidate.candidate_id,
        candidate_fingerprint=candidate.candidate_fingerprint,
        marker_cut_id="m3e1-authority-marker-cut-" + marker_fingerprint,
        marker_cut_fingerprint=marker_fingerprint,
        subject_ids=("marker-subject-a", "marker-subject-b"),
        subject_fingerprints=(_digest("marker-subject-a"), _digest("marker-subject-b")),
        applicable_scopes=(scope, scope),
        coverage_complete=True,
    )
    requirements = derive_authority_requirements(
        candidate_position=0,
        candidate=candidate,
        support_snapshot=bundle["support"],
        cost_assessment=None,
        tag_assessment=tag,
    ).requirements

    assert tag.applicable_scopes == (scope,)
    assert len(tuple(item for item in requirements if item.gate is AuthorityGateCode.P_TAG)) == 1


def test_overtime_complete_negative_proof_passes():
    cut, _ = _build_cut()
    assessment = _assessment(cut)

    assert cut.overtime_assessments[0].applicability is OvertimeApplicability.DOES_NOT_CREATE_OVERTIME
    assert _gate(assessment, AuthorityGateCode.P_OVERTIME).decision is GateDecision.PASS
    assert assessment.authority_outcome is AuthorityOutcome.ACT


def test_overtime_unknown_abstains():
    cut, _ = _build_cut(incomplete_overtime_positions=(0,))
    assessment = _assessment(cut)

    assert cut.overtime_assessments[0].applicability is OvertimeApplicability.UNKNOWN
    assert assessment.authority_outcome is AuthorityOutcome.ABSTAIN
    assert assessment.reason_codes == ("OVERTIME_EVIDENCE_UNKNOWN",)


def test_created_overtime_asks_and_cannot_resolve_through_e0_scope():
    def lengthen_first_candidate(position, candidate):
        if position != 0:
            return candidate
        placement = candidate.proposed_worker_placements[0]
        placement = replace(
            placement,
            proposed_end=placement.proposed_start + timedelta(hours=12),
        )
        return replace(candidate, proposed_worker_placements=(placement,))

    cut, _ = _build_cut(
        _m3_bundle(candidate_transform=lengthen_first_candidate),
        approved_gates=(AuthorityGateCode.P_COST,),
    )
    assessment = _assessment(cut)

    assert cut.overtime_assessments[0].applicability is OvertimeApplicability.CREATES_OVERTIME
    assert assessment.authority_outcome is AuthorityOutcome.ASK
    assert assessment.reason_codes == ("P_OVERTIME_AUTHORITY_REQUIRED",)
    assert not tuple(
        item for item in assessment.authority_requirements if item.gate is AuthorityGateCode.P_OVERTIME
    )


def test_one_worker_overtime_increase_is_not_offset_by_another_reduction():
    bundle = _m3_bundle(
        support_kwargs={"workers": (ANNA,), "sku_by_letter": {"A": "waterproof.bath"}}
    )
    candidate = bundle["m3"].candidates[0]
    baseline = next(
        item
        for item in bundle["support"].schedule.placements
        if item.commitment_id == candidate.modified_commitment_ids[0]
    )
    proposed = candidate.proposed_worker_placements[0]
    cut, _ = _build_cut(
        bundle,
        common_work_intervals_by_worker={
            PETER: ((baseline.planned_start - timedelta(hours=8), baseline.planned_start),),
            ANNA: ((proposed.proposed_start - timedelta(hours=8), proposed.proposed_start),),
        },
    )

    assert {item.worker_id for item in cut.overtime_assessments[0].comparisons} == {PETER, ANNA}
    assert cut.overtime_assessments[0].applicability is OvertimeApplicability.CREATES_OVERTIME


def test_incomplete_worker_period_coverage_cannot_be_negative_overtime_proof():
    bundle = _m3_bundle(
        support_kwargs={"workers": (ANNA,), "sku_by_letter": {"A": "waterproof.bath"}}
    )
    working_cut = _working_time_cut(bundle, incomplete_positions=(0,))
    overtime = assess_candidate_overtime(0, bundle["m3"].candidates[0], working_cut)

    assert overtime.applicability is OvertimeApplicability.UNKNOWN
    assert any(not item.coverage_complete for item in overtime.comparisons)


@pytest.mark.parametrize(
    "work_kind,field_name",
    [
        (WorkingTimeWorkKind.BASELINE_CHANGED, "interval_start"),
        (WorkingTimeWorkKind.PROPOSED_CANDIDATE, "interval_end"),
    ],
)
def test_refingerprinted_wrong_changed_interval_cannot_match_c0_or_m3(
    work_kind, field_name
):
    cut, _ = _build_cut()
    source = next(
        item for item in cut.working_time_cut.work_items if item.work_kind is work_kind
    )
    changed_value = (
        source.interval_start + timedelta(minutes=1)
        if field_name == "interval_start"
        else source.interval_end - timedelta(minutes=1)
    )
    malicious = _refingerprinted_work_item_cut(
        cut.working_time_cut,
        lambda item: item.work_item_id == source.work_item_id,
        **{field_name: changed_value},
    )

    with pytest.raises(
        OperationalAuthorityValidationError,
        match="working_time_changed_placement_binding",
    ):
        _rebuild_authority_cut_with_working_time(cut, malicious)


@pytest.mark.parametrize(
    "work_kind,wrong_worker",
    [
        (WorkingTimeWorkKind.BASELINE_CHANGED, STEFAN),
        (WorkingTimeWorkKind.PROPOSED_CANDIDATE, PETER),
    ],
)
def test_wrong_changed_worker_is_rejected(work_kind, wrong_worker):
    working = _working_time_cut(_m3_bundle())
    source = next(item for item in working.work_items if item.work_kind is work_kind)
    changed = replace(source, worker_id=wrong_worker)

    with pytest.raises(OperationalAuthorityValidationError):
        replace(
            working,
            work_items=tuple(
                changed if item.work_item_id == source.work_item_id else item
                for item in working.work_items
            ),
        )


def test_missing_required_worker_comparison_is_unknown_not_negative_proof():
    cut, _ = _build_cut()
    candidate = cut.working_time_cut.candidate_evidence[0]
    missing_worker = candidate.comparisons[0].worker_id
    incomplete = _omit_working_time_comparison(
        cut.working_time_cut,
        0,
        lambda item: item.worker_id == missing_worker,
    )
    rebuilt = _rebuild_authority_cut_with_working_time(cut, incomplete)

    assert rebuilt.overtime_assessments[0].applicability is OvertimeApplicability.UNKNOWN


def test_missing_required_calculation_period_is_unknown_not_negative_proof():
    bundle = _m3_bundle(
        support_kwargs={
            "worker_windows": {
                STEFAN: (
                    replace(
                        _support(_evaluation()).worker_availability[0].available_windows[0],
                        start_at=DAY_START + timedelta(days=1),
                        end_at=DAY_START + timedelta(days=2),
                    ),
                )
            }
        }
    )
    position = next(
        index
        for index, candidate in enumerate(bundle["m3"].candidates)
        if any(item.business_date != D for item in candidate.proposed_worker_placements)
    )
    cut, _ = _build_cut(bundle)
    target = cut.working_time_cut.candidate_evidence[position].comparisons[0]
    incomplete = _omit_working_time_comparison(
        cut.working_time_cut,
        position,
        lambda item: item.period_key == target.period_key,
    )
    rebuilt = _rebuild_authority_cut_with_working_time(cut, incomplete)

    assert rebuilt.overtime_assessments[position].applicability is OvertimeApplicability.UNKNOWN


def test_reassignment_truthfully_represents_baseline_only_and_candidate_only_workers():
    working = _working_time_cut(
        _m3_bundle(
            support_kwargs={"workers": (ANNA,), "sku_by_letter": {"A": "waterproof.bath"}}
        )
    )
    comparisons = working.candidate_evidence[0].comparisons

    assert any(item.baseline_work_item_ids and not item.candidate_work_item_ids for item in comparisons)
    assert any(item.candidate_work_item_ids and not item.baseline_work_item_ids for item in comparisons)


def test_complete_two_worker_two_period_universe_is_canonical_and_valid():
    bundle = _m3_bundle(
        support_kwargs={
            "worker_windows": {
                STEFAN: (
                    replace(
                        _support(_evaluation()).worker_availability[0].available_windows[0],
                        start_at=DAY_START + timedelta(days=1),
                        end_at=DAY_START + timedelta(days=2),
                    ),
                )
            }
        }
    )
    position = next(
        index
        for index, candidate in enumerate(bundle["m3"].candidates)
        if any(item.business_date != D for item in candidate.proposed_worker_placements)
    )
    working = _working_time_cut(bundle)
    evidence = working.candidate_evidence[position]

    assert evidence.universe_complete
    assert len({item.worker_id for item in evidence.comparisons}) == 2
    assert len({(item.calculation_period_start, item.calculation_period_end) for item in evidence.comparisons}) == 2
    assert tuple(item.period_key for item in evidence.comparisons) == tuple(
        sorted(item.period_key for item in evidence.comparisons)
    )


def test_twelve_hour_proposal_cannot_claim_zero_overtime_under_eight_hour_rule():
    def long_candidate(position, candidate):
        if position:
            return candidate
        placement = candidate.proposed_worker_placements[0]
        return replace(
            candidate,
            proposed_worker_placements=(
                replace(
                    placement,
                    proposed_end=placement.proposed_start + timedelta(hours=12),
                ),
            ),
        )

    working = _working_time_cut(_m3_bundle(candidate_transform=long_candidate))
    evidence = working.candidate_evidence[0]
    comparison = next(item for item in evidence.comparisons if item.candidate_overtime)
    falsified = replace(comparison, candidate_overtime=Decimal("0"))
    falsified_evidence = replace(
        evidence,
        comparisons=tuple(
            falsified if item.period_key == comparison.period_key else item
            for item in evidence.comparisons
        ),
    )

    with pytest.raises(OperationalAuthorityValidationError, match="derived_overtime"):
        replace(
            working,
            candidate_evidence=(falsified_evidence,) + working.candidate_evidence[1:],
        )


def test_overtime_increase_is_not_netted_against_decrease_in_another_period():
    def move_same_worker_to_next_day(position, candidate):
        if position:
            return candidate
        placement = candidate.proposed_worker_placements[0]
        return replace(
            candidate,
            proposed_worker_placements=(
                replace(
                    placement,
                    worker_id=PETER,
                    business_date=placement.business_date + timedelta(days=1),
                    proposed_start=placement.proposed_start + timedelta(days=1),
                    proposed_end=placement.proposed_end + timedelta(days=1),
                ),
            ),
        )

    bundle = _m3_bundle(candidate_transform=move_same_worker_to_next_day)
    baseline = bundle["support"].schedule.placements[0]
    proposed = bundle["m3"].candidates[0].proposed_worker_placements[0]
    working = _working_time_cut(
        bundle,
        common_intervals_by_worker={
            PETER: (
                (baseline.planned_start - timedelta(hours=8), baseline.planned_start),
                (proposed.proposed_start - timedelta(hours=8), proposed.proposed_start),
            )
        },
    )
    overtime = assess_candidate_overtime(0, bundle["m3"].candidates[0], working)

    assert any(item.candidate_overtime > item.baseline_overtime for item in overtime.comparisons)
    assert any(item.candidate_overtime < item.baseline_overtime for item in overtime.comparisons)
    assert overtime.applicability is OvertimeApplicability.CREATES_OVERTIME


def _eight_unchanged_stefan_bundle():
    commitments = (_commitment("A", worker=PETER),) + tuple(
        _commitment(letter, worker=STEFAN) for letter in "BCDEFGHI"
    )
    unchanged_start = DAY_START - timedelta(hours=6)
    starts = {
        "A": DAY_START + timedelta(hours=4),
        **{
            letter: unchanged_start + timedelta(hours=position)
            for position, letter in enumerate("BCDEFGHI")
        },
    }
    return _m3_bundle(
        evaluation_kwargs={"commitments": commitments, "direct_letters": ("A",)},
        support_kwargs={"workers": (STEFAN,), "starts": starts},
    )


def test_ot_complete_01_eight_unchanged_hours_plus_candidate_creates_overtime_and_asks():
    cut, _ = _build_cut(_eight_unchanged_stefan_bundle())
    comparison = next(
        item
        for item in cut.overtime_assessments[0].comparisons
        if item.worker_id == STEFAN
    )
    assessment = _assessment(cut)

    assert comparison.baseline_overtime == Decimal("0")
    assert comparison.candidate_overtime == Decimal("3600")
    assert cut.overtime_assessments[0].applicability is OvertimeApplicability.CREATES_OVERTIME
    assert assessment.authority_outcome is AuthorityOutcome.ASK
    assert assessment.reason_codes == ("P_OVERTIME_AUTHORITY_REQUIRED",)


@pytest.mark.parametrize("omit_all", [False, True])
def test_ot_complete_02_03_omitted_c0_common_work_is_rejected(omit_all):
    cut, _ = _build_cut(_eight_unchanged_stefan_bundle())
    stefan_common = tuple(
        item
        for item in cut.working_time_cut.work_items
        if item.worker_id == STEFAN
        and item.work_kind is WorkingTimeWorkKind.UNCHANGED_SCHEDULED
        and item.source_record_id
        == cut.feasibility_support_snapshot.schedule.source_record_id
    )
    assert len(stefan_common) == 8
    selected_ids = {
        item.work_item_id for item in (stefan_common if omit_all else stefan_common[:1])
    }
    malicious = _omit_common_work_items_for_candidate(
        cut.working_time_cut,
        0,
        lambda item: item.work_item_id in selected_ids,
    )

    with pytest.raises(
        OperationalAuthorityValidationError,
        match="working_time_c0_common_coverage",
    ):
        _rebuild_authority_cut_with_working_time(cut, malicious)


def test_ot_complete_04_missing_working_time_source_head_is_rejected():
    working = _working_time_cut(_eight_unchanged_stefan_bundle())

    with pytest.raises(
        OperationalAuthorityValidationError,
        match="working_time_source_head_manifest_binding",
    ):
        replace(working, source_head_observations=())


@pytest.mark.parametrize(
    "field_name,replacement_value",
    [
        ("source_head_id", "wrong-working-time-source-head"),
        ("source_revision", 99),
        ("source_fingerprint", _digest("wrong-working-time-source-head")),
    ],
)
def test_ot_complete_05_mismatched_source_head_identity_revision_or_fingerprint_is_rejected(
    field_name, replacement_value
):
    working = _working_time_cut(_eight_unchanged_stefan_bundle())
    c0 = next(
        item
        for item in working.common_work_manifests
        if item.source_kind is WorkingTimeSourceKind.C0_PLAN_SCHEDULE
    )
    with pytest.raises(OperationalAuthorityValidationError):
        changed = replace(c0, **{field_name: replacement_value})
        manifests = tuple(
            changed if item.manifest_id == c0.manifest_id else item
            for item in working.common_work_manifests
        )
        replace(
            working,
            common_work_manifests=manifests,
            source_head_observations=tuple(
                EvidenceBinding(
                    kind="WORKING_TIME_SOURCE_MANIFEST",
                    record_id=item.manifest_id,
                    fingerprint=item.manifest_fingerprint,
                )
                for item in manifests
            ),
        )


def test_ot_complete_06_complete_c0_common_manifest_is_valid():
    cut, _ = _build_cut(_eight_unchanged_stefan_bundle())
    comparison = next(
        item
        for item in cut.working_time_cut.candidate_evidence[0].comparisons
        if item.worker_id == STEFAN
    )
    common = tuple(
        item_id
        for item_id in comparison.baseline_work_item_ids
        if next(
            item
            for item in cut.working_time_cut.work_items
            if item.work_item_id == item_id
        ).work_kind
        is WorkingTimeWorkKind.UNCHANGED_SCHEDULED
    )

    assert len(common) == 8
    assert cut.working_time_cut.common_work_manifests
    assert cut.working_time_cut.source_head_observations
    assert cut.overtime_assessments[0].applicability is OvertimeApplicability.CREATES_OVERTIME


def test_ot_source_01_populated_ledger_plus_candidate_work_creates_overtime_and_asks():
    cut, position = _ledger_eight_hours_then_candidate_cut()
    comparison = next(
        item
        for item in cut.overtime_assessments[position].comparisons
        if item.worker_id == STEFAN
    )

    assert comparison.baseline_overtime == Decimal("0")
    assert comparison.candidate_overtime == Decimal("3600")
    assert (
        cut.overtime_assessments[position].applicability
        is OvertimeApplicability.CREATES_OVERTIME
    )
    assert _assessment(cut, position).authority_outcome is AuthorityOutcome.ASK


def test_ot_source_02_deleting_entire_populated_ledger_source_class_is_rejected():
    cut, _ = _ledger_eight_hours_then_candidate_cut()
    changes = _without_ledger_source_class(cut.working_time_cut)

    with pytest.raises(
        OperationalAuthorityValidationError,
        match="working_time_source_selection_universe",
    ):
        replace(cut.working_time_cut, **changes)


def test_ot_source_03_selection_without_ledger_manifest_fails_closed():
    working = _working_time_cut(_m3_bundle())
    ledger = _working_time_source_manifest(
        working,
        WorkingTimeSourceKind.WORKING_TIME_LEDGER,
    )

    with pytest.raises(
        OperationalAuthorityValidationError,
        match="working_time_source_selection_manifest_coverage",
    ):
        replace(
            working,
            common_work_manifests=tuple(
                item for item in working.common_work_manifests if item is not ledger
            ),
        )


def test_ot_source_04_ledger_manifest_without_source_head_fails_closed():
    working = _working_time_cut(_m3_bundle())
    ledger = _working_time_source_manifest(
        working,
        WorkingTimeSourceKind.WORKING_TIME_LEDGER,
    )

    with pytest.raises(
        OperationalAuthorityValidationError,
        match="working_time_source_head_manifest_binding",
    ):
        replace(
            working,
            source_head_observations=tuple(
                item
                for item in working.source_head_observations
                if item.record_id != ledger.manifest_id
            ),
        )


def test_ot_source_05_manifest_and_head_without_source_selection_fail_closed():
    working = _working_time_cut(_m3_bundle())

    with pytest.raises(
        OperationalAuthorityValidationError,
        match="working_time_source_selection_universe",
    ):
        replace(
            working,
            source_selections=tuple(
                item
                for item in working.source_selections
                if item.source_kind is not WorkingTimeSourceKind.WORKING_TIME_LEDGER
            ),
        )


def test_ot_source_06_explicit_authoritatively_empty_ledger_is_complete():
    working = _working_time_cut(_m3_bundle())
    selection = _working_time_source_selection(
        working,
        WorkingTimeSourceKind.WORKING_TIME_LEDGER,
    )
    manifest = _working_time_source_manifest(
        working,
        WorkingTimeSourceKind.WORKING_TIME_LEDGER,
    )

    assert selection.state is WorkingTimeSourceClassState.AUTHORITATIVELY_EMPTY
    assert selection.source_head_id == manifest.source_head_id
    assert selection.manifest_id == manifest.manifest_id
    assert manifest.coverage_complete
    assert all(not item.common_work_item_ids for item in manifest.entries)


def test_ot_source_07_explicit_empty_ledger_preserves_legitimate_negative_proof_and_act():
    cut, _ = _build_cut()

    assert (
        cut.overtime_assessments[0].applicability
        is OvertimeApplicability.DOES_NOT_CREATE_OVERTIME
    )
    assert _assessment(cut).authority_outcome is AuthorityOutcome.ACT


def test_ot_source_08_unknown_ledger_is_unknown_and_abstains():
    cut, _ = _build_cut(
        ledger_source_state=WorkingTimeSourceClassState.UNKNOWN,
        ledger_reason_code="WORKING_TIME_LEDGER_UNAVAILABLE",
    )
    selection = _working_time_source_selection(
        cut.working_time_cut,
        WorkingTimeSourceKind.WORKING_TIME_LEDGER,
    )
    currentness = next(
        item
        for item in cut.currentness_vector.observations
        if item.subject_key.startswith("WORKING_TIME_SOURCE_SELECTION|")
        and "WORKING_TIME_LEDGER" in item.subject_key
    )

    assert selection.state is WorkingTimeSourceClassState.UNKNOWN
    assert selection.reason_code == "WORKING_TIME_LEDGER_UNAVAILABLE"
    assert currentness.expected_state is CurrentnessEvidenceState.UNKNOWN
    assert not currentness.exact_match
    assert cut.overtime_assessments[0].applicability is OvertimeApplicability.UNKNOWN
    assert _assessment(cut).authority_outcome is AuthorityOutcome.ABSTAIN


def test_ot_source_09_wrong_ledger_revision_fails_closed():
    working = _working_time_cut(_m3_bundle())
    selected = _working_time_source_selection(
        working,
        WorkingTimeSourceKind.WORKING_TIME_LEDGER,
    )
    changed = replace(selected, source_revision=selected.source_revision + 1)

    with pytest.raises(
        OperationalAuthorityValidationError,
        match="working_time_source_selection_manifest_binding",
    ):
        replace(
            working,
            source_selections=tuple(
                changed if item.source_kind is selected.source_kind else item
                for item in working.source_selections
            ),
        )


def test_ot_source_10_wrong_ledger_fingerprint_fails_closed():
    working = _working_time_cut(_m3_bundle())
    selected = _working_time_source_selection(
        working,
        WorkingTimeSourceKind.WORKING_TIME_LEDGER,
    )
    changed = replace(selected, source_fingerprint=_digest("wrong-ledger-content"))

    with pytest.raises(
        OperationalAuthorityValidationError,
        match="working_time_source_selection_manifest_binding",
    ):
        replace(
            working,
            source_selections=tuple(
                changed if item.source_kind is selected.source_kind else item
                for item in working.source_selections
            ),
        )


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("source_capture_id", "wrong-ledger-capture"),
        ("source_capture_generation", 99),
        ("source_capture_fingerprint", _digest("wrong-ledger-capture")),
        ("provenance_reference", "wrong-ledger-provenance"),
    ],
)
def test_ot_source_11_wrong_ledger_capture_or_provenance_fails_closed(
    field_name, value
):
    working = _working_time_cut(_m3_bundle())
    selected = _working_time_source_selection(
        working,
        WorkingTimeSourceKind.WORKING_TIME_LEDGER,
    )
    changed = replace(selected, **{field_name: value})

    with pytest.raises(
        OperationalAuthorityValidationError,
        match="working_time_source_selection_manifest_binding",
    ):
        replace(
            working,
            source_selections=tuple(
                changed if item.source_kind is selected.source_kind else item
                for item in working.source_selections
            ),
        )


def test_ot_source_12_empty_to_populated_ledger_changes_working_time_cut_identity():
    empty, bundle = _build_cut()
    populated, _ = _build_cut(
        bundle,
        common_work_intervals_by_worker={
            STEFAN: ((DAY_START - timedelta(hours=2), DAY_START - timedelta(hours=1)),)
        },
    )

    assert (
        _working_time_source_selection(
            empty.working_time_cut,
            WorkingTimeSourceKind.WORKING_TIME_LEDGER,
        ).state
        is WorkingTimeSourceClassState.AUTHORITATIVELY_EMPTY
    )
    assert (
        _working_time_source_selection(
            populated.working_time_cut,
            WorkingTimeSourceKind.WORKING_TIME_LEDGER,
        ).state
        is WorkingTimeSourceClassState.PRESENT
    )
    assert empty.working_time_cut.cut_id != populated.working_time_cut.cut_id


def test_ot_source_13_ledger_revision_changes_authority_cut_and_currentness_identity():
    first, _ = _build_cut()
    changed_working = _revised_empty_ledger_working_cut(first.working_time_cut)
    second = _rebuild_authority_cut_with_working_time(first, changed_working)

    assert first.working_time_cut.cut_id != second.working_time_cut.cut_id
    assert first.currentness_vector.vector_id != second.currentness_vector.vector_id
    assert first.cut_id != second.cut_id


def test_ot_source_14_omitted_c0_manifest_remains_rejected():
    working = _working_time_cut(_m3_bundle())
    c0 = _working_time_source_manifest(
        working,
        WorkingTimeSourceKind.C0_PLAN_SCHEDULE,
    )

    with pytest.raises(
        OperationalAuthorityValidationError,
        match="working_time_source_selection_manifest_coverage",
    ):
        replace(
            working,
            common_work_manifests=tuple(
                item for item in working.common_work_manifests if item is not c0
            ),
        )


def test_tag_applicable_requires_exact_owner_approval():
    bundle = _m3_bundle()
    subject = derive_authority_marker_subjects(bundle["m3"].candidates[0], bundle["support"])[0]
    cut, _ = _build_cut(bundle, marked_subject_ids=(subject.marker_subject_id,))
    assessment = _assessment(cut)

    assert cut.approval_tag_assessments[0].applicability is ApprovalMarkerApplicability.APPLICABLE
    assert assessment.authority_outcome is AuthorityOutcome.ASK
    assert _gate(assessment, AuthorityGateCode.P_TAG).decision is GateDecision.NEEDS_HUMAN_AUTHORITY


def test_tag_complete_negative_coverage_is_not_applicable():
    cut, _ = _build_cut()

    assert cut.approval_tag_assessments[0].applicability is ApprovalMarkerApplicability.NOT_APPLICABLE
    assert all(item.status is MarkerCoverageStatus.NO_MARKER for item in cut.marker_support_cut.coverages)
    assert _assessment(cut).authority_outcome is AuthorityOutcome.ACT


def test_tag_incomplete_coverage_is_unknown_and_abstains():
    bundle = _m3_bundle()
    subject = derive_authority_marker_subjects(bundle["m3"].candidates[0], bundle["support"])[0]
    cut, _ = _build_cut(bundle, unknown_marker_subject_ids=(subject.marker_subject_id,))

    assert cut.approval_tag_assessments[0].applicability is ApprovalMarkerApplicability.UNKNOWN
    assert _assessment(cut).authority_outcome is AuthorityOutcome.ABSTAIN


def test_residual_impact_does_not_add_a_tag_subject():
    bundle = _m3_bundle()
    original = bundle["m3"].candidates[0]
    changed = replace(
        original,
        impact=replace(
            original.impact,
            residual_unresolved_impact=original.impact.direct_current_impact,
        ),
    )

    before = derive_authority_marker_subjects(original, bundle["support"])
    after = derive_authority_marker_subjects(changed, bundle["support"])
    assert before == after


def test_direct_dependency_and_restored_impact_do_not_add_subjects_by_membership_alone():
    bundle = _m3_bundle()
    candidate = bundle["m3"].candidates[0]
    baseline = derive_authority_marker_subjects(candidate, bundle["support"])
    unrelated = candidate.impact.direct_current_impact
    changed = replace(
        candidate,
        impact=CandidateImpactEvidence(
            direct_current_impact=unrelated,
            dependency_impact=unrelated,
            candidate_induced_impact=candidate.impact.candidate_induced_impact,
            restored_impact=unrelated,
            residual_unresolved_impact=(),
        ),
    )

    assert derive_authority_marker_subjects(changed, bundle["support"]) == baseline


def test_current_freely_given_private_vehicle_consent_passes_with_owner_approval():
    bundle = _m3_bundle(
        private_vehicle=True,
        support_kwargs={"workers": (ANNA,), "sku_by_letter": {"A": "waterproof.bath"}},
    )
    cut, _ = _build_cut(bundle, approved_gates=(AuthorityGateCode.P_VEHICLE,))

    assert all(item.is_affirmative_current_consent for item in cut.worker_consent_head_selections)
    assert _assessment(cut).authority_outcome is AuthorityOutcome.ACT


@pytest.mark.parametrize(
    "consent_status",
    [
        None,
        RecordedConsentStatus.REQUESTED,
        RecordedConsentStatus.DECLINED,
        RecordedConsentStatus.PRESSURED,
        RecordedConsentStatus.ABSENT,
        RecordedConsentStatus.REVOKED,
        RecordedConsentStatus.UNKNOWN,
    ],
)
def test_nonaffirmative_private_vehicle_consent_abstains(consent_status):
    bundle = _m3_bundle(
        private_vehicle=True,
        support_kwargs={"workers": (ANNA,), "sku_by_letter": {"A": "waterproof.bath"}},
    )
    cut, _ = _build_cut(
        bundle,
        approved_gates=(AuthorityGateCode.P_VEHICLE,),
        consent_status=consent_status,
    )

    assert _assessment(cut).authority_outcome is AuthorityOutcome.ABSTAIN


def test_historical_superseded_freely_given_consent_is_not_current_authority():
    bundle = _m3_bundle(
        private_vehicle=True,
        support_kwargs={"workers": (ANNA,), "sku_by_letter": {"A": "waterproof.bath"}},
    )
    cut, _ = _build_cut(
        bundle,
        approved_gates=(AuthorityGateCode.P_VEHICLE,),
        consent_status=RecordedConsentStatus.FREELY_GIVEN,
        consent_current=False,
    )

    assert not cut.worker_consent_head_selections[0].is_affirmative_current_consent
    assert _assessment(cut).authority_outcome is AuthorityOutcome.ABSTAIN


def test_owner_approval_missing_with_valid_vehicle_consent_asks():
    bundle = _m3_bundle(
        private_vehicle=True,
        support_kwargs={"workers": (ANNA,), "sku_by_letter": {"A": "waterproof.bath"}},
    )
    cut, _ = _build_cut(bundle, consent_status=RecordedConsentStatus.FREELY_GIVEN)

    assert _assessment(cut).authority_outcome is AuthorityOutcome.ASK
    assert _assessment(cut).reason_codes == ("P_VEHICLE_OWNER_APPROVAL_REQUIRED",)


def test_search_exhausted_without_feasible_candidate_preserves_assessments():
    bundle = _m3_bundle(
        support_kwargs={"worker_windows": {STEFAN: ()}},
        force_exhausted=True,
    )
    cut, _ = _build_cut(bundle)
    result = evaluate_operational_authority(cut)

    assert result.evaluation_status is M3AuthorityEvaluationStatus.CANDIDATES_ASSESSED
    assert result.search_envelope_exhausted is True
    assert result.search_authority_projection is SearchAuthorityProjection.ABSTAIN
    assert result.top_level_reason_codes == ("ENVELOPE_EXHAUSTED",)
    assert len(result.candidate_assessments) == len(bundle["m3"].candidates)


def test_search_exhausted_with_feasible_candidate_does_not_downgrade_it():
    bundle = _m3_bundle(force_exhausted=True)
    cut, _ = _build_cut(bundle)
    result = evaluate_operational_authority(cut)

    assert result.search_envelope_exhausted is True
    assert result.search_authority_projection is SearchAuthorityProjection.NOT_APPLICABLE
    assert result.top_level_reason_codes == ()
    assert any(item.authority_outcome is AuthorityOutcome.ACT for item in result.candidate_assessments)


def test_mixed_candidate_outcomes_are_retained_in_exact_order_without_aggregate():
    bundle = _m3_bundle(
        support_kwargs={"workers": (ANNA, STEFAN), "sku_by_letter": {"A": "waterproof.bath"}}
    )
    cut, _ = _build_cut(bundle)
    result = evaluate_operational_authority(cut)

    outcomes = tuple(item.authority_outcome for item in result.candidate_assessments)
    assert AuthorityOutcome.ACT in outcomes
    assert AuthorityOutcome.BLOCK in outcomes
    assert tuple(item.candidate_id for item in result.candidate_assessments) == tuple(
        item.candidate_id for item in bundle["m3"].candidates
    )
    assert not hasattr(result, "authority_outcome")


def test_ordinary_act_projection_is_exact():
    cut, _ = _build_cut()
    assessment = _assessment(cut)

    assert assessment.authority_outcome is AuthorityOutcome.ACT
    assert assessment.technical_feasibility is TechnicalFeasibility.FEASIBLE
    assert assessment.policy_relation is PolicyRelation.WITHIN_ENVELOPE
    assert assessment.override_status is OverrideStatus.NOT_REQUIRED
    assert assessment.execution_authorization is ExecutionAuthorization.ALLOWED
    assert assessment.disposition is Disposition.EXECUTE
    assert all(item.decision is GateDecision.PASS for item in assessment.gate_assessments)


def test_approved_tag_soft_exception_act_projection_is_exact():
    bundle = _m3_bundle()
    subject = derive_authority_marker_subjects(bundle["m3"].candidates[0], bundle["support"])[0]
    cut, _ = _build_cut(
        bundle,
        marked_subject_ids=(subject.marker_subject_id,),
        approved_gates=(AuthorityGateCode.P_TAG,),
    )
    assessment = _assessment(cut)

    assert assessment.authority_outcome is AuthorityOutcome.ACT
    assert assessment.policy_relation is PolicyRelation.SOFT_EXCEPTION
    assert assessment.override_status is OverrideStatus.APPROVED
    assert assessment.disposition is Disposition.EXECUTE_WITH_RECORDED_RISK


def test_later_day_candidate_asks_without_fabricating_e0_scope():
    bundle = _m3_bundle(
        support_kwargs={
            "worker_windows": {
                STEFAN: (
                    replace(
                        _support(_evaluation()).worker_availability[0].available_windows[0],
                        start_at=DAY_START + timedelta(days=1),
                        end_at=DAY_START + timedelta(days=2),
                    ),
                )
            }
        }
    )
    later_position = next(
        position
        for position, candidate in enumerate(bundle["m3"].candidates)
        if any(item.business_date != D for item in candidate.proposed_worker_placements)
    )
    cut, _ = _build_cut(bundle)
    assessment = _assessment(cut, later_position)

    assert assessment.authority_outcome is AuthorityOutcome.ASK
    assert assessment.reason_codes == ("LATER_OPERATIONAL_DAY_AUTHORITY_REQUIRED",)
    assert not tuple(
        item for item in assessment.authority_requirements if item.gate is AuthorityGateCode.P_HORIZON
    )


def test_separately_proven_hard_boundary_is_forbidden_block():
    cut, _ = _build_cut(
        hard_boundary_overrides={
            0: (
                HardBoundaryApplicability.APPLICABLE,
                HardBoundaryKind.SAFETY,
                "HARD_SAFETY_BLOCK",
            )
        }
    )
    assessment = _assessment(cut)

    assert assessment.authority_outcome is AuthorityOutcome.BLOCK
    assert assessment.policy_relation is PolicyRelation.HARD_BLOCK
    assert assessment.override_status is OverrideStatus.NOT_REQUIRED
    assert assessment.execution_authorization is ExecutionAuthorization.BLOCKED
    assert assessment.disposition is Disposition.FORBIDDEN


def test_hard_boundary_wins_over_approved_soft_exception():
    bundle = _m3_bundle(cost_delta=Decimal("50.01"))
    cut, _ = _build_cut(
        bundle,
        approved_gates=(AuthorityGateCode.P_COST,),
        hard_boundary_overrides={
            0: (
                HardBoundaryApplicability.APPLICABLE,
                HardBoundaryKind.LEGAL,
                "LEGAL_PROHIBITION",
            )
        },
    )

    assert _assessment(cut).authority_outcome is AuthorityOutcome.BLOCK
    assert _assessment(cut).disposition is Disposition.FORBIDDEN


def test_unknown_currentness_wins_over_missing_approval_ask():
    bundle = _m3_bundle(cost_delta=Decimal("50.01"))
    cut, _ = _build_cut(
        bundle,
        stale_kind=CurrentnessKind.M2_ASSIGNMENT_PRECONDITIONS,
    )
    assessment = _assessment(cut)

    assert assessment.authority_outcome is AuthorityOutcome.ABSTAIN
    assert assessment.reason_codes == ("DECISION_TIME_CURRENTNESS_FAILED",)


def test_partial_repair_wins_even_when_every_available_approval_is_selected():
    def add_residual(_position, candidate):
        return replace(
            candidate,
            impact=replace(
                candidate.impact,
                residual_unresolved_impact=candidate.impact.direct_current_impact,
            ),
        )

    bundle = _m3_bundle(candidate_transform=add_residual, cost_delta=Decimal("50.01"))
    cut, _ = _build_cut(bundle, approved_gates=(AuthorityGateCode.P_COST,))

    assert _assessment(cut).authority_outcome is AuthorityOutcome.ABSTAIN
    assert _assessment(cut).reason_codes == ("PARTIAL_REPAIR_UNRESOLVED_IMPACT",)


def test_stale_evidence_precedes_partial_repair():
    def add_residual(_position, candidate):
        return replace(
            candidate,
            impact=replace(
                candidate.impact,
                residual_unresolved_impact=candidate.impact.direct_current_impact,
            ),
        )

    bundle = _m3_bundle(candidate_transform=add_residual)
    cut, _ = _build_cut(
        bundle,
        stale_kind=CurrentnessKind.M1_HANDOFF_PRECONDITIONS,
    )

    assert _assessment(cut).authority_outcome is AuthorityOutcome.ABSTAIN
    assert _assessment(cut).reason_codes == ("DECISION_TIME_CURRENTNESS_FAILED",)


def test_currentness_vector_contains_every_frozen_category_and_exact_bindings():
    cut, _ = _build_cut()
    vector = cut.currentness_vector

    assert {item.kind for item in vector.observations} == set(CurrentnessKind)
    assert vector.all_current
    assert vector.evaluation_input_id == cut.evaluation_input.evaluation_input_id
    assert vector.c0_source_cut_id == cut.feasibility_support_snapshot.source_cut_id
    assert vector.m3_result_id == cut.m3_result.result_id
    assert vector.internal_cost_support_id == cut.internal_cost_support.support_id
    assert vector.m3d_result_id == cut.m3d_result.result_id
    assert vector.working_time_cut_id == cut.working_time_cut.cut_id
    assert vector.marker_cut_id == cut.marker_support_cut.cut_id
    assert set(vector.trusted_principal_ids) == {
        item.principal_id for item in cut.trusted_principals
    }
    assert set(vector.authority_requirement_set_ids) == {
        item.requirement_set_id for item in cut.requirement_sets
    }


def test_missing_currentness_category_is_rejected():
    cut, _ = _build_cut()
    removed_kind = CurrentnessKind.DERIVED_UNIVERSES_RESULT_INPUTS

    with pytest.raises(OperationalAuthorityValidationError):
        replace(
            cut.currentness_vector,
            observations=tuple(
                item
                for item in cut.currentness_vector.observations
                if item.kind is not removed_kind
            ),
        )


def test_stale_task_precondition_prevents_act():
    cut, _ = _build_cut(stale_kind=CurrentnessKind.M2_JOB_TASK_PRECONDITIONS)

    assert not cut.currentness_vector.all_current
    assert _assessment(cut).authority_outcome is AuthorityOutcome.ABSTAIN


def _replace_required_currentness_subject_with_generic(cut, kind, family):
    target = next(
        item
        for item in cut.currentness_vector.observations
        if item.kind is kind and item.subject_key.startswith(family + "|")
    )
    generic = replace(target, subject_key="GENERIC_CATEGORY_PLACEHOLDER")
    vector = replace(
        cut.currentness_vector,
        observations=tuple(
            generic if item.observation_id == target.observation_id else item
            for item in cut.currentness_vector.observations
        ),
    )
    with pytest.raises(
        OperationalAuthorityValidationError,
        match="currentness_required_subject_coverage",
    ):
        replace(cut, currentness_vector=vector)


def test_currentness_exact_required_subject_universe_is_complete():
    cut, _ = _build_cut(
        _m3_bundle(private_vehicle=True, support_kwargs={"sku_by_letter": {"A": "waterproof.bath"}})
    )
    observations = cut.currentness_vector.observations

    assert cut.currentness_vector.all_current
    assert len(observations) > len(CurrentnessKind)
    assert {item.kind for item in observations} == set(CurrentnessKind)
    assert len({(item.kind, item.subject_key) for item in observations}) == len(observations)
    assert {
        item.subject_key
        for item in observations
        if item.kind is CurrentnessKind.M2_JOB_TASK_PRECONDITIONS
    } == {
        "M2_JOB_TASK|" + canonical_json([item.job_id, item.task_id])
        for item in cut.evaluation_input.current_m2_task_preconditions
    }
    assert len(
        tuple(
            item
            for item in observations
            if item.kind is CurrentnessKind.PLAN_DAY_PRECONDITIONS
        )
    ) == len(cut.evaluation_input.plan_day_associations)


@pytest.mark.parametrize(
    "kind,family",
    [
        (CurrentnessKind.M2_JOB_TASK_PRECONDITIONS, "M2_JOB_TASK"),
        (CurrentnessKind.M2_ASSIGNMENT_PRECONDITIONS, "M2_ASSIGNMENT"),
        (CurrentnessKind.PLAN_DAY_PRECONDITIONS, "PLAN_DAY_ROOT_HEAD"),
        (CurrentnessKind.C0_SUPPORT_SELECTED_SOURCES, "C0_TASK_CONSTRAINT"),
    ],
)
def test_missing_exact_precondition_or_source_subject_is_rejected(kind, family):
    cut, _ = _build_cut()
    _replace_required_currentness_subject_with_generic(cut, kind, family)


def test_missing_owner_approval_selection_currentness_subject_is_rejected():
    cut, _ = _build_cut(_m3_bundle(cost_delta=Decimal("50.01")))
    _replace_required_currentness_subject_with_generic(
        cut,
        CurrentnessKind.OWNER_APPROVAL_SELECTIONS,
        "OWNER_APPROVAL_REQUIREMENT_SELECTION",
    )


def test_missing_worker_consent_head_currentness_subject_is_rejected():
    cut, _ = _build_cut(
        _m3_bundle(
            private_vehicle=True,
            support_kwargs={"sku_by_letter": {"A": "waterproof.bath"}},
        )
    )
    _replace_required_currentness_subject_with_generic(
        cut,
        CurrentnessKind.WORKER_CONSENT_HEADS,
        "WORKER_CONSENT_REQUIREMENT_HEAD",
    )


def test_wrong_subject_key_with_correct_currentness_kind_is_rejected():
    cut, _ = _build_cut()
    _replace_required_currentness_subject_with_generic(
        cut,
        CurrentnessKind.M2_JOB_TASK_PRECONDITIONS,
        "M2_JOB_TASK",
    )


def test_one_generic_observation_per_category_cannot_authorize_cut():
    cut, _ = _build_cut()
    by_kind = {
        kind: next(
            item for item in cut.currentness_vector.observations if item.kind is kind
        )
        for kind in CurrentnessKind
    }
    generic = tuple(
        replace(item, subject_key="GENERIC_CATEGORY_PLACEHOLDER")
        for item in by_kind.values()
    )
    vector = replace(cut.currentness_vector, observations=generic)

    assert vector.all_current
    with pytest.raises(
        OperationalAuthorityValidationError,
        match="currentness_required_subject_coverage",
    ):
        replace(cut, currentness_vector=vector)


def test_duplicate_exact_currentness_subject_is_rejected():
    cut, _ = _build_cut()
    target = cut.currentness_vector.observations[0]

    with pytest.raises(OperationalAuthorityValidationError):
        replace(
            cut.currentness_vector,
            observations=(target, target) + cut.currentness_vector.observations[1:],
        )


def _plan_day_currentness_observation(cut, plan_day_id):
    return next(
        item
        for item in cut.currentness_vector.observations
        if item.kind is CurrentnessKind.PLAN_DAY_PRECONDITIONS
        and item.expected_identity == plan_day_id
    )


def _observe_new_plan_day_head_on_old_cut(old_cut, new_cut, plan_day_id):
    old = _plan_day_currentness_observation(old_cut, plan_day_id)
    new = _plan_day_currentness_observation(new_cut, plan_day_id)
    stale = replace(
        old,
        observed_state=new.expected_state,
        observed_identity=new.expected_identity,
        observed_revision=new.expected_revision,
        observed_fingerprint=new.expected_fingerprint,
        observed_status=new.expected_status,
        observed_semantic_json=new.expected_semantic_json,
    )
    vector = replace(
        old_cut.currentness_vector,
        observations=tuple(
            stale if item.observation_id == old.observation_id else item
            for item in old_cut.currentness_vector.observations
        ),
    )
    return replace(old_cut, currentness_vector=vector)


@pytest.mark.parametrize("change", ["revision", "content", "status"])
def test_pd_01_02_03_plan_day_revision_content_or_status_change_is_stale(change):
    bundle = _m3_bundle()
    roots = list(_plan_day_roots(bundle))
    old_cut, _ = _build_cut(bundle, plan_day_roots=tuple(roots))
    original = roots[0]
    if change == "revision":
        roots[0] = replace(original, plan_day_revision=1)
    elif change == "content":
        roots[0] = replace(original, start_at=original.start_at + timedelta(hours=1))
    else:
        roots[0] = replace(original, status=PlanDayStatus.ACTIVE)
    new_cut, _ = _build_cut(bundle, plan_day_roots=tuple(roots))
    stale_cut = _observe_new_plan_day_head_on_old_cut(
        old_cut,
        new_cut,
        original.plan_day_id,
    )

    assert old_cut.plan_day_root_heads[0].content_sha256 != new_cut.plan_day_root_heads[0].content_sha256
    assert not stale_cut.currentness_vector.all_current
    assert _assessment(stale_cut).authority_outcome is AuthorityOutcome.ABSTAIN
    assert _assessment(stale_cut).reason_codes == ("DECISION_TIME_CURRENTNESS_FAILED",)


def test_pd_04_removed_plan_day_root_head_observation_fails_closed():
    cut, _ = _build_cut()
    target = next(
        item
        for item in cut.currentness_vector.observations
        if item.kind is CurrentnessKind.PLAN_DAY_PRECONDITIONS
    )
    vector = replace(
        cut.currentness_vector,
        observations=tuple(
            item
            for item in cut.currentness_vector.observations
            if item.observation_id != target.observation_id
        ),
    )

    with pytest.raises(
        OperationalAuthorityValidationError,
        match="currentness_required_subject_coverage",
    ):
        replace(cut, currentness_vector=vector)


def test_pd_05_wrong_plan_day_root_head_subject_key_fails_closed():
    cut, _ = _build_cut()
    _replace_required_currentness_subject_with_generic(
        cut,
        CurrentnessKind.PLAN_DAY_PRECONDITIONS,
        "PLAN_DAY_ROOT_HEAD",
    )


def test_pd_06_multiple_plan_day_root_heads_all_current_are_valid():
    cut, _ = _build_cut()
    observations = tuple(
        item
        for item in cut.currentness_vector.observations
        if item.kind is CurrentnessKind.PLAN_DAY_PRECONDITIONS
    )

    assert len(cut.plan_day_root_heads) == len(cut.evaluation_input.plan_day_associations) > 1
    assert len(observations) == len(cut.plan_day_root_heads)
    assert all(item.exact_match for item in observations)
    assert _assessment(cut).authority_outcome is AuthorityOutcome.ACT


def test_pd_07_one_changed_head_in_multiple_plan_days_prevents_currentness():
    bundle = _m3_bundle()
    roots = list(_plan_day_roots(bundle))
    old_cut, _ = _build_cut(bundle, plan_day_roots=tuple(roots))
    target = roots[-1]
    roots[-1] = replace(
        target,
        plan_day_revision=1,
        status=PlanDayStatus.ACTIVE,
    )
    new_cut, _ = _build_cut(bundle, plan_day_roots=tuple(roots))
    stale = _observe_new_plan_day_head_on_old_cut(old_cut, new_cut, target.plan_day_id)
    plan_day_observations = tuple(
        item
        for item in stale.currentness_vector.observations
        if item.kind is CurrentnessKind.PLAN_DAY_PRECONDITIONS
    )

    assert sum(not item.exact_match for item in plan_day_observations) == 1
    assert _assessment(stale).authority_outcome is AuthorityOutcome.ABSTAIN


def test_pd_08_plan_day_root_revision_hash_status_change_changes_cut_and_result_identity():
    bundle = _m3_bundle()
    roots = list(_plan_day_roots(bundle))
    first, _ = _build_cut(bundle, plan_day_roots=tuple(roots))
    roots[0] = replace(
        roots[0],
        plan_day_revision=1,
        status=PlanDayStatus.ACTIVE,
    )
    second, _ = _build_cut(bundle, plan_day_roots=tuple(roots))
    first_result = evaluate_operational_authority(first)
    second_result = evaluate_operational_authority(second)

    assert first.plan_day_root_heads[0].content_sha256 != second.plan_day_root_heads[0].content_sha256
    assert first.cut_id != second.cut_id
    assert first_result.result_id != second_result.result_id
    assert first_result.candidate_assessments[0].authority_outcome is AuthorityOutcome.ACT
    assert second_result.candidate_assessments[0].authority_outcome is AuthorityOutcome.ACT


def test_marker_subject_universe_uses_only_modified_and_candidate_induced_fields():
    bundle = _m3_bundle()
    candidate = bundle["m3"].candidates[0]
    subjects = derive_authority_marker_subjects(candidate, bundle["support"])
    kinds = {item.subject_kind.value for item in subjects}

    assert "COMMITMENT" in kinds
    assert "WORKER" in kinds
    assert all(item.job_id and item.task_id and item.operational_date for item in subjects)
    assert {item.commitment_id for item in subjects if item.commitment_id} == set(candidate.modified_commitment_ids)


def test_authority_cut_identity_changes_when_working_time_head_changes():
    first, _ = _build_cut()
    source_content = canonical_json(
        {
            "complete_common_work_items": [],
            "provenance_reference": "working-time-ledger-v2",
            "schema_version": "m3e1-working-time-ledger-source-v1",
            "source_capture_generation": 2,
            "source_capture_id": "working-time-capture-v2",
            "source_head_id": "working-time-head-v2",
            "source_revision": 2,
        }
    )
    entries = tuple(
        WorkingTimeCommonManifestEntry(
            candidate_position=candidate.candidate_position,
            candidate_id=candidate.candidate_id,
            candidate_fingerprint=candidate.candidate_fingerprint,
            worker_id=comparison.worker_id,
            calculation_period_start=comparison.calculation_period_start,
            calculation_period_end=comparison.calculation_period_end,
            timezone_name=comparison.timezone_name,
            common_work_item_ids=(),
            common_work_item_fingerprints=(),
            coverage_complete=True,
        )
        for candidate in first.working_time_cut.candidate_evidence
        for comparison in candidate.comparisons
    )
    additional_manifest = WorkingTimeSourceManifest(
        authority_root_id=first.authority_root.authority_root_id,
        authority_root_fingerprint=first.authority_root.authority_root_fingerprint,
        company_id=first.authority_root.company_id,
        company_plan_id=first.authority_root.company_plan_id,
        worker_registry_revision=first.authority_root.worker_registry_revision,
        worker_registry_fingerprint=first.authority_root.worker_registry_fingerprint,
        source_kind=WorkingTimeSourceKind.WORKING_TIME_LEDGER,
        source_head_id="working-time-head-v2",
        source_revision=2,
        source_fingerprint=sha256_text(source_content),
        source_capture_id="working-time-capture-v2",
        source_capture_generation=2,
        source_capture_fingerprint=_digest("working-time-capture-v2"),
        provenance_reference="working-time-ledger-v2",
        canonical_source_content_json=source_content,
        entries=entries,
        coverage_complete=True,
    )
    manifests = tuple(
        additional_manifest
        if item.source_kind is WorkingTimeSourceKind.WORKING_TIME_LEDGER
        else item
        for item in first.working_time_cut.common_work_manifests
    )
    selections = _rebind_working_time_source_selections(
        first.working_time_cut,
        manifests,
    )
    changed_working = replace(
        first.working_time_cut,
        source_selections=selections,
        common_work_manifests=manifests,
        source_head_observations=tuple(
            EvidenceBinding(
                kind="WORKING_TIME_SOURCE_MANIFEST",
                record_id=item.manifest_id,
                fingerprint=item.manifest_fingerprint,
            )
            for item in manifests
        ),
    )

    assert changed_working.cut_id != first.working_time_cut.cut_id


def test_exact_input_replay_produces_byte_identical_result():
    cut, _ = _build_cut()
    first = evaluate_operational_authority(cut)
    replay = evaluate_operational_authority(cut)

    assert first == replay
    assert first.result_id == replay.result_id
    assert serialize_operational_authority_result(first) == serialize_operational_authority_result(replay)
