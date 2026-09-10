from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from tests.integration.test_m3_unavailability_impact_integration import (
    ACTIVATION_ID, NOW, UNAVAILABLE_ID, build_request, post, seed,
)
from tests.unit.planning.test_m3_unavailability_impact import resign_request
from werkcrew_ai.field.models import (
    AssignmentKind, AssignmentState, CompletionType, M2JobExecutionRoot, PlanDayRoot,
    PlanDayStatus, TaskDefinition, TaskState, TaskStatus, WorkerIdentityRegistry,
)
from werkcrew_ai.field.repository import M2DurableRepository
from werkcrew_ai.intake import CanonicalJobIntake, CanonicalJobRepository, M1BoundaryPublicationRepository
from werkcrew_ai.planning.current_plan import (
    CompanyPlan, M3BootstrapConflictError, M3BootstrapError, M3BootstrapStorageError,
    PlanDayAssociation, PlanningCommitment, PlanRevision, TaskDependency,
    serialize_plan_revision,
)
from werkcrew_ai.planning.current_plan_repository import CurrentPlanRepository
from werkcrew_ai.planning.m3_unavailability_impact import (
    M3ImpactBoundaryError, M3ImpactOutcome, evaluate_unavailability_impact,
    serialize_unavailability_impact_result,
)


def setup(path, *, reference=None, second_task_status=TaskStatus.OPEN):
    m2, app = seed(path, reference=reference)
    m2.create_plan_day_root(PlanDayRoot("replacement-day", "replacement", NOW.date(), NOW,
                                     PlanDayStatus.ISSUED))
    CanonicalJobRepository(path).create_job(job_id="job-2", intake=CanonicalJobIntake("b0", None, None, ()), created_at=NOW)
    pubs = M1BoundaryPublicationRepository(path)
    publication = pubs.publish_handoff(pubs.current_projection("job-2"), published_at=NOW)
    task = TaskState(TaskDefinition(task_id="task-2", definition_version="task-2-v1", job_id="job-2",
        source_handoff_id=publication.handoff_id, source_revision=publication.source_revision,
        business_meaning="Explicit independent canonical task", completion_type=CompletionType.TASK,
        assignment_kind=AssignmentKind.SINGLE), status=second_task_status)
    m2.create_job_execution_root(M2JobExecutionRoot("job-2", (task,), ()))
    post(app, "DAY_PLAN_ACTIVATED", ACTIVATION_ID)
    post(app, "UNAVAILABLE_TODAY_REPORTED", UNAVAILABLE_ID)
    request = build_request(path)
    commitments = []
    for number in (1, 2):
        definition = m2.get_job_execution_root(f"job-{number}").tasks[0].definition
        commitments.append(PlanningCommitment(
            commitment_id=f"c{number}", job_id=definition.job_id, task_id=definition.task_id,
            task_definition_version=definition.definition_version,
            source_handoff_id=definition.source_handoff_id, source_revision=definition.source_revision,
            business_date=NOW.date(), planned_worker_ids=("worker-1",) if number == 1 else ("replacement",),
            source_m2_assignment_ids=("assignment-1",) if number == 1 else (),
        ))
    company = CompanyPlan(company_plan_id="canonical-company-plan", provenance_reference="trusted-bootstrap-import")
    revision = PlanRevision(company_plan_id=company.company_plan_id, revision=0, previous_revision_id=None,
        provenance_reference="explicit-baseline-0",
        plan_days=(PlanDayAssociation(plan_day_id="plan-day", worker_id="worker-1", business_date=NOW.date()),
                   PlanDayAssociation(plan_day_id="replacement-day", worker_id="replacement", business_date=NOW.date())),
        commitments=tuple(commitments), dependencies=(TaskDependency(
            predecessor_task_id="task-1", successor_task_id="task-2", provenance_reference="explicit-constraint-record-1"),))
    return CurrentPlanRepository(path), company, revision, request, m2


