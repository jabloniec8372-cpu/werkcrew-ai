from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from werkcrew_ai.api.m2_runtime import (
    m2_repository,
    router,
    server_event_id,
    server_timestamp,
)
from werkcrew_ai.field.models import (
    AssignmentKind,
    AssignmentState,
    CompletionType,
    DirectiveClass,
    DirectiveDefinition,
    DirectiveRoot,
    DirectiveType,
    EffectType,
    FieldEventEnvelope,
    FieldEventInput,
    FieldEventType,
    M2JobExecutionRoot,
    M2Policy,
    PolicyTimeContext,
    PlanDayRoot,
    PlanDayStatus,
    SystemEffect,
    TaskDefinition,
    TaskState,
    TaskStatus,
    WorkerIdentityRegistry,
)
from werkcrew_ai.field.reducer import reduce
from werkcrew_ai.field.repository import (
    FIELD_EVENT,
    M2DurableRepository,
    M2InsufficientHistoricalEvidenceError,
    M2NotFoundError,
    M2StorageIntegrityError,
)
from werkcrew_ai.field.serialization import (
    canonical_json,
    serialize_effect,
    sha256_text,
    verify_canonical_document,
)
from werkcrew_ai.intake import (
    CanonicalJobIntake,
    CanonicalJobRepository,
    M1BoundaryPublicationRepository,
)
from werkcrew_ai.planning.m2_bridge import (
    M2EffectSourceIdentity,
    M2UnavailableToM3Bridge,
    OperationalContextStatus,
)


NOW = datetime(2026, 9, 6, 8, 0, tzinfo=timezone.utc)
EVENT_ID = "018f6f3e-7c45-7ac8-b0b6-6f1f93194c21"
SECOND_EVENT_ID = "018f6f3e-7c45-7ac8-b0b6-6f1f93194c22"
SERVER_EVENT_ID = "6a166438-239d-4b0a-8277-d613db328547"


def _publication(path: Path, job_id: str):
    CanonicalJobRepository(path).create_job(
        job_id=job_id,
        intake=CanonicalJobIntake("m2-m3-bridge-test", None, None, ()),
        created_at=NOW - timedelta(days=1),
    )
    boundary = M1BoundaryPublicationRepository(path)
    return boundary.publish_handoff(
        boundary.current_projection(job_id),
        published_at=NOW - timedelta(hours=12),
    )


def _seed(path: Path, *, with_assignments: bool = True) -> M2DurableRepository:
    repository = M2DurableRepository(path)
    repository.initialize(now=NOW)
    repository.create_worker_registry(
        WorkerIdentityRegistry(("worker-api", "worker-crew"))
    )
    repository.create_plan_day_root(
        PlanDayRoot(
            "plan-api",
            "worker-api",
            date(2026, 9, 6),
            NOW - timedelta(hours=1),
            PlanDayStatus.ISSUED,
            confirmed_plan_reference="confirmed-plan-api",
        )
    )
    if with_assignments:
        publication = _publication(path, "job-api")
        tasks = (
            TaskState(
                TaskDefinition(
                    task_id="task-history",
                    definition_version="task-history-v1",
                    job_id="job-api",
                    source_handoff_id=publication.handoff_id,
                    source_revision=publication.source_revision,
                    business_meaning="historical task",
                    completion_type=CompletionType.TASK,
                    assignment_kind=AssignmentKind.CREW,
                ),
                status=TaskStatus.DONE,
            ),
            TaskState(
                TaskDefinition(
                    task_id="task-current",
                    definition_version="task-current-v1",
                    job_id="job-api",
                    source_handoff_id=publication.handoff_id,
                    source_revision=publication.source_revision,
                    business_meaning="current task",
                    completion_type=CompletionType.TASK,
                    assignment_kind=AssignmentKind.CREW,
                    supersedes_task_id="task-history",
                ),
                status=TaskStatus.WAITING,
            ),
        )
        assignments = (
            AssignmentState(
                assignment_id="assignment-history",
                job_id="job-api",
                task_id="task-history",
                task_definition_version="task-history-v1",
                kind=AssignmentKind.CREW,
                member_worker_ids=("worker-api", "worker-crew"),
                plan_day_ids=("plan-api",),
                lead_worker_id="worker-api",
                released_worker_ids=("worker-crew",),
            ),
            AssignmentState(
                assignment_id="assignment-current",
                job_id="job-api",
                task_id="task-current",
                task_definition_version="task-current-v1",
                kind=AssignmentKind.CREW,
                member_worker_ids=("worker-api", "worker-crew"),
                plan_day_ids=("plan-api",),
                lead_worker_id="worker-api",
                supersedes_assignment_id="assignment-history",
            ),
        )
        repository.create_job_execution_root(
            M2JobExecutionRoot("job-api", tasks, assignments)
        )
    repository.execute(
        FieldEventInput(
            FieldEventEnvelope(
                event_id="activation-event-api",
                schema_version=1,
                event_type=FieldEventType.DAY_PLAN_ACTIVATED,
                actor_id="worker-api",
                occurred_at=NOW - timedelta(minutes=30),
                plan_day_id="plan-api",
            ),
            "activation-server-api",
        ),
        PolicyTimeContext(NOW - timedelta(minutes=30), M2Policy()),
        received_at=NOW - timedelta(minutes=30),
    )
    return repository


