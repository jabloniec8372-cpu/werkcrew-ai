from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP, localcontext

import pytest

from werkcrew_ai.field.serialization import sha256_text
from werkcrew_ai.pricing.internal_cost_support import (
    LaborCostSubjectScope,
    NoSourceReason,
    RateSelectionStatus,
    V1_INTERNAL_LABOR_RATES,
    current_internal_labor_cost_rule,
)
from werkcrew_ai.pricing.internal_labor_cost_consequence import (
    CandidateInternalLaborCostConsequence,
    InternalLaborCostLine,
    LaborCostCompleteness,
    LaborCostIssueCode,
    M3InternalLaborCostConsequenceResult,
    M3InternalLaborCostConsequenceValidationError,
    serialize_internal_labor_cost_consequence_result,
)


UTC = timezone.utc
START = datetime(2026, 9, 14, 8, tzinfo=UTC)
H = sha256_text("m3-d-unit")
STEFAN_V1_RATE = dict(V1_INTERNAL_LABOR_RATES)["stefan-mueller"]


def line(
    scope: LaborCostSubjectScope,
    *,
    commitment_id: str = "commitment-a",
    job_id: str = "job-a",
    task_id: str = "task-a",
    worker_id: str = "anna-fischer",
    rate: Decimal | None = Decimal("37.00"),
    start: datetime = START,
    end: datetime | None = None,
    position: int = 0,
    candidate_fingerprint: str = H,
) -> InternalLaborCostLine:
    end = end or start + timedelta(hours=1)
    subject_fingerprint = sha256_text(
        f"subject:{position}:{scope.value}:{commitment_id}:{worker_id}:{start}:{end}"
    )
    selection_fingerprint = sha256_text(
        f"selection:{subject_fingerprint}:{'unknown' if rate is None else rate}"
    )
    common = dict(
        candidate_position=position,
        candidate_id="m3-candidate-" + candidate_fingerprint,
        candidate_fingerprint=candidate_fingerprint,
        scope=scope,
        commitment_id=commitment_id,
        job_id=job_id,
        task_id=task_id,
        worker_id=worker_id,
        interval_start=start,
        interval_end=end,
        subject_id="m5-internal-labor-cost-subject-" + subject_fingerprint,
        subject_fingerprint=subject_fingerprint,
        selection_id="m5-internal-labor-rate-selection-" + selection_fingerprint,
        selection_fingerprint=selection_fingerprint,
    )
    if rate is None:
        return InternalLaborCostLine(
            **common,
            selection_status=RateSelectionStatus.NO_SOURCE,
            source_record_id=None,
            source_fingerprint=None,
            source_revision=None,
            source_capture_id=None,
            source_capture_fingerprint=None,
            source_capture_generation=None,
            no_source_reason=NoSourceReason.NO_RATE_SOURCE,
            rate_amount=None,
        )
    source_fingerprint = sha256_text(f"source:{worker_id}:{rate}")
    capture_fingerprint = sha256_text(f"capture:{source_fingerprint}")
    return InternalLaborCostLine(
        **common,
        selection_status=RateSelectionStatus.SELECTED,
        source_record_id="m5-internal-labor-rate-source-" + source_fingerprint,
        source_fingerprint=source_fingerprint,
        source_revision=0,
        source_capture_id="m5-internal-cost-source-capture-" + capture_fingerprint,
        source_capture_fingerprint=capture_fingerprint,
        source_capture_generation=1,
        no_source_reason=None,
        rate_amount=rate,
    )


def consequence(
    *,
    baseline_rate: Decimal | None = Decimal("37.00"),
    candidate_rate: Decimal | None = Decimal("37.00"),
    baseline_end: datetime | None = None,
    candidate_end: datetime | None = None,
    position: int = 0,
    candidate_fingerprint: str = H,
    baseline_worker: str = "anna-fischer",
    candidate_worker: str = "anna-fischer",
) -> CandidateInternalLaborCostConsequence:
    baseline = line(
        LaborCostSubjectScope.BASELINE,
        rate=baseline_rate,
        end=baseline_end,
        position=position,
        candidate_fingerprint=candidate_fingerprint,
        worker_id=baseline_worker,
    )
    proposed = line(
        LaborCostSubjectScope.CANDIDATE,
        rate=candidate_rate,
        end=candidate_end,
        position=position,
        candidate_fingerprint=candidate_fingerprint,
        worker_id=candidate_worker,
    )
    return CandidateInternalLaborCostConsequence(
        candidate_position=position,
        candidate_id="m3-candidate-" + candidate_fingerprint,
        candidate_fingerprint=candidate_fingerprint,
        modified_commitment_ids=("commitment-a",),
        baseline_lines=(baseline,),
        candidate_lines=(proposed,),
    )


