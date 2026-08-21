from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

from werkcrew_ai.domain import (
    Availability,
    Employee,
    JobRequest,
    JobRequirement,
    PlanningWorkItem,
    Vehicle,
)
from werkcrew_ai.field import validate_after_site_visit
from werkcrew_ai.infrastructure.demo_repository import (
    load_demo_job_request,
    load_demo_planning_data,
    load_demo_site_visit_report,
    load_demo_workforce,
)
from werkcrew_ai.planning import (
    evaluate_employee_candidate,
    generate_plan_variants,
    intervals_overlap,
    validate_plan,
)
from werkcrew_ai.planning.crew_planner import (
    REASON_INACTIVE,
    REASON_MISSING_SKILL,
    REASON_NO_AVAILABILITY,
    REASON_TIME_CONFLICT,
    REASON_VEHICLE_UNAVAILABLE,
)


def at(hour: int) -> datetime:
    return datetime(2026, 9, 14, hour, tzinfo=timezone.utc)


def employee(
    *,
    skill_ids: tuple[str, ...] = ("skill-a",),
    is_available: bool = True,
) -> Employee:
    return Employee(
        id="employee-test",
        name="Employee TEST",
        is_active=True,
        skill_ids=skill_ids,
        availability=(Availability(at(8), at(16), is_available, "TEST"),),
        hourly_cost_amount=Decimal("30"),
        cost_currency="EUR",
    )


def work_item(
    item_id: str = "task-a",
    *,
    predecessors: tuple[str, ...] = (),
    vehicle_type: str | None = None,
) -> PlanningWorkItem:
    return PlanningWorkItem(
        id=item_id,
        job_requirement_id=f"req-{item_id}",
        name=f"Task {item_id}",
        required_skill_ids=("skill-a",),
        estimated_hours=Decimal("8"),
        predecessor_ids=predecessors,
        required_vehicle_type=vehicle_type,
    )


def job_for(items: tuple[PlanningWorkItem, ...]) -> JobRequest:
    return JobRequest(
        id="job-test",
        title="Job TEST",
        description="Jawny zakres testowy",
        site_address="Test address",
        desired_start_date=date(2026, 9, 14),
        requirements=tuple(
            JobRequirement(
                id=item.job_requirement_id,
                description=item.name,
                quantity=Decimal("1"),
                unit="scope",
                is_confirmed=True,
            )
            for item in items
        ),
    )


def ready_demo_job() -> JobRequest:
    return validate_after_site_visit(
        load_demo_job_request(), load_demo_site_visit_report()
    ).updated_job_request


def demo_planning_result():
    _, employees, vehicles = load_demo_workforce()
    items, planning_window_end = load_demo_planning_data()
    result = generate_plan_variants(
        ready_demo_job(), items, employees, vehicles, planning_window_end
    )
    return result, items, employees, vehicles


def test_matching_skill_and_availability_make_candidate_eligible() -> None:
    evaluation = evaluate_employee_candidate(work_item(), employee(), at(0), at(23))

    assert evaluation.eligible is True


def test_missing_skill_rejects_candidate_with_reason() -> None:
    evaluation = evaluate_employee_candidate(
        work_item(), employee(skill_ids=("other-skill",)), at(0), at(23)
    )

    assert evaluation.eligible is False
    assert evaluation.reason_code == REASON_MISSING_SKILL


def test_inactive_employee_is_not_eligible() -> None:
    evaluation = evaluate_employee_candidate(
        work_item(), replace(employee(), is_active=False), at(0), at(23)
    )

    assert evaluation.eligible is False
    assert evaluation.reason_code == REASON_INACTIVE


def test_missing_availability_rejects_candidate_with_reason() -> None:
    evaluation = evaluate_employee_candidate(
        work_item(), employee(is_available=False), at(0), at(23)
    )

    assert evaluation.eligible is False
    assert evaluation.reason_code == REASON_NO_AVAILABILITY


def test_time_conflict_never_creates_double_assignment() -> None:
    first = work_item("task-a")
    second = work_item("task-b")
    result = generate_plan_variants(
        job_for((first, second)),
        (first, second),
        (employee(),),
        (),
        date(2026, 9, 14),
    )

    assert result.plans == ()
    assert any(
        entry.reason_code == REASON_TIME_CONFLICT
        for entry in result.decision_trace
    )


def test_unavailable_vehicle_blocks_plan() -> None:
    item = work_item(vehicle_type="cargo_van")
    unavailable_vehicle = Vehicle(
        id="vehicle-test",
        name="Vehicle TEST",
        vehicle_type="cargo_van",
        is_active=True,
        availability=(Availability(at(7), at(17), False, "TEST service"),),
        cost_per_km_amount=Decimal("0.5"),
        cost_currency="EUR",
    )

    result = generate_plan_variants(
        job_for((item,)),
        (item,),
        (employee(),),
        (unavailable_vehicle,),
        date(2026, 9, 14),
    )

    assert result.plans == ()
    assert any(
        entry.reason_code == REASON_VEHICLE_UNAVAILABLE
        for entry in result.decision_trace
    )


def test_dependencies_are_scheduled_in_required_order() -> None:
    result, items, _, _ = demo_planning_result()
    predecessors = {
        item.id: item.predecessor_ids for item in items
    }

    for plan in result.plans:
        assignments = {item.work_item_id: item for item in plan.assignments}
        for item_id, predecessor_ids in predecessors.items():
            for predecessor_id in predecessor_ids:
                assert assignments[predecessor_id].end_at <= assignments[item_id].start_at


def test_plan_a_is_executable() -> None:
    result, items, employees, vehicles = demo_planning_result()

    assert result.plans[0].label == "PLAN A"
    assert validate_plan(result.plans[0], items, employees, vehicles) == ()
    for index, first in enumerate(result.plans[0].assignments):
        for second in result.plans[0].assignments[index + 1 :]:
            if first.employee_id == second.employee_id:
                assert not intervals_overlap(
                    first.start_at, first.end_at, second.start_at, second.end_at
                )


def test_plan_b_is_really_different_from_plan_a() -> None:
    result, _, _, _ = demo_planning_result()

    assert len(result.plans) == 2
    plan_a, plan_b = result.plans
    assert plan_b.label == "PLAN B"
    assert plan_a.assignments != plan_b.assignments
    assert (
        plan_a.employee_ids != plan_b.employee_ids
        or plan_a.start_at != plan_b.start_at
        or plan_a.end_at != plan_b.end_at
    )


def test_planner_does_not_invent_plan_b_when_only_one_schedule_exists() -> None:
    item = work_item()
    result = generate_plan_variants(
        job_for((item,)),
        (item,),
        (employee(),),
        (),
        date(2026, 9, 14),
    )

    assert len(result.plans) == 1
    assert result.plans[0].label == "PLAN A"
    assert any("Plan B nie powstał" in reason for reason in result.inability_reasons)