def _client(
    repository: M2DurableRepository,
    *,
    generated_server_event_id: str = SERVER_EVENT_ID,
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[m2_repository] = lambda: repository
    app.dependency_overrides[server_timestamp] = lambda: NOW
    app.dependency_overrides[server_event_id] = lambda: generated_server_event_id
    return TestClient(app)


def _post(
    client: TestClient,
    *,
    event_id: str = EVENT_ID,
    reason: str = "SICK",
):
    return client.post(
        "/api/m2/field-events/unavailable-today-reported",
        headers={"X-WERKcrew-Worker-ID": "worker-api"},
        json={
            "event_id": event_id,
            "schema_version": 1,
            "event_type": "UNAVAILABLE_TODAY_REPORTED",
            "actor_id": "worker-api",
            "occurred_at": "2026-09-06T09:59:00+02:00",
            "plan_day_id": "plan-api",
            "reason_class": reason,
            "offline_origin": False,
        },
    )


def _counts(repository: M2DurableRepository) -> dict[str, int]:
    with repository._connect() as connection:
        return {
            "inbox": connection.execute(
                "SELECT count(*) FROM m2_input_inbox"
            ).fetchone()[0],
            "receipts": connection.execute(
                "SELECT count(*) FROM m2_input_inbox "
                "WHERE appended_receipt_json IS NOT NULL"
            ).fetchone()[0],
            "outbox": connection.execute(
                "SELECT count(*) FROM m2_effect_outbox"
            ).fetchone()[0],
            "directives": connection.execute(
                "SELECT count(*) FROM m2_directive_roots"
            ).fetchone()[0],
            "m7_proposals": connection.execute(
                "SELECT count(*) FROM replan_proposals"
            ).fetchone()[0],
            "m7_gates": connection.execute(
                "SELECT count(*) FROM pending_gates"
            ).fetchone()[0],
            "m7_decisions": connection.execute(
                "SELECT count(*) FROM owner_decisions"
            ).fetchone()[0],
        }


def _database_snapshot(path: Path) -> dict[str, tuple[tuple[object, ...], ...]]:
    uri = f"{path.as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        return {
            table: tuple(
                sorted(
                    connection.execute(f'SELECT * FROM "{table}"').fetchall(),
                    key=repr,
                )
            )
            for table in tables
        }


def _execute_field(
    repository: M2DurableRepository,
    event_id: str,
    event_type: FieldEventType,
    **values: object,
):
    return repository.execute(
        FieldEventInput(
            FieldEventEnvelope(
                event_id=event_id,
                schema_version=1,
                event_type=event_type,
                actor_id="worker-api",
                occurred_at=NOW + timedelta(minutes=1),
                **values,
            ),
            f"server-{event_id}",
        ),
        PolicyTimeContext(NOW + timedelta(minutes=1), M2Policy()),
        received_at=NOW + timedelta(minutes=1),
    )