def result(*consequences) -> M3InternalLaborCostConsequenceResult:
    rule = current_internal_labor_cost_rule()
    return M3InternalLaborCostConsequenceResult(
        source_evaluation_input_id="m3-evaluation-input-" + H,
        source_evaluation_input_fingerprint=H,
        source_support_snapshot_id="m3-feasibility-support-" + H,
        source_support_snapshot_fingerprint=H,
        source_m3_result_id="m3-feasibility-result-" + H,
        source_m3_result_fingerprint=H,
        source_internal_cost_support_id="m5-internal-cost-support-" + H,
        source_internal_cost_support_fingerprint=H,
        company_plan_id="company-plan",
        base_plan_revision=0,
        base_plan_revision_id="m3-plan-revision-" + H,
        base_plan_revision_fingerprint=H,
        m5_rule_id=rule.rule_id,
        m5_rule_revision=rule.rule_revision,
        m5_rule_fingerprint=rule.rule_fingerprint,
        search_envelope_exhausted=False,
        global_solution_status="NOT_EVALUATED_GLOBALLY",
        consequences=tuple(consequences),
    )


@pytest.mark.parametrize(
    ("baseline_rate", "candidate_rate", "baseline_worker", "candidate_worker", "expected_delta"),
    (
        (Decimal("37.00"), Decimal("37.00"), "anna-fischer", "anna-fischer", Decimal("0.00")),
        (Decimal("31.00"), Decimal("45.00"), "jonas-klein", "stefan-mueller", Decimal("14.00")),
        (Decimal("45.00"), Decimal("31.00"), "stefan-mueller", "jonas-klein", Decimal("-14.00")),
    ),
)
def test_signed_delta_uses_candidate_minus_exact_baseline(
    baseline_rate, candidate_rate, baseline_worker, candidate_worker, expected_delta
):
    value = consequence(
        baseline_rate=baseline_rate,
        candidate_rate=candidate_rate,
        baseline_worker=baseline_worker,
        candidate_worker=candidate_worker,
    )
    assert value.completeness is LaborCostCompleteness.COMPLETE
    assert value.internal_labor_cost_delta == expected_delta


@pytest.mark.parametrize(
    ("baseline_end", "candidate_end", "expected_delta"),
    (
        (START + timedelta(hours=1), START + timedelta(hours=2), Decimal("37.00")),
        (START + timedelta(hours=2), START + timedelta(hours=1), Decimal("-37.00")),
    ),
)
def test_same_rate_duration_change_has_signed_delta(
    baseline_end, candidate_end, expected_delta
):
    value = consequence(
        baseline_end=baseline_end, candidate_end=candidate_end
    )
    assert value.internal_labor_cost_delta == expected_delta


def test_multi_commitment_totals_exactly_modified_scope():
    baseline = (
        line(LaborCostSubjectScope.BASELINE, commitment_id="a", rate=Decimal("31.00")),
        line(
            LaborCostSubjectScope.BASELINE,
            commitment_id="b",
            job_id="job-b",
            task_id="task-b",
            rate=Decimal("34.00"),
        ),
    )
    candidate = (
        line(LaborCostSubjectScope.CANDIDATE, commitment_id="a", rate=Decimal("37.00")),
        line(
            LaborCostSubjectScope.CANDIDATE,
            commitment_id="b",
            job_id="job-b",
            task_id="task-b",
            rate=Decimal("38.00"),
        ),
    )
    value = CandidateInternalLaborCostConsequence(
        candidate_position=0,
        candidate_id="m3-candidate-" + H,
        candidate_fingerprint=H,
        modified_commitment_ids=("a", "b"),
        baseline_lines=tuple(reversed(baseline)),
        candidate_lines=tuple(reversed(candidate)),
    )
    assert value.baseline_internal_labor_cost == Decimal("65.00")
    assert value.candidate_internal_labor_cost == Decimal("75.00")
    assert value.internal_labor_cost_delta == Decimal("10.00")
    assert tuple(item.commitment_id for item in value.baseline_lines) == ("a", "b")


def test_fractional_hour_and_half_up_line_rounding_are_exact_decimal():
    fractional = line(
        LaborCostSubjectScope.CANDIDATE,
        rate=Decimal("37.00"),
        end=START + timedelta(minutes=90),
    )
    half_cent = line(
        LaborCostSubjectScope.CANDIDATE,
        worker_id="stefan-mueller",
        rate=STEFAN_V1_RATE,
        end=START + timedelta(seconds=62),
    )
    assert fractional.duration_seconds == 5400
    assert fractional.labor_amount == Decimal("55.50")
    assert STEFAN_V1_RATE == Decimal("45.00")
    assert half_cent.duration_seconds == 62
    assert half_cent.labor_amount == Decimal("0.78")


