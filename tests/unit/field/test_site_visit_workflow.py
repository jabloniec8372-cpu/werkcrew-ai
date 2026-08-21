from dataclasses import replace

from werkcrew_ai.domain import WorkflowState
from werkcrew_ai.field import (
    assign_site_assessor,
    build_site_visit_brief,
    validate_after_site_visit,
)
from werkcrew_ai.infrastructure.demo_repository import (
    load_demo_job_request,
    load_demo_site_visit_report,
    load_demo_workforce,
)
from werkcrew_ai.planning import assess_job_request


def test_brief_is_built_from_assessment_gaps_and_risks() -> None:
    job_request = load_demo_job_request()
    assessment = assess_job_request(job_request)

    brief = build_site_visit_brief(job_request, assessment)

    assert brief.job_request == job_request
    assert brief.missing_information == assessment.missing_information
    assert brief.detected_risks == assessment.detected_risks
    assert "Hydroizolacja stref mokrych" in brief.verification_items
    assert any("wilgoci" in checkpoint for checkpoint in brief.checkpoints)


def test_assignment_uses_required_skill_activity_and_availability() -> None:
    job_request = load_demo_job_request()
    _, employees, _ = load_demo_workforce()

    assignment = assign_site_assessor(employees, job_request.desired_start_date)

    assert assignment.assigned is True
    assert assignment.employee is not None
    assert assignment.employee.id == "emp-peter-demo"
    assert "site-assessment" in assignment.employee.skill_ids
    assert assignment.employee.is_active is True
    assert assignment.scheduled_at is not None
    assert assignment.scheduled_at.date() < job_request.desired_start_date


def test_assignment_is_refused_without_required_conditions() -> None:
    job_request = load_demo_job_request()
    _, employees, _ = load_demo_workforce()
    peter = next(employee for employee in employees if employee.id == "emp-peter-demo")
    invalid_candidates = (
        replace(peter, is_active=False),
        replace(peter, skill_ids=("tiling",)),
        replace(
            peter,
            availability=tuple(
                replace(slot, is_available=False) for slot in peter.availability
            ),
        ),
    )

    for candidate in invalid_candidates:
        assignment = assign_site_assessor(
            (candidate,), job_request.desired_start_date
        )
        assert assignment.assigned is False
        assert assignment.employee is None
        assert assignment.message


def test_complete_report_makes_job_ready_for_planning() -> None:
    job_request = load_demo_job_request()
    report = load_demo_site_visit_report()

    validation = validate_after_site_visit(job_request, report)

    assert validation.workflow_state is WorkflowState.READY_FOR_PLANNING
    assert validation.ready_for_planning is True
    assert validation.remaining_missing_information == ()
    assert validation.unresolved_risks == ()
    assert validation.updated_job_request.missing_information == ()
    assert validation.updated_job_request.reported_risks == ()
    assert all(
        requirement.is_confirmed
        for requirement in validation.updated_job_request.requirements
    )


def test_incomplete_report_does_not_make_job_ready_for_planning() -> None:
    job_request = load_demo_job_request()
    report = replace(
        load_demo_site_visit_report(),
        moisture_findings="",
        measurements=(),
    )

    validation = validate_after_site_visit(job_request, report)

    assert validation.workflow_state is WorkflowState.SITE_VISIT_COMPLETED
    assert validation.ready_for_planning is False
    assert validation.remaining_missing_information
    assert validation.updated_job_request.missing_information


def test_unresolved_risk_blocks_ready_for_planning() -> None:
    job_request = load_demo_job_request()
    report = replace(
        load_demo_site_visit_report(),
        unresolved_risk=True,
        unresolved_risk_details="Podejrzenie nieszczelności pionu",
    )

    validation = validate_after_site_visit(job_request, report)

    assert validation.workflow_state is WorkflowState.SITE_VISIT_COMPLETED
    assert validation.ready_for_planning is False
    assert validation.unresolved_risks