def _create_later_job(
    path: Path,
    repository: M2DurableRepository,
    *,
    job_id: str = "job-later",
) -> None:
    publication = _publication(path, job_id)
    task_id = f"task-{job_id}"
    repository.create_job_execution_root(
        M2JobExecutionRoot(
            job_id,
            (
                TaskState(
                    TaskDefinition(
                        task_id=task_id,
                        definition_version=f"{task_id}-v1",
                        job_id=job_id,
                        source_handoff_id=publication.handoff_id,
                        source_revision=publication.source_revision,
                        business_meaning="later work",
                        completion_type=CompletionType.TASK,
                    )
                ),
            ),
            (
                AssignmentState(
                    assignment_id=f"assignment-{job_id}",
                    job_id=job_id,
                    task_id=task_id,
                    task_definition_version=f"{task_id}-v1",
                    kind=AssignmentKind.SINGLE,
                    member_worker_ids=("worker-api",),
                    plan_day_ids=("plan-api",),
                ),
            ),
        )
    )


def test_real_http_sqlite_reducer_to_bridge_preserves_lineage_without_mutation(
    tmp_path: Path,
) -> None:
    repository = _seed(tmp_path / "bridge-integration.db")
    response = _post(_client(repository))

    assert response.status_code == 200
    before_counts = _counts(repository)
    before_plan = repository.get_plan_day_root("plan-api")
    before_job = repository.get_job_execution_root("job-api")

    request = M2UnavailableToM3Bridge(repository).build_request(
        M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0)
    )

    assert request.snapshot.source.server_event_id == SERVER_EVENT_ID
    assert request.snapshot.source.effect_type == "UNAVAILABLE_TODAY_RECORDED"
    assert request.snapshot.reason.value == "SICK"
    assert request.snapshot.context_status is OperationalContextStatus.EVALUATION_REQUIRED
    assert request.snapshot.plan_day.worker_available is False
    assert request.snapshot.plan_day.plan_day_revision == 2
    assert tuple(item[:2] for item in request.snapshot.job_root_preimages) == (
        ("job-api", 0),
    )
    assert request.snapshot.activation_lineage.activation_event_id == (
        "activation-event-api"
    )
    assert [item.assignment_id for item in request.snapshot.assignments] == [
        "assignment-current",
        "assignment-history",
    ]
    assert [item.task_status for item in request.snapshot.assignments] == [
        TaskStatus.WAITING,
        TaskStatus.DONE,
    ]
    assert request.snapshot.assignments[0].supersedes_assignment_id == (
        "assignment-history"
    )
    assert request.authoritative_plan_mutation is False
    assert _counts(repository) == before_counts == {
        "inbox": 2,
        "receipts": 2,
        "outbox": 2,
        "directives": 0,
        "m7_proposals": 0,
        "m7_gates": 0,
        "m7_decisions": 0,
    }
    assert repository.get_plan_day_root("plan-api") == before_plan
    assert repository.get_job_execution_root("job-api") == before_job


def test_retry_rebuilds_identical_request_and_distinct_event_has_new_source(
    tmp_path: Path,
) -> None:
    repository = _seed(tmp_path / "bridge-retry.db")
    client = _client(repository)
    first_response = _post(client)
    retry_response = _post(client)
    bridge = M2UnavailableToM3Bridge(repository)

    first = bridge.build_request(M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0))
    replayed = bridge.build_request(M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0))
    second_response = _post(
        _client(
            repository,
            generated_server_event_id="6a166438-239d-4b0a-8277-d613db328548",
        ),
        event_id=SECOND_EVENT_ID,
        reason="OTHER",
    )
    second = bridge.build_request(
        M2EffectSourceIdentity(FIELD_EVENT, SECOND_EVENT_ID, 0)
    )

    assert first_response.status_code == retry_response.status_code == 200
    assert retry_response.json()["replayed"] is True
    assert replayed == first
    assert replayed.request_fingerprint == first.request_fingerprint
    assert second_response.status_code == 200
    assert second.snapshot.source.event_id == SECOND_EVENT_ID
    assert second.snapshot.reason.value == "OTHER"
    assert second.request_fingerprint != first.request_fingerprint
    assert repository.get_plan_day_root("plan-api").plan_day_revision == 2
    assert _counts(repository)["outbox"] == 3


