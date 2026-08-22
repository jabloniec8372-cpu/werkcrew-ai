import importlib

from fastapi.testclient import TestClient

from tests.unit.agent.fakes import calculate_pricing_model
from werkcrew_ai.agent import AgentActivityStore, WerkcrewAgentOrchestrator
from werkcrew_ai.api.app import app
from werkcrew_ai.infrastructure.demo_repository import (
    load_demo_pricing_data,
    load_demo_site_visit_report,
)
from werkcrew_ai.infrastructure.demo_workflow_store import DemoWorkflowStore


def planned_store(**kwargs) -> DemoWorkflowStore:
    store = DemoWorkflowStore(**kwargs)
    store.assess_job()
    store.create_site_visit()
    store.submit_report(load_demo_site_visit_report())
    store.generate_plans()
    return store


def test_complete_pricing_ui_uses_safe_demo_labels(monkeypatch) -> None:
    app_module = importlib.import_module("werkcrew_ai.api.app")
    store = planned_store()
    store.calculate_plan_quotes()
    monkeypatch.setattr(app_module, "demo_workflow_store", store)

    response = TestClient(app).get("/demo/coordinator")

    assert response.status_code == 200
    assert response.text.count('data-pricing-status="COMPLETE"') == 2
    assert "Szacowany koszt firmy — dane demonstracyjne" in response.text
    assert "Deterministyczna estymacja kosztu na jawnych danych syntetycznych" in response.text
    assert "DEMO_SYNTHETIC" in response.text
    assert "4464.42 EUR" in response.text
    assert "4658.41 EUR" in response.text
    assert 'data-variants-comparable="true"' in response.text
    assert "LABOR" in response.text
    assert "gwarantowana marża" not in response.text
    assert "ostateczna cena" not in response.text


def test_partial_pricing_ui_marks_variants_not_comparable(monkeypatch) -> None:
    app_module = importlib.import_module("werkcrew_ai.api.app")
    context, usages = load_demo_pricing_data()
    store = planned_store(
        pricing_context=context,
        vehicle_usages=tuple(item for item in usages if item.plan_id.endswith("-a")),
    )
    activity = AgentActivityStore()
    WerkcrewAgentOrchestrator(
        store,
        activity,
        model=calculate_pricing_model(),
    ).run()
    monkeypatch.setattr(app_module, "demo_workflow_store", store)
    monkeypatch.setattr(app_module, "demo_agent_activity_store", activity)

    response = TestClient(app).get("/demo/coordinator")

    assert response.status_code == 200
    assert response.text.count('data-pricing-status="COMPLETE"') == 1
    assert response.text.count('data-pricing-status="INCOMPLETE"') == 1
    assert "MISSING_VEHICLE_DISTANCE" in response.text
    assert 'data-variants-comparable="false"' in response.text
    assert "Warianty nie są jeszcze porównywalne" in response.text
    assert 'data-agent-status="WAITING_FOR_PRICING_INPUT"' in response.text
    assert "Owner gate pozostaje zamknięty" in response.text
