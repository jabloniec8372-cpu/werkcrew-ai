from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from werkcrew_ai.api.m2_runtime import m2_repository, router, server_event_id, server_timestamp
from werkcrew_ai.field.models import (
    AssignmentKind, AssignmentState, CompletionType, DirectiveClass,
    DirectiveDefinition, DirectiveRoot, DirectiveType, M2JobExecutionRoot,
    PlanDayRoot, PlanDayStatus, TaskDefinition, TaskState, WorkerIdentityRegistry,
)
from werkcrew_ai.field.repository import M2DurableRepository
from werkcrew_ai.field.serialization import canonical_json
from werkcrew_ai.intake import CanonicalJobIntake, CanonicalJobRepository, M1BoundaryPublicationRepository
from werkcrew_ai.planning.m2_bridge import M2EffectSourceIdentity, M2UnavailableToM3Bridge
from werkcrew_ai.planning.m3_unavailability_impact import (
    AUTHORITY_SCHEMA_VERSION, SCOPE_SCHEMA_VERSION, M3CurrentCommitment,
    M3CurrentPlanningScope, M3ImpactAssociationError, M3ImpactOutcome,
    M3PlanningScopeAuthority, evaluate_unavailability_impact,
    serialize_current_planning_scope, serialize_unavailability_impact_result,
)


NOW = datetime(2026, 9, 6, 8, tzinfo=timezone.utc)
ACTIVATION_ID = "018f6f3e-7c45-7ac8-b0b6-6f1f93194c20"
UNAVAILABLE_ID = "018f6f3e-7c45-7ac8-b0b6-6f1f93194c21"
ACK_ID = "018f6f3e-7c45-7ac8-b0b6-6f1f93194c22"
ROOT = Path(__file__).resolve().parents[2]


def seed(path, *, reference=None):
    repository = M2DurableRepository(path)
    repository.initialize(now=NOW)
    repository.create_worker_registry(WorkerIdentityRegistry(("worker-1", "replacement")))
    repository.create_plan_day_root(PlanDayRoot(
        "plan-day", "worker-1", NOW.date(), NOW - timedelta(hours=1),
        PlanDayStatus.ISSUED, confirmed_plan_reference=reference,
    ))
    CanonicalJobRepository(path).create_job(
        job_id="job-1", intake=CanonicalJobIntake("m3-a-test", None, None, ()),
        created_at=NOW - timedelta(days=1),
    )
    publication_repository = M1BoundaryPublicationRepository(path)
    publication = publication_repository.publish_handoff(
        publication_repository.current_projection("job-1"),
        published_at=NOW - timedelta(hours=12),
    )
    task = TaskState(TaskDefinition(
        task_id="task-1", definition_version="task-1-v1", job_id="job-1",
        source_handoff_id=publication.handoff_id, source_revision=publication.source_revision,
        business_meaning="M3-A real historical assignment", completion_type=CompletionType.TASK,
        assignment_kind=AssignmentKind.SINGLE,
    ))
    assignment = AssignmentState(
        "assignment-1", "job-1", "task-1", "task-1-v1", AssignmentKind.SINGLE,
        ("worker-1",), ("plan-day",),
    )
    repository.create_job_execution_root(M2JobExecutionRoot("job-1", (task,), (assignment,)))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[m2_repository] = lambda: repository
    app.dependency_overrides[server_timestamp] = lambda: NOW
    return repository, app


def post(app, event_type, event_id, *, retry=False):
    app.dependency_overrides[server_event_id] = lambda: ("retry-" if retry else "server-") + event_id
    payload = {
        "event_id": event_id, "schema_version": 1, "actor_id": "worker-1",
        "occurred_at": NOW.isoformat(), "event_type": event_type,
    }
    if event_type == "WORKER_ACKNOWLEDGED":
        payload["directive_id"] = "later-directive"
        endpoint = "worker-acknowledged"
    else:
        payload["plan_day_id"] = "plan-day"
        endpoint = "day-plan-activated"
    if event_type == "UNAVAILABLE_TODAY_REPORTED":
        payload["reason_class"] = "SICK"
        endpoint = "unavailable-today-reported"
    with TestClient(app) as client:
        response = client.post(
            "/api/m2/field-events/" + endpoint,
            headers={"X-WERKcrew-Worker-ID": "worker-1"}, json=payload,
        )
    assert response.status_code == 200, response.text
    return response.json()


