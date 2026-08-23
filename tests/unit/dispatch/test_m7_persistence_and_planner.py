from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from werkcrew_ai.dispatch import (
    COMPANY_TIMEZONE,
    AddressStatus,
    AgentTraceEvent,
    CalendarStatus,
    DispatchPlanStatus,
    MaterialReadiness,
    MaterialReadinessStatus,
    ReplanProposalStatus,
    TraceActor,
)
from werkcrew_ai.dispatch.service import PersistentDispatchService
from werkcrew_ai.infrastructure.m7_demo import (
    M7_NOW,
    M7_PLANNING_DATE,
    M7_WORKDAY_END,
    M7_WORKDAY_START,
    M7_WORKER_ID,
    demo_jobs,
    seed_m7_demo,
)
from werkcrew_ai.persistence import (
    CrossJobReferenceError,
    SqliteBusinessRepository,
    StaleRevisionError,
)
from werkcrew_ai.routing import AmazonLocationRouteProvider, AmazonLocationSettings


@pytest.fixture
def repository(tmp_path: Path) -> SqliteBusinessRepository:
    value = SqliteBusinessRepository(tmp_path / "business.db")
    seed_m7_demo(value)
    return value


def delayed_replan(repository: SqliteBusinessRepository):
    ids = seed_ids(repository)
    service = PersistentDispatchService(repository)
    service.record_material_delay(
        job_id="job-b-demo",
        scheduled_task_id=ids.task_b,
        expected_revision=0,
        available_at=datetime(2026, 9, 14, 11, 0, tzinfo=COMPANY_TIMEZONE),
        now=M7_NOW,
    )
    result = service.propose_dispatch_replan(
        initiating_workflow_instance_id=ids.workflow_b,
        affected_job_ids=("job-b-demo", "job-c-demo"),
        worker_id=M7_WORKER_ID,
        planning_date=M7_PLANNING_DATE,
        now=M7_NOW,
        workday_start=M7_WORKDAY_START,
        workday_end=M7_WORKDAY_END,
    )
    return ids, service, result


def seed_ids(repository: SqliteBusinessRepository):
    workflows = repository.workflows_for_jobs(
        ("job-a-demo", "job-b-demo", "job-c-demo")
    )
    by_job = {item.job_id: item.workflow_instance_id for item in workflows}
    assignments = repository.calendar_for_jobs(("job-b-demo", "job-c-demo"))
    by_job_assignment = {item.job_id: item for item in assignments}
    from werkcrew_ai.infrastructure.m7_demo import M7DemoIds

    return M7DemoIds(
        by_job["job-a-demo"],
        by_job["job-b-demo"],
        by_job["job-c-demo"],
        by_job_assignment["job-b-demo"].assignment_id,
        by_job_assignment["job-c-demo"].assignment_id,
        by_job_assignment["job-b-demo"].scheduled_task_id,
        by_job_assignment["job-c-demo"].scheduled_task_id,
    )


def test_sqlite_schema_has_required_m7_tables(repository) -> None:
    with repository._connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "jobs",
        "workflow_instances",
        "session_bindings",
        "pending_gates",
        "owner_decisions",
        "calendar_assignments",
        "material_readiness",
        "route_snapshots",
        "replan_proposals",
        "agent_trace_events",
    }.issubset(names)
    assert len(repository.list_jobs()) == 3
    assert all(item.schema_version == 1 for item in repository.workflows_for_jobs(tuple(j.job_id for j in demo_jobs())))


def test_snapshot_rejects_authoritative_cross_job_duplicate(repository) -> None:
    workflow = repository.get_workflow("workflow-job-a-demo-v1")
    with pytest.raises(ValueError, match="normalized cross-job facts"):
        repository.update_workflow_cas(
            workflow_instance_id=workflow.workflow_instance_id,
            expected_revision=workflow.revision,
            state=workflow.state,
            snapshot={"calendar_assignments": []},
            now=M7_NOW,
        )


def test_workflow_compare_and_swap_rejects_stale_revision(repository) -> None:
    workflow = repository.get_workflow("workflow-job-c-demo-v1")
    updated = repository.update_workflow_cas(
        workflow_instance_id=workflow.workflow_instance_id,
        expected_revision=0,
        state=workflow.state,
        snapshot=workflow.snapshot,
        now=M7_NOW,
    )
    assert updated.revision == 1
    with pytest.raises(StaleRevisionError, match="STALE"):
        repository.update_workflow_cas(
            workflow_instance_id=workflow.workflow_instance_id,
            expected_revision=0,
            state=workflow.state,
            snapshot=workflow.snapshot,
            now=M7_NOW,
        )


def test_cross_job_material_reference_is_rejected(repository) -> None:
    with pytest.raises(CrossJobReferenceError):
        repository.put_material_readiness(
            MaterialReadiness(
                "job-a-demo",
                "task-job-b-existing",
                MaterialReadinessStatus.READY,
                M7_NOW,
                True,
                "CONTROLLED_DEMO_INPUT",
                M7_NOW,
            )
        )


