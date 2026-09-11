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
from fastapi import FastAPI

from tests.integration.test_current_plan_bootstrap import m2_rows, next_revision, setup
from tests.integration.test_m3_unavailability_impact_integration import (
    ACK_ID, NOW, UNAVAILABLE_ID, build_request, post,
)
from tests.unit.planning.test_m3_unavailability_impact import resign_request
from werkcrew_ai.api.m2_runtime import m2_repository, router, server_timestamp
from werkcrew_ai.field.models import (
    AssignmentKind, CompletionType, DirectiveClass, DirectiveDefinition, DirectiveRoot,
    DirectiveType, FieldEventEnvelope, FieldEventInput, FieldEventType, M2JobExecutionRoot,
    M2Policy, PolicyTimeContext, ReductionOutcome, TaskDefinition, TaskState, TaskStatus,
)
from werkcrew_ai.intake import CanonicalJobIntake, CanonicalJobRepository, M1BoundaryPublicationRepository
from werkcrew_ai.planning.current_plan import PlanningCommitment, TaskDependency
from werkcrew_ai.planning.evaluation_input import (
    CANDIDATE_ABSENCE, DependencyEvidence, DependencyImpactScope,
    M3EvaluationInputConflictError, M3EvaluationInputStorageError,
    semantic_evaluation_input_json, serialize_evaluation_input,
)
from werkcrew_ai.planning.evaluation_repository import M3EvaluationRepository
from werkcrew_ai.planning.m3_unavailability_impact import M3ImpactOutcome, M3ImpactValidationError


