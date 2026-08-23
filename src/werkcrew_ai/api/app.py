"""FastAPI entry point for the deterministic WERKcrew AI DEMO workflows."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from werkcrew_ai.agent import (
    PersistentDispatchOrchestrator,
    WerkcrewAgentOrchestrator,
    demo_agent_activity_store,
)
from werkcrew_ai.catalog import M8_CONFIGURATION, assert_valid_m8_configuration
from werkcrew_ai.dispatch.service import PersistentDispatchService
from werkcrew_ai.domain import SiteMeasurement, SiteVisitReport
from werkcrew_ai.infrastructure.demo_repository import (
    load_demo_job_request,
    load_demo_site_visit_report,
    load_demo_workforce,
)
from werkcrew_ai.infrastructure.demo_workflow_store import (
    DemoWorkflowSnapshot,
    OwnerDecisionConflictError,
    OwnerDecisionInputError,
    demo_workflow_store,
)
from werkcrew_ai.infrastructure.m7_demo import (
    M7_NOW,
    M7_PLANNING_DATE,
    M7_WORKDAY_END,
    M7_WORKDAY_START,
    M7_WORKER_ID,
    seed_m7_demo,
)
from werkcrew_ai.persistence import (
    GateConflictError,
    GateInputError,
    SqliteBusinessRepository,
    SqliteSettings,
    StaleRevisionError,
)
from werkcrew_ai.planning import assess_job_request

assert_valid_m8_configuration(M8_CONFIGURATION)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
templates = Jinja2Templates(directory=REPOSITORY_ROOT / "templates")

app = FastAPI(
    title="WERKcrew AI",
    version="0.6.0",
    description=(
        "Deterministyczna ocena zlecenia, oględziny i warianty planowania DEMO."
    ),
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


def _redirect_to(request: Request, route_name: str) -> RedirectResponse:
    return RedirectResponse(
        url=str(request.url_for(route_name)),
        status_code=303,
    )


def _live_agent_orchestrator() -> WerkcrewAgentOrchestrator:
    """Lazy factory: importing or browsing the app never calls Bedrock."""

    return WerkcrewAgentOrchestrator(
        demo_workflow_store,
        demo_agent_activity_store,
    )


agent_orchestrator_factory = _live_agent_orchestrator


def _m7_repository() -> SqliteBusinessRepository:
    repository = SqliteBusinessRepository(
        SqliteSettings.from_environment().database_path
    )
    repository.initialize(now=M7_NOW)
    if not repository.list_jobs():
        seed_m7_demo(repository)
    return repository


m7_repository_factory = _m7_repository


def _m7_orchestrator(
    repository: SqliteBusinessRepository,
    workflow_instance_id: str,
) -> PersistentDispatchOrchestrator:
    return PersistentDispatchOrchestrator(
        repository,
        initiating_workflow_instance_id=workflow_instance_id,
        now=M7_NOW,
        planning_date=M7_PLANNING_DATE,
        workday_start=M7_WORKDAY_START,
        workday_end=M7_WORKDAY_END,
    )


m7_orchestrator_factory = _m7_orchestrator


@app.get("/", include_in_schema=False)
def index(request: Request) -> RedirectResponse:
    return RedirectResponse(
        url=str(request.url_for("coordinator_view")),
        status_code=307,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _m7_public_context(repository: SqliteBusinessRepository) -> dict[str, object]:
    jobs = repository.list_jobs()
    workflows = repository.workflows_for_jobs(tuple(item.job_id for item in jobs))
    workflow_by_job = {item.job_id: item for item in workflows}
    job_ids = tuple(item.job_id for item in jobs)
    return {
        "jobs": jobs,
        "workflows": workflow_by_job,
        "assignments": repository.calendar_for_jobs(job_ids),
        "materials": repository.materials_for_jobs(job_ids),
        "proposals": repository.list_proposals(),
        "gates": {
            workflow.workflow_instance_id: repository.pending_gate_for_workflow(
                workflow.workflow_instance_id
            )
            for workflow in workflows
        },
        "traces": {
            job.job_id: repository.trace_for_job(job.job_id) for job in jobs
        },
    }


@app.get("/demo/m7", response_class=HTMLResponse)
def m7_dispatch_view(request: Request) -> HTMLResponse:
    repository = m7_repository_factory()
    return templates.TemplateResponse(
        request=request,
        name="m7_dispatch.html",
        context=_m7_public_context(repository),
    )


@app.get("/api/m7/jobs")
def m7_jobs() -> list[dict[str, object]]:
    repository = m7_repository_factory()
    result = []
    for job in repository.list_jobs():
        workflows = repository.workflows_for_jobs((job.job_id,))
        result.append(
            {
                "job_id": job.job_id,
                "title": job.title,
                "address_status": job.address_status.value,
                "workflow_instances": [
                    {
                        "workflow_instance_id": item.workflow_instance_id,
                        "revision": item.revision,
                        "schema_version": item.schema_version,
                        "state": item.state,
                    }
                    for item in workflows
                ],
            }
        )
    return result


@app.get("/api/m7/jobs/{job_id}/trace")
def m7_job_trace(job_id: str):
    repository = m7_repository_factory()
    return [asdict(item) for item in repository.trace_for_job(job_id)]


@app.post("/demo/m7/material-delay")
def m7_material_delay(
    request: Request,
    job_id: str = Form(...),
    scheduled_task_id: str = Form(...),
    expected_revision: int = Form(...),
    available_at: str = Form(...),
):
    repository = m7_repository_factory()
    try:
        PersistentDispatchService(repository).record_material_delay(
            job_id=job_id,
            scheduled_task_id=scheduled_task_id,
            expected_revision=expected_revision,
            available_at=datetime.fromisoformat(available_at),
            now=M7_NOW,
        )
    except ValueError as exc:
        return PlainTextResponse(str(exc), status_code=422)
    except StaleRevisionError as exc:
        return PlainTextResponse(str(exc), status_code=409)
    return _redirect_to(request, "m7_dispatch_view")


@app.post("/demo/m7/proposals")
def m7_create_proposal(
    request: Request,
    initiating_workflow_instance_id: str = Form(...),
    affected_job_ids: str = Form(...),
    worker_id: str = Form(default=M7_WORKER_ID),
):
    repository = m7_repository_factory()
    try:
        PersistentDispatchService(repository).propose_dispatch_replan(
            initiating_workflow_instance_id=initiating_workflow_instance_id,
            affected_job_ids=tuple(
                item.strip() for item in affected_job_ids.split(",") if item.strip()
            ),
            worker_id=worker_id,
            planning_date=M7_PLANNING_DATE,
            now=M7_NOW,
            workday_start=M7_WORKDAY_START,
            workday_end=M7_WORKDAY_END,
        )
    except ValueError as exc:
        return PlainTextResponse(str(exc), status_code=422)
    return _redirect_to(request, "m7_dispatch_view")


@app.post("/demo/m7/agent/run")
def m7_run_agent(request: Request):
    repository = m7_repository_factory()
    m7_orchestrator_factory(
        repository, "workflow-job-b-demo-v1"
    ).run_material_delay_scenario(
        job_id="job-b-demo",
        scheduled_task_id="task-job-b-existing",
        available_at=datetime.fromisoformat("2026-09-14T11:00:00+02:00"),
        affected_job_ids=("job-b-demo", "job-c-demo"),
        worker_id=M7_WORKER_ID,
    )
    return _redirect_to(request, "m7_dispatch_view")


@app.post("/demo/m7/owner-decision")
def m7_owner_decision(
    request: Request,
    gate_id: str = Form(...),
    action: str = Form(...),
):
    repository = m7_repository_factory()
    try:
        gate = repository.get_gate(gate_id)
        m7_orchestrator_factory(
            repository, gate.workflow_instance_id
        ).resume_replan_decision(gate_id=gate_id, action=action)
    except GateInputError as exc:
        return PlainTextResponse(str(exc), status_code=422)
    except (GateConflictError, StaleRevisionError) as exc:
        return PlainTextResponse(str(exc), status_code=409)
    return _redirect_to(request, "m7_dispatch_view")


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
        context={
            "snapshot": demo_workflow_store.get(),
            "agent_state": demo_agent_activity_store.get(),
        },
    )


@app.post("/demo/site-visits")
def create_demo_site_visit(request: Request) -> RedirectResponse:
    demo_workflow_store.assess_job()
    demo_workflow_store.create_site_visit()
    return _redirect_to(request, "coordinator_view")


@app.post("/demo/plans")
def generate_demo_plans(request: Request) -> RedirectResponse:
    demo_workflow_store.generate_plans()
    return _redirect_to(request, "coordinator_view")


@app.post("/demo/pricing")
def calculate_demo_pricing(request: Request):
    try:
        demo_workflow_store.calculate_plan_quotes()
    except OwnerDecisionConflictError as exc:
        return PlainTextResponse(str(exc), status_code=409)
    return _redirect_to(request, "coordinator_view")


@app.post("/demo/agent/run")
def run_demo_agent(request: Request) -> RedirectResponse:
    agent_orchestrator_factory().run()
    return _redirect_to(request, "coordinator_view")


@app.post("/demo/owner-decision")
def submit_owner_decision(
    request: Request,
    gate_id: str = Form(...),
    action: str = Form(...),
    plan_id: str | None = Form(default=None),
):
    """Accept only minimal browser input, then resume the persisted interrupt."""

    selected_plan_id = plan_id.strip() if plan_id and plan_id.strip() else None
    try:
        agent_orchestrator_factory().resume_owner_decision(
            gate_id=gate_id,
            action=action,
            selected_plan_id=selected_plan_id,
        )
    except OwnerDecisionInputError as exc:
        return PlainTextResponse(str(exc), status_code=422)
    except OwnerDecisionConflictError as exc:
        return PlainTextResponse(str(exc), status_code=409)
    return _redirect_to(request, "coordinator_view")


@app.post("/demo/reset")
def reset_demo_workflow(request: Request) -> RedirectResponse:
    demo_workflow_store.reset()
    demo_agent_activity_store.reset()
    return _redirect_to(request, "coordinator_view")


@app.get("/demo/field", response_class=HTMLResponse)
def field_view(request: Request):
    snapshot = demo_workflow_store.get()
    if snapshot.site_visit is None:
        return _redirect_to(request, "coordinator_view")
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
        return _redirect_to(request, "coordinator_view")

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
    demo_agent_activity_store.record(
        action="Site report received",
        public_result="Raport człowieka został zapisany przez WERKcrew Field.",
        workflow_state=updated_snapshot.workflow_state,
        rationale="Dane pochodzą z formularza terenowego, nie z modelu.",
    )

    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request=request,
            name="partials/report_result.html",
            context={"snapshot": updated_snapshot},
        )
    return _redirect_to(request, "field_view")