@pytest.mark.parametrize(
    ("status", "available_at", "blocking", "valid"),
    [
        (MaterialReadinessStatus.READY, None, True, True),
        (MaterialReadinessStatus.EXPECTED, None, True, False),
        (MaterialReadinessStatus.EXPECTED, M7_NOW, True, True),
        (MaterialReadinessStatus.BLOCKED, None, True, True),
        (MaterialReadinessStatus.BLOCKED, None, False, True),
    ],
)
def test_material_readiness_semantics(status, available_at, blocking, valid) -> None:
    if valid:
        MaterialReadiness(
            "job",
            "task",
            status,
            available_at,
            blocking,
            "TEST",
            M7_NOW,
        )
    else:
        with pytest.raises(ValueError, match="available_at"):
            MaterialReadiness(
                "job",
                "task",
                status,
                available_at,
                blocking,
                "TEST",
                M7_NOW,
            )


def test_main_scenario_deterministically_proposes_c_then_b(repository) -> None:
    _, _, result = delayed_replan(repository)
    assert result.status is DispatchPlanStatus.PROPOSAL_CREATED
    proposal = result.proposal
    assert [item.job_id for item in proposal.before_schedule] == [
        "job-b-demo",
        "job-c-demo",
    ]
    assert [item.job_id for item in proposal.after_schedule] == [
        "job-c-demo",
        "job-b-demo",
    ]
    assert proposal.worker_idle_time_after_seconds < proposal.worker_idle_time_before_seconds
    assert proposal.travel_time_after_seconds == 1200
    assert proposal.travel_time_before_seconds == 1500
    assert proposal.conditional is True
    assert "HARD_DEADLINE_PRESERVED" in proposal.reason_codes
    assert proposal.status is ReplanProposalStatus.PENDING_OWNER_APPROVAL


def test_identical_inputs_return_identical_immutable_proposal(repository) -> None:
    _, service, first = delayed_replan(repository)
    second = service.propose_dispatch_replan(
        initiating_workflow_instance_id="workflow-job-b-demo-v1",
        affected_job_ids=("job-b-demo", "job-c-demo"),
        worker_id=M7_WORKER_ID,
        planning_date=M7_PLANNING_DATE,
        now=M7_NOW,
        workday_start=M7_WORKDAY_START,
        workday_end=M7_WORKDAY_END,
    )
    assert second.proposal == first.proposal
    assert len(repository.list_proposals()) == 1


def test_true_hard_deadline_violation_is_not_ranked(repository) -> None:
    ids = seed_ids(repository)
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE calendar_assignments SET hard_deadline=? WHERE assignment_id=?",
            (
                datetime(2026, 9, 14, 10, 30, tzinfo=COMPANY_TIMEZONE).isoformat(),
                ids.assignment_b,
            ),
        )
    service = PersistentDispatchService(repository)
    service.record_material_delay(
        job_id="job-b-demo",
        scheduled_task_id=ids.task_b,
        expected_revision=0,
        available_at=datetime(2026, 9, 14, 11, 0, tzinfo=COMPANY_TIMEZONE),
        now=M7_NOW,
    )
    result = service.propose_dispatch_replan(
        initiating_workflow_instance_id=ids.workflow_b,
        affected_job_ids=("job-b-demo", "job-c-demo"),
        worker_id=M7_WORKER_ID,
        planning_date=M7_PLANNING_DATE,
        now=M7_NOW,
        workday_start=M7_WORKDAY_START,
        workday_end=M7_WORKDAY_END,
    )
    assert result.status is DispatchPlanStatus.NO_FEASIBLE_REPLAN
    assert "HARD_DEADLINE_VIOLATION" in result.reason_codes
    assert repository.list_proposals() == ()


def test_blocked_material_prevents_replan_but_nonblocking_does_not(repository) -> None:
    ids = seed_ids(repository)
    repository.update_material_readiness_cas(
        job_id="job-b-demo",
        scheduled_task_id=ids.task_b,
        expected_revision=0,
        status=MaterialReadinessStatus.BLOCKED,
        available_at=None,
        blocking=True,
        source="CONTROLLED_DEMO_INPUT",
        now=M7_NOW,
    )
    service = PersistentDispatchService(repository)
    blocked = service.propose_dispatch_replan(
        initiating_workflow_instance_id=ids.workflow_b,
        affected_job_ids=("job-b-demo", "job-c-demo"),
        worker_id=M7_WORKER_ID,
        planning_date=M7_PLANNING_DATE,
        now=M7_NOW,
        workday_start=M7_WORKDAY_START,
        workday_end=M7_WORKDAY_END,
    )
    assert blocked.status is DispatchPlanStatus.NO_FEASIBLE_REPLAN
    assert "MATERIAL_BLOCKED" in blocked.reason_codes
    repository.update_material_readiness_cas(
        job_id="job-b-demo",
        scheduled_task_id=ids.task_b,
        expected_revision=1,
        status=MaterialReadinessStatus.BLOCKED,
        available_at=None,
        blocking=False,
        source="CONTROLLED_DEMO_INPUT",
        now=M7_NOW,
    )
    nonblocking = service.propose_dispatch_replan(
        initiating_workflow_instance_id=ids.workflow_b,
        affected_job_ids=("job-b-demo", "job-c-demo"),
        worker_id=M7_WORKER_ID,
        planning_date=M7_PLANNING_DATE,
        now=M7_NOW,
        workday_start=M7_WORKDAY_START,
        workday_end=M7_WORKDAY_END,
    )
    assert "MATERIAL_BLOCKED" not in nonblocking.reason_codes