def m2_rows(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'm2_%' ORDER BY name")]
        return tuple((name, tuple(conn.execute('SELECT * FROM "' + name + '" ORDER BY rowid'))) for name in names)


def file_bytes(path):
    # SQLite read-only connections can update WAL-index reader coordination.
    # Compare durable database/WAL content, not transient shared-memory locks.
    return {p.name: p.read_bytes() for p in path.parent.iterdir() if p.is_file() and not p.name.endswith("-shm")}


def next_revision(revision, **changes):
    values = dict(revision=revision.revision + 1, previous_revision_id=revision.revision_id,
                  provenance_reference=f"explicit-baseline-{revision.revision + 1}")
    values.update(changes)
    return replace(revision, **values)


@pytest.mark.parametrize("reference", [None, "opaque-not-a-company-plan"])
def test_real_persisted_producer_to_m3a_and_m2_unchanged(tmp_path, reference):
    path = tmp_path / "bootstrap.db"
    repo, company, revision, request, _ = setup(path, reference=reference)
    before = m2_rows(path)
    assert repo.import_revision(company, revision) == revision
    assert m2_rows(path) == before
    bytes_before = file_bytes(path)
    snapshot = repo.produce_current_snapshot(request)
    assert snapshot.company_plan == company
    assert snapshot.revision == revision
    assert snapshot.scope.coverage_complete is True
    assert snapshot.scope.company_plan_id == company.company_plan_id
    assert snapshot.request == request == build_request(path)
    assert [c.commitment_id for c in snapshot.scope.commitments] == ["c1"]
    result = evaluate_unavailability_impact(snapshot.request, snapshot.scope, authority=snapshot.authority)
    assert result.outcome is M3ImpactOutcome.REPLAN_REQUIRED
    assert repo.evaluate_current_unavailability(request) == result
    assert file_bytes(path) == bytes_before
    assert m2_rows(path) == before


def test_append_history_replay_current_selection_and_restart(tmp_path):
    path = tmp_path / "history.db"
    repo, company, zero, request, _ = setup(path)
    repo.import_revision(company, zero)
    one = next_revision(zero, commitments=(replace(zero.commitments[0], planned_worker_ids=("replacement",)), zero.commitments[1]))
    before = m2_rows(path)
    repo.import_revision(company, one)
    assert repo.import_revision(company, zero) == zero  # old replay must not move head backwards
    assert repo.import_revision(company, one) == one
    restarted = CurrentPlanRepository(path)
    assert restarted.get_revision(company.company_plan_id) == one
    assert restarted.get_revision(company.company_plan_id, 0) == zero
    assert restarted.evaluate_current_unavailability(request).outcome is M3ImpactOutcome.NO_REPLAN_REQUIRED
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM m3_plan_revisions").fetchone() == (2,)
    assert m2_rows(path) == before


def test_competing_or_stale_import_fails_without_partial_writes(tmp_path):
    path = tmp_path / "cas.db"
    repo, company, zero, _, _ = setup(path)
    repo.import_revision(company, zero)
    one = next_revision(zero)
    repo.import_revision(company, one)
    for wrong in (replace(one, provenance_reference="competing"), next_revision(one, previous_revision_id=zero.revision_id)):
        with pytest.raises(M3BootstrapConflictError):
            repo.import_revision(company, wrong)
    assert repo.get_revision(company.company_plan_id) == one


@pytest.mark.parametrize("recursive", [0, 1])
@pytest.mark.parametrize("table", ["m3_company_plans", "m3_plan_day_associations", "m3_plan_revisions"])
def test_immutable_sql_update_delete_replace_guards(tmp_path, recursive, table):
    path = tmp_path / "guards.db"
    repo, company, revision, _, _ = setup(path)
    repo.import_revision(company, revision)
    for operation in (f"DELETE FROM {table}", f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}",
                      f"UPDATE {table} SET company_plan_id=company_plan_id"):
        with repo.transaction() as conn:
            conn.execute(f"PRAGMA recursive_triggers={recursive}")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(operation)
    assert repo.get_revision(company.company_plan_id) == revision


