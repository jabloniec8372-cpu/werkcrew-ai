from __future__ import annotations

import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from tests.unit.agent.fakes import ErrorModel, owner_interrupt_model, owner_resume_model
from werkcrew_ai.agent import AgentActivityStore, WerkcrewAgentOrchestrator
from werkcrew_ai.api.app import app
from werkcrew_ai.domain import OwnerDecisionGateStatus, WorkflowState
from werkcrew_ai.infrastructure.demo_repository import load_demo_site_visit_report
from werkcrew_ai.infrastructure.demo_workflow_store import DemoWorkflowStore


def interrupted_store(tmp_path: Path):
    store = DemoWorkflowStore()
    store.assess_job()
    store.create_site_visit()
    store.submit_report(load_demo_site_visit_report())
    store.generate_plans()
    store.calculate_plan_quotes()
    activity = AgentActivityStore()
    outcome = WerkcrewAgentOrchestrator(
        store,
        activity,
        model=owner_interrupt_model(),
        session_storage_dir=str(tmp_path),
    ).run()
    assert outcome.stop_reason == "interrupt"
    return store, activity


def test_coordinator_buttons_resume_real_gate_with_minimal_trusted_payload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_module = importlib.import_module("werkcrew_ai.api.app")
    store, activity = interrupted_store(tmp_path)
    gate = store.get().pending_owner_gate
    plans = store.get().planning_result.plans
    monkeypatch.setattr(app_module, "demo_workflow_store", store)
    monkeypatch.setattr(app_module, "demo_agent_activity_store", activity)
    monkeypatch.setattr(
        app_module,
        "agent_orchestrator_factory",
        lambda: WerkcrewAgentOrchestrator(
            store,
            activity,
            model=owner_resume_model(),
            session_storage_dir=str(tmp_path),
        ),
    )
    client = TestClient(app)

    before = client.get("/demo/coordinator")
    assert before.status_code == 200
    assert 'data-owner-gate-status="PENDING"' in before.text
    assert "Approve PLAN A" in before.text
    assert "Approve PLAN B" in before.text
    assert "Reject all plans" in before.text
    assert 'name="session_id"' not in before.text
    assert 'name="agent_id"' not in before.text
    assert 'name="interrupt_id"' not in before.text
    assert 'name="pricing_gate_fingerprint"' not in before.text
    assert 'name="workflow_instance_id"' not in before.text
    assert 'name="price"' not in before.text

    response = client.post(
        "/demo/owner-decision",
        data={
            "gate_id": gate.gate_id,
            "action": "APPROVE_PLAN",
            "plan_id": plans[0].id,
            "session_id": "browser-cannot-override",
            "agent_id": "browser-cannot-override",
            "interrupt_id": "browser-cannot-override",
            "pricing_gate_fingerprint": "browser-cannot-override",
            "workflow_instance_id": "browser-cannot-override",
            "gross_price": "0.00",
            "timestamp": "1970-01-01T00:00:00Z",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    snapshot = store.get()
    assert snapshot.workflow_state is WorkflowState.PLAN_APPROVED
    assert snapshot.owner_decision.selected_plan_id == plans[0].id
    assert snapshot.owner_decision.pricing_gate_fingerprint == gate.pricing_gate_fingerprint
    assert snapshot.pending_owner_gate.session_id == gate.session_id
    assert snapshot.pending_owner_gate.agent_id == gate.agent_id
    assert snapshot.pending_owner_gate.interrupt_id == gate.interrupt_id
    assert snapshot.pending_owner_gate.status is OwnerDecisionGateStatus.RESOLVED
    assert 'data-owner-decision="APPROVE_PLAN"' in response.text
    assert "COORDINATOR_UI" in response.text
    assert "OWNER" in response.text
    assert "PLAN_APPROVED" in response.text
    assert "Approve PLAN A" not in response.text
    assert "Reject all plans" not in response.text
    assert "Oferta nie została utworzona ani wysłana" in response.text


def test_owner_decision_http_validation_rejects_before_resume(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_module = importlib.import_module("werkcrew_ai.api.app")
    store, activity = interrupted_store(tmp_path)
    gate = store.get().pending_owner_gate
    monkeypatch.setattr(app_module, "demo_workflow_store", store)
    monkeypatch.setattr(app_module, "demo_agent_activity_store", activity)
    monkeypatch.setattr(
        app_module,
        "agent_orchestrator_factory",
        lambda: WerkcrewAgentOrchestrator(
            store,
            activity,
            model=ErrorModel(),
            session_storage_dir=str(tmp_path),
        ),
    )
    client = TestClient(app)

    assert client.post(
        "/demo/owner-decision", json={"gate_id": gate.gate_id}
    ).status_code == 422
    assert client.post(
        "/demo/owner-decision",
        data={"gate_id": gate.gate_id, "action": "MALFORMED"},
    ).status_code == 422
    assert client.post(
        "/demo/owner-decision",
        data={"gate_id": gate.gate_id, "action": "APPROVE_PLAN"},
    ).status_code == 422
    assert client.post(
        "/demo/owner-decision",
        data={
            "gate_id": gate.gate_id,
            "action": "REJECT_ALL",
            "plan_id": gate.eligible_plan_ids[0],
        },
    ).status_code == 422
    assert client.post(
        "/demo/owner-decision",
        data={"gate_id": "wrong", "action": "REJECT_ALL"},
    ).status_code == 409
    assert client.post(
        "/demo/owner-decision",
        data={
            "gate_id": gate.gate_id,
            "action": "APPROVE_PLAN",
            "plan_id": "missing-plan",
        },
    ).status_code == 422

    assert store.get().owner_decision is None
    assert store.get().pending_owner_gate.status is OwnerDecisionGateStatus.PENDING
