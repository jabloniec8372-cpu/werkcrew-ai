from fastapi.testclient import TestClient

from werkcrew_ai.api.app import app
from werkcrew_ai.infrastructure.demo_repository import load_demo_site_visit_report
from werkcrew_ai.infrastructure.demo_workflow_store import demo_workflow_store


def complete_report_form() -> dict[str, str]:
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


def test_complete_demo_flow_from_assessment_to_ready_for_planning() -> None:
    demo_workflow_store.reset()
    client = TestClient(app)

    before = client.get("/demo/coordinator")
    assert before.status_code == 200
    assert 'data-workflow-state="SITE_VISIT_REQUIRED"' in before.text
    assert "SITE_VISIT_REQUIRED" in before.text

    scheduled = client.post("/demo/site-visits", follow_redirects=True)
    assert scheduled.status_code == 200
    assert 'data-workflow-state="SITE_VISIT_SCHEDULED"' in scheduled.text
    assert "Peter DEMO" in scheduled.text

    field = client.get("/demo/field")
    assert field.status_code == 200
    assert "Lista kontrolna" in field.text
    assert "Raport z oględzin" in field.text

    submitted = client.post(
        "/demo/field/report",
        headers={"HX-Request": "true"},
        data=complete_report_form(),
    )
    assert submitted.status_code == 200
    assert 'data-workflow-state="READY_FOR_PLANNING"' in submitted.text

    after = client.get("/demo/coordinator")
    assert after.status_code == 200
    assert 'data-workflow-state="READY_FOR_PLANNING"' in after.text
    assert "31.40 m2" in after.text

    demo_workflow_store.reset()


def test_navigation_preserves_custom_origin_and_in_memory_state() -> None:
    custom_origin = "http://127.0.0.1:8765"
    client = TestClient(app, base_url=custom_origin)
    demo_workflow_store.reset()

    reset = client.post("/demo/reset", follow_redirects=False)
    assert reset.headers["location"] == f"{custom_origin}/demo/coordinator"

    created = client.post("/demo/site-visits", follow_redirects=False)
    assert created.headers["location"] == f"{custom_origin}/demo/coordinator"

    coordinator = client.get("/demo/coordinator")
    assert 'data-workflow-state="SITE_VISIT_SCHEDULED"' in coordinator.text
    assert f'href="{custom_origin}/demo/field"' in coordinator.text
    assert "8000" not in coordinator.text

    field = client.get("/demo/field")
    assert field.status_code == 200
    assert "SITE_VISIT_SCHEDULED" in field.text
    assert "Kompleksowy remont łazienki DEMO" in field.text
    assert f'action="{custom_origin}/demo/field/report"' in field.text
    assert "8000" not in field.text

    submitted = client.post(
        "/demo/field/report",
        data=complete_report_form(),
        follow_redirects=False,
    )
    assert submitted.headers["location"] == f"{custom_origin}/demo/field"

    after = client.get("/demo/coordinator")
    assert 'data-workflow-state="READY_FOR_PLANNING"' in after.text

    demo_workflow_store.reset()
