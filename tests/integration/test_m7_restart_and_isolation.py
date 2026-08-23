from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from werkcrew_ai.agent.session import WERKCREW_AGENT_ID
from werkcrew_ai.dispatch import PersistentGate, PersistentGateStatus
from werkcrew_ai.infrastructure.m7_demo import M7_NOW, seed_m7_demo
from werkcrew_ai.persistence import (
    CrossJobReferenceError,
    GateConflictError,
    SqliteBusinessRepository,
    RecoveryInconsistencyError,
)


def run_probe(mode: str, database: Path, storage: Path) -> dict:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(Path.cwd()), str(Path.cwd() / "src"))
    )
    completed = subprocess.run(
        [
            sys.executable,
            "tests/support/m7_process_probe.py",
            mode,
            str(database),
            str(storage),
        ],
        cwd=Path.cwd(),
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_real_process_restart_recovers_business_and_pending_gate(tmp_path: Path) -> None:
    database = tmp_path / "restart.db"
    storage = tmp_path / "sessions"
    first = run_probe("create", database, storage)
    second = run_probe("recover", database, storage)
    assert first["stop_reason"] == "interrupt"
    assert first["workflow_id"] == second["workflow_id"]
    assert first["workflow_state"] == second["workflow_state"]
    assert first["workflow_revision"] == second["workflow_revision"] == 0
    assert first["session_id"] == second["session_id"]
    assert first["agent_id"] == second["agent_id"] == WERKCREW_AGENT_ID
    assert first["gate_id"] == second["gate_id"]
    assert first["interrupt_id"] == second["interrupt_id"]
    assert first["gate_status"] == second["gate_status"] == "PENDING"
    assert first["trace_count"] == second["trace_count"]
    assert first["decision_count"] == second["decision_count"] == 0
    assert second["auto_resumed"] is False


def test_job_action_isolation_and_cross_job_rejection(tmp_path: Path) -> None:
    repository = SqliteBusinessRepository(tmp_path / "isolation.db")
    ids = seed_m7_demo(repository)
    workflow_b = repository.get_workflow(ids.workflow_b)
    repository.update_workflow_cas(
        workflow_instance_id=ids.workflow_b,
        expected_revision=workflow_b.revision,
        state="SITE_VISIT_SCHEDULED",
        snapshot={"data_classification": "DEMO_SYNTHETIC", "job_local_status": "SITE_VISIT_SCHEDULED"},
        now=M7_NOW,
    )
    binding_a = repository.get_session_binding(ids.workflow_a)
    gate_a = repository.save_pending_gate(
        PersistentGate(
            gate_id="plan-gate-job-a",
            workflow_instance_id=ids.workflow_a,
            gate_type="PLAN_OWNER_REVIEW",
            proposal_id=None,
            gate_fingerprint="pricing-fingerprint-demo",
            session_id=binding_a.session_id,
            agent_id=binding_a.agent_id,
            interrupt_id="interrupt-job-a",
            status=PersistentGateStatus.PENDING,
            response_fingerprint=None,
            decision_id=None,
            created_at=M7_NOW,
            resolved_at=None,
        )
    )
    before_a = repository.get_workflow(ids.workflow_a)
    before_b = repository.get_workflow(ids.workflow_b)
    traces_a = repository.trace_for_job("job-a-demo")
    traces_b = repository.trace_for_job("job-b-demo")
    session_a = repository.get_session_binding(ids.workflow_a)
    session_b = repository.get_session_binding(ids.workflow_b)

    workflow_c = repository.get_workflow(ids.workflow_c)
    updated_c = repository.update_workflow_cas(
        workflow_instance_id=ids.workflow_c,
        expected_revision=workflow_c.revision,
        state="DISPATCH_CHECKED",
        snapshot={"data_classification": "DEMO_SYNTHETIC", "job_local_status": "DISPATCH_CHECKED"},
        now=M7_NOW,
    )
    assert updated_c.revision == workflow_c.revision + 1
    assert repository.get_workflow(ids.workflow_a) == before_a
    assert repository.get_workflow(ids.workflow_b) == before_b
    assert repository.get_session_binding(ids.workflow_a) == session_a
    assert repository.get_session_binding(ids.workflow_b) == session_b
    assert repository.get_gate(gate_a.gate_id) == gate_a
    assert repository.trace_for_job("job-a-demo") == traces_a
    assert repository.trace_for_job("job-b-demo") == traces_b

    wrong = replace(repository.get_assignment(ids.assignment_b), job_id="job-c-demo")
    with pytest.raises(CrossJobReferenceError):
        repository.add_calendar_assignment(wrong, now=M7_NOW)


def test_concurrent_replan_clicks_claim_at_most_one(tmp_path: Path) -> None:
    created = run_probe("create", tmp_path / "clicks.db", tmp_path / "sessions")
    repository = SqliteBusinessRepository(tmp_path / "clicks.db")

    def claim():
        try:
            return repository.claim_replan_response(
                gate_id=created["gate_id"], action="APPROVE_REPLAN"
            )
        except GateConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: claim(), range(2)))
    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert repository.get_gate(created["gate_id"]).status is PersistentGateStatus.RESUMING


def test_recovery_fails_safe_when_sqlite_and_strands_storage_disagree(
    tmp_path: Path,
) -> None:
    created = run_probe("create", tmp_path / "mismatch.db", tmp_path / "sessions")
    repository = SqliteBusinessRepository(tmp_path / "mismatch.db")
    with pytest.raises(RecoveryInconsistencyError, match="session is unavailable"):
        repository.recover_pending_gate(
            workflow_instance_id=created["workflow_id"],
            session_storage_dir=str(tmp_path / "different-empty-storage"),
        )
