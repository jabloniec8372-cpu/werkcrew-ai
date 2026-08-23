"""Small domain model used by the first WERKcrew AI vertical slice."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Skill:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Availability:
    start_at: datetime
    end_at: datetime
    is_available: bool
    note: str = ""


@dataclass(frozen=True, slots=True)
class Employee:
    id: str
    name: str
    is_active: bool
    skill_ids: tuple[str, ...]
    availability: tuple[Availability, ...]
    hourly_cost_amount: Decimal
    cost_currency: str


@dataclass(frozen=True, slots=True)
class Vehicle:
    id: str
    name: str
    vehicle_type: str
    is_active: bool
    availability: tuple[Availability, ...]
    cost_per_km_amount: Decimal
    cost_currency: str
    capacity_note: str = ""


@dataclass(frozen=True, slots=True)
class JobRequirement:
    id: str
    description: str
    quantity: Decimal | None = None
    unit: str | None = None
    is_confirmed: bool = False
    requires_site_verification: bool = False


@dataclass(frozen=True, slots=True)
class JobRequest:
    id: str
    title: str
    description: str
    site_address: str | None
    desired_start_date: date | None
    requirements: tuple[JobRequirement, ...] = field(default_factory=tuple)
    missing_information: tuple[str, ...] = field(default_factory=tuple)
    reported_risks: tuple[str, ...] = field(default_factory=tuple)


class WorkflowState(StrEnum):
    RECEIVED = "RECEIVED"
    SITE_VISIT_REQUIRED = "SITE_VISIT_REQUIRED"
    SITE_VISIT_SCHEDULED = "SITE_VISIT_SCHEDULED"
    SITE_VISIT_COMPLETED = "SITE_VISIT_COMPLETED"
    READY_FOR_PLANNING = "READY_FOR_PLANNING"
    PLANS_READY_FOR_REVIEW = "PLANS_READY_FOR_REVIEW"
    PRICING_READY_FOR_REVIEW = "PRICING_READY_FOR_REVIEW"
    PLAN_APPROVED = "PLAN_APPROVED"
    PLANS_REJECTED = "PLANS_REJECTED"


class OwnerDecisionAction(StrEnum):
    APPROVE_PLAN = "APPROVE_PLAN"
    REJECT_ALL = "REJECT_ALL"


class OwnerDecisionGateStatus(StrEnum):
    PENDING = "PENDING"
    RESUMING = "RESUMING"
    RESOLVED = "RESOLVED"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True, slots=True)
class OwnerDecision:
    decision_id: str
    gate_id: str
    job_request_id: str
    action: OwnerDecisionAction
    selected_plan_id: str | None
    pricing_gate_fingerprint: str
    decided_at: datetime
    actor_role: str = "OWNER"
    source: str = "COORDINATOR_UI"


@dataclass(frozen=True, slots=True)
class PendingOwnerDecisionGate:
    gate_id: str
    workflow_instance_id: str
    job_request_id: str
    pricing_gate_fingerprint: str
    eligible_plan_ids: tuple[str, ...]
    session_id: str
    agent_id: str
    interrupt_id: str | None
    status: OwnerDecisionGateStatus
    response_fingerprint: str | None
    decision_id: str | None
    created_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class SiteVisitBrief:
    job_request: JobRequest
    missing_information: tuple[str, ...]
    detected_risks: tuple[str, ...]
    verification_items: tuple[str, ...]
    checkpoints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SiteMeasurement:
    requirement_id: str
    quantity: Decimal
    unit: str


@dataclass(frozen=True, slots=True)
class SiteVisitReport:
    site_visit_id: str
    measured_dimensions: str
    measurements: tuple[SiteMeasurement, ...]
    substrate_condition: str
    moisture_findings: str
    access_conditions: str
    installation_findings: str
    notes: str
    unresolved_risk: bool
    unresolved_risk_details: str = ""


@dataclass(frozen=True, slots=True)
class SiteVisit:
    """A scheduled or completed visit within the explicitly bounded M2 workflow."""

    id: str
    job_request_id: str
    status: WorkflowState
    brief: SiteVisitBrief
    scheduled_at: datetime | None = None
    assigned_employee_id: str | None = None
    report: SiteVisitReport | None = None


@dataclass(frozen=True, slots=True)
class PostVisitValidation:
    workflow_state: WorkflowState
    updated_job_request: JobRequest
    remaining_missing_information: tuple[str, ...]
    unresolved_risks: tuple[str, ...]
    rationale: str

    @property
    def ready_for_planning(self) -> bool:
        return self.workflow_state is WorkflowState.READY_FOR_PLANNING


@dataclass(frozen=True, slots=True)
class PlanningWorkItem:
    id: str
    job_requirement_id: str
    name: str
    required_skill_ids: tuple[str, ...]
    estimated_hours: Decimal
    predecessor_ids: tuple[str, ...] = field(default_factory=tuple)
    required_vehicle_type: str | None = None


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    work_item_id: str
    work_item_name: str
    employee_id: str
    start_at: datetime
    end_at: datetime
    vehicle_id: str | None = None


@dataclass(frozen=True, slots=True)
class PlanVariant:
    id: str
    job_request_id: str
    label: str
    start_at: datetime
    end_at: datetime
    assignments: tuple[ScheduledTask, ...]
    employee_ids: tuple[str, ...]
    vehicle_ids: tuple[str, ...]
    rationale: str
    limitations: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DecisionTraceEntry:
    work_item_id: str
    work_item_name: str
    candidate_type: str
    candidate_id: str
    candidate_name: str
    outcome: str
    reason_code: str
    reason: str


@dataclass(frozen=True, slots=True)
class PlanningResult:
    plans: tuple[PlanVariant, ...]
    decision_trace: tuple[DecisionTraceEntry, ...]
    inability_reasons: tuple[str, ...]
    rule_version: str = "crew-planner-v1"