def test_in_progress_assignment_is_never_moved(repository) -> None:
    ids = seed_ids(repository)
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE calendar_assignments SET status='IN_PROGRESS' WHERE assignment_id=?",
            (ids.assignment_c,),
        )
    result = PersistentDispatchService(repository).propose_dispatch_replan(
        initiating_workflow_instance_id=ids.workflow_b,
        affected_job_ids=("job-b-demo", "job-c-demo"),
        worker_id=M7_WORKER_ID,
        planning_date=M7_PLANNING_DATE,
        now=M7_NOW,
        workday_start=M7_WORKDAY_START,
        workday_end=M7_WORKDAY_END,
    )
    assert result.status is DispatchPlanStatus.NO_FEASIBLE_REPLAN
    assert result.reason_codes == ("IN_PROGRESS_IMMOVABLE",)


def test_unconfirmed_address_and_missing_route_are_hard_constraints(repository) -> None:
    ids = seed_ids(repository)
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET address_status='UNCONFIRMED' WHERE job_id='job-c-demo'"
        )
    result = PersistentDispatchService(repository).propose_dispatch_replan(
        initiating_workflow_instance_id=ids.workflow_b,
        affected_job_ids=("job-b-demo", "job-c-demo"),
        worker_id=M7_WORKER_ID,
        planning_date=M7_PLANNING_DATE,
        now=M7_NOW,
        workday_start=M7_WORKDAY_START,
        workday_end=M7_WORKDAY_END,
    )
    assert result.status is DispatchPlanStatus.NO_FEASIBLE_REPLAN
    assert result.reason_codes == ("ADDRESS_NOT_CONFIRMED",)


def test_planner_rejects_naive_hidden_time(repository) -> None:
    ids = seed_ids(repository)
    service = PersistentDispatchService(repository)
    with pytest.raises(ValueError, match="timezone-aware"):
        service.propose_dispatch_replan(
            initiating_workflow_instance_id=ids.workflow_b,
            affected_job_ids=("job-b-demo", "job-c-demo"),
            worker_id=M7_WORKER_ID,
            planning_date=M7_PLANNING_DATE,
            now=datetime(2026, 9, 14, 7, 0),
            workday_start=M7_WORKDAY_START,
            workday_end=M7_WORKDAY_END,
        )


def test_trace_redacts_address_coordinates_and_private_reasoning(repository) -> None:
    workflow = repository.get_workflow("workflow-job-b-demo-v1")
    base = AgentTraceEvent(
        "trace-safe",
        M7_NOW,
        "job-b-demo",
        workflow.workflow_instance_id,
        TraceActor.SYSTEM,
        "Safe fact",
        "Task readiness only.",
        "Recorded.",
        workflow.state,
        workflow.state,
        "ROUTE_FEASIBLE",
        "daily-dispatch-v1",
        None,
        True,
        None,
        None,
        None,
        None,
        "safe",
    )
    repository.append_trace(base)
    with pytest.raises(ValueError, match="address or exact coordinates"):
        repository.append_trace(
            replace(base, event_id="trace-address", result_summary="Potsdamer Platz 1")
        )
    with pytest.raises(ValueError, match="private content"):
        repository.append_trace(
            replace(base, event_id="trace-cot", result_summary="chain-of-thought")
        )


class FakeRoutesClient:
    def __init__(self):
        self.requests = []

    def calculate_routes(self, **kwargs):
        self.requests.append(kwargs)
        return {"Routes": [{"Summary": {"Distance": 12345.4, "Duration": 987.2}}]}


class FakeSession:
    def __init__(self, client):
        self._client = client

    def client(self, name, region_name):
        assert name == "geo-routes"
        assert region_name == "eu-central-1"
        return self._client


def test_amazon_location_adapter_returns_only_business_route_snapshot() -> None:
    client = FakeRoutesClient()
    provider = AmazonLocationRouteProvider(
        AmazonLocationSettings("eu-central-1"),
        session_factory=lambda **_: FakeSession(client),
    )
    route = provider.calculate_route(
        origin=demo_jobs()[1],
        destination=demo_jobs()[2],
        departure_time=M7_WORKDAY_START,
        retrieved_at=M7_NOW,
    )
    assert route.provider == "AMAZON_LOCATION_CALCULATE_ROUTES"
    assert route.distance_meters == 12345
    assert route.travel_duration_seconds == 987
    assert len(client.requests) == 1
    assert not hasattr(route, "raw_response")