def m3_plan_rows(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        return tuple((name, tuple(connection.execute(f"SELECT * FROM {name} ORDER BY rowid"))) for name in (
            "m3_company_plans", "m3_plan_day_associations", "m3_plan_revisions"))


def evaluation_count(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        return connection.execute("SELECT count(*) FROM m3_evaluation_inputs").fetchone()[0]


def insert_evaluation_row(connection, value):
    connection.execute(
        "INSERT INTO m3_evaluation_inputs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            value.evaluation_input_id,
            value.evaluation_input_fingerprint,
            value.schema_version,
            value.rule_version,
            value.historical_source_scope.request_fingerprint,
            value.historical_source_scope.event_id,
            value.company_plan_id,
            value.base_plan_revision,
            value.base_plan_revision_id,
            value.current_planning_scope_fingerprint,
            semantic_evaluation_input_json(value),
        ),
    )


def build_without_record(repository, request):
    trusted = repository.produce_current_snapshot(request)
    with repository.transaction() as connection:
        return repository._build_input(connection, trusted)


def completion_event():
    return FieldEventInput(FieldEventEnvelope(
        event_id="completion-after-evaluation", schema_version=1,
        event_type=FieldEventType.TASK_COMPLETION_REPORTED, actor_id="worker-1",
        occurred_at=NOW + timedelta(minutes=1), plan_day_id="plan-day",
        job_id="job-1", task_id="task-1", assignment_id="assignment-1",
    ), "server-completion-after-evaluation")


def test_real_bridge_b0_snapshot_persists_four_distinct_scopes_without_m2_or_plan_mutation(tmp_path):
    path = tmp_path / "evaluation.db"
    b0, company, revision, request, _ = setup(path)
    b0.import_revision(company, revision)
    m2_before, plan_before = m2_rows(path), m3_plan_rows(path)
    value = M3EvaluationRepository(path).record_evaluation_input(request)
    assert value.historical_source_scope.request_fingerprint == request.request_fingerprint
    assert tuple(item.assignment_id for item in value.historical_source_scope.assignments) == ("assignment-1",)
    assert value.base_plan_revision_id == revision.revision_id
    assert value.base_plan_revision_fingerprint == revision.fingerprint
    assert tuple(item.commitment_id for item in value.current_active_commitments) == ("c1", "c2")
    assert tuple(item.commitment_id for item in value.direct_current_impact.commitments) == ("c1",)
    assert value.direct_current_impact.outcome is M3ImpactOutcome.REPLAN_REQUIRED
    assert tuple(item.commitment_id for item in value.dependency_impact.commitments) == ("c2",)
    assert value.dependency_impact.commitments[0].originating_direct_task_ids == ("task-1",)
    assert value.candidate_induced_impact.status == CANDIDATE_ABSENCE
    assert value.candidate_induced_impact.commitments == ()
    assert value.candidate_induced_impact.candidate_fingerprint is None
    assert value.current_m2_task_preconditions[0].job_root_sha256
    assert value.current_active_commitments[0].source_handoff_sha256
    assert build_request(path) == request
    assert m2_rows(path) == m2_before
    assert m3_plan_rows(path) == plan_before


def test_duplicate_replay_and_restart_reconstruct_exact_input(tmp_path):
    path = tmp_path / "restart.db"
    b0, company, revision, request, _ = setup(path)
    b0.import_revision(company, revision)
    repository = M3EvaluationRepository(path)
    first = repository.record_evaluation_input(request)
    assert repository.record_evaluation_input(request) == first
    assert evaluation_count(path) == 1
    restarted = M3EvaluationRepository(path)
    assert restarted.get_evaluation_input(first.evaluation_input_id) == first
    assert serialize_evaluation_input(restarted.get_evaluation_input(first.evaluation_input_id)) == serialize_evaluation_input(first)


def test_exact_revision_tuple_and_complete_dependency_snapshot_persist_and_reconstruct(tmp_path):
    path = tmp_path / "exact-revision.db"
    b0, company, revision, request, _ = setup(path)
    b0.import_revision(company, revision)
    repository = M3EvaluationRepository(path)
    value = repository.record_evaluation_input(request)
    assert (
        value.company_plan_id,
        value.base_plan_revision,
        value.base_plan_revision_id,
        value.base_plan_revision_fingerprint,
    ) == (
        revision.company_plan_id,
        revision.revision,
        revision.revision_id,
        revision.fingerprint,
    )
    assert tuple(
        (edge.predecessor_task_id, edge.successor_task_id, edge.relation,
         edge.provenance_reference)
        for edge in value.dependency_impact.edges
    ) == tuple(
        (edge.predecessor_task_id, edge.successor_task_id, edge.relation,
         edge.provenance_reference)
        for edge in revision.dependencies
    )
    assert M3EvaluationRepository(path).get_evaluation_input(value.evaluation_input_id) == value


def test_cross_revision_tuple_forgery_is_rejected_by_writer_database_and_reader(
    tmp_path, monkeypatch,
):
    path = tmp_path / "cross-revision.db"
    b0, company, zero, request, _ = setup(path)
    b0.import_revision(company, zero)
    one = next_revision(zero, dependencies=())
    b0.import_revision(company, one)
    repository = M3EvaluationRepository(path)
    valid = build_without_record(repository, request)
    forged = replace(valid, base_plan_revision=0)
    assert forged.base_plan_revision == zero.revision
    assert (forged.base_plan_revision_id, forged.base_plan_revision_fingerprint) == (
        one.revision_id, one.fingerprint)

    monkeypatch.setattr(repository, "_build_input", lambda _connection, _trusted: forged)
    with pytest.raises(M3EvaluationInputConflictError, match="BASE_PLAN_REVISION_MISMATCH"):
        repository.record_evaluation_input(request)
    assert evaluation_count(path) == 0

    with pytest.raises(sqlite3.IntegrityError, match="one exact PlanRevision"):
        with repository.transaction() as connection:
            insert_evaluation_row(connection, forged)
    assert evaluation_count(path) == 0

    with closing(repository._connect()) as connection:
        connection.execute("DROP TRIGGER m3_evaluation_inputs_exact_revision")
        insert_evaluation_row(connection, forged)
        connection.commit()
    with pytest.raises(M3EvaluationInputStorageError, match="BASE_PLAN_REVISION_MISMATCH"):
        repository.get_evaluation_input(forged.evaluation_input_id)


@pytest.mark.parametrize("edge", [
    DependencyEvidence(
        predecessor_task_id="task-2", successor_task_id="task-1",
        relation="FINISH_BEFORE_START", provenance_reference="invented-reverse-edge",
    ),
    DependencyEvidence(
        predecessor_task_id="task-1", successor_task_id="task-outside-base-revision",
        relation="FINISH_BEFORE_START", provenance_reference="invented-dangling-edge",
    ),
], ids=("invented-edge", "dangling-endpoint"))
def test_dependency_evidence_not_in_exact_base_revision_is_rejected_before_write_and_on_read(
    tmp_path, monkeypatch, edge,
):
    path = tmp_path / f"dependency-{edge.provenance_reference}.db"
    b0, company, revision, request, _ = setup(path)
    b0.import_revision(company, revision)
    repository = M3EvaluationRepository(path)
    valid = build_without_record(repository, request)
    forged = replace(valid, dependency_impact=DependencyImpactScope(
        edges=(edge,), commitments=()))

    monkeypatch.setattr(repository, "_build_input", lambda _connection, _trusted: forged)
    with pytest.raises(M3EvaluationInputConflictError, match="BASE_PLAN_DEPENDENCIES_MISMATCH"):
        repository.record_evaluation_input(request)
    assert evaluation_count(path) == 0

    with repository.transaction() as connection:
        insert_evaluation_row(connection, forged)
    with pytest.raises(M3EvaluationInputStorageError, match="BASE_PLAN_DEPENDENCIES_MISMATCH"):
        repository.get_evaluation_input(forged.evaluation_input_id)


def test_dependency_edge_from_another_revision_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "cross-revision-dependency.db"
    b0, company, zero, request, _ = setup(path)
    b0.import_revision(company, zero)
    one = next_revision(zero, dependencies=())
    b0.import_revision(company, one)
    repository = M3EvaluationRepository(path)
    valid = build_without_record(repository, request)
    edge = zero.dependencies[0]
    forged = replace(valid, dependency_impact=DependencyImpactScope(
        edges=(DependencyEvidence(
            predecessor_task_id=edge.predecessor_task_id,
            successor_task_id=edge.successor_task_id,
            relation=edge.relation,
            provenance_reference=edge.provenance_reference,
        ),), commitments=()))
    monkeypatch.setattr(repository, "_build_input", lambda _connection, _trusted: forged)
    with pytest.raises(M3EvaluationInputConflictError, match="BASE_PLAN_DEPENDENCIES_MISMATCH"):
        repository.record_evaluation_input(request)
    assert evaluation_count(path) == 0


def test_incomplete_dependency_snapshot_is_not_accepted_as_full_revision_truth(
    tmp_path, monkeypatch,
):
    path = tmp_path / "missing-dependency.db"
    b0, company, revision, request, _ = setup(path)
    b0.import_revision(company, revision)
    repository = M3EvaluationRepository(path)
    valid = build_without_record(repository, request)
    assert valid.dependency_impact.edges
    forged = replace(valid, dependency_impact=DependencyImpactScope(
        edges=(), commitments=()))
    monkeypatch.setattr(repository, "_build_input", lambda _connection, _trusted: forged)
    with pytest.raises(M3EvaluationInputConflictError, match="BASE_PLAN_DEPENDENCIES_MISMATCH"):
        repository.record_evaluation_input(request)
    assert evaluation_count(path) == 0


def test_new_plan_revision_creates_new_evaluation_identity_and_preserves_old_record(tmp_path):
    path = tmp_path / "revision.db"
    b0, company, zero, request, _ = setup(path)
    b0.import_revision(company, zero)
    repository = M3EvaluationRepository(path)
    old = repository.record_evaluation_input(request)
    one = next_revision(zero, dependencies=())
    b0.import_revision(company, one)
    new = repository.record_evaluation_input(request)
    assert new.base_plan_revision == 1 and new.base_plan_revision_id == one.revision_id
    assert new.evaluation_input_id != old.evaluation_input_id
    assert new.dependency_impact.edges == () and new.dependency_impact.commitments == ()
    assert repository.get_evaluation_input(old.evaluation_input_id) == old
    assert evaluation_count(path) == 2


def test_current_m2_done_changes_fresh_input_but_not_historical_bridge_or_plan_history(tmp_path):
    path = tmp_path / "done.db"
    b0, company, revision, request, m2 = setup(path)
    b0.import_revision(company, revision)
    repository = M3EvaluationRepository(path)
    before = repository.record_evaluation_input(request)
    plan_before = m3_plan_rows(path)
    result = m2.execute(completion_event(), PolicyTimeContext(NOW + timedelta(minutes=1), M2Policy()),
                        received_at=NOW + timedelta(minutes=1))
    assert result.outcome is ReductionOutcome.APPLIED
    after_request = build_request(path)
    after = repository.record_evaluation_input(after_request)
    assert after_request == request
    assert after.historical_source_scope == before.historical_source_scope
    assert after.base_plan_revision_id == before.base_plan_revision_id
    assert after.current_planning_scope_fingerprint != before.current_planning_scope_fingerprint
    assert after.evaluation_input_id != before.evaluation_input_id
    assert tuple(item.commitment_id for item in after.current_active_commitments) == ("c2",)
    assert next(item for item in after.current_m2_task_preconditions if item.task_id == "task-1").task_status is TaskStatus.DONE
    assert after.direct_current_impact.commitments == ()
    assert after.direct_current_impact.outcome is M3ImpactOutcome.NO_REPLAN_REQUIRED
    assert after.dependency_impact.commitments == ()
    assert m3_plan_rows(path) == plan_before


def test_later_ack_does_not_rewrite_historical_proof_or_semantic_evaluation_input(tmp_path):
    path = tmp_path / "ack.db"
    b0, company, revision, request, m2 = setup(path)
    b0.import_revision(company, revision)
    repository = M3EvaluationRepository(path)
    before = repository.record_evaluation_input(request)
    m2.create_directive_root(DirectiveRoot(DirectiveDefinition(
        directive_id="later-directive", directive_type=DirectiveType.ACTION_REQUIRED,
        directive_class=DirectiveClass.ACTION, worker_id="worker-1", issued_at=NOW,
        plan_day_id="plan-day", proposed_plan_reference="later-opaque-reference")))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[m2_repository] = lambda: m2
    app.dependency_overrides[server_timestamp] = lambda: NOW
    post(app, "WORKER_ACKNOWLEDGED", ACK_ID)
    after_request = build_request(path)
    after = repository.record_evaluation_input(after_request)
    assert after_request == request
    assert after_request.request_fingerprint == request.request_fingerprint
    assert after_request.snapshot.plan_day.confirmed_plan_reference is None
    assert after == before
    assert evaluation_count(path) == 1


def test_stale_b0_snapshot_rejected_without_partial_record(tmp_path, monkeypatch):
    path = tmp_path / "stale.db"
    b0, company, zero, request, _ = setup(path)
    b0.import_revision(company, zero)
    repository = M3EvaluationRepository(path)
    stale = repository.produce_current_snapshot(request)
    b0.import_revision(company, next_revision(zero, dependencies=()))
    monkeypatch.setattr(repository, "produce_current_snapshot", lambda _request: stale)
    with pytest.raises(M3EvaluationInputConflictError, match="CURRENT_PLAN_REVISION_CHANGED"):
        repository.record_evaluation_input(request)
    assert evaluation_count(path) == 0


def test_mismatched_historical_request_rejected_without_record(tmp_path):
    path = tmp_path / "mismatch.db"
    b0, company, revision, request, _ = setup(path)
    b0.import_revision(company, revision)
    forged = resign_request(request, snapshot=replace(request.snapshot,
        plan_day=replace(request.snapshot.plan_day, worker_id="replacement")))
    with pytest.raises(M3ImpactValidationError, match="request.assignment_scope"):
        M3EvaluationRepository(path).record_evaluation_input(forged)
    assert evaluation_count(path) == 0


def test_exact_0007_database_upgrades_through_current_head_without_mutating_m2_or_plan_state(tmp_path):
    path = tmp_path / "upgrade.db"
    b0, company, revision, _, _ = setup(path)
    b0.import_revision(company, revision)
    with closing(sqlite3.connect(path)) as connection:
        for table in (
            "m5_internal_cost_support_selections",
            "m5_internal_cost_support_subjects",
            "m5_internal_cost_support_candidates",
            "m5_internal_cost_support_cuts",
            "m5_internal_cost_source_captures",
            "m5_internal_labor_rate_sources",
            "m5_internal_cost_rules",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_migrations WHERE version=10")
        for trigger in (
            "m3_feasibility_support_bindings_no_update",
            "m3_feasibility_support_bindings_no_delete",
            "m3_feasibility_support_bindings_no_replace",
        ):
            connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute("DROP TABLE m3_feasibility_support_source_bindings")
        for trigger in (
            "m3_feasibility_support_exact_input_revision",
            "m3_feasibility_support_no_update",
            "m3_feasibility_support_no_delete",
            "m3_feasibility_support_no_replace",
        ):
            connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute("DROP TABLE m3_feasibility_support_snapshots")
        for trigger in (
            "m3_feasibility_source_cuts_exact_capture",
            "m3_feasibility_source_cuts_no_update",
            "m3_feasibility_source_cuts_no_delete",
            "m3_feasibility_source_cuts_no_replace",
        ):
            connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute("DROP TABLE m3_feasibility_source_selection_cuts")
        for trigger in (
            "m3_feasibility_worker_registry_captures_append",
            "m3_feasibility_worker_registry_captures_no_update",
            "m3_feasibility_worker_registry_captures_no_delete",
            "m3_feasibility_worker_registry_captures_no_replace",
        ):
            connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute(
            "DROP TABLE m3_feasibility_worker_registry_captures"
        )
        for trigger in (
            "m3_feasibility_worker_registry_provenance_current_authority",
            "m3_feasibility_worker_registry_provenance_no_update",
            "m3_feasibility_worker_registry_provenance_no_delete",
            "m3_feasibility_worker_registry_provenance_no_replace",
        ):
            connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute(
            "DROP TABLE m3_feasibility_worker_registry_provenance"
        )
        for trigger in (
            "m3_feasibility_sources_append",
            "m3_feasibility_task_binding_immutable",
            "m3_feasibility_schedule_exact_revision",
            "m3_feasibility_sources_no_update",
            "m3_feasibility_sources_no_delete",
            "m3_feasibility_sources_no_replace",
        ):
            connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute("DROP TABLE m3_feasibility_source_records")
        for trigger in (
            "m3_feasibility_m8_configurations_no_update",
            "m3_feasibility_m8_configurations_no_delete",
            "m3_feasibility_m8_configurations_no_replace",
        ):
            connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute("DROP TABLE m3_feasibility_m8_configurations")
        connection.execute("DELETE FROM schema_migrations WHERE version=9")
        for trigger in (
            "m3_evaluation_inputs_no_update",
            "m3_evaluation_inputs_no_delete",
            "m3_evaluation_inputs_no_replace",
        ):
            connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute("DROP TABLE m3_evaluation_inputs")
        connection.execute("DELETE FROM schema_migrations WHERE version=8")
        connection.commit()
    before_m2, before_plan = m2_rows(path), m3_plan_rows(path)
    repository = M3EvaluationRepository(path)
    assert repository.initialize(now=NOW) == (
        "0008_m3_evaluation_input",
        "0009_m3_feasibility_support",
        "0010_m5_internal_labor_cost_support",
    )
    assert repository.initialize(now=NOW) == ()
    assert m2_rows(path) == before_m2
    assert m3_plan_rows(path) == before_plan
    assert evaluation_count(path) == 0


@pytest.mark.parametrize("column,value", [
    ("canonical_semantic_json", "{}"),
    ("evaluation_input_fingerprint", "broken"),
    ("base_plan_revision_id", "wrong"),
    ("current_scope_fingerprint", "0" * 64),
])
def test_corrupted_stored_evaluation_fails_closed(tmp_path, column, value):
    path = tmp_path / "corrupt.db"
    b0, company, revision, request, _ = setup(path)
    b0.import_revision(company, revision)
    repository = M3EvaluationRepository(path)
    item = repository.record_evaluation_input(request)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN")
        connection.execute("DROP TRIGGER m3_evaluation_inputs_no_update")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(f"UPDATE m3_evaluation_inputs SET {column}=?", (value,))
        connection.commit()
    with pytest.raises(M3EvaluationInputStorageError):
        repository.get_evaluation_input(item.evaluation_input_id)


@pytest.mark.parametrize("recursive", [0, 1])
@pytest.mark.parametrize("operation", [
    "UPDATE m3_evaluation_inputs SET source_event_id=source_event_id",
    "DELETE FROM m3_evaluation_inputs",
    "INSERT OR REPLACE INTO m3_evaluation_inputs SELECT * FROM m3_evaluation_inputs",
])
def test_sql_rows_are_immutable_and_replace_independent(tmp_path, recursive, operation):
    path = tmp_path / "guards.db"
    b0, company, revision, request, _ = setup(path)
    b0.import_revision(company, revision)
    repository = M3EvaluationRepository(path)
    original = repository.record_evaluation_input(request)
    with repository.transaction() as connection:
        connection.execute(f"PRAGMA recursive_triggers={recursive}")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(operation)
    assert repository.get_evaluation_input(original.evaluation_input_id) == original


def test_dependency_traversal_is_explicit_transitive_and_ordered(tmp_path):
    path = tmp_path / "dependency.db"
    b0, company, revision, request, m2 = setup(path)
    CanonicalJobRepository(path).create_job(job_id="job-3", intake=CanonicalJobIntake("m3-b", None, None, ()), created_at=NOW)
    publications = M1BoundaryPublicationRepository(path)
    publication = publications.publish_handoff(publications.current_projection("job-3"), published_at=NOW)
    task = TaskState(TaskDefinition(task_id="task-3", definition_version="task-3-v1", job_id="job-3",
        source_handoff_id=publication.handoff_id, source_revision=publication.source_revision,
        business_meaning="Transitive dependency target", completion_type=CompletionType.TASK,
        assignment_kind=AssignmentKind.SINGLE))
    m2.create_job_execution_root(M2JobExecutionRoot("job-3", (task,), ()))
    third = PlanningCommitment(commitment_id="c3", job_id="job-3", task_id="task-3",
        task_definition_version="task-3-v1", source_handoff_id=publication.handoff_id,
        source_revision=publication.source_revision, business_date=NOW.date(),
        planned_worker_ids=("replacement",), source_m2_assignment_ids=())
    expanded = replace(revision, commitments=revision.commitments + (third,),
        dependencies=revision.dependencies + (TaskDependency(predecessor_task_id="task-2",
            successor_task_id="task-3", provenance_reference="explicit-constraint-record-2"),))
    b0.import_revision(company, expanded)
    value = M3EvaluationRepository(path).record_evaluation_input(request)
    assert tuple(item.task_id for item in value.dependency_impact.commitments) == ("task-2", "task-3")
    assert all(item.originating_direct_task_ids == ("task-1",) for item in value.dependency_impact.commitments)


def test_done_dependency_node_stops_dependency_propagation(tmp_path):
    path = tmp_path / "done-edge.db"
    b0, company, revision, request, _ = setup(path, second_task_status=TaskStatus.DONE)
    b0.import_revision(company, revision)
    value = M3EvaluationRepository(path).record_evaluation_input(request)
    assert tuple(item.commitment_id for item in value.direct_current_impact.commitments) == ("c1",)
    assert value.dependency_impact.commitments == ()


def test_fresh_process_replay_is_deterministic_across_hash_seeds(tmp_path):
    path = tmp_path / "process.db"
    b0, company, revision, request, _ = setup(path)
    b0.import_revision(company, revision)
    expected = serialize_evaluation_input(M3EvaluationRepository(path).record_evaluation_input(request))
    root = Path(__file__).resolve().parents[2]
    for seed_value in ("1", "73", "random"):
        completed = subprocess.run(
            [sys.executable, "-B", str(root / "tests/support/evaluation_input_process_probe.py"),
             str(path), UNAVAILABLE_ID], cwd=root,
            env=dict(os.environ, PYTHONHASHSEED=seed_value, PYTHONPATH=str(root / "src"),
                     PYTHONDONTWRITEBYTECODE="1"), capture_output=True, text=True,
            check=True, timeout=30,
        )
        assert completed.stdout.strip() == expected
        assert evaluation_count(path) == 1