def test_caller_scope_and_rehashed_request_cannot_supply_authority(tmp_path):
    path = tmp_path / "authority.db"
    repo, company, revision, request, _ = setup(path)
    with pytest.raises(M3BootstrapConflictError, match="M3_ASSOCIATION_REQUIRED"):
        repo.produce_current_snapshot(request)
    repo.import_revision(company, revision)
    snapshot = repo.produce_current_snapshot(request)
    forged_scope = replace(snapshot.scope, commitments=())
    with pytest.raises(M3ImpactBoundaryError):
        evaluate_unavailability_impact(request, forged_scope, authority=snapshot.authority)
    # A re-hashed DTO is not the durable historical cause, even if IDs still match.
    forged_request = resign_request(request, snapshot=replace(request.snapshot,
        plan_day=replace(request.snapshot.plan_day, confirmed_plan_reference="invented")))
    with pytest.raises(M3BootstrapConflictError, match="HISTORICAL_CAUSE_MISMATCH"):
        repo.produce_current_snapshot(forged_request)
    with pytest.raises(TypeError):
        repo.produce_current_snapshot(request, scope=forged_scope, authority=replace(
            snapshot.authority, expected_snapshot_fingerprint=forged_scope.snapshot_fingerprint))
    assert repo.evaluate_current_unavailability(request).outcome is M3ImpactOutcome.REPLAN_REQUIRED


def test_complete_scope_includes_current_worker_without_historical_lineage(tmp_path):
    path = tmp_path / "coverage.db"
    repo, company, revision, request, _ = setup(path)
    revision = replace(revision, commitments=(revision.commitments[0], replace(revision.commitments[1], planned_worker_ids=("worker-1",))))
    repo.import_revision(company, revision)
    result = repo.evaluate_current_unavailability(request)
    assert [c.commitment_id for c in result.affected_commitments] == ["c1", "c2"]
    assert result.affected_commitments[1].matched_historical_assignment_ids == ()
    assert "M2_ASSIGNMENT_LINEAGE_MISSING" in result.reason_codes


def test_canonical_m2_done_task_is_not_emitted_as_current_active_work(tmp_path):
    path = tmp_path / "done-filter.db"
    repo, company, revision, request, _ = setup(path, second_task_status=TaskStatus.DONE)
    done = replace(revision.commitments[1], planned_worker_ids=("worker-1",))
    done_only = replace(revision, commitments=(done,), dependencies=())
    historical_fingerprint = request.request_fingerprint
    repo.import_revision(company, done_only)
    snapshot = repo.produce_current_snapshot(request)
    assert snapshot.revision.commitments == (done,)
    assert snapshot.scope.commitments == ()
    assert repo.evaluate_current_unavailability(request).outcome is M3ImpactOutcome.NO_REPLAN_REQUIRED
    assert build_request(path) == request
    assert request.request_fingerprint == historical_fingerprint


def test_non_completed_current_worker_commitment_remains_affected(tmp_path):
    path = tmp_path / "open-visible.db"
    repo, company, revision, request, _ = setup(path)
    open_commitment = replace(revision.commitments[1], planned_worker_ids=("worker-1",))
    repo.import_revision(company, replace(revision, commitments=(open_commitment,), dependencies=()))
    snapshot = repo.produce_current_snapshot(request)
    assert tuple(item.commitment_id for item in snapshot.scope.commitments) == ("c2",)
    result = repo.evaluate_current_unavailability(request)
    assert result.outcome is M3ImpactOutcome.REPLAN_REQUIRED
    assert tuple(item.commitment_id for item in result.affected_commitments) == ("c2",)


@pytest.mark.parametrize("changes", [
    {"job_id": "missing"}, {"task_id": "task-2"}, {"task_definition_version": "wrong"},
    {"source_handoff_id": "wrong"}, {"source_revision": 999},
    {"source_m2_assignment_ids": ("missing",)},
])
def test_canonical_task_and_publication_lineage_rejected_atomically(tmp_path, changes):
    path = tmp_path / "binding.db"
    repo, company, revision, _, _ = setup(path)
    malformed = replace(revision, commitments=(replace(revision.commitments[0], **changes),), dependencies=())
    before = m2_rows(path)
    with pytest.raises(M3BootstrapError):
        repo.import_revision(company, malformed)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM m3_company_plans").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM m3_plan_day_associations").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM m3_plan_revisions").fetchone() == (0,)
    assert m2_rows(path) == before


def test_wrong_worker_date_and_cross_company_association_rejected(tmp_path):
    path = tmp_path / "association.db"
    repo, company, revision, _, _ = setup(path)
    for day in (replace(revision.plan_days[0], worker_id="wrong"),
                replace(revision.plan_days[0], business_date=NOW.date() + timedelta(days=1))):
        wrong = replace(revision, plan_days=(day,), commitments=(), dependencies=())
        with pytest.raises(M3BootstrapConflictError):
            repo.import_revision(company, wrong)
    repo.import_revision(company, revision)
    other = CompanyPlan(company_plan_id="another-plan", provenance_reference="another-source")
    with pytest.raises(M3BootstrapConflictError):
        repo.import_revision(other, replace(revision, company_plan_id=other.company_plan_id, commitments=(), dependencies=()))


