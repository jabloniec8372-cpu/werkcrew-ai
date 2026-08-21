from fastapi.testclient import TestClient

from werkcrew_ai.api.app import app
from werkcrew_ai.infrastructure.demo_repository import load_demo_site_visit_report
from werkcrew_ai.infrastructure.demo_workflow_store import demo_workflow_store


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

    report = load_demo_site_visit_report()
    measurements = {item.requirement_id: item.quantity for item in report.measurements}
    submitted = client.post(
        "/demo/field/report",
        headers={"HX-Request": "true"},
        data={
            "measured_dimensions": report.measured_dimensions,
            "waterproofing_quantity": str(measurements["req-waterproofing"]),
            "tiling_quantity": str(measurements["req-tiling"]),
            "substrate_condition": report.substrate_condition,
            "moisture_findings": report.moisture_findings,
            "access_conditions": report.access_conditions,
            "installation_findings": report.installation_findings,
            "notes": report.notes,
            "unresolved_risk_details": "",
        },
    )
    assert submitted.status_code == 200
    assert 'data-workflow-state="READY_FOR_PLANNING"' in submitted.text

    after = client.get("/demo/coordinator")
    assert after.status_code == 200
    assert 'data-workflow-state="READY_FOR_PLANNING"' in after.text
    assert "31.40 m2" in after.text

    demo_workflow_store.reset()
