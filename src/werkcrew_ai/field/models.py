"""Immutable models for the canonical deterministic M2 reducer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from werkcrew_ai.intake import M1HandoffPublication


M2_REDUCER_RULE_VERSION = "m2-reducer-v1"


def _nonblank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-blank")
    return value.strip()


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must be unique")
    return tuple(sorted(values))


class PlanDayStatus(StrEnum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    ACTIVE = "ACTIVE"
    CLOSED = "DAY_CLOSED"


class TaskStatus(StrEnum):
    OPEN = "OPEN"
    BLOCKED = "BLOCKED"
    WAITING = "WAITING"
    DONE = "DONE"
    DISPUTED = "DISPUTED"
    SAFE_HOLD = "SAFE_HOLD"


class StageStatus(StrEnum):
    OPEN = "OPEN"
    DONE = "DONE"
    DISPUTED = "DISPUTED"
    SAFE_HOLD = "SAFE_HOLD"


class AssignmentKind(StrEnum):
    SINGLE = "SINGLE"
    CREW = "CREW"


class CompletionType(StrEnum):
    TASK = "TASK"
    STAGE = "STAGE"
    WAITING = "WAITING"
    VISIT = "VISIT"
    DAY_CLOSE = "DAY_CLOSE"


class EvidenceKind(StrEnum):
    PHOTO = "PHOTO"
    VOICE = "VOICE"
    TEXT = "TEXT"
    MEASUREMENT = "MEASUREMENT"


class FieldEventType(StrEnum):
    DAY_PLAN_ACTIVATED = "DAY_PLAN_ACTIVATED"
    START_DELAY_REPORTED = "START_DELAY_REPORTED"
    START_EXCEPTION_REPORTED = "START_EXCEPTION_REPORTED"
    UNAVAILABLE_TODAY_REPORTED = "UNAVAILABLE_TODAY_REPORTED"
    WORK_START_BLOCKED = "WORK_START_BLOCKED"
    RESOURCE_MISSING_REPORTED = "RESOURCE_MISSING_REPORTED"
    SITE_PROBLEM_REPORTED = "SITE_PROBLEM_REPORTED"
    SCOPE_FACT_REPORTED = "SCOPE_FACT_REPORTED"
    TASK_COMPLETION_REPORTED = "TASK_COMPLETION_REPORTED"
    STAGE_COMPLETION_REPORTED = "STAGE_COMPLETION_REPORTED"
    TECHNICAL_WAIT_REPORTED = "TECHNICAL_WAIT_REPORTED"
    VISIT_COMPLETION_REPORTED = "VISIT_COMPLETION_REPORTED"
    DAY_CLOSE_REPORTED = "DAY_CLOSE_REPORTED"
    COMPLETION_DISPUTED = "COMPLETION_DISPUTED"
    WORKER_ACKNOWLEDGED = "WORKER_ACKNOWLEDGED"
    WORKER_ACTION_EXCEPTION = "WORKER_ACTION_EXCEPTION"


class DelayReason(StrEnum):
    TRAFFIC = "TRAFFIC"
    TRANSPORT = "TRANSPORT"
    PERSONAL = "PERSONAL"
    OTHER = "OTHER"


class StartExceptionReason(StrEnum):
    TRANSPORT_BLOCKED = "TRANSPORT_BLOCKED"
    PERSONAL_EMERGENCY = "PERSONAL_EMERGENCY"
    SICK = "SICK"
    OTHER = "OTHER"


class WorkStartBlockedReason(StrEnum):
    NO_ACCESS = "NO_ACCESS"
    CLIENT_ABSENT = "CLIENT_ABSENT"
    PREDECESSOR_NOT_READY = "PREDECESSOR_NOT_READY"
    UNSAFE = "UNSAFE"
    WEATHER = "WEATHER"
    OTHER = "OTHER"


class ResourceKind(StrEnum):
    MATERIAL = "MATERIAL"
    TOOL = "TOOL"
    VEHICLE = "VEHICLE"
    KEY = "KEY"
    DRAWING = "DRAWING"
    OTHER = "OTHER"


class UnavailableReason(StrEnum):
    SICK = "SICK"
    PERSONAL_EMERGENCY = "PERSONAL_EMERGENCY"
    OTHER = "OTHER"


class ProblemHint(StrEnum):
    BLOCKS_WORK = "BLOCKS_WORK"
    UNSAFE = "UNSAFE"
    QUALITY = "QUALITY"
    OTHER = "OTHER"


class ActionExceptionReason(StrEnum):
    VEHICLE_UNSAFE = "VEHICLE_UNSAFE"
    PHYSICALLY_UNABLE = "PHYSICALLY_UNABLE"
    CONFLICTING_FACT = "CONFLICTING_FACT"
    ALREADY_COMMITTED = "ALREADY_COMMITTED"
    OTHER = "OTHER"


class DirectiveClass(StrEnum):
    INFO = "INFO"
    ACTION = "ACTION"
    STOP = "STOP"


class DirectiveType(StrEnum):
    INFO_NOTICE = "INFO_NOTICE"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    STOP_DIRECTIVE = "STOP_DIRECTIVE"
    END_OF_DAY_OPTIONS = "END_OF_DAY_OPTIONS"
    WHY_EXPLAINED = "WHY_EXPLAINED"


class DeliveryEvidence(StrEnum):
    QUEUED = "QUEUED"
    CHANNEL_ACCEPTED = "CHANNEL_ACCEPTED"
    APP_OBSERVED = "APP_OBSERVED"
    ACKED = "ACKED"


class SiteProblemAction(StrEnum):
    RECORD_ONLY = "RECORD_ONLY"
    HOLD = "HOLD"
    ESCALATE = "ESCALATE"
    HOLD_AND_ESCALATE = "HOLD_AND_ESCALATE"
    REQUEST_STOP = "REQUEST_STOP"


class EndOfDayAction(StrEnum):
    RETURN_BASE = "RETURN_BASE"
    PREP_NEXT_DAY = "PREP_NEXT_DAY"
    OFFER_SOFT_OPTIONS = "OFFER_SOFT_OPTIONS"
    END_ON_SITE = "END_ON_SITE"
    ASK_OWNER = "ASK_OWNER"


class SystemSignalType(StrEnum):
    START_WINDOW_ELAPSED = "START_WINDOW_ELAPSED"
    DIRECTIVE_E1_ELAPSED = "DIRECTIVE_E1_ELAPSED"
    EARLY_FINISH_EVALUATED = "EARLY_FINISH_EVALUATED"
    END_OF_DAY_POLICY_SATISFIED = "END_OF_DAY_POLICY_SATISFIED"
    STATE_REHYDRATED = "STATE_REHYDRATED"
    SYNC_REPLAYED = "SYNC_REPLAYED"
    DAY_ROLLED_OVER = "DAY_ROLLED_OVER"


class ReductionOutcome(StrEnum):
    APPLIED = "APPLIED"
    REPORTED = "REPORTED"
    NOOP = "NOOP"
    REJECTED = "REJECTED"


class EffectType(StrEnum):
    PLAN_DAY_ACTIVATED = "PLAN_DAY_ACTIVATED"
    START_UNKNOWN_ESCALATED = "START_UNKNOWN_ESCALATED"
    START_DELAY_RECORDED = "START_DELAY_RECORDED"
    DELAY_IMPACT_EVALUATION_REQUIRED = "DELAY_IMPACT_EVALUATION_REQUIRED"
    START_EXCEPTION_RECORDED = "START_EXCEPTION_RECORDED"
    TRANSPORT_RESOLUTION_REQUIRED = "TRANSPORT_RESOLUTION_REQUIRED"
    UNAVAILABLE_TODAY_RECORDED = "UNAVAILABLE_TODAY_RECORDED"
    TASK_HELD = "TASK_HELD"
    RESOURCE_MISSING_RECORDED = "RESOURCE_MISSING_RECORDED"
    SITE_PROBLEM_RECORDED = "SITE_PROBLEM_RECORDED"
    SITE_PROBLEM_ESCALATED = "SITE_PROBLEM_ESCALATED"
    STOP_DIRECTIVE_REQUESTED = "STOP_DIRECTIVE_REQUESTED"
    NON_BINDING_SCOPE_CHANGE_RECORDED = "NON_BINDING_SCOPE_CHANGE_RECORDED"
    COMPLETION_ACCEPTED = "COMPLETION_ACCEPTED"
    COMPLETION_REJECTED = "COMPLETION_REJECTED"
    ASSIGNMENT_INVALID = "ASSIGNMENT_INVALID"
    TECHNICAL_WAIT_RECORDED = "TECHNICAL_WAIT_RECORDED"
    COMPLETION_DISPUTED = "COMPLETION_DISPUTED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    DIRECTIVE_ACKED = "DIRECTIVE_ACKED"
    ACTION_REEVALUATION_REQUIRED = "ACTION_REEVALUATION_REQUIRED"
    SAFE_HOLD_ENTERED = "SAFE_HOLD_ENTERED"
    ACK_MISSING_ESCALATED = "ACK_MISSING_ESCALATED"
    STOP_UNCONFIRMED = "STOP_UNCONFIRMED"
    EARLY_FINISH_SOFT_OPTIONS = "EARLY_FINISH_SOFT_OPTIONS"
    END_OF_DAY_POLICY_REQUESTED = "END_OF_DAY_POLICY_REQUESTED"
    DAY_CLOSED = "DAY_CLOSED"


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    evidence_id: str
    kind: EvidenceKind
    content_reference: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _nonblank(self.evidence_id, "evidence_id"))
        object.__setattr__(
            self,
            "content_reference",
            _nonblank(self.content_reference, "content_reference"),
        )


@dataclass(frozen=True, slots=True)
class StageState:
    stage_id: str
    status: StageStatus = StageStatus.OPEN
    completion_event_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage_id", _nonblank(self.stage_id, "stage_id"))


@dataclass(frozen=True, slots=True)
class TaskDefinition:
    task_id: str
    definition_version: str
    job_id: str
    source_handoff_id: str
    source_revision: int
    business_meaning: str
    completion_type: CompletionType
    required_postconditions: tuple[str, ...] = ()
    required_evidence: tuple[EvidenceKind, ...] = ()
    stage_ids: tuple[str, ...] = ()
    requires_quantity: bool = False
    assignment_kind: AssignmentKind = AssignmentKind.SINGLE
    supersedes_task_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "task_id",
            "definition_version",
            "job_id",
            "source_handoff_id",
            "business_meaning",
        ):
            object.__setattr__(self, name, _nonblank(getattr(self, name), name))
        if isinstance(self.source_revision, bool) or self.source_revision < 1:
            raise ValueError("source_revision must be positive")
        object.__setattr__(
            self,
            "required_postconditions",
            _unique(self.required_postconditions, "required_postconditions"),
        )
        object.__setattr__(self, "stage_ids", _unique(self.stage_ids, "stage_ids"))
        if len(self.required_evidence) != len(set(self.required_evidence)):
            raise ValueError("required_evidence must be unique")
        object.__setattr__(
            self,
            "required_evidence",
            tuple(sorted(self.required_evidence, key=lambda item: item.value)),
        )
        if self.supersedes_task_id is not None:
            object.__setattr__(
                self,
                "supersedes_task_id",
                _nonblank(self.supersedes_task_id, "supersedes_task_id"),
            )
            if self.supersedes_task_id == self.task_id:
                raise ValueError("task cannot supersede itself")


@dataclass(frozen=True, slots=True)
class FieldObservation:
    event_id: str
    actor_id: str
    text: str | None
    evidence: tuple[EvidenceItem, ...]
    interpretation: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _nonblank(self.event_id, "event_id"))
        object.__setattr__(self, "actor_id", _nonblank(self.actor_id, "actor_id"))
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("observation evidence identities must be unique")


@dataclass(frozen=True, slots=True)
class TaskState:
    definition: TaskDefinition
    status: TaskStatus = TaskStatus.OPEN
    stages: tuple[StageState, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    observations: tuple[FieldObservation, ...] = ()
    completion_event_id: str | None = None
    safe_hold_directive_id: str | None = None
    blocked_pending_resolution: bool = False
    execution_authorized: bool = True

    def __post_init__(self) -> None:
        stage_ids = tuple(stage.stage_id for stage in self.stages)
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("task stages must be unique")
        if set(stage_ids) != set(self.definition.stage_ids):
            raise ValueError("task stages must match the immutable task definition")
        object.__setattr__(self, "stages", tuple(sorted(self.stages, key=lambda item: item.stage_id)))
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("task evidence identities must be unique")
        observation_ids = tuple(item.event_id for item in self.observations)
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("task observation event identities must be unique")
        if self.status is TaskStatus.SAFE_HOLD and (
            not self.blocked_pending_resolution or self.execution_authorized
        ):
            raise ValueError("SAFE_HOLD must block execution pending resolution")


@dataclass(frozen=True, slots=True)
class AssignmentState:
    assignment_id: str
    job_id: str
    task_id: str
    task_definition_version: str
    kind: AssignmentKind
    member_worker_ids: tuple[str, ...]
    plan_day_ids: tuple[str, ...]
    lead_worker_id: str | None = None
    released_worker_ids: tuple[str, ...] = ()
    supersedes_assignment_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("assignment_id", "job_id", "task_id", "task_definition_version"):
            object.__setattr__(self, name, _nonblank(getattr(self, name), name))
        member_worker_ids = tuple(
            _nonblank(item, "member_worker_id") for item in self.member_worker_ids
        )
        plan_day_ids = tuple(
            _nonblank(item, "plan_day_id") for item in self.plan_day_ids
        )
        released_worker_ids = tuple(
            _nonblank(item, "released_worker_id") for item in self.released_worker_ids
        )
        object.__setattr__(
            self,
            "member_worker_ids",
            _unique(member_worker_ids, "member_worker_ids"),
        )
        object.__setattr__(self, "plan_day_ids", _unique(plan_day_ids, "plan_day_ids"))
        object.__setattr__(
            self,
            "released_worker_ids",
            _unique(released_worker_ids, "released_worker_ids"),
        )
        if self.lead_worker_id is not None:
            object.__setattr__(
                self,
                "lead_worker_id",
                _nonblank(self.lead_worker_id, "lead_worker_id"),
            )
        if self.supersedes_assignment_id is not None:
            object.__setattr__(
                self,
                "supersedes_assignment_id",
                _nonblank(self.supersedes_assignment_id, "supersedes_assignment_id"),
            )
            if self.supersedes_assignment_id == self.assignment_id:
                raise ValueError("assignment cannot supersede itself")


@dataclass(frozen=True, slots=True)
class PlanDayRoot:
    plan_day_id: str
    worker_id: str
    business_date: date
    start_at: datetime
    status: PlanDayStatus
    worker_available: bool = True
    day_close_reported: bool = False
    start_unknown_escalated: bool = False
    confirmed_plan_reference: str | None = None
    plan_day_revision: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_day_id", _nonblank(self.plan_day_id, "plan_day_id"))
        object.__setattr__(self, "worker_id", _nonblank(self.worker_id, "worker_id"))
        _aware(self.start_at, "start_at")
        if isinstance(self.plan_day_revision, bool) or self.plan_day_revision < 0:
            raise ValueError("plan_day_revision must be non-negative")
        if self.confirmed_plan_reference is not None:
            object.__setattr__(
                self,
                "confirmed_plan_reference",
                _nonblank(self.confirmed_plan_reference, "confirmed_plan_reference"),
            )
        if self.status is PlanDayStatus.CLOSED and self.worker_available:
            raise ValueError("DAY_CLOSED cannot retain worker availability")


@dataclass(frozen=True, slots=True)
class DirectiveDefinition:
    directive_id: str
    directive_type: DirectiveType
    directive_class: DirectiveClass
    worker_id: str
    issued_at: datetime
    escalation_due_at: datetime | None = None
    job_id: str | None = None
    task_id: str | None = None
    assignment_id: str | None = None
    plan_day_id: str | None = None
    proposed_plan_reference: str | None = None
    issuance_sequence: int = 1
    supersedes_directive_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "directive_id", _nonblank(self.directive_id, "directive_id"))
        object.__setattr__(self, "worker_id", _nonblank(self.worker_id, "worker_id"))
        _aware(self.issued_at, "issued_at")
        if self.escalation_due_at is not None:
            _aware(self.escalation_due_at, "escalation_due_at")
        for name in (
            "job_id",
            "task_id",
            "assignment_id",
            "plan_day_id",
            "proposed_plan_reference",
            "supersedes_directive_id",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _nonblank(value, name))
        if isinstance(self.issuance_sequence, bool) or self.issuance_sequence < 1:
            raise ValueError("issuance_sequence must be positive")
        if self.supersedes_directive_id == self.directive_id:
            raise ValueError("directive cannot supersede itself")
        if (
            self.directive_type is DirectiveType.ACTION_REQUIRED
            and self.directive_class is not DirectiveClass.ACTION
        ):
            raise ValueError("ACTION_REQUIRED must use ACTION class")
        if (
            self.directive_type is DirectiveType.STOP_DIRECTIVE
            and self.directive_class is not DirectiveClass.STOP
        ):
            raise ValueError("STOP_DIRECTIVE must use STOP class")


@dataclass(frozen=True, slots=True)
class DirectiveRoot:
    definition: DirectiveDefinition
    delivery_evidence: DeliveryEvidence = DeliveryEvidence.QUEUED
    acknowledged_event_id: str | None = None
    exception_event_id: str | None = None
    e1_escalated: bool = False
    stop_in_force: bool = False
    directive_revision: int = 0

    def __post_init__(self) -> None:
        for name in ("acknowledged_event_id", "exception_event_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _nonblank(value, name))
        if isinstance(self.directive_revision, bool) or self.directive_revision < 0:
            raise ValueError("directive_revision must be non-negative")

    @property
    def directive_id(self) -> str:
        return self.definition.directive_id

    @property
    def directive_type(self) -> DirectiveType:
        return self.definition.directive_type

    @property
    def directive_class(self) -> DirectiveClass:
        return self.definition.directive_class

    @property
    def worker_id(self) -> str:
        return self.definition.worker_id

    @property
    def issued_at(self) -> datetime:
        return self.definition.issued_at

    @property
    def escalation_due_at(self) -> datetime | None:
        return self.definition.escalation_due_at

    @property
    def job_id(self) -> str | None:
        return self.definition.job_id

    @property
    def task_id(self) -> str | None:
        return self.definition.task_id

    @property
    def assignment_id(self) -> str | None:
        return self.definition.assignment_id

    @property
    def plan_day_id(self) -> str | None:
        return self.definition.plan_day_id

    @property
    def proposed_plan_reference(self) -> str | None:
        return self.definition.proposed_plan_reference

    @property
    def issuance_sequence(self) -> int:
        return self.definition.issuance_sequence

    @property
    def supersedes_directive_id(self) -> str | None:
        return self.definition.supersedes_directive_id


@dataclass(frozen=True, slots=True)
class FieldEventEnvelope:
    event_id: str
    schema_version: int
    event_type: FieldEventType
    actor_id: str
    occurred_at: datetime
    plan_day_id: str | None = None
    job_id: str | None = None
    task_id: str | None = None
    assignment_id: str | None = None
    stage_id: str | None = None
    directive_id: str | None = None
    against_event_id: str | None = None
    reason_class: str | None = None
    eta: str | None = None
    text: str | None = None
    wait_condition: str | None = None
    wait_until: datetime | None = None
    quantity: str | None = None
    unit: str | None = None
    severity_hint: ProblemHint | None = None
    attachments: tuple[EvidenceItem, ...] = ()
    satisfied_postconditions: tuple[str, ...] = ()
    offline_origin: bool = False
    client_context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _nonblank(self.event_id, "event_id"))
        object.__setattr__(self, "actor_id", _nonblank(self.actor_id, "actor_id"))
        if isinstance(self.schema_version, bool) or self.schema_version < 1:
            raise ValueError("schema_version must be positive")
        _aware(self.occurred_at, "occurred_at")
        if self.wait_until is not None:
            _aware(self.wait_until, "wait_until")
        object.__setattr__(
            self,
            "satisfied_postconditions",
            _unique(self.satisfied_postconditions, "satisfied_postconditions"),
        )
        evidence_ids = tuple(item.evidence_id for item in self.attachments)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("attachment evidence identities must be unique")
        keys = tuple(key for key, _value in self.client_context)
        if len(keys) != len(set(keys)):
            raise ValueError("client_context keys must be unique")
        object.__setattr__(self, "client_context", tuple(sorted(self.client_context)))


@dataclass(frozen=True, slots=True)
class FieldEventInput:
    event: FieldEventEnvelope
    server_event_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "server_event_id",
            _nonblank(self.server_event_id, "server_event_id"),
        )


@dataclass(frozen=True, slots=True)
class SystemSignal:
    signal_id: str
    signal_type: SystemSignalType
    plan_day_id: str | None = None
    directive_id: str | None = None
    task_id: str | None = None
    policy_result: EndOfDayAction | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_id", _nonblank(self.signal_id, "signal_id"))


@dataclass(frozen=True, slots=True)
class M2Policy:
    start_unknown_escalation_enabled: bool = True
    site_problem_action: SiteProblemAction = SiteProblemAction.RECORD_ONLY
    early_finish_options: tuple[EndOfDayAction, ...] = ()
    end_of_day_action: EndOfDayAction = EndOfDayAction.END_ON_SITE

    def __post_init__(self) -> None:
        if len(self.early_finish_options) != len(set(self.early_finish_options)):
            raise ValueError("early_finish_options must be unique")


@dataclass(frozen=True, slots=True)
class PolicyTimeContext:
    now: datetime
    policy: M2Policy
    rule_version: str = M2_REDUCER_RULE_VERSION

    def __post_init__(self) -> None:
        _aware(self.now, "now")
        object.__setattr__(self, "rule_version", _nonblank(self.rule_version, "rule_version"))


@dataclass(frozen=True, slots=True)
class SystemEffect:
    effect_type: EffectType
    source_id: str
    plan_day_id: str | None = None
    job_id: str | None = None
    task_id: str | None = None
    assignment_id: str | None = None
    stage_id: str | None = None
    directive_id: str | None = None
    actor_id: str | None = None
    details: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _nonblank(self.source_id, "source_id"))
        keys = tuple(key for key, _value in self.details)
        if len(keys) != len(set(keys)):
            raise ValueError("effect detail keys must be unique")
        object.__setattr__(self, "details", tuple(sorted(self.details)))


@dataclass(frozen=True, slots=True)
class ProcessedEventReceipt:
    event: FieldEventEnvelope
    server_event_id: str
    outcome: ReductionOutcome
    response_effects: tuple[SystemEffect, ...]
    missing_requirements: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class M2JobExecutionRoot:
    job_id: str
    tasks: tuple[TaskState, ...]
    assignments: tuple[AssignmentState, ...]
    job_execution_revision: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "job_id", _nonblank(self.job_id, "job_id"))
        if isinstance(self.job_execution_revision, bool) or self.job_execution_revision < 0:
            raise ValueError("job_execution_revision must be non-negative")
        for name, items, identity in (
            ("tasks", self.tasks, lambda item: item.definition.task_id),
            ("assignments", self.assignments, lambda item: item.assignment_id),
        ):
            ids = tuple(identity(item) for item in items)
            if len(ids) != len(set(ids)):
                raise ValueError(f"{name} identities must be unique")
            object.__setattr__(self, name, tuple(sorted(items, key=identity)))

        for task in self.tasks:
            if task.definition.job_id != self.job_id:
                raise ValueError("task definition must belong to its job execution root")
            predecessor_id = task.definition.supersedes_task_id
            if predecessor_id is not None:
                predecessor = self.task(predecessor_id)
                if predecessor is None:
                    raise ValueError(
                        "superseded task must exist in the same job execution root"
                    )
                if (
                    predecessor.definition.source_revision
                    > task.definition.source_revision
                ):
                    raise ValueError("task cannot supersede a newer source revision")
        for assignment in self.assignments:
            task = self.task(assignment.task_id)
            if assignment.job_id != self.job_id:
                raise ValueError("assignment must belong to its job execution root")
            if task is None:
                raise ValueError("assignment must reference a task in its job execution root")
            if assignment.task_definition_version != task.definition.definition_version:
                raise ValueError("assignment task definition version must be exact")
            predecessor_id = assignment.supersedes_assignment_id
            if predecessor_id is not None:
                predecessor = self.assignment(predecessor_id)
                if predecessor is None:
                    raise ValueError(
                        "superseded assignment must exist in the same job execution root"
                    )
                if not self._task_lineage_contains(
                    task.definition.task_id,
                    predecessor.task_id,
                ):
                    raise ValueError(
                        "superseded assignment must use a compatible task lineage"
                    )

        self._validate_acyclic_supersession(
            tuple(
                (
                    task.definition.task_id,
                    task.definition.supersedes_task_id,
                )
                for task in self.tasks
            ),
            "task",
        )
        self._validate_acyclic_supersession(
            tuple(
                (assignment.assignment_id, assignment.supersedes_assignment_id)
                for assignment in self.assignments
            ),
            "assignment",
        )

    def task(self, task_id: str) -> TaskState | None:
        return next((item for item in self.tasks if item.definition.task_id == task_id), None)

    def assignment(self, assignment_id: str) -> AssignmentState | None:
        return next((item for item in self.assignments if item.assignment_id == assignment_id), None)

    def _task_lineage_contains(self, task_id: str, predecessor_task_id: str) -> bool:
        current = self.task(task_id)
        visited: set[str] = set()
        while current is not None and current.definition.task_id not in visited:
            current_id = current.definition.task_id
            if current_id == predecessor_task_id:
                return True
            visited.add(current_id)
            next_id = current.definition.supersedes_task_id
            current = self.task(next_id) if next_id is not None else None
        return False

    @staticmethod
    def _validate_acyclic_supersession(
        relations: tuple[tuple[str, str | None], ...],
        relation_name: str,
    ) -> None:
        predecessor_by_id = dict(relations)
        for identity in predecessor_by_id:
            visited: set[str] = set()
            current: str | None = identity
            while current is not None:
                if current in visited:
                    raise ValueError(f"{relation_name} supersession must be acyclic")
                visited.add(current)
                current = predecessor_by_id.get(current)


@dataclass(frozen=True, slots=True)
class WorkerIdentityRegistry:
    worker_ids: tuple[str, ...]
    registry_revision: int = 0

    def __post_init__(self) -> None:
        worker_ids = tuple(_nonblank(item, "worker_id") for item in self.worker_ids)
        object.__setattr__(self, "worker_ids", _unique(worker_ids, "worker_ids"))
        if isinstance(self.registry_revision, bool) or self.registry_revision < 0:
            raise ValueError("registry_revision must be non-negative")


@dataclass(frozen=True, slots=True)
class ProcessedEventLedger:
    receipts: tuple[ProcessedEventReceipt, ...] = ()

    def __post_init__(self) -> None:
        event_ids = tuple(item.event.event_id for item in self.receipts)
        server_event_ids = tuple(item.server_event_id for item in self.receipts)
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("processed event identities must be unique")
        if len(server_event_ids) != len(set(server_event_ids)):
            raise ValueError("processed server event identities must be unique")
        object.__setattr__(
            self,
            "receipts",
            tuple(sorted(self.receipts, key=lambda item: item.event.event_id)),
        )

    def __iter__(self):
        return iter(self.receipts)

    def __len__(self) -> int:
        return len(self.receipts)

    def receipt(self, event_id: str) -> ProcessedEventReceipt | None:
        return next(
            (item for item in self.receipts if item.event.event_id == event_id),
            None,
        )


@dataclass(frozen=True, slots=True)
class M2ReductionScope:
    publications: tuple[M1HandoffPublication, ...]
    worker_registry: WorkerIdentityRegistry
    job_execution_roots: tuple[M2JobExecutionRoot, ...] = ()
    plan_day_roots: tuple[PlanDayRoot, ...] = ()
    directive_roots: tuple[DirectiveRoot, ...] = ()
    processed_events: ProcessedEventLedger = field(default_factory=ProcessedEventLedger)

    def __post_init__(self) -> None:
        publication_key = lambda item: (
            item.projection.job_id,
            item.projection.source_revision,
            item.handoff_id,
        )
        publication_keys = tuple(publication_key(item) for item in self.publications)
        if len(publication_keys) != len(set(publication_keys)):
            raise ValueError("publication identities must be unique")
        object.__setattr__(self, "publications", tuple(sorted(self.publications, key=publication_key)))

        for name, items, identity in (
            ("job_execution_roots", self.job_execution_roots, lambda item: item.job_id),
            ("plan_day_roots", self.plan_day_roots, lambda item: item.plan_day_id),
            ("directive_roots", self.directive_roots, lambda item: item.directive_id),
        ):
            ids = tuple(identity(item) for item in items)
            if len(ids) != len(set(ids)):
                raise ValueError(f"{name} identities must be unique")
            object.__setattr__(self, name, tuple(sorted(items, key=identity)))

        task_ids = tuple(item.definition.task_id for item in self.tasks)
        assignment_ids = tuple(item.assignment_id for item in self.assignments)
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task identities must be globally unique in reduction scope")
        if len(assignment_ids) != len(set(assignment_ids)):
            raise ValueError("assignment identities must be globally unique in reduction scope")

        for task in self.tasks:
            definition = task.definition
            matches = tuple(
                publication
                for publication in self.publications
                if publication.projection.job_id == definition.job_id
                and publication.projection.source_revision == definition.source_revision
                and publication.handoff_id == definition.source_handoff_id
            )
            if len(matches) != 1:
                raise ValueError(
                    "task definition must reference exactly one canonical M1 publication"
                )

        workers = set(self.worker_registry.worker_ids)
        if any(plan.worker_id not in workers for plan in self.plan_day_roots):
            raise ValueError("plan day worker must use the canonical worker namespace")
        if any(directive.worker_id not in workers for directive in self.directive_roots):
            raise ValueError("directive worker must use the canonical worker namespace")
        for assignment in self.assignments:
            if any(member not in workers for member in assignment.member_worker_ids):
                raise ValueError("assignment member must use the canonical worker namespace")
            if (
                assignment.lead_worker_id is not None
                and assignment.lead_worker_id not in workers
            ):
                raise ValueError("assignment lead must use the canonical worker namespace")

        seen_sequences: set[tuple[tuple[str, str, str], int]] = set()
        for directive in self.directive_roots:
            sequence_key = (
                self.directive_stream_key(directive),
                directive.issuance_sequence,
            )
            if sequence_key in seen_sequences:
                raise ValueError(
                    "issuance_sequence must be unique within a directive stream"
                )
            seen_sequences.add(sequence_key)

            predecessor_id = directive.supersedes_directive_id
            if predecessor_id is None:
                continue
            predecessor = self.directive(predecessor_id)
            if predecessor is None:
                raise ValueError("superseded directive must exist in reduction scope")
            if predecessor.worker_id != directive.worker_id:
                raise ValueError("superseded directive must use the same worker")
            if (
                self.directive_stream_key(predecessor)
                != self.directive_stream_key(directive)
                or not self._directive_scopes_compatible(predecessor, directive)
            ):
                raise ValueError("superseded directive must use a compatible scope")
            if predecessor.issuance_sequence >= directive.issuance_sequence:
                raise ValueError("superseded directive must have an earlier issuance sequence")

    @property
    def worker_ids(self) -> tuple[str, ...]:
        return self.worker_registry.worker_ids

    @property
    def plan_days(self) -> tuple[PlanDayRoot, ...]:
        return self.plan_day_roots

    @property
    def tasks(self) -> tuple[TaskState, ...]:
        return tuple(
            sorted(
                (
                    task
                    for root in self.job_execution_roots
                    for task in root.tasks
                ),
                key=lambda item: item.definition.task_id,
            )
        )

    @property
    def assignments(self) -> tuple[AssignmentState, ...]:
        return tuple(
            sorted(
                (
                    assignment
                    for root in self.job_execution_roots
                    for assignment in root.assignments
                ),
                key=lambda item: item.assignment_id,
            )
        )

    @property
    def directives(self) -> tuple[DirectiveRoot, ...]:
        return self.directive_roots

    def job_execution(self, job_id: str) -> M2JobExecutionRoot | None:
        return next(
            (item for item in self.job_execution_roots if item.job_id == job_id),
            None,
        )

    def plan_day(self, plan_day_id: str) -> PlanDayRoot | None:
        return next((item for item in self.plan_day_roots if item.plan_day_id == plan_day_id), None)

    def task(self, task_id: str) -> TaskState | None:
        return next((item for item in self.tasks if item.definition.task_id == task_id), None)

    def assignment(self, assignment_id: str) -> AssignmentState | None:
        return next((item for item in self.assignments if item.assignment_id == assignment_id), None)

    def directive(self, directive_id: str) -> DirectiveRoot | None:
        return next((item for item in self.directive_roots if item.directive_id == directive_id), None)

    def receipt(self, event_id: str) -> ProcessedEventReceipt | None:
        return self.processed_events.receipt(event_id)

    def directive_stream_key(
        self,
        directive: DirectiveRoot,
    ) -> tuple[str, str, str]:
        if directive.plan_day_id is not None:
            return directive.worker_id, "PLAN_DAY", directive.plan_day_id
        if directive.assignment_id is not None:
            return directive.worker_id, "ASSIGNMENT", directive.assignment_id
        if directive.task_id is not None:
            return directive.worker_id, "TASK", directive.task_id
        if directive.job_id is not None:
            return directive.worker_id, "JOB", directive.job_id
        return directive.worker_id, "WORKER", directive.worker_id

    def directive_scope_errors(
        self,
        directive: DirectiveRoot,
    ) -> tuple[str, ...]:
        errors: list[str] = []
        if (
            directive.job_id is not None
            and self.job_execution(directive.job_id) is None
        ):
            errors.append("CROSS_JOB_DIRECTIVE")
        if directive.worker_id not in self.worker_registry.worker_ids:
            errors.append("UNKNOWN_DIRECTIVE_WORKER")

        task = self.task(directive.task_id) if directive.task_id is not None else None
        if directive.task_id is not None and task is None:
            errors.append("UNKNOWN_DIRECTIVE_TASK")
        elif (
            task is not None
            and directive.job_id is not None
            and task.definition.job_id != directive.job_id
        ):
            errors.append("CROSS_JOB_DIRECTIVE_TASK")

        assignment = (
            self.assignment(directive.assignment_id)
            if directive.assignment_id is not None
            else None
        )
        if directive.assignment_id is not None and assignment is None:
            errors.append("UNKNOWN_DIRECTIVE_ASSIGNMENT")
        elif assignment is not None:
            if directive.job_id is not None and assignment.job_id != directive.job_id:
                errors.append("CROSS_JOB_DIRECTIVE_ASSIGNMENT")
            if any(
                member not in self.worker_registry.worker_ids
                for member in assignment.member_worker_ids
            ):
                errors.append("UNKNOWN_DIRECTIVE_ASSIGNMENT_MEMBER")
            if directive.task_id is not None and assignment.task_id != directive.task_id:
                errors.append("CROSS_TASK_DIRECTIVE_ASSIGNMENT")
            if (
                task is not None
                and assignment.task_definition_version
                != task.definition.definition_version
            ):
                errors.append("DIRECTIVE_TASK_DEFINITION_VERSION_MISMATCH")
            if directive.worker_id not in assignment.member_worker_ids:
                errors.append("DIRECTIVE_WORKER_NOT_ASSIGNED")

        plan = (
            self.plan_day(directive.plan_day_id)
            if directive.plan_day_id is not None
            else None
        )
        if directive.plan_day_id is not None and plan is None:
            errors.append("UNKNOWN_DIRECTIVE_PLAN_DAY")
        elif plan is not None:
            if plan.worker_id != directive.worker_id:
                errors.append("DIRECTIVE_PLAN_DAY_WORKER_MISMATCH")
            if assignment is not None and plan.plan_day_id not in assignment.plan_day_ids:
                errors.append("CROSS_PLAN_DAY_DIRECTIVE_ASSIGNMENT")

        if assignment is None and (
            task is not None
            or (directive.job_id is not None and directive.plan_day_id is not None)
        ):
            context_matches = tuple(
                candidate
                for candidate in self.assignments
                if candidate.job_id
                == (
                    task.definition.job_id
                    if task is not None
                    else directive.job_id
                )
                and (
                    task is None
                    or (
                        candidate.task_id == task.definition.task_id
                        and candidate.task_definition_version
                        == task.definition.definition_version
                    )
                )
                and directive.worker_id in candidate.member_worker_ids
                and (
                    directive.plan_day_id is None
                    or directive.plan_day_id in candidate.plan_day_ids
                )
            )
            if not context_matches:
                errors.append("DIRECTIVE_EXECUTION_CONTEXT_REQUIRED")
            elif len(context_matches) > 1:
                errors.append("AMBIGUOUS_DIRECTIVE_EXECUTION_CONTEXT")

        return tuple(sorted(set(errors)))

    def directive_worker_informed(self, directive_id: str) -> bool:
        directive = self.directive(directive_id)
        if (
            directive is None
            or directive.delivery_evidence is not DeliveryEvidence.ACKED
            or directive.acknowledged_event_id is None
            or self.directive_scope_errors(directive)
        ):
            return False
        receipt = self.receipt(directive.acknowledged_event_id)
        if receipt is None:
            return False
        event = receipt.event
        scope_pairs = (
            (event.job_id, directive.job_id),
            (event.task_id, directive.task_id),
            (event.assignment_id, directive.assignment_id),
            (event.plan_day_id, directive.plan_day_id),
        )
        return (
            receipt.outcome is ReductionOutcome.APPLIED
            and event.event_type is FieldEventType.WORKER_ACKNOWLEDGED
            and event.directive_id == directive.directive_id
            and event.actor_id == directive.worker_id
            and all(
                event_value is None or event_value == directive_value
                for event_value, directive_value in scope_pairs
            )
        )

    def later_governing_directive_exists(self, directive: DirectiveRoot) -> bool:
        stream_key = self.directive_stream_key(directive)
        return any(
            candidate.issuance_sequence > directive.issuance_sequence
            and self.directive_stream_key(candidate) == stream_key
            and not self.directive_scope_errors(candidate)
            and (
                self._directive_supersedes(candidate, directive.directive_id)
                or (
                    candidate.directive_class is DirectiveClass.ACTION
                    and self.directive_worker_informed(candidate.directive_id)
                )
                or (
                    candidate.directive_class is DirectiveClass.STOP
                    and (
                        candidate.stop_in_force
                        or self.directive_worker_informed(candidate.directive_id)
                    )
                )
            )
            for candidate in self.directive_roots
        )

    def _directive_scopes_compatible(
        self,
        predecessor: DirectiveRoot,
        successor: DirectiveRoot,
    ) -> bool:
        predecessor_job = self._resolved_directive_job(predecessor)
        successor_job = self._resolved_directive_job(successor)
        if (
            predecessor_job is not None
            and successor_job is not None
            and predecessor_job != successor_job
        ):
            return False
        return all(
            predecessor_value is None
            or successor_value is None
            or predecessor_value == successor_value
            for predecessor_value, successor_value in (
                (predecessor.task_id, successor.task_id),
                (predecessor.assignment_id, successor.assignment_id),
                (predecessor.plan_day_id, successor.plan_day_id),
            )
        )

    def _directive_supersedes(
        self,
        directive: DirectiveRoot,
        predecessor_id: str,
    ) -> bool:
        current = directive
        visited: set[str] = set()
        while current.supersedes_directive_id is not None:
            current_id = current.directive_id
            if current_id in visited:
                return False
            visited.add(current_id)
            if current.supersedes_directive_id == predecessor_id:
                return True
            predecessor = self.directive(current.supersedes_directive_id)
            if predecessor is None:
                return False
            current = predecessor
        return False

    def _resolved_directive_job(self, directive: DirectiveRoot) -> str | None:
        if directive.job_id is not None:
            return directive.job_id
        if directive.task_id is not None:
            task = self.task(directive.task_id)
            if task is not None:
                return task.definition.job_id
        if directive.assignment_id is not None:
            assignment = self.assignment(directive.assignment_id)
            if assignment is not None:
                return assignment.job_id
        return None

    def publication(
        self,
        job_id: str,
        source_revision: int,
        handoff_id: str,
    ) -> M1HandoffPublication | None:
        return next(
            (
                item
                for item in self.publications
                if item.projection.job_id == job_id
                and item.projection.source_revision == source_revision
                and item.handoff_id == handoff_id
            ),
            None,
        )


class RootKind(StrEnum):
    JOB_EXECUTION = "JOB_EXECUTION"
    PLAN_DAY = "PLAN_DAY"
    DIRECTIVE = "DIRECTIVE"


RootState = M2JobExecutionRoot | PlanDayRoot | DirectiveRoot


@dataclass(frozen=True, slots=True)
class RootDelta:
    root_kind: RootKind
    root_id: str
    expected_revision: int
    resulting_revision: int
    next_root: RootState

    def __post_init__(self) -> None:
        object.__setattr__(self, "root_id", _nonblank(self.root_id, "root_id"))
        if isinstance(self.expected_revision, bool) or self.expected_revision < 0:
            raise ValueError("expected_revision must be non-negative")
        if self.resulting_revision != self.expected_revision + 1:
            raise ValueError("resulting_revision must increment exactly once")
        expected_type, identity, revision = {
            RootKind.JOB_EXECUTION: (
                M2JobExecutionRoot,
                self.next_root.job_id
                if isinstance(self.next_root, M2JobExecutionRoot)
                else None,
                self.next_root.job_execution_revision
                if isinstance(self.next_root, M2JobExecutionRoot)
                else None,
            ),
            RootKind.PLAN_DAY: (
                PlanDayRoot,
                self.next_root.plan_day_id
                if isinstance(self.next_root, PlanDayRoot)
                else None,
                self.next_root.plan_day_revision
                if isinstance(self.next_root, PlanDayRoot)
                else None,
            ),
            RootKind.DIRECTIVE: (
                DirectiveRoot,
                self.next_root.directive_id
                if isinstance(self.next_root, DirectiveRoot)
                else None,
                self.next_root.directive_revision
                if isinstance(self.next_root, DirectiveRoot)
                else None,
            ),
        }[self.root_kind]
        if not isinstance(self.next_root, expected_type):
            raise ValueError("root_kind must match next_root type")
        if identity != self.root_id:
            raise ValueError("root_id must match next_root identity")
        if revision != self.resulting_revision:
            raise ValueError("next_root revision must match resulting_revision")


@dataclass(frozen=True, slots=True)
class Reduction:
    state: M2ReductionScope
    outcome: ReductionOutcome
    root_deltas: tuple[RootDelta, ...] = field(default_factory=tuple)
    appended_receipt: ProcessedEventReceipt | None = None
    emitted_effects: tuple[SystemEffect, ...] = field(default_factory=tuple)
    response_effects: tuple[SystemEffect, ...] = field(default_factory=tuple)
    server_event_id: str | None = None
    missing_requirements: tuple[str, ...] = field(default_factory=tuple)
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    replayed: bool = False


ExplicitInput = FieldEventInput | SystemSignal
