"""Persistent M7 dispatch domain models with explicit time and ownership."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo


COMPANY_TIMEZONE_NAME = "Europe/Berlin"
COMPANY_TIMEZONE = ZoneInfo(COMPANY_TIMEZONE_NAME)
DISPATCH_RULE_VERSION = "daily-dispatch-v1"
WORKFLOW_SNAPSHOT_SCHEMA_VERSION = 1


def require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class AddressStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    UNCONFIRMED = "UNCONFIRMED"


@dataclass(frozen=True, slots=True)
class StructuredAddress:
    street: str
    house_number: str
    postal_code: str
    city: str
    country: str


@dataclass(frozen=True, slots=True)
class ConfirmedCoordinates:
    latitude: str
    longitude: str
    source: str


@dataclass(frozen=True, slots=True)
class PersistentJob:
    job_id: str
    title: str
    address: StructuredAddress
    address_status: AddressStatus
    coordinates: ConfirmedCoordinates | None


@dataclass(frozen=True, slots=True)
class PersistentWorkflow:
    workflow_instance_id: str
    job_id: str
    revision: int
    schema_version: int
    state: str
    snapshot: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SessionBinding:
    workflow_instance_id: str
    session_id: str
    agent_id: str
    created_at: datetime


class CalendarStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class CalendarAssignment:
    assignment_id: str
    job_id: str
    workflow_instance_id: str
    scheduled_task_id: str
    work_item_name: str
    worker_id: str
    required_skill_ids: tuple[str, ...]
    vehicle_id: str | None
    start_at: datetime
    end_at: datetime
    hard_deadline: datetime | None
    status: CalendarStatus
    revision: int = 0

    def __post_init__(self) -> None:
        require_aware(self.start_at, "start_at")
        require_aware(self.end_at, "end_at")
        if self.end_at <= self.start_at:
            raise ValueError("Calendar assignment duration must be positive")
        if self.hard_deadline is not None:
            require_aware(self.hard_deadline, "hard_deadline")


@dataclass(frozen=True, slots=True)
class AvailabilityWindow:
    start_at: datetime
    end_at: datetime

    def __post_init__(self) -> None:
        require_aware(self.start_at, "availability.start_at")
        require_aware(self.end_at, "availability.end_at")
        if self.end_at <= self.start_at:
            raise ValueError("Availability duration must be positive")


@dataclass(frozen=True, slots=True)
class DispatchWorker:
    worker_id: str
    skill_ids: tuple[str, ...]
    availability: tuple[AvailabilityWindow, ...]


@dataclass(frozen=True, slots=True)
class DispatchVehicle:
    vehicle_id: str
    availability: tuple[AvailabilityWindow, ...]


class MaterialReadinessStatus(StrEnum):
    READY = "READY"
    EXPECTED = "EXPECTED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class MaterialReadiness:
    job_id: str
    scheduled_task_id: str
    status: MaterialReadinessStatus
    available_at: datetime | None
    blocking: bool
    source: str
    updated_at: datetime
    revision: int = 0

    def __post_init__(self) -> None:
        require_aware(self.updated_at, "material.updated_at")
        if self.available_at is not None:
            require_aware(self.available_at, "material.available_at")
        if (
            self.blocking
            and self.status is MaterialReadinessStatus.EXPECTED
            and self.available_at is None
        ):
            raise ValueError("Blocking EXPECTED material requires available_at")


class RouteSnapshotStatus(StrEnum):
    VALID = "VALID"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class RouteSnapshot:
    route_snapshot_id: str
    origin_reference: str
    origin_fingerprint: str
    destination_reference: str
    destination_fingerprint: str
    transport_mode: str
    departure_time_basis: str
    distance_meters: int
    travel_duration_seconds: int
    provider: str
    retrieved_at: datetime
    status: RouteSnapshotStatus
    input_fingerprint: str

    def __post_init__(self) -> None:
        require_aware(self.retrieved_at, "route.retrieved_at")
        if self.distance_meters < 0 or self.travel_duration_seconds < 0:
            raise ValueError("Route distance and duration cannot be negative")


@dataclass(frozen=True, slots=True)
class SchedulePlacement:
    assignment_id: str
    job_id: str
    scheduled_task_id: str
    worker_id: str
    vehicle_id: str | None
    start_at: datetime
    end_at: datetime


class ReplanProposalStatus(StrEnum):
    PENDING_OWNER_APPROVAL = "PENDING_OWNER_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    STALE = "STALE"
    APPLIED = "APPLIED"


@dataclass(frozen=True, slots=True)
class ReplanProposal:
    proposal_id: str
    initiating_workflow_instance_id: str
    status: ReplanProposalStatus
    affected_job_ids: tuple[str, ...]
    affected_workflow_instance_ids: tuple[str, ...]
    affected_assignment_ids: tuple[str, ...]
    before_schedule: tuple[SchedulePlacement, ...]
    after_schedule: tuple[SchedulePlacement, ...]
    reason_codes: tuple[str, ...]
    material_readiness_references: tuple[str, ...]
    route_snapshot_references: tuple[str, ...]
    expected_workflow_revisions: tuple[tuple[str, int], ...]
    expected_calendar_revisions: tuple[tuple[str, int], ...]
    worker_idle_time_before_seconds: int
    worker_idle_time_after_seconds: int
    travel_time_before_seconds: int
    travel_time_after_seconds: int
    deadline_impact: str
    conditional: bool
    proposal_fingerprint: str
    created_at: datetime


class DispatchPlanStatus(StrEnum):
    PROPOSAL_CREATED = "PROPOSAL_CREATED"
    NO_FEASIBLE_REPLAN = "NO_FEASIBLE_REPLAN"
    NO_CHANGE = "NO_CHANGE"


@dataclass(frozen=True, slots=True)
class DispatchPlanResult:
    status: DispatchPlanStatus
    proposal: ReplanProposal | None
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    public_summary: str = ""


class PersistentGateStatus(StrEnum):
    PENDING = "PENDING"
    RESUMING = "RESUMING"
    RESOLVED = "RESOLVED"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True, slots=True)
class PersistentGate:
    gate_id: str
    workflow_instance_id: str
    gate_type: str
    proposal_id: str | None
    gate_fingerprint: str
    session_id: str
    agent_id: str
    interrupt_id: str | None
    status: PersistentGateStatus
    response_fingerprint: str | None
    decision_id: str | None
    created_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class PersistentOwnerDecision:
    decision_id: str
    gate_id: str
    workflow_instance_id: str
    proposal_id: str | None
    action: str
    selected_plan_id: str | None
    decision_fingerprint: str
    decided_at: datetime
    actor_role: str
    source: str


class TraceActor(StrEnum):
    AGENT = "AGENT"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"
    TOOL = "TOOL"


@dataclass(frozen=True, slots=True)
class AgentTraceEvent:
    event_id: str
    timestamp: datetime
    job_id: str
    workflow_instance_id: str
    actor: TraceActor
    action: str
    redacted_input_summary: str
    result_summary: str
    previous_state: str | None
    next_state: str | None
    reason_code: str | None
    rule_version: str | None
    external_data_source: str | None
    success: bool
    gate_id: str | None
    owner_decision_id: str | None
    proposal_id: str | None
    operation_id: str | None
    correlation_id: str


@dataclass(frozen=True, slots=True)
class ReplanApprovalClaim:
    canonical_response: dict[str, Any] | None
    interrupt_id: str | None
    existing_decision: PersistentOwnerDecision | None
