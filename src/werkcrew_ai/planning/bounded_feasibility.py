"""Pure M3-C bounded repair search over one immutable M3-B/C0 evidence cut.

The module deliberately has no persistence or orchestration dependency.  The
small service at the bottom reloads the two durable inputs and then delegates
to the same pure function; candidate creation has no write path.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo

from werkcrew_ai.catalog.models import VehicleClass
from werkcrew_ai.field.models import AssignmentKind
from werkcrew_ai.field.serialization import canonical_json, sha256_text
from werkcrew_ai.planning.crew_planner import intervals_overlap
from werkcrew_ai.planning.evaluation_input import (
    CANDIDATE_ABSENCE,
    CurrentCommitmentEvidence,
    DependencyEvidence,
    ImpactCommitmentReference,
    M3EvaluationInput,
    deserialize_evaluation_input,
    serialize_evaluation_input,
)
from werkcrew_ai.planning.feasibility_support import (
    AvailabilityEvidence,
    AvailabilityKnowledge,
    ConstraintKnowledge,
    FeasibilitySupportSnapshot,
    ReadinessState,
    RouteEvidence,
    RouteKnowledge,
    ScheduledPlacementEvidence,
    TaskConstraintSource,
    VehicleTechnicalEvidence,
    WorkerTechnicalEvidence,
    deserialize_feasibility_support,
    serialize_feasibility_support,
)


BUSINESS_TIMEZONE = "Europe/Berlin"
PLANNING_HORIZON_DAYS = 3
MAX_EVALUATED_CANDIDATES = 12
MAX_MODIFIED_COMMITMENTS = 3
MAX_SEED_PAIR_PROBES = MAX_EVALUATED_CANDIDATES + 1
MAX_ANCHORS_PER_SEED_PAIR = MAX_EVALUATED_CANDIDATES + 1
CANDIDATE_SCHEMA_VERSION = "m3-bounded-repair-candidate-v1"
SEARCH_RESULT_SCHEMA_VERSION = "m3-bounded-feasibility-result-v1"
RULE_VERSION = "m3-bounded-feasibility-v1"


class M3BoundedFeasibilityError(ValueError):
    def __init__(self, code: str, field_name: str) -> None:
        self.code, self.field_name = code, field_name
        super().__init__(f"{code}: {field_name}")


class M3BoundedFeasibilityValidationError(M3BoundedFeasibilityError):
    pass


class M3BoundedFeasibilityBindingError(M3BoundedFeasibilityError):
    pass


class CandidateVerdict(StrEnum):
    FEASIBLE = "FEASIBLE"
    REJECTED = "REJECTED"


class ConstraintStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class SearchOutcome(StrEnum):
    FEASIBLE_CANDIDATES_FOUND = "FEASIBLE_CANDIDATES_FOUND"
    ALL_EVALUATED_CANDIDATES_REJECTED = "ALL_EVALUATED_CANDIDATES_REJECTED"
    SEARCH_ENVELOPE_EXHAUSTED = "SEARCH_ENVELOPE_EXHAUSTED"
    CREW_REPAIR_UNSUPPORTED = "CREW_REPAIR_UNSUPPORTED"
    INSUFFICIENT_CRITICAL_EVIDENCE = "INSUFFICIENT_CRITICAL_EVIDENCE"
    NO_REPAIR_REQUIRED = "NO_REPAIR_REQUIRED"


class ReasonCode(StrEnum):
    EXACT_SKILL_MATCH = "EXACT_SKILL_MATCH"
    EXACT_SKILL_MISMATCH = "EXACT_SKILL_MISMATCH"
    SKILL_EVIDENCE_UNKNOWN = "SKILL_EVIDENCE_UNKNOWN"
    WORKER_AVAILABLE = "WORKER_AVAILABLE"
    WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"
    AVAILABILITY_UNKNOWN = "AVAILABILITY_UNKNOWN"
    NO_OVERLAP = "NO_OVERLAP"
    OVERLAP_CONFLICT = "OVERLAP_CONFLICT"
    DEPENDENCY_SATISFIED = "DEPENDENCY_SATISFIED"
    DEPENDENCY_VIOLATION = "DEPENDENCY_VIOLATION"
    READINESS_READY = "READINESS_READY"
    READINESS_NOT_READY = "READINESS_NOT_READY"
    READINESS_UNKNOWN = "READINESS_UNKNOWN"
    VEHICLE_AVAILABLE = "VEHICLE_AVAILABLE"
    VEHICLE_NOT_REQUIRED = "VEHICLE_NOT_REQUIRED"
    VEHICLE_UNAVAILABLE = "VEHICLE_UNAVAILABLE"
    VEHICLE_EVIDENCE_UNKNOWN = "VEHICLE_EVIDENCE_UNKNOWN"
    ROUTE_BUFFER_SATISFIED = "ROUTE_BUFFER_SATISFIED"
    ROUTE_BUFFER_VIOLATION = "ROUTE_BUFFER_VIOLATION"
    ROUTE_EVIDENCE_UNKNOWN = "ROUTE_EVIDENCE_UNKNOWN"
    TECHNICALLY_FITS_WINDOW = "TECHNICALLY_FITS_WINDOW"
    CUSTOMER_WINDOW_ABSENT = "CUSTOMER_WINDOW_ABSENT"
    CUSTOMER_WINDOW_VIOLATION = "CUSTOMER_WINDOW_VIOLATION"
    CUSTOMER_WINDOW_EVIDENCE_UNKNOWN = "CUSTOMER_WINDOW_EVIDENCE_UNKNOWN"
    DEADLINE_MET = "DEADLINE_MET"
    DEADLINE_ABSENT = "DEADLINE_ABSENT"
    DEADLINE_VIOLATION = "DEADLINE_VIOLATION"
    DEADLINE_EVIDENCE_UNKNOWN = "DEADLINE_EVIDENCE_UNKNOWN"
    CREW_REPAIR_UNSUPPORTED = "CREW_REPAIR_UNSUPPORTED"
    SINGLE_KIND_EVIDENCE_UNKNOWN = "SINGLE_KIND_EVIDENCE_UNKNOWN"
    SEARCH_ENVELOPE_LIMIT = "SEARCH_ENVELOPE_LIMIT"
    PRIVATE_VEHICLE_AUTHORITY_REQUIRED = "PRIVATE_VEHICLE_AUTHORITY_REQUIRED"


def _require(condition: bool, field_name: str) -> None:
    if not condition:
        raise M3BoundedFeasibilityValidationError("INVALID_VALUE", field_name)


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


def _canonical_records(values, expected: type, key, field_name: str):
    _require(type(values) in (tuple, list), field_name)
    normalized = []
    for value in values:
        _require(type(value) is expected and replace(value) == value, field_name)
        normalized.append(value)
    keys = [key(value) for value in normalized]
    _require(len(keys) == len(set(keys)), field_name)
    return tuple(sorted(normalized, key=key))


def _primitive(value):
    if isinstance(value, StrEnum):
        return value.value
    if type(value) is datetime:
        return value.isoformat()
    if type(value) is date:
        return value.isoformat()
    if type(value) is tuple:
        return [_primitive(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {item.name: _primitive(getattr(value, item.name)) for item in fields(value)}
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposedWorkerPlacement:
    commitment_id: str
    job_id: str
    task_id: str
    worker_id: str
    vehicle_id: str | None
    business_date: date
    proposed_start: datetime
    proposed_end: datetime

    def __post_init__(self) -> None:
        for name in ("commitment_id", "job_id", "task_id", "worker_id"):
            _identity(getattr(self, name), name)
        if self.vehicle_id is not None:
            _identity(self.vehicle_id, "vehicle_id")
        _require(type(self.business_date) is date, "business_date")
        start = _instant(self.proposed_start, "proposed_start")
        end = _instant(self.proposed_end, "proposed_end")
        _require(end > start, "proposed_interval")
        object.__setattr__(self, "proposed_start", start)
        object.__setattr__(self, "proposed_end", end)


@dataclass(frozen=True, slots=True, kw_only=True)
class TechnicalConstraintResult:
    status: ConstraintStatus
    reason_code: ReasonCode
    commitment_id: str
    subject_id: str
    related_commitment_ids: tuple[str, ...] = ()
    evidence_reference_ids: tuple[str, ...] = ()
    required_value: str | None = None
    actual_value: str | None = None

    def __post_init__(self) -> None:
        _require(type(self.status) is ConstraintStatus, "constraint_status")
        _require(type(self.reason_code) is ReasonCode, "reason_code")
        _identity(self.commitment_id, "commitment_id")
        _identity(self.subject_id, "constraint_subject_id")
        for name in ("related_commitment_ids", "evidence_reference_ids"):
            values = getattr(self, name)
            _require(type(values) in (tuple, list), name)
            for value in values:
                _identity(value, name)
            _require(len(values) == len(set(values)), name)
            object.__setattr__(self, name, tuple(sorted(values)))
        for name in ("required_value", "actual_value"):
            value = getattr(self, name)
            _require(value is None or type(value) is str, name)


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateImpactEvidence:
    direct_current_impact: tuple[ImpactCommitmentReference, ...]
    dependency_impact: tuple[ImpactCommitmentReference, ...]
    candidate_induced_impact: tuple[ImpactCommitmentReference, ...]
    restored_impact: tuple[ImpactCommitmentReference, ...]
    residual_unresolved_impact: tuple[ImpactCommitmentReference, ...]

    def __post_init__(self) -> None:
        for name in (
            "direct_current_impact",
            "dependency_impact",
            "candidate_induced_impact",
            "restored_impact",
            "residual_unresolved_impact",
        ):
            object.__setattr__(
                self,
                name,
                _canonical_records(
                    getattr(self, name),
                    ImpactCommitmentReference,
                    lambda item: (item.job_id, item.task_id, item.commitment_id),
                    name,
                ),
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class TechnicalOrderingEvidence:
    restored_commitment_count: int
    protected_known_customer_window_count: int
    modified_commitment_count: int
    travel_and_buffer_seconds: int

    def __post_init__(self) -> None:
        for name in (
            "restored_commitment_count",
            "protected_known_customer_window_count",
            "modified_commitment_count",
            "travel_and_buffer_seconds",
        ):
            value = getattr(self, name)
            _require(type(value) is int and value >= 0, name)
        _require(
            self.modified_commitment_count <= MAX_MODIFIED_COMMITMENTS,
            "modified_commitment_count",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class M3RepairCandidate:
    source_evaluation_input_id: str
    source_evaluation_input_fingerprint: str
    source_support_snapshot_id: str
    source_support_snapshot_fingerprint: str
    company_plan_id: str
    base_plan_revision: int
    base_plan_revision_id: str
    base_plan_revision_fingerprint: str
    modified_commitment_ids: tuple[str, ...]
    canonical_job_ids: tuple[str, ...]
    canonical_task_ids: tuple[str, ...]
    proposed_worker_placements: tuple[ProposedWorkerPlacement, ...]
    affected_dependencies: tuple[DependencyEvidence, ...]
    impact: CandidateImpactEvidence
    technical_constraint_results: tuple[TechnicalConstraintResult, ...]
    verdict: CandidateVerdict
    rejection_trace: tuple[TechnicalConstraintResult, ...]
    authority_impacts: tuple[ReasonCode, ...]
    technical_ordering: TechnicalOrderingEvidence
    schema_version: str = field(default=CANDIDATE_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=RULE_VERSION, init=False)
    candidate_fingerprint: str = field(init=False)
    candidate_id: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "source_evaluation_input_id",
            "source_support_snapshot_id",
            "company_plan_id",
            "base_plan_revision_id",
        ):
            _identity(getattr(self, name), name)
        for name in (
            "source_evaluation_input_fingerprint",
            "source_support_snapshot_fingerprint",
            "base_plan_revision_fingerprint",
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
            self.base_plan_revision_id
            == "m3-plan-revision-" + self.base_plan_revision_fingerprint,
            "base_plan_revision_id",
        )
        _require(type(self.base_plan_revision) is int and self.base_plan_revision >= 0, "base_plan_revision")
        placements = _canonical_records(
            self.proposed_worker_placements,
            ProposedWorkerPlacement,
            lambda item: (item.job_id, item.task_id, item.commitment_id),
            "proposed_worker_placements",
        )
        modified_ids = tuple(item.commitment_id for item in placements)
        _require(tuple(self.modified_commitment_ids) == modified_ids, "modified_commitment_ids")
        _require(0 < len(modified_ids) <= MAX_MODIFIED_COMMITMENTS, "modified_commitment_ids")
        canonical_job_ids = tuple(sorted({item.job_id for item in placements}))
        canonical_task_ids = tuple(sorted({item.task_id for item in placements}))
        _require(tuple(self.canonical_job_ids) == canonical_job_ids, "canonical_job_ids")
        _require(tuple(self.canonical_task_ids) == canonical_task_ids, "canonical_task_ids")
        dependencies = _canonical_records(
            self.affected_dependencies,
            DependencyEvidence,
            lambda item: (item.predecessor_task_id, item.successor_task_id),
            "affected_dependencies",
        )
        constraints = tuple(self.technical_constraint_results)
        _require(all(type(item) is TechnicalConstraintResult for item in constraints), "technical_constraint_results")
        rejection = tuple(item for item in constraints if item.status is not ConstraintStatus.PASS)
        _require(tuple(self.rejection_trace) == rejection, "rejection_trace")
        _require(
            (self.verdict is CandidateVerdict.FEASIBLE and not rejection)
            or (self.verdict is CandidateVerdict.REJECTED and bool(rejection)),
            "verdict",
        )
        _require(type(self.impact) is CandidateImpactEvidence and replace(self.impact) == self.impact, "impact")
        _require(
            type(self.technical_ordering) is TechnicalOrderingEvidence
            and replace(self.technical_ordering) == self.technical_ordering,
            "technical_ordering",
        )
        authority = tuple(sorted(set(self.authority_impacts), key=lambda item: item.value))
        _require(all(type(item) is ReasonCode for item in authority), "authority_impacts")
        object.__setattr__(self, "modified_commitment_ids", modified_ids)
        object.__setattr__(self, "canonical_job_ids", canonical_job_ids)
        object.__setattr__(self, "canonical_task_ids", canonical_task_ids)
        object.__setattr__(self, "proposed_worker_placements", placements)
        object.__setattr__(self, "affected_dependencies", dependencies)
        object.__setattr__(self, "technical_constraint_results", constraints)
        object.__setattr__(self, "rejection_trace", rejection)
        object.__setattr__(self, "authority_impacts", authority)
        digest = sha256_text(_candidate_semantic_json(self))
        object.__setattr__(self, "candidate_fingerprint", digest)
        object.__setattr__(self, "candidate_id", "m3-candidate-" + digest)


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchTraceEntry:
    reason_code: ReasonCode
    commitment_ids: tuple[str, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        _require(type(self.reason_code) is ReasonCode, "search_reason_code")
        for value in self.commitment_ids:
            _identity(value, "search_commitment_ids")
        object.__setattr__(self, "commitment_ids", tuple(sorted(set(self.commitment_ids))))
        _require(type(self.detail) is str, "search_detail")


@dataclass(frozen=True, slots=True, kw_only=True)
class M3BoundedFeasibilityResult:
    source_evaluation_input_id: str
    source_evaluation_input_fingerprint: str
    source_support_snapshot_id: str
    source_support_snapshot_fingerprint: str
    company_plan_id: str
    base_plan_revision: int
    outcome: SearchOutcome
    candidates: tuple[M3RepairCandidate, ...]
    feasible_candidate_ids: tuple[str, ...]
    rejected_candidate_ids: tuple[str, ...]
    evaluated_candidate_count: int
    search_envelope_exhausted: bool
    global_solution_status: str
    unsupported_commitment_ids: tuple[str, ...]
    insufficient_evidence_commitment_ids: tuple[str, ...]
    search_trace: tuple[SearchTraceEntry, ...]
    schema_version: str = field(default=SEARCH_RESULT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=RULE_VERSION, init=False)
    result_fingerprint: str = field(init=False)
    result_id: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "source_evaluation_input_id",
            "source_support_snapshot_id",
            "company_plan_id",
        ):
            _identity(getattr(self, name), name)
        for name in (
            "source_evaluation_input_fingerprint",
            "source_support_snapshot_fingerprint",
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
            type(self.base_plan_revision) is int and self.base_plan_revision >= 0,
            "base_plan_revision",
        )
        _require(type(self.outcome) is SearchOutcome, "search_outcome")
        _require(type(self.search_envelope_exhausted) is bool, "search_envelope_exhausted")
        _require(self.global_solution_status == "NOT_EVALUATED_GLOBALLY", "global_solution_status")
        candidates = tuple(self.candidates)
        _require(all(type(item) is M3RepairCandidate for item in candidates), "candidates")
        _require(len(candidates) <= MAX_EVALUATED_CANDIDATES, "candidates")
        _require(self.evaluated_candidate_count == len(candidates), "evaluated_candidate_count")
        _require(
            all(
                (
                    item.source_evaluation_input_id,
                    item.source_evaluation_input_fingerprint,
                    item.source_support_snapshot_id,
                    item.source_support_snapshot_fingerprint,
                    item.company_plan_id,
                    item.base_plan_revision,
                )
                == (
                    self.source_evaluation_input_id,
                    self.source_evaluation_input_fingerprint,
                    self.source_support_snapshot_id,
                    self.source_support_snapshot_fingerprint,
                    self.company_plan_id,
                    self.base_plan_revision,
                )
                for item in candidates
            ),
            "candidate_source_binding",
        )
        feasible_ids = tuple(self.feasible_candidate_ids)
        rejected_ids = tuple(self.rejected_candidate_ids)
        _require(
            feasible_ids
            == tuple(item.candidate_id for item in candidates if item.verdict is CandidateVerdict.FEASIBLE),
            "feasible_candidate_ids",
        )
        _require(
            rejected_ids
            == tuple(item.candidate_id for item in candidates if item.verdict is CandidateVerdict.REJECTED),
            "rejected_candidate_ids",
        )
        for name in ("unsupported_commitment_ids", "insufficient_evidence_commitment_ids"):
            values = tuple(getattr(self, name))
            _require(len(values) == len(set(values)), name)
            object.__setattr__(self, name, tuple(sorted(values)))
        search_trace = tuple(self.search_trace)
        _require(all(type(item) is SearchTraceEntry for item in search_trace), "search_trace")
        trace_proves_exhaustion = any(
            item.reason_code is ReasonCode.SEARCH_ENVELOPE_LIMIT
            for item in search_trace
        )
        _require(
            self.search_envelope_exhausted == trace_proves_exhaustion,
            "search_envelope_exhausted",
        )
        feasible_count = sum(
            item.verdict is CandidateVerdict.FEASIBLE for item in candidates
        )
        _require(
            (self.outcome is SearchOutcome.FEASIBLE_CANDIDATES_FOUND)
            == (feasible_count > 0),
            "search_outcome",
        )
        if not feasible_count and self.search_envelope_exhausted:
            _require(
                self.outcome is SearchOutcome.SEARCH_ENVELOPE_EXHAUSTED,
                "search_outcome",
            )
        if self.outcome is SearchOutcome.SEARCH_ENVELOPE_EXHAUSTED:
            _require(self.search_envelope_exhausted and not feasible_count, "search_outcome")
        if self.outcome is SearchOutcome.ALL_EVALUATED_CANDIDATES_REJECTED:
            _require(
                not self.search_envelope_exhausted
                and not trace_proves_exhaustion
                and not feasible_count
                and all(item.verdict is CandidateVerdict.REJECTED for item in candidates),
                "search_outcome",
            )
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "feasible_candidate_ids", feasible_ids)
        object.__setattr__(self, "rejected_candidate_ids", rejected_ids)
        object.__setattr__(self, "search_trace", search_trace)
        digest = sha256_text(_result_semantic_json(self))
        object.__setattr__(self, "result_fingerprint", digest)
        object.__setattr__(self, "result_id", "m3-feasibility-result-" + digest)


def _candidate_semantic_json(candidate: M3RepairCandidate) -> str:
    return canonical_json(
        {
            item.name: _primitive(getattr(candidate, item.name))
            for item in fields(candidate)
            if item.name not in ("candidate_fingerprint", "candidate_id")
        }
    )


def serialize_repair_candidate(candidate: M3RepairCandidate) -> str:
    _require(type(candidate) is M3RepairCandidate and replace(candidate) == candidate, "candidate")
    return canonical_json({item.name: _primitive(getattr(candidate, item.name)) for item in fields(candidate)})


def _result_semantic_json(result: M3BoundedFeasibilityResult) -> str:
    return canonical_json(
        {
            item.name: _primitive(getattr(result, item.name))
            for item in fields(result)
            if item.name not in ("result_fingerprint", "result_id")
        }
    )


def serialize_bounded_feasibility_result(result: M3BoundedFeasibilityResult) -> str:
    _require(type(result) is M3BoundedFeasibilityResult and replace(result) == result, "result")
    return canonical_json({item.name: _primitive(getattr(result, item.name)) for item in fields(result)})


@dataclass(frozen=True, slots=True)
class _Draft:
    placements: tuple[ProposedWorkerPlacement, ...]

    @property
    def signature(self) -> tuple:
        return tuple(
            (
                item.commitment_id,
                item.worker_id,
                item.vehicle_id,
                item.proposed_start,
                item.proposed_end,
            )
            for item in sorted(self.placements, key=lambda value: (value.job_id, value.task_id, value.commitment_id))
        )


@dataclass(slots=True)
class _Context:
    evaluation: M3EvaluationInput
    support: FeasibilitySupportSnapshot
    zone: ZoneInfo
    horizon_start: datetime
    horizon_end: datetime
    commitments: dict[str, CurrentCommitmentEvidence]
    active_placements: dict[str, ScheduledPlacementEvidence]
    constraints: dict[str, TaskConstraintSource]
    workers: dict[str, WorkerTechnicalEvidence]
    worker_availability: dict[str, AvailabilityEvidence]
    readiness: dict[str, object]
    vehicles: dict[str, VehicleTechnicalEvidence]
    vehicle_availability: dict[str, AvailabilityEvidence]
    routes: dict[tuple[str, str, str, str], RouteEvidence]
    task_to_commitment: dict[str, str]
    direct_ids: tuple[str, ...]


def _fail_binding(code: str, field_name: str) -> None:
    raise M3BoundedFeasibilityBindingError(code, field_name)


def _validated_context(
    evaluation: M3EvaluationInput,
    support: FeasibilitySupportSnapshot,
) -> _Context:
    if type(evaluation) is not M3EvaluationInput:
        _fail_binding("EVALUATION_INPUT_REQUIRED", "evaluation_input")
    if type(support) is not FeasibilitySupportSnapshot:
        _fail_binding("FEASIBILITY_SUPPORT_REQUIRED", "support_snapshot")
    try:
        restored_evaluation = deserialize_evaluation_input(serialize_evaluation_input(evaluation))
        restored_support = deserialize_feasibility_support(serialize_feasibility_support(support))
    except ValueError as error:
        raise M3BoundedFeasibilityBindingError("SOURCE_INTEGRITY_FAILURE", "source") from error
    if restored_evaluation != evaluation:
        _fail_binding("EVALUATION_INPUT_INTEGRITY_FAILURE", "evaluation_input")
    if restored_support != support:
        _fail_binding("SUPPORT_INTEGRITY_FAILURE", "support_snapshot")
    exact_evaluation = (
        evaluation.evaluation_input_id,
        evaluation.evaluation_input_fingerprint,
        evaluation.company_plan_id,
        evaluation.base_plan_revision,
        evaluation.base_plan_revision_id,
        evaluation.base_plan_revision_fingerprint,
    )
    exact_support = (
        support.evaluation_input_id,
        support.evaluation_input_fingerprint,
        support.company_plan_id,
        support.base_plan_revision,
        support.base_plan_revision_id,
        support.base_plan_revision_fingerprint,
    )
    if exact_evaluation != exact_support:
        _fail_binding("SUPPORT_EVALUATION_INPUT_MISMATCH", "support_snapshot")
    schedule_revision = (
        support.schedule.company_plan_id,
        support.schedule.base_plan_revision,
        support.schedule.base_plan_revision_id,
        support.schedule.base_plan_revision_fingerprint,
    )
    if schedule_revision != exact_support[2:]:
        _fail_binding("SUPPORT_SCHEDULE_REVISION_MISMATCH", "schedule")
    if support.schedule.business_timezone != BUSINESS_TIMEZONE:
        _fail_binding("UNSUPPORTED_BUSINESS_TIMEZONE", "business_timezone")
    if evaluation.candidate_induced_impact.status != CANDIDATE_ABSENCE:
        _fail_binding("EVALUATION_INPUT_ALREADY_HAS_CANDIDATE", "candidate_induced_impact")

    commitments = {item.commitment_id: item for item in evaluation.current_active_commitments}
    placement_by_id = {item.commitment_id: item for item in support.schedule.placements}
    for commitment_id, commitment in commitments.items():
        placement = placement_by_id.get(commitment_id)
        if placement is None or (
            placement.job_id,
            placement.task_id,
            placement.task_definition_version,
            placement.business_date,
            placement.worker_ids,
        ) != (
            commitment.job_id,
            commitment.task_id,
            commitment.task_definition_version,
            commitment.business_date,
            commitment.planned_worker_ids,
        ):
            _fail_binding("SUPPORT_ACTIVE_SCHEDULE_MISMATCH", "schedule")
    by_task_constraint = {(item.job_id, item.task_id): item for item in support.task_constraints}
    resolved_constraints: dict[str, TaskConstraintSource] = {}
    readiness_by_task = {(item.job_id, item.task_id): item for item in support.readiness}
    resolved_readiness = {}
    for commitment in evaluation.current_active_commitments:
        constraint = by_task_constraint.get((commitment.job_id, commitment.task_id))
        readiness = readiness_by_task.get((commitment.job_id, commitment.task_id))
        if constraint is None or readiness is None:
            _fail_binding("SUPPORT_TASK_COVERAGE_MISMATCH", "task_evidence")
        if (
            constraint.task_definition_version,
            constraint.source_handoff_id,
            constraint.source_revision_number,
        ) != (
            commitment.task_definition_version,
            commitment.source_handoff_id,
            commitment.source_revision,
        ) or readiness.task_definition_version != commitment.task_definition_version:
            _fail_binding("SUPPORT_TASK_BINDING_MISMATCH", "task_evidence")
        if (
            constraint.m8_configuration_fingerprint,
            constraint.m8_service_catalog_version,
            constraint.m8_planning_profile_version,
        ) != (
            support.m8_configuration_fingerprint,
            support.m8_service_catalog_version,
            support.m8_planning_profile_version,
        ):
            _fail_binding("SUPPORT_M8_BINDING_MISMATCH", "task_constraint")
        if int((placement_by_id[commitment.commitment_id].planned_end - placement_by_id[commitment.commitment_id].planned_start).total_seconds()) != constraint.duration_seconds:
            _fail_binding("SUPPORT_PLANNED_DURATION_MISMATCH", "schedule")
        resolved_constraints[commitment.commitment_id] = constraint
        resolved_readiness[commitment.commitment_id] = readiness
    if len(by_task_constraint) != len(commitments) or len(readiness_by_task) != len(commitments):
        _fail_binding("SUPPORT_TASK_COVERAGE_MISMATCH", "task_evidence")
    if any(
        item.skill_matrix_version != support.m8_skill_matrix_version
        for item in support.worker_technical_evidence
    ):
        _fail_binding("SUPPORT_M8_BINDING_MISMATCH", "worker_technical_evidence")
    endpoints = {
        (item.location_reference, item.location_fingerprint)
        for item in support.task_constraints
    }
    expected_routes = {
        (origin[0], origin[1], destination[0], destination[1])
        for origin in endpoints
        for destination in endpoints
        if origin != destination
    }
    actual_routes = {
        (
            item.origin_reference,
            item.origin_fingerprint,
            item.destination_reference,
            item.destination_fingerprint,
        )
        for item in support.routes
    }
    if actual_routes != expected_routes or any(
        item.transport_mode != "CAR" for item in support.routes
    ):
        _fail_binding("SUPPORT_ROUTE_COVERAGE_MISMATCH", "routes")

    zone = ZoneInfo(BUSINESS_TIMEZONE)
    horizon_start = datetime.combine(evaluation.business_date, time.min, zone).astimezone(timezone.utc)
    horizon_end = datetime.combine(
        evaluation.business_date + timedelta(days=PLANNING_HORIZON_DAYS),
        time.min,
        zone,
    ).astimezone(timezone.utc)
    direct_ids = tuple(item.commitment_id for item in evaluation.direct_current_impact.commitments)
    return _Context(
        evaluation=evaluation,
        support=support,
        zone=zone,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        commitments=commitments,
        active_placements={key: placement_by_id[key] for key in commitments},
        constraints=resolved_constraints,
        workers={item.worker_id: item for item in support.worker_technical_evidence},
        worker_availability={item.subject_id: item for item in support.worker_availability},
        readiness=resolved_readiness,
        vehicles={item.vehicle_id: item for item in support.vehicle_technical_evidence},
        vehicle_availability={item.subject_id: item for item in support.vehicle_availability},
        routes={
            (
                item.origin_fingerprint,
                item.destination_fingerprint,
                item.origin_reference,
                item.destination_reference,
            ): item
            for item in support.routes
        },
        task_to_commitment={item.task_id: item.commitment_id for item in evaluation.current_active_commitments},
        direct_ids=direct_ids,
    )


def _commitment_kind(context: _Context, commitment_id: str) -> AssignmentKind | None:
    commitment = context.commitments[commitment_id]
    historical = {
        item.assignment_id: item
        for item in context.evaluation.historical_source_scope.assignments
    }
    if len(commitment.planned_worker_ids) > 1:
        return AssignmentKind.CREW
    if not commitment.source_m2_assignment_ids:
        return None
    sources = [historical.get(item) for item in commitment.source_m2_assignment_ids]
    if any(item is None for item in sources):
        return None
    kinds = {item.assignment_kind for item in sources if item is not None}
    if AssignmentKind.CREW in kinds:
        return AssignmentKind.CREW
    return AssignmentKind.SINGLE if kinds == {AssignmentKind.SINGLE} else None


def _vehicle_options(context: _Context, commitment_id: str, worker_id: str) -> tuple[str | None, ...]:
    required = context.constraints[commitment_id].required_vehicle_class
    if required == VehicleClass.NONE.value:
        return (None,)
    matches = tuple(sorted(
        item.vehicle_id
        for item in context.support.vehicle_technical_evidence
        if required in item.capabilities
        and (item.assigned_worker_id is None or item.assigned_worker_id == worker_id)
    ))
    return matches if matches else (None,)


def _anchor_starts(context: _Context, commitment_id: str, worker_id: str) -> tuple[datetime, ...]:
    original = context.active_placements[commitment_id].planned_start
    duration = timedelta(seconds=context.constraints[commitment_id].duration_seconds)
    candidates = {original}
    availability = context.worker_availability[worker_id]
    if availability.knowledge is AvailabilityKnowledge.KNOWN:
        for window in availability.available_windows:
            candidates.add(window.start_at)
            if window.end_at - duration >= window.start_at:
                candidates.add(window.end_at - duration)
    constraint = context.constraints[commitment_id]
    if constraint.customer_window_state is ConstraintKnowledge.KNOWN:
        candidates.add(constraint.customer_window_start)
        candidates.add(constraint.customer_window_end - duration)
    for item in context.active_placements.values():
        if worker_id not in item.worker_ids or item.commitment_id == commitment_id:
            continue
        candidates.add(item.planned_end)
        route = _route_between(context, item.commitment_id, commitment_id)
        if route is not None and route.knowledge is RouteKnowledge.KNOWN:
            candidates.add(
                item.planned_end
                + timedelta(
                    seconds=route.travel_duration_seconds + route.buffer_seconds
                )
            )
    target_task_id = context.commitments[commitment_id].task_id
    for edge in context.evaluation.dependency_impact.edges:
        if edge.successor_task_id == target_task_id:
            predecessor = context.task_to_commitment.get(edge.predecessor_task_id)
            if predecessor is not None:
                candidates.add(context.active_placements[predecessor].planned_end)
        if edge.predecessor_task_id == target_task_id:
            successor = context.task_to_commitment.get(edge.successor_task_id)
            if successor is not None:
                candidates.add(
                    context.active_placements[successor].planned_start - duration
                )
    within = tuple(
        value
        for value in sorted(candidates)
        if context.horizon_start <= value < context.horizon_end
    )
    ordered = ((original,) if original in within else ()) + tuple(value for value in within if value != original)
    return ordered[:MAX_ANCHORS_PER_SEED_PAIR]


@dataclass(slots=True)
class _PrimaryDraftStream:
    """Lazy, deterministically bounded commitment/worker seed exploration."""

    context: _Context
    single_ids: tuple[str, ...]
    workers: tuple[str, ...] = field(init=False)
    pair_index: int = field(default=0, init=False)
    pair_probe_count: int = field(default=0, init=False)
    probe_budget_exhausted: bool = field(default=False, init=False)
    _starts: tuple[datetime, ...] = field(default=(), init=False)
    _vehicles: tuple[str | None, ...] = field(default=(), init=False)
    _anchor_index: int = field(default=0, init=False)
    _vehicle_index: int = field(default=0, init=False)
    _commitment_id: str | None = field(default=None, init=False)
    _worker_id: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.workers = tuple(
            worker_id
            for worker_id in sorted(self.context.workers)
            if worker_id != self.context.evaluation.unavailable_worker_id
        )

    @property
    def total_pair_count(self) -> int:
        return len(self.single_ids) * len(self.workers)

    @property
    def has_unexplored(self) -> bool:
        return (
            self._anchor_index < len(self._starts)
            or self.pair_index < self.total_pair_count
        )

    def _probe_next_pair(self) -> bool:
        if self.pair_index >= self.total_pair_count:
            return False
        if self.pair_probe_count >= MAX_SEED_PAIR_PROBES:
            self.probe_budget_exhausted = True
            return False
        commitment_index, worker_index = divmod(self.pair_index, len(self.workers))
        self.pair_index += 1
        self.pair_probe_count += 1
        self._commitment_id = self.single_ids[commitment_index]
        self._worker_id = self.workers[worker_index]
        self._starts = _anchor_starts(
            self.context,
            self._commitment_id,
            self._worker_id,
        )
        self._vehicles = _vehicle_options(
            self.context,
            self._commitment_id,
            self._worker_id,
        )
        self._anchor_index = 0
        self._vehicle_index = 0
        return True

    def next_draft(self) -> _Draft | None:
        while True:
            if self._anchor_index < len(self._starts):
                commitment_id = self._commitment_id
                worker_id = self._worker_id
                if commitment_id is None or worker_id is None:
                    raise AssertionError("seed pair state is incomplete")
                start = self._starts[self._anchor_index]
                vehicle_id = self._vehicles[self._vehicle_index]
                self._vehicle_index += 1
                if self._vehicle_index >= len(self._vehicles):
                    self._vehicle_index = 0
                    self._anchor_index += 1
                duration = timedelta(
                    seconds=self.context.constraints[commitment_id].duration_seconds
                )
                end = start + duration
                if end > self.context.horizon_end:
                    continue
                commitment = self.context.commitments[commitment_id]
                return _Draft((ProposedWorkerPlacement(
                    commitment_id=commitment_id,
                    job_id=commitment.job_id,
                    task_id=commitment.task_id,
                    worker_id=worker_id,
                    vehicle_id=vehicle_id,
                    business_date=start.astimezone(self.context.zone).date(),
                    proposed_start=start,
                    proposed_end=end,
                ),))
            if not self._probe_next_pair():
                return None


def _final_schedule(context: _Context, draft: _Draft) -> dict[str, ScheduledPlacementEvidence]:
    result = dict(context.active_placements)
    for proposed in draft.placements:
        original = result[proposed.commitment_id]
        result[proposed.commitment_id] = ScheduledPlacementEvidence(
            commitment_id=original.commitment_id,
            job_id=original.job_id,
            task_id=original.task_id,
            task_definition_version=original.task_definition_version,
            business_date=proposed.business_date,
            worker_ids=(proposed.worker_id,),
            vehicle_id=proposed.vehicle_id,
            planned_start=proposed.proposed_start,
            planned_end=proposed.proposed_end,
        )
    return result


def _result(
    status: ConstraintStatus,
    code: ReasonCode,
    placement: ProposedWorkerPlacement,
    subject_id: str,
    *,
    related: tuple[str, ...] = (),
    evidence: tuple[str, ...] = (),
    required: str | None = None,
    actual: str | None = None,
) -> TechnicalConstraintResult:
    return TechnicalConstraintResult(
        status=status,
        reason_code=code,
        commitment_id=placement.commitment_id,
        subject_id=subject_id,
        related_commitment_ids=related,
        evidence_reference_ids=evidence,
        required_value=required,
        actual_value=actual,
    )


def _availability_result(
    placement: ProposedWorkerPlacement,
    evidence: AvailabilityEvidence,
    *,
    vehicle: bool,
) -> TechnicalConstraintResult:
    unknown_code = ReasonCode.VEHICLE_EVIDENCE_UNKNOWN if vehicle else ReasonCode.AVAILABILITY_UNKNOWN
    unavailable_code = ReasonCode.VEHICLE_UNAVAILABLE if vehicle else ReasonCode.WORKER_UNAVAILABLE
    available_code = ReasonCode.VEHICLE_AVAILABLE if vehicle else ReasonCode.WORKER_AVAILABLE
    if evidence.knowledge is AvailabilityKnowledge.UNKNOWN:
        return _result(ConstraintStatus.UNKNOWN, unknown_code, placement, evidence.subject_id)
    source = (evidence.source_record_id,)
    if not (evidence.coverage_start <= placement.proposed_start and placement.proposed_end <= evidence.coverage_end):
        return _result(ConstraintStatus.UNKNOWN, unknown_code, placement, evidence.subject_id, evidence=source)
    covered = any(
        window.start_at <= placement.proposed_start and placement.proposed_end <= window.end_at
        for window in evidence.available_windows
    )
    return _result(
        ConstraintStatus.PASS if covered else ConstraintStatus.FAIL,
        available_code if covered else unavailable_code,
        placement,
        evidence.subject_id,
        evidence=source,
    )


def _affected_dependencies(context: _Context, modified_task_ids: set[str]) -> tuple[DependencyEvidence, ...]:
    affected = set(modified_task_ids)
    active_tasks = set(context.task_to_commitment)
    changed = True
    while changed:
        changed = False
        for edge in context.evaluation.dependency_impact.edges:
            if (
                edge.predecessor_task_id in active_tasks
                and edge.successor_task_id in active_tasks
                and edge.predecessor_task_id in affected
                and edge.successor_task_id not in affected
            ):
                affected.add(edge.successor_task_id)
                changed = True
    return tuple(
        edge
        for edge in context.evaluation.dependency_impact.edges
        if edge.predecessor_task_id in active_tasks
        and edge.successor_task_id in active_tasks
        and (
            edge.predecessor_task_id in affected
            or edge.successor_task_id in affected
        )
    )


def _candidate_induced_refs(
    context: _Context,
    draft: _Draft,
    schedule: dict[str, ScheduledPlacementEvidence],
    dependencies: tuple[DependencyEvidence, ...],
) -> tuple[ImpactCommitmentReference, ...]:
    direct = set(context.direct_ids)
    modified = {item.commitment_id for item in draft.placements}
    proposed_workers = {item.worker_id for item in draft.placements}
    proposed_vehicles = {item.vehicle_id for item in draft.placements if item.vehicle_id is not None}
    induced = set(modified - direct)
    for commitment_id, placement in schedule.items():
        if commitment_id in modified:
            continue
        if proposed_workers.intersection(placement.worker_ids) or (
            placement.vehicle_id is not None and placement.vehicle_id in proposed_vehicles
        ):
            induced.add(commitment_id)
    reached_tasks = {item.task_id for item in draft.placements}
    changed = True
    while changed:
        changed = False
        for edge in dependencies:
            if (
                edge.predecessor_task_id in reached_tasks
                and edge.successor_task_id not in reached_tasks
            ):
                reached_tasks.add(edge.successor_task_id)
                changed = True
    for task_id in reached_tasks - {item.task_id for item in draft.placements}:
        successor = context.task_to_commitment.get(task_id)
        if successor is not None:
            induced.add(successor)
    return tuple(
        ImpactCommitmentReference(
            commitment_id=context.commitments[item].commitment_id,
            job_id=context.commitments[item].job_id,
            task_id=context.commitments[item].task_id,
        )
        for item in induced
    )


def _route_between(context: _Context, first_id: str, second_id: str) -> RouteEvidence | None:
    first = context.constraints[first_id]
    second = context.constraints[second_id]
    if (
        first.location_reference,
        first.location_fingerprint,
    ) == (
        second.location_reference,
        second.location_fingerprint,
    ):
        return None
    return context.routes.get(
        (
            first.location_fingerprint,
            second.location_fingerprint,
            first.location_reference,
            second.location_reference,
        )
    )


def _evaluate_draft(context: _Context, draft: _Draft) -> M3RepairCandidate:
    modified_ids = {item.commitment_id for item in draft.placements}
    if len(modified_ids) > MAX_MODIFIED_COMMITMENTS:
        raise M3BoundedFeasibilityValidationError("SEARCH_ENVELOPE_LIMIT", "modified_commitment_ids")
    schedule = _final_schedule(context, draft)
    proposed_by_id = {item.commitment_id: item for item in draft.placements}
    results: list[TechnicalConstraintResult] = []
    authority_impacts: set[ReasonCode] = set()

    for placement in sorted(draft.placements, key=lambda item: (item.job_id, item.task_id, item.commitment_id)):
        constraint = context.constraints[placement.commitment_id]
        worker = context.workers[placement.worker_id]
        level = next(
            (item.level for item in worker.skill_levels if item.required_skill_key == constraint.required_skill_key),
            None,
        )
        if not worker.m8_worker_known or level is None:
            results.append(_result(
                ConstraintStatus.UNKNOWN,
                ReasonCode.SKILL_EVIDENCE_UNKNOWN,
                placement,
                placement.worker_id,
                required=f"{constraint.required_skill_key}:{constraint.minimum_skill_level}",
                actual="UNKNOWN",
            ))
        elif level < constraint.minimum_skill_level:
            results.append(_result(
                ConstraintStatus.FAIL,
                ReasonCode.EXACT_SKILL_MISMATCH,
                placement,
                placement.worker_id,
                required=f"{constraint.required_skill_key}:{constraint.minimum_skill_level}",
                actual=f"{constraint.required_skill_key}:{level}",
            ))
        else:
            results.append(_result(
                ConstraintStatus.PASS,
                ReasonCode.EXACT_SKILL_MATCH,
                placement,
                placement.worker_id,
                required=f"{constraint.required_skill_key}:{constraint.minimum_skill_level}",
                actual=f"{constraint.required_skill_key}:{level}",
            ))
        results.append(_availability_result(
            placement,
            context.worker_availability[placement.worker_id],
            vehicle=False,
        ))
        readiness = context.readiness[placement.commitment_id]
        if readiness.status is ReadinessState.READY:
            readiness_status, readiness_code = ConstraintStatus.PASS, ReasonCode.READINESS_READY
        elif readiness.status is ReadinessState.UNKNOWN:
            readiness_status, readiness_code = ConstraintStatus.UNKNOWN, ReasonCode.READINESS_UNKNOWN
        else:
            readiness_status, readiness_code = ConstraintStatus.FAIL, ReasonCode.READINESS_NOT_READY
        results.append(_result(
            readiness_status,
            readiness_code,
            placement,
            placement.task_id,
            evidence=(() if readiness.source_record_id is None else (readiness.source_record_id,)),
            required=ReadinessState.READY.value,
            actual=readiness.status.value,
        ))

        if constraint.required_vehicle_class == VehicleClass.NONE.value:
            results.append(_result(
                ConstraintStatus.PASS,
                ReasonCode.VEHICLE_NOT_REQUIRED,
                placement,
                placement.commitment_id,
                required=VehicleClass.NONE.value,
            ))
        elif placement.vehicle_id is None:
            results.append(_result(
                ConstraintStatus.FAIL,
                ReasonCode.VEHICLE_UNAVAILABLE,
                placement,
                placement.commitment_id,
                required=constraint.required_vehicle_class,
                actual="NO_CANONICAL_VEHICLE",
            ))
        else:
            vehicle = context.vehicles.get(placement.vehicle_id)
            if vehicle is None or constraint.required_vehicle_class not in vehicle.capabilities:
                results.append(_result(
                    ConstraintStatus.FAIL,
                    ReasonCode.VEHICLE_UNAVAILABLE,
                    placement,
                    placement.vehicle_id,
                    required=constraint.required_vehicle_class,
                    actual="CAPABILITY_MISMATCH",
                ))
            else:
                results.append(_availability_result(
                    placement,
                    context.vehicle_availability[placement.vehicle_id],
                    vehicle=True,
                ))
                if vehicle.kind == "PRIVATE":
                    authority_impacts.add(ReasonCode.PRIVATE_VEHICLE_AUTHORITY_REQUIRED)

        if constraint.customer_window_state is ConstraintKnowledge.UNKNOWN:
            results.append(_result(
                ConstraintStatus.UNKNOWN,
                ReasonCode.CUSTOMER_WINDOW_EVIDENCE_UNKNOWN,
                placement,
                placement.task_id,
            ))
        elif constraint.customer_window_state is ConstraintKnowledge.ABSENT:
            results.append(_result(
                ConstraintStatus.PASS,
                ReasonCode.CUSTOMER_WINDOW_ABSENT,
                placement,
                placement.task_id,
            ))
        else:
            fits = constraint.customer_window_start <= placement.proposed_start and placement.proposed_end <= constraint.customer_window_end
            results.append(_result(
                ConstraintStatus.PASS if fits else ConstraintStatus.FAIL,
                ReasonCode.TECHNICALLY_FITS_WINDOW if fits else ReasonCode.CUSTOMER_WINDOW_VIOLATION,
                placement,
                placement.task_id,
                required=f"{constraint.customer_window_start.isoformat()}/{constraint.customer_window_end.isoformat()}",
                actual=f"{placement.proposed_start.isoformat()}/{placement.proposed_end.isoformat()}",
            ))
        if constraint.hard_deadline_state is ConstraintKnowledge.UNKNOWN:
            results.append(_result(
                ConstraintStatus.UNKNOWN,
                ReasonCode.DEADLINE_EVIDENCE_UNKNOWN,
                placement,
                placement.task_id,
            ))
        elif constraint.hard_deadline_state is ConstraintKnowledge.ABSENT:
            results.append(_result(
                ConstraintStatus.PASS,
                ReasonCode.DEADLINE_ABSENT,
                placement,
                placement.task_id,
            ))
        else:
            meets = placement.proposed_end <= constraint.hard_deadline
            results.append(_result(
                ConstraintStatus.PASS if meets else ConstraintStatus.FAIL,
                ReasonCode.DEADLINE_MET if meets else ReasonCode.DEADLINE_VIOLATION,
                placement,
                placement.task_id,
                required=constraint.hard_deadline.isoformat(),
                actual=placement.proposed_end.isoformat(),
            ))

    # Half-open overlap validation considers every active, non-DONE placement,
    # while only conflicts involving this hypothetical change can reject it.
    ordered_schedule = sorted(
        schedule.values(),
        key=lambda item: (item.planned_start, item.planned_end, item.job_id, item.task_id, item.commitment_id),
    )
    for index, first in enumerate(ordered_schedule):
        for second in ordered_schedule[index + 1 :]:
            if first.commitment_id not in modified_ids and second.commitment_id not in modified_ids:
                continue
            shared_workers = tuple(sorted(set(first.worker_ids).intersection(second.worker_ids)))
            shared_vehicle = first.vehicle_id if first.vehicle_id is not None and first.vehicle_id == second.vehicle_id else None
            if not shared_workers and shared_vehicle is None:
                continue
            if intervals_overlap(first.planned_start, first.planned_end, second.planned_start, second.planned_end):
                subject = shared_workers[0] if shared_workers else shared_vehicle
                anchor = proposed_by_id.get(first.commitment_id) or proposed_by_id[second.commitment_id]
                results.append(_result(
                    ConstraintStatus.FAIL,
                    ReasonCode.OVERLAP_CONFLICT,
                    anchor,
                    subject,
                    related=(first.commitment_id, second.commitment_id),
                ))

    travel_buffer_seconds = 0
    resource_sequences: list[tuple[str, list[ScheduledPlacementEvidence]]] = []
    for worker_id in sorted({item.worker_id for item in draft.placements}):
        resource_sequences.append((worker_id, [item for item in ordered_schedule if worker_id in item.worker_ids]))
    for vehicle_id in sorted({item.vehicle_id for item in draft.placements if item.vehicle_id is not None}):
        resource_sequences.append((vehicle_id, [item for item in ordered_schedule if item.vehicle_id == vehicle_id]))
    for resource_id, sequence in resource_sequences:
        for first, second in zip(sequence, sequence[1:]):
            if first.commitment_id not in modified_ids and second.commitment_id not in modified_ids:
                continue
            route = _route_between(context, first.commitment_id, second.commitment_id)
            anchor = proposed_by_id.get(second.commitment_id) or proposed_by_id[first.commitment_id]
            if route is None:
                if context.constraints[first.commitment_id].location_fingerprint == context.constraints[second.commitment_id].location_fingerprint:
                    results.append(_result(
                        ConstraintStatus.PASS,
                        ReasonCode.ROUTE_BUFFER_SATISFIED,
                        anchor,
                        resource_id,
                        related=(first.commitment_id, second.commitment_id),
                        required="0",
                        actual=str(int((second.planned_start - first.planned_end).total_seconds())),
                    ))
                else:
                    results.append(_result(
                        ConstraintStatus.UNKNOWN,
                        ReasonCode.ROUTE_EVIDENCE_UNKNOWN,
                        anchor,
                        resource_id,
                        related=(first.commitment_id, second.commitment_id),
                    ))
                continue
            if route.knowledge is RouteKnowledge.UNKNOWN:
                results.append(_result(
                    ConstraintStatus.UNKNOWN,
                    ReasonCode.ROUTE_EVIDENCE_UNKNOWN,
                    anchor,
                    resource_id,
                    related=(first.commitment_id, second.commitment_id),
                ))
                continue
            required_seconds = route.travel_duration_seconds + route.buffer_seconds
            actual_seconds = int((second.planned_start - first.planned_end).total_seconds())
            travel_buffer_seconds += required_seconds
            fits = actual_seconds >= required_seconds
            results.append(_result(
                ConstraintStatus.PASS if fits else ConstraintStatus.FAIL,
                ReasonCode.ROUTE_BUFFER_SATISFIED if fits else ReasonCode.ROUTE_BUFFER_VIOLATION,
                anchor,
                resource_id,
                related=(first.commitment_id, second.commitment_id),
                evidence=(() if route.source_record_id is None else (route.source_record_id,)),
                required=str(required_seconds),
                actual=str(actual_seconds),
            ))

    dependencies = _affected_dependencies(context, {item.task_id for item in draft.placements})
    for edge in dependencies:
        predecessor_id = context.task_to_commitment.get(edge.predecessor_task_id)
        successor_id = context.task_to_commitment.get(edge.successor_task_id)
        if predecessor_id is None or successor_id is None:
            continue
        predecessor, successor = schedule[predecessor_id], schedule[successor_id]
        fits = predecessor.planned_end <= successor.planned_start
        anchor = proposed_by_id.get(predecessor_id) or proposed_by_id.get(successor_id) or draft.placements[0]
        results.append(_result(
            ConstraintStatus.PASS if fits else ConstraintStatus.FAIL,
            ReasonCode.DEPENDENCY_SATISFIED if fits else ReasonCode.DEPENDENCY_VIOLATION,
            anchor,
            f"{edge.predecessor_task_id}->{edge.successor_task_id}",
            related=(predecessor_id, successor_id),
            evidence=(edge.provenance_reference,),
            required="FINISH_BEFORE_START",
            actual=f"{predecessor.planned_end.isoformat()}/{successor.planned_start.isoformat()}",
        ))

    verdict = CandidateVerdict.FEASIBLE if all(item.status is ConstraintStatus.PASS for item in results) else CandidateVerdict.REJECTED
    baseline_direct = tuple(context.evaluation.direct_current_impact.commitments)
    baseline_dependency = tuple(
        ImpactCommitmentReference(
            commitment_id=item.commitment_id,
            job_id=item.job_id,
            task_id=item.task_id,
        )
        for item in context.evaluation.dependency_impact.commitments
    )
    baseline = {
        item.commitment_id: item for item in baseline_direct + baseline_dependency
    }
    restored = tuple(
        baseline[item]
        for item in modified_ids
        if verdict is CandidateVerdict.FEASIBLE and item in baseline
    )
    residual = tuple(item for key, item in baseline.items() if key not in {value.commitment_id for value in restored})
    induced = _candidate_induced_refs(context, draft, schedule, dependencies)
    impact = CandidateImpactEvidence(
        direct_current_impact=baseline_direct,
        dependency_impact=baseline_dependency,
        candidate_induced_impact=induced,
        restored_impact=restored,
        residual_unresolved_impact=residual,
    )
    protected_windows = sum(
        item.reason_code is ReasonCode.TECHNICALLY_FITS_WINDOW and item.status is ConstraintStatus.PASS
        for item in results
    )
    rejection = tuple(item for item in results if item.status is not ConstraintStatus.PASS)
    return M3RepairCandidate(
        source_evaluation_input_id=context.evaluation.evaluation_input_id,
        source_evaluation_input_fingerprint=context.evaluation.evaluation_input_fingerprint,
        source_support_snapshot_id=context.support.support_snapshot_id,
        source_support_snapshot_fingerprint=context.support.support_fingerprint,
        company_plan_id=context.evaluation.company_plan_id,
        base_plan_revision=context.evaluation.base_plan_revision,
        base_plan_revision_id=context.evaluation.base_plan_revision_id,
        base_plan_revision_fingerprint=context.evaluation.base_plan_revision_fingerprint,
        modified_commitment_ids=tuple(item.commitment_id for item in sorted(draft.placements, key=lambda value: (value.job_id, value.task_id, value.commitment_id))),
        canonical_job_ids=tuple(sorted({item.job_id for item in draft.placements})),
        canonical_task_ids=tuple(sorted({item.task_id for item in draft.placements})),
        proposed_worker_placements=draft.placements,
        affected_dependencies=dependencies,
        impact=impact,
        technical_constraint_results=tuple(results),
        verdict=verdict,
        rejection_trace=rejection,
        authority_impacts=tuple(authority_impacts),
        technical_ordering=TechnicalOrderingEvidence(
            restored_commitment_count=len(restored),
            protected_known_customer_window_count=protected_windows,
            modified_commitment_count=len(modified_ids),
            travel_and_buffer_seconds=travel_buffer_seconds,
        ),
    )


def _overlap_conflict_ids(candidate: M3RepairCandidate) -> tuple[str, ...]:
    modified = set(candidate.modified_commitment_ids)
    result = set()
    for item in candidate.rejection_trace:
        if item.reason_code is ReasonCode.OVERLAP_CONFLICT:
            result.update(value for value in item.related_commitment_ids if value not in modified)
    return tuple(sorted(result))


def _envelope_limit_trace(
    commitment_ids: set[str],
    detail: str,
) -> SearchTraceEntry:
    return SearchTraceEntry(
        reason_code=ReasonCode.SEARCH_ENVELOPE_LIMIT,
        commitment_ids=tuple(sorted(commitment_ids)),
        detail=detail,
    )


def _repair_eligibility_trace(
    context: _Context,
    commitment_id: str,
    *,
    conflict_kind: str,
) -> SearchTraceEntry | None:
    kind = _commitment_kind(context, commitment_id)
    if kind is AssignmentKind.CREW:
        return SearchTraceEntry(
            reason_code=ReasonCode.CREW_REPAIR_UNSUPPORTED,
            commitment_ids=(commitment_id,),
            detail=f"{conflict_kind} commitment is not eligible for autonomous SINGLE repair",
        )
    if kind is not AssignmentKind.SINGLE:
        return SearchTraceEntry(
            reason_code=ReasonCode.SINGLE_KIND_EVIDENCE_UNKNOWN,
            commitment_ids=(commitment_id,),
            detail="authoritative input does not prove SINGLE assignment kind",
        )
    original = context.active_placements[commitment_id]
    if len(original.worker_ids) != 1:
        return SearchTraceEntry(
            reason_code=ReasonCode.CREW_REPAIR_UNSUPPORTED,
            commitment_ids=(commitment_id,),
            detail=f"{conflict_kind} placement does not contain exactly one worker",
        )
    worker_id = original.worker_ids[0]
    if worker_id not in context.workers:
        return SearchTraceEntry(
            reason_code=ReasonCode.SKILL_EVIDENCE_UNKNOWN,
            commitment_ids=(commitment_id,),
            detail=f"{conflict_kind} worker has no frozen technical evidence",
        )
    if worker_id not in context.worker_availability:
        return SearchTraceEntry(
            reason_code=ReasonCode.AVAILABILITY_UNKNOWN,
            commitment_ids=(commitment_id,),
            detail=f"{conflict_kind} worker has no frozen availability evidence",
        )
    return None


def _expanded_draft(
    draft: _Draft,
    placement: ProposedWorkerPlacement,
) -> _Draft:
    unchanged = tuple(
        item
        for item in draft.placements
        if item.commitment_id != placement.commitment_id
    )
    return _Draft(unchanged + (placement,))


def _expand_overlap(
    context: _Context,
    draft: _Draft,
    conflict_id: str,
) -> tuple[_Draft | None, SearchTraceEntry | None]:
    modified = {item.commitment_id for item in draft.placements}
    if len(modified) >= MAX_MODIFIED_COMMITMENTS:
        return None, _envelope_limit_trace(
            modified | {conflict_id},
            "candidate would modify more than 3 existing commitments",
        )
    eligibility = _repair_eligibility_trace(
        context,
        conflict_id,
        conflict_kind="overlap-conflicting",
    )
    if eligibility is not None:
        return None, eligibility
    original = context.active_placements[conflict_id]
    latest = max(draft.placements, key=lambda item: (item.proposed_end, item.commitment_id))
    route = _route_between(context, latest.commitment_id, conflict_id)
    route_seconds = 0
    if route is not None and route.knowledge is RouteKnowledge.KNOWN:
        route_seconds = route.travel_duration_seconds + route.buffer_seconds
    start = latest.proposed_end + timedelta(seconds=route_seconds)
    end = start + timedelta(seconds=context.constraints[conflict_id].duration_seconds)
    if not (context.horizon_start <= start < end <= context.horizon_end):
        return None, _envelope_limit_trace(
            {conflict_id},
            "overlap repair would leave D..D+2 planning horizon",
        )
    extra = ProposedWorkerPlacement(
        commitment_id=conflict_id,
        job_id=context.commitments[conflict_id].job_id,
        task_id=context.commitments[conflict_id].task_id,
        worker_id=original.worker_ids[0],
        vehicle_id=original.vehicle_id,
        business_date=start.astimezone(context.zone).date(),
        proposed_start=start,
        proposed_end=end,
    )
    return _expanded_draft(draft, extra), None


def _first_dependency_violation(
    candidate: M3RepairCandidate,
) -> DependencyEvidence | None:
    rejected = tuple(
        item
        for item in candidate.rejection_trace
        if item.reason_code is ReasonCode.DEPENDENCY_VIOLATION
    )
    modified = set(candidate.modified_commitment_ids)
    for edge in candidate.affected_dependencies:
        violation = next(
            (
                item
                for item in rejected
                if item.subject_id
                == f"{edge.predecessor_task_id}->{edge.successor_task_id}"
            ),
            None,
        )
        if violation is not None and modified.intersection(
            violation.related_commitment_ids
        ):
            return edge
    return None


def _shared_route_seconds(
    context: _Context,
    first: ScheduledPlacementEvidence,
    second: ScheduledPlacementEvidence,
) -> int:
    shared_worker = bool(set(first.worker_ids).intersection(second.worker_ids))
    shared_vehicle = (
        first.vehicle_id is not None and first.vehicle_id == second.vehicle_id
    )
    if not shared_worker and not shared_vehicle:
        return 0
    route = _route_between(context, first.commitment_id, second.commitment_id)
    if route is not None and route.knowledge is RouteKnowledge.KNOWN:
        return route.travel_duration_seconds + route.buffer_seconds
    # Zero is not an evidence default: the ordinary validator will reject or
    # classify UNKNOWN when distinct locations lack known route evidence.
    return 0


def _expand_dependency(
    context: _Context,
    draft: _Draft,
    edge: DependencyEvidence,
) -> tuple[_Draft | None, SearchTraceEntry | None]:
    schedule = _final_schedule(context, draft)
    predecessor_id = context.task_to_commitment[edge.predecessor_task_id]
    successor_id = context.task_to_commitment[edge.successor_task_id]
    modified = {item.commitment_id for item in draft.placements}
    if predecessor_id in modified:
        target_id = successor_id
        move_after_predecessor = True
    elif successor_id in modified:
        target_id = predecessor_id
        move_after_predecessor = False
    else:
        return None, None
    if target_id not in modified and len(modified) >= MAX_MODIFIED_COMMITMENTS:
        return None, _envelope_limit_trace(
            modified | {target_id},
            "candidate would modify more than 3 existing commitments",
        )
    eligibility = _repair_eligibility_trace(
        context,
        target_id,
        conflict_kind="dependency-counterpart",
    )
    if eligibility is not None:
        return None, eligibility

    original = context.active_placements[target_id]
    existing = next(
        (item for item in draft.placements if item.commitment_id == target_id),
        None,
    )
    worker_id = existing.worker_id if existing is not None else original.worker_ids[0]
    vehicle_id = existing.vehicle_id if existing is not None else original.vehicle_id
    duration = timedelta(seconds=context.constraints[target_id].duration_seconds)
    if move_after_predecessor:
        predecessor = schedule[predecessor_id]
        target_shape = ScheduledPlacementEvidence(
            commitment_id=original.commitment_id,
            job_id=original.job_id,
            task_id=original.task_id,
            task_definition_version=original.task_definition_version,
            business_date=predecessor.planned_end.astimezone(context.zone).date(),
            worker_ids=(worker_id,),
            vehicle_id=vehicle_id,
            planned_start=predecessor.planned_end,
            planned_end=predecessor.planned_end + duration,
        )
        start = predecessor.planned_end + timedelta(
            seconds=_shared_route_seconds(context, predecessor, target_shape)
        )
        end = start + duration
    else:
        successor = schedule[successor_id]
        target_shape = ScheduledPlacementEvidence(
            commitment_id=original.commitment_id,
            job_id=original.job_id,
            task_id=original.task_id,
            task_definition_version=original.task_definition_version,
            business_date=(successor.planned_start - duration).astimezone(context.zone).date(),
            worker_ids=(worker_id,),
            vehicle_id=vehicle_id,
            planned_start=successor.planned_start - duration,
            planned_end=successor.planned_start,
        )
        end = successor.planned_start - timedelta(
            seconds=_shared_route_seconds(context, target_shape, successor)
        )
        start = end - duration
    if not (context.horizon_start <= start < end <= context.horizon_end):
        return None, _envelope_limit_trace(
            modified | {target_id},
            "dependency repair would leave D..D+2 planning horizon",
        )
    commitment = context.commitments[target_id]
    replacement = ProposedWorkerPlacement(
        commitment_id=target_id,
        job_id=commitment.job_id,
        task_id=commitment.task_id,
        worker_id=worker_id,
        vehicle_id=vehicle_id,
        business_date=start.astimezone(context.zone).date(),
        proposed_start=start,
        proposed_end=end,
    )
    return _expanded_draft(draft, replacement), None


def _expand_first_repairable_rejection(
    context: _Context,
    draft: _Draft,
    candidate: M3RepairCandidate,
) -> tuple[_Draft | None, SearchTraceEntry | None]:
    """Follow one stable path: first overlap ID, then first explicit dependency."""

    conflicts = _overlap_conflict_ids(candidate)
    if conflicts:
        return _expand_overlap(context, draft, conflicts[0])
    dependency = _first_dependency_violation(candidate)
    if dependency is not None:
        return _expand_dependency(context, draft, dependency)
    return None, None


def generate_bounded_repair_candidates(
    evaluation_input: M3EvaluationInput,
    support_snapshot: FeasibilitySupportSnapshot,
) -> M3BoundedFeasibilityResult:
    """Evaluate at most 12 deterministic candidates from one exact evidence cut.

    Seed pairs are streamed by direct commitment `(job, task, commitment)` and
    then worker ID.  Only when a pair is reached are its original-first/UTC
    anchors and lexical vehicle options inspected.  At most 13 pairs are probed.
    A rejected draft follows one immediate repair path: first stable overlap,
    otherwise first stable explicit dependency.  The 12-candidate guard is
    checked immediately before technical validation.
    """

    context = _validated_context(evaluation_input, support_snapshot)
    search_trace: list[SearchTraceEntry] = []
    if not context.direct_ids:
        return _make_result(context, SearchOutcome.NO_REPAIR_REQUIRED, (), False, (), (), ())

    single_ids, crew_ids, unknown_ids = [], [], []
    for commitment_id in context.direct_ids:
        kind = _commitment_kind(context, commitment_id)
        if kind is AssignmentKind.SINGLE:
            single_ids.append(commitment_id)
        elif kind is AssignmentKind.CREW:
            crew_ids.append(commitment_id)
            search_trace.append(SearchTraceEntry(
                reason_code=ReasonCode.CREW_REPAIR_UNSUPPORTED,
                commitment_ids=(commitment_id,),
                detail="autonomous M3 v0.1 repair supports SINGLE commitments only",
            ))
        else:
            unknown_ids.append(commitment_id)
            search_trace.append(SearchTraceEntry(
                reason_code=ReasonCode.SINGLE_KIND_EVIDENCE_UNKNOWN,
                commitment_ids=(commitment_id,),
                detail="historical evidence does not prove SINGLE assignment kind",
            ))
    single_ids_tuple = tuple(sorted(single_ids, key=lambda item: (
        context.commitments[item].job_id,
        context.commitments[item].task_id,
        item,
    )))
    if not single_ids_tuple:
        outcome = SearchOutcome.CREW_REPAIR_UNSUPPORTED if crew_ids and not unknown_ids else SearchOutcome.INSUFFICIENT_CRITICAL_EVIDENCE
        return _make_result(
            context,
            outcome,
            (),
            False,
            tuple(crew_ids),
            tuple(unknown_ids),
            tuple(search_trace),
        )

    primary = _PrimaryDraftStream(context, single_ids_tuple)
    pending: deque[_Draft] = deque()
    search_exhausted = False
    evaluation_budget_truncated = False
    candidates: list[M3RepairCandidate] = []
    seen = set()
    insufficient = set(unknown_ids)
    while len(candidates) < MAX_EVALUATED_CANDIDATES:
        draft = pending.popleft() if pending else primary.next_draft()
        if draft is None:
            break
        if draft.signature in seen:
            continue
        seen.add(draft.signature)
        # Exact enforcement point: no draft can reach _evaluate_draft after 12.
        if len(candidates) >= MAX_EVALUATED_CANDIDATES:
            search_exhausted = True
            break
        candidate = _evaluate_draft(context, draft)
        candidates.append(candidate)
        if any(item.status is ConstraintStatus.UNKNOWN for item in candidate.rejection_trace):
            insufficient.update(candidate.modified_commitment_ids)
        expansion, trace = _expand_first_repairable_rejection(
            context,
            draft,
            candidate,
        )
        if trace is not None:
            search_trace.append(trace)
            if trace.reason_code in (
                ReasonCode.SINGLE_KIND_EVIDENCE_UNKNOWN,
                ReasonCode.SKILL_EVIDENCE_UNKNOWN,
                ReasonCode.AVAILABILITY_UNKNOWN,
            ):
                insufficient.update(trace.commitment_ids)
            if trace.reason_code is ReasonCode.SEARCH_ENVELOPE_LIMIT:
                search_exhausted = True
        if expansion is not None and expansion.signature not in seen:
            if len(candidates) < MAX_EVALUATED_CANDIDATES:
                pending.appendleft(expansion)
            else:
                search_exhausted = True
                evaluation_budget_truncated = True

    if primary.probe_budget_exhausted:
        search_exhausted = True
        search_trace.append(SearchTraceEntry(
            reason_code=ReasonCode.SEARCH_ENVELOPE_LIMIT,
            commitment_ids=tuple(single_ids_tuple),
            detail=(
                "seed-pair probing stopped at the explicit "
                f"{MAX_SEED_PAIR_PROBES}-pair budget"
            ),
        ))
    if len(candidates) >= MAX_EVALUATED_CANDIDATES and (
        evaluation_budget_truncated or pending or primary.has_unexplored
    ):
        search_exhausted = True
        search_trace.append(SearchTraceEntry(
            reason_code=ReasonCode.SEARCH_ENVELOPE_LIMIT,
            commitment_ids=tuple(single_ids_tuple),
            detail="technical validation stopped at 12 candidate variants",
        ))
    feasible = any(item.verdict is CandidateVerdict.FEASIBLE for item in candidates)
    if feasible:
        outcome = SearchOutcome.FEASIBLE_CANDIDATES_FOUND
    elif search_exhausted:
        outcome = SearchOutcome.SEARCH_ENVELOPE_EXHAUSTED
    elif insufficient:
        outcome = SearchOutcome.INSUFFICIENT_CRITICAL_EVIDENCE
    elif crew_ids:
        outcome = SearchOutcome.CREW_REPAIR_UNSUPPORTED
    else:
        outcome = SearchOutcome.ALL_EVALUATED_CANDIDATES_REJECTED
    return _make_result(
        context,
        outcome,
        tuple(candidates),
        search_exhausted,
        tuple(crew_ids),
        tuple(insufficient),
        tuple(search_trace),
    )


def _make_result(
    context: _Context,
    outcome: SearchOutcome,
    candidates: tuple[M3RepairCandidate, ...],
    exhausted: bool,
    unsupported: tuple[str, ...],
    insufficient: tuple[str, ...],
    trace: tuple[SearchTraceEntry, ...],
) -> M3BoundedFeasibilityResult:
    return M3BoundedFeasibilityResult(
        source_evaluation_input_id=context.evaluation.evaluation_input_id,
        source_evaluation_input_fingerprint=context.evaluation.evaluation_input_fingerprint,
        source_support_snapshot_id=context.support.support_snapshot_id,
        source_support_snapshot_fingerprint=context.support.support_fingerprint,
        company_plan_id=context.evaluation.company_plan_id,
        base_plan_revision=context.evaluation.base_plan_revision,
        outcome=outcome,
        candidates=candidates,
        feasible_candidate_ids=tuple(item.candidate_id for item in candidates if item.verdict is CandidateVerdict.FEASIBLE),
        rejected_candidate_ids=tuple(item.candidate_id for item in candidates if item.verdict is CandidateVerdict.REJECTED),
        evaluated_candidate_count=len(candidates),
        search_envelope_exhausted=exhausted,
        global_solution_status="NOT_EVALUATED_GLOBALLY",
        unsupported_commitment_ids=unsupported,
        insufficient_evidence_commitment_ids=insufficient,
        search_trace=trace,
    )


class _PersistedEvidenceReader(Protocol):
    def get_evaluation_input(self, evaluation_input_id: str) -> M3EvaluationInput: ...

    def get_feasibility_support(self, support_snapshot_id: str) -> FeasibilitySupportSnapshot: ...


class M3BoundedFeasibilityService:
    """Read-only persisted-input adapter; it exposes no source or result writes."""

    def __init__(self, repository: _PersistedEvidenceReader) -> None:
        self._repository = repository

    def evaluate(
        self,
        evaluation_input_id: str,
        support_snapshot_id: str,
    ) -> M3BoundedFeasibilityResult:
        _identity(evaluation_input_id, "evaluation_input_id")
        _identity(support_snapshot_id, "support_snapshot_id")
        evaluation = self._repository.get_evaluation_input(evaluation_input_id)
        support = self._repository.get_feasibility_support(support_snapshot_id)
        return generate_bounded_repair_candidates(evaluation, support)
