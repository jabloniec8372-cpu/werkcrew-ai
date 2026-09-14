"""Canonical M3-E1 operational-authority evidence and pure evaluation.

This module implements the decision-time, per-candidate boundary frozen by
ADR 0014.  It deliberately contains no repository, recorder, clock, random
identity, mutation, ranking, selection, APPLY, messaging, or LLM integration.

Constructing one of these values does not make caller data authoritative.  A
later persistence atom must reconstruct the same values from deployment-
restricted sources and capture the currentness vector in one coherent read
transaction.  The evaluator here consumes only that already-canonical cut.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from werkcrew_ai.authority.models import (
    AUTHORITY_RULE_VERSION,
    CompanyAuthorityRoot,
    PrincipalType,
    TrustedPrincipal,
)
from werkcrew_ai.authority.policy_evidence import (
    COMPANY_POLICY_PROFILE_SCHEMA_VERSION,
    COMPANY_POLICY_RULE_VERSION,
    POLICY_EVIDENCE_RULE_VERSION,
    POLICY_ISSUANCE_SCHEMA_VERSION,
    AuthorityEvidenceScope,
    CompanyPolicyProfile,
    EvidenceScopeKind,
    EvidenceSubjectType,
    PolicyIssuanceEvidence,
    RecordedConsentStatus,
    authority_evidence_scope_semantic_json,
)
from werkcrew_ai.domain.decision_semantics import (
    Disposition,
    ExecutionAuthorization,
    OverrideStatus,
    PolicyRelation,
    TechnicalFeasibility,
)
from werkcrew_ai.field.serialization import canonical_json, sha256_text
from werkcrew_ai.field.models import PlanDayRoot, PlanDayStatus
from werkcrew_ai.field.serialization import (
    deserialize_plan_day_root,
    serialize_plan_day_root,
)
from werkcrew_ai.planning.bounded_feasibility import (
    CandidateVerdict,
    ConstraintStatus,
    M3BoundedFeasibilityResult,
    M3RepairCandidate,
    ReasonCode,
    SearchOutcome,
)
from werkcrew_ai.planning.current_plan import (
    CompanyPlan,
    PlanRevision,
    deserialize_plan_revision,
    serialize_plan_revision,
)
from werkcrew_ai.planning.evaluation_input import (
    M3EvaluationInput,
    deserialize_evaluation_input,
    serialize_evaluation_input,
)
from werkcrew_ai.planning.feasibility_support import (
    FeasibilitySupportSnapshot,
    ScheduledPlacementEvidence,
    deserialize_feasibility_support,
    serialize_feasibility_support,
    source_semantic_json as feasibility_source_semantic_json,
)
from werkcrew_ai.pricing.internal_cost_support import InternalCostSupportCut
from werkcrew_ai.pricing.internal_labor_cost_consequence import (
    CandidateInternalLaborCostConsequence,
    LaborCostCompleteness,
    M3InternalLaborCostConsequenceResult,
)


OPERATIONAL_AUTHORITY_RULE_VERSION = "m3-operational-authority-v1"
WORKING_TIME_RULE_SCHEMA_VERSION = "m3e1-working-time-rule-evidence-v1"
WORKING_TIME_ITEM_SCHEMA_VERSION = "m3e1-working-time-work-item-v1"
WORKING_TIME_COMMON_MANIFEST_ENTRY_SCHEMA_VERSION = (
    "m3e1-working-time-common-manifest-entry-v1"
)
WORKING_TIME_SOURCE_MANIFEST_SCHEMA_VERSION = "m3e1-working-time-source-manifest-v1"
WORKING_TIME_SOURCE_SELECTION_SCHEMA_VERSION = "m3e1-working-time-source-selection-v1"
WORKING_TIME_COMPARISON_SCHEMA_VERSION = "m3e1-worker-period-overtime-comparison-v1"
WORKING_TIME_CANDIDATE_SCHEMA_VERSION = "m3e1-candidate-working-time-evidence-v1"
WORKING_TIME_CUT_SCHEMA_VERSION = "m3e1-working-time-evidence-cut-v1"
OVERTIME_ASSESSMENT_SCHEMA_VERSION = "m3e1-candidate-overtime-assessment-v1"
MARKER_SUBJECT_SCHEMA_VERSION = "m3e1-authority-marker-subject-v1"
MARKER_RESOURCE_SUBJECT_SCHEMA_VERSION = (
    "m3e1-authority-marker-resource-subject-v1"
)
MARKER_RESOURCE_SUBJECT_RULE_VERSION = "m3e1-authority-marker-subject-v1"
MARKER_EVIDENCE_SCHEMA_VERSION = "m3e1-authority-marker-evidence-v1"
MARKER_UNIVERSE_SCHEMA_VERSION = "m3e1-candidate-marker-subject-universe-v1"
MARKER_COVERAGE_SCHEMA_VERSION = "m3e1-authority-marker-subject-coverage-v1"
MARKER_CUT_SCHEMA_VERSION = "m3e1-authority-marker-support-cut-v1"
TAG_ASSESSMENT_SCHEMA_VERSION = "m3e1-candidate-approval-tag-assessment-v1"
P_COST_SUBJECT_SCHEMA_VERSION = "m3e1-p-cost-approval-subject-v1"
COST_ASSESSMENT_SCHEMA_VERSION = "m3e1-candidate-cost-assessment-v1"
AUTHORITY_REQUIREMENT_SCHEMA_VERSION = "m3e1-authority-requirement-v1"
AUTHORITY_REQUIREMENT_SET_SCHEMA_VERSION = "m3e1-authority-requirement-set-v1"
OWNER_SELECTION_SCHEMA_VERSION = "m3e1-owner-approval-selection-v1"
CONSENT_SELECTION_SCHEMA_VERSION = "m3e1-worker-consent-head-selection-v1"
HARD_BOUNDARY_SCHEMA_VERSION = "m3e1-hard-boundary-assessment-v1"
CURRENTNESS_VECTOR_SCHEMA_VERSION = "m3e1-decision-time-currentness-vector-v1"
CURRENTNESS_OBSERVATION_SCHEMA_VERSION = "m3e1-currentness-observation-v1"
PLAN_DAY_ROOT_HEAD_SCHEMA_VERSION = "m3e1-plan-day-root-head-v1"
AUTHORITY_CUT_SCHEMA_VERSION = "m3e1-authority-evidence-cut-v1"
GATE_ASSESSMENT_SCHEMA_VERSION = "m3e1-authority-gate-assessment-v1"
CANDIDATE_ASSESSMENT_SCHEMA_VERSION = "m3e1-candidate-authority-assessment-v1"
AUTHORITY_RESULT_SCHEMA_VERSION = "m3e1-operational-authority-result-v1"

P_COST_THRESHOLD_EUR = Decimal("50.00")
EUR = "EUR"


class OperationalAuthorityError(ValueError):
    def __init__(self, code: str, field_name: str) -> None:
        self.code = code
        self.field_name = field_name
        super().__init__(f"{code}: {field_name}")


class OperationalAuthorityValidationError(OperationalAuthorityError):
    pass


class OperationalAuthorityBindingError(OperationalAuthorityError):
    pass


class OvertimeApplicability(StrEnum):
    CREATES_OVERTIME = "CREATES_OVERTIME"
    DOES_NOT_CREATE_OVERTIME = "DOES_NOT_CREATE_OVERTIME"
    UNKNOWN = "UNKNOWN"


class ApprovalMarkerApplicability(StrEnum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class AuthorityMarkerKind(StrEnum):
    OWNER_APPROVAL_REQUIRED = "OWNER_APPROVAL_REQUIRED"


class AuthorityGateCode(StrEnum):
    P_ASSIGN = "P_ASSIGN"
    P_WINDOW_01 = "P_WINDOW_01"
    P_COST = "P_COST"
    P_OVERTIME = "P_OVERTIME"
    P_VEHICLE = "P_VEHICLE"
    P_TAG = "P_TAG"
    P_HORIZON = "P_HORIZON"
    P_SEARCH = "P_SEARCH"
    PARTIAL_REPAIR = "PARTIAL_REPAIR"


class GateDecision(StrEnum):
    PASS = "PASS"
    NEEDS_HUMAN_AUTHORITY = "NEEDS_HUMAN_AUTHORITY"
    HARD_BLOCK = "HARD_BLOCK"
    INDETERMINATE = "INDETERMINATE"


class AuthorityOutcome(StrEnum):
    ACT = "ACT"
    ASK = "ASK"
    BLOCK = "BLOCK"
    ABSTAIN = "ABSTAIN"


class M3AuthorityEvaluationStatus(StrEnum):
    CANDIDATES_ASSESSED = "CANDIDATES_ASSESSED"
    NO_ACTION_REQUIRED = "NO_ACTION_REQUIRED"


class SearchAuthorityProjection(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ABSTAIN = "ABSTAIN"


class CostApplicability(StrEnum):
    WITHIN_ENVELOPE = "WITHIN_ENVELOPE"
    SOFT_EXCEPTION = "SOFT_EXCEPTION"
    UNKNOWN = "UNKNOWN"


class PCostApprovalSubjectState(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    COMPLETE = "COMPLETE"
    UNAVAILABLE_UNSUPPORTED_POLICY = "UNAVAILABLE_UNSUPPORTED_POLICY"


class PolicySupportStatus(StrEnum):
    ISSUED_SUPPORTED = "ISSUED_SUPPORTED"
    POLICY_UNSIGNED = "POLICY_UNSIGNED"
    UNSUPPORTED = "UNSUPPORTED"


class AuthoritySubjectClass(StrEnum):
    NATURAL_SUBJECT = "NATURAL_SUBJECT"
    CANDIDATE_CONSEQUENCE = "CANDIDATE_CONSEQUENCE"


class SelectionStatus(StrEnum):
    SELECTED = "SELECTED"
    ABSENT = "ABSENT"


class MarkerSubjectKind(StrEnum):
    COMMITMENT = "COMMITMENT"
    WORKER = "WORKER"
    VEHICLE = "VEHICLE"


class MarkerCoverageStatus(StrEnum):
    MARKED = "MARKED"
    NO_MARKER = "NO_MARKER"
    UNKNOWN = "UNKNOWN"


class WorkingTimeWorkKind(StrEnum):
    COMPLETED_COUNTABLE = "COMPLETED_COUNTABLE"
    UNCHANGED_SCHEDULED = "UNCHANGED_SCHEDULED"
    BASELINE_CHANGED = "BASELINE_CHANGED"
    PROPOSED_CANDIDATE = "PROPOSED_CANDIDATE"


class WorkingTimeSourceKind(StrEnum):
    C0_PLAN_SCHEDULE = "C0_PLAN_SCHEDULE"
    WORKING_TIME_LEDGER = "WORKING_TIME_LEDGER"


class WorkingTimeSourceClassState(StrEnum):
    PRESENT = "PRESENT"
    AUTHORITATIVELY_EMPTY = "AUTHORITATIVELY_EMPTY"
    UNKNOWN = "UNKNOWN"


class HardBoundaryKind(StrEnum):
    POLICY = "POLICY"
    LEGAL = "LEGAL"
    SAFETY = "SAFETY"
    REQUIRED_PERMISSION = "REQUIRED_PERMISSION"


class HardBoundaryApplicability(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    APPLICABLE = "APPLICABLE"
    UNKNOWN = "UNKNOWN"


class CurrentnessEvidenceState(StrEnum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"
    INCOMPLETE = "INCOMPLETE"


class CurrentnessKind(StrEnum):
    COMPANY_PLAN_REVISION_HEAD = "01_COMPANY_PLAN_REVISION_HEAD"
    CURRENT_PLANNING_SCOPE = "02_CURRENT_PLANNING_SCOPE"
    M1_HANDOFF_PRECONDITIONS = "03_M1_HANDOFF_PRECONDITIONS"
    M2_JOB_TASK_PRECONDITIONS = "04_M2_JOB_TASK_PRECONDITIONS"
    M2_ASSIGNMENT_PRECONDITIONS = "05_M2_ASSIGNMENT_PRECONDITIONS"
    PLAN_DAY_PRECONDITIONS = "06_PLAN_DAY_PRECONDITIONS"
    EVALUATION_INPUT_BINDING = "07_EVALUATION_INPUT_BINDING"
    C0_SUPPORT_SELECTED_SOURCES = "08_C0_SUPPORT_SELECTED_SOURCES"
    M3_C_RECOMPUTATION = "09_M3_C_RECOMPUTATION"
    M5_M3_D_CHAIN = "10_M5_M3_D_CHAIN"
    AUTH_0_BINDING = "11_AUTH_0_BINDING"
    POLICY_BINDING = "12_POLICY_BINDING"
    OWNER_APPROVAL_SELECTIONS = "13_OWNER_APPROVAL_SELECTIONS"
    WORKER_CONSENT_HEADS = "14_WORKER_CONSENT_HEADS"
    WORKING_TIME_MARKER_HEADS = "15_WORKING_TIME_MARKER_HEADS"
    DERIVED_UNIVERSES_RESULT_INPUTS = "16_DERIVED_UNIVERSES_RESULT_INPUTS"


def _require(condition: bool, field_name: str) -> None:
    if not condition:
        raise OperationalAuthorityValidationError("INVALID_VALUE", field_name)


def _bind(condition: bool, code: str, field_name: str) -> None:
    if not condition:
        raise OperationalAuthorityBindingError(code, field_name)


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


def _optional_identity(value: str | None, field_name: str) -> None:
    _require(value is None or type(value) is str, field_name)
    if value is not None:
        _identity(value, field_name)


def _digest(value: str, field_name: str) -> None:
    _require(
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value),
        field_name,
    )


def _optional_digest(value: str | None, field_name: str) -> None:
    _require(value is None or type(value) is str, field_name)
    if value is not None:
        _digest(value, field_name)


def _revision(value: int, field_name: str, *, positive: bool = False) -> None:
    _require(type(value) is int and 0 <= value <= 9223372036854775807, field_name)
    if positive:
        _require(value > 0, field_name)


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


def _canonical_decimal(value: Decimal, field_name: str) -> str:
    _require(type(value) is Decimal and value.is_finite(), field_name)
    sign, raw_digits, exponent = value.as_tuple()
    digits = list(raw_digits) or [0]
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if all(digit == 0 for digit in digits):
        return "0"
    text = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        text += "0" * exponent
    else:
        point = len(text) + exponent
        text = (
            "0." + "0" * (-point) + text
            if point <= 0
            else text[:point] + "." + text[point:]
        )
    return ("-" if sign else "") + text


def _money(value: Decimal, field_name: str) -> Decimal:
    _require(type(value) is Decimal and value.is_finite(), field_name)
    numerator, denominator = value.as_integer_ratio()
    _, remainder = divmod(numerator * 100, denominator)
    _require(remainder == 0, field_name)
    return value


def _primitive(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if type(value) is Decimal:
        return _canonical_decimal(value, "decimal")
    if type(value) is datetime:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if type(value) is date:
        return value.isoformat()
    if type(value) is tuple:
        return [_primitive(item) for item in value]
    if type(value) is list:
        return [_primitive(item) for item in value]
    if type(value) is dict:
        return {str(key): _primitive(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return {
            item.name: _primitive(getattr(value, item.name)) for item in fields(value)
        }
    return value


def _semantic_json(value: Any, omitted: tuple[str, ...]) -> str:
    return canonical_json(
        {
            item.name: _primitive(getattr(value, item.name))
            for item in fields(value)
            if item.name not in omitted
        }
    )


def _canonical_records(
    values: Iterable[Any],
    expected: type,
    key,
    field_name: str,
) -> tuple[Any, ...]:
    _require(type(values) in (tuple, list), field_name)
    result = []
    for value in values:
        _require(type(value) is expected, field_name)
        _require(replace(value) == value, field_name)
        result.append(value)
    keys = [key(value) for value in result]
    _require(len(keys) == len(set(keys)), field_name)
    return tuple(sorted(result, key=key))


def _ordered_records(values: Iterable[Any], expected: type, field_name: str) -> tuple[Any, ...]:
    _require(type(values) in (tuple, list), field_name)
    result = tuple(values)
    _require(
        all(type(value) is expected and replace(value) == value for value in result),
        field_name,
    )
    return result


def _bound_id(record_id: str, fingerprint: str, prefix: str, field_name: str) -> None:
    _identity(record_id, field_name + "_id")
    _digest(fingerprint, field_name + "_fingerprint")
    _require(record_id == prefix + fingerprint, field_name + "_id")


def _root_binding(authority_root_id: str, authority_root_fingerprint: str) -> None:
    _bound_id(
        authority_root_id,
        authority_root_fingerprint,
        "auth0-company-authority-root-",
        "authority_root",
    )


def _candidate_binding(candidate_id: str, candidate_fingerprint: str) -> None:
    _bound_id(candidate_id, candidate_fingerprint, "m3-candidate-", "candidate")


def _canonical_json_document(value: str, field_name: str) -> None:
    _require(type(value) is str, field_name)
    try:
        document = json.loads(value)
    except (json.JSONDecodeError, TypeError, RecursionError) as error:
        raise OperationalAuthorityValidationError("INVALID_VALUE", field_name) from error
    _require(canonical_json(document) == value, field_name)


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceBinding:
    kind: str
    record_id: str
    fingerprint: str

    def __post_init__(self) -> None:
        _identity(self.kind, "binding.kind")
        _identity(self.record_id, "binding.record_id")
        _digest(self.fingerprint, "binding.fingerprint")


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationalSlice:
    job_id: str
    operational_date: date

    def __post_init__(self) -> None:
        _identity(self.job_id, "slice.job_id")
        _require(type(self.operational_date) is date, "slice.operational_date")


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkingTimeRuleEvidence:
    authority_root_id: str
    authority_root_fingerprint: str
    company_id: str
    company_plan_id: str
    worker_id: str
    worker_registry_revision: int
    worker_registry_fingerprint: str
    calculation_period_start: datetime
    calculation_period_end: datetime
    source_record_id: str
    source_revision: int
    source_fingerprint: str
    source_capture_id: str
    source_capture_generation: int
    source_capture_fingerprint: str
    business_effective_from: datetime
    business_effective_until: datetime | None
    provenance_reference: str
    timezone_name: str
    calculation_period_semantics: str
    countable_work_semantics: str
    threshold_overtime_semantics: str
    interval_splitting_semantics: str
    rounding_semantics: str
    algorithm_version: str
    canonical_rule_payload_json: str
    schema_version: str = field(default=WORKING_TIME_RULE_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    rule_fingerprint: str = field(init=False)
    rule_id: str = field(init=False)

    def __post_init__(self) -> None:
        _root_binding(self.authority_root_id, self.authority_root_fingerprint)
        for name in (
            "company_id",
            "company_plan_id",
            "worker_id",
            "source_record_id",
            "source_capture_id",
            "provenance_reference",
            "timezone_name",
            "calculation_period_semantics",
            "countable_work_semantics",
            "threshold_overtime_semantics",
            "interval_splitting_semantics",
            "rounding_semantics",
            "algorithm_version",
        ):
            _identity(getattr(self, name), "working_time_rule." + name)
        _canonical_json_document(
            self.canonical_rule_payload_json,
            "working_time_rule.canonical_rule_payload_json",
        )
        payload = json.loads(self.canonical_rule_payload_json)
        _require(
            type(payload) is dict
            and set(payload)
            == {
                "algorithm_version",
                "calculation_period_kind",
                "countable_work_mode",
                "interval_aggregation",
                "overtime_formula",
                "overtime_threshold_seconds",
                "rounding_mode",
            },
            "working_time_rule.canonical_rule_payload_json",
        )
        _require(
            payload["algorithm_version"] == self.algorithm_version
            and payload["calculation_period_kind"] == "LOCAL_CALENDAR_DAY"
            and payload["countable_work_mode"] == "ALL_BOUND_WORK_ITEMS_COUNTABLE"
            and payload["interval_aggregation"] == "UNION"
            and payload["overtime_formula"]
            == "MAX_ZERO_COUNTABLE_SECONDS_MINUS_THRESHOLD"
            and type(payload["overtime_threshold_seconds"]) is int
            and payload["overtime_threshold_seconds"] >= 0
            and payload["rounding_mode"] == "EXACT_SECONDS"
            and self.calculation_period_semantics == "LOCAL_CALENDAR_DAY"
            and self.countable_work_semantics
            == "ALL_BOUND_WORK_ITEMS_COUNTABLE"
            and self.threshold_overtime_semantics
            == "MAX_ZERO_COUNTABLE_SECONDS_MINUS_THRESHOLD"
            and self.interval_splitting_semantics
            == "INTERSECT_LOCAL_PERIOD_BOUNDARIES"
            and self.rounding_semantics == "EXACT_SECONDS",
            "working_time_rule.supported_algorithm",
        )
        _revision(self.worker_registry_revision, "worker_registry_revision")
        _digest(self.worker_registry_fingerprint, "worker_registry_fingerprint")
        _revision(self.source_revision, "source_revision")
        _digest(self.source_fingerprint, "source_fingerprint")
        _revision(self.source_capture_generation, "source_capture_generation", positive=True)
        _digest(self.source_capture_fingerprint, "source_capture_fingerprint")
        period_start = _instant(self.calculation_period_start, "calculation_period_start")
        period_end = _instant(self.calculation_period_end, "calculation_period_end")
        _require(period_end > period_start, "calculation_period")
        effective_from = _instant(self.business_effective_from, "business_effective_from")
        effective_until = (
            None
            if self.business_effective_until is None
            else _instant(self.business_effective_until, "business_effective_until")
        )
        _require(effective_until is None or effective_until > effective_from, "business_effective_interval")
        _require(
            effective_from <= period_start
            and (effective_until is None or effective_until >= period_end),
            "rule_effective_period_coverage",
        )
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as error:
            raise OperationalAuthorityValidationError(
                "UNKNOWN_TIMEZONE", "timezone_name"
            ) from error
        object.__setattr__(self, "calculation_period_start", period_start)
        object.__setattr__(self, "calculation_period_end", period_end)
        object.__setattr__(self, "business_effective_from", effective_from)
        object.__setattr__(self, "business_effective_until", effective_until)
        digest = sha256_text(working_time_rule_evidence_semantic_json(self))
        object.__setattr__(self, "rule_fingerprint", digest)
        object.__setattr__(self, "rule_id", "m3e1-working-time-rule-" + digest)


def working_time_rule_evidence_semantic_json(value: WorkingTimeRuleEvidence) -> str:
    _require(type(value) is WorkingTimeRuleEvidence, "working_time_rule")
    return _semantic_json(value, ("rule_fingerprint", "rule_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkingTimeWorkItem:
    worker_id: str
    work_kind: WorkingTimeWorkKind
    commitment_id: str | None
    job_id: str | None
    task_id: str | None
    operational_date: date | None
    candidate_id: str | None
    candidate_fingerprint: str | None
    interval_start: datetime
    interval_end: datetime
    countability_classification: str
    source_record_id: str
    source_revision: int
    source_fingerprint: str
    source_capture_id: str
    source_capture_generation: int
    source_capture_fingerprint: str
    provenance_reference: str
    schema_version: str = field(default=WORKING_TIME_ITEM_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    work_item_fingerprint: str = field(init=False)
    work_item_id: str = field(init=False)

    def __post_init__(self) -> None:
        _identity(self.worker_id, "work_item.worker_id")
        _require(type(self.work_kind) is WorkingTimeWorkKind, "work_item.work_kind")
        _optional_identity(self.commitment_id, "work_item.commitment_id")
        _optional_identity(self.job_id, "work_item.job_id")
        _optional_identity(self.task_id, "work_item.task_id")
        _require(
            self.operational_date is None or type(self.operational_date) is date,
            "work_item.operational_date",
        )
        slice_values = (
            self.commitment_id,
            self.job_id,
            self.task_id,
            self.operational_date,
        )
        _require(
            all(value is None for value in slice_values)
            or all(value is not None for value in slice_values),
            "work_item.operational_slice",
        )
        if self.candidate_id is None or self.candidate_fingerprint is None:
            _require(
                self.candidate_id is None and self.candidate_fingerprint is None,
                "work_item.candidate_binding",
            )
        else:
            _candidate_binding(self.candidate_id, self.candidate_fingerprint)
        if self.work_kind is WorkingTimeWorkKind.PROPOSED_CANDIDATE:
            _require(
                self.candidate_id is not None
                and all(value is not None for value in slice_values),
                "work_item.candidate_binding",
            )
        else:
            _require(self.candidate_id is None, "work_item.candidate_binding")
        if self.work_kind is WorkingTimeWorkKind.BASELINE_CHANGED:
            _require(
                all(value is not None for value in slice_values),
                "work_item.baseline_binding",
            )
        start = _instant(self.interval_start, "work_item.interval_start")
        end = _instant(self.interval_end, "work_item.interval_end")
        _require(end > start, "work_item.interval")
        object.__setattr__(self, "interval_start", start)
        object.__setattr__(self, "interval_end", end)
        for name in (
            "countability_classification",
            "source_record_id",
            "source_capture_id",
            "provenance_reference",
        ):
            _identity(getattr(self, name), "work_item." + name)
        _revision(self.source_revision, "work_item.source_revision")
        _digest(self.source_fingerprint, "work_item.source_fingerprint")
        _revision(self.source_capture_generation, "work_item.source_capture_generation")
        _digest(self.source_capture_fingerprint, "work_item.source_capture_fingerprint")
        digest = sha256_text(working_time_work_item_semantic_json(self))
        object.__setattr__(self, "work_item_fingerprint", digest)
        object.__setattr__(self, "work_item_id", "m3e1-working-time-item-" + digest)


def working_time_work_item_semantic_json(value: WorkingTimeWorkItem) -> str:
    _require(type(value) is WorkingTimeWorkItem, "working_time_work_item")
    return _semantic_json(value, ("work_item_fingerprint", "work_item_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkingTimeCommonManifestEntry:
    candidate_position: int
    candidate_id: str
    candidate_fingerprint: str
    worker_id: str
    calculation_period_start: datetime
    calculation_period_end: datetime
    timezone_name: str
    common_work_item_ids: tuple[str, ...]
    common_work_item_fingerprints: tuple[str, ...]
    coverage_complete: bool
    schema_version: str = field(
        default=WORKING_TIME_COMMON_MANIFEST_ENTRY_SCHEMA_VERSION,
        init=False,
    )
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    entry_fingerprint: str = field(init=False)
    entry_id: str = field(init=False)

    def __post_init__(self) -> None:
        _revision(self.candidate_position, "common_manifest.candidate_position")
        _candidate_binding(self.candidate_id, self.candidate_fingerprint)
        _identity(self.worker_id, "common_manifest.worker_id")
        start = _instant(
            self.calculation_period_start,
            "common_manifest.calculation_period_start",
        )
        end = _instant(
            self.calculation_period_end,
            "common_manifest.calculation_period_end",
        )
        _require(end > start, "common_manifest.calculation_period")
        _identity(self.timezone_name, "common_manifest.timezone_name")
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as error:
            raise OperationalAuthorityValidationError(
                "UNKNOWN_TIMEZONE",
                "common_manifest.timezone_name",
            ) from error
        _require(type(self.coverage_complete) is bool, "common_manifest.coverage_complete")
        ids = tuple(self.common_work_item_ids)
        fingerprints = tuple(self.common_work_item_fingerprints)
        _require(
            len(ids) == len(fingerprints) and len(ids) == len(set(ids)),
            "common_manifest.work_items",
        )
        pairs = tuple(sorted(zip(ids, fingerprints, strict=True)))
        for item_id, fingerprint in pairs:
            _bound_id(
                item_id,
                fingerprint,
                "m3e1-working-time-item-",
                "common_manifest.work_item",
            )
        object.__setattr__(self, "calculation_period_start", start)
        object.__setattr__(self, "calculation_period_end", end)
        object.__setattr__(self, "common_work_item_ids", tuple(item[0] for item in pairs))
        object.__setattr__(
            self,
            "common_work_item_fingerprints",
            tuple(item[1] for item in pairs),
        )
        digest = sha256_text(working_time_common_manifest_entry_semantic_json(self))
        object.__setattr__(self, "entry_fingerprint", digest)
        object.__setattr__(self, "entry_id", "m3e1-working-time-common-manifest-entry-" + digest)

    @property
    def period_key(self) -> str:
        return _working_period_key(
            self.worker_id,
            self.calculation_period_start,
            self.calculation_period_end,
            self.timezone_name,
        )


def working_time_common_manifest_entry_semantic_json(
    value: WorkingTimeCommonManifestEntry,
) -> str:
    return _semantic_json(value, ("entry_fingerprint", "entry_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkingTimeSourceManifest:
    authority_root_id: str
    authority_root_fingerprint: str
    company_id: str
    company_plan_id: str
    worker_registry_revision: int
    worker_registry_fingerprint: str
    source_kind: WorkingTimeSourceKind
    source_head_id: str
    source_revision: int
    source_fingerprint: str
    source_capture_id: str
    source_capture_generation: int
    source_capture_fingerprint: str
    provenance_reference: str
    canonical_source_content_json: str
    entries: tuple[WorkingTimeCommonManifestEntry, ...]
    coverage_complete: bool
    producer_classification: str = field(
        default="DEPLOYMENT_RESTRICTED_AUTH0_COMPANY_BOUND_TRUSTED_ADAPTOR",
        init=False,
    )
    schema_version: str = field(default=WORKING_TIME_SOURCE_MANIFEST_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    manifest_fingerprint: str = field(init=False)
    manifest_id: str = field(init=False)

    def __post_init__(self) -> None:
        _root_binding(self.authority_root_id, self.authority_root_fingerprint)
        for name in (
            "company_id",
            "company_plan_id",
            "source_head_id",
            "source_capture_id",
            "provenance_reference",
        ):
            _identity(getattr(self, name), "working_time_manifest." + name)
        _revision(self.worker_registry_revision, "working_time_manifest.worker_registry_revision")
        _digest(self.worker_registry_fingerprint, "working_time_manifest.worker_registry_fingerprint")
        _require(type(self.source_kind) is WorkingTimeSourceKind, "working_time_manifest.source_kind")
        _revision(self.source_revision, "working_time_manifest.source_revision")
        _digest(self.source_fingerprint, "working_time_manifest.source_fingerprint")
        _revision(
            self.source_capture_generation,
            "working_time_manifest.source_capture_generation",
        )
        _digest(
            self.source_capture_fingerprint,
            "working_time_manifest.source_capture_fingerprint",
        )
        _canonical_json_document(
            self.canonical_source_content_json,
            "working_time_manifest.canonical_source_content_json",
        )
        _require(
            sha256_text(self.canonical_source_content_json) == self.source_fingerprint,
            "working_time_manifest.source_content_fingerprint",
        )
        if self.source_kind is WorkingTimeSourceKind.WORKING_TIME_LEDGER:
            document = json.loads(self.canonical_source_content_json)
            _require(
                type(document) is dict
                and set(document)
                == {
                    "complete_common_work_items",
                    "provenance_reference",
                    "schema_version",
                    "source_capture_generation",
                    "source_capture_id",
                    "source_head_id",
                    "source_revision",
                }
                and document["schema_version"]
                == "m3e1-working-time-ledger-source-v1"
                and document["source_head_id"] == self.source_head_id
                and document["source_revision"] == self.source_revision
                and document["source_capture_id"] == self.source_capture_id
                and document["source_capture_generation"]
                == self.source_capture_generation
                and document["provenance_reference"] == self.provenance_reference
                and type(document["complete_common_work_items"]) is list,
                "working_time_manifest.ledger_source_content",
            )
        _require(type(self.coverage_complete) is bool, "working_time_manifest.coverage_complete")
        entries = _canonical_records(
            self.entries,
            WorkingTimeCommonManifestEntry,
            lambda item: (item.candidate_position, item.period_key),
            "working_time_manifest.entries",
        )
        _require(
            not self.coverage_complete or all(item.coverage_complete for item in entries),
            "working_time_manifest.coverage",
        )
        object.__setattr__(self, "entries", entries)
        digest = sha256_text(working_time_source_manifest_semantic_json(self))
        object.__setattr__(self, "manifest_fingerprint", digest)
        object.__setattr__(self, "manifest_id", "m3e1-working-time-source-manifest-" + digest)


def working_time_source_manifest_semantic_json(value: WorkingTimeSourceManifest) -> str:
    return _semantic_json(value, ("manifest_fingerprint", "manifest_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkingTimeSourceSelection:
    authority_root_id: str
    authority_root_fingerprint: str
    company_id: str
    company_plan_id: str
    worker_registry_revision: int
    worker_registry_fingerprint: str
    source_kind: WorkingTimeSourceKind
    state: WorkingTimeSourceClassState
    source_head_id: str | None
    source_revision: int | None
    source_fingerprint: str | None
    source_capture_id: str | None
    source_capture_generation: int | None
    source_capture_fingerprint: str | None
    provenance_reference: str | None
    manifest_id: str | None
    manifest_fingerprint: str | None
    reason_code: str | None
    producer_classification: str = field(
        default="DEPLOYMENT_RESTRICTED_AUTH0_COMPANY_BOUND_TRUSTED_ADAPTOR",
        init=False,
    )
    schema_version: str = field(
        default=WORKING_TIME_SOURCE_SELECTION_SCHEMA_VERSION,
        init=False,
    )
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    selection_fingerprint: str = field(init=False)
    selection_id: str = field(init=False)

    def __post_init__(self) -> None:
        _root_binding(self.authority_root_id, self.authority_root_fingerprint)
        for name in ("company_id", "company_plan_id"):
            _identity(getattr(self, name), "working_time_source_selection." + name)
        _revision(
            self.worker_registry_revision,
            "working_time_source_selection.worker_registry_revision",
        )
        _digest(
            self.worker_registry_fingerprint,
            "working_time_source_selection.worker_registry_fingerprint",
        )
        _require(
            type(self.source_kind) is WorkingTimeSourceKind,
            "working_time_source_selection.source_kind",
        )
        _require(
            type(self.state) is WorkingTimeSourceClassState,
            "working_time_source_selection.state",
        )
        source_values = (
            self.source_head_id,
            self.source_revision,
            self.source_fingerprint,
            self.source_capture_id,
            self.source_capture_generation,
            self.source_capture_fingerprint,
            self.provenance_reference,
            self.manifest_id,
            self.manifest_fingerprint,
        )
        if self.state is WorkingTimeSourceClassState.UNKNOWN:
            _require(
                all(item is None for item in source_values),
                "working_time_source_selection.unknown_source",
            )
            _identity(
                self.reason_code,
                "working_time_source_selection.reason_code",
            )
        else:
            _require(
                all(item is not None for item in source_values)
                and self.reason_code is None,
                "working_time_source_selection.authoritative_source",
            )
            _identity(
                self.source_head_id,
                "working_time_source_selection.source_head_id",
            )
            _revision(
                self.source_revision,
                "working_time_source_selection.source_revision",
            )
            _digest(
                self.source_fingerprint,
                "working_time_source_selection.source_fingerprint",
            )
            _identity(
                self.source_capture_id,
                "working_time_source_selection.source_capture_id",
            )
            _revision(
                self.source_capture_generation,
                "working_time_source_selection.source_capture_generation",
            )
            _digest(
                self.source_capture_fingerprint,
                "working_time_source_selection.source_capture_fingerprint",
            )
            _identity(
                self.provenance_reference,
                "working_time_source_selection.provenance_reference",
            )
            _bound_id(
                self.manifest_id,
                self.manifest_fingerprint,
                "m3e1-working-time-source-manifest-",
                "working_time_source_selection.manifest",
            )
        digest = sha256_text(working_time_source_selection_semantic_json(self))
        object.__setattr__(self, "selection_fingerprint", digest)
        object.__setattr__(
            self,
            "selection_id",
            "m3e1-working-time-source-selection-" + digest,
        )


def working_time_source_selection_semantic_json(
    value: WorkingTimeSourceSelection,
) -> str:
    return _semantic_json(value, ("selection_fingerprint", "selection_id"))


def _working_time_ledger_source_item(value: WorkingTimeWorkItem) -> dict[str, Any]:
    return {
        "commitment_id": value.commitment_id,
        "countability_classification": value.countability_classification,
        "interval_end": _primitive(value.interval_end),
        "interval_start": _primitive(value.interval_start),
        "job_id": value.job_id,
        "operational_date": _primitive(value.operational_date),
        "provenance_reference": value.provenance_reference,
        "task_id": value.task_id,
        "work_kind": value.work_kind.value,
        "worker_id": value.worker_id,
    }


def _working_time_ledger_source_content_json(
    manifest: WorkingTimeSourceManifest,
    work_items: Iterable[WorkingTimeWorkItem],
) -> str:
    items = tuple(
        sorted(
            (_working_time_ledger_source_item(item) for item in work_items),
            key=canonical_json,
        )
    )
    return canonical_json(
        {
            "complete_common_work_items": items,
            "provenance_reference": manifest.provenance_reference,
            "schema_version": "m3e1-working-time-ledger-source-v1",
            "source_capture_generation": manifest.source_capture_generation,
            "source_capture_id": manifest.source_capture_id,
            "source_head_id": manifest.source_head_id,
            "source_revision": manifest.source_revision,
        }
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerPeriodOvertimeComparison:
    worker_id: str
    calculation_period_start: datetime
    calculation_period_end: datetime
    timezone_name: str
    rule_id: str | None
    rule_fingerprint: str | None
    baseline_work_item_ids: tuple[str, ...]
    baseline_work_item_fingerprints: tuple[str, ...]
    candidate_work_item_ids: tuple[str, ...]
    candidate_work_item_fingerprints: tuple[str, ...]
    coverage_complete: bool
    baseline_overtime: Decimal | None
    candidate_overtime: Decimal | None
    overtime_unit: str | None
    schema_version: str = field(
        default=WORKING_TIME_COMPARISON_SCHEMA_VERSION,
        init=False,
    )
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    comparison_fingerprint: str = field(init=False)
    comparison_id: str = field(init=False)

    def __post_init__(self) -> None:
        _identity(self.worker_id, "overtime_comparison.worker_id")
        start = _instant(self.calculation_period_start, "calculation_period_start")
        end = _instant(self.calculation_period_end, "calculation_period_end")
        _require(end > start, "calculation_period")
        object.__setattr__(self, "calculation_period_start", start)
        object.__setattr__(self, "calculation_period_end", end)
        _identity(self.timezone_name, "timezone_name")
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as error:
            raise OperationalAuthorityValidationError(
                "UNKNOWN_TIMEZONE", "timezone_name"
            ) from error
        _require(type(self.coverage_complete) is bool, "coverage_complete")
        for ids_name, fingerprints_name in (
            ("baseline_work_item_ids", "baseline_work_item_fingerprints"),
            ("candidate_work_item_ids", "candidate_work_item_fingerprints"),
        ):
            ids = tuple(getattr(self, ids_name))
            fingerprints = tuple(getattr(self, fingerprints_name))
            _require(len(ids) == len(fingerprints), ids_name)
            _require(len(ids) == len(set(ids)), ids_name)
            for item_id in ids:
                _identity(item_id, ids_name)
            for fingerprint in fingerprints:
                _digest(fingerprint, fingerprints_name)
            pairs = tuple(sorted(zip(ids, fingerprints, strict=True)))
            object.__setattr__(self, ids_name, tuple(item[0] for item in pairs))
            object.__setattr__(self, fingerprints_name, tuple(item[1] for item in pairs))
        if self.coverage_complete:
            _require(self.rule_id is not None and self.rule_fingerprint is not None, "rule")
            _bound_id(self.rule_id, self.rule_fingerprint, "m3e1-working-time-rule-", "rule")
            _require(
                bool(self.baseline_work_item_ids or self.candidate_work_item_ids),
                "work_item_ids",
            )
            _require(
                type(self.baseline_overtime) is Decimal
                and type(self.candidate_overtime) is Decimal,
                "overtime_amount",
            )
            _canonical_decimal(self.baseline_overtime, "baseline_overtime")
            _canonical_decimal(self.candidate_overtime, "candidate_overtime")
            _require(self.baseline_overtime >= 0 and self.candidate_overtime >= 0, "overtime_amount")
            _identity(self.overtime_unit, "overtime_unit")
        else:
            _require(
                self.baseline_overtime is None
                and self.candidate_overtime is None
                and self.overtime_unit is None,
                "incomplete_overtime_amount",
            )
            _optional_identity(self.rule_id, "rule_id")
            _optional_digest(self.rule_fingerprint, "rule_fingerprint")
            _require(
                (self.rule_id is None) == (self.rule_fingerprint is None),
                "rule_binding",
            )
        digest = sha256_text(worker_period_overtime_comparison_semantic_json(self))
        object.__setattr__(self, "comparison_fingerprint", digest)
        object.__setattr__(
            self,
            "comparison_id",
            "m3e1-worker-period-overtime-comparison-" + digest,
        )

    @property
    def period_key(self) -> str:
        return canonical_json(
            {
                "calculation_period_end": _primitive(self.calculation_period_end),
                "calculation_period_start": _primitive(self.calculation_period_start),
                "timezone_name": self.timezone_name,
                "worker_id": self.worker_id,
            }
        )

    @property
    def creates_overtime(self) -> bool:
        return bool(
            self.coverage_complete
            and self.candidate_overtime > self.baseline_overtime
        )


def worker_period_overtime_comparison_semantic_json(
    value: WorkerPeriodOvertimeComparison,
) -> str:
    return _semantic_json(value, ("comparison_fingerprint", "comparison_id"))


def _working_time_rule_payload(value: WorkingTimeRuleEvidence) -> dict[str, Any]:
    return json.loads(value.canonical_rule_payload_json)


def _local_calendar_periods(
    rule: WorkingTimeRuleEvidence,
    intervals: Iterable[tuple[datetime, datetime]],
) -> tuple[tuple[datetime, datetime], ...]:
    payload = _working_time_rule_payload(rule)
    _require(
        payload["calculation_period_kind"] == "LOCAL_CALENDAR_DAY",
        "calculation_period_kind",
    )
    zone = ZoneInfo(rule.timezone_name)
    result: set[tuple[datetime, datetime]] = set()
    for raw_start, raw_end in intervals:
        start = _instant(raw_start, "working_interval_start")
        end = _instant(raw_end, "working_interval_end")
        _require(end > start, "working_interval")
        current_date = start.astimezone(zone).date()
        final_date = (end - timedelta(seconds=1)).astimezone(zone).date()
        while current_date <= final_date:
            local_start = datetime.combine(
                current_date,
                datetime.min.time(),
                tzinfo=zone,
            )
            local_end = datetime.combine(
                current_date + timedelta(days=1),
                datetime.min.time(),
                tzinfo=zone,
            )
            result.add(
                (
                    local_start.astimezone(timezone.utc),
                    local_end.astimezone(timezone.utc),
                )
            )
            current_date += timedelta(days=1)
    return tuple(sorted(result))


def calculate_rule_overtime(
    rule: WorkingTimeRuleEvidence,
    work_items: Iterable[WorkingTimeWorkItem],
    *,
    calculation_period_start: datetime,
    calculation_period_end: datetime,
) -> Decimal:
    """Apply the selected canonical rule to one worker/period evidence side."""

    _require(
        type(rule) is WorkingTimeRuleEvidence and replace(rule) == rule,
        "working_time_rule",
    )
    period_start = _instant(calculation_period_start, "calculation_period_start")
    period_end = _instant(calculation_period_end, "calculation_period_end")
    _require(
        (period_start, period_end)
        == (rule.calculation_period_start, rule.calculation_period_end),
        "calculation_period",
    )
    items = tuple(work_items)
    _require(
        all(
            type(item) is WorkingTimeWorkItem
            and replace(item) == item
            and item.worker_id == rule.worker_id
            and item.interval_start >= period_start
            and item.interval_end <= period_end
            and item.countability_classification == "COUNTABLE"
            for item in items
        ),
        "rule_work_items",
    )
    intervals = sorted((item.interval_start, item.interval_end) for item in items)
    merged: list[tuple[datetime, datetime]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        elif end > merged[-1][1]:
            merged[-1] = (merged[-1][0], end)
    countable_seconds = sum(
        int((end - start).total_seconds()) for start, end in merged
    )
    payload = _working_time_rule_payload(rule)
    overtime_seconds = max(
        0,
        countable_seconds - payload["overtime_threshold_seconds"],
    )
    return Decimal(overtime_seconds)


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateWorkingTimeEvidence:
    candidate_position: int
    candidate_id: str
    candidate_fingerprint: str
    relevant_period_keys: tuple[str, ...]
    comparisons: tuple[WorkerPeriodOvertimeComparison, ...]
    universe_complete: bool
    schema_version: str = field(default=WORKING_TIME_CANDIDATE_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    evidence_fingerprint: str = field(init=False)
    evidence_id: str = field(init=False)

    def __post_init__(self) -> None:
        _revision(self.candidate_position, "candidate_position")
        _candidate_binding(self.candidate_id, self.candidate_fingerprint)
        _require(type(self.universe_complete) is bool, "universe_complete")
        keys = tuple(self.relevant_period_keys)
        for key in keys:
            _canonical_json_document(key, "relevant_period_key")
        _require(len(keys) == len(set(keys)), "relevant_period_keys")
        keys = tuple(sorted(keys))
        comparisons = _canonical_records(
            self.comparisons,
            WorkerPeriodOvertimeComparison,
            lambda item: item.period_key,
            "comparisons",
        )
        comparison_keys = tuple(item.period_key for item in comparisons)
        _require(
            not self.universe_complete or (bool(keys) and keys == comparison_keys),
            "relevant_period_keys",
        )
        object.__setattr__(self, "relevant_period_keys", keys)
        object.__setattr__(self, "comparisons", comparisons)
        digest = sha256_text(candidate_working_time_evidence_semantic_json(self))
        object.__setattr__(self, "evidence_fingerprint", digest)
        object.__setattr__(self, "evidence_id", "m3e1-candidate-working-time-" + digest)


def candidate_working_time_evidence_semantic_json(value: CandidateWorkingTimeEvidence) -> str:
    return _semantic_json(value, ("evidence_fingerprint", "evidence_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkingTimeEvidenceCut:
    authority_root_id: str
    authority_root_fingerprint: str
    company_id: str
    company_plan_id: str
    worker_registry_revision: int
    worker_registry_fingerprint: str
    base_plan_revision: int
    base_plan_revision_id: str
    base_plan_revision_fingerprint: str
    evaluation_input_id: str
    evaluation_input_fingerprint: str
    feasibility_support_snapshot_id: str
    feasibility_support_snapshot_fingerprint: str
    m3_result_id: str
    m3_result_fingerprint: str
    rule_evidence: tuple[WorkingTimeRuleEvidence, ...]
    work_items: tuple[WorkingTimeWorkItem, ...]
    source_selections: tuple[WorkingTimeSourceSelection, ...]
    common_work_manifests: tuple[WorkingTimeSourceManifest, ...]
    candidate_evidence: tuple[CandidateWorkingTimeEvidence, ...]
    source_head_observations: tuple[EvidenceBinding, ...]
    producer_classification: str = field(
        default="DEPLOYMENT_RESTRICTED_AUTH0_COMPANY_BOUND_TRUSTED_ADAPTOR",
        init=False,
    )
    schema_version: str = field(default=WORKING_TIME_CUT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    cut_fingerprint: str = field(init=False)
    cut_id: str = field(init=False)

    def __post_init__(self) -> None:
        _root_binding(self.authority_root_id, self.authority_root_fingerprint)
        for name in ("company_id", "company_plan_id"):
            _identity(getattr(self, name), name)
        _revision(self.worker_registry_revision, "worker_registry_revision")
        _digest(self.worker_registry_fingerprint, "worker_registry_fingerprint")
        _revision(self.base_plan_revision, "base_plan_revision")
        _bound_id(self.base_plan_revision_id, self.base_plan_revision_fingerprint, "m3-plan-revision-", "base_plan_revision")
        _bound_id(self.evaluation_input_id, self.evaluation_input_fingerprint, "m3-evaluation-input-", "evaluation_input")
        _bound_id(self.feasibility_support_snapshot_id, self.feasibility_support_snapshot_fingerprint, "m3-feasibility-support-", "feasibility_support_snapshot")
        _bound_id(self.m3_result_id, self.m3_result_fingerprint, "m3-feasibility-result-", "m3_result")
        rules = _canonical_records(
            self.rule_evidence,
            WorkingTimeRuleEvidence,
            lambda item: (item.worker_id, item.calculation_period_start, item.calculation_period_end, item.rule_id),
            "rule_evidence",
        )
        _require(
            all(
                (
                    item.authority_root_id,
                    item.authority_root_fingerprint,
                    item.company_id,
                    item.company_plan_id,
                    item.worker_registry_revision,
                    item.worker_registry_fingerprint,
                )
                == (
                    self.authority_root_id,
                    self.authority_root_fingerprint,
                    self.company_id,
                    self.company_plan_id,
                    self.worker_registry_revision,
                    self.worker_registry_fingerprint,
                )
                for item in rules
            ),
            "working_time_rule_authority_binding",
        )
        items = _canonical_records(
            self.work_items,
            WorkingTimeWorkItem,
            lambda item: item.work_item_id,
            "work_items",
        )
        selections = _canonical_records(
            self.source_selections,
            WorkingTimeSourceSelection,
            lambda item: item.source_kind.value,
            "working_time_source_selections",
        )
        _require(
            {item.source_kind for item in selections} == set(WorkingTimeSourceKind),
            "working_time_source_selection_universe",
        )
        _require(
            all(
                (
                    item.authority_root_id,
                    item.authority_root_fingerprint,
                    item.company_id,
                    item.company_plan_id,
                    item.worker_registry_revision,
                    item.worker_registry_fingerprint,
                )
                == (
                    self.authority_root_id,
                    self.authority_root_fingerprint,
                    self.company_id,
                    self.company_plan_id,
                    self.worker_registry_revision,
                    self.worker_registry_fingerprint,
                )
                for item in selections
            ),
            "working_time_source_selection_authority_binding",
        )
        selections_by_kind = {item.source_kind: item for item in selections}
        _require(
            selections_by_kind[WorkingTimeSourceKind.C0_PLAN_SCHEDULE].state
            is WorkingTimeSourceClassState.PRESENT,
            "working_time_c0_source_selection",
        )
        manifests = _canonical_records(
            self.common_work_manifests,
            WorkingTimeSourceManifest,
            lambda item: item.source_kind.value,
            "common_work_manifests",
        )
        _require(
            all(
                (
                    item.authority_root_id,
                    item.authority_root_fingerprint,
                    item.company_id,
                    item.company_plan_id,
                    item.worker_registry_revision,
                    item.worker_registry_fingerprint,
                )
                == (
                    self.authority_root_id,
                    self.authority_root_fingerprint,
                    self.company_id,
                    self.company_plan_id,
                    self.worker_registry_revision,
                    self.worker_registry_fingerprint,
                )
                for item in manifests
            ),
            "working_time_manifest_authority_binding",
        )
        manifests_by_kind = {item.source_kind: item for item in manifests}
        _require(
            set(manifests_by_kind)
            == {
                item.source_kind
                for item in selections
                if item.state is not WorkingTimeSourceClassState.UNKNOWN
            },
            "working_time_source_selection_manifest_coverage",
        )
        for selection in selections:
            manifest = manifests_by_kind.get(selection.source_kind)
            if selection.state is WorkingTimeSourceClassState.UNKNOWN:
                _require(manifest is None, "working_time_unknown_source_manifest")
                continue
            _require(
                manifest is not None
                and manifest.coverage_complete
                and (
                    selection.source_head_id,
                    selection.source_revision,
                    selection.source_fingerprint,
                    selection.source_capture_id,
                    selection.source_capture_generation,
                    selection.source_capture_fingerprint,
                    selection.provenance_reference,
                    selection.manifest_id,
                    selection.manifest_fingerprint,
                )
                == (
                    manifest.source_head_id,
                    manifest.source_revision,
                    manifest.source_fingerprint,
                    manifest.source_capture_id,
                    manifest.source_capture_generation,
                    manifest.source_capture_fingerprint,
                    manifest.provenance_reference,
                    manifest.manifest_id,
                    manifest.manifest_fingerprint,
                ),
                "working_time_source_selection_manifest_binding",
            )
        candidates = _ordered_records(self.candidate_evidence, CandidateWorkingTimeEvidence, "candidate_evidence")
        _require(
            tuple(item.candidate_position for item in candidates) == tuple(range(len(candidates))),
            "candidate_evidence",
        )
        heads = _canonical_records(
            self.source_head_observations,
            EvidenceBinding,
            lambda item: (item.kind, item.record_id),
            "source_head_observations",
        )
        _require(
            {
                (item.kind, item.record_id, item.fingerprint)
                for item in heads
            }
            == {
                (
                    "WORKING_TIME_SOURCE_MANIFEST",
                    item.manifest_id,
                    item.manifest_fingerprint,
                )
                for item in manifests
            },
            "working_time_source_head_manifest_binding",
        )
        rule_by_id = {item.rule_id: item for item in rules}
        item_by_id = {item.work_item_id: item for item in items}
        candidate_by_position = {
            item.candidate_position: item for item in candidates
        }
        comparison_keys = {
            (candidate.candidate_position, comparison.period_key)
            for candidate in candidates
            for comparison in candidate.comparisons
        }
        manifest_common_by_key: dict[tuple[int, str], set[str]] = {}
        common_item_manifest: dict[str, str] = {}
        manifest_item_ids: dict[str, set[str]] = {
            manifest.manifest_id: set() for manifest in manifests
        }
        for manifest in manifests:
            for entry in manifest.entries:
                candidate = candidate_by_position.get(entry.candidate_position)
                _require(
                    candidate is not None
                    and (
                        entry.candidate_id,
                        entry.candidate_fingerprint,
                    )
                    == (
                        candidate.candidate_id,
                        candidate.candidate_fingerprint,
                    )
                    and (entry.candidate_position, entry.period_key)
                    in comparison_keys,
                    "working_time_manifest_candidate_period_binding",
                )
                key = (entry.candidate_position, entry.period_key)
                selected = manifest_common_by_key.setdefault(key, set())
                for item_id, fingerprint in zip(
                    entry.common_work_item_ids,
                    entry.common_work_item_fingerprints,
                    strict=True,
                ):
                    work_item = item_by_id.get(item_id)
                    _require(
                        work_item is not None
                        and work_item.work_item_fingerprint == fingerprint
                        and work_item.work_kind
                        in (
                            WorkingTimeWorkKind.COMPLETED_COUNTABLE,
                            WorkingTimeWorkKind.UNCHANGED_SCHEDULED,
                        )
                        and work_item.worker_id == entry.worker_id
                        and work_item.interval_start >= entry.calculation_period_start
                        and work_item.interval_end <= entry.calculation_period_end
                        and work_item.source_record_id == manifest.source_head_id
                        and work_item.source_revision == manifest.source_revision
                        and work_item.source_fingerprint == manifest.source_fingerprint
                        and work_item.source_capture_id == manifest.source_capture_id
                        and work_item.source_capture_generation
                        == manifest.source_capture_generation
                        and work_item.source_capture_fingerprint
                        == manifest.source_capture_fingerprint,
                        "working_time_manifest_work_item_binding",
                    )
                    _require(
                        item_id not in common_item_manifest
                        or common_item_manifest[item_id] == manifest.manifest_id,
                        "working_time_common_item_source_ambiguity",
                    )
                    common_item_manifest[item_id] = manifest.manifest_id
                    manifest_item_ids[manifest.manifest_id].add(item_id)
                    selected.add(item_id)
        for manifest in manifests:
            if manifest.source_kind is WorkingTimeSourceKind.WORKING_TIME_LEDGER:
                _require(
                    manifest.canonical_source_content_json
                    == _working_time_ledger_source_content_json(
                        manifest,
                        (
                            item_by_id[item_id]
                            for item_id in sorted(manifest_item_ids[manifest.manifest_id])
                        ),
                    ),
                    "working_time_ledger_source_content_binding",
                )
                ledger_selection = selections_by_kind[
                    WorkingTimeSourceKind.WORKING_TIME_LEDGER
                ]
                _require(
                    (
                        ledger_selection.state
                        is WorkingTimeSourceClassState.AUTHORITATIVELY_EMPTY
                        and not manifest_item_ids[manifest.manifest_id]
                    )
                    or (
                        ledger_selection.state
                        is WorkingTimeSourceClassState.PRESENT
                        and bool(manifest_item_ids[manifest.manifest_id])
                    ),
                    "working_time_ledger_source_state",
                )
        source_universe_complete = all(
            item.state is not WorkingTimeSourceClassState.UNKNOWN
            for item in selections
        )
        _require(
            source_universe_complete
            or not any(item.universe_complete for item in candidates),
            "working_time_source_universe_complete",
        )
        referenced_item_ids: set[str] = set()
        referenced_rule_ids: set[str] = set()
        for candidate in candidates:
            for comparison in candidate.comparisons:
                rule = None
                baseline_items: list[WorkingTimeWorkItem] = []
                candidate_items: list[WorkingTimeWorkItem] = []
                if comparison.rule_id is not None:
                    rule = rule_by_id.get(comparison.rule_id)
                    _require(
                        rule is not None
                        and rule.rule_fingerprint == comparison.rule_fingerprint
                        and rule.worker_id == comparison.worker_id
                        and rule.calculation_period_start == comparison.calculation_period_start
                        and rule.calculation_period_end == comparison.calculation_period_end
                        and rule.timezone_name == comparison.timezone_name,
                        "comparison_rule_binding",
                    )
                    referenced_rule_ids.add(comparison.rule_id)
                for ids_name, fps_name in (
                    ("baseline_work_item_ids", "baseline_work_item_fingerprints"),
                    ("candidate_work_item_ids", "candidate_work_item_fingerprints"),
                ):
                    selected_items = (
                        baseline_items
                        if ids_name == "baseline_work_item_ids"
                        else candidate_items
                    )
                    for item_id, fingerprint in zip(
                        getattr(comparison, ids_name),
                        getattr(comparison, fps_name),
                        strict=True,
                    ):
                        work_item = item_by_id.get(item_id)
                        referenced_item_ids.add(item_id)
                        _require(
                            work_item is not None
                            and work_item.work_item_fingerprint == fingerprint
                            and work_item.worker_id == comparison.worker_id,
                            "comparison_work_item_binding",
                        )
                        if ids_name == "baseline_work_item_ids":
                            _require(
                                work_item.work_kind
                                is not WorkingTimeWorkKind.PROPOSED_CANDIDATE,
                                "baseline_work_item_kind",
                            )
                        else:
                            _require(
                                work_item.work_kind
                                is not WorkingTimeWorkKind.BASELINE_CHANGED,
                                "candidate_work_item_kind",
                            )
                        if work_item.work_kind is WorkingTimeWorkKind.PROPOSED_CANDIDATE:
                            _require(
                                work_item.candidate_id == candidate.candidate_id,
                                "candidate_work_item_binding",
                            )
                        selected_items.append(work_item)
                common_kinds = (
                    WorkingTimeWorkKind.COMPLETED_COUNTABLE,
                    WorkingTimeWorkKind.UNCHANGED_SCHEDULED,
                )
                baseline_common = {
                    item.work_item_id
                    for item in baseline_items
                    if item.work_kind in common_kinds
                }
                candidate_common = {
                    item.work_item_id
                    for item in candidate_items
                    if item.work_kind in common_kinds
                }
                _require(
                    not comparison.coverage_complete
                    or baseline_common == candidate_common,
                    "unchanged_work_universe",
                )
                manifest_common = manifest_common_by_key.get(
                    (candidate.candidate_position, comparison.period_key),
                    set(),
                )
                _require(
                    baseline_common.issubset(manifest_common)
                    and candidate_common.issubset(manifest_common),
                    "working_time_common_manifest_subset",
                )
                if comparison.coverage_complete:
                    _require(
                        manifests
                        and all(manifest.coverage_complete for manifest in manifests)
                        and all(
                            any(
                                entry.candidate_position == candidate.candidate_position
                                and entry.period_key == comparison.period_key
                                and entry.coverage_complete
                                for entry in manifest.entries
                            )
                            for manifest in manifests
                        )
                        and baseline_common == manifest_common
                        and candidate_common == manifest_common,
                        "working_time_common_manifest_complete",
                    )
                    _require(rule is not None, "comparison_rule_binding")
                    _require(
                        comparison.baseline_overtime
                        == calculate_rule_overtime(
                            rule,
                            baseline_items,
                            calculation_period_start=comparison.calculation_period_start,
                            calculation_period_end=comparison.calculation_period_end,
                        )
                        and comparison.candidate_overtime
                        == calculate_rule_overtime(
                            rule,
                            candidate_items,
                            calculation_period_start=comparison.calculation_period_start,
                            calculation_period_end=comparison.calculation_period_end,
                        )
                        and comparison.overtime_unit == "SECONDS",
                        "derived_overtime_amount",
                    )
        _require(
            referenced_item_ids == set(item_by_id),
            "working_time_item_universe",
        )
        _require(
            referenced_rule_ids == set(rule_by_id),
            "working_time_rule_universe",
        )
        _require(
            {
                item.work_item_id
                for item in items
                if item.work_kind
                in (
                    WorkingTimeWorkKind.COMPLETED_COUNTABLE,
                    WorkingTimeWorkKind.UNCHANGED_SCHEDULED,
                )
            }
            == set(common_item_manifest),
            "working_time_common_manifest_item_universe",
        )
        object.__setattr__(self, "rule_evidence", rules)
        object.__setattr__(self, "work_items", items)
        object.__setattr__(self, "source_selections", selections)
        object.__setattr__(self, "common_work_manifests", manifests)
        object.__setattr__(self, "candidate_evidence", candidates)
        object.__setattr__(self, "source_head_observations", heads)
        digest = sha256_text(working_time_evidence_cut_semantic_json(self))
        object.__setattr__(self, "cut_fingerprint", digest)
        object.__setattr__(self, "cut_id", "m3e1-working-time-cut-" + digest)


def working_time_evidence_cut_semantic_json(value: WorkingTimeEvidenceCut) -> str:
    return _semantic_json(value, ("cut_fingerprint", "cut_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateOvertimeAssessment:
    candidate_position: int
    candidate_id: str
    candidate_fingerprint: str
    working_time_cut_id: str
    working_time_cut_fingerprint: str
    candidate_working_time_evidence_id: str
    candidate_working_time_evidence_fingerprint: str
    relevant_period_keys: tuple[str, ...]
    comparisons: tuple[WorkerPeriodOvertimeComparison, ...]
    universe_complete: bool
    schema_version: str = field(default=OVERTIME_ASSESSMENT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    applicability: OvertimeApplicability = field(init=False)
    assessment_fingerprint: str = field(init=False)
    assessment_id: str = field(init=False)

    def __post_init__(self) -> None:
        _revision(self.candidate_position, "candidate_position")
        _candidate_binding(self.candidate_id, self.candidate_fingerprint)
        _bound_id(self.working_time_cut_id, self.working_time_cut_fingerprint, "m3e1-working-time-cut-", "working_time_cut")
        _bound_id(self.candidate_working_time_evidence_id, self.candidate_working_time_evidence_fingerprint, "m3e1-candidate-working-time-", "candidate_working_time_evidence")
        keys = tuple(self.relevant_period_keys)
        comparisons = _canonical_records(
            self.comparisons,
            WorkerPeriodOvertimeComparison,
            lambda item: item.period_key,
            "comparisons",
        )
        complete = (
            self.universe_complete
            and bool(keys)
            and keys == tuple(item.period_key for item in comparisons)
            and all(item.coverage_complete for item in comparisons)
        )
        if not complete:
            applicability = OvertimeApplicability.UNKNOWN
        elif any(item.creates_overtime for item in comparisons):
            applicability = OvertimeApplicability.CREATES_OVERTIME
        else:
            applicability = OvertimeApplicability.DOES_NOT_CREATE_OVERTIME
        object.__setattr__(self, "relevant_period_keys", keys)
        object.__setattr__(self, "comparisons", comparisons)
        object.__setattr__(self, "applicability", applicability)
        digest = sha256_text(candidate_overtime_assessment_semantic_json(self))
        object.__setattr__(self, "assessment_fingerprint", digest)
        object.__setattr__(self, "assessment_id", "m3e1-overtime-assessment-" + digest)


def candidate_overtime_assessment_semantic_json(value: CandidateOvertimeAssessment) -> str:
    return _semantic_json(value, ("assessment_fingerprint", "assessment_id"))


def assess_candidate_overtime(
    candidate_position: int,
    candidate: M3RepairCandidate,
    working_time_cut: WorkingTimeEvidenceCut,
) -> CandidateOvertimeAssessment:
    _require(type(candidate) is M3RepairCandidate and replace(candidate) == candidate, "candidate")
    evidence = next(
        (
            item
            for item in working_time_cut.candidate_evidence
            if item.candidate_position == candidate_position
            and item.candidate_id == candidate.candidate_id
            and item.candidate_fingerprint == candidate.candidate_fingerprint
        ),
        None,
    )
    _require(evidence is not None, "candidate_working_time_evidence")
    return CandidateOvertimeAssessment(
        candidate_position=candidate_position,
        candidate_id=candidate.candidate_id,
        candidate_fingerprint=candidate.candidate_fingerprint,
        working_time_cut_id=working_time_cut.cut_id,
        working_time_cut_fingerprint=working_time_cut.cut_fingerprint,
        candidate_working_time_evidence_id=evidence.evidence_id,
        candidate_working_time_evidence_fingerprint=evidence.evidence_fingerprint,
        relevant_period_keys=evidence.relevant_period_keys,
        comparisons=evidence.comparisons,
        universe_complete=evidence.universe_complete,
    )


def _working_time_rule_family_json(value: WorkingTimeRuleEvidence) -> str:
    document = _primitive(value)
    for name in (
        "calculation_period_start",
        "calculation_period_end",
        "rule_fingerprint",
        "rule_id",
    ):
        document.pop(name)
    return canonical_json(document)


def _working_period_key(
    worker_id: str,
    period_start: datetime,
    period_end: datetime,
    timezone_name: str,
) -> str:
    return canonical_json(
        {
            "calculation_period_end": _primitive(period_end),
            "calculation_period_start": _primitive(period_start),
            "timezone_name": timezone_name,
            "worker_id": worker_id,
        }
    )


def _work_item_binding(value: WorkingTimeWorkItem) -> tuple[Any, ...]:
    return (
        value.worker_id,
        value.work_kind,
        value.commitment_id,
        value.job_id,
        value.task_id,
        value.operational_date,
        value.candidate_id,
        value.candidate_fingerprint,
        value.interval_start,
        value.interval_end,
        value.countability_classification,
        value.source_record_id,
        value.source_revision,
        value.source_fingerprint,
        value.source_capture_id,
        value.source_capture_generation,
        value.source_capture_fingerprint,
        value.provenance_reference,
    )


def _expected_changed_work_binding(
    *,
    worker_id: str,
    work_kind: WorkingTimeWorkKind,
    commitment_id: str,
    job_id: str,
    task_id: str,
    operational_date: date,
    candidate: M3RepairCandidate,
    support_snapshot: FeasibilitySupportSnapshot,
    m3_result: M3BoundedFeasibilityResult,
    interval_start: datetime,
    interval_end: datetime,
) -> tuple[Any, ...]:
    if work_kind is WorkingTimeWorkKind.BASELINE_CHANGED:
        candidate_id = None
        candidate_fingerprint = None
        source_record_id = support_snapshot.schedule.source_record_id
        source_revision = support_snapshot.schedule.source_revision
        source_fingerprint = support_snapshot.schedule.source_fingerprint
        source_capture_id = support_snapshot.source_cut_id
        source_capture_generation = support_snapshot.source_cut_generation
        source_capture_fingerprint = support_snapshot.source_cut_fingerprint
        provenance_reference = support_snapshot.schedule.provenance_reference
    else:
        candidate_id = candidate.candidate_id
        candidate_fingerprint = candidate.candidate_fingerprint
        source_record_id = candidate.candidate_id
        source_revision = 0
        source_fingerprint = candidate.candidate_fingerprint
        source_capture_id = m3_result.result_id
        source_capture_generation = 0
        source_capture_fingerprint = m3_result.result_fingerprint
        provenance_reference = "M3_C_CANONICAL_PROPOSED_PLACEMENT"
    return (
        worker_id,
        work_kind,
        commitment_id,
        job_id,
        task_id,
        operational_date,
        candidate_id,
        candidate_fingerprint,
        interval_start,
        interval_end,
        "COUNTABLE",
        source_record_id,
        source_revision,
        source_fingerprint,
        source_capture_id,
        source_capture_generation,
        source_capture_fingerprint,
        provenance_reference,
    )


def _expected_c0_common_work_binding(
    *,
    worker_id: str,
    placement: ScheduledPlacementEvidence,
    support_snapshot: FeasibilitySupportSnapshot,
    interval_start: datetime,
    interval_end: datetime,
) -> tuple[Any, ...]:
    return (
        worker_id,
        WorkingTimeWorkKind.UNCHANGED_SCHEDULED,
        placement.commitment_id,
        placement.job_id,
        placement.task_id,
        placement.business_date,
        None,
        None,
        interval_start,
        interval_end,
        "COUNTABLE",
        support_snapshot.schedule.source_record_id,
        support_snapshot.schedule.source_revision,
        support_snapshot.schedule.source_fingerprint,
        support_snapshot.source_cut_id,
        support_snapshot.source_cut_generation,
        support_snapshot.source_cut_fingerprint,
        support_snapshot.schedule.provenance_reference,
    )


def _validate_working_time_cut_against_candidates(
    value: WorkingTimeEvidenceCut,
    support_snapshot: FeasibilitySupportSnapshot,
    m3_result: M3BoundedFeasibilityResult,
) -> None:
    baseline_by_id = {
        item.commitment_id: item for item in support_snapshot.schedule.placements
    }
    rules_by_worker: dict[str, list[WorkingTimeRuleEvidence]] = {}
    rules_by_period: dict[tuple[str, datetime, datetime], WorkingTimeRuleEvidence] = {}
    for rule in value.rule_evidence:
        rules_by_worker.setdefault(rule.worker_id, []).append(rule)
        period_key = (
            rule.worker_id,
            rule.calculation_period_start,
            rule.calculation_period_end,
        )
        _require(period_key not in rules_by_period, "working_time_rule_period")
        rules_by_period[period_key] = rule
    item_by_id = {item.work_item_id: item for item in value.work_items}
    c0_manifests = tuple(
        item
        for item in value.common_work_manifests
        if item.source_kind is WorkingTimeSourceKind.C0_PLAN_SCHEDULE
    )
    _require(len(c0_manifests) <= 1, "working_time_c0_manifest")
    if c0_manifests:
        c0_manifest = c0_manifests[0]
        _require(
            (
                c0_manifest.source_head_id,
                c0_manifest.source_revision,
                c0_manifest.source_fingerprint,
                c0_manifest.source_capture_id,
                c0_manifest.source_capture_generation,
                c0_manifest.source_capture_fingerprint,
                c0_manifest.provenance_reference,
                c0_manifest.canonical_source_content_json,
            )
            == (
                support_snapshot.schedule.source_record_id,
                support_snapshot.schedule.source_revision,
                support_snapshot.schedule.source_fingerprint,
                support_snapshot.source_cut_id,
                support_snapshot.source_cut_generation,
                support_snapshot.source_cut_fingerprint,
                support_snapshot.schedule.provenance_reference,
                feasibility_source_semantic_json(support_snapshot.schedule),
            ),
            "working_time_c0_manifest_source_binding",
        )

    for position, candidate in enumerate(m3_result.candidates):
        evidence = value.candidate_evidence[position]
        proposed_by_id = {
            item.commitment_id: item for item in candidate.proposed_worker_placements
        }
        intervals_by_worker: dict[str, list[tuple[datetime, datetime]]] = {}
        for commitment_id in candidate.modified_commitment_ids:
            baseline = baseline_by_id.get(commitment_id)
            proposed = proposed_by_id.get(commitment_id)
            _require(
                baseline is not None
                and proposed is not None
                and (
                    baseline.commitment_id,
                    baseline.job_id,
                    baseline.task_id,
                )
                == (
                    proposed.commitment_id,
                    proposed.job_id,
                    proposed.task_id,
                ),
                "working_time_modified_commitment_binding",
            )
            for worker_id in baseline.worker_ids:
                intervals_by_worker.setdefault(worker_id, []).append(
                    (baseline.planned_start, baseline.planned_end)
                )
            intervals_by_worker.setdefault(proposed.worker_id, []).append(
                (proposed.proposed_start, proposed.proposed_end)
            )

        expected_periods: dict[str, tuple[str, datetime, datetime, str]] = {}
        workers_without_rules: set[str] = set()
        for worker_id, intervals in intervals_by_worker.items():
            worker_rules = sorted(
                rules_by_worker.get(worker_id, ()),
                key=lambda item: (
                    item.calculation_period_start,
                    item.calculation_period_end,
                ),
            )
            if not worker_rules:
                workers_without_rules.add(worker_id)
                continue
            family = _working_time_rule_family_json(worker_rules[0])
            _require(
                all(_working_time_rule_family_json(item) == family for item in worker_rules),
                "working_time_rule_family",
            )
            for period_start, period_end in _local_calendar_periods(
                worker_rules[0],
                intervals,
            ):
                key = _working_period_key(
                    worker_id,
                    period_start,
                    period_end,
                    worker_rules[0].timezone_name,
                )
                expected_periods[key] = (
                    worker_id,
                    period_start,
                    period_end,
                    worker_rules[0].timezone_name,
                )

        actual_by_key = {item.period_key: item for item in evidence.comparisons}
        _require(
            set(evidence.relevant_period_keys).issubset(expected_periods)
            and set(actual_by_key).issubset(expected_periods)
            and set(actual_by_key).issubset(evidence.relevant_period_keys),
            "working_time_period_universe",
        )
        if evidence.universe_complete:
            _require(not workers_without_rules, "working_time_worker_rule_coverage")
            _require(
                len(c0_manifests) == 1 and c0_manifests[0].coverage_complete,
                "working_time_c0_manifest_complete",
            )
            _require(
                set(evidence.relevant_period_keys) == set(expected_periods)
                and set(actual_by_key) == set(expected_periods),
                "working_time_period_universe",
            )

        for period_key, comparison in actual_by_key.items():
            worker_id, period_start, period_end, timezone_name = expected_periods[
                period_key
            ]
            selected_rule = rules_by_period.get(
                (worker_id, period_start, period_end)
            )
            _require(
                selected_rule is not None
                and comparison.rule_id == selected_rule.rule_id
                and comparison.rule_fingerprint == selected_rule.rule_fingerprint
                and comparison.timezone_name == timezone_name,
                "working_time_selected_rule",
            )
            expected_baseline: set[tuple[Any, ...]] = set()
            expected_candidate: set[tuple[Any, ...]] = set()
            for commitment_id in candidate.modified_commitment_ids:
                baseline = baseline_by_id[commitment_id]
                proposed = proposed_by_id[commitment_id]
                if worker_id in baseline.worker_ids:
                    start = max(baseline.planned_start, period_start)
                    end = min(baseline.planned_end, period_end)
                    if end > start:
                        expected_baseline.add(
                            _expected_changed_work_binding(
                                worker_id=worker_id,
                                work_kind=WorkingTimeWorkKind.BASELINE_CHANGED,
                                commitment_id=baseline.commitment_id,
                                job_id=baseline.job_id,
                                task_id=baseline.task_id,
                                operational_date=baseline.business_date,
                                candidate=candidate,
                                support_snapshot=support_snapshot,
                                m3_result=m3_result,
                                interval_start=start,
                                interval_end=end,
                            )
                        )
                if worker_id == proposed.worker_id:
                    start = max(proposed.proposed_start, period_start)
                    end = min(proposed.proposed_end, period_end)
                    if end > start:
                        expected_candidate.add(
                            _expected_changed_work_binding(
                                worker_id=worker_id,
                                work_kind=WorkingTimeWorkKind.PROPOSED_CANDIDATE,
                                commitment_id=proposed.commitment_id,
                                job_id=proposed.job_id,
                                task_id=proposed.task_id,
                                operational_date=proposed.business_date,
                                candidate=candidate,
                                support_snapshot=support_snapshot,
                                m3_result=m3_result,
                                interval_start=start,
                                interval_end=end,
                            )
                        )
            baseline_items = tuple(
                item_by_id[item_id] for item_id in comparison.baseline_work_item_ids
            )
            candidate_items = tuple(
                item_by_id[item_id] for item_id in comparison.candidate_work_item_ids
            )
            actual_baseline = {
                _work_item_binding(item)
                for item in baseline_items
                if item.work_kind is WorkingTimeWorkKind.BASELINE_CHANGED
            }
            actual_candidate = {
                _work_item_binding(item)
                for item in candidate_items
                if item.work_kind is WorkingTimeWorkKind.PROPOSED_CANDIDATE
            }
            _require(
                actual_baseline.issubset(expected_baseline)
                and actual_candidate.issubset(expected_candidate),
                "working_time_changed_placement_binding",
            )
            expected_c0_common = {
                _expected_c0_common_work_binding(
                    worker_id=worker_id,
                    placement=placement,
                    support_snapshot=support_snapshot,
                    interval_start=max(placement.planned_start, period_start),
                    interval_end=min(placement.planned_end, period_end),
                )
                for placement in support_snapshot.schedule.placements
                if placement.commitment_id not in candidate.modified_commitment_ids
                and worker_id in placement.worker_ids
                and min(placement.planned_end, period_end)
                > max(placement.planned_start, period_start)
            }
            actual_c0_common = {
                _work_item_binding(item)
                for item in baseline_items
                if item.work_kind is WorkingTimeWorkKind.UNCHANGED_SCHEDULED
                and (
                    item.source_record_id,
                    item.source_revision,
                    item.source_fingerprint,
                    item.source_capture_id,
                    item.source_capture_generation,
                    item.source_capture_fingerprint,
                    item.provenance_reference,
                )
                == (
                    support_snapshot.schedule.source_record_id,
                    support_snapshot.schedule.source_revision,
                    support_snapshot.schedule.source_fingerprint,
                    support_snapshot.source_cut_id,
                    support_snapshot.source_cut_generation,
                    support_snapshot.source_cut_fingerprint,
                    support_snapshot.schedule.provenance_reference,
                )
            }
            _require(
                actual_c0_common.issubset(expected_c0_common),
                "working_time_c0_common_binding",
            )
            if evidence.universe_complete and comparison.coverage_complete:
                _require(
                    actual_baseline == expected_baseline
                    and actual_candidate == expected_candidate,
                    "working_time_changed_placement_coverage",
                )
                _require(
                    actual_c0_common == expected_c0_common,
                    "working_time_c0_common_coverage",
                )


def _marker_resource_subject_id(kind: MarkerSubjectKind, resource_id: str) -> str:
    _require(kind in (MarkerSubjectKind.WORKER, MarkerSubjectKind.VEHICLE), "resource_kind")
    _identity(resource_id, "resource_id")
    document = canonical_json(
        {
            "resource_id": resource_id,
            "resource_kind": kind.value,
            "rule_version": MARKER_RESOURCE_SUBJECT_RULE_VERSION,
            "schema_version": MARKER_RESOURCE_SUBJECT_SCHEMA_VERSION,
        }
    )
    return "m3e1-authority-marker-resource-subject-" + sha256_text(document)


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorityMarkerSubject:
    subject_kind: MarkerSubjectKind
    evidence_subject_type: EvidenceSubjectType
    subject_id: str
    commitment_id: str | None
    resource_id: str | None
    job_id: str
    task_id: str
    operational_date: date
    schema_version: str = field(default=MARKER_SUBJECT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    subject_fingerprint: str = field(init=False)
    marker_subject_id: str = field(init=False)

    def __post_init__(self) -> None:
        _require(type(self.subject_kind) is MarkerSubjectKind, "subject_kind")
        _require(type(self.evidence_subject_type) is EvidenceSubjectType, "evidence_subject_type")
        for name in ("subject_id", "job_id", "task_id"):
            _identity(getattr(self, name), "marker_subject." + name)
        _require(type(self.operational_date) is date, "marker_subject.operational_date")
        if self.subject_kind is MarkerSubjectKind.COMMITMENT:
            _require(
                self.evidence_subject_type is EvidenceSubjectType.COMMITMENT
                and self.commitment_id == self.subject_id
                and self.resource_id is None,
                "commitment_marker_subject",
            )
            _identity(self.commitment_id, "marker_subject.commitment_id")
        else:
            _require(
                self.evidence_subject_type is EvidenceSubjectType.RESOURCE
                and self.commitment_id is None
                and self.resource_id is not None,
                "resource_marker_subject",
            )
            _identity(self.resource_id, "marker_subject.resource_id")
            _require(
                self.subject_id
                == _marker_resource_subject_id(self.subject_kind, self.resource_id),
                "marker_subject.subject_id",
            )
        digest = sha256_text(authority_marker_subject_semantic_json(self))
        object.__setattr__(self, "subject_fingerprint", digest)
        object.__setattr__(self, "marker_subject_id", "m3e1-marker-subject-" + digest)


def authority_marker_subject_semantic_json(value: AuthorityMarkerSubject) -> str:
    return _semantic_json(value, ("subject_fingerprint", "marker_subject_id"))


def _commitment_marker_subject(
    *, commitment_id: str, job_id: str, task_id: str, business_date: date
) -> AuthorityMarkerSubject:
    return AuthorityMarkerSubject(
        subject_kind=MarkerSubjectKind.COMMITMENT,
        evidence_subject_type=EvidenceSubjectType.COMMITMENT,
        subject_id=commitment_id,
        commitment_id=commitment_id,
        resource_id=None,
        job_id=job_id,
        task_id=task_id,
        operational_date=business_date,
    )


def _resource_marker_subject(
    *,
    kind: MarkerSubjectKind,
    resource_id: str,
    job_id: str,
    task_id: str,
    business_date: date,
) -> AuthorityMarkerSubject:
    return AuthorityMarkerSubject(
        subject_kind=kind,
        evidence_subject_type=EvidenceSubjectType.RESOURCE,
        subject_id=_marker_resource_subject_id(kind, resource_id),
        commitment_id=None,
        resource_id=resource_id,
        job_id=job_id,
        task_id=task_id,
        operational_date=business_date,
    )


def _subjects_for_baseline_placement(
    placement: ScheduledPlacementEvidence,
) -> tuple[AuthorityMarkerSubject, ...]:
    values = [
        _commitment_marker_subject(
            commitment_id=placement.commitment_id,
            job_id=placement.job_id,
            task_id=placement.task_id,
            business_date=placement.business_date,
        )
    ]
    values.extend(
        _resource_marker_subject(
            kind=MarkerSubjectKind.WORKER,
            resource_id=worker_id,
            job_id=placement.job_id,
            task_id=placement.task_id,
            business_date=placement.business_date,
        )
        for worker_id in placement.worker_ids
    )
    if placement.vehicle_id is not None:
        values.append(
            _resource_marker_subject(
                kind=MarkerSubjectKind.VEHICLE,
                resource_id=placement.vehicle_id,
                job_id=placement.job_id,
                task_id=placement.task_id,
                business_date=placement.business_date,
            )
        )
    return tuple(values)


def _subjects_for_proposed_placement(placement) -> tuple[AuthorityMarkerSubject, ...]:
    values = [
        _commitment_marker_subject(
            commitment_id=placement.commitment_id,
            job_id=placement.job_id,
            task_id=placement.task_id,
            business_date=placement.business_date,
        ),
        _resource_marker_subject(
            kind=MarkerSubjectKind.WORKER,
            resource_id=placement.worker_id,
            job_id=placement.job_id,
            task_id=placement.task_id,
            business_date=placement.business_date,
        ),
    ]
    if placement.vehicle_id is not None:
        values.append(
            _resource_marker_subject(
                kind=MarkerSubjectKind.VEHICLE,
                resource_id=placement.vehicle_id,
                job_id=placement.job_id,
                task_id=placement.task_id,
                business_date=placement.business_date,
            )
        )
    return tuple(values)


def derive_authority_marker_subjects(
    candidate: M3RepairCandidate,
    support_snapshot: FeasibilitySupportSnapshot,
) -> tuple[AuthorityMarkerSubject, ...]:
    """Derive exactly ADR 0014's modified/candidate-induced subject universe."""

    _require(type(candidate) is M3RepairCandidate and replace(candidate) == candidate, "candidate")
    _require(
        type(support_snapshot) is FeasibilitySupportSnapshot
        and deserialize_feasibility_support(serialize_feasibility_support(support_snapshot))
        == support_snapshot,
        "support_snapshot",
    )
    _require(
        (candidate.source_support_snapshot_id, candidate.source_support_snapshot_fingerprint)
        == (support_snapshot.support_snapshot_id, support_snapshot.support_fingerprint),
        "candidate_support_binding",
    )
    baseline_by_id = {
        item.commitment_id: item for item in support_snapshot.schedule.placements
    }
    proposed_by_id = {
        item.commitment_id: item for item in candidate.proposed_worker_placements
    }
    subjects: dict[tuple[Any, ...], AuthorityMarkerSubject] = {}

    def include(value: AuthorityMarkerSubject) -> None:
        key = (
            value.subject_kind.value,
            value.subject_id,
            value.job_id,
            value.task_id,
            value.operational_date.isoformat(),
        )
        subjects[key] = value

    for commitment_id in candidate.modified_commitment_ids:
        baseline = baseline_by_id.get(commitment_id)
        proposed = proposed_by_id.get(commitment_id)
        _require(baseline is not None and proposed is not None, "modified_commitment_binding")
        _require(
            (baseline.commitment_id, baseline.job_id, baseline.task_id)
            == (proposed.commitment_id, proposed.job_id, proposed.task_id),
            "modified_commitment_binding",
        )
        for value in _subjects_for_baseline_placement(baseline):
            include(value)
        for value in _subjects_for_proposed_placement(proposed):
            include(value)

    modified = set(candidate.modified_commitment_ids)
    for reference in candidate.impact.candidate_induced_impact:
        baseline = baseline_by_id.get(reference.commitment_id)
        _require(
            baseline is not None
            and (baseline.commitment_id, baseline.job_id, baseline.task_id)
            == (reference.commitment_id, reference.job_id, reference.task_id),
            "candidate_induced_impact_binding",
        )
        if reference.commitment_id not in modified:
            for value in _subjects_for_baseline_placement(baseline):
                include(value)

    return tuple(subjects[key] for key in sorted(subjects))


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateMarkerSubjectUniverse:
    candidate_position: int
    candidate_id: str
    candidate_fingerprint: str
    subjects: tuple[AuthorityMarkerSubject, ...]
    schema_version: str = field(default=MARKER_UNIVERSE_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    universe_fingerprint: str = field(init=False)
    universe_id: str = field(init=False)

    def __post_init__(self) -> None:
        _revision(self.candidate_position, "candidate_position")
        _candidate_binding(self.candidate_id, self.candidate_fingerprint)
        subjects = _canonical_records(
            self.subjects,
            AuthorityMarkerSubject,
            lambda item: (
                item.subject_kind.value,
                item.subject_id,
                item.job_id,
                item.task_id,
                item.operational_date,
            ),
            "marker_subjects",
        )
        object.__setattr__(self, "subjects", subjects)
        digest = sha256_text(candidate_marker_subject_universe_semantic_json(self))
        object.__setattr__(self, "universe_fingerprint", digest)
        object.__setattr__(self, "universe_id", "m3e1-marker-subject-universe-" + digest)


def candidate_marker_subject_universe_semantic_json(
    value: CandidateMarkerSubjectUniverse,
) -> str:
    return _semantic_json(value, ("universe_fingerprint", "universe_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorityMarkerEvidence:
    authority_root_id: str
    authority_root_fingerprint: str
    company_id: str
    company_plan_id: str
    worker_registry_revision: int
    worker_registry_fingerprint: str
    subject: AuthorityMarkerSubject
    worker_id: str | None
    marker_assignments: tuple[AuthorityMarkerKind, ...]
    complete_subject_coverage: bool
    source_record_id: str
    source_revision: int
    source_fingerprint: str
    source_capture_id: str
    source_capture_generation: int
    source_capture_fingerprint: str
    provenance_reference: str
    producer_classification: str = field(
        default="DEPLOYMENT_RESTRICTED_AUTH0_COMPANY_BOUND_TRUSTED_ADAPTOR",
        init=False,
    )
    schema_version: str = field(default=MARKER_EVIDENCE_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    evidence_fingerprint: str = field(init=False)
    evidence_id: str = field(init=False)

    def __post_init__(self) -> None:
        _root_binding(self.authority_root_id, self.authority_root_fingerprint)
        for name in (
            "company_id",
            "company_plan_id",
            "source_record_id",
            "source_capture_id",
            "provenance_reference",
        ):
            _identity(getattr(self, name), "marker_evidence." + name)
        _revision(self.worker_registry_revision, "worker_registry_revision")
        _digest(self.worker_registry_fingerprint, "worker_registry_fingerprint")
        _require(type(self.subject) is AuthorityMarkerSubject and replace(self.subject) == self.subject, "marker_subject")
        _optional_identity(self.worker_id, "marker_evidence.worker_id")
        assignments = tuple(sorted(set(self.marker_assignments), key=lambda item: item.value))
        _require(
            all(type(item) is AuthorityMarkerKind for item in assignments),
            "marker_assignments",
        )
        _require(type(self.complete_subject_coverage) is bool, "complete_subject_coverage")
        _revision(self.source_revision, "source_revision")
        _digest(self.source_fingerprint, "source_fingerprint")
        _revision(self.source_capture_generation, "source_capture_generation", positive=True)
        _digest(self.source_capture_fingerprint, "source_capture_fingerprint")
        object.__setattr__(self, "marker_assignments", assignments)
        digest = sha256_text(authority_marker_evidence_semantic_json(self))
        object.__setattr__(self, "evidence_fingerprint", digest)
        object.__setattr__(self, "evidence_id", "m3e1-authority-marker-evidence-" + digest)

    @property
    def authority_scope(self) -> AuthorityEvidenceScope | None:
        if AuthorityMarkerKind.OWNER_APPROVAL_REQUIRED not in self.marker_assignments:
            return None
        return AuthorityEvidenceScope(
            scope_kind=EvidenceScopeKind.OWNER_APPROVAL_REQUIRED,
            subject_type=self.subject.evidence_subject_type,
            subject_id=self.subject.subject_id,
            operational_date=self.subject.operational_date,
            job_id=self.subject.job_id,
            worker_id=self.worker_id,
        )


def authority_marker_evidence_semantic_json(value: AuthorityMarkerEvidence) -> str:
    return _semantic_json(value, ("evidence_fingerprint", "evidence_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorityMarkerSubjectCoverage:
    subject: AuthorityMarkerSubject
    evidence: tuple[AuthorityMarkerEvidence, ...]
    coverage_complete: bool
    status: MarkerCoverageStatus = field(init=False)
    schema_version: str = field(default=MARKER_COVERAGE_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    coverage_fingerprint: str = field(init=False)
    coverage_id: str = field(init=False)

    def __post_init__(self) -> None:
        _require(type(self.subject) is AuthorityMarkerSubject and replace(self.subject) == self.subject, "marker_subject")
        _require(type(self.coverage_complete) is bool, "coverage_complete")
        evidence = _canonical_records(
            self.evidence,
            AuthorityMarkerEvidence,
            lambda item: ("" if item.worker_id is None else item.worker_id, item.evidence_id),
            "marker_evidence",
        )
        _require(
            all(item.subject == self.subject for item in evidence),
            "marker_evidence_subject_binding",
        )
        if not self.coverage_complete or not evidence:
            status = MarkerCoverageStatus.UNKNOWN
        elif any(
            AuthorityMarkerKind.OWNER_APPROVAL_REQUIRED in item.marker_assignments
            for item in evidence
        ):
            status = MarkerCoverageStatus.MARKED
        else:
            status = MarkerCoverageStatus.NO_MARKER
        if self.coverage_complete:
            _require(all(item.complete_subject_coverage for item in evidence), "complete_subject_coverage")
            source_heads = {
                (item.source_record_id, item.source_revision, item.source_fingerprint)
                for item in evidence
            }
            _require(len(source_heads) == 1, "marker_source_head")
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "status", status)
        digest = sha256_text(authority_marker_subject_coverage_semantic_json(self))
        object.__setattr__(self, "coverage_fingerprint", digest)
        object.__setattr__(self, "coverage_id", "m3e1-marker-subject-coverage-" + digest)


def authority_marker_subject_coverage_semantic_json(
    value: AuthorityMarkerSubjectCoverage,
) -> str:
    return _semantic_json(value, ("coverage_fingerprint", "coverage_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorityMarkerSupportCut:
    authority_root_id: str
    authority_root_fingerprint: str
    company_id: str
    company_plan_id: str
    worker_registry_revision: int
    worker_registry_fingerprint: str
    evaluation_input_id: str
    evaluation_input_fingerprint: str
    feasibility_support_snapshot_id: str
    feasibility_support_snapshot_fingerprint: str
    m3_result_id: str
    m3_result_fingerprint: str
    candidate_universes: tuple[CandidateMarkerSubjectUniverse, ...]
    coverages: tuple[AuthorityMarkerSubjectCoverage, ...]
    source_head_observations: tuple[EvidenceBinding, ...]
    producer_classification: str = field(
        default="DEPLOYMENT_RESTRICTED_AUTH0_COMPANY_BOUND_TRUSTED_ADAPTOR",
        init=False,
    )
    schema_version: str = field(default=MARKER_CUT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    cut_fingerprint: str = field(init=False)
    cut_id: str = field(init=False)

    def __post_init__(self) -> None:
        _root_binding(self.authority_root_id, self.authority_root_fingerprint)
        for name in ("company_id", "company_plan_id"):
            _identity(getattr(self, name), name)
        _revision(self.worker_registry_revision, "worker_registry_revision")
        _digest(self.worker_registry_fingerprint, "worker_registry_fingerprint")
        _bound_id(self.evaluation_input_id, self.evaluation_input_fingerprint, "m3-evaluation-input-", "evaluation_input")
        _bound_id(self.feasibility_support_snapshot_id, self.feasibility_support_snapshot_fingerprint, "m3-feasibility-support-", "feasibility_support_snapshot")
        _bound_id(self.m3_result_id, self.m3_result_fingerprint, "m3-feasibility-result-", "m3_result")
        universes = _ordered_records(self.candidate_universes, CandidateMarkerSubjectUniverse, "candidate_universes")
        _require(
            tuple(item.candidate_position for item in universes) == tuple(range(len(universes))),
            "candidate_universes",
        )
        coverages = _canonical_records(
            self.coverages,
            AuthorityMarkerSubjectCoverage,
            lambda item: item.subject.marker_subject_id,
            "marker_coverages",
        )
        _require(
            all(
                (
                    evidence.authority_root_id,
                    evidence.authority_root_fingerprint,
                    evidence.company_id,
                    evidence.company_plan_id,
                    evidence.worker_registry_revision,
                    evidence.worker_registry_fingerprint,
                )
                == (
                    self.authority_root_id,
                    self.authority_root_fingerprint,
                    self.company_id,
                    self.company_plan_id,
                    self.worker_registry_revision,
                    self.worker_registry_fingerprint,
                )
                for coverage in coverages
                for evidence in coverage.evidence
            ),
            "marker_evidence_authority_binding",
        )
        required_subject_ids = {
            subject.marker_subject_id
            for universe in universes
            for subject in universe.subjects
        }
        _require(
            {item.subject.marker_subject_id for item in coverages}
            == required_subject_ids,
            "marker_coverage_universe",
        )
        heads = _canonical_records(
            self.source_head_observations,
            EvidenceBinding,
            lambda item: (item.kind, item.record_id),
            "source_head_observations",
        )
        object.__setattr__(self, "candidate_universes", universes)
        object.__setattr__(self, "coverages", coverages)
        object.__setattr__(self, "source_head_observations", heads)
        digest = sha256_text(authority_marker_support_cut_semantic_json(self))
        object.__setattr__(self, "cut_fingerprint", digest)
        object.__setattr__(self, "cut_id", "m3e1-authority-marker-cut-" + digest)


def authority_marker_support_cut_semantic_json(value: AuthorityMarkerSupportCut) -> str:
    return _semantic_json(value, ("cut_fingerprint", "cut_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateApprovalTagAssessment:
    candidate_position: int
    candidate_id: str
    candidate_fingerprint: str
    marker_cut_id: str
    marker_cut_fingerprint: str
    subject_ids: tuple[str, ...]
    subject_fingerprints: tuple[str, ...]
    applicable_scopes: tuple[AuthorityEvidenceScope, ...]
    coverage_complete: bool
    applicability: ApprovalMarkerApplicability = field(init=False)
    schema_version: str = field(default=TAG_ASSESSMENT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    assessment_fingerprint: str = field(init=False)
    assessment_id: str = field(init=False)

    def __post_init__(self) -> None:
        _revision(self.candidate_position, "candidate_position")
        _candidate_binding(self.candidate_id, self.candidate_fingerprint)
        _bound_id(self.marker_cut_id, self.marker_cut_fingerprint, "m3e1-authority-marker-cut-", "marker_cut")
        ids = tuple(self.subject_ids)
        fingerprints = tuple(self.subject_fingerprints)
        _require(len(ids) == len(fingerprints) and len(ids) == len(set(ids)), "marker_subject_bindings")
        pairs = tuple(sorted(zip(ids, fingerprints, strict=True)))
        for item_id, fingerprint in pairs:
            _identity(item_id, "marker_subject_id")
            _digest(fingerprint, "marker_subject_fingerprint")
        raw_scopes = tuple(self.applicable_scopes)
        _require(
            all(
                type(item) is AuthorityEvidenceScope and replace(item) == item
                for item in raw_scopes
            ),
            "applicable_scopes",
        )
        scopes_by_semantic = {
            authority_evidence_scope_semantic_json(item): item
            for item in raw_scopes
        }
        scopes = tuple(
            scopes_by_semantic[key] for key in sorted(scopes_by_semantic)
        )
        _require(type(self.coverage_complete) is bool, "coverage_complete")
        if not self.coverage_complete:
            applicability = ApprovalMarkerApplicability.UNKNOWN
        elif scopes:
            applicability = ApprovalMarkerApplicability.APPLICABLE
        else:
            applicability = ApprovalMarkerApplicability.NOT_APPLICABLE
        object.__setattr__(self, "subject_ids", tuple(item[0] for item in pairs))
        object.__setattr__(self, "subject_fingerprints", tuple(item[1] for item in pairs))
        object.__setattr__(self, "applicable_scopes", scopes)
        object.__setattr__(self, "applicability", applicability)
        digest = sha256_text(candidate_approval_tag_assessment_semantic_json(self))
        object.__setattr__(self, "assessment_fingerprint", digest)
        object.__setattr__(self, "assessment_id", "m3e1-approval-tag-assessment-" + digest)


def candidate_approval_tag_assessment_semantic_json(value: CandidateApprovalTagAssessment) -> str:
    return _semantic_json(value, ("assessment_fingerprint", "assessment_id"))


def assess_candidate_approval_tags(
    candidate_position: int,
    candidate: M3RepairCandidate,
    marker_cut: AuthorityMarkerSupportCut,
) -> CandidateApprovalTagAssessment:
    universe = next(
        (
            item
            for item in marker_cut.candidate_universes
            if item.candidate_position == candidate_position
            and item.candidate_id == candidate.candidate_id
            and item.candidate_fingerprint == candidate.candidate_fingerprint
        ),
        None,
    )
    _require(universe is not None, "candidate_marker_universe")
    coverage_by_subject = {
        item.subject.marker_subject_id: item for item in marker_cut.coverages
    }
    selected = tuple(coverage_by_subject[item.marker_subject_id] for item in universe.subjects)
    coverage_complete = all(
        item.coverage_complete and item.status is not MarkerCoverageStatus.UNKNOWN
        for item in selected
    )
    scopes = tuple(
        evidence.authority_scope
        for coverage in selected
        for evidence in coverage.evidence
        if evidence.authority_scope is not None
    )
    return CandidateApprovalTagAssessment(
        candidate_position=candidate_position,
        candidate_id=candidate.candidate_id,
        candidate_fingerprint=candidate.candidate_fingerprint,
        marker_cut_id=marker_cut.cut_id,
        marker_cut_fingerprint=marker_cut.cut_fingerprint,
        subject_ids=tuple(item.marker_subject_id for item in universe.subjects),
        subject_fingerprints=tuple(item.subject_fingerprint for item in universe.subjects),
        applicable_scopes=scopes,
        coverage_complete=coverage_complete,
    )


def derive_affected_operational_slices(
    candidate: M3RepairCandidate,
    support_snapshot: FeasibilitySupportSnapshot,
) -> tuple[OperationalSlice, ...]:
    _require(type(candidate) is M3RepairCandidate and replace(candidate) == candidate, "candidate")
    baseline_by_id = {
        item.commitment_id: item for item in support_snapshot.schedule.placements
    }
    proposed_by_id = {
        item.commitment_id: item for item in candidate.proposed_worker_placements
    }
    slices: set[tuple[str, date]] = set()
    for commitment_id in candidate.modified_commitment_ids:
        baseline = baseline_by_id.get(commitment_id)
        proposed = proposed_by_id.get(commitment_id)
        _require(baseline is not None and proposed is not None, "modified_commitment_binding")
        slices.add((baseline.job_id, baseline.business_date))
        slices.add((proposed.job_id, proposed.business_date))
    _require(bool(slices), "affected_operational_slices")
    return tuple(
        OperationalSlice(job_id=job_id, operational_date=business_date)
        for job_id, business_date in sorted(slices)
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class PCostApprovalSubject:
    candidate_id: str
    candidate_fingerprint: str
    consequence_id: str
    consequence_fingerprint: str
    m3d_result_id: str
    m3d_result_fingerprint: str
    internal_cost_support_id: str
    internal_cost_support_fingerprint: str
    internal_labor_cost_delta: Decimal
    profile_id: str
    profile_fingerprint: str
    issuance_id: str
    issuance_fingerprint: str
    authority_root_id: str
    authority_root_fingerprint: str
    company_id: str
    company_plan_id: str
    company_plan_provenance_reference: str
    base_plan_revision: int
    base_plan_revision_id: str
    base_plan_revision_fingerprint: str
    represented_slice: OperationalSlice
    affected_slices: tuple[OperationalSlice, ...]
    gate: AuthorityGateCode = field(default=AuthorityGateCode.P_COST, init=False)
    currency: str = field(default=EUR, init=False)
    schema_version: str = field(default=P_COST_SUBJECT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    subject_fingerprint: str = field(init=False)
    subject_id: str = field(init=False)

    def __post_init__(self) -> None:
        _candidate_binding(self.candidate_id, self.candidate_fingerprint)
        _bound_id(self.consequence_id, self.consequence_fingerprint, "m3-internal-labor-cost-consequence-", "consequence")
        _bound_id(self.m3d_result_id, self.m3d_result_fingerprint, "m3-internal-labor-cost-result-", "m3d_result")
        _bound_id(self.internal_cost_support_id, self.internal_cost_support_fingerprint, "m5-internal-cost-support-", "internal_cost_support")
        _bound_id(self.profile_id, self.profile_fingerprint, "m3e0-company-policy-profile-", "profile")
        _bound_id(self.issuance_id, self.issuance_fingerprint, "m3e0-policy-issuance-", "issuance")
        _root_binding(self.authority_root_id, self.authority_root_fingerprint)
        for name in (
            "company_id",
            "company_plan_id",
            "company_plan_provenance_reference",
        ):
            _identity(getattr(self, name), "p_cost." + name)
        _revision(self.base_plan_revision, "base_plan_revision")
        _bound_id(self.base_plan_revision_id, self.base_plan_revision_fingerprint, "m3-plan-revision-", "base_plan_revision")
        _money(self.internal_labor_cost_delta, "internal_labor_cost_delta")
        _require(
            self.internal_labor_cost_delta > P_COST_THRESHOLD_EUR,
            "internal_labor_cost_delta",
        )
        _require(type(self.represented_slice) is OperationalSlice and replace(self.represented_slice) == self.represented_slice, "represented_slice")
        slices = _canonical_records(
            self.affected_slices,
            OperationalSlice,
            lambda item: (item.job_id, item.operational_date),
            "affected_slices",
        )
        _require(self.represented_slice in slices, "represented_slice")
        object.__setattr__(self, "affected_slices", slices)
        digest = sha256_text(pcost_approval_subject_semantic_json(self))
        object.__setattr__(self, "subject_fingerprint", digest)
        object.__setattr__(self, "subject_id", "m3e1-p-cost-approval-subject-" + digest)

    @property
    def authority_scope(self) -> AuthorityEvidenceScope:
        return AuthorityEvidenceScope(
            scope_kind=EvidenceScopeKind.OWNER_APPROVAL_REQUIRED,
            subject_type=EvidenceSubjectType.DECISION,
            subject_id=self.subject_id,
            operational_date=self.represented_slice.operational_date,
            job_id=self.represented_slice.job_id,
            worker_id=None,
        )


def pcost_approval_subject_semantic_json(value: PCostApprovalSubject) -> str:
    return _semantic_json(value, ("subject_fingerprint", "subject_id"))


def _consequence_for_candidate(
    candidate_position: int,
    candidate: M3RepairCandidate,
    result: M3InternalLaborCostConsequenceResult,
) -> CandidateInternalLaborCostConsequence | None:
    return next(
        (
            item
            for item in result.consequences
            if item.candidate_position == candidate_position
            and item.candidate_id == candidate.candidate_id
            and item.candidate_fingerprint == candidate.candidate_fingerprint
        ),
        None,
    )


def derive_pcost_approval_subjects(
    *,
    candidate_position: int,
    candidate: M3RepairCandidate,
    support_snapshot: FeasibilitySupportSnapshot,
    internal_cost_support: InternalCostSupportCut,
    m3d_result: M3InternalLaborCostConsequenceResult,
    policy_profile: CompanyPolicyProfile,
    policy_issuance: PolicyIssuanceEvidence,
    authority_root: CompanyAuthorityRoot,
    company_plan: CompanyPlan,
    base_plan_revision: PlanRevision,
) -> tuple[PCostApprovalSubject, ...]:
    consequence = _consequence_for_candidate(candidate_position, candidate, m3d_result)
    _require(
        consequence is not None
        and consequence.completeness is LaborCostCompleteness.COMPLETE
        and consequence.internal_labor_cost_delta is not None
        and consequence.internal_labor_cost_delta > P_COST_THRESHOLD_EUR,
        "p_cost_consequence",
    )
    _require(
        (m3d_result.source_internal_cost_support_id, m3d_result.source_internal_cost_support_fingerprint)
        == (internal_cost_support.support_id, internal_cost_support.support_fingerprint),
        "m3d_internal_cost_support_binding",
    )
    _require(
        (policy_issuance.profile_id, policy_issuance.profile_fingerprint)
        == (policy_profile.profile_id, policy_profile.profile_fingerprint),
        "policy_issuance_profile_binding",
    )
    _require(
        (
            policy_issuance.authority_root_id,
            policy_issuance.authority_root_fingerprint,
            policy_issuance.company_id,
            policy_issuance.company_plan_id,
            policy_issuance.company_plan_provenance_reference,
        )
        == (
            authority_root.authority_root_id,
            authority_root.authority_root_fingerprint,
            authority_root.company_id,
            authority_root.company_plan_id,
            authority_root.company_plan_provenance_reference,
        ),
        "policy_issuance_authority_binding",
    )
    _require(
        (
            candidate.source_support_snapshot_id,
            candidate.source_support_snapshot_fingerprint,
            candidate.company_plan_id,
            candidate.base_plan_revision,
            candidate.base_plan_revision_id,
            candidate.base_plan_revision_fingerprint,
        )
        == (
            support_snapshot.support_snapshot_id,
            support_snapshot.support_fingerprint,
            support_snapshot.company_plan_id,
            support_snapshot.base_plan_revision,
            support_snapshot.base_plan_revision_id,
            support_snapshot.base_plan_revision_fingerprint,
        ),
        "p_cost_candidate_support_binding",
    )
    _require(
        (
            authority_root.company_plan_id,
            authority_root.company_plan_provenance_reference,
            policy_profile.authority_root_id,
            policy_profile.authority_root_fingerprint,
            policy_profile.company_id,
            policy_profile.company_plan_id,
            policy_profile.company_plan_provenance_reference,
            company_plan.company_plan_id,
            company_plan.provenance_reference,
            base_plan_revision.company_plan_id,
            base_plan_revision.revision,
            base_plan_revision.revision_id,
            base_plan_revision.fingerprint,
        )
        == (
            company_plan.company_plan_id,
            company_plan.provenance_reference,
            authority_root.authority_root_id,
            authority_root.authority_root_fingerprint,
            authority_root.company_id,
            authority_root.company_plan_id,
            authority_root.company_plan_provenance_reference,
            candidate.company_plan_id,
            authority_root.company_plan_provenance_reference,
            candidate.company_plan_id,
            candidate.base_plan_revision,
            candidate.base_plan_revision_id,
            candidate.base_plan_revision_fingerprint,
        ),
        "p_cost_authority_chain",
    )
    slices = derive_affected_operational_slices(candidate, support_snapshot)
    return tuple(
        PCostApprovalSubject(
            candidate_id=candidate.candidate_id,
            candidate_fingerprint=candidate.candidate_fingerprint,
            consequence_id=consequence.consequence_id,
            consequence_fingerprint=consequence.consequence_fingerprint,
            m3d_result_id=m3d_result.result_id,
            m3d_result_fingerprint=m3d_result.result_fingerprint,
            internal_cost_support_id=internal_cost_support.support_id,
            internal_cost_support_fingerprint=internal_cost_support.support_fingerprint,
            internal_labor_cost_delta=consequence.internal_labor_cost_delta,
            profile_id=policy_profile.profile_id,
            profile_fingerprint=policy_profile.profile_fingerprint,
            issuance_id=policy_issuance.issuance_id,
            issuance_fingerprint=policy_issuance.issuance_fingerprint,
            authority_root_id=authority_root.authority_root_id,
            authority_root_fingerprint=authority_root.authority_root_fingerprint,
            company_id=authority_root.company_id,
            company_plan_id=company_plan.company_plan_id,
            company_plan_provenance_reference=company_plan.provenance_reference,
            base_plan_revision=base_plan_revision.revision,
            base_plan_revision_id=base_plan_revision.revision_id,
            base_plan_revision_fingerprint=base_plan_revision.fingerprint,
            represented_slice=item,
            affected_slices=slices,
        )
        for item in slices
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateCostAssessment:
    candidate_position: int
    candidate_id: str
    candidate_fingerprint: str
    consequence_id: str
    consequence_fingerprint: str
    m3d_result_id: str
    m3d_result_fingerprint: str
    completeness: LaborCostCompleteness
    internal_labor_cost_delta: Decimal | None
    approval_subjects: tuple[PCostApprovalSubject, ...]
    approval_subject_state: PCostApprovalSubjectState
    applicability: CostApplicability = field(init=False)
    currency: str = field(default=EUR, init=False)
    schema_version: str = field(default=COST_ASSESSMENT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    assessment_fingerprint: str = field(init=False)
    assessment_id: str = field(init=False)

    def __post_init__(self) -> None:
        _revision(self.candidate_position, "candidate_position")
        _candidate_binding(self.candidate_id, self.candidate_fingerprint)
        _bound_id(self.consequence_id, self.consequence_fingerprint, "m3-internal-labor-cost-consequence-", "consequence")
        _bound_id(self.m3d_result_id, self.m3d_result_fingerprint, "m3-internal-labor-cost-result-", "m3d_result")
        _require(type(self.completeness) is LaborCostCompleteness, "cost_completeness")
        _require(
            type(self.approval_subject_state) is PCostApprovalSubjectState,
            "approval_subject_state",
        )
        subjects = _canonical_records(
            self.approval_subjects,
            PCostApprovalSubject,
            lambda item: (item.represented_slice.job_id, item.represented_slice.operational_date),
            "p_cost_approval_subjects",
        )
        if self.completeness is LaborCostCompleteness.INCOMPLETE:
            _require(
                self.internal_labor_cost_delta is None
                and not subjects
                and self.approval_subject_state
                is PCostApprovalSubjectState.NOT_APPLICABLE,
                "incomplete_cost",
            )
            applicability = CostApplicability.UNKNOWN
        else:
            _money(self.internal_labor_cost_delta, "internal_labor_cost_delta")
            if self.internal_labor_cost_delta <= P_COST_THRESHOLD_EUR:
                _require(
                    not subjects
                    and self.approval_subject_state
                    is PCostApprovalSubjectState.NOT_APPLICABLE,
                    "p_cost_approval_subjects",
                )
                applicability = CostApplicability.WITHIN_ENVELOPE
            else:
                if self.approval_subject_state is PCostApprovalSubjectState.COMPLETE:
                    _require(bool(subjects), "p_cost_approval_subjects")
                    affected_slices = subjects[0].affected_slices
                    _require(
                        all(
                            item.candidate_id == self.candidate_id
                            and item.candidate_fingerprint
                            == self.candidate_fingerprint
                            and item.consequence_id == self.consequence_id
                            and item.consequence_fingerprint
                            == self.consequence_fingerprint
                            and item.m3d_result_id == self.m3d_result_id
                            and item.m3d_result_fingerprint
                            == self.m3d_result_fingerprint
                            and item.internal_labor_cost_delta
                            == self.internal_labor_cost_delta
                            and item.affected_slices == affected_slices
                            for item in subjects
                        ),
                        "p_cost_approval_subject_binding",
                    )
                    _require(
                        tuple(item.represented_slice for item in subjects)
                        == affected_slices,
                        "p_cost_approval_subject_universe",
                    )
                else:
                    _require(
                        self.approval_subject_state
                        is PCostApprovalSubjectState.UNAVAILABLE_UNSUPPORTED_POLICY
                        and not subjects,
                        "p_cost_approval_subjects",
                    )
                applicability = CostApplicability.SOFT_EXCEPTION
        object.__setattr__(self, "approval_subjects", subjects)
        object.__setattr__(self, "applicability", applicability)
        digest = sha256_text(candidate_cost_assessment_semantic_json(self))
        object.__setattr__(self, "assessment_fingerprint", digest)
        object.__setattr__(self, "assessment_id", "m3e1-cost-assessment-" + digest)


def candidate_cost_assessment_semantic_json(value: CandidateCostAssessment) -> str:
    return _semantic_json(value, ("assessment_fingerprint", "assessment_id"))


def assess_candidate_cost(
    *,
    candidate_position: int,
    candidate: M3RepairCandidate,
    support_snapshot: FeasibilitySupportSnapshot,
    internal_cost_support: InternalCostSupportCut,
    m3d_result: M3InternalLaborCostConsequenceResult,
    policy_profile: CompanyPolicyProfile | None,
    policy_issuance: PolicyIssuanceEvidence | None,
    authority_root: CompanyAuthorityRoot,
    company_plan: CompanyPlan,
    base_plan_revision: PlanRevision,
) -> CandidateCostAssessment:
    consequence = _consequence_for_candidate(candidate_position, candidate, m3d_result)
    _require(consequence is not None, "candidate_cost_consequence")
    subjects: tuple[PCostApprovalSubject, ...] = ()
    subject_state = PCostApprovalSubjectState.NOT_APPLICABLE
    if (
        consequence.completeness is LaborCostCompleteness.COMPLETE
        and consequence.internal_labor_cost_delta is not None
        and consequence.internal_labor_cost_delta > P_COST_THRESHOLD_EUR
    ):
        if policy_profile is None or policy_issuance is None:
            _require(
                policy_profile is None and policy_issuance is None,
                "p_cost_policy_binding",
            )
            subject_state = PCostApprovalSubjectState.UNAVAILABLE_UNSUPPORTED_POLICY
        else:
            subjects = derive_pcost_approval_subjects(
                candidate_position=candidate_position,
                candidate=candidate,
                support_snapshot=support_snapshot,
                internal_cost_support=internal_cost_support,
                m3d_result=m3d_result,
                policy_profile=policy_profile,
                policy_issuance=policy_issuance,
                authority_root=authority_root,
                company_plan=company_plan,
                base_plan_revision=base_plan_revision,
            )
            subject_state = PCostApprovalSubjectState.COMPLETE
    return CandidateCostAssessment(
        candidate_position=candidate_position,
        candidate_id=candidate.candidate_id,
        candidate_fingerprint=candidate.candidate_fingerprint,
        consequence_id=consequence.consequence_id,
        consequence_fingerprint=consequence.consequence_fingerprint,
        m3d_result_id=m3d_result.result_id,
        m3d_result_fingerprint=m3d_result.result_fingerprint,
        completeness=consequence.completeness,
        internal_labor_cost_delta=consequence.internal_labor_cost_delta,
        approval_subjects=subjects,
        approval_subject_state=subject_state,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorityRequirement:
    gate: AuthorityGateCode
    candidate_id: str
    candidate_fingerprint: str
    scope: AuthorityEvidenceScope
    required_principal_type: PrincipalType
    required_worker_id: str | None
    subject_class: AuthoritySubjectClass
    supporting_bindings: tuple[EvidenceBinding, ...]
    schema_version: str = field(default=AUTHORITY_REQUIREMENT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    requirement_fingerprint: str = field(init=False)
    requirement_id: str = field(init=False)

    def __post_init__(self) -> None:
        _require(type(self.gate) is AuthorityGateCode, "requirement.gate")
        _candidate_binding(self.candidate_id, self.candidate_fingerprint)
        _require(type(self.scope) is AuthorityEvidenceScope and replace(self.scope) == self.scope, "requirement.scope")
        _require(type(self.required_principal_type) is PrincipalType, "required_principal_type")
        _require(type(self.subject_class) is AuthoritySubjectClass, "subject_class")
        if self.required_principal_type is PrincipalType.WORKER:
            _identity(self.required_worker_id, "required_worker_id")
            _require(self.scope.worker_id == self.required_worker_id, "required_worker_id")
            _require(self.gate is AuthorityGateCode.P_VEHICLE, "worker_requirement_gate")
        else:
            _require(self.required_worker_id is None, "required_worker_id")
        if self.gate is AuthorityGateCode.P_COST:
            _require(
                self.subject_class is AuthoritySubjectClass.CANDIDATE_CONSEQUENCE
                and self.scope.subject_type is EvidenceSubjectType.DECISION,
                "p_cost_requirement",
            )
        elif self.gate in (AuthorityGateCode.P_TAG, AuthorityGateCode.P_VEHICLE):
            _require(self.subject_class is AuthoritySubjectClass.NATURAL_SUBJECT, "natural_requirement")
        else:
            _require(False, "unsupported_requirement_gate")
        bindings = _canonical_records(
            self.supporting_bindings,
            EvidenceBinding,
            lambda item: (item.kind, item.record_id, item.fingerprint),
            "supporting_bindings",
        )
        _require(bool(bindings), "supporting_bindings")
        object.__setattr__(self, "supporting_bindings", bindings)
        digest = sha256_text(authority_requirement_semantic_json(self))
        object.__setattr__(self, "requirement_fingerprint", digest)
        object.__setattr__(self, "requirement_id", "m3e1-authority-requirement-" + digest)


def authority_requirement_semantic_json(value: AuthorityRequirement) -> str:
    return _semantic_json(value, ("requirement_fingerprint", "requirement_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateAuthorityRequirementSet:
    candidate_position: int
    candidate_id: str
    candidate_fingerprint: str
    requirements: tuple[AuthorityRequirement, ...]
    schema_version: str = field(default=AUTHORITY_REQUIREMENT_SET_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    requirement_set_fingerprint: str = field(init=False)
    requirement_set_id: str = field(init=False)

    def __post_init__(self) -> None:
        _revision(self.candidate_position, "candidate_position")
        _candidate_binding(self.candidate_id, self.candidate_fingerprint)
        requirements = _canonical_records(
            self.requirements,
            AuthorityRequirement,
            lambda item: (
                item.gate.value,
                item.required_principal_type.value,
                item.scope.scope_id,
                item.requirement_id,
            ),
            "requirements",
        )
        _require(
            all(
                (item.candidate_id, item.candidate_fingerprint)
                == (self.candidate_id, self.candidate_fingerprint)
                for item in requirements
            ),
            "requirement_candidate_binding",
        )
        object.__setattr__(self, "requirements", requirements)
        digest = sha256_text(candidate_authority_requirement_set_semantic_json(self))
        object.__setattr__(self, "requirement_set_fingerprint", digest)
        object.__setattr__(self, "requirement_set_id", "m3e1-authority-requirement-set-" + digest)


def candidate_authority_requirement_set_semantic_json(value: CandidateAuthorityRequirementSet) -> str:
    return _semantic_json(value, ("requirement_set_fingerprint", "requirement_set_id"))


def _private_vehicle_scopes(
    candidate: M3RepairCandidate,
    support_snapshot: FeasibilitySupportSnapshot,
) -> tuple[AuthorityEvidenceScope, ...]:
    vehicle_by_id = {
        item.vehicle_id: item for item in support_snapshot.vehicle_technical_evidence
    }
    scopes = []
    for placement in candidate.proposed_worker_placements:
        if placement.vehicle_id is None:
            continue
        vehicle = vehicle_by_id.get(placement.vehicle_id)
        _require(vehicle is not None, "candidate_vehicle_binding")
        if vehicle.kind == "PRIVATE":
            scopes.append(
                AuthorityEvidenceScope(
                    scope_kind=EvidenceScopeKind.PRIVATE_VEHICLE_USE,
                    subject_type=EvidenceSubjectType.PRIVATE_VEHICLE,
                    subject_id=placement.vehicle_id,
                    operational_date=placement.business_date,
                    job_id=placement.job_id,
                    worker_id=placement.worker_id,
                )
            )
    scopes_by_semantic = {
        authority_evidence_scope_semantic_json(item): item for item in scopes
    }
    result = tuple(
        scopes_by_semantic[key] for key in sorted(scopes_by_semantic)
    )
    _require(
        bool(result)
        == (ReasonCode.PRIVATE_VEHICLE_AUTHORITY_REQUIRED in candidate.authority_impacts),
        "private_vehicle_authority_impact",
    )
    return result


def derive_authority_requirements(
    *,
    candidate_position: int,
    candidate: M3RepairCandidate,
    support_snapshot: FeasibilitySupportSnapshot,
    cost_assessment: CandidateCostAssessment | None,
    tag_assessment: CandidateApprovalTagAssessment,
) -> CandidateAuthorityRequirementSet:
    values: list[AuthorityRequirement] = []
    if cost_assessment is not None:
        _require(
            (cost_assessment.candidate_position, cost_assessment.candidate_id)
            == (candidate_position, candidate.candidate_id),
            "cost_assessment_candidate_binding",
        )
        for subject in cost_assessment.approval_subjects:
            values.append(
                AuthorityRequirement(
                    gate=AuthorityGateCode.P_COST,
                    candidate_id=candidate.candidate_id,
                    candidate_fingerprint=candidate.candidate_fingerprint,
                    scope=subject.authority_scope,
                    required_principal_type=PrincipalType.OWNER,
                    required_worker_id=None,
                    subject_class=AuthoritySubjectClass.CANDIDATE_CONSEQUENCE,
                    supporting_bindings=(
                        EvidenceBinding(
                            kind="P_COST_APPROVAL_SUBJECT",
                            record_id=subject.subject_id,
                            fingerprint=subject.subject_fingerprint,
                        ),
                    ),
                )
            )
    _require(
        (tag_assessment.candidate_position, tag_assessment.candidate_id)
        == (candidate_position, candidate.candidate_id),
        "tag_assessment_candidate_binding",
    )
    for scope in tag_assessment.applicable_scopes:
        values.append(
            AuthorityRequirement(
                gate=AuthorityGateCode.P_TAG,
                candidate_id=candidate.candidate_id,
                candidate_fingerprint=candidate.candidate_fingerprint,
                scope=scope,
                required_principal_type=PrincipalType.OWNER,
                required_worker_id=None,
                subject_class=AuthoritySubjectClass.NATURAL_SUBJECT,
                supporting_bindings=(
                    EvidenceBinding(
                        kind="APPROVAL_TAG_ASSESSMENT",
                        record_id=tag_assessment.assessment_id,
                        fingerprint=tag_assessment.assessment_fingerprint,
                    ),
                ),
            )
        )
    for scope in _private_vehicle_scopes(candidate, support_snapshot):
        common = {
            "gate": AuthorityGateCode.P_VEHICLE,
            "candidate_id": candidate.candidate_id,
            "candidate_fingerprint": candidate.candidate_fingerprint,
            "scope": scope,
            "subject_class": AuthoritySubjectClass.NATURAL_SUBJECT,
            "supporting_bindings": (
                EvidenceBinding(
                    kind="M3_CANDIDATE",
                    record_id=candidate.candidate_id,
                    fingerprint=candidate.candidate_fingerprint,
                ),
            ),
        }
        values.append(
            AuthorityRequirement(
                **common,
                required_principal_type=PrincipalType.OWNER,
                required_worker_id=None,
            )
        )
        values.append(
            AuthorityRequirement(
                **common,
                required_principal_type=PrincipalType.WORKER,
                required_worker_id=scope.worker_id,
            )
        )
    # Canonical SET semantics: only byte-identical requirements collapse.
    by_semantic = {authority_requirement_semantic_json(item): item for item in values}
    return CandidateAuthorityRequirementSet(
        candidate_position=candidate_position,
        candidate_id=candidate.candidate_id,
        candidate_fingerprint=candidate.candidate_fingerprint,
        requirements=tuple(by_semantic[key] for key in sorted(by_semantic)),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class OwnerApprovalSelection:
    requirement_id: str
    requirement_fingerprint: str
    authority_root_id: str
    authority_root_fingerprint: str
    owner_principal_id: str | None
    owner_principal_fingerprint: str | None
    scope: AuthorityEvidenceScope
    status: SelectionStatus
    approval_id: str | None
    approval_fingerprint: str | None
    recorded_scope_id: str | None
    recorded_scope_fingerprint: str | None
    schema_version: str = field(default=OWNER_SELECTION_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    selection_fingerprint: str = field(init=False)
    selection_id: str = field(init=False)

    def __post_init__(self) -> None:
        _bound_id(self.requirement_id, self.requirement_fingerprint, "m3e1-authority-requirement-", "requirement")
        _root_binding(self.authority_root_id, self.authority_root_fingerprint)
        _require(type(self.scope) is AuthorityEvidenceScope and replace(self.scope) == self.scope, "scope")
        _require(type(self.status) is SelectionStatus, "selection_status")
        if self.status is SelectionStatus.SELECTED:
            _bound_id(self.owner_principal_id, self.owner_principal_fingerprint, "auth0-trusted-principal-", "owner_principal")
            _bound_id(self.approval_id, self.approval_fingerprint, "m3e0-owner-approval-", "approval")
            _require(
                (self.recorded_scope_id, self.recorded_scope_fingerprint)
                == (self.scope.scope_id, self.scope.scope_fingerprint),
                "recorded_approval_scope",
            )
        else:
            _require(
                self.owner_principal_id is None
                and self.owner_principal_fingerprint is None
                and self.approval_id is None
                and self.approval_fingerprint is None,
                "absent_owner_approval",
            )
            _require(
                self.recorded_scope_id is None
                and self.recorded_scope_fingerprint is None,
                "absent_owner_approval_scope",
            )
        digest = sha256_text(owner_approval_selection_semantic_json(self))
        object.__setattr__(self, "selection_fingerprint", digest)
        object.__setattr__(self, "selection_id", "m3e1-owner-approval-selection-" + digest)


def owner_approval_selection_semantic_json(value: OwnerApprovalSelection) -> str:
    return _semantic_json(value, ("selection_fingerprint", "selection_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerConsentHeadSelection:
    requirement_id: str
    requirement_fingerprint: str
    authority_root_id: str
    authority_root_fingerprint: str
    worker_id: str
    scope: AuthorityEvidenceScope
    status: SelectionStatus
    worker_principal_id: str | None
    worker_principal_fingerprint: str | None
    consent_id: str | None
    consent_fingerprint: str | None
    recorded_scope_id: str | None
    recorded_scope_fingerprint: str | None
    consent_status: RecordedConsentStatus | None
    lineage_sequence: int | None
    previous_consent_id: str | None
    previous_consent_fingerprint: str | None
    is_current_lineage_head: bool
    schema_version: str = field(default=CONSENT_SELECTION_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    selection_fingerprint: str = field(init=False)
    selection_id: str = field(init=False)

    def __post_init__(self) -> None:
        _bound_id(self.requirement_id, self.requirement_fingerprint, "m3e1-authority-requirement-", "requirement")
        _root_binding(self.authority_root_id, self.authority_root_fingerprint)
        _identity(self.worker_id, "worker_id")
        _require(type(self.scope) is AuthorityEvidenceScope and replace(self.scope) == self.scope, "scope")
        _require(
            self.scope.scope_kind is EvidenceScopeKind.PRIVATE_VEHICLE_USE
            and self.scope.worker_id == self.worker_id,
            "worker_consent_scope",
        )
        _require(type(self.status) is SelectionStatus, "selection_status")
        _require(type(self.is_current_lineage_head) is bool, "is_current_lineage_head")
        if self.status is SelectionStatus.SELECTED:
            _bound_id(self.worker_principal_id, self.worker_principal_fingerprint, "auth0-trusted-principal-", "worker_principal")
            _bound_id(self.consent_id, self.consent_fingerprint, "m3e0-worker-consent-", "consent")
            _require(
                (self.recorded_scope_id, self.recorded_scope_fingerprint)
                == (self.scope.scope_id, self.scope.scope_fingerprint),
                "recorded_consent_scope",
            )
            _require(type(self.consent_status) is RecordedConsentStatus, "consent_status")
            _revision(self.lineage_sequence, "lineage_sequence", positive=True)
            if self.lineage_sequence == 1:
                _require(
                    self.previous_consent_id is None
                    and self.previous_consent_fingerprint is None,
                    "previous_consent",
                )
            else:
                _bound_id(self.previous_consent_id, self.previous_consent_fingerprint, "m3e0-worker-consent-", "previous_consent")
        else:
            _require(
                self.worker_principal_id is None
                and self.worker_principal_fingerprint is None
                and self.consent_id is None
                and self.consent_fingerprint is None
                and self.recorded_scope_id is None
                and self.recorded_scope_fingerprint is None
                and self.consent_status is None
                and self.lineage_sequence is None
                and self.previous_consent_id is None
                and self.previous_consent_fingerprint is None
                and not self.is_current_lineage_head,
                "absent_worker_consent",
            )
        digest = sha256_text(worker_consent_head_selection_semantic_json(self))
        object.__setattr__(self, "selection_fingerprint", digest)
        object.__setattr__(self, "selection_id", "m3e1-worker-consent-selection-" + digest)

    @property
    def is_affirmative_current_consent(self) -> bool:
        return (
            self.status is SelectionStatus.SELECTED
            and self.is_current_lineage_head
            and self.consent_status is RecordedConsentStatus.FREELY_GIVEN
        )


def worker_consent_head_selection_semantic_json(value: WorkerConsentHeadSelection) -> str:
    return _semantic_json(value, ("selection_fingerprint", "selection_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateHardBoundaryAssessment:
    candidate_position: int
    candidate_id: str
    candidate_fingerprint: str
    applicability: HardBoundaryApplicability
    boundary_kind: HardBoundaryKind | None
    reason_code: str | None
    evidence_bindings: tuple[EvidenceBinding, ...]
    schema_version: str = field(default=HARD_BOUNDARY_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    assessment_fingerprint: str = field(init=False)
    assessment_id: str = field(init=False)

    def __post_init__(self) -> None:
        _revision(self.candidate_position, "candidate_position")
        _candidate_binding(self.candidate_id, self.candidate_fingerprint)
        _require(type(self.applicability) is HardBoundaryApplicability, "hard_boundary_applicability")
        bindings = _canonical_records(
            self.evidence_bindings,
            EvidenceBinding,
            lambda item: (item.kind, item.record_id),
            "hard_boundary_evidence",
        )
        if self.applicability is HardBoundaryApplicability.APPLICABLE:
            _require(type(self.boundary_kind) is HardBoundaryKind, "hard_boundary_kind")
            _identity(self.reason_code, "hard_boundary_reason")
            _require(bool(bindings), "hard_boundary_evidence")
        elif self.applicability is HardBoundaryApplicability.NOT_APPLICABLE:
            _require(self.boundary_kind is None and self.reason_code is None, "hard_boundary")
        else:
            _require(self.boundary_kind is None, "hard_boundary_kind")
            _identity(self.reason_code, "hard_boundary_reason")
        object.__setattr__(self, "evidence_bindings", bindings)
        digest = sha256_text(candidate_hard_boundary_assessment_semantic_json(self))
        object.__setattr__(self, "assessment_fingerprint", digest)
        object.__setattr__(self, "assessment_id", "m3e1-hard-boundary-assessment-" + digest)


def candidate_hard_boundary_assessment_semantic_json(value: CandidateHardBoundaryAssessment) -> str:
    return _semantic_json(value, ("assessment_fingerprint", "assessment_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanDayRootHeadEvidence:
    plan_day_id: str
    worker_id: str
    business_date: date
    plan_day_revision: int
    content_sha256: str
    status: PlanDayStatus
    canonical_plan_day_root_json: str
    schema_version: str = field(default=PLAN_DAY_ROOT_HEAD_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    head_fingerprint: str = field(init=False)
    head_id: str = field(init=False)

    def __post_init__(self) -> None:
        _identity(self.plan_day_id, "plan_day_root_head.plan_day_id")
        _identity(self.worker_id, "plan_day_root_head.worker_id")
        _require(type(self.business_date) is date, "plan_day_root_head.business_date")
        _revision(self.plan_day_revision, "plan_day_root_head.plan_day_revision")
        _digest(self.content_sha256, "plan_day_root_head.content_sha256")
        _require(type(self.status) is PlanDayStatus, "plan_day_root_head.status")
        _canonical_json_document(
            self.canonical_plan_day_root_json,
            "plan_day_root_head.canonical_plan_day_root_json",
        )
        try:
            root = deserialize_plan_day_root(self.canonical_plan_day_root_json)
        except ValueError as error:
            raise OperationalAuthorityValidationError(
                "INVALID_PLAN_DAY_ROOT",
                "plan_day_root_head.canonical_plan_day_root_json",
            ) from error
        _require(
            serialize_plan_day_root(root) == self.canonical_plan_day_root_json
            and sha256_text(self.canonical_plan_day_root_json) == self.content_sha256
            and (
                root.plan_day_id,
                root.worker_id,
                root.business_date,
                root.plan_day_revision,
                root.status,
            )
            == (
                self.plan_day_id,
                self.worker_id,
                self.business_date,
                self.plan_day_revision,
                self.status,
            ),
            "plan_day_root_head.binding",
        )
        digest = sha256_text(plan_day_root_head_evidence_semantic_json(self))
        object.__setattr__(self, "head_fingerprint", digest)
        object.__setattr__(self, "head_id", "m3e1-plan-day-root-head-" + digest)


def plan_day_root_head_evidence_semantic_json(value: PlanDayRootHeadEvidence) -> str:
    return _semantic_json(value, ("head_fingerprint", "head_id"))


def plan_day_root_head_evidence(root: PlanDayRoot) -> PlanDayRootHeadEvidence:
    _require(type(root) is PlanDayRoot and replace(root) == root, "plan_day_root")
    canonical = serialize_plan_day_root(root)
    return PlanDayRootHeadEvidence(
        plan_day_id=root.plan_day_id,
        worker_id=root.worker_id,
        business_date=root.business_date,
        plan_day_revision=root.plan_day_revision,
        content_sha256=sha256_text(canonical),
        status=root.status,
        canonical_plan_day_root_json=canonical,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class CurrentnessObservation:
    kind: CurrentnessKind
    subject_key: str
    expected_state: CurrentnessEvidenceState
    observed_state: CurrentnessEvidenceState
    expected_identity: str | None
    expected_revision: int | None
    expected_fingerprint: str | None
    expected_status: str | None
    expected_semantic_json: str | None
    observed_identity: str | None
    observed_revision: int | None
    observed_fingerprint: str | None
    observed_status: str | None
    observed_semantic_json: str | None
    exact_match: bool = field(init=False)
    schema_version: str = field(default=CURRENTNESS_OBSERVATION_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    observation_fingerprint: str = field(init=False)
    observation_id: str = field(init=False)

    def __post_init__(self) -> None:
        _require(type(self.kind) is CurrentnessKind, "currentness.kind")
        _identity(self.subject_key, "currentness.subject_key")
        _require(type(self.expected_state) is CurrentnessEvidenceState, "currentness.expected_state")
        _require(type(self.observed_state) is CurrentnessEvidenceState, "currentness.observed_state")
        expected_values = (
            self.expected_identity,
            self.expected_revision,
            self.expected_fingerprint,
            self.expected_status,
            self.expected_semantic_json,
        )
        observed_values = (
            self.observed_identity,
            self.observed_revision,
            self.observed_fingerprint,
            self.observed_status,
            self.observed_semantic_json,
        )
        if self.expected_state is CurrentnessEvidenceState.ABSENT:
            _require(all(item is None for item in expected_values), "currentness.expected_absence")
        elif self.expected_state is CurrentnessEvidenceState.PRESENT:
            _require(any(item is not None for item in expected_values), "currentness.expected_values")
            _canonical_json_document(self.expected_semantic_json, "currentness.expected_semantic_json")
        else:
            _require(
                self.expected_semantic_json is None
                or type(self.expected_semantic_json) is str,
                "currentness.expected_semantic_json",
            )
            if self.expected_semantic_json is not None:
                _canonical_json_document(self.expected_semantic_json, "currentness.expected_semantic_json")
        if self.observed_state is CurrentnessEvidenceState.ABSENT:
            _require(all(item is None for item in observed_values), "currentness.observed_absence")
        elif self.observed_state is CurrentnessEvidenceState.PRESENT:
            _require(any(item is not None for item in observed_values), "currentness.observed_values")
            _canonical_json_document(self.observed_semantic_json, "currentness.observed_semantic_json")
        else:
            _require(
                self.observed_semantic_json is None
                or type(self.observed_semantic_json) is str,
                "currentness.observed_semantic_json",
            )
            if self.observed_semantic_json is not None:
                _canonical_json_document(self.observed_semantic_json, "currentness.observed_semantic_json")
        for value, name in (
            (self.expected_identity, "currentness.expected_identity"),
            (self.expected_status, "currentness.expected_status"),
            (self.observed_identity, "currentness.observed_identity"),
            (self.observed_status, "currentness.observed_status"),
        ):
            _optional_identity(value, name)
        for value, name in (
            (self.expected_revision, "currentness.expected_revision"),
            (self.observed_revision, "currentness.observed_revision"),
        ):
            _require(value is None or type(value) is int, name)
            if value is not None:
                _revision(value, name)
        _optional_digest(self.expected_fingerprint, "currentness.expected_fingerprint")
        _optional_digest(self.observed_fingerprint, "currentness.observed_fingerprint")
        affirmative_state = self.expected_state in (
            CurrentnessEvidenceState.PRESENT,
            CurrentnessEvidenceState.ABSENT,
        )
        exact = (
            affirmative_state
            and self.expected_state is self.observed_state
            and expected_values == observed_values
        )
        object.__setattr__(self, "exact_match", exact)
        digest = sha256_text(currentness_observation_semantic_json(self))
        object.__setattr__(self, "observation_fingerprint", digest)
        object.__setattr__(self, "observation_id", "m3e1-currentness-observation-" + digest)


def currentness_observation_semantic_json(value: CurrentnessObservation) -> str:
    return _semantic_json(value, ("observation_fingerprint", "observation_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class M3DecisionTimeCurrentnessVector:
    company_plan_id: str
    company_plan_provenance_reference: str
    base_plan_revision: int
    base_plan_revision_id: str
    base_plan_revision_fingerprint: str
    current_planning_scope_fingerprint: str
    evaluation_input_id: str
    evaluation_input_fingerprint: str
    c0_source_cut_id: str
    c0_source_cut_fingerprint: str
    feasibility_support_snapshot_id: str
    feasibility_support_snapshot_fingerprint: str
    m3_result_id: str
    m3_result_fingerprint: str
    internal_cost_support_id: str
    internal_cost_support_fingerprint: str
    m3d_result_id: str
    m3d_result_fingerprint: str
    authority_root_id: str
    authority_root_fingerprint: str
    company_id: str
    worker_registry_revision: int
    worker_registry_fingerprint: str
    trusted_principal_ids: tuple[str, ...]
    trusted_principal_fingerprints: tuple[str, ...]
    policy_status: PolicySupportStatus
    policy_profile_id: str | None
    policy_profile_fingerprint: str | None
    policy_issuance_id: str | None
    policy_issuance_fingerprint: str | None
    authority_requirement_set_ids: tuple[str, ...]
    authority_requirement_set_fingerprints: tuple[str, ...]
    owner_approval_selection_ids: tuple[str, ...]
    owner_approval_selection_fingerprints: tuple[str, ...]
    worker_consent_selection_ids: tuple[str, ...]
    worker_consent_selection_fingerprints: tuple[str, ...]
    working_time_cut_id: str
    working_time_cut_fingerprint: str
    marker_cut_id: str
    marker_cut_fingerprint: str
    observations: tuple[CurrentnessObservation, ...]
    schema_version: str = field(default=CURRENTNESS_VECTOR_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    vector_fingerprint: str = field(init=False)
    vector_id: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("company_plan_id", "company_plan_provenance_reference", "company_id"):
            _identity(getattr(self, name), "currentness." + name)
        _revision(self.base_plan_revision, "currentness.base_plan_revision")
        _bound_id(self.base_plan_revision_id, self.base_plan_revision_fingerprint, "m3-plan-revision-", "base_plan_revision")
        _digest(self.current_planning_scope_fingerprint, "current_planning_scope_fingerprint")
        _bound_id(self.evaluation_input_id, self.evaluation_input_fingerprint, "m3-evaluation-input-", "evaluation_input")
        _bound_id(self.c0_source_cut_id, self.c0_source_cut_fingerprint, "m3-feasibility-source-cut-", "c0_source_cut")
        _bound_id(self.feasibility_support_snapshot_id, self.feasibility_support_snapshot_fingerprint, "m3-feasibility-support-", "feasibility_support_snapshot")
        _bound_id(self.m3_result_id, self.m3_result_fingerprint, "m3-feasibility-result-", "m3_result")
        _bound_id(self.internal_cost_support_id, self.internal_cost_support_fingerprint, "m5-internal-cost-support-", "internal_cost_support")
        _bound_id(self.m3d_result_id, self.m3d_result_fingerprint, "m3-internal-labor-cost-result-", "m3d_result")
        _root_binding(self.authority_root_id, self.authority_root_fingerprint)
        _revision(self.worker_registry_revision, "worker_registry_revision")
        _digest(self.worker_registry_fingerprint, "worker_registry_fingerprint")
        _require(type(self.policy_status) is PolicySupportStatus, "policy_status")
        policy_values = (
            self.policy_profile_id,
            self.policy_profile_fingerprint,
            self.policy_issuance_id,
            self.policy_issuance_fingerprint,
        )
        if self.policy_status is PolicySupportStatus.ISSUED_SUPPORTED:
            _bound_id(self.policy_profile_id, self.policy_profile_fingerprint, "m3e0-company-policy-profile-", "policy_profile")
            _bound_id(self.policy_issuance_id, self.policy_issuance_fingerprint, "m3e0-policy-issuance-", "policy_issuance")
        else:
            _require(all(item is None for item in policy_values), "unsupported_policy_identity")
        for ids_name, fps_name, prefix in (
            ("trusted_principal_ids", "trusted_principal_fingerprints", "auth0-trusted-principal-"),
            (
                "authority_requirement_set_ids",
                "authority_requirement_set_fingerprints",
                "m3e1-authority-requirement-set-",
            ),
            ("owner_approval_selection_ids", "owner_approval_selection_fingerprints", "m3e1-owner-approval-selection-"),
            ("worker_consent_selection_ids", "worker_consent_selection_fingerprints", "m3e1-worker-consent-selection-"),
        ):
            ids = tuple(getattr(self, ids_name))
            fingerprints = tuple(getattr(self, fps_name))
            _require(len(ids) == len(fingerprints) and len(ids) == len(set(ids)), ids_name)
            pairs = tuple(sorted(zip(ids, fingerprints, strict=True)))
            for record_id, fingerprint in pairs:
                _bound_id(record_id, fingerprint, prefix, ids_name)
            object.__setattr__(self, ids_name, tuple(item[0] for item in pairs))
            object.__setattr__(self, fps_name, tuple(item[1] for item in pairs))
        _bound_id(self.working_time_cut_id, self.working_time_cut_fingerprint, "m3e1-working-time-cut-", "working_time_cut")
        _bound_id(self.marker_cut_id, self.marker_cut_fingerprint, "m3e1-authority-marker-cut-", "marker_cut")
        observations = _canonical_records(
            self.observations,
            CurrentnessObservation,
            lambda item: (item.kind.value, item.subject_key),
            "currentness_observations",
        )
        _require(
            {item.kind for item in observations} == set(CurrentnessKind),
            "currentness_observation_categories",
        )
        object.__setattr__(self, "observations", observations)
        digest = sha256_text(m3_decision_time_currentness_vector_semantic_json(self))
        object.__setattr__(self, "vector_fingerprint", digest)
        object.__setattr__(self, "vector_id", "m3e1-currentness-vector-" + digest)

    @property
    def all_current(self) -> bool:
        return all(item.exact_match for item in self.observations)


def m3_decision_time_currentness_vector_semantic_json(value: M3DecisionTimeCurrentnessVector) -> str:
    return _semantic_json(value, ("vector_fingerprint", "vector_id"))


def derive_required_currentness_observations(
    *,
    company_plan: CompanyPlan,
    base_plan_revision: PlanRevision,
    plan_day_root_heads: tuple[PlanDayRootHeadEvidence, ...],
    evaluation_input: M3EvaluationInput,
    feasibility_support_snapshot: FeasibilitySupportSnapshot,
    m3_result: M3BoundedFeasibilityResult,
    internal_cost_support: InternalCostSupportCut,
    m3d_result: M3InternalLaborCostConsequenceResult,
    authority_root: CompanyAuthorityRoot,
    trusted_principals: tuple[TrustedPrincipal, ...],
    policy_status: PolicySupportStatus,
    policy_profile: CompanyPolicyProfile | None,
    policy_issuance: PolicyIssuanceEvidence | None,
    hard_boundaries: tuple[CandidateHardBoundaryAssessment, ...],
    working_time_cut: WorkingTimeEvidenceCut,
    overtime_assessments: tuple[CandidateOvertimeAssessment, ...],
    marker_support_cut: AuthorityMarkerSupportCut,
    approval_tag_assessments: tuple[CandidateApprovalTagAssessment, ...],
    cost_assessments: tuple[CandidateCostAssessment, ...],
    requirement_sets: tuple[CandidateAuthorityRequirementSet, ...],
    owner_approval_selections: tuple[OwnerApprovalSelection, ...],
    worker_consent_head_selections: tuple[WorkerConsentHeadSelection, ...],
) -> tuple[CurrentnessObservation, ...]:
    """Derive the exhaustive decision-time subject inventory for one cut.

    This is a pure projection over already-canonical inputs.  It performs no
    mutable lookup.  Atom 2 may capture the observed side from trusted stores,
    but it must use these exact subject keys and expected values.
    """

    observations: dict[tuple[CurrentnessKind, str], CurrentnessObservation] = {}

    def add(
        kind: CurrentnessKind,
        family: str,
        key: Any,
        value: Any,
        *,
        identity: str | None = None,
        revision: int | None = None,
        status: str = "CURRENT",
        evidence_state: CurrentnessEvidenceState = CurrentnessEvidenceState.PRESENT,
    ) -> None:
        semantic = canonical_json(_primitive(value))
        fingerprint = sha256_text(semantic)
        subject_key = family + "|" + canonical_json(_primitive(key))
        expected_identity = identity or family + "-" + fingerprint
        item = CurrentnessObservation(
            kind=kind,
            subject_key=subject_key,
            expected_state=evidence_state,
            observed_state=evidence_state,
            expected_identity=expected_identity,
            expected_revision=revision,
            expected_fingerprint=fingerprint,
            expected_status=status,
            expected_semantic_json=semantic,
            observed_identity=expected_identity,
            observed_revision=revision,
            observed_fingerprint=fingerprint,
            observed_status=status,
            observed_semantic_json=semantic,
        )
        subject = (kind, subject_key)
        existing = observations.get(subject)
        _require(
            existing is None or existing == item,
            "currentness_required_subject_conflict",
        )
        observations[subject] = item

    def add_absence(kind: CurrentnessKind) -> None:
        subject_key = "NO_REQUIRED_SUBJECTS"
        observations[(kind, subject_key)] = CurrentnessObservation(
            kind=kind,
            subject_key=subject_key,
            expected_state=CurrentnessEvidenceState.ABSENT,
            observed_state=CurrentnessEvidenceState.ABSENT,
            expected_identity=None,
            expected_revision=None,
            expected_fingerprint=None,
            expected_status=None,
            expected_semantic_json=None,
            observed_identity=None,
            observed_revision=None,
            observed_fingerprint=None,
            observed_status=None,
            observed_semantic_json=None,
        )

    add(
        CurrentnessKind.COMPANY_PLAN_REVISION_HEAD,
        "COMPANY_PLAN_REVISION_HEAD",
        company_plan.company_plan_id,
        {"company_plan": company_plan, "plan_revision": base_plan_revision},
        identity=base_plan_revision.revision_id,
        revision=base_plan_revision.revision,
    )
    add(
        CurrentnessKind.CURRENT_PLANNING_SCOPE,
        "CURRENT_PLANNING_SCOPE",
        company_plan.company_plan_id,
        {
            "company_plan_id": company_plan.company_plan_id,
            "base_plan_revision_id": base_plan_revision.revision_id,
            "planning_scope_fingerprint": evaluation_input.current_planning_scope_fingerprint,
        },
        identity=company_plan.company_plan_id,
        revision=base_plan_revision.revision,
    )

    historical = evaluation_input.historical_source_scope
    add(
        CurrentnessKind.M1_HANDOFF_PRECONDITIONS,
        "M1_REDUCTION_HANDOFF",
        (historical.input_namespace, historical.event_id, historical.effect_ordinal),
        historical,
        identity=historical.server_event_id,
        revision=historical.effect_ordinal,
    )
    for item in historical.job_roots:
        add(
            CurrentnessKind.M1_HANDOFF_PRECONDITIONS,
            "M1_JOB_ROOT",
            item.job_id,
            item,
            identity=item.job_id,
            revision=item.revision,
        )
    for item in historical.assignments:
        add(
            CurrentnessKind.M1_HANDOFF_PRECONDITIONS,
            "M1_ASSIGNMENT_HANDOFF",
            item.assignment_id,
            item,
            identity=item.assignment_id,
            revision=item.task_source_revision,
            status=item.task_status.value,
        )
    for item in evaluation_input.current_active_commitments:
        add(
            CurrentnessKind.M1_HANDOFF_PRECONDITIONS,
            "M1_CURRENT_COMMITMENT_HANDOFF",
            item.commitment_id,
            item,
            identity=item.source_handoff_id,
            revision=item.source_revision,
        )

    for item in evaluation_input.current_m2_task_preconditions:
        add(
            CurrentnessKind.M2_JOB_TASK_PRECONDITIONS,
            "M2_JOB_TASK",
            (item.job_id, item.task_id),
            item,
            identity=item.source_handoff_id,
            revision=item.job_execution_revision,
            status=item.task_status.value,
        )
    for item in evaluation_input.current_m2_assignment_preconditions:
        add(
            CurrentnessKind.M2_ASSIGNMENT_PRECONDITIONS,
            "M2_ASSIGNMENT",
            item.assignment_id,
            item,
            identity=item.assignment_id,
        )
    for item in plan_day_root_heads:
        add(
            CurrentnessKind.PLAN_DAY_PRECONDITIONS,
            "PLAN_DAY_ROOT_HEAD",
            item.plan_day_id,
            item,
            identity=item.plan_day_id,
            revision=item.plan_day_revision,
            status=item.status.value,
        )
    add(
        CurrentnessKind.EVALUATION_INPUT_BINDING,
        "M3_EVALUATION_INPUT",
        evaluation_input.evaluation_input_id,
        evaluation_input,
        identity=evaluation_input.evaluation_input_id,
    )

    support = feasibility_support_snapshot
    add(
        CurrentnessKind.C0_SUPPORT_SELECTED_SOURCES,
        "C0_SOURCE_CUT",
        support.company_plan_id,
        {
            "source_cut_id": support.source_cut_id,
            "source_cut_generation": support.source_cut_generation,
            "source_cut_fingerprint": support.source_cut_fingerprint,
        },
        identity=support.source_cut_id,
        revision=support.source_cut_generation,
    )
    add(
        CurrentnessKind.C0_SUPPORT_SELECTED_SOURCES,
        "C0_WORKER_REGISTRY_HEAD",
        support.company_plan_id,
        {
            "provenance_id": support.worker_registry_provenance_id,
            "provenance_fingerprint": support.worker_registry_provenance_fingerprint,
            "capture_id": support.worker_registry_capture_id,
            "capture_generation": support.worker_registry_capture_generation,
            "capture_fingerprint": support.worker_registry_capture_fingerprint,
            "registry_revision": support.worker_registry_revision,
            "registry_fingerprint": support.worker_registry_fingerprint,
        },
        identity=support.worker_registry_capture_id,
        revision=support.worker_registry_capture_generation,
    )
    add(
        CurrentnessKind.C0_SUPPORT_SELECTED_SOURCES,
        "C0_M8_CONFIGURATION",
        support.company_plan_id,
        {
            "service_catalog_version": support.m8_service_catalog_version,
            "planning_profile_version": support.m8_planning_profile_version,
            "skill_matrix_version": support.m8_skill_matrix_version,
            "vehicle_policy_version": support.m8_vehicle_policy_version,
            "configuration_fingerprint": support.m8_configuration_fingerprint,
        },
        identity=support.company_plan_id,
    )
    add(
        CurrentnessKind.C0_SUPPORT_SELECTED_SOURCES,
        "C0_SUPPORT_SNAPSHOT",
        support.support_snapshot_id,
        support,
        identity=support.support_snapshot_id,
    )

    source_groups = (
        ("C0_TASK_CONSTRAINT", support.task_constraints, lambda item: (item.job_id, item.task_id)),
        ("C0_PLAN_SCHEDULE", (support.schedule,), lambda item: item.company_plan_id),
        ("C0_WORKER_TECHNICAL", support.worker_technical_evidence, lambda item: item.worker_id),
        ("C0_WORKER_AVAILABILITY", support.worker_availability, lambda item: item.subject_id),
        ("C0_READINESS", support.readiness, lambda item: (item.job_id, item.task_id)),
        ("C0_VEHICLE_TECHNICAL", support.vehicle_technical_evidence, lambda item: item.vehicle_id),
        ("C0_VEHICLE_AVAILABILITY", support.vehicle_availability, lambda item: item.subject_id),
        (
            "C0_ROUTE",
            support.routes,
            lambda item: (
                item.origin_reference,
                item.destination_reference,
                item.transport_mode,
                item.departure_time_basis,
            ),
        ),
    )
    for family, records, key in source_groups:
        for item in records:
            add(
                CurrentnessKind.C0_SUPPORT_SELECTED_SOURCES,
                family,
                key(item),
                item,
                identity=getattr(item, "source_record_id", None),
                revision=getattr(item, "source_revision", None),
                status=(
                    getattr(item, "knowledge").value
                    if isinstance(getattr(item, "knowledge", None), StrEnum)
                    else "CURRENT"
                ),
            )

    add(
        CurrentnessKind.M3_C_RECOMPUTATION,
        "M3_C_RESULT",
        m3_result.result_id,
        m3_result,
        identity=m3_result.result_id,
    )
    for position, candidate in enumerate(m3_result.candidates):
        add(
            CurrentnessKind.M3_C_RECOMPUTATION,
            "M3_C_CANDIDATE",
            position,
            candidate,
            identity=candidate.candidate_id,
            status=candidate.verdict.value,
        )

    add(
        CurrentnessKind.M5_M3_D_CHAIN,
        "M5_SUPPORT_CUT",
        internal_cost_support.support_id,
        internal_cost_support,
        identity=internal_cost_support.support_id,
        revision=internal_cost_support.source_cut_generation,
    )
    for item in internal_cost_support.subjects:
        add(
            CurrentnessKind.M5_M3_D_CHAIN,
            "M5_COST_SUBJECT",
            item.subject_id,
            item,
            identity=item.subject_id,
        )
    for item in internal_cost_support.selections:
        add(
            CurrentnessKind.M5_M3_D_CHAIN,
            "M5_RATE_SELECTION",
            item.subject_id,
            item,
            identity=item.selection_id,
            revision=item.source_capture_generation,
            status=item.status.value,
        )
    add(
        CurrentnessKind.M5_M3_D_CHAIN,
        "M3_D_RESULT",
        m3d_result.result_id,
        m3d_result,
        identity=m3d_result.result_id,
    )
    for item in m3d_result.consequences:
        add(
            CurrentnessKind.M5_M3_D_CHAIN,
            "M3_D_CONSEQUENCE",
            item.candidate_id,
            item,
            identity=item.consequence_id,
            status=item.completeness.value,
        )

    add(
        CurrentnessKind.AUTH_0_BINDING,
        "AUTH_0_ROOT",
        authority_root.company_id,
        authority_root,
        identity=authority_root.authority_root_id,
        revision=authority_root.worker_registry_revision,
    )
    for item in trusted_principals:
        add(
            CurrentnessKind.AUTH_0_BINDING,
            "AUTH_0_PRINCIPAL",
            item.principal_id,
            item,
            identity=item.principal_id,
        )

    add(
        CurrentnessKind.POLICY_BINDING,
        "POLICY_RESOLUTION",
        authority_root.authority_root_id,
        {
            "status": policy_status,
            "profile": policy_profile,
            "issuance": policy_issuance,
        },
        identity=(
            policy_issuance.issuance_id
            if policy_issuance is not None
            else authority_root.authority_root_id
        ),
        status=policy_status.value,
    )

    owner_by_requirement = {
        item.requirement_id: item for item in owner_approval_selections
    }
    worker_by_requirement = {
        item.requirement_id: item for item in worker_consent_head_selections
    }
    for requirement_set in requirement_sets:
        for requirement in requirement_set.requirements:
            if requirement.required_principal_type is PrincipalType.OWNER:
                selection = owner_by_requirement[requirement.requirement_id]
                add(
                    CurrentnessKind.OWNER_APPROVAL_SELECTIONS,
                    "OWNER_APPROVAL_REQUIREMENT_SELECTION",
                    requirement.requirement_id,
                    {"requirement": requirement, "selection": selection},
                    identity=selection.selection_id,
                    status=selection.status.value,
                )
            else:
                selection = worker_by_requirement[requirement.requirement_id]
                add(
                    CurrentnessKind.WORKER_CONSENT_HEADS,
                    "WORKER_CONSENT_REQUIREMENT_HEAD",
                    requirement.requirement_id,
                    {"requirement": requirement, "selection": selection},
                    identity=selection.selection_id,
                    revision=selection.lineage_sequence,
                    status=(
                        selection.consent_status.value
                        if selection.consent_status is not None
                        else selection.status.value
                    ),
                )

    add(
        CurrentnessKind.WORKING_TIME_MARKER_HEADS,
        "WORKING_TIME_CUT",
        working_time_cut.company_plan_id,
        working_time_cut,
        identity=working_time_cut.cut_id,
    )
    for item in working_time_cut.source_selections:
        add(
            CurrentnessKind.WORKING_TIME_MARKER_HEADS,
            "WORKING_TIME_SOURCE_SELECTION",
            item.source_kind,
            item,
            identity=item.source_head_id or item.selection_id,
            revision=item.source_revision,
            status=item.state.value,
            evidence_state=(
                CurrentnessEvidenceState.UNKNOWN
                if item.state is WorkingTimeSourceClassState.UNKNOWN
                else CurrentnessEvidenceState.PRESENT
            ),
        )
    for item in working_time_cut.source_head_observations:
        add(
            CurrentnessKind.WORKING_TIME_MARKER_HEADS,
            "WORKING_TIME_SOURCE_HEAD",
            (item.kind, item.record_id),
            item,
            identity=item.record_id,
        )
    for item in working_time_cut.common_work_manifests:
        add(
            CurrentnessKind.WORKING_TIME_MARKER_HEADS,
            "WORKING_TIME_COMPLETE_SOURCE_MANIFEST",
            (item.source_kind, item.source_head_id),
            item,
            identity=item.source_head_id,
            revision=item.source_revision,
            status="COMPLETE" if item.coverage_complete else "INCOMPLETE",
        )
    for item in working_time_cut.rule_evidence:
        add(
            CurrentnessKind.WORKING_TIME_MARKER_HEADS,
            "WORKING_TIME_RULE_HEAD",
            (item.worker_id, item.calculation_period_start, item.calculation_period_end),
            item,
            identity=item.source_record_id,
            revision=item.source_revision,
        )
    for item in working_time_cut.work_items:
        add(
            CurrentnessKind.WORKING_TIME_MARKER_HEADS,
            "WORKING_TIME_ITEM_SOURCE_HEAD",
            (item.work_kind, item.worker_id, item.source_record_id),
            {
                "source_record_id": item.source_record_id,
                "source_revision": item.source_revision,
                "source_fingerprint": item.source_fingerprint,
                "source_capture_id": item.source_capture_id,
                "source_capture_generation": item.source_capture_generation,
                "source_capture_fingerprint": item.source_capture_fingerprint,
            },
            identity=item.source_record_id,
            revision=item.source_revision,
        )
    add(
        CurrentnessKind.WORKING_TIME_MARKER_HEADS,
        "AUTHORITY_MARKER_CUT",
        marker_support_cut.company_plan_id,
        marker_support_cut,
        identity=marker_support_cut.cut_id,
    )
    for item in marker_support_cut.source_head_observations:
        add(
            CurrentnessKind.WORKING_TIME_MARKER_HEADS,
            "AUTHORITY_MARKER_SOURCE_HEAD",
            (item.kind, item.record_id),
            item,
            identity=item.record_id,
        )

    derived_groups = (
        ("HARD_BOUNDARY", hard_boundaries, lambda item: item.candidate_position),
        ("OVERTIME_ASSESSMENT", overtime_assessments, lambda item: item.candidate_position),
        (
            "WORKING_TIME_CANDIDATE_UNIVERSE",
            working_time_cut.candidate_evidence,
            lambda item: item.candidate_position,
        ),
        (
            "MARKER_CANDIDATE_UNIVERSE",
            marker_support_cut.candidate_universes,
            lambda item: item.candidate_position,
        ),
        ("APPROVAL_TAG_ASSESSMENT", approval_tag_assessments, lambda item: item.candidate_position),
        ("COST_ASSESSMENT", cost_assessments, lambda item: item.candidate_position),
        ("AUTHORITY_REQUIREMENT_SET", requirement_sets, lambda item: item.candidate_position),
    )
    for family, records, key in derived_groups:
        for item in records:
            add(
                CurrentnessKind.DERIVED_UNIVERSES_RESULT_INPUTS,
                family,
                key(item),
                item,
                identity=(
                    getattr(item, "assessment_id", None)
                    or getattr(item, "requirement_set_id", None)
                    or getattr(item, "evidence_id", None)
                ),
            )
    for item in marker_support_cut.coverages:
        add(
            CurrentnessKind.DERIVED_UNIVERSES_RESULT_INPUTS,
            "MARKER_SUBJECT_COVERAGE",
            item.subject.marker_subject_id,
            item,
            identity=item.coverage_id,
            status="COMPLETE" if item.coverage_complete else "INCOMPLETE",
        )
    for candidate in working_time_cut.candidate_evidence:
        for comparison in candidate.comparisons:
            add(
                CurrentnessKind.DERIVED_UNIVERSES_RESULT_INPUTS,
                "WORKING_TIME_WORKER_PERIOD_COMPARISON",
                (candidate.candidate_position, comparison.period_key),
                comparison,
                identity=comparison.comparison_id,
                status="COMPLETE" if comparison.coverage_complete else "INCOMPLETE",
            )
    for universe in marker_support_cut.candidate_universes:
        for subject in universe.subjects:
            add(
                CurrentnessKind.DERIVED_UNIVERSES_RESULT_INPUTS,
                "AUTHORITY_MARKER_SUBJECT",
                subject.marker_subject_id,
                subject,
                identity=subject.marker_subject_id,
            )
    for assessment in cost_assessments:
        for subject in assessment.approval_subjects:
            add(
                CurrentnessKind.DERIVED_UNIVERSES_RESULT_INPUTS,
                "P_COST_APPROVAL_SUBJECT",
                subject.subject_id,
                subject,
                identity=subject.subject_id,
            )
    for requirement_set in requirement_sets:
        for requirement in requirement_set.requirements:
            add(
                CurrentnessKind.DERIVED_UNIVERSES_RESULT_INPUTS,
                "AUTHORITY_REQUIREMENT",
                requirement.requirement_id,
                requirement,
                identity=requirement.requirement_id,
            )

    for kind in CurrentnessKind:
        if not any(item_kind is kind for item_kind, _ in observations):
            add_absence(kind)
    return tuple(
        observations[key]
        for key in sorted(observations, key=lambda item: (item[0].value, item[1]))
    )


def _validate_upstream_integrity(
    evaluation_input: M3EvaluationInput,
    support: FeasibilitySupportSnapshot,
    m3_result: M3BoundedFeasibilityResult,
    cost_support: InternalCostSupportCut,
    m3d_result: M3InternalLaborCostConsequenceResult,
) -> None:
    try:
        _bind(
            type(evaluation_input) is M3EvaluationInput
            and deserialize_evaluation_input(serialize_evaluation_input(evaluation_input))
            == evaluation_input,
            "INVALID_EVALUATION_INPUT",
            "evaluation_input",
        )
        _bind(
            type(support) is FeasibilitySupportSnapshot
            and deserialize_feasibility_support(serialize_feasibility_support(support))
            == support,
            "INVALID_FEASIBILITY_SUPPORT",
            "feasibility_support",
        )
    except ValueError as error:
        raise OperationalAuthorityBindingError("UPSTREAM_INTEGRITY_FAILURE", "upstream") from error
    _bind(type(m3_result) is M3BoundedFeasibilityResult, "INVALID_M3_RESULT", "m3_result")
    for candidate in m3_result.candidates:
        _bind(replace(candidate) == candidate, "INVALID_M3_CANDIDATE", "m3_result.candidates")
    _bind(replace(m3_result) == m3_result, "INVALID_M3_RESULT", "m3_result")
    _bind(type(cost_support) is InternalCostSupportCut and replace(cost_support) == cost_support, "INVALID_M5_SUPPORT", "internal_cost_support")
    _bind(type(m3d_result) is M3InternalLaborCostConsequenceResult, "INVALID_M3D_RESULT", "m3d_result")
    for consequence in m3d_result.consequences:
        for line in consequence.baseline_lines + consequence.candidate_lines:
            _bind(replace(line) == line, "INVALID_M3D_LINE", "m3d_result")
        _bind(replace(consequence) == consequence, "INVALID_M3D_CONSEQUENCE", "m3d_result")
    _bind(replace(m3d_result) == m3d_result, "INVALID_M3D_RESULT", "m3d_result")


@dataclass(frozen=True, slots=True, kw_only=True)
class M3AuthorityEvidenceCut:
    company_plan: CompanyPlan
    base_plan_revision: PlanRevision
    plan_day_root_heads: tuple[PlanDayRootHeadEvidence, ...]
    evaluation_input: M3EvaluationInput
    feasibility_support_snapshot: FeasibilitySupportSnapshot
    m3_result: M3BoundedFeasibilityResult
    internal_cost_support: InternalCostSupportCut
    m3d_result: M3InternalLaborCostConsequenceResult
    authority_root: CompanyAuthorityRoot
    trusted_principals: tuple[TrustedPrincipal, ...]
    policy_status: PolicySupportStatus
    policy_profile: CompanyPolicyProfile | None
    policy_issuance: PolicyIssuanceEvidence | None
    policy_reason_code: str | None
    hard_boundaries: tuple[CandidateHardBoundaryAssessment, ...]
    working_time_rule_evidence: tuple[WorkingTimeRuleEvidence, ...]
    working_time_cut: WorkingTimeEvidenceCut
    overtime_assessments: tuple[CandidateOvertimeAssessment, ...]
    marker_evidence: tuple[AuthorityMarkerEvidence, ...]
    marker_support_cut: AuthorityMarkerSupportCut
    approval_tag_assessments: tuple[CandidateApprovalTagAssessment, ...]
    cost_assessments: tuple[CandidateCostAssessment, ...]
    requirement_sets: tuple[CandidateAuthorityRequirementSet, ...]
    owner_approval_selections: tuple[OwnerApprovalSelection, ...]
    worker_consent_head_selections: tuple[WorkerConsentHeadSelection, ...]
    currentness_vector: M3DecisionTimeCurrentnessVector
    schema_version: str = field(default=AUTHORITY_CUT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    cut_fingerprint: str = field(init=False)
    cut_id: str = field(init=False)

    def __post_init__(self) -> None:
        _require(type(self.company_plan) is CompanyPlan and replace(self.company_plan) == self.company_plan, "company_plan")
        _require(type(self.base_plan_revision) is PlanRevision, "base_plan_revision")
        _require(
            deserialize_plan_revision(serialize_plan_revision(self.base_plan_revision))
            == self.base_plan_revision,
            "base_plan_revision",
        )
        plan_day_heads = _canonical_records(
            self.plan_day_root_heads,
            PlanDayRootHeadEvidence,
            lambda item: item.plan_day_id,
            "plan_day_root_heads",
        )
        evaluation_days = {
            item.plan_day_id: item for item in self.evaluation_input.plan_day_associations
        }
        revision_days = {
            item.plan_day_id: item for item in self.base_plan_revision.plan_days
        }
        _require(
            {item.plan_day_id for item in plan_day_heads}
            == set(evaluation_days)
            == set(revision_days),
            "plan_day_root_head_coverage",
        )
        _require(
            all(
                (
                    item.worker_id,
                    item.business_date,
                )
                == (
                    evaluation_days[item.plan_day_id].worker_id,
                    evaluation_days[item.plan_day_id].business_date,
                )
                == (
                    revision_days[item.plan_day_id].worker_id,
                    revision_days[item.plan_day_id].business_date,
                )
                for item in plan_day_heads
            ),
            "plan_day_root_head_binding",
        )
        _validate_upstream_integrity(
            self.evaluation_input,
            self.feasibility_support_snapshot,
            self.m3_result,
            self.internal_cost_support,
            self.m3d_result,
        )
        _require(type(self.authority_root) is CompanyAuthorityRoot and replace(self.authority_root) == self.authority_root, "authority_root")
        principals = _canonical_records(
            self.trusted_principals,
            TrustedPrincipal,
            lambda item: item.principal_id,
            "trusted_principals",
        )
        _require(type(self.policy_status) is PolicySupportStatus, "policy_status")
        if self.policy_status is PolicySupportStatus.ISSUED_SUPPORTED:
            _require(
                type(self.policy_profile) is CompanyPolicyProfile
                and replace(self.policy_profile) == self.policy_profile
                and type(self.policy_issuance) is PolicyIssuanceEvidence
                and replace(self.policy_issuance) == self.policy_issuance
                and self.policy_reason_code is None,
                "supported_policy",
            )
            _require(
                self.policy_profile.schema_version == COMPANY_POLICY_PROFILE_SCHEMA_VERSION
                and self.policy_profile.rule_version == COMPANY_POLICY_RULE_VERSION
                and self.policy_issuance.schema_version == POLICY_ISSUANCE_SCHEMA_VERSION
                and self.policy_issuance.rule_version == POLICY_EVIDENCE_RULE_VERSION,
                "supported_policy_version",
            )
        else:
            _require(self.policy_profile is None and self.policy_issuance is None, "unsupported_policy")
            _identity(self.policy_reason_code, "policy_reason_code")

        exact_common = (
            self.company_plan.company_plan_id,
            self.base_plan_revision.company_plan_id,
            self.evaluation_input.company_plan_id,
            self.feasibility_support_snapshot.company_plan_id,
            self.m3_result.company_plan_id,
            self.internal_cost_support.company_plan_id,
            self.m3d_result.company_plan_id,
            self.authority_root.company_plan_id,
        )
        _require(len(set(exact_common)) == 1, "company_plan_binding")
        _require(
            self.company_plan.provenance_reference
            == self.authority_root.company_plan_provenance_reference,
            "company_plan_provenance_binding",
        )
        _require(
            (
                self.feasibility_support_snapshot.worker_registry_revision,
                self.feasibility_support_snapshot.worker_registry_fingerprint,
            )
            == (
                self.authority_root.worker_registry_revision,
                self.authority_root.worker_registry_fingerprint,
            ),
            "worker_registry_authority_binding",
        )
        exact_revision = (
            self.base_plan_revision.revision,
            self.base_plan_revision.revision_id,
            self.base_plan_revision.fingerprint,
        )
        _require(
            all(
                (
                    value.base_plan_revision,
                    value.base_plan_revision_id,
                    value.base_plan_revision_fingerprint,
                )
                == exact_revision
                for value in (
                    self.evaluation_input,
                    self.feasibility_support_snapshot,
                    self.internal_cost_support,
                    self.m3d_result,
                )
            ),
            "base_plan_revision_binding",
        )
        _require(
            (
                self.m3_result.source_evaluation_input_id,
                self.m3_result.source_evaluation_input_fingerprint,
                self.m3_result.source_support_snapshot_id,
                self.m3_result.source_support_snapshot_fingerprint,
            )
            == (
                self.evaluation_input.evaluation_input_id,
                self.evaluation_input.evaluation_input_fingerprint,
                self.feasibility_support_snapshot.support_snapshot_id,
                self.feasibility_support_snapshot.support_fingerprint,
            ),
            "m3_source_binding",
        )
        _require(
            (
                self.internal_cost_support.evaluation_input_id,
                self.internal_cost_support.evaluation_input_fingerprint,
                self.internal_cost_support.feasibility_support_snapshot_id,
                self.internal_cost_support.feasibility_support_snapshot_fingerprint,
                self.internal_cost_support.m3_result_id,
                self.internal_cost_support.m3_result_fingerprint,
                self.m3d_result.source_evaluation_input_id,
                self.m3d_result.source_evaluation_input_fingerprint,
                self.m3d_result.source_support_snapshot_id,
                self.m3d_result.source_support_snapshot_fingerprint,
                self.m3d_result.source_m3_result_id,
                self.m3d_result.source_m3_result_fingerprint,
                self.m3d_result.source_internal_cost_support_id,
                self.m3d_result.source_internal_cost_support_fingerprint,
                self.m3d_result.m5_rule_id,
                self.m3d_result.m5_rule_revision,
                self.m3d_result.m5_rule_fingerprint,
            )
            == (
                self.evaluation_input.evaluation_input_id,
                self.evaluation_input.evaluation_input_fingerprint,
                self.feasibility_support_snapshot.support_snapshot_id,
                self.feasibility_support_snapshot.support_fingerprint,
                self.m3_result.result_id,
                self.m3_result.result_fingerprint,
                self.evaluation_input.evaluation_input_id,
                self.evaluation_input.evaluation_input_fingerprint,
                self.feasibility_support_snapshot.support_snapshot_id,
                self.feasibility_support_snapshot.support_fingerprint,
                self.m3_result.result_id,
                self.m3_result.result_fingerprint,
                self.internal_cost_support.support_id,
                self.internal_cost_support.support_fingerprint,
                self.internal_cost_support.rule_id,
                self.internal_cost_support.rule_revision,
                self.internal_cost_support.rule_fingerprint,
            ),
            "m5_m3d_binding",
        )
        _require(
            all(
                (
                    item.authority_root_id,
                    item.authority_root_fingerprint,
                    item.company_id,
                    item.company_plan_id,
                    item.worker_registry_revision,
                    item.worker_registry_fingerprint,
                )
                == (
                    self.authority_root.authority_root_id,
                    self.authority_root.authority_root_fingerprint,
                    self.authority_root.company_id,
                    self.authority_root.company_plan_id,
                    self.authority_root.worker_registry_revision,
                    self.authority_root.worker_registry_fingerprint,
                )
                for item in principals
            ),
            "principal_authority_binding",
        )
        owner_principals = tuple(
            item for item in principals if item.principal_type is PrincipalType.OWNER
        )
        worker_principals = tuple(
            item for item in principals if item.principal_type is PrincipalType.WORKER
        )
        _require(
            len(owner_principals) == 1
            and owner_principals[0].principal_subject_id
            == self.authority_root.owner_principal_subject_id,
            "owner_principal_authority_binding",
        )
        _require(
            len({item.worker_id for item in worker_principals})
            == len(worker_principals)
            and all(
                item.worker_id in self.authority_root.worker_ids
                for item in worker_principals
            ),
            "worker_principal_authority_binding",
        )
        principals_by_id = {item.principal_id: item for item in principals}
        if self.policy_status is PolicySupportStatus.ISSUED_SUPPORTED:
            _require(
                (
                    self.policy_profile.authority_root_id,
                    self.policy_profile.authority_root_fingerprint,
                    self.policy_profile.company_id,
                    self.policy_profile.company_plan_id,
                    self.policy_profile.company_plan_provenance_reference,
                    self.policy_issuance.profile_id,
                    self.policy_issuance.profile_fingerprint,
                    self.policy_issuance.authority_root_id,
                    self.policy_issuance.authority_root_fingerprint,
                    self.policy_issuance.owner_principal_id,
                    self.policy_issuance.owner_principal_fingerprint,
                    self.policy_issuance.company_id,
                    self.policy_issuance.company_plan_id,
                    self.policy_issuance.company_plan_provenance_reference,
                )
                == (
                    self.authority_root.authority_root_id,
                    self.authority_root.authority_root_fingerprint,
                    self.authority_root.company_id,
                    self.authority_root.company_plan_id,
                    self.authority_root.company_plan_provenance_reference,
                    self.policy_profile.profile_id,
                    self.policy_profile.profile_fingerprint,
                    self.authority_root.authority_root_id,
                    self.authority_root.authority_root_fingerprint,
                    owner_principals[0].principal_id,
                    owner_principals[0].principal_fingerprint,
                    self.authority_root.company_id,
                    self.authority_root.company_plan_id,
                    self.authority_root.company_plan_provenance_reference,
                ),
                "policy_authority_binding",
            )

        candidate_keys = tuple(
            (position, item.candidate_id, item.candidate_fingerprint)
            for position, item in enumerate(self.m3_result.candidates)
        )
        hard = _ordered_records(self.hard_boundaries, CandidateHardBoundaryAssessment, "hard_boundaries")
        overtime = _ordered_records(self.overtime_assessments, CandidateOvertimeAssessment, "overtime_assessments")
        tags = _ordered_records(self.approval_tag_assessments, CandidateApprovalTagAssessment, "approval_tag_assessments")
        requirement_sets = _ordered_records(self.requirement_sets, CandidateAuthorityRequirementSet, "requirement_sets")
        for values, name in (
            (hard, "hard_boundaries"),
            (overtime, "overtime_assessments"),
            (tags, "approval_tag_assessments"),
            (requirement_sets, "requirement_sets"),
        ):
            _require(
                tuple((item.candidate_position, item.candidate_id, item.candidate_fingerprint) for item in values)
                == candidate_keys,
                name,
            )
        feasible_keys = tuple(
            key
            for key, candidate in zip(candidate_keys, self.m3_result.candidates, strict=True)
            if candidate.verdict is CandidateVerdict.FEASIBLE
        )
        costs = _ordered_records(self.cost_assessments, CandidateCostAssessment, "cost_assessments")
        _require(
            tuple((item.candidate_position, item.candidate_id, item.candidate_fingerprint) for item in costs)
            == feasible_keys,
            "cost_assessments",
        )
        expected_costs = tuple(
            assess_candidate_cost(
                candidate_position=position,
                candidate=candidate,
                support_snapshot=self.feasibility_support_snapshot,
                internal_cost_support=self.internal_cost_support,
                m3d_result=self.m3d_result,
                policy_profile=self.policy_profile,
                policy_issuance=self.policy_issuance,
                authority_root=self.authority_root,
                company_plan=self.company_plan,
                base_plan_revision=self.base_plan_revision,
            )
            for position, candidate in enumerate(self.m3_result.candidates)
            if candidate.verdict is CandidateVerdict.FEASIBLE
        )
        _require(costs == expected_costs, "cost_assessments")

        rules = _canonical_records(
            self.working_time_rule_evidence,
            WorkingTimeRuleEvidence,
            lambda item: item.rule_id,
            "working_time_rule_evidence",
        )
        _require(type(self.working_time_cut) is WorkingTimeEvidenceCut and replace(self.working_time_cut) == self.working_time_cut, "working_time_cut")
        _require(
            (
                self.working_time_cut.authority_root_id,
                self.working_time_cut.authority_root_fingerprint,
                self.working_time_cut.company_id,
                self.working_time_cut.company_plan_id,
                self.working_time_cut.worker_registry_revision,
                self.working_time_cut.worker_registry_fingerprint,
                self.working_time_cut.evaluation_input_id,
                self.working_time_cut.feasibility_support_snapshot_id,
                self.working_time_cut.m3_result_id,
            )
            == (
                self.authority_root.authority_root_id,
                self.authority_root.authority_root_fingerprint,
                self.authority_root.company_id,
                self.company_plan.company_plan_id,
                self.authority_root.worker_registry_revision,
                self.authority_root.worker_registry_fingerprint,
                self.evaluation_input.evaluation_input_id,
                self.feasibility_support_snapshot.support_snapshot_id,
                self.m3_result.result_id,
            ),
            "working_time_cut_binding",
        )
        _require(
            {(item.rule_id, item.rule_fingerprint) for item in rules}
            == {
                (item.rule_id, item.rule_fingerprint)
                for item in self.working_time_cut.rule_evidence
            },
            "working_time_rule_cut_binding",
        )
        _require(
            all(item.worker_id in self.authority_root.worker_ids for item in rules)
            and all(
                item.worker_id in self.authority_root.worker_ids
                for item in self.working_time_cut.work_items
            ),
            "working_time_worker_registry_binding",
        )
        _validate_working_time_cut_against_candidates(
            self.working_time_cut,
            self.feasibility_support_snapshot,
            self.m3_result,
        )
        _require(
            tuple(assess_candidate_overtime(position, candidate, self.working_time_cut) for position, candidate in enumerate(self.m3_result.candidates))
            == overtime,
            "overtime_assessments",
        )
        baseline_by_id = {
            item.commitment_id: item
            for item in self.feasibility_support_snapshot.schedule.placements
        }
        for position, candidate in enumerate(self.m3_result.candidates):
            relevant_workers = {
                worker_id
                for commitment_id in candidate.modified_commitment_ids
                for worker_id in baseline_by_id[commitment_id].worker_ids
            }
            relevant_workers.update(
                item.worker_id for item in candidate.proposed_worker_placements
            )
            candidate_working = self.working_time_cut.candidate_evidence[position]
            if candidate_working.universe_complete:
                _require(
                    {item.worker_id for item in candidate_working.comparisons}
                    == relevant_workers,
                    "overtime_worker_universe",
                )
        marker_evidence = _canonical_records(
            self.marker_evidence,
            AuthorityMarkerEvidence,
            lambda item: item.evidence_id,
            "marker_evidence",
        )
        _require(type(self.marker_support_cut) is AuthorityMarkerSupportCut and replace(self.marker_support_cut) == self.marker_support_cut, "marker_support_cut")
        _require(
            (
                self.marker_support_cut.authority_root_id,
                self.marker_support_cut.authority_root_fingerprint,
                self.marker_support_cut.company_id,
                self.marker_support_cut.company_plan_id,
                self.marker_support_cut.worker_registry_revision,
                self.marker_support_cut.worker_registry_fingerprint,
                self.marker_support_cut.evaluation_input_id,
                self.marker_support_cut.feasibility_support_snapshot_id,
                self.marker_support_cut.m3_result_id,
            )
            == (
                self.authority_root.authority_root_id,
                self.authority_root.authority_root_fingerprint,
                self.authority_root.company_id,
                self.company_plan.company_plan_id,
                self.authority_root.worker_registry_revision,
                self.authority_root.worker_registry_fingerprint,
                self.evaluation_input.evaluation_input_id,
                self.feasibility_support_snapshot.support_snapshot_id,
                self.m3_result.result_id,
            ),
            "marker_support_cut_binding",
        )
        cut_marker_ids = {
            item.evidence_id
            for coverage in self.marker_support_cut.coverages
            for item in coverage.evidence
        }
        _require({item.evidence_id for item in marker_evidence} == cut_marker_ids, "marker_evidence_cut_binding")
        _require(
            all(
                item.worker_id is None
                or item.worker_id in self.authority_root.worker_ids
                for item in marker_evidence
            ),
            "marker_worker_registry_binding",
        )
        expected_universes = tuple(
            CandidateMarkerSubjectUniverse(
                candidate_position=position,
                candidate_id=candidate.candidate_id,
                candidate_fingerprint=candidate.candidate_fingerprint,
                subjects=derive_authority_marker_subjects(candidate, self.feasibility_support_snapshot),
            )
            for position, candidate in enumerate(self.m3_result.candidates)
        )
        _require(self.marker_support_cut.candidate_universes == expected_universes, "marker_subject_universes")
        _require(
            tuple(assess_candidate_approval_tags(position, candidate, self.marker_support_cut) for position, candidate in enumerate(self.m3_result.candidates))
            == tags,
            "approval_tag_assessments",
        )

        cost_by_candidate = {item.candidate_id: item for item in costs}
        tag_by_candidate = {item.candidate_id: item for item in tags}
        expected_requirements = tuple(
            derive_authority_requirements(
                candidate_position=position,
                candidate=candidate,
                support_snapshot=self.feasibility_support_snapshot,
                cost_assessment=cost_by_candidate.get(candidate.candidate_id),
                tag_assessment=tag_by_candidate[candidate.candidate_id],
            )
            for position, candidate in enumerate(self.m3_result.candidates)
        )
        _require(requirement_sets == expected_requirements, "authority_requirement_sets")

        owner_selections = _canonical_records(
            self.owner_approval_selections,
            OwnerApprovalSelection,
            lambda item: item.requirement_id,
            "owner_approval_selections",
        )
        consent_selections = _canonical_records(
            self.worker_consent_head_selections,
            WorkerConsentHeadSelection,
            lambda item: item.requirement_id,
            "worker_consent_head_selections",
        )
        requirements = tuple(
            item for requirement_set in requirement_sets for item in requirement_set.requirements
        )
        owner_requirements = {
            item.requirement_id: item
            for item in requirements
            if item.required_principal_type is PrincipalType.OWNER
        }
        worker_requirements = {
            item.requirement_id: item
            for item in requirements
            if item.required_principal_type is PrincipalType.WORKER
        }
        _require({item.requirement_id for item in owner_selections} == set(owner_requirements), "owner_approval_selection_coverage")
        _require({item.requirement_id for item in consent_selections} == set(worker_requirements), "worker_consent_selection_coverage")
        for selection in owner_selections:
            requirement = owner_requirements[selection.requirement_id]
            selected_principal = (
                principals_by_id.get(selection.owner_principal_id)
                if selection.status is SelectionStatus.SELECTED
                else None
            )
            _require(
                selection.requirement_fingerprint == requirement.requirement_fingerprint
                and selection.scope == requirement.scope
                and selection.authority_root_id == self.authority_root.authority_root_id,
                "owner_approval_selection_binding",
            )
            _require(
                selection.status is SelectionStatus.ABSENT
                or (
                    selected_principal is owner_principals[0]
                    and selection.owner_principal_fingerprint
                    == selected_principal.principal_fingerprint
                ),
                "owner_approval_principal_binding",
            )
        for selection in consent_selections:
            requirement = worker_requirements[selection.requirement_id]
            selected_principal = (
                principals_by_id.get(selection.worker_principal_id)
                if selection.status is SelectionStatus.SELECTED
                else None
            )
            _require(
                selection.requirement_fingerprint == requirement.requirement_fingerprint
                and selection.scope == requirement.scope
                and selection.worker_id == requirement.required_worker_id
                and selection.authority_root_id == self.authority_root.authority_root_id,
                "worker_consent_selection_binding",
            )
            _require(
                selection.status is SelectionStatus.ABSENT
                or (
                    selected_principal is not None
                    and selected_principal.principal_type is PrincipalType.WORKER
                    and selected_principal.worker_id == selection.worker_id
                    and selection.worker_principal_fingerprint
                    == selected_principal.principal_fingerprint
                ),
                "worker_consent_principal_binding",
            )
        reused_approvals: dict[str, tuple[str, str]] = {}
        for selection in owner_selections:
            if selection.approval_id is None:
                continue
            semantic_scope = authority_evidence_scope_semantic_json(selection.scope)
            binding = (selection.authority_root_id, semantic_scope)
            _require(
                selection.approval_id not in reused_approvals
                or reused_approvals[selection.approval_id] == binding,
                "owner_approval_cross_scope_reuse",
            )
            reused_approvals[selection.approval_id] = binding
        reused_consents: dict[str, tuple[str, str, str]] = {}
        for selection in consent_selections:
            if selection.consent_id is None:
                continue
            binding = (
                selection.authority_root_id,
                selection.worker_id,
                authority_evidence_scope_semantic_json(selection.scope),
            )
            _require(
                selection.consent_id not in reused_consents
                or reused_consents[selection.consent_id] == binding,
                "worker_consent_cross_scope_reuse",
            )
            reused_consents[selection.consent_id] = binding

        _require(type(self.currentness_vector) is M3DecisionTimeCurrentnessVector and replace(self.currentness_vector) == self.currentness_vector, "currentness_vector")
        vector = self.currentness_vector
        expected_policy = (
            (self.policy_profile.profile_id, self.policy_profile.profile_fingerprint, self.policy_issuance.issuance_id, self.policy_issuance.issuance_fingerprint)
            if self.policy_status is PolicySupportStatus.ISSUED_SUPPORTED
            else (None, None, None, None)
        )
        _require(
            (
                vector.company_plan_id,
                vector.company_plan_provenance_reference,
                vector.base_plan_revision,
                vector.base_plan_revision_id,
                vector.base_plan_revision_fingerprint,
                vector.current_planning_scope_fingerprint,
                vector.evaluation_input_id,
                vector.evaluation_input_fingerprint,
                vector.c0_source_cut_id,
                vector.c0_source_cut_fingerprint,
                vector.feasibility_support_snapshot_id,
                vector.feasibility_support_snapshot_fingerprint,
                vector.m3_result_id,
                vector.m3_result_fingerprint,
                vector.internal_cost_support_id,
                vector.internal_cost_support_fingerprint,
                vector.m3d_result_id,
                vector.m3d_result_fingerprint,
                vector.authority_root_id,
                vector.authority_root_fingerprint,
                vector.company_id,
                vector.worker_registry_revision,
                vector.worker_registry_fingerprint,
                vector.policy_status,
                vector.policy_profile_id,
                vector.policy_profile_fingerprint,
                vector.policy_issuance_id,
                vector.policy_issuance_fingerprint,
                vector.working_time_cut_id,
                vector.working_time_cut_fingerprint,
                vector.marker_cut_id,
                vector.marker_cut_fingerprint,
            )
            == (
                self.company_plan.company_plan_id,
                self.company_plan.provenance_reference,
                self.base_plan_revision.revision,
                self.base_plan_revision.revision_id,
                self.base_plan_revision.fingerprint,
                self.evaluation_input.current_planning_scope_fingerprint,
                self.evaluation_input.evaluation_input_id,
                self.evaluation_input.evaluation_input_fingerprint,
                self.feasibility_support_snapshot.source_cut_id,
                self.feasibility_support_snapshot.source_cut_fingerprint,
                self.feasibility_support_snapshot.support_snapshot_id,
                self.feasibility_support_snapshot.support_fingerprint,
                self.m3_result.result_id,
                self.m3_result.result_fingerprint,
                self.internal_cost_support.support_id,
                self.internal_cost_support.support_fingerprint,
                self.m3d_result.result_id,
                self.m3d_result.result_fingerprint,
                self.authority_root.authority_root_id,
                self.authority_root.authority_root_fingerprint,
                self.authority_root.company_id,
                self.authority_root.worker_registry_revision,
                self.authority_root.worker_registry_fingerprint,
                self.policy_status,
                *expected_policy,
                self.working_time_cut.cut_id,
                self.working_time_cut.cut_fingerprint,
                self.marker_support_cut.cut_id,
                self.marker_support_cut.cut_fingerprint,
            ),
            "currentness_vector_binding",
        )
        _require(
            set(
                zip(
                    vector.trusted_principal_ids,
                    vector.trusted_principal_fingerprints,
                    strict=True,
                )
            )
            == {
                (item.principal_id, item.principal_fingerprint)
                for item in principals
            }
            and set(
                zip(
                    vector.authority_requirement_set_ids,
                    vector.authority_requirement_set_fingerprints,
                    strict=True,
                )
            )
            == {
                (item.requirement_set_id, item.requirement_set_fingerprint)
                for item in requirement_sets
            }
            and set(
                zip(
                    vector.owner_approval_selection_ids,
                    vector.owner_approval_selection_fingerprints,
                    strict=True,
                )
            )
            == {
                (item.selection_id, item.selection_fingerprint)
                for item in owner_selections
            }
            and set(
                zip(
                    vector.worker_consent_selection_ids,
                    vector.worker_consent_selection_fingerprints,
                    strict=True,
                )
            )
            == {
                (item.selection_id, item.selection_fingerprint)
                for item in consent_selections
            },
            "currentness_authority_binding",
        )
        required_currentness = derive_required_currentness_observations(
            company_plan=self.company_plan,
            base_plan_revision=self.base_plan_revision,
            plan_day_root_heads=plan_day_heads,
            evaluation_input=self.evaluation_input,
            feasibility_support_snapshot=self.feasibility_support_snapshot,
            m3_result=self.m3_result,
            internal_cost_support=self.internal_cost_support,
            m3d_result=self.m3d_result,
            authority_root=self.authority_root,
            trusted_principals=principals,
            policy_status=self.policy_status,
            policy_profile=self.policy_profile,
            policy_issuance=self.policy_issuance,
            hard_boundaries=hard,
            working_time_cut=self.working_time_cut,
            overtime_assessments=overtime,
            marker_support_cut=self.marker_support_cut,
            approval_tag_assessments=tags,
            cost_assessments=costs,
            requirement_sets=requirement_sets,
            owner_approval_selections=owner_selections,
            worker_consent_head_selections=consent_selections,
        )
        expected_by_subject = {
            (item.kind, item.subject_key): item for item in required_currentness
        }
        observed_by_subject = {
            (item.kind, item.subject_key): item for item in vector.observations
        }
        _require(
            set(observed_by_subject) == set(expected_by_subject),
            "currentness_required_subject_coverage",
        )
        for subject, expected in expected_by_subject.items():
            observed = observed_by_subject[subject]
            _require(
                (
                    observed.expected_state,
                    observed.expected_identity,
                    observed.expected_revision,
                    observed.expected_fingerprint,
                    observed.expected_status,
                    observed.expected_semantic_json,
                )
                == (
                    expected.expected_state,
                    expected.expected_identity,
                    expected.expected_revision,
                    expected.expected_fingerprint,
                    expected.expected_status,
                    expected.expected_semantic_json,
                ),
                "currentness_required_subject_expectation",
            )
        object.__setattr__(self, "trusted_principals", principals)
        object.__setattr__(self, "plan_day_root_heads", plan_day_heads)
        object.__setattr__(self, "hard_boundaries", hard)
        object.__setattr__(self, "working_time_rule_evidence", rules)
        object.__setattr__(self, "overtime_assessments", overtime)
        object.__setattr__(self, "marker_evidence", marker_evidence)
        object.__setattr__(self, "approval_tag_assessments", tags)
        object.__setattr__(self, "cost_assessments", costs)
        object.__setattr__(self, "requirement_sets", requirement_sets)
        object.__setattr__(self, "owner_approval_selections", owner_selections)
        object.__setattr__(self, "worker_consent_head_selections", consent_selections)
        digest = sha256_text(m3_authority_evidence_cut_semantic_json(self))
        object.__setattr__(self, "cut_fingerprint", digest)
        object.__setattr__(self, "cut_id", "m3e1-authority-evidence-cut-" + digest)


def m3_authority_evidence_cut_semantic_json(value: M3AuthorityEvidenceCut) -> str:
    return _semantic_json(value, ("cut_fingerprint", "cut_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorityGateAssessment:
    gate: AuthorityGateCode
    decision: GateDecision
    reason_codes: tuple[str, ...]
    requirement_ids: tuple[str, ...]
    requirement_fingerprints: tuple[str, ...]
    schema_version: str = field(default=GATE_ASSESSMENT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    assessment_fingerprint: str = field(init=False)
    assessment_id: str = field(init=False)

    def __post_init__(self) -> None:
        _require(type(self.gate) is AuthorityGateCode, "gate")
        _require(type(self.decision) is GateDecision, "gate_decision")
        reasons = tuple(self.reason_codes)
        for reason in reasons:
            _identity(reason, "gate_reason_code")
        ids = tuple(self.requirement_ids)
        fingerprints = tuple(self.requirement_fingerprints)
        _require(len(ids) == len(fingerprints) and len(ids) == len(set(ids)), "gate_requirements")
        pairs = tuple(sorted(zip(ids, fingerprints, strict=True)))
        for record_id, fingerprint in pairs:
            _bound_id(record_id, fingerprint, "m3e1-authority-requirement-", "gate_requirement")
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "requirement_ids", tuple(item[0] for item in pairs))
        object.__setattr__(self, "requirement_fingerprints", tuple(item[1] for item in pairs))
        digest = sha256_text(authority_gate_assessment_semantic_json(self))
        object.__setattr__(self, "assessment_fingerprint", digest)
        object.__setattr__(self, "assessment_id", "m3e1-authority-gate-assessment-" + digest)


def authority_gate_assessment_semantic_json(value: AuthorityGateAssessment) -> str:
    return _semantic_json(value, ("assessment_fingerprint", "assessment_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateAuthorityAssessment:
    candidate_position: int
    candidate_id: str
    candidate_fingerprint: str
    authority_evidence_cut_id: str
    authority_evidence_cut_fingerprint: str
    technical_feasibility: TechnicalFeasibility
    authority_outcome: AuthorityOutcome
    policy_relation: PolicyRelation
    override_status: OverrideStatus
    execution_authorization: ExecutionAuthorization
    disposition: Disposition
    gate_assessments: tuple[AuthorityGateAssessment, ...]
    authority_requirements: tuple[AuthorityRequirement, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = field(default=CANDIDATE_ASSESSMENT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    assessment_fingerprint: str = field(init=False)
    assessment_id: str = field(init=False)

    def __post_init__(self) -> None:
        _revision(self.candidate_position, "candidate_position")
        _candidate_binding(self.candidate_id, self.candidate_fingerprint)
        _bound_id(self.authority_evidence_cut_id, self.authority_evidence_cut_fingerprint, "m3e1-authority-evidence-cut-", "authority_evidence_cut")
        for value, expected, name in (
            (self.technical_feasibility, TechnicalFeasibility, "technical_feasibility"),
            (self.authority_outcome, AuthorityOutcome, "authority_outcome"),
            (self.policy_relation, PolicyRelation, "policy_relation"),
            (self.override_status, OverrideStatus, "override_status"),
            (self.execution_authorization, ExecutionAuthorization, "execution_authorization"),
            (self.disposition, Disposition, "disposition"),
        ):
            _require(type(value) is expected, name)
        gates = _ordered_records(self.gate_assessments, AuthorityGateAssessment, "gate_assessments")
        _require(tuple(item.gate for item in gates) == tuple(AuthorityGateCode), "gate_assessments")
        requirements = _canonical_records(
            self.authority_requirements,
            AuthorityRequirement,
            lambda item: item.requirement_id,
            "authority_requirements",
        )
        _require(all(item.candidate_id == self.candidate_id for item in requirements), "authority_requirements")
        for gate in AuthorityGateCode:
            assessment = gates[tuple(AuthorityGateCode).index(gate)]
            expected = tuple(
                sorted(
                    (
                        item.requirement_id,
                        item.requirement_fingerprint,
                    )
                    for item in requirements
                    if item.gate is gate
                )
            )
            _require(
                tuple(
                    zip(
                        assessment.requirement_ids,
                        assessment.requirement_fingerprints,
                        strict=True,
                    )
                )
                == expected,
                "gate_requirement_binding",
            )
        reasons = tuple(self.reason_codes)
        for reason in reasons:
            _identity(reason, "candidate_reason_code")
        if self.authority_outcome is AuthorityOutcome.ACT:
            _require(
                self.technical_feasibility is TechnicalFeasibility.FEASIBLE
                and self.execution_authorization is ExecutionAuthorization.ALLOWED
                and self.disposition in (Disposition.EXECUTE, Disposition.EXECUTE_WITH_RECORDED_RISK)
                and all(item.decision is GateDecision.PASS for item in gates),
                "act_projection",
            )
            if self.policy_relation is PolicyRelation.SOFT_EXCEPTION:
                _require(
                    self.override_status is OverrideStatus.APPROVED
                    and self.disposition
                    is Disposition.EXECUTE_WITH_RECORDED_RISK,
                    "approved_soft_exception_projection",
                )
            else:
                _require(
                    self.policy_relation is PolicyRelation.WITHIN_ENVELOPE
                    and self.override_status is OverrideStatus.NOT_REQUIRED
                    and self.disposition is Disposition.EXECUTE,
                    "within_envelope_act_projection",
                )
        else:
            _require(
                self.execution_authorization is ExecutionAuthorization.BLOCKED
                and self.disposition in (Disposition.ABSTAIN, Disposition.FORBIDDEN),
                "non_act_projection",
            )
        _require(
            (self.policy_relation is PolicyRelation.HARD_BLOCK)
            == (self.disposition is Disposition.FORBIDDEN),
            "hard_block_projection",
        )
        _require(
            self.policy_relation is not PolicyRelation.HARD_BLOCK
            or self.authority_outcome is AuthorityOutcome.BLOCK,
            "hard_block_outcome",
        )
        object.__setattr__(self, "gate_assessments", gates)
        object.__setattr__(self, "authority_requirements", requirements)
        object.__setattr__(self, "reason_codes", reasons)
        digest = sha256_text(candidate_authority_assessment_semantic_json(self))
        object.__setattr__(self, "assessment_fingerprint", digest)
        object.__setattr__(self, "assessment_id", "m3e1-candidate-authority-assessment-" + digest)


def candidate_authority_assessment_semantic_json(value: CandidateAuthorityAssessment) -> str:
    return _semantic_json(value, ("assessment_fingerprint", "assessment_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class M3OperationalAuthorityResult:
    authority_evidence_cut_id: str
    authority_evidence_cut_fingerprint: str
    source_evaluation_input_id: str
    source_evaluation_input_fingerprint: str
    source_m3_result_id: str
    source_m3_result_fingerprint: str
    source_m3d_result_id: str
    source_m3d_result_fingerprint: str
    evaluation_status: M3AuthorityEvaluationStatus
    search_outcome: SearchOutcome
    search_envelope_exhausted: bool
    search_authority_projection: SearchAuthorityProjection
    top_level_reason_codes: tuple[str, ...]
    candidate_assessments: tuple[CandidateAuthorityAssessment, ...]
    schema_version: str = field(default=AUTHORITY_RESULT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=OPERATIONAL_AUTHORITY_RULE_VERSION, init=False)
    result_fingerprint: str = field(init=False)
    result_id: str = field(init=False)

    def __post_init__(self) -> None:
        _bound_id(self.authority_evidence_cut_id, self.authority_evidence_cut_fingerprint, "m3e1-authority-evidence-cut-", "authority_evidence_cut")
        _bound_id(self.source_evaluation_input_id, self.source_evaluation_input_fingerprint, "m3-evaluation-input-", "source_evaluation_input")
        _bound_id(self.source_m3_result_id, self.source_m3_result_fingerprint, "m3-feasibility-result-", "source_m3_result")
        _bound_id(self.source_m3d_result_id, self.source_m3d_result_fingerprint, "m3-internal-labor-cost-result-", "source_m3d_result")
        _require(type(self.evaluation_status) is M3AuthorityEvaluationStatus, "evaluation_status")
        _require(type(self.search_outcome) is SearchOutcome, "search_outcome")
        _require(type(self.search_envelope_exhausted) is bool, "search_envelope_exhausted")
        _require(type(self.search_authority_projection) is SearchAuthorityProjection, "search_authority_projection")
        reasons = tuple(self.top_level_reason_codes)
        for reason in reasons:
            _identity(reason, "top_level_reason_code")
        _require(len(reasons) == len(set(reasons)), "top_level_reason_codes")
        assessments = _ordered_records(self.candidate_assessments, CandidateAuthorityAssessment, "candidate_assessments")
        _require(tuple(item.candidate_position for item in assessments) == tuple(range(len(assessments))), "candidate_assessments")
        _require(
            all(
                (
                    item.authority_evidence_cut_id,
                    item.authority_evidence_cut_fingerprint,
                )
                == (
                    self.authority_evidence_cut_id,
                    self.authority_evidence_cut_fingerprint,
                )
                for item in assessments
            ),
            "candidate_assessment_cut_binding",
        )
        if self.evaluation_status is M3AuthorityEvaluationStatus.NO_ACTION_REQUIRED:
            _require(
                self.search_outcome is SearchOutcome.NO_REPAIR_REQUIRED
                and not assessments
                and self.search_authority_projection is SearchAuthorityProjection.NOT_APPLICABLE
                and not reasons,
                "no_action_required",
            )
        else:
            _require(self.search_outcome is not SearchOutcome.NO_REPAIR_REQUIRED, "candidates_assessed")
            any_feasible = any(
                item.technical_feasibility is TechnicalFeasibility.FEASIBLE
                for item in assessments
            )
            if self.search_envelope_exhausted and not any_feasible:
                expected_search = (
                    SearchAuthorityProjection.ABSTAIN,
                    ("ENVELOPE_EXHAUSTED",),
                )
            elif any_feasible:
                expected_search = (SearchAuthorityProjection.NOT_APPLICABLE, ())
            elif self.search_outcome in (
                SearchOutcome.INSUFFICIENT_CRITICAL_EVIDENCE,
                SearchOutcome.CREW_REPAIR_UNSUPPORTED,
            ):
                expected_search = (
                    SearchAuthorityProjection.ABSTAIN,
                    (self.search_outcome.value,),
                )
            else:
                expected_search = (SearchAuthorityProjection.NOT_APPLICABLE, ())
            _require(
                (self.search_authority_projection, reasons) == expected_search,
                "search_authority_projection",
            )
        object.__setattr__(self, "top_level_reason_codes", reasons)
        object.__setattr__(self, "candidate_assessments", assessments)
        digest = sha256_text(m3_operational_authority_result_semantic_json(self))
        object.__setattr__(self, "result_fingerprint", digest)
        object.__setattr__(self, "result_id", "m3e1-operational-authority-result-" + digest)


def m3_operational_authority_result_semantic_json(value: M3OperationalAuthorityResult) -> str:
    return _semantic_json(value, ("result_fingerprint", "result_id"))


def serialize_operational_authority_result(value: M3OperationalAuthorityResult) -> str:
    _require(type(value) is M3OperationalAuthorityResult and replace(value) == value, "authority_result")
    return canonical_json({item.name: _primitive(getattr(value, item.name)) for item in fields(value)})


def _gate_assessment(
    gate: AuthorityGateCode,
    decision: GateDecision,
    reasons: tuple[str, ...] = (),
    requirements: tuple[AuthorityRequirement, ...] = (),
) -> AuthorityGateAssessment:
    return AuthorityGateAssessment(
        gate=gate,
        decision=decision,
        reason_codes=reasons,
        requirement_ids=tuple(item.requirement_id for item in requirements),
        requirement_fingerprints=tuple(item.requirement_fingerprint for item in requirements),
    )


def _technical_feasibility(candidate: M3RepairCandidate) -> TechnicalFeasibility:
    if candidate.verdict is CandidateVerdict.FEASIBLE:
        return TechnicalFeasibility.FEASIBLE
    if any(item.status is ConstraintStatus.UNKNOWN for item in candidate.rejection_trace):
        return TechnicalFeasibility.UNKNOWN
    return TechnicalFeasibility.INFEASIBLE


def _candidate_assessment(
    cut: M3AuthorityEvidenceCut,
    position: int,
    candidate: M3RepairCandidate,
) -> CandidateAuthorityAssessment:
    hard = cut.hard_boundaries[position]
    overtime = cut.overtime_assessments[position]
    tag = cut.approval_tag_assessments[position]
    requirement_set = cut.requirement_sets[position]
    requirements = requirement_set.requirements
    cost = next((item for item in cut.cost_assessments if item.candidate_id == candidate.candidate_id), None)
    owner_by_requirement = {item.requirement_id: item for item in cut.owner_approval_selections}
    consent_by_requirement = {item.requirement_id: item for item in cut.worker_consent_head_selections}
    by_gate = {
        gate: tuple(item for item in requirements if item.gate is gate)
        for gate in AuthorityGateCode
    }
    owner_requirements = tuple(
        item for item in requirements if item.required_principal_type is PrincipalType.OWNER
    )
    missing_owner = tuple(
        item
        for item in owner_requirements
        if owner_by_requirement[item.requirement_id].status is not SelectionStatus.SELECTED
    )
    worker_requirements = tuple(
        item for item in requirements if item.required_principal_type is PrincipalType.WORKER
    )
    nonaffirmative_consent = tuple(
        item
        for item in worker_requirements
        if not consent_by_requirement[item.requirement_id].is_affirmative_current_consent
    )
    window_failures = tuple(
        item
        for item in candidate.rejection_trace
        if item.status is ConstraintStatus.FAIL
        and item.reason_code is ReasonCode.CUSTOMER_WINDOW_VIOLATION
    )
    ordinary_failures = tuple(
        item
        for item in candidate.rejection_trace
        if item.status is ConstraintStatus.FAIL
        and item.reason_code is not ReasonCode.CUSTOMER_WINDOW_VIOLATION
    )
    unknown_constraints = tuple(
        item for item in candidate.rejection_trace if item.status is ConstraintStatus.UNKNOWN
    )
    partial = bool(candidate.impact.residual_unresolved_impact)
    later_day = any(
        item.business_date != cut.evaluation_input.business_date
        for item in candidate.proposed_worker_placements
    )

    cost_gate = _gate_assessment(AuthorityGateCode.P_COST, GateDecision.PASS)
    if cost is not None:
        cost_requirements = by_gate[AuthorityGateCode.P_COST]
        missing_cost = tuple(item for item in missing_owner if item.gate is AuthorityGateCode.P_COST)
        if cost.applicability is CostApplicability.UNKNOWN:
            cost_gate = _gate_assessment(
                AuthorityGateCode.P_COST,
                GateDecision.INDETERMINATE,
                ("INCOMPLETE_INTERNAL_LABOR_COST_CONSEQUENCE",),
                cost_requirements,
            )
        elif missing_cost:
            cost_gate = _gate_assessment(
                AuthorityGateCode.P_COST,
                GateDecision.NEEDS_HUMAN_AUTHORITY,
                ("P_COST_OWNER_APPROVAL_REQUIRED",),
                cost_requirements,
            )
        else:
            cost_gate = _gate_assessment(
                AuthorityGateCode.P_COST, GateDecision.PASS, (), cost_requirements
            )

    overtime_gate = {
        OvertimeApplicability.DOES_NOT_CREATE_OVERTIME: _gate_assessment(
            AuthorityGateCode.P_OVERTIME, GateDecision.PASS
        ),
        OvertimeApplicability.UNKNOWN: _gate_assessment(
            AuthorityGateCode.P_OVERTIME,
            GateDecision.INDETERMINATE,
            ("OVERTIME_EVIDENCE_UNKNOWN",),
        ),
        OvertimeApplicability.CREATES_OVERTIME: _gate_assessment(
            AuthorityGateCode.P_OVERTIME,
            GateDecision.NEEDS_HUMAN_AUTHORITY,
            ("P_OVERTIME_AUTHORITY_REQUIRED",),
        ),
    }[overtime.applicability]

    tag_requirements = by_gate[AuthorityGateCode.P_TAG]
    missing_tag = tuple(item for item in missing_owner if item.gate is AuthorityGateCode.P_TAG)
    if tag.applicability is ApprovalMarkerApplicability.UNKNOWN:
        tag_gate = _gate_assessment(
            AuthorityGateCode.P_TAG,
            GateDecision.INDETERMINATE,
            ("AUTHORITY_MARKER_EVIDENCE_UNKNOWN",),
            tag_requirements,
        )
    elif missing_tag:
        tag_gate = _gate_assessment(
            AuthorityGateCode.P_TAG,
            GateDecision.NEEDS_HUMAN_AUTHORITY,
            ("P_TAG_OWNER_APPROVAL_REQUIRED",),
            tag_requirements,
        )
    else:
        tag_gate = _gate_assessment(
            AuthorityGateCode.P_TAG, GateDecision.PASS, (), tag_requirements
        )

    vehicle_requirements = by_gate[AuthorityGateCode.P_VEHICLE]
    missing_vehicle_owner = tuple(
        item for item in missing_owner if item.gate is AuthorityGateCode.P_VEHICLE
    )
    if nonaffirmative_consent:
        vehicle_gate = _gate_assessment(
            AuthorityGateCode.P_VEHICLE,
            GateDecision.INDETERMINATE,
            ("CURRENT_FREELY_GIVEN_WORKER_CONSENT_REQUIRED",),
            vehicle_requirements,
        )
    elif missing_vehicle_owner:
        vehicle_gate = _gate_assessment(
            AuthorityGateCode.P_VEHICLE,
            GateDecision.NEEDS_HUMAN_AUTHORITY,
            ("P_VEHICLE_OWNER_APPROVAL_REQUIRED",),
            vehicle_requirements,
        )
    else:
        vehicle_gate = _gate_assessment(
            AuthorityGateCode.P_VEHICLE, GateDecision.PASS, (), vehicle_requirements
        )

    if hard.applicability is HardBoundaryApplicability.APPLICABLE:
        assign_gate = _gate_assessment(
            AuthorityGateCode.P_ASSIGN,
            GateDecision.HARD_BLOCK,
            (hard.reason_code,),
        )
    elif hard.applicability is HardBoundaryApplicability.UNKNOWN:
        assign_gate = _gate_assessment(
            AuthorityGateCode.P_ASSIGN,
            GateDecision.INDETERMINATE,
            (hard.reason_code,),
        )
    elif ordinary_failures:
        assign_gate = _gate_assessment(
            AuthorityGateCode.P_ASSIGN,
            GateDecision.HARD_BLOCK,
            tuple(item.reason_code.value for item in ordinary_failures),
        )
    elif unknown_constraints:
        assign_gate = _gate_assessment(
            AuthorityGateCode.P_ASSIGN,
            GateDecision.INDETERMINATE,
            tuple(item.reason_code.value for item in unknown_constraints),
        )
    else:
        assign_gate = _gate_assessment(AuthorityGateCode.P_ASSIGN, GateDecision.PASS)

    window_gate = (
        _gate_assessment(
            AuthorityGateCode.P_WINDOW_01,
            GateDecision.NEEDS_HUMAN_AUTHORITY,
            ("OWNER_DECISION_REQUIRED_FOR_CUSTOMER_PROMISE",),
        )
        if window_failures
        else _gate_assessment(AuthorityGateCode.P_WINDOW_01, GateDecision.PASS)
    )
    horizon_gate = (
        _gate_assessment(
            AuthorityGateCode.P_HORIZON,
            GateDecision.NEEDS_HUMAN_AUTHORITY,
            ("LATER_OPERATIONAL_DAY_AUTHORITY_REQUIRED",),
        )
        if later_day
        else _gate_assessment(AuthorityGateCode.P_HORIZON, GateDecision.PASS)
    )
    search_gate = (
        _gate_assessment(
            AuthorityGateCode.P_SEARCH,
            GateDecision.INDETERMINATE,
            ("ENVELOPE_EXHAUSTED",),
        )
        if cut.m3_result.search_envelope_exhausted
        and not cut.m3_result.feasible_candidate_ids
        else _gate_assessment(AuthorityGateCode.P_SEARCH, GateDecision.PASS)
    )
    partial_gate = (
        _gate_assessment(
            AuthorityGateCode.PARTIAL_REPAIR,
            GateDecision.INDETERMINATE,
            ("PARTIAL_REPAIR_UNRESOLVED_IMPACT",),
        )
        if partial
        else _gate_assessment(AuthorityGateCode.PARTIAL_REPAIR, GateDecision.PASS)
    )
    gates_by_code = {
        AuthorityGateCode.P_ASSIGN: assign_gate,
        AuthorityGateCode.P_WINDOW_01: window_gate,
        AuthorityGateCode.P_COST: cost_gate,
        AuthorityGateCode.P_OVERTIME: overtime_gate,
        AuthorityGateCode.P_VEHICLE: vehicle_gate,
        AuthorityGateCode.P_TAG: tag_gate,
        AuthorityGateCode.P_HORIZON: horizon_gate,
        AuthorityGateCode.P_SEARCH: search_gate,
        AuthorityGateCode.PARTIAL_REPAIR: partial_gate,
    }
    gates = tuple(gates_by_code[item] for item in AuthorityGateCode)

    soft_exception = bool(
        (cost is not None and cost.applicability is CostApplicability.SOFT_EXCEPTION)
        or tag.applicability is ApprovalMarkerApplicability.APPLICABLE
    )
    missing_soft_approval = any(
        item.gate in (AuthorityGateCode.P_COST, AuthorityGateCode.P_TAG)
        for item in missing_owner
    ) or bool(
        cost is not None
        and cost.approval_subject_state
        is PCostApprovalSubjectState.UNAVAILABLE_UNSUPPORTED_POLICY
    )
    if hard.applicability is HardBoundaryApplicability.APPLICABLE:
        policy_relation = PolicyRelation.HARD_BLOCK
        override_status = OverrideStatus.NOT_REQUIRED
    elif soft_exception:
        policy_relation = PolicyRelation.SOFT_EXCEPTION
        override_status = OverrideStatus.PENDING if missing_soft_approval else OverrideStatus.APPROVED
    else:
        policy_relation = PolicyRelation.WITHIN_ENVELOPE
        override_status = OverrideStatus.NOT_REQUIRED

    technical = _technical_feasibility(candidate)
    if hard.applicability is HardBoundaryApplicability.APPLICABLE:
        outcome = AuthorityOutcome.BLOCK
        disposition = Disposition.FORBIDDEN
        reasons = (hard.reason_code,)
    elif ordinary_failures:
        outcome = AuthorityOutcome.BLOCK
        disposition = Disposition.ABSTAIN
        reasons = tuple(item.reason_code.value for item in candidate.rejection_trace)
    elif window_failures:
        outcome = AuthorityOutcome.ASK
        disposition = Disposition.ABSTAIN
        reasons = ("OWNER_DECISION_REQUIRED_FOR_CUSTOMER_PROMISE",)
    elif unknown_constraints:
        outcome = AuthorityOutcome.ABSTAIN
        disposition = Disposition.ABSTAIN
        reasons = tuple(item.reason_code.value for item in unknown_constraints)
    elif hard.applicability is HardBoundaryApplicability.UNKNOWN:
        outcome = AuthorityOutcome.ABSTAIN
        disposition = Disposition.ABSTAIN
        reasons = (hard.reason_code,)
    elif not cut.currentness_vector.all_current:
        outcome = AuthorityOutcome.ABSTAIN
        disposition = Disposition.ABSTAIN
        reasons = ("DECISION_TIME_CURRENTNESS_FAILED",)
    elif cost is not None and cost.applicability is CostApplicability.UNKNOWN:
        outcome = AuthorityOutcome.ABSTAIN
        disposition = Disposition.ABSTAIN
        reasons = ("INCOMPLETE_INTERNAL_LABOR_COST_CONSEQUENCE",)
    elif overtime.applicability is OvertimeApplicability.UNKNOWN:
        outcome = AuthorityOutcome.ABSTAIN
        disposition = Disposition.ABSTAIN
        reasons = ("OVERTIME_EVIDENCE_UNKNOWN",)
    elif tag.applicability is ApprovalMarkerApplicability.UNKNOWN:
        outcome = AuthorityOutcome.ABSTAIN
        disposition = Disposition.ABSTAIN
        reasons = ("AUTHORITY_MARKER_EVIDENCE_UNKNOWN",)
    elif partial:
        outcome = AuthorityOutcome.ABSTAIN
        disposition = Disposition.ABSTAIN
        reasons = ("PARTIAL_REPAIR_UNRESOLVED_IMPACT",)
    elif cut.policy_status is not PolicySupportStatus.ISSUED_SUPPORTED:
        outcome = AuthorityOutcome.ABSTAIN
        disposition = Disposition.ABSTAIN
        reasons = (cut.policy_reason_code,)
    elif nonaffirmative_consent:
        outcome = AuthorityOutcome.ABSTAIN
        disposition = Disposition.ABSTAIN
        reasons = ("CURRENT_FREELY_GIVEN_WORKER_CONSENT_REQUIRED",)
    elif missing_owner:
        outcome = AuthorityOutcome.ASK
        disposition = Disposition.ABSTAIN
        reasons = tuple(
            dict.fromkeys(
                {
                    AuthorityGateCode.P_COST: "P_COST_OWNER_APPROVAL_REQUIRED",
                    AuthorityGateCode.P_TAG: "P_TAG_OWNER_APPROVAL_REQUIRED",
                    AuthorityGateCode.P_VEHICLE: "P_VEHICLE_OWNER_APPROVAL_REQUIRED",
                }[item.gate]
                for item in missing_owner
            )
        )
    elif overtime.applicability is OvertimeApplicability.CREATES_OVERTIME:
        outcome = AuthorityOutcome.ASK
        disposition = Disposition.ABSTAIN
        reasons = ("P_OVERTIME_AUTHORITY_REQUIRED",)
    elif later_day:
        outcome = AuthorityOutcome.ASK
        disposition = Disposition.ABSTAIN
        reasons = ("LATER_OPERATIONAL_DAY_AUTHORITY_REQUIRED",)
    else:
        outcome = AuthorityOutcome.ACT
        disposition = Disposition.EXECUTE_WITH_RECORDED_RISK if soft_exception else Disposition.EXECUTE
        reasons = ("OWNER_APPROVED_SOFT_EXCEPTION",) if soft_exception else ()
    execution = ExecutionAuthorization.ALLOWED if outcome is AuthorityOutcome.ACT else ExecutionAuthorization.BLOCKED
    return CandidateAuthorityAssessment(
        candidate_position=position,
        candidate_id=candidate.candidate_id,
        candidate_fingerprint=candidate.candidate_fingerprint,
        authority_evidence_cut_id=cut.cut_id,
        authority_evidence_cut_fingerprint=cut.cut_fingerprint,
        technical_feasibility=technical,
        authority_outcome=outcome,
        policy_relation=policy_relation,
        override_status=override_status,
        execution_authorization=execution,
        disposition=disposition,
        gate_assessments=gates,
        authority_requirements=requirements,
        reason_codes=reasons,
    )


def evaluate_operational_authority(
    evidence_cut: M3AuthorityEvidenceCut,
) -> M3OperationalAuthorityResult:
    """Evaluate every M3-C candidate without side effects or selection."""

    _bind(type(evidence_cut) is M3AuthorityEvidenceCut, "AUTHORITY_CUT_REQUIRED", "evidence_cut")
    try:
        _bind(replace(evidence_cut) == evidence_cut, "INVALID_AUTHORITY_CUT", "evidence_cut")
    except ValueError as error:
        raise OperationalAuthorityBindingError("INVALID_AUTHORITY_CUT", "evidence_cut") from error
    m3 = evidence_cut.m3_result
    if m3.outcome is SearchOutcome.NO_REPAIR_REQUIRED:
        status = M3AuthorityEvaluationStatus.NO_ACTION_REQUIRED
        assessments: tuple[CandidateAuthorityAssessment, ...] = ()
        projection = SearchAuthorityProjection.NOT_APPLICABLE
        top_reasons: tuple[str, ...] = ()
    else:
        status = M3AuthorityEvaluationStatus.CANDIDATES_ASSESSED
        assessments = tuple(
            _candidate_assessment(evidence_cut, position, candidate)
            for position, candidate in enumerate(m3.candidates)
        )
        if m3.search_envelope_exhausted and not m3.feasible_candidate_ids:
            projection = SearchAuthorityProjection.ABSTAIN
            top_reasons = ("ENVELOPE_EXHAUSTED",)
        elif m3.feasible_candidate_ids:
            projection = SearchAuthorityProjection.NOT_APPLICABLE
            top_reasons = ()
        elif m3.outcome in (
            SearchOutcome.INSUFFICIENT_CRITICAL_EVIDENCE,
            SearchOutcome.CREW_REPAIR_UNSUPPORTED,
        ):
            projection = SearchAuthorityProjection.ABSTAIN
            top_reasons = (m3.outcome.value,)
        else:
            projection = SearchAuthorityProjection.NOT_APPLICABLE
            top_reasons = ()
    return M3OperationalAuthorityResult(
        authority_evidence_cut_id=evidence_cut.cut_id,
        authority_evidence_cut_fingerprint=evidence_cut.cut_fingerprint,
        source_evaluation_input_id=evidence_cut.evaluation_input.evaluation_input_id,
        source_evaluation_input_fingerprint=evidence_cut.evaluation_input.evaluation_input_fingerprint,
        source_m3_result_id=m3.result_id,
        source_m3_result_fingerprint=m3.result_fingerprint,
        source_m3d_result_id=evidence_cut.m3d_result.result_id,
        source_m3d_result_fingerprint=evidence_cut.m3d_result.result_fingerprint,
        evaluation_status=status,
        search_outcome=m3.outcome,
        search_envelope_exhausted=m3.search_envelope_exhausted,
        search_authority_projection=projection,
        top_level_reason_codes=top_reasons,
        candidate_assessments=assessments,
    )


__all__ = [
    "OPERATIONAL_AUTHORITY_RULE_VERSION",
    "P_COST_THRESHOLD_EUR",
    "ApprovalMarkerApplicability",
    "AuthorityGateAssessment",
    "AuthorityGateCode",
    "AuthorityMarkerEvidence",
    "AuthorityMarkerKind",
    "AuthorityMarkerSubject",
    "AuthorityMarkerSubjectCoverage",
    "AuthorityMarkerSupportCut",
    "AuthorityOutcome",
    "AuthorityRequirement",
    "AuthoritySubjectClass",
    "CandidateApprovalTagAssessment",
    "CandidateAuthorityAssessment",
    "CandidateAuthorityRequirementSet",
    "CandidateCostAssessment",
    "CandidateHardBoundaryAssessment",
    "CandidateMarkerSubjectUniverse",
    "CandidateOvertimeAssessment",
    "CandidateWorkingTimeEvidence",
    "CostApplicability",
    "CurrentnessEvidenceState",
    "CurrentnessKind",
    "CurrentnessObservation",
    "EvidenceBinding",
    "GateDecision",
    "HardBoundaryApplicability",
    "HardBoundaryKind",
    "M3AuthorityEvaluationStatus",
    "M3AuthorityEvidenceCut",
    "M3DecisionTimeCurrentnessVector",
    "M3OperationalAuthorityResult",
    "MarkerCoverageStatus",
    "MarkerSubjectKind",
    "OperationalAuthorityBindingError",
    "OperationalAuthorityError",
    "OperationalAuthorityValidationError",
    "OperationalSlice",
    "OvertimeApplicability",
    "OwnerApprovalSelection",
    "PCostApprovalSubject",
    "PCostApprovalSubjectState",
    "PlanDayRootHeadEvidence",
    "PolicySupportStatus",
    "SearchAuthorityProjection",
    "SelectionStatus",
    "WorkerConsentHeadSelection",
    "WorkerPeriodOvertimeComparison",
    "WorkingTimeEvidenceCut",
    "WorkingTimeCommonManifestEntry",
    "WorkingTimeRuleEvidence",
    "WorkingTimeSourceClassState",
    "WorkingTimeSourceKind",
    "WorkingTimeSourceManifest",
    "WorkingTimeSourceSelection",
    "WorkingTimeWorkItem",
    "WorkingTimeWorkKind",
    "assess_candidate_approval_tags",
    "assess_candidate_cost",
    "assess_candidate_overtime",
    "calculate_rule_overtime",
    "derive_affected_operational_slices",
    "derive_authority_marker_subjects",
    "derive_authority_requirements",
    "derive_pcost_approval_subjects",
    "derive_required_currentness_observations",
    "evaluate_operational_authority",
    "m3_authority_evidence_cut_semantic_json",
    "m3_operational_authority_result_semantic_json",
    "plan_day_root_head_evidence",
    "plan_day_root_head_evidence_semantic_json",
    "pcost_approval_subject_semantic_json",
    "serialize_operational_authority_result",
    "working_time_source_selection_semantic_json",
]
