from decimal import Decimal

import pytest

from werkcrew_ai.agent import (
    AgentActivityStore,
    AgentStatus,
    WerkcrewAgentOrchestrator,
    WerkcrewAgentTools,
)
from werkcrew_ai.domain import WorkflowState
from werkcrew_ai.infrastructure.demo_repository import load_demo_site_visit_report
from werkcrew_ai.infrastructure.demo_workflow_store import DemoWorkflowStore

from .fakes import ErrorModel, first_agent_run_model, resumed_agent_run_model


EXPECTED_TOOLS = {
    "get_job_state",
    "assess_job",
    "prepare_site_visit",
    "get_site_visit_status",
    "validate_site_visit_report",
    "generate_crew_plans",
}


@pytest.fixture
def stores():
    return DemoWorkflowStore(), AgentActivityStore()


def test_strands_registers_exact_m4_tool_catalog(stores) -> None:
    workflow, activity = stores
    orchestrator = WerkcrewAgentOrchestrator(
        workflow, activity, model=first_agent_run_model()
    )

    assert set(orchestrator.build_agent().tool_names) == EXPECTED_TOOLS
    assert "submit" not in " ".join(EXPECTED_TOOLS)
    assert "approve" not in " ".join(EXPECTED_TOOLS)


def test_get_job_state_is_read_only(stores) -> None:
    workflow, activity = stores
    tools = WerkcrewAgentTools(workflow, activity)
    before = workflow.get()

    result = tools.get_job_state()

    assert result["workflow_state"] == "RECEIVED"
    assert result["assessment_completed"] is False
    assert result["assessment"] is None
    assert result["allowed_next_actions"] == ["assess_job"]
    assert result["job_request"]["id"] == before.current_job_request.id
    assert result["plans_exist"] is False
    assert workflow.get() == before


def test_assess_job_delegates_to_m1(stores) -> None:
    workflow, activity = stores
    result = WerkcrewAgentTools(workflow, activity).assess_job()

    assert result["workflow_state"] == "SITE_VISIT_REQUIRED"
    assert result["decision"] == "SITE_VISIT_REQUIRED"
    assert result["site_visit_required"] is True
    assert result["rule_version"] == "job-assessment-v1"
    assert len(result["missing_information"]) == 7
    assert len(result["detected_risks"]) == 4
    assert workflow.get().initial_assessment is not None
    assert workflow.get().workflow_state is WorkflowState.SITE_VISIT_REQUIRED
    next_state = WerkcrewAgentTools(workflow, activity).get_job_state()
    assert next_state["assessment_completed"] is True
    assert next_state["allowed_next_actions"] == ["prepare_site_visit"]


def test_assessment_is_idempotent_once_persisted(stores) -> None:
    workflow, _ = stores
    first = workflow.assess_job()

    second = workflow.assess_job()

    assert second is first
    assert second.initial_assessment is first.initial_assessment


def test_fresh_job_cannot_prepare_site_visit_before_assessment(stores) -> None:
    workflow, activity = stores

    with pytest.raises(ValueError, match="Najpierw wykonaj ocenę M1"):
        WerkcrewAgentTools(workflow, activity).prepare_site_visit()

    assert workflow.get().workflow_state is WorkflowState.RECEIVED
    assert workflow.get().site_visit is None


def test_already_assessed_job_can_prepare_site_visit_without_reassessment(
    stores,
) -> None:
    workflow, activity = stores
    first_assessment = workflow.assess_job().initial_assessment

    result = WerkcrewAgentTools(workflow, activity).prepare_site_visit()

    assert result["workflow_state"] == "SITE_VISIT_SCHEDULED"
    assert workflow.get().initial_assessment is first_assessment


def test_first_real_strands_loop_prepares_visit_and_stops_for_human(stores) -> None:
    workflow, activity = stores
    model = first_agent_run_model()

    outcome = WerkcrewAgentOrchestrator(
        workflow, activity, model=model
    ).run()

    snapshot = workflow.get()
    assert snapshot.workflow_state is WorkflowState.SITE_VISIT_SCHEDULED
    assert snapshot.site_visit is not None
    assert snapshot.site_visit.assigned_employee_id is not None
    assert snapshot.site_visit.report is None
    assert outcome.runtime_state.status is AgentStatus.WAITING_FOR_FIELD_REPORT
    assert outcome.runtime_state.invoked_tools == (
        "get_job_state",
        "assess_job",
        "prepare_site_visit",
    )
    assert model.stream_calls == 4