def test_two_62_second_stefan_lines_are_rounded_individually_before_totaling():
    def lines(scope):
        return (
            line(
                scope,
                commitment_id="a",
                worker_id="stefan-mueller",
                rate=STEFAN_V1_RATE,
                end=START + timedelta(seconds=62),
            ),
            line(
                scope,
                commitment_id="b",
                job_id="job-b",
                task_id="task-b",
                worker_id="stefan-mueller",
                rate=STEFAN_V1_RATE,
                end=START + timedelta(seconds=62),
            ),
        )

    value = CandidateInternalLaborCostConsequence(
        candidate_position=0,
        candidate_id="m3-candidate-" + H,
        candidate_fingerprint=H,
        modified_commitment_ids=("a", "b"),
        baseline_lines=lines(LaborCostSubjectScope.BASELINE),
        candidate_lines=lines(LaborCostSubjectScope.CANDIDATE),
    )

    assert tuple(item.labor_amount for item in value.baseline_lines) == (
        Decimal("0.78"),
        Decimal("0.78"),
    )
    assert tuple(item.labor_amount for item in value.candidate_lines) == (
        Decimal("0.78"),
        Decimal("0.78"),
    )
    assert value.baseline_internal_labor_cost == Decimal("1.56")
    assert value.candidate_internal_labor_cost == Decimal("1.56")
    assert value.internal_labor_cost_delta == Decimal("0.00")


def _stefan_62_second_result_snapshot(*, precision, rounding):
    with localcontext() as context:
        context.prec = precision
        context.rounding = rounding
        value = result(
            consequence(
                baseline_rate=STEFAN_V1_RATE,
                candidate_rate=STEFAN_V1_RATE,
                baseline_end=START + timedelta(seconds=62),
                candidate_end=START + timedelta(seconds=62),
                baseline_worker="stefan-mueller",
                candidate_worker="stefan-mueller",
            )
        )
        candidate = value.consequences[0]
        placement = candidate.candidate_lines[0]
        return (
            placement.labor_amount,
            placement.line_fingerprint,
            placement.line_id,
            candidate.baseline_internal_labor_cost,
            candidate.candidate_internal_labor_cost,
            candidate.internal_labor_cost_delta,
            candidate.consequence_fingerprint,
            candidate.consequence_id,
            value.result_fingerprint,
            value.result_id,
            serialize_internal_labor_cost_consequence_result(value),
        )


def _legacy_intermediate(*, precision, rounding):
    with localcontext() as context:
        context.prec = precision
        context.rounding = rounding
        return Decimal(62) / Decimal(3600) * STEFAN_V1_RATE


def test_line_amount_and_all_identities_ignore_ambient_decimal_rounding():
    assert _legacy_intermediate(precision=6, rounding=ROUND_DOWN) != (
        _legacy_intermediate(precision=6, rounding=ROUND_UP)
    )
    down = _stefan_62_second_result_snapshot(precision=6, rounding=ROUND_DOWN)
    up = _stefan_62_second_result_snapshot(precision=6, rounding=ROUND_UP)
    assert down == up
    assert down[0] == Decimal("0.78")


def test_line_amount_and_all_identities_ignore_ambient_decimal_precision():
    assert _legacy_intermediate(precision=2, rounding=ROUND_DOWN) != (
        _legacy_intermediate(precision=80, rounding=ROUND_DOWN)
    )
    low = _stefan_62_second_result_snapshot(precision=2, rounding=ROUND_DOWN)
    high = _stefan_62_second_result_snapshot(precision=80, rounding=ROUND_DOWN)
    assert low == high
    assert low[0] == Decimal("0.78")


@pytest.mark.parametrize("rate", [Decimal("45"), Decimal("45.0"), Decimal("45.00")])
def test_permitted_rate_scale_variants_have_one_canonical_amount_and_line_id(rate):
    canonical = line(
        LaborCostSubjectScope.CANDIDATE,
        worker_id="stefan-mueller",
        rate=STEFAN_V1_RATE,
        end=START + timedelta(seconds=62),
    )
    value = replace(canonical, rate_amount=rate)
    assert value.labor_amount == Decimal("0.78")
    assert value.line_id == canonical.line_id


