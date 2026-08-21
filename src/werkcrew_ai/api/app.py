"""FastAPI entry point for the first WERKcrew AI vertical slice."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI

from werkcrew_ai.infrastructure.demo_repository import (
    load_demo_job_request,
    load_demo_workforce,
)
from werkcrew_ai.planning import assess_job_request

app = FastAPI(
    title="WERKcrew AI",
    version="0.1.0",
    description="Deterministyczna ocena kompletności i ryzyka zlecenia.",
)


@app.get("/")
def index() -> dict[str, str]:
    return {
        "service": "WERKcrew AI",
        "status": "running",
        "demo_endpoint": "/api/demo/job-assessment",
        "docs": "/docs",
    }


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
                "Dokładna rola Petera pozostaje poza zakresem tej reguły; "
                "flaga oznacza potrzebę jego uwagi w procesie oględzin."
            ),
        },
        "demo_resources": {
            "skills": [asdict(skill) for skill in skills],
            "employees": [asdict(employee) for employee in employees],
            "vehicles": [asdict(vehicle) for vehicle in vehicles],
        },
    }