def test_no_linked_assignments_is_an_explicit_request_not_feasibility_proof(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bridge-empty.db"
    repository = _seed(path, with_assignments=False)
    assert _post(_client(repository)).status_code == 200

    identity = M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0)
    request = M2UnavailableToM3Bridge(repository).build_request(identity)

    assert request.snapshot.context_status is OperationalContextStatus.NO_LINKED_ASSIGNMENTS
    assert request.snapshot.assignments == ()
    assert request.snapshot.job_root_preimages == ()
    assert request.requires_fresh_evaluation is True
    _create_later_job(path, repository, job_id="job-after-empty-proof")
    rebuilt = M2UnavailableToM3Bridge(repository).build_request(identity)
    assert rebuilt == request


def test_real_multi_job_plan_day_context_is_exact_and_deterministic(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bridge-multi-job.db"
    repository = _seed(path)
    publication = _publication(path, "job-second")
    task = TaskState(
        TaskDefinition(
            task_id="task-second",
            definition_version="task-second-v1",
            job_id="job-second",
            source_handoff_id=publication.handoff_id,
            source_revision=publication.source_revision,
            business_meaning="second job work",
            completion_type=CompletionType.TASK,
        ),
        status=TaskStatus.BLOCKED,
    )
    repository.create_job_execution_root(
        M2JobExecutionRoot(
            "job-second",
            (task,),
            (
                AssignmentState(
                    assignment_id="assignment-second",
                    job_id="job-second",
                    task_id="task-second",
                    task_definition_version="task-second-v1",
                    kind=AssignmentKind.SINGLE,
                    member_worker_ids=("worker-api",),
                    plan_day_ids=("plan-api",),
                ),
            ),
            job_execution_revision=0,
        )
    )
    assert _post(_client(repository)).status_code == 200

    request = M2UnavailableToM3Bridge(repository).build_request(
        M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0)
    )

    assert tuple(item[:2] for item in request.snapshot.job_root_preimages) == (
        ("job-api", 0),
        ("job-second", 0),
    )
    assert [item.assignment_id for item in request.snapshot.assignments] == [
        "assignment-current",
        "assignment-history",
        "assignment-second",
    ]


def _probe(path: Path, action: str, *, hash_seed: str = "random"):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    environment["PYTHONHASHSEED"] = hash_seed
    completed = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).resolve().parents[1]
                / "support"
                / "m2_m3_bridge_process_probe.py"
            ),
            str(path),
            action,
            "--event-id",
            EVENT_ID,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.stderr == ""
    return json.loads(completed.stdout)