def test_first_agent_run_cannot_create_peter_report(stores) -> None:
    workflow, activity = stores
    WerkcrewAgentOrchestrator(
        workflow, activity, model=first_agent_run_model()
    ).run()

    assert workflow.get().site_visit.report is None
    assert all("report" not in name or name.startswith("get_") for name in (
        "get_job_state", "assess_job", "prepare_site_visit"
    ))


def test_resume_after_real_field_report_reaches_owner_boundary(stores) -> None:
    workflow, activity = stores
    WerkcrewAgentOrchestrator(
        workflow, activity, model=first_agent_run_model()
    ).run()
    workflow.submit_report(load_demo_site_visit_report())
    activity.record(
        action="Site report received",
        public_result="Raport człowieka zapisany.",
        workflow_state=workflow.get().workflow_state,
        rationale="Źródło: WERKcrew Field.",
    )

    outcome = WerkcrewAgentOrchestrator(
        workflow, activity, model=resumed_agent_run_model()
    ).run()

    snapshot = workflow.get()
    assert snapshot.workflow_state is WorkflowState.PLANS_READY_FOR_REVIEW
    assert outcome.runtime_state.status is AgentStatus.WAITING_FOR_OWNER_REVIEW
    assert outcome.runtime_state.invoked_tools[-4:] == (
        "get_job_state",
        "get_site_visit_status",
        "validate_site_visit_report",
        "generate_crew_plans",
    )
    assert snapshot.planning_result is not None
    assert len(snapshot.planning_result.plans) == 2


def test_validate_report_tool_preserves_canonical_measurements(stores) -> None:
    workflow, activity = stores
    workflow.assess_job()
    workflow.create_site_visit()
    workflow.submit_report(load_demo_site_visit_report())

    result = WerkcrewAgentTools(workflow, activity).validate_site_visit_report()
    quantities = {
        requirement["id"]: Decimal(requirement["quantity"])
        for requirement in result["updated_job_request"]["requirements"]
        if requirement["quantity"] is not None
    }

    assert result["ready_for_planning"] is True
    assert result["workflow_state"] == "READY_FOR_PLANNING"
    assert quantities["req-waterproofing"] == Decimal("8.60")
    assert quantities["req-tiling"] == Decimal("31.40")


def test_generate_plans_tool_preserves_trace_and_complete_scope(stores) -> None:
    workflow, activity = stores
    workflow.assess_job()
    workflow.create_site_visit()
    workflow.submit_report(load_demo_site_visit_report())

    result = WerkcrewAgentTools(workflow, activity).generate_crew_plans()
    task_names = {
        assignment["work_item_name"]
        for plan in result["plans"]
        for assignment in plan["assignments"]
    }

    assert result["workflow_state"] == "PLANS_READY_FOR_REVIEW"
    assert len(result["plans"]) == 2
    assert len(task_names) == 6
    assert any("instalacji" in name for name in task_names)
    assert any("Biały montaż" in name for name in task_names)
    assert result["decision_trace"]
    assert result["rejected_candidates_and_resources"]


def test_activity_timeline_is_public_tool_audit(stores) -> None:
    workflow, activity = stores
    WerkcrewAgentOrchestrator(
        workflow, activity, model=first_agent_run_model()
    ).run()

    timeline = activity.get().timeline
    assert timeline[0].action == "Received job request"
    assert [entry.tool for entry in timeline if entry.tool] == [
        "get_job_state",
        "assess_job",
        "prepare_site_visit",
    ]
    assert timeline[-1].action == "Waiting for field report"
    assert all(entry.public_result and entry.rationale for entry in timeline)


def test_agent_never_approves_a_plan(stores) -> None:
    workflow, activity = stores
    workflow.assess_job()
    workflow.create_site_visit()
    workflow.submit_report(load_demo_site_visit_report())
    outcome = WerkcrewAgentOrchestrator(
        workflow, activity, model=resumed_agent_run_model()
    ).run()

    assert workflow.get().workflow_state is WorkflowState.PLANS_READY_FOR_REVIEW
    assert outcome.runtime_state.status is AgentStatus.WAITING_FOR_OWNER_REVIEW
    assert not any("approve" in tool_name for tool_name in EXPECTED_TOOLS)


def test_provider_error_is_visible_and_does_not_fake_progress(stores) -> None:
    workflow, activity = stores

    outcome = WerkcrewAgentOrchestrator(
        workflow, activity, model=ErrorModel()
    ).run()

    assert outcome.runtime_state.status is AgentStatus.ERROR
    assert "simulated Bedrock outage" in outcome.public_message
    assert workflow.get().workflow_state is WorkflowState.RECEIVED
    assert workflow.get().site_visit is None
