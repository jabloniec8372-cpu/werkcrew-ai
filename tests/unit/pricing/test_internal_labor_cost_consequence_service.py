from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_UP, localcontext

import pytest

from tests.unit.planning.test_bounded_feasibility import (
    ANNA,
    STEFAN,
    _evaluation,
    _support,
)
from werkcrew_ai.field.models import WorkerIdentityRegistry
from werkcrew_ai.field.serialization import serialize_worker_registry, sha256_text
from werkcrew_ai.planning.bounded_feasibility import (
    CandidateVerdict,
    ReasonCode,
    SearchOutcome,
    SearchTraceEntry,
    generate_bounded_repair_candidates,
)
from werkcrew_ai.pricing.internal_cost_repository import derive_internal_cost_subjects
from werkcrew_ai.pricing.internal_cost_support import (
    V1_INTERNAL_LABOR_RATES,
    InternalCostCandidateBinding,
    InternalCostSourceCapture,
    InternalCostSupportCut,
    InternalLaborRateSelection,
    LaborCostSubjectScope,
    NoSourceReason,
    RateSelectionStatus,
    WorkerInternalCostRateRevision,
    current_internal_labor_cost_rule,
)
from werkcrew_ai.pricing.internal_labor_cost_consequence import (
    LaborCostCompleteness,
    M3InternalLaborCostConsequenceBindingError,
    M3InternalLaborCostConsequenceService,
    serialize_internal_labor_cost_consequence_result,
)


class EvidenceReader:
    def __init__(self, evidence):
        self.evidence = evidence

    def get_internal_cost_consequence_evidence(self, support_id):
        return self.evidence


def evidence(*, workers=(ANNA, STEFAN), sku_by_letter=None):
    evaluation = _evaluation()
    support = _support(
        evaluation,
        workers=workers,
        sku_by_letter=sku_by_letter or {"A": "waterproof.bath"},
    )
    result = generate_bounded_repair_candidates(evaluation, support)
    return build_evidence(evaluation, support, result)


def build_evidence(evaluation, support, result, *, no_source_subject_ids=()):
    subjects = derive_internal_cost_subjects(result, support)
    rate_by_worker = dict(V1_INTERNAL_LABOR_RATES)
    workers = tuple(sorted({item.worker_id for item in subjects}))
    assert set(workers) <= set(rate_by_worker)
    registry = WorkerIdentityRegistry(worker_ids=tuple(sorted(rate_by_worker)), registry_revision=1)
    registry_json = serialize_worker_registry(registry)
    registry_fingerprint = sha256_text(registry_json)
    rule = current_internal_labor_cost_rule()
    sources = tuple(
        WorkerInternalCostRateRevision(
            worker_id=worker_id,
            rate_amount=rate_by_worker[worker_id],
            source_revision=0,
            previous_source_record_id=None,
            effective_from=None,
            effective_until=None,
            worker_registry_revision=registry.registry_revision,
            worker_registry_fingerprint=registry_fingerprint,
            canonical_worker_registry_json=registry_json,
            rule_id=rule.rule_id,
            rule_fingerprint=rule.rule_fingerprint,
        )
        for worker_id in workers
    )
    source_by_worker = {item.worker_id: item for item in sources}
    capture_by_worker = {
        source.worker_id: InternalCostSourceCapture(
            ledger_generation=position + 1,
            source_record_id=source.source_record_id,
            source_fingerprint=source.source_fingerprint,
            worker_id=source.worker_id,
            source_revision=source.source_revision,
        )
        for position, source in enumerate(sources)
    }
    no_source_subject_ids = set(no_source_subject_ids)
    selections = []
    for subject in subjects:
        if subject.subject_id in no_source_subject_ids:
            selections.append(
                InternalLaborRateSelection(
                    subject_id=subject.subject_id,
                    subject_fingerprint=subject.subject_fingerprint,
                    status=RateSelectionStatus.NO_SOURCE,
                    source_record_id=None,
                    source_fingerprint=None,
                    source_revision=None,
                    source_capture_id=None,
                    source_capture_fingerprint=None,
                    source_capture_generation=None,
                    no_source_reason=NoSourceReason.NO_RATE_SOURCE,
                )
            )
            continue
        source = source_by_worker[subject.worker_id]
        capture = capture_by_worker[subject.worker_id]
        selections.append(
            InternalLaborRateSelection(
                subject_id=subject.subject_id,
                subject_fingerprint=subject.subject_fingerprint,
                status=RateSelectionStatus.SELECTED,
                source_record_id=source.source_record_id,
                source_fingerprint=source.source_fingerprint,
                source_revision=source.source_revision,
                source_capture_id=capture.capture_id,
                source_capture_fingerprint=capture.capture_fingerprint,
                source_capture_generation=capture.ledger_generation,
                no_source_reason=None,
            )
        )
    cut = InternalCostSupportCut(
        evaluation_input_id=evaluation.evaluation_input_id,
        evaluation_input_fingerprint=evaluation.evaluation_input_fingerprint,
        feasibility_support_snapshot_id=support.support_snapshot_id,
        feasibility_support_snapshot_fingerprint=support.support_fingerprint,
        company_plan_id=evaluation.company_plan_id,
        base_plan_revision=evaluation.base_plan_revision,
        base_plan_revision_id=evaluation.base_plan_revision_id,
        base_plan_revision_fingerprint=evaluation.base_plan_revision_fingerprint,
        m3_result_id=result.result_id,
        m3_result_fingerprint=result.result_fingerprint,
        candidate_bindings=tuple(
            InternalCostCandidateBinding(
                position=position,
                candidate_id=candidate.candidate_id,
                candidate_fingerprint=candidate.candidate_fingerprint,
            )
            for position, candidate in enumerate(result.candidates)
        ),
        rule_id=rule.rule_id,
        rule_revision=rule.rule_revision,
        rule_fingerprint=rule.rule_fingerprint,
        source_cut_generation=len(sources),
        subjects=subjects,
        selections=tuple(selections),
    )
    selected_source_ids = {
        item.source_record_id
        for item in cut.selections
        if item.status is RateSelectionStatus.SELECTED
    }
    selected_sources = tuple(
        item for item in sources if item.source_record_id in selected_source_ids
    )
    return evaluation, support, result, cut, selected_sources