def test_fresh_process_reconstructs_same_request_from_durable_source(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bridge-fresh-process.db"
    _seed(path)

    process_a = _probe(path, "post-unavailable")
    process_b = _probe(path, "build-request")
    process_c = _probe(path, "build-request")

    assert process_a["status_code"] == 200
    assert process_a["replayed"] is False
    assert process_b == process_c
    assert process_b["source"] == {
        "event_id": EVENT_ID,
        "server_event_id": process_a["server_event_id"],
        "effect_ordinal": 0,
    }
    assert process_b["counts"] == {
        "inbox": 2,
        "outbox": 2,
        "receipts": 2,
    }
    assert process_b["plan_revision"] == 2
    assert process_b["worker_available"] is False
    assert process_b["assignment_ids"] == [
        "assignment-current",
        "assignment-history",
    ]


def test_cross_process_fingerprint_is_independent_of_python_hash_seed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hash-seed.db"
    _seed(path)
    posted = _probe(path, "post-unavailable", hash_seed="11")
    first = _probe(path, "build-request", hash_seed="17")
    second = _probe(path, "build-request", hash_seed="991")

    assert posted["status_code"] == 200
    assert first == second
    assert len(first["fingerprint"]) == 64


def test_v2_proof_captures_exact_scope_and_ignores_all_later_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical-v2.db"
    repository = _seed(path)
    assert _post(_client(repository)).status_code == 200
    bridge = M2UnavailableToM3Bridge(repository)
    identity = M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0)
    before = bridge.build_request(identity)

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT reduction_input_proof_json, reduction_input_proof_sha256 "
            "FROM m2_input_inbox WHERE input_id=?",
            (EVENT_ID,),
        ).fetchone()
    proof = verify_canonical_document(row[0], row[1])
    assert proof["schema_version"] == "m2-reduction-input-proof-v2"
    assert proof["payload"]["complete_plan_day_assignment_scope_ids"] == [
        "plan-api"
    ]
    assert [
        item["payload"]["job_id"]
        for item in proof["payload"]["job_execution_roots"]
    ] == ["job-api"]

    _create_later_job(path, repository)
    _execute_field(
        repository,
        "later-task-change",
        FieldEventType.WORK_START_BLOCKED,
        plan_day_id="plan-api",
        job_id="job-api",
        task_id="task-current",
        assignment_id="assignment-current",
        reason_class="OTHER",
    )
    directive = DirectiveRoot(
        DirectiveDefinition(
            directive_id="later-plan-directive",
            directive_type=DirectiveType.ACTION_REQUIRED,
            directive_class=DirectiveClass.ACTION,
            worker_id="worker-api",
            issued_at=NOW + timedelta(minutes=2),
            job_id="job-api",
            task_id="task-current",
            assignment_id="assignment-current",
            plan_day_id="plan-api",
            proposed_plan_reference="confirmed-plan-v2",
        )
    )
    repository.create_directive_root(directive)
    _execute_field(
        repository,
        "later-plan-ack",
        FieldEventType.WORKER_ACKNOWLEDGED,
        plan_day_id="plan-api",
        job_id="job-api",
        task_id="task-current",
        assignment_id="assignment-current",
        directive_id=directive.directive_id,
    )
    assert repository.get_plan_day_root("plan-api").confirmed_plan_reference == (
        "confirmed-plan-v2"
    )
    assert repository.get_job_execution_root("job-api").job_execution_revision == 1

    after = M2UnavailableToM3Bridge(M2DurableRepository(path)).build_request(identity)
    assert after == before
    assert after.request_fingerprint == before.request_fingerprint
    assert after.snapshot.plan_day.confirmed_plan_reference == "confirmed-plan-api"
    assert after.snapshot.plan_day.plan_day_revision == 2
    assert {item.job_id for item in after.snapshot.assignments} == {"job-api"}
    assert {item.task_status for item in after.snapshot.assignments} == {
        TaskStatus.DONE,
        TaskStatus.WAITING,
    }


def _complete_legacy_v1_unavailable(
    repository: M2DurableRepository,
    event_id: str,
) -> FieldEventInput:
    explicit = FieldEventInput(
        FieldEventEnvelope(
            event_id=event_id,
            schema_version=1,
            event_type=FieldEventType.UNAVAILABLE_TODAY_REPORTED,
            actor_id="worker-api",
            occurred_at=NOW,
            plan_day_id="plan-api",
            reason_class="SICK",
        ),
        f"server-{event_id}",
    )
    context = PolicyTimeContext(NOW, M2Policy())
    repository.accept_input(explicit, context, received_at=NOW)
    with repository.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM m2_input_inbox WHERE input_id=?", (event_id,)
        ).fetchone()
        hydration = repository._hydrate(connection, explicit)
        reduction = repository._normalize_reduction_for_durability(
            hydration.scope,
            explicit,
            reduce(hydration.scope, explicit, context),
        )
        repository._validate_reduction_result(
            hydration.scope, reduction, hydration.preconditions
        )
        repository._recheck_preconditions(connection, hydration.preconditions)
        resulting = repository._apply_deltas(connection, reduction.root_deltas)
        repository._insert_outbox(
            connection, FIELD_EVENT, event_id, reduction.emitted_effects, NOW
        )
        repository._complete_inbox(
            connection,
            row,
            reduction,
            hydration.scope,
            hydration.routing,
            hydration.preconditions,
            resulting,
            NOW,
        )
    return explicit


