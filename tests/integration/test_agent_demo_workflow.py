import importlib

from fastapi.testclient import TestClient

from tests.unit.agent.fakes import first_agent_run_model, resumed_agent_run_model
from werkcrew_ai.agent import (
    AgentStatus,
    WerkcrewAgentOrchestrator,
    demo_agent_activity_store,
)
from werkcrew_ai.api.app import app
from werkcrew_ai.domain import WorkflowState
from werkcrew_ai.infrastructure.demo_repository import load_demo_site_visit_report
from werkcrew_ai.infrastructure.demo_workflow_store import demo_workflow_store


def _report_form() -> dict[str, str]:
    report = load_demo_site_visit_report()
    measurements = {item.requirement_id: item.quantity for item in report.measurements}
    return {
        "measured_dimensions": report.measured_dimensions,
        "waterproofing_quantity": str(measurements["req-waterproofing"]),
        "tiling_quantity": str(measurements["req-tiling"]),
        "substrate_condition": report.substrate_condition,
        "moisture_findings": report.moisture_findings,
        "access_conditions": report.access_conditions,
        "installation_findings": report.installation_findings,
        "notes": report.notes,
        "unresolved_risk_details": "",
    }


def _factory(model):
    return lambda: WerkcrewAgentOrchestrator(
        demo_workflow_store,
        demo_agent_activity_store,
        model=model,
    )


def test_ui_runs_and_resumes_real_strands_loop_at_human_boundaries(monkeypatch) -> None:
    app_module = importlib.import_module("werkcrew_ai.api.app")
    client = TestClient(app)
    demo_workflow_store.reset()
    demo_agent_activity_store.reset()

    monkeypatch.setattr(
        app_module,
        "agent_orchestrator_factory",
        _factory(first_agent_run_model()),
    )
    first = client.post("/demo/agent/run", follow_redirects=True)

    assert first.status_code == 200
    assert 'data-agent-status="WAITING_FOR_FIELD_REPORT"' in first.text
    assert "prepare_site_visit" in first.text
    assert "Peter DEMO" in first.text
    assert demo_workflow_store.get().site_visit.report is None

    submitted = client.post(
        "/demo/field/report",
        data=_report_form(),
        follow_redirects=True,
    )
    assert submitted.status_code == 200
    assert demo_workflow_store.get().workflow_state is WorkflowState.READY_FOR_PLANNING
    assert any(
        entry.action == "Site report received"
        for entry in demo_agent_activity_store.get().timeline
    )

    monkeypatch.setattr(
        app_module,
        "agent_orchestrator_factory",
        _factory(resumed_agent_run_model()),
    )
    resumed = client.post("/demo/agent/run", follow_redirects=True)

    assert resumed.status_code == 200
    assert 'data-agent-status="WAITING_FOR_OWNER_REVIEW"' in resumed.text
    assert 'data-workflow-state="PLANS_READY_FOR_REVIEW"' in resumed.text
    assert "generate_crew_plans" in resumed.text
    assert "PLAN A" in resumed.text
    assert "PLAN B" in resumed.text
    assert demo_agent_activity_store.get().status is AgentStatus.WAITING_FOR_OWNER_REVIEW
    assert demo_workflow_store.get().workflow_state is WorkflowState.PLANS_READY_FOR_REVIEW

    client.post("/demo/reset")
