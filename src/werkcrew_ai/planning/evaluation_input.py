"""M3-B immutable fresh EvaluationInput and deterministic impact scopes."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, replace
from datetime import date
from enum import StrEnum

from werkcrew_ai.field.models import AssignmentKind, TaskStatus
from werkcrew_ai.field.serialization import canonical_json, sha256_text
from werkcrew_ai.planning.m3_unavailability_impact import M3ImpactOutcome


EVALUATION_INPUT_SCHEMA_VERSION = "m3-evaluation-input-v1"
IMPACT_ANALYSIS_RULE_VERSION = "m3-unavailability-impact-analysis-v1"
CANDIDATE_ABSENCE = "NO_CANDIDATE_PROVIDED"


class M3EvaluationInputError(ValueError):
    def __init__(self, code: str, field_name: str) -> None:
        self.code, self.field_name = code, field_name
        super().__init__(f"{code}: {field_name}")


class M3EvaluationInputValidationError(M3EvaluationInputError):
    pass


class M3EvaluationInputStorageError(M3EvaluationInputError):
    pass


class M3EvaluationInputConflictError(M3EvaluationInputError):
    pass


def _require(condition: bool, name: str) -> None:
    if not condition:
        raise M3EvaluationInputValidationError("INVALID_VALUE", name)


def _identity(value: str, name: str) -> None:
    _require(type(value) is str and bool(value) and value == value.strip(), name)
    _require(not any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value), name)


def _optional_identity(value: str | None, name: str) -> None:
    _require(value is None or type(value) is str, name)
    if value is not None:
        _identity(value, name)


def _digest(value: str, name: str) -> None:
    _require(type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value), name)


def _revision(value: int, name: str) -> None:
    _require(type(value) is int and 0 <= value <= 9223372036854775807, name)


def _record(value: object, expected: type, name: str) -> None:
    _require(type(value) is expected, name)
    _require(all(hasattr(value, item.name) for item in fields(expected)), name)


def _identities(values, name: str) -> tuple[str, ...]:
    _require(type(values) in (tuple, list), name)
    for value in values:
        _identity(value, name)
    _require(len(values) == len(set(values)), name)
    return tuple(sorted(values))


def _records(values, expected: type, key, name: str):
    _require(type(values) in (tuple, list), name)
    result = []
    for value in values:
        _record(value, expected, name)
        _require(replace(value) == value, name)
        result.append(value)
    keys = [key(value) for value in result]
    _require(len(keys) == len(set(keys)), name)
    return tuple(sorted(result, key=key))


@dataclass(frozen=True, slots=True, kw_only=True)
class HistoricalJobRootEvidence:
    job_id: str
    revision: int
    content_sha256: str

    def __post_init__(self) -> None:
        _identity(self.job_id, "historical_job_id")
        _revision(self.revision, "historical_job_revision")
        _digest(self.content_sha256, "historical_job_sha256")


@dataclass(frozen=True, slots=True, kw_only=True)
class HistoricalAssignmentEvidence:
    assignment_id: str
    job_id: str
    task_id: str
    task_definition_version: str
    assignment_kind: AssignmentKind
    member_worker_ids: tuple[str, ...]
    plan_day_ids: tuple[str, ...]
    lead_worker_id: str | None
    released_worker_ids: tuple[str, ...]
    supersedes_assignment_id: str | None
    task_status: TaskStatus
    task_source_handoff_id: str
    task_source_revision: int
    supersedes_task_id: str | None
    job_execution_revision: int
    job_root_sha256: str

    def __post_init__(self) -> None:
        for name in ("assignment_id", "job_id", "task_id", "task_definition_version", "task_source_handoff_id"):
            _identity(getattr(self, name), name)
        _require(type(self.assignment_kind) is AssignmentKind, "assignment_kind")
        _require(type(self.task_status) is TaskStatus, "historical_task_status")
        for name in ("member_worker_ids", "plan_day_ids", "released_worker_ids"):
            object.__setattr__(self, name, _identities(getattr(self, name), name))
        _optional_identity(self.lead_worker_id, "lead_worker_id")
        _optional_identity(self.supersedes_assignment_id, "supersedes_assignment_id")
        _optional_identity(self.supersedes_task_id, "supersedes_task_id")
        _revision(self.task_source_revision, "task_source_revision")
        _require(self.task_source_revision > 0, "task_source_revision")
        _revision(self.job_execution_revision, "historical_job_execution_revision")
        _digest(self.job_root_sha256, "historical_job_root_sha256")


@dataclass(frozen=True, slots=True, kw_only=True)
class HistoricalSourceScope:
    request_fingerprint: str
    input_namespace: str
    event_id: str
    server_event_id: str
    input_payload_sha256: str
    reduction_proof_sha256: str
    effect_ordinal: int
    effect_sha256: str
    historical_plan_result_sha256: str
    assignments: tuple[HistoricalAssignmentEvidence, ...]
    job_roots: tuple[HistoricalJobRootEvidence, ...]

    def __post_init__(self) -> None:
        _digest(self.request_fingerprint, "request_fingerprint")
        for name in ("input_namespace", "event_id", "server_event_id"):
            _identity(getattr(self, name), name)
        for name in ("input_payload_sha256", "reduction_proof_sha256", "effect_sha256", "historical_plan_result_sha256"):
            _digest(getattr(self, name), name)
        _revision(self.effect_ordinal, "effect_ordinal")
        object.__setattr__(self, "assignments", _records(
            self.assignments, HistoricalAssignmentEvidence,
            lambda item: (item.job_id, item.task_id, item.assignment_id), "historical_assignments",
        ))
        object.__setattr__(self, "job_roots", _records(
            self.job_roots, HistoricalJobRootEvidence,
            lambda item: item.job_id, "historical_job_roots",
        ))
        _require({item.job_id for item in self.assignments} == {item.job_id for item in self.job_roots}, "historical_scope")


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanDayAssociationEvidence:
    plan_day_id: str
    worker_id: str
    business_date: date

    def __post_init__(self) -> None:
        _identity(self.plan_day_id, "plan_day_id")
        _identity(self.worker_id, "worker_id")
        _require(type(self.business_date) is date, "business_date")


@dataclass(frozen=True, slots=True, kw_only=True)
class CurrentCommitmentEvidence:
    commitment_id: str
    job_id: str
    task_id: str
    task_definition_version: str
    source_handoff_id: str
    source_revision: int
    source_handoff_sha256: str
    business_date: date
    planned_worker_ids: tuple[str, ...]
    source_m2_assignment_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("commitment_id", "job_id", "task_id", "task_definition_version", "source_handoff_id"):
            _identity(getattr(self, name), name)
        _revision(self.source_revision, "source_revision")
        _require(self.source_revision > 0, "source_revision")
        _digest(self.source_handoff_sha256, "source_handoff_sha256")
        _require(type(self.business_date) is date, "business_date")
        for name in ("planned_worker_ids", "source_m2_assignment_ids"):
            object.__setattr__(self, name, _identities(getattr(self, name), name))


@dataclass(frozen=True, slots=True, kw_only=True)
class CurrentM2TaskPrecondition:
    job_id: str
    job_execution_revision: int
    job_root_sha256: str
    task_id: str
    task_definition_version: str
    task_route_sha256: str
    source_handoff_id: str
    source_revision: int
    task_status: TaskStatus

    def __post_init__(self) -> None:
        for name in ("job_id", "task_id", "task_definition_version", "source_handoff_id"):
            _identity(getattr(self, name), name)
        _revision(self.job_execution_revision, "job_execution_revision")
        _digest(self.job_root_sha256, "job_root_sha256")
        _digest(self.task_route_sha256, "task_route_sha256")
        _revision(self.source_revision, "source_revision")
        _require(self.source_revision > 0, "source_revision")
        _require(type(self.task_status) is TaskStatus, "task_status")


@dataclass(frozen=True, slots=True, kw_only=True)
class CurrentM2AssignmentPrecondition:
    assignment_id: str
    job_id: str
    task_id: str
    assignment_route_sha256: str
    plan_day_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("assignment_id", "job_id", "task_id"):
            _identity(getattr(self, name), name)
        _digest(self.assignment_route_sha256, "assignment_route_sha256")
        object.__setattr__(self, "plan_day_ids", _identities(self.plan_day_ids, "assignment_plan_day_ids"))


@dataclass(frozen=True, slots=True, kw_only=True)
class DependencyEvidence:
    predecessor_task_id: str
    successor_task_id: str
    relation: str
    provenance_reference: str

    def __post_init__(self) -> None:
        for name in ("predecessor_task_id", "successor_task_id", "relation", "provenance_reference"):
            _identity(getattr(self, name), name)
        _require(self.relation == "FINISH_BEFORE_START", "dependency_relation")
        _require(self.predecessor_task_id != self.successor_task_id, "dependency_self_edge")


@dataclass(frozen=True, slots=True, kw_only=True)
class ImpactCommitmentReference:
    commitment_id: str
    job_id: str
    task_id: str

    def __post_init__(self) -> None:
        for name in ("commitment_id", "job_id", "task_id"):
            _identity(getattr(self, name), name)


@dataclass(frozen=True, slots=True, kw_only=True)
class DependencyImpactReference(ImpactCommitmentReference):
    originating_direct_task_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        ImpactCommitmentReference.__post_init__(self)
        origins = _identities(self.originating_direct_task_ids, "originating_direct_task_ids")
        _require(bool(origins), "originating_direct_task_ids")
        object.__setattr__(self, "originating_direct_task_ids", origins)


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectCurrentImpactScope:
    m3a_evaluation_id: str
    m3a_evaluation_fingerprint: str
    outcome: M3ImpactOutcome
    commitments: tuple[ImpactCommitmentReference, ...]

    def __post_init__(self) -> None:
        _identity(self.m3a_evaluation_id, "m3a_evaluation_id")
        _digest(self.m3a_evaluation_fingerprint, "m3a_evaluation_fingerprint")
        _require(type(self.outcome) is M3ImpactOutcome, "m3a_outcome")
        object.__setattr__(self, "commitments", _records(
            self.commitments, ImpactCommitmentReference,
            lambda item: (item.job_id, item.task_id, item.commitment_id), "direct_current_impact",
        ))


@dataclass(frozen=True, slots=True, kw_only=True)
class DependencyImpactScope:
    edges: tuple[DependencyEvidence, ...]
    commitments: tuple[DependencyImpactReference, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "edges", _records(
            self.edges, DependencyEvidence,
            lambda item: (item.predecessor_task_id, item.successor_task_id), "dependency_edges",
        ))
        object.__setattr__(self, "commitments", _records(
            self.commitments, DependencyImpactReference,
            lambda item: (item.job_id, item.task_id, item.commitment_id), "dependency_impact",
        ))


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateInducedImpactScope:
    status: str = CANDIDATE_ABSENCE
    candidate_fingerprint: None = None
    commitments: tuple[ImpactCommitmentReference, ...] = ()

    def __post_init__(self) -> None:
        _require(type(self.status) is str and self.status == CANDIDATE_ABSENCE, "candidate_status")
        _require(self.candidate_fingerprint is None, "candidate_fingerprint")
        _require(type(self.commitments) in (tuple, list) and not self.commitments, "candidate_commitments")
        object.__setattr__(self, "commitments", ())


@dataclass(frozen=True, slots=True, kw_only=True)
class M3EvaluationInput:
    historical_source_scope: HistoricalSourceScope
    company_plan_id: str
    base_plan_revision: int
    base_plan_revision_id: str
    base_plan_revision_fingerprint: str
    current_planning_scope_fingerprint: str
    unavailable_worker_id: str
    business_date: date
    plan_day_id: str
    plan_day_associations: tuple[PlanDayAssociationEvidence, ...]
    current_active_commitments: tuple[CurrentCommitmentEvidence, ...]
    current_m2_task_preconditions: tuple[CurrentM2TaskPrecondition, ...]
    current_m2_assignment_preconditions: tuple[CurrentM2AssignmentPrecondition, ...]
    direct_current_impact: DirectCurrentImpactScope
    dependency_impact: DependencyImpactScope
    candidate_induced_impact: CandidateInducedImpactScope
    schema_version: str = field(default=EVALUATION_INPUT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=IMPACT_ANALYSIS_RULE_VERSION, init=False)
    evaluation_input_fingerprint: str = field(init=False)
    evaluation_input_id: str = field(init=False)

    def __post_init__(self) -> None:
        _record(self.historical_source_scope, HistoricalSourceScope, "historical_source_scope")
        _require(replace(self.historical_source_scope) == self.historical_source_scope, "historical_source_scope")
        _identity(self.company_plan_id, "company_plan_id")
        _revision(self.base_plan_revision, "base_plan_revision")
        _identity(self.base_plan_revision_id, "base_plan_revision_id")
        for name in ("base_plan_revision_fingerprint", "current_planning_scope_fingerprint"):
            _digest(getattr(self, name), name)
        _require(
            self.base_plan_revision_id
            == "m3-plan-revision-" + self.base_plan_revision_fingerprint,
            "base_plan_revision_identity",
        )
        _identity(self.unavailable_worker_id, "unavailable_worker_id")
        _require(type(self.business_date) is date, "business_date")
        _identity(self.plan_day_id, "plan_day_id")
        days = _records(self.plan_day_associations, PlanDayAssociationEvidence,
                        lambda item: item.plan_day_id, "plan_day_associations")
        commitments = _records(self.current_active_commitments, CurrentCommitmentEvidence,
                               lambda item: (item.job_id, item.task_id, item.commitment_id), "current_active_commitments")
        tasks = _records(self.current_m2_task_preconditions, CurrentM2TaskPrecondition,
                         lambda item: (item.job_id, item.task_id), "current_m2_task_preconditions")
        assignments = _records(self.current_m2_assignment_preconditions, CurrentM2AssignmentPrecondition,
                               lambda item: item.assignment_id, "current_m2_assignment_preconditions")
        _record(self.direct_current_impact, DirectCurrentImpactScope, "direct_current_impact")
        _record(self.dependency_impact, DependencyImpactScope, "dependency_impact")
        _record(self.candidate_induced_impact, CandidateInducedImpactScope, "candidate_induced_impact")
        for value, name in ((self.direct_current_impact, "direct_current_impact"),
                            (self.dependency_impact, "dependency_impact"),
                            (self.candidate_induced_impact, "candidate_induced_impact")):
            _require(replace(value) == value, name)
        _require(any(item.plan_day_id == self.plan_day_id and item.worker_id == self.unavailable_worker_id
                     and item.business_date == self.business_date for item in days), "plan_day_association")
        active_keys = {(item.job_id, item.task_id) for item in commitments}
        task_keys = {(item.job_id, item.task_id) for item in tasks}
        _require(active_keys.issubset(task_keys), "current_task_preconditions")
        _require(all(item.task_status is not TaskStatus.DONE for item in tasks if (item.job_id, item.task_id) in active_keys),
                 "active_done_task")
        task_by_key = {(item.job_id, item.task_id): item for item in tasks}
        day_membership = {(item.worker_id, item.business_date) for item in days}
        assignments_by_id = {item.assignment_id: item for item in assignments}
        for commitment in commitments:
            current_task = task_by_key[(commitment.job_id, commitment.task_id)]
            _require(
                (
                    current_task.task_definition_version,
                    current_task.source_handoff_id,
                    current_task.source_revision,
                )
                == (
                    commitment.task_definition_version,
                    commitment.source_handoff_id,
                    commitment.source_revision,
                ),
                "current_task_binding",
            )
            _require(
                all((worker_id, commitment.business_date) in day_membership
                    for worker_id in commitment.planned_worker_ids),
                "current_commitment_worker_day",
            )
            for assignment_id in commitment.source_m2_assignment_ids:
                assignment = assignments_by_id.get(assignment_id)
                _require(
                    assignment is not None
                    and (assignment.job_id, assignment.task_id)
                    == (commitment.job_id, commitment.task_id),
                    "current_assignment_binding",
                )
        refs = {(item.commitment_id, item.job_id, item.task_id) for item in commitments}
        direct_refs = {(item.commitment_id, item.job_id, item.task_id) for item in self.direct_current_impact.commitments}
        dependency_refs = {(item.commitment_id, item.job_id, item.task_id) for item in self.dependency_impact.commitments}
        _require(direct_refs.issubset(refs) and dependency_refs.issubset(refs), "impact_commitments")
        _require(direct_refs.isdisjoint(dependency_refs), "impact_scope_overlap")
        _require(
            self.direct_current_impact.m3a_evaluation_id
            == "m3-impact-" + self.direct_current_impact.m3a_evaluation_fingerprint,
            "m3a_evaluation_identity",
        )
        _require(
            (bool(direct_refs) and self.direct_current_impact.outcome is M3ImpactOutcome.REPLAN_REQUIRED)
            or (
                not direct_refs
                and self.direct_current_impact.outcome
                in (M3ImpactOutcome.NO_LINKED_COMMITMENTS, M3ImpactOutcome.NO_REPLAN_REQUIRED)
            ),
            "direct_impact_outcome",
        )
        _require(
            all(self.unavailable_worker_id in commitment.planned_worker_ids
                for commitment in commitments
                if (commitment.commitment_id, commitment.job_id, commitment.task_id) in direct_refs),
            "direct_worker_binding",
        )
        direct_task_ids = {item.task_id for item in self.direct_current_impact.commitments}
        adjacency: dict[str, set[str]] = {}
        for edge in self.dependency_impact.edges:
            adjacency.setdefault(edge.predecessor_task_id, set()).add(edge.successor_task_id)
        for impacted in self.dependency_impact.commitments:
            _require(
                set(impacted.originating_direct_task_ids).issubset(direct_task_ids),
                "dependency_impact_origin",
            )
            for origin in impacted.originating_direct_task_ids:
                pending, visited = [origin], {origin}
                while pending and impacted.task_id not in visited:
                    predecessor = pending.pop(0)
                    for successor in sorted(adjacency.get(predecessor, set())):
                        if successor not in visited:
                            visited.add(successor)
                            pending.append(successor)
                _require(impacted.task_id in visited, "dependency_impact_path")
        object.__setattr__(self, "plan_day_associations", days)
        object.__setattr__(self, "current_active_commitments", commitments)
        object.__setattr__(self, "current_m2_task_preconditions", tasks)
        object.__setattr__(self, "current_m2_assignment_preconditions", assignments)
        digest = sha256_text(_semantic_json(self))
        object.__setattr__(self, "evaluation_input_fingerprint", digest)
        object.__setattr__(self, "evaluation_input_id", "m3-evaluation-input-" + digest)


_NESTED_TYPES = (
    HistoricalJobRootEvidence, HistoricalAssignmentEvidence, HistoricalSourceScope,
    PlanDayAssociationEvidence, CurrentCommitmentEvidence, CurrentM2TaskPrecondition,
    CurrentM2AssignmentPrecondition, DependencyEvidence, ImpactCommitmentReference,
    DependencyImpactReference, DirectCurrentImpactScope, DependencyImpactScope,
    CandidateInducedImpactScope,
)


def _value(value):
    if isinstance(value, StrEnum):
        return value.value
    if type(value) is date:
        return value.isoformat()
    if type(value) is tuple:
        return [_value(item) for item in value]
    if type(value) in _NESTED_TYPES:
        return {item.name: _value(getattr(value, item.name)) for item in fields(value)}
    return value


def _document(value: M3EvaluationInput, *, include_identity: bool) -> dict[str, object]:
    excluded = set() if include_identity else {"evaluation_input_fingerprint", "evaluation_input_id"}
    return {item.name: _value(getattr(value, item.name)) for item in fields(value) if item.name not in excluded}


def _semantic_json(value: M3EvaluationInput) -> str:
    return canonical_json(_document(value, include_identity=False))


def _validate(value: M3EvaluationInput) -> None:
    _record(value, M3EvaluationInput, "evaluation_input")
    for name in ("plan_day_associations", "current_active_commitments", "current_m2_task_preconditions",
                 "current_m2_assignment_preconditions"):
        _require(type(getattr(value, name)) is tuple, name)
    _digest(value.evaluation_input_fingerprint, "evaluation_input_fingerprint")
    _identity(value.evaluation_input_id, "evaluation_input_id")
    _require(replace(value) == value, "evaluation_input")


def semantic_evaluation_input_json(value: M3EvaluationInput) -> str:
    _validate(value)
    return _semantic_json(value)


def serialize_evaluation_input(value: M3EvaluationInput) -> str:
    _validate(value)
    return canonical_json(_document(value, include_identity=True))


def _from_document(document: dict[str, object]) -> M3EvaluationInput:
    historical = document["historical_source_scope"]
    _require(type(historical) is dict, "historical_source_scope")
    historical = HistoricalSourceScope(**{
        **historical,
        "assignments": tuple(HistoricalAssignmentEvidence(
            **{**item, "assignment_kind": AssignmentKind(item["assignment_kind"]),
               "task_status": TaskStatus(item["task_status"])}) for item in historical["assignments"]),
        "job_roots": tuple(HistoricalJobRootEvidence(**item) for item in historical["job_roots"]),
    })
    days = tuple(PlanDayAssociationEvidence(
        **{**item, "business_date": date.fromisoformat(item["business_date"])})
        for item in document["plan_day_associations"])
    commitments = tuple(CurrentCommitmentEvidence(
        **{**item, "business_date": date.fromisoformat(item["business_date"])})
        for item in document["current_active_commitments"])
    tasks = tuple(CurrentM2TaskPrecondition(
        **{**item, "task_status": TaskStatus(item["task_status"])})
        for item in document["current_m2_task_preconditions"])
    assignments = tuple(CurrentM2AssignmentPrecondition(**item)
                        for item in document["current_m2_assignment_preconditions"])
    direct = document["direct_current_impact"]
    direct = DirectCurrentImpactScope(
        **{**direct, "outcome": M3ImpactOutcome(direct["outcome"]),
           "commitments": tuple(ImpactCommitmentReference(**item) for item in direct["commitments"])})
    dependency = document["dependency_impact"]
    dependency = DependencyImpactScope(
        edges=tuple(DependencyEvidence(**item) for item in dependency["edges"]),
        commitments=tuple(DependencyImpactReference(**item) for item in dependency["commitments"]),
    )
    candidate = CandidateInducedImpactScope(**document["candidate_induced_impact"])
    values = {**document, "historical_source_scope": historical,
              "business_date": date.fromisoformat(document["business_date"]),
              "plan_day_associations": days, "current_active_commitments": commitments,
              "current_m2_task_preconditions": tasks,
              "current_m2_assignment_preconditions": assignments,
              "direct_current_impact": direct, "dependency_impact": dependency,
              "candidate_induced_impact": candidate}
    values.pop("schema_version")
    values.pop("rule_version")
    values.pop("evaluation_input_fingerprint", None)
    values.pop("evaluation_input_id", None)
    return M3EvaluationInput(**values)


def deserialize_evaluation_input(raw: str) -> M3EvaluationInput:
    try:
        document = json.loads(raw)
        _require(type(document) is dict and canonical_json(document) == raw, "canonical_document")
        _require(document.get("schema_version") == EVALUATION_INPUT_SCHEMA_VERSION, "schema_version")
        _require(document.get("rule_version") == IMPACT_ANALYSIS_RULE_VERSION, "rule_version")
        expected_fingerprint = document.get("evaluation_input_fingerprint")
        expected_id = document.get("evaluation_input_id")
        value = _from_document(document)
        _require(value.evaluation_input_fingerprint == expected_fingerprint, "evaluation_input_fingerprint")
        _require(value.evaluation_input_id == expected_id, "evaluation_input_id")
        _require(serialize_evaluation_input(value) == raw, "canonical_order")
        return value
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        if isinstance(error, M3EvaluationInputStorageError):
            raise
        raise M3EvaluationInputStorageError("INVALID_EVALUATION_INPUT", "evaluation_input") from error


def restore_evaluation_input(raw_semantic: str, fingerprint: str, evaluation_input_id: str) -> M3EvaluationInput:
    try:
        document = json.loads(raw_semantic)
        _require(type(document) is dict and canonical_json(document) == raw_semantic, "canonical_semantic_document")
        value = _from_document(document)
        _require(semantic_evaluation_input_json(value) == raw_semantic, "canonical_order")
        _require(value.evaluation_input_fingerprint == fingerprint, "evaluation_input_fingerprint")
        _require(value.evaluation_input_id == evaluation_input_id, "evaluation_input_id")
        return value
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        raise M3EvaluationInputStorageError("INVALID_EVALUATION_INPUT", "evaluation_input") from error
