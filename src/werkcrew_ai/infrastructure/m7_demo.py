"""Explicit DEMO_SYNTHETIC fixtures for M7 acceptance scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from werkcrew_ai.agent.session import WERKCREW_AGENT_ID, workflow_session_id
from werkcrew_ai.dispatch import (
    COMPANY_TIMEZONE,
    WORKFLOW_SNAPSHOT_SCHEMA_VERSION,
    AddressStatus,
    AvailabilityWindow,
    CalendarAssignment,
    CalendarStatus,
    ConfirmedCoordinates,
    DispatchVehicle,
    DispatchWorker,
    MaterialReadiness,
    MaterialReadinessStatus,
    PersistentJob,
    RouteSnapshot,
    RouteSnapshotStatus,
    StructuredAddress,
)
from werkcrew_ai.persistence import SqliteBusinessRepository
from werkcrew_ai.routing import location_fingerprint


M7_PLANNING_DATE = date(2026, 9, 14)
M7_NOW = datetime(2026, 9, 14, 7, 0, tzinfo=COMPANY_TIMEZONE)
M7_WORKDAY_START = datetime.combine(M7_PLANNING_DATE, time(8, 0), COMPANY_TIMEZONE)
M7_WORKDAY_END = datetime.combine(M7_PLANNING_DATE, time(16, 0), COMPANY_TIMEZONE)
M7_WORKER_ID = "worker-x-demo"
M7_VEHICLE_ID = "vehicle-dispatch-demo"


@dataclass(frozen=True, slots=True)
class M7DemoIds:
    workflow_a: str
    workflow_b: str
    workflow_c: str
    assignment_b: str
    assignment_c: str
    task_b: str
    task_c: str


def demo_jobs() -> tuple[PersistentJob, ...]:
    return (
        PersistentJob(
            "job-a-demo",
            "Owner review DEMO",
            StructuredAddress("Alexanderplatz", "1", "10178", "Berlin", "DE"),
            AddressStatus.CONFIRMED,
            ConfirmedCoordinates("52.52190", "13.41320", "DEMO_PRECONFIRMED"),
        ),
        PersistentJob(
            "job-b-demo",
            "Material-delayed task DEMO",
            StructuredAddress("Potsdamer Platz", "1", "10785", "Berlin", "DE"),
            AddressStatus.CONFIRMED,
            ConfirmedCoordinates("52.50960", "13.37600", "DEMO_PRECONFIRMED"),
        ),
        PersistentJob(
            "job-c-demo",
            "Alternative ready task DEMO",
            StructuredAddress("Invalidenstrasse", "117", "10115", "Berlin", "DE"),
            AddressStatus.CONFIRMED,
            ConfirmedCoordinates("52.53080", "13.37770", "DEMO_PRECONFIRMED"),
        ),
    )


def seed_m7_demo(
    repository: SqliteBusinessRepository,
    *,
    now: datetime = M7_NOW,
    include_reverse_route_fixture: bool = True,
) -> M7DemoIds:
    """Create exactly three isolated workflows and the coherent B/C dispatch fixture."""

    repository.initialize(now=now)
    jobs = demo_jobs()
    workflows = {
        "job-a-demo": "workflow-job-a-demo-v1",
        "job-b-demo": "workflow-job-b-demo-v1",
        "job-c-demo": "workflow-job-c-demo-v1",
    }
    states = {
        "job-a-demo": "PRICING_READY_FOR_REVIEW",
        "job-b-demo": "PLAN_APPROVED",
        "job-c-demo": "PLAN_APPROVED",
    }
    for job in jobs:
        workflow_id = workflows[job.job_id]
        repository.create_job_workflow(
            job=job,
            workflow_instance_id=workflow_id,
            state=states[job.job_id],
            snapshot={
                "data_classification": "DEMO_SYNTHETIC",
                "job_local_status": states[job.job_id],
            },
            schema_version=WORKFLOW_SNAPSHOT_SCHEMA_VERSION,
            now=now,
            session_id=workflow_session_id(job.job_id, workflow_id),
            agent_id=WERKCREW_AGENT_ID,
        )

    worker = DispatchWorker(
        M7_WORKER_ID,
        ("tiling",),
        (AvailabilityWindow(M7_WORKDAY_START, M7_WORKDAY_END),),
    )
    vehicle = DispatchVehicle(
        M7_VEHICLE_ID,
        (AvailabilityWindow(M7_WORKDAY_START, M7_WORKDAY_END),),
    )
    repository.put_worker(worker, now=now)
    repository.put_vehicle(vehicle, now=now)

    task_b = "task-job-b-existing"
    task_c = "task-job-c-existing"
    assignment_b = CalendarAssignment(
        "assignment-job-b",
        "job-b-demo",
        workflows["job-b-demo"],
        task_b,
        "Existing material-dependent task B",
        M7_WORKER_ID,
        ("tiling",),
        M7_VEHICLE_ID,
        datetime(2026, 9, 14, 8, 0, tzinfo=COMPANY_TIMEZONE),
        datetime(2026, 9, 14, 10, 0, tzinfo=COMPANY_TIMEZONE),
        datetime(2026, 9, 14, 14, 0, tzinfo=COMPANY_TIMEZONE),
        CalendarStatus.CONFIRMED,
    )
    assignment_c = CalendarAssignment(
        "assignment-job-c",
        "job-c-demo",
        workflows["job-c-demo"],
        task_c,
        "Existing ready alternative task C",
        M7_WORKER_ID,
        ("tiling",),
        M7_VEHICLE_ID,
        datetime(2026, 9, 14, 10, 30, tzinfo=COMPANY_TIMEZONE),
        datetime(2026, 9, 14, 12, 30, tzinfo=COMPANY_TIMEZONE),
        datetime(2026, 9, 14, 15, 30, tzinfo=COMPANY_TIMEZONE),
        CalendarStatus.CONFIRMED,
    )
    repository.add_calendar_assignment(assignment_b, now=now)
    repository.add_calendar_assignment(assignment_c, now=now)
    repository.put_material_readiness(
        MaterialReadiness(
            "job-b-demo",
            task_b,
            MaterialReadinessStatus.READY,
            now,
            True,
            "CONTROLLED_DEMO_INPUT",
            now,
        )
    )
    repository.put_material_readiness(
        MaterialReadiness(
            "job-c-demo",
            task_c,
            MaterialReadinessStatus.READY,
            now,
            True,
            "CONTROLLED_DEMO_INPUT",
            now,
        )
    )

    job_b = jobs[1]
    job_c = jobs[2]
    route_fixtures = [(job_b, job_c, 1500, 14000)]
    if include_reverse_route_fixture:
        route_fixtures.append((job_c, job_b, 1200, 13500))
    for origin, destination, seconds, meters in route_fixtures:
        origin_fp = location_fingerprint(origin)
        destination_fp = location_fingerprint(destination)
        input_fp = f"fixture:{origin.job_id}:{destination.job_id}:2026-09-14"
        repository.save_route_snapshot(
            RouteSnapshot(
                route_snapshot_id=f"route-{origin.job_id}-to-{destination.job_id}",
                origin_reference=origin.job_id,
                origin_fingerprint=origin_fp,
                destination_reference=destination.job_id,
                destination_fingerprint=destination_fp,
                transport_mode="Car",
                departure_time_basis="2026-09-14 DEMO planning day",
                distance_meters=meters,
                travel_duration_seconds=seconds,
                provider="DEMO_SYNTHETIC_ROUTE_FIXTURE",
                retrieved_at=now,
                status=RouteSnapshotStatus.VALID,
                input_fingerprint=input_fp,
            )
        )
    return M7DemoIds(
        workflows["job-a-demo"],
        workflows["job-b-demo"],
        workflows["job-c-demo"],
        assignment_b.assignment_id,
        assignment_c.assignment_id,
        task_b,
        task_c,
    )