def test_explicit_dependencies_are_stored_not_derived_from_m2(tmp_path):
    path = tmp_path / "dependencies.db"
    repo, company, revision, _, m2 = setup(path)
    assert m2.get_job_execution_root("job-1").tasks[0].definition.supersedes_task_id is None
    assert m2.get_job_execution_root("job-2").tasks[0].definition.supersedes_task_id is None
    repo.import_revision(company, revision)
    assert repo.get_revision(company.company_plan_id).dependencies == revision.dependencies
    empty = next_revision(revision, dependencies=())
    repo.import_revision(company, empty)
    assert repo.get_revision(company.company_plan_id).dependencies == ()
    assert repo.get_revision(company.company_plan_id, 0).dependencies == revision.dependencies


def test_current_snapshot_is_one_database_read_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "concurrent.db"
    repo, company, zero, request, _ = setup(path)
    repo.import_revision(company, zero)
    one = next_revision(zero)
    original = repo._history
    def concurrent_append(connection, company_plan_id):
        history = original(connection, company_plan_id)
        CurrentPlanRepository(path).import_revision(company, one)
        return history
    monkeypatch.setattr(repo, "_history", concurrent_append)
    assert repo.produce_current_snapshot(request).revision == zero
    assert CurrentPlanRepository(path).produce_current_snapshot(request).revision == one


def test_missing_database_read_does_not_create_artifacts(tmp_path):
    path = tmp_path / "missing.db"
    before = tuple(tmp_path.iterdir())
    with pytest.raises(M3BootstrapStorageError):
        CurrentPlanRepository(path).get_revision("unknown")
    assert tuple(tmp_path.iterdir()) == before


def test_snapshot_connection_itself_cannot_write(tmp_path):
    path = tmp_path / "readonly.db"
    repo, company, revision, _, _ = setup(path)
    repo.import_revision(company, revision)
    with repo._read_snapshot() as conn:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE forbidden_write(id TEXT)")
    assert repo.get_revision(company.company_plan_id) == revision


def test_fresh_process_producer_and_hash_seed_determinism(tmp_path):
    path = tmp_path / "restart.db"
    repo, company, revision, request, _ = setup(path)
    repo.import_revision(company, revision)
    expected = {"revision": serialize_plan_revision(revision), "revision_id": revision.revision_id,
                "impact": serialize_unavailability_impact_result(repo.evaluate_current_unavailability(request))}
    before = file_bytes(path)
    root = Path(__file__).resolve().parents[2]
    for seed_value in ("1", "73", "random"):
        completed = subprocess.run([sys.executable, "-B", str(root / "tests/support/current_plan_process_probe.py"),
            str(path), UNAVAILABLE_ID], cwd=root, env=dict(os.environ, PYTHONHASHSEED=seed_value,
                PYTHONPATH=str(root / "src"), PYTHONDONTWRITEBYTECODE="1"), check=True,
            capture_output=True, text=True, timeout=30)
        assert json.loads(completed.stdout) == expected
        assert file_bytes(path) == before


