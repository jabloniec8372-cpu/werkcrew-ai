"""Strict canonical serialization for durable M2 roots, inputs, and receipts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Callable, Mapping, TypeVar

from werkcrew_ai.field.models import (
    AssignmentKind,
    AssignmentState,
    CompletionType,
    DeliveryEvidence,
    DirectiveClass,
    DirectiveDefinition,
    DirectiveRoot,
    DirectiveType,
    EffectType,
    EndOfDayAction,
    EvidenceItem,
    EvidenceKind,
    FieldEventEnvelope,
    FieldEventInput,
    FieldEventType,
    FieldObservation,
    M2JobExecutionRoot,
    M2Policy,
    PlanDayRoot,
    PlanDayStatus,
    PolicyTimeContext,
    ProblemHint,
    ProcessedEventReceipt,
    ReductionOutcome,
    SiteProblemAction,
    StageState,
    StageStatus,
    SystemEffect,
    SystemSignal,
    SystemSignalType,
    TaskDefinition,
    TaskState,
    TaskStatus,
    WorkerIdentityRegistry,
)


class SerializationError(ValueError):
    """Stored data is non-canonical, malformed, or inconsistent."""


class RootAccess(StrEnum):
    MUTATE = "MUTATE"
    READ_AUTHORITY = "READ_AUTHORITY"


@dataclass(frozen=True, slots=True, order=True)
class RootPrecondition:
    root_kind: str
    root_id: str
    access: RootAccess
    expected_revision: int
    expected_content_sha256: str

    def __post_init__(self) -> None:
        if not self.root_kind.strip() or not self.root_id.strip():
            raise ValueError("root precondition identity must be non-blank")
        if isinstance(self.expected_revision, bool) or self.expected_revision < 0:
            raise ValueError("expected_revision must be non-negative")
        _validate_digest(self.expected_content_sha256)


@dataclass(frozen=True, slots=True, order=True)
class ImmutablePrecondition:
    record_kind: str
    record_id: str
    content_sha256: str

    def __post_init__(self) -> None:
        if not self.record_kind.strip() or not self.record_id.strip():
            raise ValueError("immutable precondition identity must be non-blank")
        _validate_digest(self.content_sha256)


@dataclass(frozen=True, slots=True)
class PreconditionVector:
    roots: tuple[RootPrecondition, ...] = ()
    immutable_records: tuple[ImmutablePrecondition, ...] = ()

    def __post_init__(self) -> None:
        roots = tuple(sorted(self.roots, key=lambda item: (item.root_kind, item.root_id)))
        immutable = tuple(
            sorted(
                self.immutable_records,
                key=lambda item: (item.record_kind, item.record_id),
            )
        )
        if len({(item.root_kind, item.root_id) for item in roots}) != len(roots):
            raise ValueError("root preconditions must be unique")
        if len({(item.record_kind, item.record_id) for item in immutable}) != len(
            immutable
        ):
            raise ValueError("immutable preconditions must be unique")
        object.__setattr__(self, "roots", roots)
        object.__setattr__(self, "immutable_records", immutable)


@dataclass(frozen=True, slots=True, order=True)
class ResultingRevision:
    root_kind: str
    root_id: str
    resulting_revision: int
    resulting_content_sha256: str

    def __post_init__(self) -> None:
        if not self.root_kind.strip() or not self.root_id.strip():
            raise ValueError("resulting revision identity must be non-blank")
        if isinstance(self.resulting_revision, bool) or self.resulting_revision < 0:
            raise ValueError("resulting_revision must be non-negative")
        _validate_digest(self.resulting_content_sha256)


@dataclass(frozen=True, slots=True)
class ResultingRevisionVector:
    roots: tuple[ResultingRevision, ...] = ()

    def __post_init__(self) -> None:
        roots = tuple(sorted(self.roots, key=lambda item: (item.root_kind, item.root_id)))
        if len({(item.root_kind, item.root_id) for item in roots}) != len(roots):
            raise ValueError("resulting revisions must be unique")
        object.__setattr__(self, "roots", roots)


@dataclass(frozen=True, slots=True)
class RoutingVector:
    job_root_ids: tuple[str, ...] = ()
    plan_day_ids: tuple[str, ...] = ()
    directive_ids: tuple[str, ...] = ()
    publication_keys: tuple[tuple[str, int, str], ...] = ()
    receipt_event_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "job_root_ids",
            "plan_day_ids",
            "directive_ids",
            "receipt_event_ids",
            "evidence_ids",
        ):
            values = tuple(sorted(getattr(self, field_name)))
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
            object.__setattr__(self, field_name, values)
        keys = tuple(sorted(self.publication_keys))
        if len(keys) != len(set(keys)):
            raise ValueError("publication_keys must be unique")
        object.__setattr__(self, "publication_keys", keys)


T = TypeVar("T")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_text(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_canonical_document(raw: str, expected_sha256: str | None = None) -> Any:
    if not isinstance(raw, str):
        raise SerializationError("canonical document must be text")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise SerializationError("canonical document is not valid JSON") from error
    try:
        encoded = canonical_json(value)
    except (TypeError, ValueError) as error:
        raise SerializationError("canonical document contains unsupported values") from error
    if encoded != raw:
        raise SerializationError("stored JSON is not canonical")
    if expected_sha256 is not None:
        _validate_digest(expected_sha256)
        if sha256_text(raw) != expected_sha256:
            raise SerializationError("stored content hash does not match")
    return value


def _validate_digest(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("SHA-256 digest must be 64 lowercase hexadecimal characters")


def _document(document_type: str, schema_version: str, payload: Any) -> str:
    return canonical_json(
        {
            "document_type": document_type,
            "payload": payload,
            "schema_version": schema_version,
        }
    )


def _payload(raw: str, document_type: str, schema_version: str) -> Mapping[str, Any]:
    value = verify_canonical_document(raw)
    mapping = _mapping(value, "document")
    _keys(mapping, {"document_type", "payload", "schema_version"}, "document")
    if mapping["document_type"] != document_type:
        raise SerializationError(f"expected document type {document_type}")
    if mapping["schema_version"] != schema_version:
        raise SerializationError(f"unsupported schema for {document_type}")
    return _mapping(mapping["payload"], "payload")


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SerializationError(f"{path} must be an object")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise SerializationError(f"{path} must be an array")
    return value


def _keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise SerializationError(f"{path} fields do not match schema")


def _string(value: Any, path: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise SerializationError(f"{path} must be text")
    return value


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SerializationError(f"{path} must be an integer")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise SerializationError(f"{path} must be a boolean")
    return value


def _datetime(value: Any, path: str, *, optional: bool = False) -> datetime | None:
    text = _string(value, path, optional=optional)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise SerializationError(f"{path} is not an ISO datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SerializationError(f"{path} must be timezone-aware")
    return parsed


def _date(value: Any, path: str) -> date:
    text = _string(value, path)
    assert text is not None
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise SerializationError(f"{path} is not an ISO date") from error


def _enum(enum_type: type[T], value: Any, path: str, *, optional: bool = False) -> T | None:
    text = _string(value, path, optional=optional)
    if text is None:
        return None
    try:
        return enum_type(text)  # type: ignore[call-arg]
    except ValueError as error:
        raise SerializationError(f"{path} has an unsupported value") from error


def _string_tuple(value: Any, path: str) -> tuple[str, ...]:
    values = _list(value, path)
    result = tuple(_string(item, f"{path}[]") for item in values)
    if any(item is None for item in result):  # pragma: no cover - defensive
        raise SerializationError(f"{path} cannot contain null")
    return result  # type: ignore[return-value]


def _evidence_payload(item: EvidenceItem) -> dict[str, Any]:
    return {
        "content_reference": item.content_reference,
        "evidence_id": item.evidence_id,
        "kind": item.kind.value,
    }


def _evidence(value: Any, path: str) -> EvidenceItem:
    data = _mapping(value, path)
    _keys(data, {"content_reference", "evidence_id", "kind"}, path)
    return EvidenceItem(
        evidence_id=_string(data["evidence_id"], f"{path}.evidence_id"),  # type: ignore[arg-type]
        kind=_enum(EvidenceKind, data["kind"], f"{path}.kind"),  # type: ignore[arg-type]
        content_reference=_string(
            data["content_reference"], f"{path}.content_reference"
        ),  # type: ignore[arg-type]
    )


def _stage_payload(item: StageState) -> dict[str, Any]:
    return {
        "completion_event_id": item.completion_event_id,
        "stage_id": item.stage_id,
        "status": item.status.value,
    }


def _stage(value: Any, path: str) -> StageState:
    data = _mapping(value, path)
    _keys(data, {"completion_event_id", "stage_id", "status"}, path)
    return StageState(
        stage_id=_string(data["stage_id"], f"{path}.stage_id"),  # type: ignore[arg-type]
        status=_enum(StageStatus, data["status"], f"{path}.status"),  # type: ignore[arg-type]
        completion_event_id=_string(
            data["completion_event_id"],
            f"{path}.completion_event_id",
            optional=True,
        ),
    )


def _task_definition_payload(item: TaskDefinition) -> dict[str, Any]:
    return {
        "assignment_kind": item.assignment_kind.value,
        "business_meaning": item.business_meaning,
        "completion_type": item.completion_type.value,
        "definition_version": item.definition_version,
        "job_id": item.job_id,
        "required_evidence": [value.value for value in item.required_evidence],
        "required_postconditions": list(item.required_postconditions),
        "requires_quantity": item.requires_quantity,
        "source_handoff_id": item.source_handoff_id,
        "source_revision": item.source_revision,
        "stage_ids": list(item.stage_ids),
        "supersedes_task_id": item.supersedes_task_id,
        "task_id": item.task_id,
    }


def _task_definition(value: Any, path: str) -> TaskDefinition:
    data = _mapping(value, path)
    expected = {
        "assignment_kind",
        "business_meaning",
        "completion_type",
        "definition_version",
        "job_id",
        "required_evidence",
        "required_postconditions",
        "requires_quantity",
        "source_handoff_id",
        "source_revision",
        "stage_ids",
        "supersedes_task_id",
        "task_id",
    }
    _keys(data, expected, path)
    return TaskDefinition(
        task_id=_string(data["task_id"], f"{path}.task_id"),  # type: ignore[arg-type]
        definition_version=_string(
            data["definition_version"], f"{path}.definition_version"
        ),  # type: ignore[arg-type]
        job_id=_string(data["job_id"], f"{path}.job_id"),  # type: ignore[arg-type]
        source_handoff_id=_string(
            data["source_handoff_id"], f"{path}.source_handoff_id"
        ),  # type: ignore[arg-type]
        source_revision=_integer(data["source_revision"], f"{path}.source_revision"),
        business_meaning=_string(
            data["business_meaning"], f"{path}.business_meaning"
        ),  # type: ignore[arg-type]
        completion_type=_enum(
            CompletionType, data["completion_type"], f"{path}.completion_type"
        ),  # type: ignore[arg-type]
        required_postconditions=_string_tuple(
            data["required_postconditions"], f"{path}.required_postconditions"
        ),
        required_evidence=tuple(
            _enum(EvidenceKind, item, f"{path}.required_evidence[]")
            for item in _list(data["required_evidence"], f"{path}.required_evidence")
        ),  # type: ignore[arg-type]
        stage_ids=_string_tuple(data["stage_ids"], f"{path}.stage_ids"),
        requires_quantity=_boolean(
            data["requires_quantity"], f"{path}.requires_quantity"
        ),
        assignment_kind=_enum(
            AssignmentKind, data["assignment_kind"], f"{path}.assignment_kind"
        ),  # type: ignore[arg-type]
        supersedes_task_id=_string(
            data["supersedes_task_id"], f"{path}.supersedes_task_id", optional=True
        ),
    )


def _observation_payload(item: FieldObservation) -> dict[str, Any]:
    return {
        "actor_id": item.actor_id,
        "event_id": item.event_id,
        "evidence": [_evidence_payload(value) for value in item.evidence],
        "interpretation": item.interpretation,
        "text": item.text,
    }


def _observation(value: Any, path: str) -> FieldObservation:
    data = _mapping(value, path)
    _keys(data, {"actor_id", "event_id", "evidence", "interpretation", "text"}, path)
    return FieldObservation(
        event_id=_string(data["event_id"], f"{path}.event_id"),  # type: ignore[arg-type]
        actor_id=_string(data["actor_id"], f"{path}.actor_id"),  # type: ignore[arg-type]
        text=_string(data["text"], f"{path}.text", optional=True),
        evidence=tuple(
            _evidence(item, f"{path}.evidence[]")
            for item in _list(data["evidence"], f"{path}.evidence")
        ),
        interpretation=_string(
            data["interpretation"], f"{path}.interpretation", optional=True
        ),
    )


def _task_payload(item: TaskState) -> dict[str, Any]:
    return {
        "blocked_pending_resolution": item.blocked_pending_resolution,
        "completion_event_id": item.completion_event_id,
        "definition": _task_definition_payload(item.definition),
        "evidence": [_evidence_payload(value) for value in item.evidence],
        "execution_authorized": item.execution_authorized,
        "observations": [_observation_payload(value) for value in item.observations],
        "safe_hold_directive_id": item.safe_hold_directive_id,
        "stages": [_stage_payload(value) for value in item.stages],
        "status": item.status.value,
    }


def _task(value: Any, path: str) -> TaskState:
    data = _mapping(value, path)
    expected = {
        "blocked_pending_resolution",
        "completion_event_id",
        "definition",
        "evidence",
        "execution_authorized",
        "observations",
        "safe_hold_directive_id",
        "stages",
        "status",
    }
    _keys(data, expected, path)
    return TaskState(
        definition=_task_definition(data["definition"], f"{path}.definition"),
        status=_enum(TaskStatus, data["status"], f"{path}.status"),  # type: ignore[arg-type]
        stages=tuple(
            _stage(item, f"{path}.stages[]")
            for item in _list(data["stages"], f"{path}.stages")
        ),
        evidence=tuple(
            _evidence(item, f"{path}.evidence[]")
            for item in _list(data["evidence"], f"{path}.evidence")
        ),
        observations=tuple(
            _observation(item, f"{path}.observations[]")
            for item in _list(data["observations"], f"{path}.observations")
        ),
        completion_event_id=_string(
            data["completion_event_id"], f"{path}.completion_event_id", optional=True
        ),
        safe_hold_directive_id=_string(
            data["safe_hold_directive_id"],
            f"{path}.safe_hold_directive_id",
            optional=True,
        ),
        blocked_pending_resolution=_boolean(
            data["blocked_pending_resolution"],
            f"{path}.blocked_pending_resolution",
        ),
        execution_authorized=_boolean(
            data["execution_authorized"], f"{path}.execution_authorized"
        ),
    )


def _assignment_payload(item: AssignmentState) -> dict[str, Any]:
    return {
        "assignment_id": item.assignment_id,
        "job_id": item.job_id,
        "kind": item.kind.value,
        "lead_worker_id": item.lead_worker_id,
        "member_worker_ids": list(item.member_worker_ids),
        "plan_day_ids": list(item.plan_day_ids),
        "released_worker_ids": list(item.released_worker_ids),
        "supersedes_assignment_id": item.supersedes_assignment_id,
        "task_definition_version": item.task_definition_version,
        "task_id": item.task_id,
    }


def _assignment(value: Any, path: str) -> AssignmentState:
    data = _mapping(value, path)
    expected = {
        "assignment_id",
        "job_id",
        "kind",
        "lead_worker_id",
        "member_worker_ids",
        "plan_day_ids",
        "released_worker_ids",
        "supersedes_assignment_id",
        "task_definition_version",
        "task_id",
    }
    _keys(data, expected, path)
    return AssignmentState(
        assignment_id=_string(data["assignment_id"], f"{path}.assignment_id"),  # type: ignore[arg-type]
        job_id=_string(data["job_id"], f"{path}.job_id"),  # type: ignore[arg-type]
        task_id=_string(data["task_id"], f"{path}.task_id"),  # type: ignore[arg-type]
        task_definition_version=_string(
            data["task_definition_version"], f"{path}.task_definition_version"
        ),  # type: ignore[arg-type]
        kind=_enum(AssignmentKind, data["kind"], f"{path}.kind"),  # type: ignore[arg-type]
        member_worker_ids=_string_tuple(
            data["member_worker_ids"], f"{path}.member_worker_ids"
        ),
        plan_day_ids=_string_tuple(data["plan_day_ids"], f"{path}.plan_day_ids"),
        lead_worker_id=_string(
            data["lead_worker_id"], f"{path}.lead_worker_id", optional=True
        ),
        released_worker_ids=_string_tuple(
            data["released_worker_ids"], f"{path}.released_worker_ids"
        ),
        supersedes_assignment_id=_string(
            data["supersedes_assignment_id"],
            f"{path}.supersedes_assignment_id",
            optional=True,
        ),
    )


def serialize_job_execution_root(value: M2JobExecutionRoot) -> str:
    return _document(
        "M2JobExecutionRoot",
        "m2-job-execution-root-v1",
        {
            "assignments": [_assignment_payload(item) for item in value.assignments],
            "job_execution_revision": value.job_execution_revision,
            "job_id": value.job_id,
            "tasks": [_task_payload(item) for item in value.tasks],
        },
    )


def deserialize_job_execution_root(raw: str) -> M2JobExecutionRoot:
    data = _payload(raw, "M2JobExecutionRoot", "m2-job-execution-root-v1")
    _keys(data, {"assignments", "job_execution_revision", "job_id", "tasks"}, "payload")
    result = M2JobExecutionRoot(
        job_id=_string(data["job_id"], "payload.job_id"),  # type: ignore[arg-type]
        tasks=tuple(
            _task(item, "payload.tasks[]")
            for item in _list(data["tasks"], "payload.tasks")
        ),
        assignments=tuple(
            _assignment(item, "payload.assignments[]")
            for item in _list(data["assignments"], "payload.assignments")
        ),
        job_execution_revision=_integer(
            data["job_execution_revision"], "payload.job_execution_revision"
        ),
    )
    if serialize_job_execution_root(result) != raw:
        raise SerializationError("job execution root does not round-trip canonically")
    return result


def serialize_plan_day_root(value: PlanDayRoot) -> str:
    return _document(
        "PlanDayRoot",
        "m2-plan-day-root-v1",
        {
            "business_date": value.business_date.isoformat(),
            "confirmed_plan_reference": value.confirmed_plan_reference,
            "day_close_reported": value.day_close_reported,
            "plan_day_id": value.plan_day_id,
            "plan_day_revision": value.plan_day_revision,
            "start_at": value.start_at.isoformat(),
            "start_unknown_escalated": value.start_unknown_escalated,
            "status": value.status.value,
            "worker_available": value.worker_available,
            "worker_id": value.worker_id,
        },
    )


def deserialize_plan_day_root(raw: str) -> PlanDayRoot:
    data = _payload(raw, "PlanDayRoot", "m2-plan-day-root-v1")
    expected = {
        "business_date",
        "confirmed_plan_reference",
        "day_close_reported",
        "plan_day_id",
        "plan_day_revision",
        "start_at",
        "start_unknown_escalated",
        "status",
        "worker_available",
        "worker_id",
    }
    _keys(data, expected, "payload")
    result = PlanDayRoot(
        plan_day_id=_string(data["plan_day_id"], "payload.plan_day_id"),  # type: ignore[arg-type]
        worker_id=_string(data["worker_id"], "payload.worker_id"),  # type: ignore[arg-type]
        business_date=_date(data["business_date"], "payload.business_date"),
        start_at=_datetime(data["start_at"], "payload.start_at"),  # type: ignore[arg-type]
        status=_enum(PlanDayStatus, data["status"], "payload.status"),  # type: ignore[arg-type]
        worker_available=_boolean(data["worker_available"], "payload.worker_available"),
        day_close_reported=_boolean(
            data["day_close_reported"], "payload.day_close_reported"
        ),
        start_unknown_escalated=_boolean(
            data["start_unknown_escalated"], "payload.start_unknown_escalated"
        ),
        confirmed_plan_reference=_string(
            data["confirmed_plan_reference"],
            "payload.confirmed_plan_reference",
            optional=True,
        ),
        plan_day_revision=_integer(
            data["plan_day_revision"], "payload.plan_day_revision"
        ),
    )
    if serialize_plan_day_root(result) != raw:
        raise SerializationError("plan day root does not round-trip canonically")
    return result


def _directive_definition_payload(value: DirectiveDefinition) -> dict[str, Any]:
    return {
        "assignment_id": value.assignment_id,
        "directive_class": value.directive_class.value,
        "directive_id": value.directive_id,
        "directive_type": value.directive_type.value,
        "escalation_due_at": (
            value.escalation_due_at.isoformat()
            if value.escalation_due_at is not None
            else None
        ),
        "issuance_sequence": value.issuance_sequence,
        "issued_at": value.issued_at.isoformat(),
        "job_id": value.job_id,
        "plan_day_id": value.plan_day_id,
        "proposed_plan_reference": value.proposed_plan_reference,
        "supersedes_directive_id": value.supersedes_directive_id,
        "task_id": value.task_id,
        "worker_id": value.worker_id,
    }


def _directive_definition(value: Any, path: str) -> DirectiveDefinition:
    data = _mapping(value, path)
    expected = {
        "assignment_id",
        "directive_class",
        "directive_id",
        "directive_type",
        "escalation_due_at",
        "issuance_sequence",
        "issued_at",
        "job_id",
        "plan_day_id",
        "proposed_plan_reference",
        "supersedes_directive_id",
        "task_id",
        "worker_id",
    }
    _keys(data, expected, path)
    return DirectiveDefinition(
        directive_id=_string(data["directive_id"], f"{path}.directive_id"),  # type: ignore[arg-type]
        directive_type=_enum(
            DirectiveType, data["directive_type"], f"{path}.directive_type"
        ),  # type: ignore[arg-type]
        directive_class=_enum(
            DirectiveClass, data["directive_class"], f"{path}.directive_class"
        ),  # type: ignore[arg-type]
        worker_id=_string(data["worker_id"], f"{path}.worker_id"),  # type: ignore[arg-type]
        issued_at=_datetime(data["issued_at"], f"{path}.issued_at"),  # type: ignore[arg-type]
        escalation_due_at=_datetime(
            data["escalation_due_at"], f"{path}.escalation_due_at", optional=True
        ),
        job_id=_string(data["job_id"], f"{path}.job_id", optional=True),
        task_id=_string(data["task_id"], f"{path}.task_id", optional=True),
        assignment_id=_string(
            data["assignment_id"], f"{path}.assignment_id", optional=True
        ),
        plan_day_id=_string(
            data["plan_day_id"], f"{path}.plan_day_id", optional=True
        ),
        proposed_plan_reference=_string(
            data["proposed_plan_reference"],
            f"{path}.proposed_plan_reference",
            optional=True,
        ),
        issuance_sequence=_integer(
            data["issuance_sequence"], f"{path}.issuance_sequence"
        ),
        supersedes_directive_id=_string(
            data["supersedes_directive_id"],
            f"{path}.supersedes_directive_id",
            optional=True,
        ),
    )


def serialize_directive_root(value: DirectiveRoot) -> str:
    return _document(
        "DirectiveRoot",
        "m2-directive-root-v1",
        {
            "acknowledged_event_id": value.acknowledged_event_id,
            "definition": _directive_definition_payload(value.definition),
            "delivery_evidence": value.delivery_evidence.value,
            "directive_revision": value.directive_revision,
            "e1_escalated": value.e1_escalated,
            "exception_event_id": value.exception_event_id,
            "stop_in_force": value.stop_in_force,
        },
    )


def deserialize_directive_root(raw: str) -> DirectiveRoot:
    data = _payload(raw, "DirectiveRoot", "m2-directive-root-v1")
    expected = {
        "acknowledged_event_id",
        "definition",
        "delivery_evidence",
        "directive_revision",
        "e1_escalated",
        "exception_event_id",
        "stop_in_force",
    }
    _keys(data, expected, "payload")
    result = DirectiveRoot(
        definition=_directive_definition(data["definition"], "payload.definition"),
        delivery_evidence=_enum(
            DeliveryEvidence, data["delivery_evidence"], "payload.delivery_evidence"
        ),  # type: ignore[arg-type]
        acknowledged_event_id=_string(
            data["acknowledged_event_id"],
            "payload.acknowledged_event_id",
            optional=True,
        ),
        exception_event_id=_string(
            data["exception_event_id"], "payload.exception_event_id", optional=True
        ),
        e1_escalated=_boolean(data["e1_escalated"], "payload.e1_escalated"),
        stop_in_force=_boolean(data["stop_in_force"], "payload.stop_in_force"),
        directive_revision=_integer(
            data["directive_revision"], "payload.directive_revision"
        ),
    )
    if serialize_directive_root(result) != raw:
        raise SerializationError("directive root does not round-trip canonically")
    return result


def serialize_worker_registry(value: WorkerIdentityRegistry) -> str:
    return _document(
        "WorkerIdentityRegistry",
        "m2-worker-registry-v1",
        {
            "registry_revision": value.registry_revision,
            "worker_ids": list(value.worker_ids),
        },
    )


def deserialize_worker_registry(raw: str) -> WorkerIdentityRegistry:
    data = _payload(raw, "WorkerIdentityRegistry", "m2-worker-registry-v1")
    _keys(data, {"registry_revision", "worker_ids"}, "payload")
    result = WorkerIdentityRegistry(
        worker_ids=_string_tuple(data["worker_ids"], "payload.worker_ids"),
        registry_revision=_integer(
            data["registry_revision"], "payload.registry_revision"
        ),
    )
    if serialize_worker_registry(result) != raw:
        raise SerializationError("worker registry does not round-trip canonically")
    return result


_EVENT_FIELDS = {
    "actor_id",
    "against_event_id",
    "assignment_id",
    "attachments",
    "client_context",
    "eta",
    "event_id",
    "event_type",
    "job_id",
    "occurred_at",
    "offline_origin",
    "plan_day_id",
    "quantity",
    "reason_class",
    "satisfied_postconditions",
    "schema_version",
    "severity_hint",
    "stage_id",
    "task_id",
    "text",
    "unit",
    "wait_condition",
    "wait_until",
    "directive_id",
}


def _event_payload(value: FieldEventEnvelope) -> dict[str, Any]:
    return {
        "actor_id": value.actor_id,
        "against_event_id": value.against_event_id,
        "assignment_id": value.assignment_id,
        "attachments": [_evidence_payload(item) for item in value.attachments],
        "client_context": [list(item) for item in value.client_context],
        "directive_id": value.directive_id,
        "eta": value.eta,
        "event_id": value.event_id,
        "event_type": value.event_type.value,
        "job_id": value.job_id,
        "occurred_at": value.occurred_at.isoformat(),
        "offline_origin": value.offline_origin,
        "plan_day_id": value.plan_day_id,
        "quantity": value.quantity,
        "reason_class": value.reason_class,
        "satisfied_postconditions": list(value.satisfied_postconditions),
        "schema_version": value.schema_version,
        "severity_hint": value.severity_hint.value if value.severity_hint else None,
        "stage_id": value.stage_id,
        "task_id": value.task_id,
        "text": value.text,
        "unit": value.unit,
        "wait_condition": value.wait_condition,
        "wait_until": value.wait_until.isoformat() if value.wait_until else None,
    }


def _event(value: Any, path: str) -> FieldEventEnvelope:
    data = _mapping(value, path)
    _keys(data, _EVENT_FIELDS, path)
    context_values = _list(data["client_context"], f"{path}.client_context")
    context: list[tuple[str, str]] = []
    for item in context_values:
        pair = _list(item, f"{path}.client_context[]")
        if len(pair) != 2:
            raise SerializationError("client context entries must be pairs")
        key = _string(pair[0], f"{path}.client_context[].key")
        value_text = _string(pair[1], f"{path}.client_context[].value")
        assert key is not None and value_text is not None
        context.append((key, value_text))
    return FieldEventEnvelope(
        event_id=_string(data["event_id"], f"{path}.event_id"),  # type: ignore[arg-type]
        schema_version=_integer(data["schema_version"], f"{path}.schema_version"),
        event_type=_enum(
            FieldEventType, data["event_type"], f"{path}.event_type"
        ),  # type: ignore[arg-type]
        actor_id=_string(data["actor_id"], f"{path}.actor_id"),  # type: ignore[arg-type]
        occurred_at=_datetime(data["occurred_at"], f"{path}.occurred_at"),  # type: ignore[arg-type]
        plan_day_id=_string(
            data["plan_day_id"], f"{path}.plan_day_id", optional=True
        ),
        job_id=_string(data["job_id"], f"{path}.job_id", optional=True),
        task_id=_string(data["task_id"], f"{path}.task_id", optional=True),
        assignment_id=_string(
            data["assignment_id"], f"{path}.assignment_id", optional=True
        ),
        stage_id=_string(data["stage_id"], f"{path}.stage_id", optional=True),
        directive_id=_string(
            data["directive_id"], f"{path}.directive_id", optional=True
        ),
        against_event_id=_string(
            data["against_event_id"], f"{path}.against_event_id", optional=True
        ),
        reason_class=_string(
            data["reason_class"], f"{path}.reason_class", optional=True
        ),
        eta=_string(data["eta"], f"{path}.eta", optional=True),
        text=_string(data["text"], f"{path}.text", optional=True),
        wait_condition=_string(
            data["wait_condition"], f"{path}.wait_condition", optional=True
        ),
        wait_until=_datetime(
            data["wait_until"], f"{path}.wait_until", optional=True
        ),
        quantity=_string(data["quantity"], f"{path}.quantity", optional=True),
        unit=_string(data["unit"], f"{path}.unit", optional=True),
        severity_hint=_enum(
            ProblemHint, data["severity_hint"], f"{path}.severity_hint", optional=True
        ),
        attachments=tuple(
            _evidence(item, f"{path}.attachments[]")
            for item in _list(data["attachments"], f"{path}.attachments")
        ),
        satisfied_postconditions=_string_tuple(
            data["satisfied_postconditions"], f"{path}.satisfied_postconditions"
        ),
        offline_origin=_boolean(
            data["offline_origin"], f"{path}.offline_origin"
        ),
        client_context=tuple(context),
    )


def serialize_field_event_envelope(value: FieldEventEnvelope) -> str:
    return _document(
        "FieldEventEnvelope", "m2-field-event-envelope-v1", _event_payload(value)
    )


def deserialize_field_event_envelope(raw: str) -> FieldEventEnvelope:
    result = _event(
        _payload(raw, "FieldEventEnvelope", "m2-field-event-envelope-v1"),
        "payload",
    )
    if serialize_field_event_envelope(result) != raw:
        raise SerializationError("field event does not round-trip canonically")
    return result


def serialize_field_event_input(value: FieldEventInput) -> str:
    return _document(
        "FieldEventInput",
        "m2-field-event-input-v1",
        {
            "event": _event_payload(value.event),
            "server_event_id": value.server_event_id,
        },
    )


def deserialize_field_event_input(raw: str) -> FieldEventInput:
    data = _payload(raw, "FieldEventInput", "m2-field-event-input-v1")
    _keys(data, {"event", "server_event_id"}, "payload")
    result = FieldEventInput(
        event=_event(data["event"], "payload.event"),
        server_event_id=_string(
            data["server_event_id"], "payload.server_event_id"
        ),  # type: ignore[arg-type]
    )
    if serialize_field_event_input(result) != raw:
        raise SerializationError("field input does not round-trip canonically")
    return result


def _signal_payload(value: SystemSignal) -> dict[str, Any]:
    return {
        "directive_id": value.directive_id,
        "plan_day_id": value.plan_day_id,
        "policy_result": value.policy_result.value if value.policy_result else None,
        "signal_id": value.signal_id,
        "signal_type": value.signal_type.value,
        "task_id": value.task_id,
    }


def serialize_system_signal(value: SystemSignal) -> str:
    return _document("SystemSignal", "m2-system-signal-v1", _signal_payload(value))


def deserialize_system_signal(raw: str) -> SystemSignal:
    data = _payload(raw, "SystemSignal", "m2-system-signal-v1")
    _keys(
        data,
        {"directive_id", "plan_day_id", "policy_result", "signal_id", "signal_type", "task_id"},
        "payload",
    )
    result = SystemSignal(
        signal_id=_string(data["signal_id"], "payload.signal_id"),  # type: ignore[arg-type]
        signal_type=_enum(
            SystemSignalType, data["signal_type"], "payload.signal_type"
        ),  # type: ignore[arg-type]
        plan_day_id=_string(data["plan_day_id"], "payload.plan_day_id", optional=True),
        directive_id=_string(
            data["directive_id"], "payload.directive_id", optional=True
        ),
        task_id=_string(data["task_id"], "payload.task_id", optional=True),
        policy_result=_enum(
            EndOfDayAction,
            data["policy_result"],
            "payload.policy_result",
            optional=True,
        ),
    )
    if serialize_system_signal(result) != raw:
        raise SerializationError("system signal does not round-trip canonically")
    return result


def _policy_payload(value: M2Policy) -> dict[str, Any]:
    return {
        "early_finish_options": [item.value for item in value.early_finish_options],
        "end_of_day_action": value.end_of_day_action.value,
        "site_problem_action": value.site_problem_action.value,
        "start_unknown_escalation_enabled": value.start_unknown_escalation_enabled,
    }


def serialize_policy_time_context(value: PolicyTimeContext) -> str:
    return _document(
        "PolicyTimeContext",
        "m2-policy-time-context-v1",
        {
            "now": value.now.isoformat(),
            "policy": _policy_payload(value.policy),
            "rule_version": value.rule_version,
        },
    )


def deserialize_policy_time_context(raw: str) -> PolicyTimeContext:
    data = _payload(raw, "PolicyTimeContext", "m2-policy-time-context-v1")
    _keys(data, {"now", "policy", "rule_version"}, "payload")
    policy_data = _mapping(data["policy"], "payload.policy")
    _keys(
        policy_data,
        {
            "early_finish_options",
            "end_of_day_action",
            "site_problem_action",
            "start_unknown_escalation_enabled",
        },
        "payload.policy",
    )
    policy = M2Policy(
        start_unknown_escalation_enabled=_boolean(
            policy_data["start_unknown_escalation_enabled"],
            "payload.policy.start_unknown_escalation_enabled",
        ),
        site_problem_action=_enum(
            SiteProblemAction,
            policy_data["site_problem_action"],
            "payload.policy.site_problem_action",
        ),  # type: ignore[arg-type]
        early_finish_options=tuple(
            _enum(EndOfDayAction, item, "payload.policy.early_finish_options[]")
            for item in _list(
                policy_data["early_finish_options"],
                "payload.policy.early_finish_options",
            )
        ),  # type: ignore[arg-type]
        end_of_day_action=_enum(
            EndOfDayAction,
            policy_data["end_of_day_action"],
            "payload.policy.end_of_day_action",
        ),  # type: ignore[arg-type]
    )
    result = PolicyTimeContext(
        now=_datetime(data["now"], "payload.now"),  # type: ignore[arg-type]
        policy=policy,
        rule_version=_string(data["rule_version"], "payload.rule_version"),  # type: ignore[arg-type]
    )
    if serialize_policy_time_context(result) != raw:
        raise SerializationError("policy context does not round-trip canonically")
    return result


def _effect_payload(value: SystemEffect) -> dict[str, Any]:
    return {
        "actor_id": value.actor_id,
        "assignment_id": value.assignment_id,
        "details": [list(item) for item in value.details],
        "directive_id": value.directive_id,
        "effect_type": value.effect_type.value,
        "job_id": value.job_id,
        "plan_day_id": value.plan_day_id,
        "source_id": value.source_id,
        "stage_id": value.stage_id,
        "task_id": value.task_id,
    }


def _effect(value: Any, path: str) -> SystemEffect:
    data = _mapping(value, path)
    expected = {
        "actor_id",
        "assignment_id",
        "details",
        "directive_id",
        "effect_type",
        "job_id",
        "plan_day_id",
        "source_id",
        "stage_id",
        "task_id",
    }
    _keys(data, expected, path)
    details: list[tuple[str, str]] = []
    for item in _list(data["details"], f"{path}.details"):
        pair = _list(item, f"{path}.details[]")
        if len(pair) != 2:
            raise SerializationError("effect details must be key/value pairs")
        key = _string(pair[0], f"{path}.details[].key")
        text = _string(pair[1], f"{path}.details[].value")
        assert key is not None and text is not None
        details.append((key, text))
    return SystemEffect(
        effect_type=_enum(
            EffectType, data["effect_type"], f"{path}.effect_type"
        ),  # type: ignore[arg-type]
        source_id=_string(data["source_id"], f"{path}.source_id"),  # type: ignore[arg-type]
        plan_day_id=_string(
            data["plan_day_id"], f"{path}.plan_day_id", optional=True
        ),
        job_id=_string(data["job_id"], f"{path}.job_id", optional=True),
        task_id=_string(data["task_id"], f"{path}.task_id", optional=True),
        assignment_id=_string(
            data["assignment_id"], f"{path}.assignment_id", optional=True
        ),
        stage_id=_string(data["stage_id"], f"{path}.stage_id", optional=True),
        directive_id=_string(
            data["directive_id"], f"{path}.directive_id", optional=True
        ),
        actor_id=_string(data["actor_id"], f"{path}.actor_id", optional=True),
        details=tuple(details),
    )


def serialize_effect(value: SystemEffect) -> str:
    return _document("SystemEffect", "m2-system-effect-v1", _effect_payload(value))


def deserialize_effect(raw: str) -> SystemEffect:
    result = _effect(
        _payload(raw, "SystemEffect", "m2-system-effect-v1"), "payload"
    )
    if serialize_effect(result) != raw:
        raise SerializationError("effect does not round-trip canonically")
    return result


def serialize_effects(values: tuple[SystemEffect, ...]) -> str:
    return _document(
        "SystemEffectList",
        "m2-system-effect-list-v1",
        {"effects": [_effect_payload(item) for item in values]},
    )


def deserialize_effects(raw: str) -> tuple[SystemEffect, ...]:
    data = _payload(raw, "SystemEffectList", "m2-system-effect-list-v1")
    _keys(data, {"effects"}, "payload")
    result = tuple(
        _effect(item, "payload.effects[]")
        for item in _list(data["effects"], "payload.effects")
    )
    if serialize_effects(result) != raw:
        raise SerializationError("effect list does not round-trip canonically")
    return result


def serialize_string_tuple(values: tuple[str, ...], document_type: str) -> str:
    return _document(
        document_type,
        "m2-string-list-v1",
        {"values": list(values)},
    )


def deserialize_string_tuple(raw: str, document_type: str) -> tuple[str, ...]:
    data = _payload(raw, document_type, "m2-string-list-v1")
    _keys(data, {"values"}, "payload")
    result = _string_tuple(data["values"], "payload.values")
    if serialize_string_tuple(result, document_type) != raw:
        raise SerializationError("string list does not round-trip canonically")
    return result


def serialize_processed_event_receipt(value: ProcessedEventReceipt) -> str:
    return _document(
        "ProcessedEventReceipt",
        "m2-processed-event-receipt-v1",
        {
            "event": _event_payload(value.event),
            "missing_requirements": list(value.missing_requirements),
            "outcome": value.outcome.value,
            "reason_codes": list(value.reason_codes),
            "response_effects": [
                _effect_payload(item) for item in value.response_effects
            ],
            "server_event_id": value.server_event_id,
        },
    )


def deserialize_processed_event_receipt(raw: str) -> ProcessedEventReceipt:
    data = _payload(
        raw, "ProcessedEventReceipt", "m2-processed-event-receipt-v1"
    )
    expected = {
        "event",
        "missing_requirements",
        "outcome",
        "reason_codes",
        "response_effects",
        "server_event_id",
    }
    _keys(data, expected, "payload")
    result = ProcessedEventReceipt(
        event=_event(data["event"], "payload.event"),
        server_event_id=_string(
            data["server_event_id"], "payload.server_event_id"
        ),  # type: ignore[arg-type]
        outcome=_enum(ReductionOutcome, data["outcome"], "payload.outcome"),  # type: ignore[arg-type]
        response_effects=tuple(
            _effect(item, "payload.response_effects[]")
            for item in _list(data["response_effects"], "payload.response_effects")
        ),
        missing_requirements=_string_tuple(
            data["missing_requirements"], "payload.missing_requirements"
        ),
        reason_codes=_string_tuple(data["reason_codes"], "payload.reason_codes"),
    )
    if serialize_processed_event_receipt(result) != raw:
        raise SerializationError("receipt does not round-trip canonically")
    return result


def serialize_routing_vector(value: RoutingVector) -> str:
    return _document(
        "RoutingVector",
        "m2-routing-vector-v1",
        {
            "directive_ids": list(value.directive_ids),
            "evidence_ids": list(value.evidence_ids),
            "job_root_ids": list(value.job_root_ids),
            "plan_day_ids": list(value.plan_day_ids),
            "publication_keys": [list(item) for item in value.publication_keys],
            "receipt_event_ids": list(value.receipt_event_ids),
        },
    )


def deserialize_routing_vector(raw: str) -> RoutingVector:
    data = _payload(raw, "RoutingVector", "m2-routing-vector-v1")
    expected = {
        "directive_ids",
        "evidence_ids",
        "job_root_ids",
        "plan_day_ids",
        "publication_keys",
        "receipt_event_ids",
    }
    _keys(data, expected, "payload")
    publication_keys: list[tuple[str, int, str]] = []
    for item in _list(data["publication_keys"], "payload.publication_keys"):
        values = _list(item, "payload.publication_keys[]")
        if len(values) != 3:
            raise SerializationError("publication keys must have three values")
        job_id = _string(values[0], "publication.job_id")
        revision = _integer(values[1], "publication.source_revision")
        handoff_id = _string(values[2], "publication.handoff_id")
        assert job_id is not None and handoff_id is not None
        publication_keys.append((job_id, revision, handoff_id))
    result = RoutingVector(
        job_root_ids=_string_tuple(data["job_root_ids"], "payload.job_root_ids"),
        plan_day_ids=_string_tuple(data["plan_day_ids"], "payload.plan_day_ids"),
        directive_ids=_string_tuple(data["directive_ids"], "payload.directive_ids"),
        publication_keys=tuple(publication_keys),
        receipt_event_ids=_string_tuple(
            data["receipt_event_ids"], "payload.receipt_event_ids"
        ),
        evidence_ids=_string_tuple(data["evidence_ids"], "payload.evidence_ids"),
    )
    if serialize_routing_vector(result) != raw:
        raise SerializationError("routing vector does not round-trip canonically")
    return result


def serialize_precondition_vector(value: PreconditionVector) -> str:
    return _document(
        "PreconditionVector",
        "m2-precondition-vector-v1",
        {
            "immutable_records": [
                {
                    "content_sha256": item.content_sha256,
                    "record_id": item.record_id,
                    "record_kind": item.record_kind,
                }
                for item in value.immutable_records
            ],
            "roots": [
                {
                    "access": item.access.value,
                    "expected_content_sha256": item.expected_content_sha256,
                    "expected_revision": item.expected_revision,
                    "root_id": item.root_id,
                    "root_kind": item.root_kind,
                }
                for item in value.roots
            ],
        },
    )


def deserialize_precondition_vector(raw: str) -> PreconditionVector:
    data = _payload(raw, "PreconditionVector", "m2-precondition-vector-v1")
    _keys(data, {"immutable_records", "roots"}, "payload")
    roots: list[RootPrecondition] = []
    for value in _list(data["roots"], "payload.roots"):
        item = _mapping(value, "payload.roots[]")
        _keys(
            item,
            {
                "access",
                "expected_content_sha256",
                "expected_revision",
                "root_id",
                "root_kind",
            },
            "payload.roots[]",
        )
        roots.append(
            RootPrecondition(
                root_kind=_string(item["root_kind"], "root_kind"),  # type: ignore[arg-type]
                root_id=_string(item["root_id"], "root_id"),  # type: ignore[arg-type]
                access=_enum(RootAccess, item["access"], "access"),  # type: ignore[arg-type]
                expected_revision=_integer(
                    item["expected_revision"], "expected_revision"
                ),
                expected_content_sha256=_string(
                    item["expected_content_sha256"], "expected_content_sha256"
                ),  # type: ignore[arg-type]
            )
        )
    immutable: list[ImmutablePrecondition] = []
    for value in _list(data["immutable_records"], "payload.immutable_records"):
        item = _mapping(value, "payload.immutable_records[]")
        _keys(item, {"content_sha256", "record_id", "record_kind"}, "immutable")
        immutable.append(
            ImmutablePrecondition(
                record_kind=_string(item["record_kind"], "record_kind"),  # type: ignore[arg-type]
                record_id=_string(item["record_id"], "record_id"),  # type: ignore[arg-type]
                content_sha256=_string(item["content_sha256"], "content_sha256"),  # type: ignore[arg-type]
            )
        )
    result = PreconditionVector(tuple(roots), tuple(immutable))
    if serialize_precondition_vector(result) != raw:
        raise SerializationError("precondition vector does not round-trip canonically")
    return result


def serialize_resulting_revision_vector(value: ResultingRevisionVector) -> str:
    return _document(
        "ResultingRevisionVector",
        "m2-resulting-revision-vector-v1",
        {
            "roots": [
                {
                    "resulting_content_sha256": item.resulting_content_sha256,
                    "resulting_revision": item.resulting_revision,
                    "root_id": item.root_id,
                    "root_kind": item.root_kind,
                }
                for item in value.roots
            ]
        },
    )


def deserialize_resulting_revision_vector(raw: str) -> ResultingRevisionVector:
    data = _payload(
        raw, "ResultingRevisionVector", "m2-resulting-revision-vector-v1"
    )
    _keys(data, {"roots"}, "payload")
    roots: list[ResultingRevision] = []
    for value in _list(data["roots"], "payload.roots"):
        item = _mapping(value, "payload.roots[]")
        _keys(
            item,
            {
                "resulting_content_sha256",
                "resulting_revision",
                "root_id",
                "root_kind",
            },
            "payload.roots[]",
        )
        roots.append(
            ResultingRevision(
                root_kind=_string(item["root_kind"], "root_kind"),  # type: ignore[arg-type]
                root_id=_string(item["root_id"], "root_id"),  # type: ignore[arg-type]
                resulting_revision=_integer(
                    item["resulting_revision"], "resulting_revision"
                ),
                resulting_content_sha256=_string(
                    item["resulting_content_sha256"],
                    "resulting_content_sha256",
                ),  # type: ignore[arg-type]
            )
        )
    result = ResultingRevisionVector(tuple(roots))
    if serialize_resulting_revision_vector(result) != raw:
        raise SerializationError("resulting vector does not round-trip canonically")
    return result


def canonical_document_hash(serializer: Callable[[T], str], value: T) -> tuple[str, str]:
    raw = serializer(value)
    return raw, sha256_text(raw)


__all__ = [
    "ImmutablePrecondition",
    "PreconditionVector",
    "ResultingRevision",
    "ResultingRevisionVector",
    "RootAccess",
    "RootPrecondition",
    "RoutingVector",
    "SerializationError",
    "canonical_document_hash",
    "canonical_json",
    "deserialize_directive_root",
    "deserialize_effect",
    "deserialize_effects",
    "deserialize_field_event_envelope",
    "deserialize_field_event_input",
    "deserialize_job_execution_root",
    "deserialize_plan_day_root",
    "deserialize_policy_time_context",
    "deserialize_precondition_vector",
    "deserialize_processed_event_receipt",
    "deserialize_resulting_revision_vector",
    "deserialize_routing_vector",
    "deserialize_string_tuple",
    "deserialize_system_signal",
    "deserialize_worker_registry",
    "serialize_directive_root",
    "serialize_effect",
    "serialize_effects",
    "serialize_field_event_envelope",
    "serialize_field_event_input",
    "serialize_job_execution_root",
    "serialize_plan_day_root",
    "serialize_policy_time_context",
    "serialize_precondition_vector",
    "serialize_processed_event_receipt",
    "serialize_resulting_revision_vector",
    "serialize_routing_vector",
    "serialize_string_tuple",
    "serialize_system_signal",
    "serialize_worker_registry",
    "sha256_text",
    "verify_canonical_document",
]