def build_request(path):
    return M2UnavailableToM3Bridge(M2DurableRepository(path)).build_request(
        M2EffectSourceIdentity("FIELD_EVENT", UNAVAILABLE_ID, 0)
    )


def trusted_m3_fixture(request):
    # Fixed test-owned M3 association, deliberately not the M2 confirmation ref.
    scope = M3CurrentPlanningScope(
        schema_version=SCOPE_SCHEMA_VERSION, company_plan_id="company-plan-independent",
        company_plan_revision=9, plan_day_id="plan-day", unavailable_worker_id="worker-1",
        business_date=date(2026, 9, 6), source_request_fingerprint=request.request_fingerprint,
        coverage_complete=True, commitments=(M3CurrentCommitment(
            commitment_id="commitment-1", job_id="job-1", task_id="task-1",
            assigned_worker_ids=("worker-1",), source_m2_assignment_ids=("assignment-1",),
        ),),
    )
    authority = M3PlanningScopeAuthority(
        schema_version=AUTHORITY_SCHEMA_VERSION, company_plan_id="company-plan-independent",
        company_plan_revision=9, plan_day_id="plan-day", unavailable_worker_id="worker-1",
        business_date=date(2026, 9, 6), source_request_fingerprint=request.request_fingerprint,
        expected_snapshot_fingerprint=scope.snapshot_fingerprint,
    )
    return scope, authority


def files_snapshot(directory):
    return {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}


