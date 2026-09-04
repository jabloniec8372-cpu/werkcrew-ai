"""Immutable models for the canonical deterministic M2 reducer."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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

    def __post_init__(self) -> None:
        for name in ("assignment_id", "job_id", "task_id", "task_definition_version"):
            object.__setattr__(self, name, _nonblank(getattr(self, name), name))
        object.__setattr__(
            self,
            "member_worker_ids",
            _unique(self.member_worker_ids, "member_worker_ids"),
        )
        object.__setattr__(self, "plan_day_ids", _unique(self.plan_day_ids, "plan_day_ids"))
        object.__setattr__(
            self,
            "released_worker_ids",
            _unique(self.released_worker_ids, "released_worker_ids"),
        )


@dataclass(frozen=True, slots=True)
class PlanDayState:
    plan_day_id: str
    worker_id: str
    business_date: date
    start_at: datetime
    status: PlanDayStatus
    worker_available: bool = True
    day_close_reported: bool = False
    start_unknown_escalated: bool = False
    confirmed_plan_reference: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_day_id", _nonblank(self.plan_day_id, "plan_day_id"))
        object.__setattr__(self, "worker_id", _nonblank(self.worker_id, "worker_id"))
        _aware(self.start_at, "start_at")
        if self.status is PlanDayStatus.CLOSED and self.worker_available:
            raise ValueError("DAY_CLOSED cannot retain worker availability")


@dataclass(frozen=True, slots=True)
class DirectiveState:
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
    delivery_evidence: DeliveryEvidence = DeliveryEvidence.QUEUED
    acknowledged_event_id: str | None = None
    exception_event_id: str | None = None
    proposed_plan_reference: str | None = None
    e1_escalated: bool = False
    stop_in_force: bool = False
    _ack_history_validated: bool = field(default=False, init=False, repr=False)

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
            "acknowledged_event_id",
            "exception_event_id",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _nonblank(value, name))
        if self.directive_type is DirectiveType.ACTION_REQUIRED and self.directive_class is not DirectiveClass.ACTION:
            raise ValueError("ACTION_REQUIRED must use ACTION class")
        if self.directive_type is DirectiveType.STOP_DIRECTIVE and self.directive_class is not DirectiveClass.STOP:
            raise ValueError("STOP_DIRECTIVE must use STOP class")

    @property
    def worker_informed(self) -> bool:
        return (
            self.delivery_evidence is DeliveryEvidence.ACKED
            and self.acknowledged_event_id is not None
            and self._ack_history_validated
        )


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
class M2Aggregate:
    publication: M1HandoffPublication
    worker_ids: tuple[str, ...]
    plan_days: tuple[PlanDayState, ...]
    tasks: tuple[TaskState, ...]
    assignments: tuple[AssignmentState, ...]
    directives: tuple[DirectiveState, ...] = ()
    processed_events: tuple[ProcessedEventReceipt, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "worker_ids", _unique(self.worker_ids, "worker_ids"))
        for name, items, identity in (
            ("plan_days", self.plan_days, lambda item: item.plan_day_id),
            ("tasks", self.tasks, lambda item: item.definition.task_id),
            ("assignments", self.assignments, lambda item: item.assignment_id),
            ("directives", self.directives, lambda item: item.directive_id),
            ("processed_events", self.processed_events, lambda item: item.event.event_id),
        ):
            ids = tuple(identity(item) for item in items)
            if len(ids) != len(set(ids)):
                raise ValueError(f"{name} identities must be unique")
            object.__setattr__(self, name, tuple(sorted(items, key=identity)))

        projection = self.publication.projection
        for task in self.tasks:
            definition = task.definition
            if (
                definition.job_id != projection.job_id
                or definition.source_handoff_id != self.publication.handoff_id
                or definition.source_revision != projection.source_revision
            ):
                raise ValueError("task definition must reference the canonical M1 publication")
        if any(plan.worker_id not in self.worker_ids for plan in self.plan_days):
            raise ValueError("plan day worker must use the canonical worker namespace")
        if any(directive.worker_id not in self.worker_ids for directive in self.directives):
            raise ValueError("directive worker must use the canonical worker namespace")

        validated_directives = []
        for directive in self.directives:
            ack_valid = False
            if (
                directive.delivery_evidence is DeliveryEvidence.ACKED
                and directive.acknowledged_event_id is not None
            ):
                receipt = self.receipt(directive.acknowledged_event_id)
                if receipt is not None:
                    ack_event = receipt.event
                    directive_task = (
                        self.task(directive.task_id)
                        if directive.task_id is not None
                        else None
                    )
                    directive_assignment = (
                        self.assignment(directive.assignment_id)
                        if directive.assignment_id is not None
                        else None
                    )
                    directive_plan = (
                        self.plan_day(directive.plan_day_id)
                        if directive.plan_day_id is not None
                        else None
                    )
                    directive_scope_valid = (
                        directive.job_id in {None, projection.job_id}
                        and (
                            directive.task_id is None
                            or directive_task is not None
                        )
                        and (
                            directive.assignment_id is None
                            or (
                                directive_assignment is not None
                                and directive_assignment.job_id == projection.job_id
                                and all(
                                    member in self.worker_ids
                                    for member in directive_assignment.member_worker_ids
                                )
                                and (
                                    directive.task_id is None
                                    or directive_assignment.task_id == directive.task_id
                                )
                                and (
                                    directive_task is None
                                    or directive_assignment.task_definition_version
                                    == directive_task.definition.definition_version
                                )
                                and directive.worker_id
                                in directive_assignment.member_worker_ids
                            )
                        )
                        and (
                            directive.plan_day_id is None
                            or (
                                directive_plan is not None
                                and directive_plan.worker_id == directive.worker_id
                                and (
                                    directive_assignment is None
                                    or directive.plan_day_id
                                    in directive_assignment.plan_day_ids
                                )
                            )
                        )
                    )
                    scope_pairs = (
                        (ack_event.job_id, directive.job_id),
                        (ack_event.task_id, directive.task_id),
                        (ack_event.assignment_id, directive.assignment_id),
                        (ack_event.plan_day_id, directive.plan_day_id),
                    )
                    ack_valid = (
                        receipt.outcome is ReductionOutcome.APPLIED
                        and directive_scope_valid
                        and ack_event.job_id in {None, projection.job_id}
                        and ack_event.event_type is FieldEventType.WORKER_ACKNOWLEDGED
                        and ack_event.directive_id == directive.directive_id
                        and ack_event.actor_id == directive.worker_id
                        and all(
                            event_value is None
                            or event_value == directive_value
                            for event_value, directive_value in scope_pairs
                        )
                    )
            validated = replace(directive)
            object.__setattr__(validated, "_ack_history_validated", ack_valid)
            validated_directives.append(validated)
        object.__setattr__(self, "directives", tuple(validated_directives))

    def plan_day(self, plan_day_id: str) -> PlanDayState | None:
        return next((item for item in self.plan_days if item.plan_day_id == plan_day_id), None)

    def task(self, task_id: str) -> TaskState | None:
        return next((item for item in self.tasks if item.definition.task_id == task_id), None)

    def assignment(self, assignment_id: str) -> AssignmentState | None:
        return next((item for item in self.assignments if item.assignment_id == assignment_id), None)

    def directive(self, directive_id: str) -> DirectiveState | None:
        return next((item for item in self.directives if item.directive_id == directive_id), None)

    def receipt(self, event_id: str) -> ProcessedEventReceipt | None:
        return next(
            (item for item in self.processed_events if item.event.event_id == event_id),
            None,
        )


@dataclass(frozen=True, slots=True)
class Reduction:
    state: M2Aggregate
    outcome: ReductionOutcome
    emitted_effects: tuple[SystemEffect, ...] = field(default_factory=tuple)
    response_effects: tuple[SystemEffect, ...] = field(default_factory=tuple)
    server_event_id: str | None = None
    missing_requirements: tuple[str, ...] = field(default_factory=tuple)
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    replayed: bool = False


ExplicitInput = FieldEventInput | SystemSignal
