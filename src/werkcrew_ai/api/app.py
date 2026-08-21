"""FastAPI entry point for the deterministic WERKcrew AI DEMO workflows."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from werkcrew_ai.domain import SiteMeasurement, SiteVisitReport
from werkcrew_ai.infrastructure.demo_repository import (
    load_demo_job_request,
    load_demo_site_visit_report,
    load_demo_workforce,
)
from werkcrew_ai.infrastructure.demo_workflow_store import (
    DemoWorkflowSnapshot,
    demo_workflow_store,
)
from werkcrew_ai.planning import assess_job_request

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
templates = Jinja2Templates(directory=REPOSITORY_ROOT / "templates")

app = FastAPI(
    title="WERKcrew AI",
    version="0.2.0",
    description="Deterministyczna ocena zlecenia i pętla oględzin terenowych DEMO.",
)
app.mount(
    "/static",
    StaticFiles(directory=REPOSITORY_ROOT / "static"),
    name="static",
)


def _field_context(snapshot: DemoWorkflowSnapshot) -> dict[str, object]:
    submitted_report = (
        snapshot.site_visit.report
        if snapshot.site_visit is not None and snapshot.site_visit.report is not None
        else load_demo_site_visit_report()
    )
    measurement_values = {
        item.requirement_id: item.quantity for item in submitted_report.measurements
    }
    return {
        "snapshot": snapshot,
        "report": submitted_report,
        "measurement_values": measurement_values,
    }


def _measurement(
    requirement_id: str, raw_quantity: str, unit: str = "m2"
) -> SiteMeasurement | None:
    try:
        return SiteMeasurement(
            requirement_id=requirement_id,
            quantity=Decimal(raw_quantity.replace(",", ".")),
            unit=unit,
        )
    except InvalidOperation:
        return None


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/demo/coordinator", status_code=307)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/demo/job-assessment")
def demo_job_assessment() -> dict[str, object]:
    skills, employees, vehicles = load_demo_workforce()
    job_request = load_demo_job_request()
    assessment = assess_job_request(job_request)

    return {
        "data_classification": "DEMO_SYNTHETIC",
        "job_request": asdict(job_request),
        "detected_gaps_and_risks": {
            "missing_information": assessment.missing_information,
            "risks": assessment.detected_risks,
        },
        "assessment": asdict(assessment),
        "peter_and_site_visit": {
            "site_visit_required": assessment.site_visit_required,
            "peter_attention_required": assessment.peter_attention_required,
            "note": (
                "W M2 wykonawca oględzin DEMO jest dobierany po aktywności, "
                "skillu site-assessment i dostępności."
            ),
        },
        "demo_resources": {
            "skills": [asdict(skill) for skill in skills],
            "employees": [asdict(employee) for employee in employees],
            "vehicles": [asdict(vehicle) for vehicle in vehicles],
        },
    }


@app.get("/demo/coordinator", response_class=HTMLResponse)
def coordinator_view(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="coordinator.html",
        context={"snapshot": demo_workflow_store.get()},
    )


@app.post("/demo/site-visits")
def create_demo_site_visit() -> RedirectResponse:
    demo_workflow_store.create_site_visit()
    return RedirectResponse(url="/demo/coordinator", status_code=303)


@app.post("/demo/reset")
def reset_demo_workflow() -> RedirectResponse:
    demo_workflow_store.reset()
    return RedirectResponse(url="/demo/coordinator", status_code=303)


@app.get("/demo/field", response_class=HTMLResponse)
def field_view(request: Request):
    snapshot = demo_workflow_store.get()
    if snapshot.site_visit is None:
        return RedirectResponse(url="/demo/coordinator", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="field.html",
        context=_field_context(snapshot),
    )


@app.post("/demo/field/report", response_class=HTMLResponse)
def submit_field_report(
    request: Request,
    measured_dimensions: str = Form(...),
    waterproofing_quantity: str = Form(...),
    tiling_quantity: str = Form(...),
    substrate_condition: str = Form(...),
    moisture_findings: str = Form(...),
    access_conditions: str = Form(...),
    installation_findings: str = Form(...),
    notes: str = Form(...),
    unresolved_risk: str | None = Form(default=None),
    unresolved_risk_details: str = Form(default=""),
):
    snapshot = demo_workflow_store.get()
    if snapshot.site_visit is None:
        return RedirectResponse(url="/demo/coordinator", status_code=303)

    possible_measurements = (
        _measurement("req-waterproofing", waterproofing_quantity),
        _measurement("req-tiling", tiling_quantity),
    )
    report = SiteVisitReport(
        site_visit_id=snapshot.site_visit.id,
        measured_dimensions=measured_dimensions,
        measurements=tuple(
            item for item in possible_measurements if item is not None
        ),
        substrate_condition=substrate_condition,
        moisture_findings=moisture_findings,
        access_conditions=access_conditions,
        installation_findings=installation_findings,
        notes=notes,
        unresolved_risk=unresolved_risk is not None,
        unresolved_risk_details=unresolved_risk_details,
    )
    updated_snapshot = demo_workflow_store.submit_report(report)

    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request=request,
            name="partials/report_result.html",
            context={"snapshot": updated_snapshot},
        )
    return RedirectResponse(url="/demo/field", status_code=303)
