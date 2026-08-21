"""Small domain model used by the first WERKcrew AI vertical slice."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


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


@dataclass(frozen=True, slots=True)
class SiteVisit:
    """Minimal identity for a future visit; workflow and Peter's role remain open."""

    id: str
    job_request_id: str
    scheduled_at: datetime | None = None
    assigned_employee_id: str | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class PlanVariant:
    """Minimal plan container without assigning Plan A/Plan B semantics."""

    id: str
    job_request_id: str
    label: str
    employee_ids: tuple[str, ...] = field(default_factory=tuple)
    vehicle_ids: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""