@pytest.mark.parametrize("missing_scope", ["baseline", "candidate"])
def test_missing_rate_is_incomplete_without_zero_total(missing_scope):
    value = consequence(
        baseline_rate=None if missing_scope == "baseline" else Decimal("37.00"),
        candidate_rate=None if missing_scope == "candidate" else Decimal("37.00"),
    )
    assert value.completeness is LaborCostCompleteness.INCOMPLETE
    assert value.baseline_internal_labor_cost is None
    assert value.candidate_internal_labor_cost is None
    assert value.internal_labor_cost_delta is None
    assert value.issues[0].code is (
        LaborCostIssueCode.BASELINE_RATE_UNKNOWN
        if missing_scope == "baseline"
        else LaborCostIssueCode.CANDIDATE_RATE_UNKNOWN
    )


def test_reordered_consequences_have_identical_canonical_result():
    first_fingerprint = sha256_text("candidate-first")
    second_fingerprint = sha256_text("candidate-second")
    first = consequence(position=1, candidate_fingerprint=first_fingerprint)
    second = consequence(position=4, candidate_fingerprint=second_fingerprint)
    ordered = result(first, second)
    reordered = result(second, first)
    assert ordered == reordered
    assert ordered.result_id == reordered.result_id
    assert serialize_internal_labor_cost_consequence_result(ordered) == (
        serialize_internal_labor_cost_consequence_result(reordered)
    )


@pytest.mark.parametrize(
    "bad_rate",
    [37.0, Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_float_nan_and_infinity_rate_injection_is_rejected(bad_rate):
    with pytest.raises(M3InternalLaborCostConsequenceValidationError):
        line(LaborCostSubjectScope.CANDIDATE, rate=bad_rate)


@pytest.mark.parametrize("modified", [(), ("commitment-a", "extra")])
def test_omitted_or_extra_modified_commitment_is_rejected(modified):
    baseline = line(LaborCostSubjectScope.BASELINE)
    proposed = line(LaborCostSubjectScope.CANDIDATE)
    with pytest.raises(M3InternalLaborCostConsequenceValidationError):
        CandidateInternalLaborCostConsequence(
            candidate_position=0,
            candidate_id="m3-candidate-" + H,
            candidate_fingerprint=H,
            modified_commitment_ids=modified,
            baseline_lines=(baseline,),
            candidate_lines=(proposed,),
        )


def test_baseline_candidate_job_task_mismatch_is_rejected():
    baseline = line(LaborCostSubjectScope.BASELINE)
    proposed = line(LaborCostSubjectScope.CANDIDATE, task_id="foreign-task")
    with pytest.raises(M3InternalLaborCostConsequenceValidationError):
        CandidateInternalLaborCostConsequence(
            candidate_position=0,
            candidate_id="m3-candidate-" + H,
            candidate_fingerprint=H,
            modified_commitment_ids=("commitment-a",),
            baseline_lines=(baseline,),
            candidate_lines=(proposed,),
        )


@pytest.mark.parametrize("field_name", ["currency", "rule_version"])
def test_unsupported_currency_or_rule_reflective_substitution_fails_serialization(
    field_name
):
    value = result(consequence())
    object.__setattr__(value, field_name, "FORGED")
    with pytest.raises(M3InternalLaborCostConsequenceValidationError):
        serialize_internal_labor_cost_consequence_result(value)


def test_reflectively_mutated_nested_line_fails_serialization():
    value = result(consequence())
    nested = value.consequences[0].candidate_lines[0]
    object.__setattr__(nested, "labor_amount", Decimal("0.00"))
    with pytest.raises(M3InternalLaborCostConsequenceValidationError):
        serialize_internal_labor_cost_consequence_result(value)


def test_self_consistent_foreign_m5_rule_is_rejected_by_result_model():
    value = result(consequence())
    foreign = sha256_text("foreign-m5-rule")
    with pytest.raises(M3InternalLaborCostConsequenceValidationError):
        replace(
            value,
            m5_rule_id="m5-internal-cost-rule-" + foreign,
            m5_rule_fingerprint=foreign,
        )


def test_reflectively_changed_feasible_verdict_fails_serialization():
    value = result(consequence())
    object.__setattr__(value.consequences[0], "technical_verdict", "REJECTED")
    with pytest.raises(M3InternalLaborCostConsequenceValidationError):
        serialize_internal_labor_cost_consequence_result(value)


def test_result_contains_no_winner_ranking_authority_or_apply_surface():
    value = result(consequence())
    forbidden = {
        "winner",
        "best_candidate",
        "recommended_candidate",
        "ranking_score",
        "authority_decision",
        "apply",
        "customer_price",
    }
    assert forbidden.isdisjoint(value.__dataclass_fields__)
