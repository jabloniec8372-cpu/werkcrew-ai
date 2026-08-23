from __future__ import annotations

import importlib
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from tests.unit.agent.test_m7_replan_interrupt import first_model, orchestrator, resume_model
from werkcrew_ai.api.app import app
from werkcrew_ai.dispatch import COMPANY_TIMEZONE, ReplanProposalStatus
from werkcrew_ai.infrastructure.m7_demo import M7_WORKER_ID, seed_m7_demo
from werkcrew_ai.persistence import SqliteBusinessRepository


def test_m7_ui_is_multi_job_redacted_and_owner_payload_is_minimal(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_module = importlib.import_module("werkcrew_ai.api.app")
    repository = SqliteBusinessRepository(tmp_path / "ui.db")
    ids = seed_m7_demo(repository)
    storage = tmp_path / "sessions"
    first = orchestrator(repository, ids, storage, first_model(ids)).run_material_delay_scenario(
        job_id="job-b-demo",
        scheduled_task_id=ids.task_b,
        available_at=datetime(2026, 9, 14, 11, 0, tzinfo=COMPANY_TIMEZONE),
        affected_job_ids=("job-b-demo", "job-c-demo"),
        worker_id=M7_WORKER_ID,
    )
    gate = repository.get_gate(first.gate_id)
    monkeypatch.setattr(app_module, "m7_repository_factory", lambda: repository)
    monkeypatch.setattr(
        app_module,
        "m7_orchestrator_factory",
        lambda repo, workflow_id: orchestrator(repo, ids, storage, resume_model()),
    )
    client = TestClient(app)

    page = client.get("/demo/m7")
    assert page.status_code == 200
    assert page.text.count("data-job-id=") == 3
    assert "Approve replan" in page.text
    assert 'name="gate_id"' in page.text
    assert 'name="action"' in page.text
    assert 'name="session_id"' not in page.text
    assert 'name="interrupt_id"' not in page.text
    assert 'name="proposal_fingerprint"' not in page.text
    assert 'name="schedule"' not in page.text
    assert "Potsdamer Platz 1" not in page.text
    assert "52.50960" not in page.text

    api = client.get("/api/m7/jobs")
    assert api.status_code == 200
    assert len(api.json()) == 3
    assert "address" not in api.json()[0]
    assert "coordinates" not in api.json()[0]

    decided = client.post(
        "/demo/m7/owner-decision",
        data={
            "gate_id": gate.gate_id,
            "action": "APPROVE_REPLAN",
            "session_id": "browser-override",
            "interrupt_id": "browser-override",
            "proposal_fingerprint": "browser-override",
            "schedule": "browser-override",
            "route_data": "browser-override",
        },
        follow_redirects=True,
    )
    assert decided.status_code == 200
    assert repository.get_proposal(gate.proposal_id).status is ReplanProposalStatus.APPLIED
    assert len(repository.owner_decisions(proposal_id=gate.proposal_id)) == 1
    assert "Approve replan" not in decided.text