def test_actual_task_and_assignment_supersession_never_becomes_dependency(tmp_path):
    path = tmp_path / "supersession.db"
    repo, company, revision, request, m2 = setup(path)
    CanonicalJobRepository(path).create_job(job_id="job-3", intake=CanonicalJobIntake("b0", None, None, ()), created_at=NOW)
    pubs = M1BoundaryPublicationRepository(path)
    pub = pubs.publish_handoff(pubs.current_projection("job-3"), published_at=NOW)
    old = TaskState(TaskDefinition(task_id="old-task", definition_version="old-v1", job_id="job-3",
        source_handoff_id=pub.handoff_id, source_revision=pub.source_revision,
        business_meaning="Historical task definition", completion_type=CompletionType.TASK,
        assignment_kind=AssignmentKind.SINGLE))
    new = replace(old, definition=replace(old.definition, task_id="new-task", definition_version="new-v1",
        supersedes_task_id="old-task", business_meaning="Explicit superseding definition"))
    old_assignment = AssignmentState("old-assignment", "job-3", "old-task", "old-v1",
        AssignmentKind.SINGLE, ("worker-1",), ("plan-day",))
    new_assignment = AssignmentState("new-assignment", "job-3", "new-task", "new-v1",
        AssignmentKind.SINGLE, ("worker-1",), ("plan-day",), supersedes_assignment_id="old-assignment")
    m2.create_job_execution_root(M2JobExecutionRoot("job-3", (old, new), (old_assignment, new_assignment)))
    placements = tuple(PlanningCommitment(commitment_id="c-" + task.definition.task_id,
        job_id="job-3", task_id=task.definition.task_id, task_definition_version=task.definition.definition_version,
        source_handoff_id=pub.handoff_id, source_revision=pub.source_revision, business_date=NOW.date(),
        planned_worker_ids=("worker-1",), source_m2_assignment_ids=(assignment.assignment_id,))
        for task, assignment in ((old, old_assignment), (new, new_assignment)))
    revision = replace(revision, commitments=revision.commitments + placements, dependencies=())
    before = m2_rows(path)
    repo.import_revision(company, revision)
    snapshot = repo.produce_current_snapshot(request)
    assert snapshot.revision.dependencies == ()
    assert {c.task_id for c in snapshot.scope.commitments} == {"task-1", "old-task", "new-task"}
    assert len(repo.evaluate_current_unavailability(request).affected_commitments) == 3
    assert m2_rows(path) == before


def test_migration_from_0006_preserves_existing_m2_roots(tmp_path):
    from werkcrew_ai.migrations import discover_migrations
    from werkcrew_ai.persistence import MIGRATIONS_DIRECTORY

    path = tmp_path / "upgrade.db"
    first_six = discover_migrations(MIGRATIONS_DIRECTORY)[:6]
    with closing(CurrentPlanRepository(path)._connect()) as connection:
        for migration in first_six:
            connection.executescript(migration.sql)
        connection.executemany(
            "INSERT INTO schema_migrations(migration_id, version, name, checksum_sha256, applied_at) "
            "VALUES(?, ?, ?, ?, ?)",
            ((migration.migration_id, migration.version, migration.name,
              migration.checksum_sha256, NOW.isoformat()) for migration in first_six),
        )
        connection.commit()
    m2 = M2DurableRepository(path)
    m2.create_worker_registry(WorkerIdentityRegistry(("worker",)))
    m2.create_plan_day_root(PlanDayRoot("old-day", "worker", NOW.date(), NOW, PlanDayStatus.ACTIVE))
    before = m2_rows(path)
    repository = CurrentPlanRepository(path)
    assert repository.initialize(now=NOW) == (
        "0007_m3_current_plan_bootstrap",
        "0008_m3_evaluation_input",
    )
    assert repository.initialize(now=NOW) == ()
    assert m2_rows(path) == before
    with closing(sqlite3.connect(path)) as conn:
        for name in (
            "m3_company_plans", "m3_plan_revisions", "m3_plan_day_associations",
            "m3_evaluation_inputs",
        ):
            assert conn.execute(f"SELECT count(*) FROM {name}").fetchone() == (0,)


@pytest.mark.parametrize("mutation", ["json", "fingerprint", "parent", "association"])
def test_corrupt_persisted_authority_fails_closed(tmp_path, mutation):
    path = tmp_path / "corrupt.db"
    repo, company, zero, request, _ = setup(path)
    repo.import_revision(company, zero)
    repo.import_revision(company, next_revision(zero))
    # Test-only corruption bypass: production has no update/delete API.
    with repo.transaction() as conn:
        conn.execute("DROP TRIGGER m3_plan_revisions_no_update")
        conn.execute("DROP TRIGGER m3_plan_day_associations_no_update")
        conn.execute("PRAGMA ignore_check_constraints=ON")
        statements = {
            "json": "UPDATE m3_plan_revisions SET canonical_content_json='{}' WHERE revision=1",
            "fingerprint": "UPDATE m3_plan_revisions SET content_sha256='broken' WHERE revision=1",
            "parent": "UPDATE m3_plan_revisions SET previous_revision_id=NULL WHERE revision=1",
            "association": "UPDATE m3_plan_day_associations SET business_date='2026-09-07' WHERE plan_day_id='plan-day'",
        }
        conn.execute(statements[mutation])
    with pytest.raises(M3BootstrapError):
        repo.produce_current_snapshot(request)


