"""Read-only loader for synthetic DEMO data kept in the repository."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from werkcrew_ai.domain import (
    Availability,
    Employee,
    JobRequest,
    JobRequirement,
    Skill,
    Vehicle,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEMO_DATA_DIR = REPOSITORY_ROOT / "data" / "demo"


def _read_demo_json(filename: str) -> dict[str, Any]:
    with (DEMO_DATA_DIR / filename).open(encoding="utf-8") as source:
        payload = json.load(source)
    if payload.get("data_classification") != "DEMO_SYNTHETIC":
        raise ValueError(f"{filename} is not explicitly classified as DEMO_SYNTHETIC")
    return payload


def _availability(item: dict[str, Any]) -> Availability:
    return Availability(
        start_at=datetime.fromisoformat(item["start_at"]),
        end_at=datetime.fromisoformat(item["end_at"]),
        is_available=item["is_available"],
        note=item.get("note", ""),
    )


def load_demo_workforce() -> tuple[
    tuple[Skill, ...], tuple[Employee, ...], tuple[Vehicle, ...]
]:
    payload = _read_demo_json("DEMO_workforce.json")
    skills = tuple(Skill(**item) for item in payload["skills"])
    employees = tuple(
        Employee(
            id=item["id"],
            name=item["name"],
            is_active=item["is_active"],
            skill_ids=tuple(item["skill_ids"]),
            availability=tuple(_availability(slot) for slot in item["availability"]),
            hourly_cost_amount=Decimal(item["hourly_cost_amount"]),
            cost_currency=item["cost_currency"],
        )
        for item in payload["employees"]
    )
    vehicles = tuple(
        Vehicle(
            id=item["id"],
            name=item["name"],
            vehicle_type=item["vehicle_type"],
            is_active=item["is_active"],
            availability=tuple(_availability(slot) for slot in item["availability"]),
            cost_per_km_amount=Decimal(item["cost_per_km_amount"]),
            cost_currency=item["cost_currency"],
            capacity_note=item.get("capacity_note", ""),
        )
        for item in payload["vehicles"]
    )
    return skills, employees, vehicles


def load_demo_job_request() -> JobRequest:
    payload = _read_demo_json("DEMO_job_request.json")["job_request"]
    return JobRequest(
        id=payload["id"],
        title=payload["title"],
        description=payload["description"],
        site_address=payload.get("site_address"),
        desired_start_date=(
            date.fromisoformat(payload["desired_start_date"])
            if payload.get("desired_start_date")
            else None
        ),
        requirements=tuple(
            JobRequirement(
                id=item["id"],
                description=item["description"],
                quantity=(
                    Decimal(item["quantity"])
                    if item.get("quantity") is not None
                    else None
                ),
                unit=item.get("unit"),
                is_confirmed=item.get("is_confirmed", False),
                requires_site_verification=item.get(
                    "requires_site_verification", False
                ),
            )
            for item in payload["requirements"]
        ),
        missing_information=tuple(payload.get("missing_information", [])),
        reported_risks=tuple(payload.get("reported_risks", [])),
    )
