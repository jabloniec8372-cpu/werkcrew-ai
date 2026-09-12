"""Pure M3-D internal scheduled-labor cost consequence calculation.

The public service accepts only an immutable M5-A support-cut identity.  Its
repository resolves the exact historical M3 and monetary evidence; this module
never accepts caller-owned rates, placements, candidate lists, totals, or money
rules and performs no ranking, authority decision, or apply behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from werkcrew_ai.field.serialization import canonical_json, sha256_text
from werkcrew_ai.planning.bounded_feasibility import (
    CandidateVerdict,
    M3BoundedFeasibilityResult,
)
from werkcrew_ai.planning.evaluation_input import M3EvaluationInput
from werkcrew_ai.planning.feasibility_support import FeasibilitySupportSnapshot
from werkcrew_ai.pricing.internal_cost_repository import derive_internal_cost_subjects
from werkcrew_ai.pricing.internal_cost_support import (
    ACCESS_CLASSIFICATION,
    AUTOMATIC_FX,
    COMPONENT_SCOPE,
    CONFIGURATION_ID,
    CURRENCY,
    FINAL_MONEY_SCALE,
    RATE_SEMANTICS,
    ROUNDING_MODE,
    RULE_VERSION as M5_RULE_VERSION,
    SOURCE_CLASSIFICATION,
    InternalCostCandidateBinding,
    InternalCostSupportCut,
    InternalLaborCostSubject,
    InternalLaborRateSelection,
    LaborCostSubjectScope,
    NoSourceReason,
    RateSelectionStatus,
    WorkerInternalCostRateRevision,
    current_internal_labor_cost_rule,
)


CONSEQUENCE_RULE_VERSION = "m3-internal-scheduled-labor-cost-consequence-v1"
LINE_SCHEMA_VERSION = "m3-internal-labor-cost-line-v1"
CANDIDATE_CONSEQUENCE_SCHEMA_VERSION = (
    "m3-internal-labor-cost-candidate-consequence-v1"
)
RESULT_SCHEMA_VERSION = "m3-internal-labor-cost-consequence-result-v1"
ROUNDING_POINT = "EACH_PLACEMENT_LINE_THEN_SUM"
DELTA_FORMULA = "CANDIDATE_TOTAL_MINUS_BASELINE_TOTAL"
OPTIMALITY_SCOPE = "BOUNDED_M3_C_RESULT_ONLY"


class M3InternalLaborCostConsequenceError(ValueError):
    def __init__(self, code: str, field_name: str) -> None:
        self.code, self.field_name = code, field_name
        super().__init__(f"{code}: {field_name}")


class M3InternalLaborCostConsequenceValidationError(
    M3InternalLaborCostConsequenceError
):
    pass


class M3InternalLaborCostConsequenceBindingError(
    M3InternalLaborCostConsequenceError
):
    pass


class LaborCostCompleteness(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class LaborCostIssueCode(StrEnum):
    BASELINE_RATE_UNKNOWN = "BASELINE_RATE_UNKNOWN"
    CANDIDATE_RATE_UNKNOWN = "CANDIDATE_RATE_UNKNOWN"


def _require(condition: bool, field_name: str) -> None:
    if not condition:
        raise M3InternalLaborCostConsequenceValidationError(
            "INVALID_VALUE", field_name
        )


def _bind(condition: bool, code: str, field_name: str) -> None:
    if not condition:
        raise M3InternalLaborCostConsequenceBindingError(code, field_name)


def _identity(value: str, field_name: str) -> None:
    _require(type(value) is str and bool(value) and value == value.strip(), field_name)
    _require(
        not any(
            ord(character) < 32
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        ),
        field_name,
    )


def _digest(value: str, field_name: str) -> None:
    _require(
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value),
        field_name,
    )


def _revision(value: int, field_name: str) -> None:
    _require(
        type(value) is int and 0 <= value <= 9223372036854775807,
        field_name,
    )


def _instant(value: datetime, field_name: str) -> datetime:
    _require(
        type(value) is datetime
        and value.tzinfo is not None
        and value.utcoffset() is not None,
        field_name,
    )
    normalized = value.astimezone(timezone.utc)
    _require(normalized.microsecond == 0, field_name)
    return normalized


def _duration_seconds(start: datetime, end: datetime) -> int:
    duration = end - start
    _require(duration.microseconds == 0 and duration.days >= 0, "duration_seconds")
    seconds = duration.days * 86400 + duration.seconds
    _require(seconds > 0, "duration_seconds")
    return seconds


def _exact_cents(value: Decimal, field_name: str) -> int:
    """Return exact integer cents without consulting the Decimal context."""

    _require(type(value) is Decimal and value.is_finite(), field_name)
    numerator, denominator = value.as_integer_ratio()
    cents, remainder = divmod(numerator * 100, denominator)
    _require(remainder == 0, field_name)
    return cents


def _decimal_from_cents(value: int) -> Decimal:
    """Construct canonical two-decimal money without Decimal arithmetic."""

    sign = int(value < 0)
    digits = Decimal(abs(value)).as_tuple().digits
    return Decimal((sign, digits, -2))


def _placement_amount(duration_seconds: int, rate_amount: Decimal) -> Decimal:
    """Round exact ``seconds * hourly rate / 3600`` to one monetary line.

    The authoritative hourly rate is represented as exact integer cents before
    the division.  The quotient/remainder comparison is therefore the exact
    non-negative ROUND_HALF_UP decision at the 0.01 EUR boundary and cannot be
    affected by ambient Decimal precision or rounding.
    """

    _require(type(duration_seconds) is int and duration_seconds > 0, "duration_seconds")
    rate_cents = _exact_cents(rate_amount, "rate_amount")
    _require(rate_cents >= 0, "rate_amount")
    amount_cents, remainder = divmod(duration_seconds * rate_cents, 3600)
    if remainder * 2 >= 3600:
        amount_cents += 1
    return _decimal_from_cents(amount_cents)


def _primitive(value):
    if isinstance(value, StrEnum):
        return value.value
    if type(value) is Decimal:
        return format(value, ".2f")
    if type(value) is datetime:
        return value.isoformat()
    if type(value) is tuple:
        return [_primitive(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {
            item.name: _primitive(getattr(value, item.name)) for item in fields(value)
        }
    return value


def _semantic_json(value, omitted: tuple[str, ...]) -> str:
    return canonical_json(
        {
            item.name: _primitive(getattr(value, item.name))
            for item in fields(value)
            if item.name not in omitted
        }
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class InternalLaborCostIssue:
    code: LaborCostIssueCode
    scope: LaborCostSubjectScope
    commitment_id: str
    subject_id: str
    no_source_reason: NoSourceReason

    def __post_init__(self) -> None:
        _require(type(self.code) is LaborCostIssueCode, "issue_code")
        _require(type(self.scope) is LaborCostSubjectScope, "issue_scope")
        _identity(self.commitment_id, "commitment_id")
        _identity(self.subject_id, "subject_id")
        _require(type(self.no_source_reason) is NoSourceReason, "no_source_reason")
        _require(
            self.code
            is (
                LaborCostIssueCode.BASELINE_RATE_UNKNOWN
                if self.scope is LaborCostSubjectScope.BASELINE
                else LaborCostIssueCode.CANDIDATE_RATE_UNKNOWN
            ),
            "issue_code",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class InternalLaborCostLine:
    candidate_position: int
    candidate_id: str
    candidate_fingerprint: str
    scope: LaborCostSubjectScope
    commitment_id: str
    job_id: str
    task_id: str
    worker_id: str
    interval_start: datetime
    interval_end: datetime
    subject_id: str
    subject_fingerprint: str
    selection_id: str
    selection_fingerprint: str
    selection_status: RateSelectionStatus
    source_record_id: str | None
    source_fingerprint: str | None
    source_revision: int | None
    source_capture_id: str | None
    source_capture_fingerprint: str | None
    source_capture_generation: int | None
    no_source_reason: NoSourceReason | None
    rate_amount: Decimal | None
    currency: str = field(default=CURRENCY, init=False)
    rate_semantics: str = field(default=RATE_SEMANTICS, init=False)
    schema_version: str = field(default=LINE_SCHEMA_VERSION, init=False)
    duration_seconds: int = field(init=False)
    labor_amount: Decimal | None = field(init=False)
    line_fingerprint: str = field(init=False)
    line_id: str = field(init=False)

    def __post_init__(self) -> None:
        _revision(self.candidate_position, "candidate_position")
        for name in (
            "candidate_id",
            "commitment_id",
            "job_id",
            "task_id",
            "worker_id",
            "subject_id",
            "selection_id",
        ):
            _identity(getattr(self, name), name)
        for name in (
            "candidate_fingerprint",
            "subject_fingerprint",
            "selection_fingerprint",
        ):
            _digest(getattr(self, name), name)
        _require(
            self.candidate_id == "m3-candidate-" + self.candidate_fingerprint,
            "candidate_id",
        )
        _require(
            self.subject_id
            == "m5-internal-labor-cost-subject-" + self.subject_fingerprint,
            "subject_id",
        )
        _require(
            self.selection_id
            == "m5-internal-labor-rate-selection-" + self.selection_fingerprint,
            "selection_id",
        )
        _require(type(self.scope) is LaborCostSubjectScope, "scope")
        _require(type(self.selection_status) is RateSelectionStatus, "selection_status")
        start = _instant(self.interval_start, "interval_start")
        end = _instant(self.interval_end, "interval_end")
        seconds = _duration_seconds(start, end)
        object.__setattr__(self, "interval_start", start)
        object.__setattr__(self, "interval_end", end)
        object.__setattr__(self, "duration_seconds", seconds)

        source_values = (
            self.source_record_id,
            self.source_fingerprint,
            self.source_revision,
            self.source_capture_id,
            self.source_capture_fingerprint,
            self.source_capture_generation,
        )
        if self.selection_status is RateSelectionStatus.SELECTED:
            _require(all(item is not None for item in source_values), "rate_source")
            _require(self.no_source_reason is None, "no_source_reason")
            _identity(self.source_record_id, "source_record_id")
            _digest(self.source_fingerprint, "source_fingerprint")
            _revision(self.source_revision, "source_revision")
            _identity(self.source_capture_id, "source_capture_id")
            _digest(self.source_capture_fingerprint, "source_capture_fingerprint")
            _revision(self.source_capture_generation, "source_capture_generation")
            _require(self.source_capture_generation > 0, "source_capture_generation")
            _require(
                self.source_record_id
                == "m5-internal-labor-rate-source-" + self.source_fingerprint,
                "source_record_id",
            )
            _require(
                self.source_capture_id
                == "m5-internal-cost-source-capture-"
                + self.source_capture_fingerprint,
                "source_capture_id",
            )
            _require(
                type(self.rate_amount) is Decimal
                and self.rate_amount.is_finite()
                and _exact_cents(self.rate_amount, "rate_amount") >= 0,
                "rate_amount",
            )
            amount = _placement_amount(seconds, self.rate_amount)
        else:
            _require(all(item is None for item in source_values), "rate_source")
            _require(type(self.no_source_reason) is NoSourceReason, "no_source_reason")
            _require(self.rate_amount is None, "rate_amount")
            amount = None
        object.__setattr__(self, "labor_amount", amount)
        digest = sha256_text(internal_labor_cost_line_semantic_json(self))
        object.__setattr__(self, "line_fingerprint", digest)
        object.__setattr__(self, "line_id", "m3-internal-labor-cost-line-" + digest)


def internal_labor_cost_line_semantic_json(value: InternalLaborCostLine) -> str:
    return _semantic_json(value, ("line_fingerprint", "line_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateInternalLaborCostConsequence:
    candidate_position: int
    candidate_id: str
    candidate_fingerprint: str
    modified_commitment_ids: tuple[str, ...]
    baseline_lines: tuple[InternalLaborCostLine, ...]
    candidate_lines: tuple[InternalLaborCostLine, ...]
    technical_verdict: CandidateVerdict = field(
        default=CandidateVerdict.FEASIBLE, init=False
    )
    schema_version: str = field(
        default=CANDIDATE_CONSEQUENCE_SCHEMA_VERSION, init=False
    )
    completeness: LaborCostCompleteness = field(init=False)
    issues: tuple[InternalLaborCostIssue, ...] = field(init=False)
    baseline_internal_labor_cost: Decimal | None = field(init=False)
    candidate_internal_labor_cost: Decimal | None = field(init=False)
    internal_labor_cost_delta: Decimal | None = field(init=False)
    consequence_fingerprint: str = field(init=False)
    consequence_id: str = field(init=False)

    def __post_init__(self) -> None:
        _revision(self.candidate_position, "candidate_position")
        _identity(self.candidate_id, "candidate_id")
        _digest(self.candidate_fingerprint, "candidate_fingerprint")
        _require(
            self.candidate_id == "m3-candidate-" + self.candidate_fingerprint,
            "candidate_id",
        )
        _require(
            self.technical_verdict is CandidateVerdict.FEASIBLE,
            "technical_verdict",
        )
        baseline = tuple(self.baseline_lines)
        candidate = tuple(self.candidate_lines)
        _require(
            all(
                type(item) is InternalLaborCostLine and replace(item) == item
                for item in baseline + candidate
            ),
            "labor_cost_lines",
        )
        baseline = tuple(
            sorted(baseline, key=lambda item: (item.job_id, item.task_id, item.commitment_id))
        )
        candidate = tuple(
            sorted(candidate, key=lambda item: (item.job_id, item.task_id, item.commitment_id))
        )
        _require(bool(baseline) and len(baseline) == len(candidate), "labor_cost_lines")
        _require(
            all(item.scope is LaborCostSubjectScope.BASELINE for item in baseline)
            and all(item.scope is LaborCostSubjectScope.CANDIDATE for item in candidate),
            "labor_cost_line_scope",
        )
        _require(
            all(
                (item.candidate_position, item.candidate_id, item.candidate_fingerprint)
                == (self.candidate_position, self.candidate_id, self.candidate_fingerprint)
                for item in baseline + candidate
            ),
            "line_candidate_binding",
        )
        baseline_ids = tuple(item.commitment_id for item in baseline)
        candidate_ids = tuple(item.commitment_id for item in candidate)
        _require(
            tuple(self.modified_commitment_ids) == baseline_ids == candidate_ids,
            "modified_commitment_ids",
        )
        _require(len(baseline_ids) == len(set(baseline_ids)), "modified_commitment_ids")
        _require(
            all(
                (base.job_id, base.task_id, base.commitment_id)
                == (proposed.job_id, proposed.task_id, proposed.commitment_id)
                for base, proposed in zip(baseline, candidate, strict=True)
            ),
            "baseline_candidate_task_binding",
        )
        unknown_lines = tuple(
            item
            for item in baseline + candidate
            if item.selection_status is RateSelectionStatus.NO_SOURCE
        )
        issues = tuple(
            InternalLaborCostIssue(
                code=(
                    LaborCostIssueCode.BASELINE_RATE_UNKNOWN
                    if item.scope is LaborCostSubjectScope.BASELINE
                    else LaborCostIssueCode.CANDIDATE_RATE_UNKNOWN
                ),
                scope=item.scope,
                commitment_id=item.commitment_id,
                subject_id=item.subject_id,
                no_source_reason=item.no_source_reason,
            )
            for item in unknown_lines
        )
        if unknown_lines:
            completeness = LaborCostCompleteness.INCOMPLETE
            baseline_total = candidate_total = delta = None
        else:
            completeness = LaborCostCompleteness.COMPLETE
            baseline_cents = sum(
                _exact_cents(item.labor_amount, "baseline_internal_labor_cost")
                for item in baseline
            )
            candidate_cents = sum(
                _exact_cents(item.labor_amount, "candidate_internal_labor_cost")
                for item in candidate
            )
            baseline_total = _decimal_from_cents(baseline_cents)
            candidate_total = _decimal_from_cents(candidate_cents)
            delta = _decimal_from_cents(candidate_cents - baseline_cents)
        object.__setattr__(self, "baseline_lines", baseline)
        object.__setattr__(self, "candidate_lines", candidate)
        object.__setattr__(self, "completeness", completeness)
        object.__setattr__(self, "issues", issues)
        object.__setattr__(self, "baseline_internal_labor_cost", baseline_total)
        object.__setattr__(self, "candidate_internal_labor_cost", candidate_total)
        object.__setattr__(self, "internal_labor_cost_delta", delta)
        digest = sha256_text(candidate_internal_labor_cost_semantic_json(self))
        object.__setattr__(self, "consequence_fingerprint", digest)
        object.__setattr__(self, "consequence_id", "m3-internal-labor-cost-consequence-" + digest)


def candidate_internal_labor_cost_semantic_json(
    value: CandidateInternalLaborCostConsequence,
) -> str:
    return _semantic_json(value, ("consequence_fingerprint", "consequence_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class M3InternalLaborCostConsequenceResult:
    source_evaluation_input_id: str
    source_evaluation_input_fingerprint: str
    source_support_snapshot_id: str
    source_support_snapshot_fingerprint: str
    source_m3_result_id: str
    source_m3_result_fingerprint: str
    source_internal_cost_support_id: str
    source_internal_cost_support_fingerprint: str
    company_plan_id: str
    base_plan_revision: int
    base_plan_revision_id: str
    base_plan_revision_fingerprint: str
    m5_rule_id: str
    m5_rule_revision: int
    m5_rule_fingerprint: str
    search_envelope_exhausted: bool
    global_solution_status: str
    consequences: tuple[CandidateInternalLaborCostConsequence, ...]
    configuration_id: str = field(default=CONFIGURATION_ID, init=False)
    source_classification: str = field(default=SOURCE_CLASSIFICATION, init=False)
    access_classification: str = field(default=ACCESS_CLASSIFICATION, init=False)
    rate_semantics: str = field(default=RATE_SEMANTICS, init=False)
    currency: str = field(default=CURRENCY, init=False)
    m5_rule_version: str = field(default=M5_RULE_VERSION, init=False)
    m5_component_scope: str = field(default=COMPONENT_SCOPE, init=False)
    automatic_fx: str = field(default=AUTOMATIC_FX, init=False)
    rounding: str = field(default=ROUNDING_MODE, init=False)
    final_money_scale: Decimal = field(default=FINAL_MONEY_SCALE, init=False)
    rounding_point: str = field(default=ROUNDING_POINT, init=False)
    delta_formula: str = field(default=DELTA_FORMULA, init=False)
    optimality_scope: str = field(default=OPTIMALITY_SCOPE, init=False)
    schema_version: str = field(default=RESULT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=CONSEQUENCE_RULE_VERSION, init=False)
    result_fingerprint: str = field(init=False)
    result_id: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "source_evaluation_input_id",
            "source_support_snapshot_id",
            "source_m3_result_id",
            "source_internal_cost_support_id",
            "company_plan_id",
            "base_plan_revision_id",
            "m5_rule_id",
        ):
            _identity(getattr(self, name), name)
        for name in (
            "source_evaluation_input_fingerprint",
            "source_support_snapshot_fingerprint",
            "source_m3_result_fingerprint",
            "source_internal_cost_support_fingerprint",
            "base_plan_revision_fingerprint",
            "m5_rule_fingerprint",
        ):
            _digest(getattr(self, name), name)
        _require(
            self.source_evaluation_input_id
            == "m3-evaluation-input-" + self.source_evaluation_input_fingerprint,
            "source_evaluation_input_id",
        )
        _require(
            self.source_support_snapshot_id
            == "m3-feasibility-support-" + self.source_support_snapshot_fingerprint,
            "source_support_snapshot_id",
        )
        _require(
            self.source_m3_result_id
            == "m3-feasibility-result-" + self.source_m3_result_fingerprint,
            "source_m3_result_id",
        )
        _require(
            self.source_internal_cost_support_id
            == "m5-internal-cost-support-"
            + self.source_internal_cost_support_fingerprint,
            "source_internal_cost_support_id",
        )
        _require(
            self.base_plan_revision_id
            == "m3-plan-revision-" + self.base_plan_revision_fingerprint,
            "base_plan_revision_id",
        )
        _require(
            self.m5_rule_id == "m5-internal-cost-rule-" + self.m5_rule_fingerprint,
            "m5_rule_id",
        )
        authoritative_rule = current_internal_labor_cost_rule()
        _require(
            (
                self.m5_rule_id,
                self.m5_rule_revision,
                self.m5_rule_fingerprint,
            )
            == (
                authoritative_rule.rule_id,
                authoritative_rule.rule_revision,
                authoritative_rule.rule_fingerprint,
            ),
            "m5_rule",
        )
        _revision(self.base_plan_revision, "base_plan_revision")
        _revision(self.m5_rule_revision, "m5_rule_revision")
        _require(type(self.search_envelope_exhausted) is bool, "search_envelope_exhausted")
        _require(
            self.global_solution_status == "NOT_EVALUATED_GLOBALLY",
            "global_solution_status",
        )
        consequences = tuple(self.consequences)
        _require(
            all(
                type(item) is CandidateInternalLaborCostConsequence
                and replace(item) == item
                for item in consequences
            ),
            "consequences",
        )
        consequences = tuple(sorted(consequences, key=lambda item: item.candidate_position))
        _require(
            len({item.candidate_position for item in consequences}) == len(consequences)
            and len({item.candidate_id for item in consequences}) == len(consequences),
            "consequences",
        )
        object.__setattr__(self, "consequences", consequences)
        digest = sha256_text(internal_labor_cost_consequence_result_semantic_json(self))
        object.__setattr__(self, "result_fingerprint", digest)
        object.__setattr__(self, "result_id", "m3-internal-labor-cost-result-" + digest)


def internal_labor_cost_consequence_result_semantic_json(
    value: M3InternalLaborCostConsequenceResult,
) -> str:
    return _semantic_json(value, ("result_fingerprint", "result_id"))


def serialize_internal_labor_cost_consequence_result(
    value: M3InternalLaborCostConsequenceResult,
) -> str:
    _require(
        type(value) is M3InternalLaborCostConsequenceResult
        and replace(value) == value,
        "result",
    )
    return canonical_json(
        {item.name: _primitive(getattr(value, item.name)) for item in fields(value)}
    )


class _InternalCostEvidenceReader(Protocol):
    def get_internal_cost_consequence_evidence(self, support_id: str): ...


def _immutable(value, expected_type: type, field_name: str) -> None:
    _bind(
        type(value) is expected_type and replace(value) == value,
        "NON_CANONICAL_SOURCE_EVIDENCE",
        field_name,
    )


def _line(
    *,
    candidate_position: int,
    candidate_id: str,
    candidate_fingerprint: str,
    subject: InternalLaborCostSubject,
    selection: InternalLaborRateSelection,
    job_id: str,
    task_id: str,
    source_by_id: dict[str, WorkerInternalCostRateRevision],
) -> InternalLaborCostLine:
    common = dict(
        candidate_position=candidate_position,
        candidate_id=candidate_id,
        candidate_fingerprint=candidate_fingerprint,
        scope=subject.scope,
        commitment_id=subject.commitment_id,
        job_id=job_id,
        task_id=task_id,
        worker_id=subject.worker_id,
        interval_start=subject.interval_start,
        interval_end=subject.interval_end,
        subject_id=subject.subject_id,
        subject_fingerprint=subject.subject_fingerprint,
        selection_id=selection.selection_id,
        selection_fingerprint=selection.selection_fingerprint,
        selection_status=selection.status,
        source_record_id=selection.source_record_id,
        source_fingerprint=selection.source_fingerprint,
        source_revision=selection.source_revision,
        source_capture_id=selection.source_capture_id,
        source_capture_fingerprint=selection.source_capture_fingerprint,
        source_capture_generation=selection.source_capture_generation,
        no_source_reason=selection.no_source_reason,
    )
    if selection.status is RateSelectionStatus.NO_SOURCE:
        return InternalLaborCostLine(**common, rate_amount=None)
    source = source_by_id.get(selection.source_record_id)
    _bind(source is not None, "SELECTED_RATE_SOURCE_MISSING", "rate_source")
    _bind(
        (
            source.source_record_id,
            source.source_fingerprint,
            source.source_revision,
            source.worker_id,
        )
        == (
            selection.source_record_id,
            selection.source_fingerprint,
            selection.source_revision,
            subject.worker_id,
        ),
        "RATE_SOURCE_SUBJECT_MISMATCH",
        "rate_source",
    )
    return InternalLaborCostLine(**common, rate_amount=source.rate_amount)


def _calculate(
    requested_support_id: str,
    evaluation: M3EvaluationInput,
    support: FeasibilitySupportSnapshot,
    m3_result: M3BoundedFeasibilityResult,
    cost_support: InternalCostSupportCut,
    rate_sources: tuple[WorkerInternalCostRateRevision, ...],
) -> M3InternalLaborCostConsequenceResult:
    for value, expected, name in (
        (evaluation, M3EvaluationInput, "evaluation_input"),
        (support, FeasibilitySupportSnapshot, "feasibility_support"),
        (m3_result, M3BoundedFeasibilityResult, "m3_result"),
        (cost_support, InternalCostSupportCut, "internal_cost_support"),
    ):
        _immutable(value, expected, name)
    _identity(requested_support_id, "support_id")
    _bind(
        requested_support_id == cost_support.support_id,
        "REQUESTED_COST_SUPPORT_MISMATCH",
        "support_id",
    )
    _bind(
        (
            evaluation.evaluation_input_id,
            evaluation.evaluation_input_fingerprint,
            support.support_snapshot_id,
            support.support_fingerprint,
            m3_result.result_id,
            m3_result.result_fingerprint,
            evaluation.company_plan_id,
            evaluation.base_plan_revision,
            evaluation.base_plan_revision_id,
            evaluation.base_plan_revision_fingerprint,
        )
        == (
            cost_support.evaluation_input_id,
            cost_support.evaluation_input_fingerprint,
            cost_support.feasibility_support_snapshot_id,
            cost_support.feasibility_support_snapshot_fingerprint,
            cost_support.m3_result_id,
            cost_support.m3_result_fingerprint,
            cost_support.company_plan_id,
            cost_support.base_plan_revision,
            cost_support.base_plan_revision_id,
            cost_support.base_plan_revision_fingerprint,
        ),
        "M3_COST_SUPPORT_BINDING_MISMATCH",
        "internal_cost_support",
    )
    _bind(
        (
            support.evaluation_input_id,
            support.evaluation_input_fingerprint,
            support.company_plan_id,
            support.base_plan_revision,
            support.base_plan_revision_id,
            support.base_plan_revision_fingerprint,
        )
        == (
            evaluation.evaluation_input_id,
            evaluation.evaluation_input_fingerprint,
            evaluation.company_plan_id,
            evaluation.base_plan_revision,
            evaluation.base_plan_revision_id,
            evaluation.base_plan_revision_fingerprint,
        ),
        "EVALUATION_SUPPORT_BINDING_MISMATCH",
        "feasibility_support",
    )
    _bind(
        (
            m3_result.source_evaluation_input_id,
            m3_result.source_evaluation_input_fingerprint,
            m3_result.source_support_snapshot_id,
            m3_result.source_support_snapshot_fingerprint,
            m3_result.company_plan_id,
            m3_result.base_plan_revision,
        )
        == (
            evaluation.evaluation_input_id,
            evaluation.evaluation_input_fingerprint,
            support.support_snapshot_id,
            support.support_fingerprint,
            evaluation.company_plan_id,
            evaluation.base_plan_revision,
        ),
        "M3_RESULT_BINDING_MISMATCH",
        "m3_result",
    )
    expected_bindings = tuple(
        InternalCostCandidateBinding(
            position=position,
            candidate_id=candidate.candidate_id,
            candidate_fingerprint=candidate.candidate_fingerprint,
        )
        for position, candidate in enumerate(m3_result.candidates)
    )
    _bind(
        cost_support.candidate_bindings == expected_bindings,
        "CANDIDATE_SET_BINDING_MISMATCH",
        "candidate_bindings",
    )
    expected_subjects = derive_internal_cost_subjects(m3_result, support)
    _bind(
        cost_support.subjects == expected_subjects,
        "COST_SUBJECT_UNIVERSE_MISMATCH",
        "subjects",
    )
    rule = current_internal_labor_cost_rule()
    _bind(
        (
            cost_support.rule_id,
            cost_support.rule_revision,
            cost_support.rule_fingerprint,
        )
        == (rule.rule_id, rule.rule_revision, rule.rule_fingerprint),
        "INTERNAL_COST_RULE_MISMATCH",
        "internal_cost_rule",
    )
    _require(type(rate_sources) in (tuple, list), "rate_sources")
    for source in rate_sources:
        _immutable(source, WorkerInternalCostRateRevision, "rate_source")
        _bind(
            (
                source.configuration_id,
                source.rate_semantics,
                source.currency,
                source.cost_rule_version,
                source.rule_id,
                source.rule_fingerprint,
            )
            == (
                CONFIGURATION_ID,
                RATE_SEMANTICS,
                CURRENCY,
                M5_RULE_VERSION,
                rule.rule_id,
                rule.rule_fingerprint,
            ),
            "RATE_SOURCE_AUTHORITY_MISMATCH",
            "rate_source",
        )
    source_by_id = {item.source_record_id: item for item in rate_sources}
    _bind(
        len(source_by_id) == len(rate_sources),
        "DUPLICATE_RATE_SOURCE",
        "rate_sources",
    )
    selected_source_ids = {
        item.source_record_id
        for item in cost_support.selections
        if item.status is RateSelectionStatus.SELECTED
    }
    _bind(
        set(source_by_id) == selected_source_ids,
        "SELECTED_RATE_SOURCE_SET_MISMATCH",
        "rate_sources",
    )
    selection_by_subject = {
        item.subject_id: item for item in cost_support.selections
    }
    subject_by_key = {
        (item.candidate_position, item.scope, item.commitment_id): item
        for item in cost_support.subjects
    }
    base_by_commitment = {
        item.commitment_id: item for item in support.schedule.placements
    }
    active_by_commitment = {
        item.commitment_id: item for item in evaluation.current_active_commitments
    }
    consequences = []
    for position, candidate in enumerate(m3_result.candidates):
        if candidate.verdict is not CandidateVerdict.FEASIBLE:
            continue
        proposed_by_commitment = {
            item.commitment_id: item for item in candidate.proposed_worker_placements
        }
        baseline_lines = []
        candidate_lines = []
        for commitment_id in candidate.modified_commitment_ids:
            baseline = base_by_commitment.get(commitment_id)
            active = active_by_commitment.get(commitment_id)
            proposed = proposed_by_commitment.get(commitment_id)
            _bind(
                baseline is not None and active is not None and proposed is not None,
                "MODIFIED_COMMITMENT_EVIDENCE_MISSING",
                "modified_commitment_ids",
            )
            _bind(
                len(baseline.worker_ids) == 1
                and (
                    baseline.job_id,
                    baseline.task_id,
                    baseline.commitment_id,
                )
                == (active.job_id, active.task_id, active.commitment_id)
                == (proposed.job_id, proposed.task_id, proposed.commitment_id),
                "BASELINE_CANDIDATE_COMMITMENT_MISMATCH",
                "modified_commitment_ids",
            )
            baseline_subject = subject_by_key.get(
                (position, LaborCostSubjectScope.BASELINE, commitment_id)
            )
            candidate_subject = subject_by_key.get(
                (position, LaborCostSubjectScope.CANDIDATE, commitment_id)
            )
            _bind(
                baseline_subject is not None and candidate_subject is not None,
                "COST_SUBJECT_MISSING",
                "subjects",
            )
            baseline_selection = selection_by_subject.get(baseline_subject.subject_id)
            candidate_selection = selection_by_subject.get(candidate_subject.subject_id)
            _bind(
                baseline_selection is not None and candidate_selection is not None,
                "RATE_SELECTION_MISSING",
                "selections",
            )
            baseline_lines.append(
                _line(
                    candidate_position=position,
                    candidate_id=candidate.candidate_id,
                    candidate_fingerprint=candidate.candidate_fingerprint,
                    subject=baseline_subject,
                    selection=baseline_selection,
                    job_id=baseline.job_id,
                    task_id=baseline.task_id,
                    source_by_id=source_by_id,
                )
            )
            candidate_lines.append(
                _line(
                    candidate_position=position,
                    candidate_id=candidate.candidate_id,
                    candidate_fingerprint=candidate.candidate_fingerprint,
                    subject=candidate_subject,
                    selection=candidate_selection,
                    job_id=proposed.job_id,
                    task_id=proposed.task_id,
                    source_by_id=source_by_id,
                )
            )
        consequences.append(
            CandidateInternalLaborCostConsequence(
                candidate_position=position,
                candidate_id=candidate.candidate_id,
                candidate_fingerprint=candidate.candidate_fingerprint,
                modified_commitment_ids=candidate.modified_commitment_ids,
                baseline_lines=tuple(baseline_lines),
                candidate_lines=tuple(candidate_lines),
            )
        )
    value = M3InternalLaborCostConsequenceResult(
        source_evaluation_input_id=evaluation.evaluation_input_id,
        source_evaluation_input_fingerprint=evaluation.evaluation_input_fingerprint,
        source_support_snapshot_id=support.support_snapshot_id,
        source_support_snapshot_fingerprint=support.support_fingerprint,
        source_m3_result_id=m3_result.result_id,
        source_m3_result_fingerprint=m3_result.result_fingerprint,
        source_internal_cost_support_id=cost_support.support_id,
        source_internal_cost_support_fingerprint=cost_support.support_fingerprint,
        company_plan_id=evaluation.company_plan_id,
        base_plan_revision=evaluation.base_plan_revision,
        base_plan_revision_id=evaluation.base_plan_revision_id,
        base_plan_revision_fingerprint=evaluation.base_plan_revision_fingerprint,
        m5_rule_id=cost_support.rule_id,
        m5_rule_revision=cost_support.rule_revision,
        m5_rule_fingerprint=cost_support.rule_fingerprint,
        search_envelope_exhausted=m3_result.search_envelope_exhausted,
        global_solution_status=m3_result.global_solution_status,
        consequences=tuple(consequences),
    )
    expected_feasible = tuple(
        (position, candidate.candidate_id, candidate.candidate_fingerprint)
        for position, candidate in enumerate(m3_result.candidates)
        if candidate.verdict is CandidateVerdict.FEASIBLE
    )
    _bind(
        tuple(
            (item.candidate_position, item.candidate_id, item.candidate_fingerprint)
            for item in value.consequences
        )
        == expected_feasible,
        "FEASIBLE_CANDIDATE_COVERAGE_MISMATCH",
        "consequences",
    )
    return value


class M3InternalLaborCostConsequenceService:
    """Calculate M3-D from one exact durable M5-A support identity."""

    def __init__(self, repository: _InternalCostEvidenceReader) -> None:
        self.repository = repository

    def calculate(
        self, internal_cost_support_id: str
    ) -> M3InternalLaborCostConsequenceResult:
        _identity(internal_cost_support_id, "internal_cost_support_id")
        evidence = self.repository.get_internal_cost_consequence_evidence(
            internal_cost_support_id
        )
        _bind(
            type(evidence) in (tuple, list) and len(evidence) == 5,
            "INVALID_REPOSITORY_EVIDENCE",
            "repository",
        )
        return _calculate(internal_cost_support_id, *evidence)


__all__ = [
    "CANDIDATE_CONSEQUENCE_SCHEMA_VERSION",
    "CONSEQUENCE_RULE_VERSION",
    "CandidateInternalLaborCostConsequence",
    "InternalLaborCostIssue",
    "InternalLaborCostLine",
    "LaborCostCompleteness",
    "LaborCostIssueCode",
    "M3InternalLaborCostConsequenceBindingError",
    "M3InternalLaborCostConsequenceError",
    "M3InternalLaborCostConsequenceResult",
    "M3InternalLaborCostConsequenceService",
    "M3InternalLaborCostConsequenceValidationError",
    "candidate_internal_labor_cost_semantic_json",
    "internal_labor_cost_consequence_result_semantic_json",
    "internal_labor_cost_line_semantic_json",
    "serialize_internal_labor_cost_consequence_result",
]