def calculate(bundle, requested_support_id=None):
    requested_support_id = requested_support_id or bundle[3].support_id
    return M3InternalLaborCostConsequenceService(EvidenceReader(bundle)).calculate(
        requested_support_id
    )


def test_service_output_and_identities_ignore_ambient_decimal_context():
    bundle = evidence()

    def snapshot(precision, rounding):
        with localcontext() as context:
            context.prec = precision
            context.rounding = rounding
            value = calculate(bundle)
            return (
                tuple(
                    (
                        line.labor_amount,
                        line.line_fingerprint,
                        line.line_id,
                    )
                    for consequence in value.consequences
                    for line in consequence.baseline_lines + consequence.candidate_lines
                ),
                tuple(
                    (
                        consequence.baseline_internal_labor_cost,
                        consequence.candidate_internal_labor_cost,
                        consequence.internal_labor_cost_delta,
                        consequence.consequence_fingerprint,
                        consequence.consequence_id,
                    )
                    for consequence in value.consequences
                ),
                value.result_fingerprint,
                value.result_id,
                serialize_internal_labor_cost_consequence_result(value),
            )

    assert snapshot(2, ROUND_DOWN) == snapshot(80, ROUND_UP)


def test_service_prices_only_feasible_candidates_in_m3_order():
    bundle = evidence(
        workers=(ANNA, STEFAN), sku_by_letter={"A": "waterproof.bath"}
    )
    value = calculate(bundle)
    expected = tuple(
        candidate.candidate_id
        for candidate in bundle[2].candidates
        if candidate.verdict is CandidateVerdict.FEASIBLE
    )
    assert expected
    assert tuple(item.candidate_id for item in value.consequences) == expected
    assert all(
        bundle[2].candidates[item.candidate_position].verdict
        is CandidateVerdict.FEASIBLE
        for item in value.consequences
    )


def test_service_uses_exact_peter_baseline_and_anna_candidate_rates():
    value = calculate(evidence())
    consequence = value.consequences[0]
    assert consequence.baseline_lines[0].worker_id == "peter-berger"
    assert consequence.baseline_lines[0].rate_amount == Decimal("38.00")
    assert consequence.candidate_lines[0].worker_id == "anna-fischer"
    assert consequence.candidate_lines[0].rate_amount == Decimal("37.00")
    assert consequence.baseline_internal_labor_cost == Decimal("38.00")
    assert consequence.candidate_internal_labor_cost == Decimal("37.00")
    assert consequence.internal_labor_cost_delta == Decimal("-1.00")


def test_service_recomputation_is_byte_identical_and_contains_no_public_or_gen1_truth():
    bundle = evidence()
    first = calculate(bundle)
    replay = calculate(bundle)
    raw = serialize_internal_labor_cost_consequence_result(first)
    assert first == replay
    assert first.result_id == replay.result_id
    assert raw == serialize_internal_labor_cost_consequence_result(replay)
    for forbidden in (
        "emp-anna-demo",
        "DEMO_workforce",
        "DEMO_pricing",
        "customer_price",
        "tile.wall",
        "waterproof.bath",
    ):
        assert forbidden not in raw