def test_legacy_v1_replay_remains_valid_but_bridge_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-v1.db"
    repository = _seed(path, with_assignments=False)
    explicit = _complete_legacy_v1_unavailable(repository, "legacy-unavailable")

    replay = repository.execute(
        explicit,
        PolicyTimeContext(NOW + timedelta(hours=1), M2Policy()),
        received_at=NOW + timedelta(hours=1),
    )
    assert replay.replayed is True
    with pytest.raises(
        M2InsufficientHistoricalEvidenceError,
        match="exhaustive v2 historical operational evidence",
    ):
        M2UnavailableToM3Bridge(repository).build_request(
            M2EffectSourceIdentity(FIELD_EVENT, "legacy-unavailable", 0)
        )


def test_missing_or_corrupt_v2_completeness_fails_before_bridge_semantics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "corrupt-v2.db"
    repository = _seed(path)
    assert _post(_client(repository)).status_code == 200
    with sqlite3.connect(path) as connection:
        raw = connection.execute(
            "SELECT reduction_input_proof_json FROM m2_input_inbox WHERE input_id=?",
            (EVENT_ID,),
        ).fetchone()[0]
    proof = json.loads(raw)
    del proof["payload"]["complete_plan_day_assignment_scope_ids"]
    corrupt_raw = canonical_json(proof)
    with repository._connect() as connection:
        connection.execute("DROP TRIGGER m2_input_inbox_immutable")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE m2_input_inbox SET reduction_input_proof_json=?, "
            "reduction_input_proof_sha256=? WHERE input_id=?",
            (corrupt_raw, sha256_text(corrupt_raw), EVENT_ID),
        )
        connection.commit()

    with pytest.raises(M2StorageIntegrityError):
        M2UnavailableToM3Bridge(repository).build_request(
            M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0)
        )


def test_v2_proof_checksum_corruption_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "corrupt-v2-hash.db"
    repository = _seed(path)
    assert _post(_client(repository)).status_code == 200
    with repository._connect() as connection:
        connection.execute("DROP TRIGGER m2_input_inbox_immutable")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE m2_input_inbox SET reduction_input_proof_sha256=? "
            "WHERE input_id=?",
            ("0" * 64, EVENT_ID),
        )
        connection.commit()
    with pytest.raises(M2StorageIntegrityError):
        M2UnavailableToM3Bridge(repository).build_request(
            M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0)
        )


def test_unavailable_effect_checksum_is_verified_before_use(tmp_path: Path) -> None:
    path = tmp_path / "corrupt-unavailable-effect.db"
    repository = _seed(path)
    assert _post(_client(repository)).status_code == 200
    with repository._connect() as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("DROP TRIGGER m2_effect_outbox_update_guard")
        connection.execute(
            "UPDATE m2_effect_outbox SET effect_sha256=? WHERE input_id=?",
            ("0" * 64, EVENT_ID),
        )
        connection.commit()
    with pytest.raises(M2StorageIntegrityError):
        M2UnavailableToM3Bridge(repository).build_request(
            M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0)
        )


def test_activation_lineage_rejects_active_without_event_and_missing_or_mismatched_effect(
    tmp_path: Path,
) -> None:
    active_path = tmp_path / "active-without-lineage.db"
    repository = M2DurableRepository(active_path)
    repository.initialize(now=NOW)
    repository.create_worker_registry(
        WorkerIdentityRegistry(("worker-api", "worker-crew"))
    )
    repository.create_plan_day_root(
        PlanDayRoot(
            "plan-api",
            "worker-api",
            date(2026, 9, 6),
            NOW,
            PlanDayStatus.ACTIVE,
            confirmed_plan_reference="confirmed-plan-api",
        )
    )
    assert _post(_client(repository)).status_code == 200
    with pytest.raises(M2StorageIntegrityError, match="activation lineage"):
        M2UnavailableToM3Bridge(repository).build_request(
            M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0)
        )

    for mismatch in (False, True):
        path = tmp_path / f"activation-effect-{mismatch}.db"
        repository = _seed(path)
        assert _post(_client(repository)).status_code == 200
        with repository._connect() as connection:
            if mismatch:
                bad_raw = serialize_effect(
                    SystemEffect(
                        EffectType.PLAN_DAY_ACTIVATED,
                        "wrong-activation-event",
                        plan_day_id="plan-api",
                        actor_id="worker-api",
                    )
                )
                connection.execute("DROP TRIGGER m2_effect_outbox_update_guard")
                connection.execute(
                    "UPDATE m2_effect_outbox SET canonical_effect_json=?, "
                    "effect_sha256=? WHERE input_id='activation-event-api'",
                    (bad_raw, sha256_text(bad_raw)),
                )
            else:
                connection.execute("DROP TRIGGER m2_effect_outbox_no_delete")
                connection.execute(
                    "DELETE FROM m2_effect_outbox "
                    "WHERE input_id='activation-event-api'"
                )
            connection.commit()
        with pytest.raises(M2StorageIntegrityError):
            M2UnavailableToM3Bridge(repository).build_request(
                M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0)
            )