def test_canonical_task_ownership_and_identity_survive_removal(tmp_path):
    path = tmp_path / "ownership.db"
    repo, company, zero, _, _ = setup(path)
    repo.import_revision(company, zero)
    empty = next_revision(zero, commitments=(), dependencies=())
    repo.import_revision(company, empty)
    renamed = replace(zero.commitments[0], commitment_id="renamed")
    with pytest.raises(M3BootstrapConflictError, match="COMMITMENT_IDENTITY_CONFLICT"):
        repo.import_revision(company, next_revision(empty, commitments=(renamed,)))
    other = CompanyPlan(company_plan_id="other", provenance_reference="explicit-other-source")
    with pytest.raises(M3BootstrapConflictError, match="TASK_PLAN_OWNERSHIP_CONFLICT"):
        repo.import_revision(other, replace(zero, company_plan_id=other.company_plan_id))
    assert repo.get_revision(company.company_plan_id) == empty


def test_empty_current_membership_and_removed_association_are_distinct(tmp_path):
    path = tmp_path / "empty.db"
    repo, company, zero, request, _ = setup(path)
    empty = replace(zero, commitments=(), dependencies=())
    repo.import_revision(company, empty)
    assert repo.produce_current_snapshot(request).scope.commitments == ()
    assert repo.evaluate_current_unavailability(request).outcome is M3ImpactOutcome.NO_REPLAN_REQUIRED
    repo.import_revision(company, next_revision(empty, plan_days=()))
    with pytest.raises(M3BootstrapConflictError, match="ASSOCIATION_NOT_CURRENT"):
        repo.produce_current_snapshot(request)


def test_cross_date_assignment_lineage_is_not_guessed(tmp_path):
    path = tmp_path / "lineage-date.db"
    repo, company, zero, _, m2 = setup(path)
    tomorrow = NOW.date() + timedelta(days=1)
    m2.create_plan_day_root(PlanDayRoot("tomorrow", "worker-1", tomorrow, NOW, PlanDayStatus.ISSUED))
    revision = replace(zero, plan_days=zero.plan_days + (
        PlanDayAssociation(plan_day_id="tomorrow", worker_id="worker-1", business_date=tomorrow),),
        commitments=(replace(zero.commitments[0], business_date=tomorrow),), dependencies=())
    with pytest.raises(M3BootstrapConflictError, match="ASSIGNMENT_DAY_LINEAGE_MISMATCH"):
        repo.import_revision(company, revision)


def test_later_ack_cannot_backfill_the_producers_historical_cause(tmp_path):
    from fastapi import FastAPI
    from werkcrew_ai.api.m2_runtime import m2_repository, router, server_timestamp
    from werkcrew_ai.field.models import DirectiveClass, DirectiveDefinition, DirectiveRoot, DirectiveType
    from tests.integration.test_m3_unavailability_impact_integration import ACK_ID

    path = tmp_path / "later-ack.db"
    repo, company, zero, request, m2 = setup(path)
    repo.import_revision(company, zero)
    expected = repo.produce_current_snapshot(request)
    result = repo.evaluate_current_unavailability(request)
    m2.create_directive_root(DirectiveRoot(DirectiveDefinition(directive_id="later-directive",
        directive_type=DirectiveType.ACTION_REQUIRED, directive_class=DirectiveClass.ACTION,
        worker_id="worker-1", issued_at=NOW, plan_day_id="plan-day", proposed_plan_reference="later-opaque-reference")))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[m2_repository] = lambda: m2
    app.dependency_overrides[server_timestamp] = lambda: NOW
    post(app, "WORKER_ACKNOWLEDGED", ACK_ID)
    assert m2.get_plan_day_root("plan-day").confirmed_plan_reference == "later-opaque-reference"
    before = m2_rows(path)
    assert repo.produce_current_snapshot(request) == expected
    assert repo.evaluate_current_unavailability(request) == result
    assert request.snapshot.plan_day.confirmed_plan_reference is None
    assert build_request(path).request_fingerprint == request.request_fingerprint
    assert m2_rows(path) == before