@pytest.mark.parametrize("scope", [LaborCostSubjectScope.BASELINE, LaborCostSubjectScope.CANDIDATE])
def test_missing_exact_subject_rate_is_incomplete_never_zero(scope):
    initial = evidence()
    subject = next(item for item in initial[3].subjects if item.scope is scope)
    bundle = build_evidence(initial[0], initial[1], initial[2], no_source_subject_ids=(subject.subject_id,))
    value = calculate(bundle)
    affected = next(
        item
        for item in value.consequences
        if item.candidate_position == subject.candidate_position
    )
    assert affected.completeness is LaborCostCompleteness.INCOMPLETE
    assert affected.baseline_internal_labor_cost is None
    assert affected.candidate_internal_labor_cost is None
    assert affected.internal_labor_cost_delta is None


@pytest.mark.parametrize("attack", ["evaluation", "support", "revision", "result", "cost_support"])
def test_foreign_source_bindings_are_rejected(attack):
    bundle = list(evidence())
    foreign = sha256_text("foreign-" + attack)
    if attack == "evaluation":
        bundle[0] = replace(bundle[0], current_planning_scope_fingerprint=foreign)
    elif attack == "support":
        bundle[1] = replace(
            bundle[1],
            source_cut_fingerprint=foreign,
            source_cut_id="m3-feasibility-source-cut-" + foreign,
        )
    elif attack == "revision":
        bundle[0] = replace(
            bundle[0],
            base_plan_revision_fingerprint=foreign,
            base_plan_revision_id="m3-plan-revision-" + foreign,
        )
    elif attack == "result":
        bundle[2] = replace(
            bundle[2],
            search_trace=bundle[2].search_trace
            + (SearchTraceEntry(reason_code=ReasonCode.NO_OVERLAP, detail="foreign"),),
        )
    else:
        bundle[3] = replace(
            bundle[3], source_cut_generation=bundle[3].source_cut_generation + 1
        )
    with pytest.raises(M3InternalLaborCostConsequenceBindingError):
        calculate(tuple(bundle), requested_support_id=evidence()[3].support_id)


def test_candidate_fingerprint_reflective_substitution_is_rejected():
    bundle = list(evidence())
    candidate = bundle[2].candidates[0]
    object.__setattr__(candidate, "candidate_fingerprint", sha256_text("foreign-candidate"))
    with pytest.raises(M3InternalLaborCostConsequenceBindingError):
        calculate(tuple(bundle))


@pytest.mark.parametrize("scope", [LaborCostSubjectScope.BASELINE, LaborCostSubjectScope.CANDIDATE])
def test_wrong_subject_interval_is_rejected(scope):
    bundle = list(evidence())
    cut = bundle[3]
    original = next(item for item in cut.subjects if item.scope is scope)
    forged_subject = replace(
        original,
        interval_start=original.interval_start + timedelta(minutes=1),
        interval_end=original.interval_end + timedelta(minutes=1),
    )
    original_selection = next(
        item for item in cut.selections if item.subject_id == original.subject_id
    )
    forged_selection = replace(
        original_selection,
        subject_id=forged_subject.subject_id,
        subject_fingerprint=forged_subject.subject_fingerprint,
    )
    bundle[3] = replace(
        cut,
        subjects=tuple(
            forged_subject if item.subject_id == original.subject_id else item
            for item in cut.subjects
        ),
        selections=tuple(
            forged_selection if item.subject_id == original.subject_id else item
            for item in cut.selections
        ),
    )
    with pytest.raises(
        M3InternalLaborCostConsequenceBindingError,
        match="COST_SUBJECT_UNIVERSE_MISMATCH",
    ):
        calculate(tuple(bundle), requested_support_id=bundle[3].support_id)


def test_another_workers_rate_source_cannot_satisfy_subject():
    bundle = list(evidence())
    source = bundle[4][0]
    other_worker = next(worker for worker, _ in V1_INTERNAL_LABOR_RATES if worker != source.worker_id)
    bundle[4] = (replace(source, worker_id=other_worker),) + bundle[4][1:]
    with pytest.raises(M3InternalLaborCostConsequenceBindingError):
        calculate(tuple(bundle))


def test_feasible_candidates_remain_costable_when_search_envelope_is_exhausted():
    bundle = list(evidence())
    result = replace(
        bundle[2],
        outcome=SearchOutcome.FEASIBLE_CANDIDATES_FOUND,
        search_envelope_exhausted=True,
        search_trace=bundle[2].search_trace
        + (
            SearchTraceEntry(
                reason_code=ReasonCode.SEARCH_ENVELOPE_LIMIT,
                detail="bounded test truncation",
            ),
        ),
    )
    rebuilt = build_evidence(bundle[0], bundle[1], result)
    value = calculate(rebuilt)
    assert value.search_envelope_exhausted is True
    assert value.optimality_scope == "BOUNDED_M3_C_RESULT_ONLY"
    assert value.consequences