def test_activation_lineage_rejects_ambiguous_historical_transition(
    tmp_path: Path,
) -> None:
    path = tmp_path / "activation-ambiguous.db"
    repository = _seed(path)
    assert _post(_client(repository)).status_code == 200
    with repository._connect() as connection:
        original = dict(
            connection.execute(
                "SELECT * FROM m2_input_inbox "
                "WHERE input_id='activation-event-api'"
            ).fetchone()
        )
        original["input_id"] = "forged-ambiguous-activation"
        original["presented_server_event_id"] = "forged-ambiguous-server"
        original["claimed_server_event_id"] = "forged-ambiguous-server"
        columns = tuple(original)
        connection.execute(
            "INSERT INTO m2_input_inbox ("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            tuple(original[column] for column in columns),
        )
        connection.commit()

    with pytest.raises(M2StorageIntegrityError, match="missing or ambiguous"):
        M2UnavailableToM3Bridge(repository).build_request(
            M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0)
        )


def test_reconstruction_is_physically_read_only_and_missing_db_creates_nothing(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing" / "never-created.db"
    repository = M2DurableRepository(missing)
    with pytest.raises(M2NotFoundError, match="does not exist"):
        repository.reconstruct_unavailable_historical_context(
            FIELD_EVENT, EVENT_ID, 0
        )
    assert not missing.exists()
    assert not Path(f"{missing}-wal").exists()
    assert not Path(f"{missing}-shm").exists()
    assert not Path(f"{missing}-journal").exists()

    path = tmp_path / "read-only-state.db"
    repository = _seed(path)
    assert _post(_client(repository)).status_code == 200
    before = _database_snapshot(path)
    statements: list[str] = []

    class TracedReadRepository(M2DurableRepository):
        def _connect_read_only(self):
            connection = super()._connect_read_only()
            connection.set_trace_callback(statements.append)
            return connection

    bridge = M2UnavailableToM3Bridge(TracedReadRepository(path))
    bridge.build_request(M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0))
    joined = "\n".join(statements).lower()
    assert "m2_assignment_plan_days" not in joined
    assert "m2_job_execution_roots" not in joined
    assert "m2_plan_day_roots" not in joined
    assert not any(
        token in joined
        for token in ("insert ", "update ", "delete ", "journal_mode")
    )
    assert _database_snapshot(path) == before
    with pytest.raises(M2NotFoundError):
        bridge.build_request(M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 9))
    assert _database_snapshot(path) == before


def test_one_sqlite_snapshot_cannot_mix_in_a_concurrent_later_job(
    tmp_path: Path,
) -> None:
    path = tmp_path / "single-snapshot.db"
    repository = _seed(path)
    assert _post(_client(repository)).status_code == 200
    selected = threading.Event()
    writer_done = threading.Event()

    class InterleavingRepository(M2DurableRepository):
        def _verify_inbox_input(self, row):
            super()._verify_inbox_input(row)
            if row["input_id"] == EVENT_ID and not selected.is_set():
                selected.set()
                assert writer_done.wait(timeout=10)

    def writer() -> None:
        assert selected.wait(timeout=10)
        _create_later_job(path, M2DurableRepository(path), job_id="job-concurrent")
        writer_done.set()

    thread = threading.Thread(target=writer)
    thread.start()
    request = M2UnavailableToM3Bridge(InterleavingRepository(path)).build_request(
        M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0)
    )
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert {item.job_id for item in request.snapshot.assignments} == {"job-api"}
    assert M2DurableRepository(path).get_job_execution_root("job-concurrent")