def database_snapshot(path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        dump = tuple(connection.iterdump())
        counts = tuple(connection.execute(
            "SELECT (SELECT count(*) FROM m2_input_inbox), "
            "(SELECT count(*) FROM m2_input_inbox WHERE appended_receipt_json IS NOT NULL), "
            "(SELECT count(*) FROM m2_effect_outbox)"
        ).fetchone())
        return dump, counts
    finally:
        connection.rollback()
        connection.close()


@pytest.mark.parametrize("reference", [None, "opaque-confirmed-worker-plan"])
def test_real_null_or_opaque_reference_is_valid_and_independent_m3_authority_evaluates_without_mutation(tmp_path, reference):
    path = tmp_path / "m2.db"
    repository, app = seed(path, reference=reference)
    activation = post(app, "DAY_PLAN_ACTIVATED", ACTIVATION_ID)
    unavailable = post(app, "UNAVAILABLE_TODAY_REPORTED", UNAVAILABLE_ID)
    assert activation["outcome"] == unavailable["outcome"] == "APPLIED"
    plan_before = repository.get_plan_day_root("plan-day")
    job_before = repository.get_job_execution_root("job-1")
    ledger_before = repository.load_processed_event_ledger()
    database_before = database_snapshot(path)
    files_before = files_snapshot(tmp_path)
    request = build_request(path)
    assert request.snapshot.source.reduction_proof_schema_version == "m2-reduction-input-proof-v2"
    assert request.snapshot.activation_lineage.activation_event_id == ACTIVATION_ID
    assert request.snapshot.plan_day.confirmed_plan_reference == reference
    assert request.snapshot.plan_day.plan_day_revision == 2
    scope, authority = trusted_m3_fixture(request)
    with pytest.raises(M3ImpactAssociationError, match="M3_ASSOCIATION_REQUIRED"):
        evaluate_unavailability_impact(request, scope)
    result = evaluate_unavailability_impact(request, scope, authority=authority)
    assert result.outcome is M3ImpactOutcome.REPLAN_REQUIRED
    assert result.affected_commitments[0].matched_historical_assignment_ids == ("assignment-1",)
    assert result.company_plan_id == "company-plan-independent"
    assert result == evaluate_unavailability_impact(build_request(path), scope, authority=authority)
    assert database_snapshot(path) == database_before
    assert files_snapshot(tmp_path) == files_before
    assert repository.get_plan_day_root("plan-day") == plan_before
    assert repository.get_job_execution_root("job-1") == job_before
    assert repository.load_processed_event_ledger() == ledger_before
    assert database_before[1] == (2, 2, 2)
    assert post(app, "DAY_PLAN_ACTIVATED", ACTIVATION_ID, retry=True)["replayed"] is True
    assert post(app, "UNAVAILABLE_TODAY_REPORTED", UNAVAILABLE_ID, retry=True)["replayed"] is True
    assert database_snapshot(path)[1] == (2, 2, 2)


@pytest.mark.parametrize("reference", [None, "old-confirmed-reference"])
def test_later_ack_preserves_historical_request_fingerprint_and_m3_result(tmp_path, reference):
    path = tmp_path / "ack-history.db"
    repository, app = seed(path, reference=reference)
    post(app, "DAY_PLAN_ACTIVATED", ACTIVATION_ID)
    post(app, "UNAVAILABLE_TODAY_REPORTED", UNAVAILABLE_ID)
    request = build_request(path)
    scope, authority = trusted_m3_fixture(request)
    result = evaluate_unavailability_impact(request, scope, authority=authority)
    repository.create_directive_root(DirectiveRoot(DirectiveDefinition(
        directive_id="later-directive", directive_type=DirectiveType.ACTION_REQUIRED,
        directive_class=DirectiveClass.ACTION, worker_id="worker-1", issued_at=NOW,
        plan_day_id="plan-day", proposed_plan_reference="later-confirmed-worker-plan",
    )))
    post(app, "WORKER_ACKNOWLEDGED", ACK_ID)
    assert repository.get_plan_day_root("plan-day").confirmed_plan_reference == "later-confirmed-worker-plan"
    after = build_request(path)
    assert after == request
    assert after.snapshot.plan_day.confirmed_plan_reference == reference
    assert after.request_fingerprint == request.request_fingerprint
    assert evaluate_unavailability_impact(after, scope, authority=authority) == result


def test_fresh_processes_and_hash_seeds_reconstruct_identical_result_with_trusted_pin(tmp_path):
    path = tmp_path / "restart.db"
    repository, app = seed(path)
    post(app, "DAY_PLAN_ACTIVATED", ACTIVATION_ID)
    post(app, "UNAVAILABLE_TODAY_REPORTED", UNAVAILABLE_ID)
    request = build_request(path)
    scope, authority = trusted_m3_fixture(request)
    scope_path, authority_path = tmp_path / "scope.json", tmp_path / "trusted-test-authority.json"
    expected = serialize_unavailability_impact_result(evaluate_unavailability_impact(request, scope, authority=authority))
    scope_document = json.loads(serialize_current_planning_scope(scope))
    # Explicit test-fixture materialization; these are not production credentials.
    scope_path.write_text(canonical_json(scope_document), encoding="utf-8")
    authority_document = asdict(authority)
    authority_document["business_date"] = authority.business_date.isoformat()
    authority_path.write_text(canonical_json(authority_document), encoding="utf-8")
    before_files, before_database = files_snapshot(tmp_path), database_snapshot(path)
    for hash_seed in ("1", "73", "random"):
        env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1")
        process = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tests/support/m3_unavailability_impact_process_probe.py"),
             str(path), str(scope_path), str(authority_path), "--event-id", UNAVAILABLE_ID],
            cwd=ROOT, env=env, capture_output=True, text=True, check=True, timeout=30,
        )
        assert process.stdout.strip() == expected
        assert files_snapshot(tmp_path) == before_files
        assert database_snapshot(path) == before_database
