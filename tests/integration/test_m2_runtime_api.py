from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
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
from werkcrew_ai.field import (
    AssignmentKind,
    AssignmentState,
    CompletionType,
    DeliveryEvidence,
    DirectiveClass,
    DirectiveDefinition,
    DirectiveRoot,
    DirectiveType,
    FieldEventEnvelope,
    FieldEventInput,
    FieldEventType,
    M2JobExecutionRoot,
    M2DurableRepository,
    M2Policy,
    M2StaleRevisionError,
    PlanDayRoot,
    PlanDayStatus,
    PolicyTimeContext,
    TaskDefinition,
    TaskState,
    TaskStatus,
    WorkerIdentityRegistry,
)
from werkcrew_ai.intake import (
    CanonicalJobIntake,
    CanonicalJobRepository,
    M1BoundaryPublicationRepository,
)


NOW = datetime(2026, 9, 6, 8, 0, tzinfo=timezone.utc)
EVENT_ID = "018f6f3e-7c45-7ac8-b0b6-6f1f93194c21"
SECOND_EVENT_ID = "018f6f3e-7c45-7ac8-b0b6-6f1f93194c22"
SERVER_EVENT_ID = "6a166438-239d-4b0a-8277-d613db328547"
SECOND_SERVER_EVENT_ID = "6a166438-239d-4b0a-8277-d613db328548"
ACK_EVENT_ID = "018f6f3e-7c45-7ac8-b0b6-6f1f93194c31"
SECOND_ACK_EVENT_ID = "018f6f3e-7c45-7ac8-b0b6-6f1f93194c32"


def _request(
    *,
    event_id: str = EVENT_ID,
    worker_id: str = "worker-api",
    plan_day_id: str = "plan-api",
    occurred_at: str = "2026-09-06T09:59:00+02:00",
):
    return {
        "event_id": event_id,
        "schema_version": 1,
        "event_type": "DAY_PLAN_ACTIVATED",
        "actor_id": worker_id,
        "occurred_at": occurred_at,
        "plan_day_id": plan_day_id,
        "offline_origin": False,
    }


def _unavailable_request(
    *,
    event_id: str = EVENT_ID,
    worker_id: str = "worker-api",
    plan_day_id: str = "plan-api",
    occurred_at: str = "2026-09-06T09:59:00+02:00",
    reason_class: str = "SICK",
):
    return {
        "event_id": event_id,
        "schema_version": 1,
        "event_type": "UNAVAILABLE_TODAY_REPORTED",
        "actor_id": worker_id,
        "occurred_at": occurred_at,
        "plan_day_id": plan_day_id,
        "reason_class": reason_class,
        "offline_origin": False,
    }


def _ack_request(
    *,
    event_id: str = ACK_EVENT_ID,
    worker_id: str = "worker-api",
    directive_id: str = "directive-api",
    occurred_at: str = "2026-09-06T09:59:00+02:00",
):
    return {
        "event_id": event_id,
        "schema_version": 1,
        "event_type": "WORKER_ACKNOWLEDGED",
        "actor_id": worker_id,
        "occurred_at": occurred_at,
        "directive_id": directive_id,
        "offline_origin": False,
    }


def _seed(
    path: Path,
    *,
    status: PlanDayStatus = PlanDayStatus.DRAFT,
) -> M2DurableRepository:
    repository = M2DurableRepository(path)
    repository.initialize(now=NOW)
    repository.create_worker_registry(WorkerIdentityRegistry(("worker-api",)))
    repository.create_plan_day_root(
        PlanDayRoot(
            "plan-api",
            "worker-api",
            date(2026, 9, 6),
            NOW - timedelta(hours=1),
            status,
            worker_available=status is not PlanDayStatus.CLOSED,
        )
    )
    return repository


def _client(
    repository: M2DurableRepository,
    *,
    generated_server_id: str = SERVER_EVENT_ID,
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[m2_repository] = lambda: repository
    app.dependency_overrides[server_timestamp] = lambda: NOW
    app.dependency_overrides[server_event_id] = lambda: generated_server_id
    return TestClient(app)


def _post(client: TestClient, request=None):
    return client.post(
        "/api/m2/field-events/day-plan-activated",
        headers={"X-WERKcrew-Worker-ID": "worker-api"},
        json=request or _request(),
    )


def _post_unavailable(client: TestClient, request=None):
    return client.post(
        "/api/m2/field-events/unavailable-today-reported",
        headers={"X-WERKcrew-Worker-ID": "worker-api"},
        json=request or _unavailable_request(),
    )


def _post_ack(client: TestClient, request=None, *, worker_id: str = "worker-api"):
    return client.post(
        "/api/m2/field-events/worker-acknowledged",
        headers={"X-WERKcrew-Worker-ID": worker_id},
        json=request or _ack_request(worker_id=worker_id),
    )


def _create_action_directive(
    repository: M2DurableRepository,
    *,
    directive_id: str = "directive-api",
    proposed_plan_reference: str = "plan-confirmed-by-ack",
    issuance_sequence: int = 1,
    supersedes_directive_id: str | None = None,
) -> DirectiveRoot:
    root = DirectiveRoot(
        DirectiveDefinition(
            directive_id,
            DirectiveType.ACTION_REQUIRED,
            DirectiveClass.ACTION,
            "worker-api",
            NOW - timedelta(minutes=5),
            plan_day_id="plan-api",
            proposed_plan_reference=proposed_plan_reference,
            issuance_sequence=issuance_sequence,
            supersedes_directive_id=supersedes_directive_id,
        )
    )
    repository.create_directive_root(root)
    return root


def _create_safe_hold_stop(repository: M2DurableRepository, path: Path) -> None:
    CanonicalJobRepository(path).create_job(
        job_id="job-stop",
        intake=CanonicalJobIntake("ack-runtime-test", None, None, ()),
        created_at=NOW - timedelta(days=1),
    )
    publication_repository = M1BoundaryPublicationRepository(path)
    publication = publication_repository.publish_handoff(
        publication_repository.current_projection("job-stop"),
        published_at=NOW - timedelta(hours=12),
    )
    task = TaskState(
        TaskDefinition(
            "task-stop",
            "task-stop-v1",
            "job-stop",
            publication.handoff_id,
            publication.source_revision,
            "Do not continue unsafe work",
            CompletionType.TASK,
        ),
        status=TaskStatus.SAFE_HOLD,
        safe_hold_directive_id="directive-stop",
        blocked_pending_resolution=True,
        execution_authorized=False,
    )
    assignment = AssignmentState(
        "assignment-stop",
        "job-stop",
        "task-stop",
        "task-stop-v1",
        AssignmentKind.SINGLE,
        ("worker-api",),
        ("plan-api",),
    )
    repository.create_job_execution_root(
        M2JobExecutionRoot("job-stop", (task,), (assignment,))
    )
    repository.create_directive_root(
        DirectiveRoot(
            DirectiveDefinition(
                "directive-stop",
                DirectiveType.STOP_DIRECTIVE,
                DirectiveClass.STOP,
                "worker-api",
                NOW - timedelta(minutes=5),
                job_id="job-stop",
                task_id="task-stop",
                assignment_id="assignment-stop",
                plan_day_id="plan-api",
            ),
            delivery_evidence=DeliveryEvidence.CHANNEL_ACCEPTED,
            stop_in_force=True,
        )
    )


def _durable_counts(repository: M2DurableRepository, event_id: str = EVENT_ID):
    with repository._connect() as connection:
        inbox = connection.execute(
            "SELECT processing_status, outcome, presented_server_event_id "
            "FROM m2_input_inbox WHERE input_namespace='FIELD_EVENT' AND input_id=?",
            (event_id,),
        ).fetchone()
        return {
            "inbox": connection.execute(
                "SELECT count(*) FROM m2_input_inbox WHERE input_namespace='FIELD_EVENT' AND input_id=?",
                (event_id,),
            ).fetchone()[0],
            "receipts": connection.execute(
                "SELECT count(*) FROM m2_input_inbox WHERE input_namespace='FIELD_EVENT' "
                "AND input_id=? AND appended_receipt_json IS NOT NULL",
                (event_id,),
            ).fetchone()[0],
            "outbox": connection.execute(
                "SELECT count(*) FROM m2_effect_outbox WHERE input_namespace='FIELD_EVENT' AND input_id=?",
                (event_id,),
            ).fetchone()[0],
            "conflicts": connection.execute(
                "SELECT count(*) FROM m2_input_conflicts WHERE input_namespace='FIELD_EVENT' AND input_id=?",
                (event_id,),
            ).fetchone()[0],
            "status": inbox["processing_status"] if inbox is not None else None,
            "outcome": inbox["outcome"] if inbox is not None else None,
            "server_event_id": (
                inbox["presented_server_event_id"] if inbox is not None else None
            ),
        }


def test_http_to_reducer_persists_one_activation_receipt_and_recorded_outbox(
    tmp_path: Path,
) -> None:
    repository = _seed(tmp_path / "activation.db")
    client = _client(repository)

    first = _post(client)
    retry = _post(client)

    assert first.status_code == retry.status_code == 200
    assert first.json()["outcome"] == "APPLIED"
    assert first.json()["replayed"] is False
    assert retry.json()["outcome"] == "APPLIED"
    assert retry.json()["replayed"] is True
    assert retry.json()["server_event_id"] == first.json()["server_event_id"]
    assert retry.json()["effects"] == first.json()["effects"]
    assert first.json()["effects"][0]["outbox_status"] == "RECORDED"
    assert first.json()["effects"][0]["external_delivery"] == "NOT_IMPLEMENTED"

    plan = repository.get_plan_day_root("plan-api")
    assert plan.status is PlanDayStatus.ACTIVE
    assert plan.plan_day_revision == 1
    ledger = repository.load_processed_event_ledger()
    assert [receipt.event.event_id for receipt in ledger.receipts] == [EVENT_ID]
    counts = _durable_counts(repository)
    assert counts == {
        "inbox": 1,
        "receipts": 1,
        "outbox": 1,
        "conflicts": 0,
        "status": "COMPLETED",
        "outcome": "APPLIED",
        "server_event_id": SERVER_EVENT_ID,
    }
    with repository._connect() as connection:
        outbox = connection.execute(
            "SELECT effect_type, dispatch_status FROM m2_effect_outbox "
            "WHERE input_namespace='FIELD_EVENT' AND input_id=?",
            (EVENT_ID,),
        ).fetchone()
    assert tuple(outbox) == ("PLAN_DAY_ACTIVATED", "RECORDED")


def test_changed_payload_for_same_event_id_is_durable_409_conflict(
    tmp_path: Path,
) -> None:
    repository = _seed(tmp_path / "conflict.db")
    client = _client(repository)
    first = _post(client)

    conflict = _post(
        client,
        _request(occurred_at="2026-09-06T10:00:00+02:00"),
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "error_code": "M2_INPUT_CONFLICT",
        "processing_status": "CONFLICT_RECORDED",
        "retryable": False,
    }
    assert repository.get_plan_day_root("plan-api").plan_day_revision == 1
    counts = _durable_counts(repository)
    assert counts["conflicts"] == 1
    assert counts["receipts"] == counts["outbox"] == 1
    assert counts["server_event_id"] == SERVER_EVENT_ID


def test_reducer_rejection_is_completed_409_without_outbox(tmp_path: Path) -> None:
    repository = _seed(tmp_path / "rejected.db", status=PlanDayStatus.CLOSED)
    client = _client(repository)

    first = _post(client)
    retry = _post(client)

    assert first.status_code == retry.status_code == 409
    assert first.json()["processing_status"] == "COMPLETED"
    assert first.json()["outcome"] == "REJECTED"
    assert first.json()["reason_codes"] == ["PLAN_DAY_NOT_ACTIVATABLE"]
    assert first.json()["effects"] == []
    assert retry.json()["replayed"] is True
    assert retry.json()["server_event_id"] == first.json()["server_event_id"]
    assert repository.get_plan_day_root("plan-api").plan_day_revision == 0
    counts = _durable_counts(repository)
    assert counts["status"] == "COMPLETED"
    assert counts["outcome"] == "REJECTED"
    assert counts["receipts"] == 1
    assert counts["outbox"] == 0


class AlwaysStaleRepository(M2DurableRepository):
    def _recheck_preconditions(self, connection, vector) -> None:
        raise M2StaleRevisionError("controlled stale CAS proof")


def test_stale_cas_is_409_and_rolls_back_every_partial_effect(tmp_path: Path) -> None:
    path = tmp_path / "stale.db"
    _seed(path)
    repository = AlwaysStaleRepository(path)

    response = _post(_client(repository))

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error_code": "STALE_CANONICAL_REVISION",
        "processing_status": "RECEIVED",
        "retryable": True,
    }
    assert repository.get_plan_day_root("plan-api").plan_day_revision == 0
    counts = _durable_counts(repository)
    assert counts["status"] == "RECEIVED"
    assert counts["receipts"] == counts["outbox"] == 0


def test_missing_canonical_bootstrap_is_503_and_is_not_auto_created(
    tmp_path: Path,
) -> None:
    repository = M2DurableRepository(tmp_path / "missing-bootstrap.db")

    response = _post(_client(repository))

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "CANONICAL_CONTEXT_NOT_READY"
    with repository._connect() as connection:
        assert connection.execute("SELECT count(*) FROM m2_worker_registry").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM m2_plan_day_roots").fetchone()[0] == 0
    counts = _durable_counts(repository)
    assert counts["status"] == "RECEIVED"
    assert counts["receipts"] == counts["outbox"] == 0


def test_corrupt_canonical_state_is_generic_503_and_fails_closed(
    tmp_path: Path,
) -> None:
    repository = _seed(tmp_path / "corrupt.db")
    with repository._connect() as connection:
        stored = connection.execute(
            "SELECT canonical_root_json FROM m2_plan_day_roots WHERE plan_day_id='plan-api'"
        ).fetchone()[0]
        document = json.loads(stored)
        document["payload"]["confirmed_plan_reference"] = 42
        raw = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        connection.execute("DROP TRIGGER m2_plan_day_roots_revision_guard")
        connection.execute(
            "UPDATE m2_plan_day_roots SET canonical_root_json=?, content_sha256=? "
            "WHERE plan_day_id='plan-api'",
            (raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()),
        )
        connection.commit()

    response = _post(_client(repository))

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error_code": "CANONICAL_STORAGE_UNAVAILABLE",
        "processing_status": "NOT_COMPLETED",
        "retryable": False,
    }
    assert "sqlite" not in response.text.lower()
    counts = _durable_counts(repository)
    # Migration/schema validation fails before durable acceptance of a new input.
    assert counts["status"] is None
    assert counts["inbox"] == counts["receipts"] == counts["outbox"] == 0


@pytest.mark.parametrize("reason", ["SICK", "PERSONAL_EMERGENCY", "OTHER"])
def test_unavailable_http_uses_real_reducer_and_persists_exact_effect(
    tmp_path: Path,
    reason: str,
) -> None:
    repository = _seed(
        tmp_path / f"unavailable-{reason.lower()}.db",
        status=PlanDayStatus.ACTIVE,
    )
    client = _client(repository)

    response = _post_unavailable(
        client,
        _unavailable_request(reason_class=reason),
    )

    assert response.status_code == 200
    assert response.json() == {
        "input_namespace": "FIELD_EVENT",
        "event_id": EVENT_ID,
        "server_event_id": SERVER_EVENT_ID,
        "processing_status": "COMPLETED",
        "outcome": "APPLIED",
        "replayed": False,
        "missing_requirements": [],
        "reason_codes": [],
        "effects": [
            {
                "effect_type": "UNAVAILABLE_TODAY_RECORDED",
                "source_id": EVENT_ID,
                "plan_day_id": "plan-api",
                "job_id": None,
                "task_id": None,
                "assignment_id": None,
                "stage_id": None,
                "directive_id": None,
                "actor_id": "worker-api",
                "details": {"reason": reason},
                "outbox_status": "RECORDED",
                "external_delivery": "NOT_IMPLEMENTED",
            }
        ],
    }
    plan = repository.get_plan_day_root("plan-api")
    assert plan.worker_available is False
    assert plan.status is PlanDayStatus.ACTIVE
    assert plan.day_close_reported is False
    assert plan.plan_day_revision == 1
    ledger = repository.load_processed_event_ledger()
    assert len(ledger.receipts) == 1
    assert ledger.receipts[0].event.reason_class == reason
    counts = _durable_counts(repository)
    assert counts == {
        "inbox": 1,
        "receipts": 1,
        "outbox": 1,
        "conflicts": 0,
        "status": "COMPLETED",
        "outcome": "APPLIED",
        "server_event_id": SERVER_EVENT_ID,
    }
    with repository._connect() as connection:
        outbox = connection.execute(
            "SELECT effect_type, dispatch_status FROM m2_effect_outbox "
            "WHERE input_namespace='FIELD_EVENT' AND input_id=?",
            (EVENT_ID,),
        ).fetchone()
    assert tuple(outbox) == ("UNAVAILABLE_TODAY_RECORDED", "RECORDED")


def test_unavailable_identical_retry_replays_without_duplicate_effect(
    tmp_path: Path,
) -> None:
    repository = _seed(tmp_path / "unavailable-retry.db", status=PlanDayStatus.ACTIVE)
    client = _client(repository)

    first = _post_unavailable(client)
    retry = _post_unavailable(client)

    assert first.status_code == retry.status_code == 200
    assert first.json()["replayed"] is False
    assert retry.json()["replayed"] is True
    assert retry.json()["server_event_id"] == first.json()["server_event_id"]
    assert retry.json()["effects"] == first.json()["effects"]
    plan = repository.get_plan_day_root("plan-api")
    assert plan.worker_available is False
    assert plan.plan_day_revision == 1
    counts = _durable_counts(repository)
    assert counts["inbox"] == counts["receipts"] == counts["outbox"] == 1


def test_unavailable_changed_reason_is_durable_conflict_and_preserves_original(
    tmp_path: Path,
) -> None:
    repository = _seed(
        tmp_path / "unavailable-conflict.db",
        status=PlanDayStatus.ACTIVE,
    )
    client = _client(repository)

    first = _post_unavailable(client, _unavailable_request(reason_class="SICK"))
    conflict = _post_unavailable(client, _unavailable_request(reason_class="OTHER"))

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "error_code": "M2_INPUT_CONFLICT",
        "processing_status": "CONFLICT_RECORDED",
        "retryable": False,
    }
    assert repository.get_plan_day_root("plan-api").plan_day_revision == 1
    receipt = repository.load_processed_event_ledger().receipt(EVENT_ID)
    assert receipt is not None
    assert receipt.event.reason_class == "SICK"
    assert dict(receipt.response_effects[0].details) == {"reason": "SICK"}
    counts = _durable_counts(repository)
    assert counts["conflicts"] == 1
    assert counts["receipts"] == counts["outbox"] == 1


def test_distinct_unavailable_event_is_reported_without_second_root_mutation(
    tmp_path: Path,
) -> None:
    repository = _seed(
        tmp_path / "unavailable-distinct.db",
        status=PlanDayStatus.ACTIVE,
    )
    first = _post_unavailable(
        _client(repository),
        _unavailable_request(event_id=EVENT_ID, reason_class="SICK"),
    )
    second = _post_unavailable(
        _client(repository, generated_server_id=SECOND_SERVER_EVENT_ID),
        _unavailable_request(event_id=SECOND_EVENT_ID, reason_class="OTHER"),
    )

    assert first.status_code == second.status_code == 200
    assert second.json()["outcome"] == "APPLIED"
    assert second.json()["replayed"] is False
    assert second.json()["effects"][0]["effect_type"] == (
        "UNAVAILABLE_TODAY_RECORDED"
    )
    assert second.json()["effects"][0]["details"] == {"reason": "OTHER"}
    plan = repository.get_plan_day_root("plan-api")
    assert plan.worker_available is False
    assert plan.plan_day_revision == 1
    ledger = repository.load_processed_event_ledger()
    assert [item.event.event_id for item in ledger.receipts] == [
        EVENT_ID,
        SECOND_EVENT_ID,
    ]
    assert _durable_counts(repository, EVENT_ID)["outbox"] == 1
    assert _durable_counts(repository, SECOND_EVENT_ID)["outbox"] == 1
    with repository._connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM m2_effect_outbox WHERE effect_type=?",
            ("UNAVAILABLE_TODAY_RECORDED",),
        ).fetchone()[0] == 2


def test_unavailable_actor_plan_mismatch_is_completed_rejection_without_effect(
    tmp_path: Path,
) -> None:
    repository = M2DurableRepository(tmp_path / "unavailable-rejected.db")
    repository.initialize(now=NOW)
    repository.create_worker_registry(
        WorkerIdentityRegistry(("worker-api", "worker-plan-owner"))
    )
    repository.create_plan_day_root(
        PlanDayRoot(
            "plan-api",
            "worker-plan-owner",
            date(2026, 9, 6),
            NOW - timedelta(hours=1),
            PlanDayStatus.ACTIVE,
        )
    )

    response = _post_unavailable(_client(repository))

    assert response.status_code == 409
    assert response.json()["processing_status"] == "COMPLETED"
    assert response.json()["outcome"] == "REJECTED"
    assert response.json()["reason_codes"] == ["PLAN_DAY_ACTOR_MISMATCH"]
    assert response.json()["effects"] == []
    plan = repository.get_plan_day_root("plan-api")
    assert plan.worker_available is True
    assert plan.plan_day_revision == 0
    counts = _durable_counts(repository)
    assert counts["status"] == "COMPLETED"
    assert counts["receipts"] == 1
    assert counts["outbox"] == 0


def test_unavailable_stale_cas_is_atomic_and_leaves_input_received(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unavailable-stale.db"
    _seed(path, status=PlanDayStatus.ACTIVE)
    repository = AlwaysStaleRepository(path)

    response = _post_unavailable(_client(repository))

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error_code": "STALE_CANONICAL_REVISION",
        "processing_status": "RECEIVED",
        "retryable": True,
    }
    plan = repository.get_plan_day_root("plan-api")
    assert plan.worker_available is True
    assert plan.plan_day_revision == 0
    counts = _durable_counts(repository)
    assert counts["status"] == "RECEIVED"
    assert counts["receipts"] == counts["outbox"] == 0


def test_action_ack_atomically_confirms_exact_plan_and_persists_one_effect(
    tmp_path: Path,
) -> None:
    path = tmp_path / "action-ack.db"
    repository = _seed(path, status=PlanDayStatus.ACTIVE)
    _create_action_directive(repository)
    repository.create_plan_day_root(
        PlanDayRoot(
            "unrelated-plan",
            "worker-api",
            date(2026, 9, 7),
            NOW + timedelta(days=1),
            PlanDayStatus.ISSUED,
            confirmed_plan_reference="unrelated-original",
        )
    )
    repository.create_directive_root(
        DirectiveRoot(
            DirectiveDefinition(
                "unrelated-directive",
                DirectiveType.INFO_NOTICE,
                DirectiveClass.INFO,
                "worker-api",
                NOW,
            )
        )
    )

    response = _post_ack(_client(repository))

    assert response.status_code == 200
    assert response.json() == {
        "input_namespace": "FIELD_EVENT",
        "event_id": ACK_EVENT_ID,
        "server_event_id": SERVER_EVENT_ID,
        "processing_status": "COMPLETED",
        "outcome": "APPLIED",
        "replayed": False,
        "missing_requirements": [],
        "reason_codes": [],
        "effects": [
            {
                "effect_type": "DIRECTIVE_ACKED",
                "source_id": ACK_EVENT_ID,
                "plan_day_id": None,
                "job_id": None,
                "task_id": None,
                "assignment_id": None,
                "stage_id": None,
                "directive_id": "directive-api",
                "actor_id": "worker-api",
                "details": {},
                "outbox_status": "RECORDED",
                "external_delivery": "NOT_IMPLEMENTED",
            }
        ],
    }
    directive = repository.get_directive_root("directive-api")
    plan = repository.get_plan_day_root("plan-api")
    assert directive.delivery_evidence is DeliveryEvidence.ACKED
    assert directive.acknowledged_event_id == ACK_EVENT_ID
    assert directive.directive_revision == 1
    assert plan.confirmed_plan_reference == "plan-confirmed-by-ack"
    assert plan.plan_day_revision == 1
    assert repository.get_plan_day_root("unrelated-plan").confirmed_plan_reference == (
        "unrelated-original"
    )
    assert repository.get_plan_day_root("unrelated-plan").plan_day_revision == 0
    assert repository.get_directive_root("unrelated-directive").directive_revision == 0
    assert _durable_counts(repository, ACK_EVENT_ID) == {
        "inbox": 1,
        "receipts": 1,
        "outbox": 1,
        "conflicts": 0,
        "status": "COMPLETED",
        "outcome": "APPLIED",
        "server_event_id": SERVER_EVENT_ID,
    }


def test_ack_identical_retry_and_changed_payload_follow_durable_identity_contract(
    tmp_path: Path,
) -> None:
    repository = _seed(tmp_path / "ack-retry.db", status=PlanDayStatus.ACTIVE)
    _create_action_directive(repository)
    client = _client(repository)

    first = _post_ack(client)
    retry = _post_ack(client)
    conflict = _post_ack(
        client,
        _ack_request(occurred_at="2026-09-06T10:00:00+02:00"),
    )

    assert first.status_code == retry.status_code == 200
    assert first.json()["replayed"] is False
    assert retry.json()["replayed"] is True
    assert retry.json()["server_event_id"] == first.json()["server_event_id"]
    assert retry.json()["effects"] == first.json()["effects"]
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "error_code": "M2_INPUT_CONFLICT",
        "processing_status": "CONFLICT_RECORDED",
        "retryable": False,
    }
    assert repository.get_directive_root("directive-api").directive_revision == 1
    assert repository.get_plan_day_root("plan-api").plan_day_revision == 1
    counts = _durable_counts(repository, ACK_EVENT_ID)
    assert counts["inbox"] == counts["receipts"] == counts["outbox"] == 1
    assert counts["conflicts"] == 1


def test_distinct_second_ack_is_durable_noop_without_duplicate_effect(
    tmp_path: Path,
) -> None:
    repository = _seed(tmp_path / "ack-noop.db", status=PlanDayStatus.ACTIVE)
    _create_action_directive(repository)

    first = _post_ack(_client(repository))
    second = _post_ack(
        _client(repository, generated_server_id=SECOND_SERVER_EVENT_ID),
        _ack_request(event_id=SECOND_ACK_EVENT_ID),
    )

    assert first.status_code == second.status_code == 200
    assert second.json()["outcome"] == "NOOP"
    assert second.json()["replayed"] is False
    assert second.json()["effects"] == []
    assert repository.get_directive_root("directive-api").directive_revision == 1
    assert repository.get_plan_day_root("plan-api").plan_day_revision == 1
    assert _durable_counts(repository, SECOND_ACK_EVENT_ID)["receipts"] == 1
    assert _durable_counts(repository, SECOND_ACK_EVENT_ID)["outbox"] == 0


def test_stop_ack_records_evidence_without_weakening_stop_or_safe_hold(
    tmp_path: Path,
) -> None:
    path = tmp_path / "stop-ack.db"
    repository = _seed(path, status=PlanDayStatus.ACTIVE)
    _create_safe_hold_stop(repository, path)
    before_plan = repository.get_plan_day_root("plan-api")
    before_job = repository.get_job_execution_root("job-stop")

    response = _post_ack(
        _client(repository),
        _ack_request(directive_id="directive-stop"),
    )

    assert response.status_code == 200
    assert response.json()["effects"][0]["effect_type"] == "DIRECTIVE_ACKED"
    directive = repository.get_directive_root("directive-stop")
    task = repository.get_job_execution_root("job-stop").task("task-stop")
    assert directive.delivery_evidence is DeliveryEvidence.ACKED
    assert directive.stop_in_force is True
    assert directive.acknowledged_event_id == ACK_EVENT_ID
    assert task is not None
    assert task.status is TaskStatus.SAFE_HOLD
    assert task.blocked_pending_resolution is True
    assert task.execution_authorized is False
    assert task.safe_hold_directive_id == "directive-stop"
    assert repository.get_plan_day_root("plan-api") == before_plan
    assert repository.get_job_execution_root("job-stop") == before_job


def test_unknown_or_foreign_worker_directive_ack_is_completed_rejection(
    tmp_path: Path,
) -> None:
    unknown_repository = _seed(
        tmp_path / "ack-unknown.db", status=PlanDayStatus.ACTIVE
    )
    unknown = _post_ack(_client(unknown_repository))
    assert unknown.status_code == 409
    assert unknown.json()["outcome"] == "REJECTED"
    assert unknown.json()["reason_codes"] == ["UNKNOWN_DIRECTIVE"]
    assert unknown.json()["effects"] == []
    assert _durable_counts(unknown_repository, ACK_EVENT_ID)["receipts"] == 1

    foreign_path = tmp_path / "ack-foreign.db"
    foreign_repository = M2DurableRepository(foreign_path)
    foreign_repository.initialize(now=NOW)
    foreign_repository.create_worker_registry(
        WorkerIdentityRegistry(("worker-api", "worker-other"))
    )
    foreign_repository.create_directive_root(
        DirectiveRoot(
            DirectiveDefinition(
                "directive-foreign",
                DirectiveType.ACTION_REQUIRED,
                DirectiveClass.ACTION,
                "worker-other",
                NOW,
            )
        )
    )
    foreign = _post_ack(
        _client(foreign_repository),
        _ack_request(directive_id="directive-foreign"),
    )
    assert foreign.status_code == 409
    assert foreign.json()["outcome"] == "REJECTED"
    assert foreign.json()["reason_codes"] == ["DIRECTIVE_ACTOR_MISMATCH"]
    assert foreign.json()["effects"] == []
    assert foreign_repository.get_directive_root(
        "directive-foreign"
    ).delivery_evidence is DeliveryEvidence.QUEUED


def test_ack_identity_failures_happen_before_durable_acceptance(
    tmp_path: Path,
) -> None:
    repository = _seed(tmp_path / "ack-identity.db", status=PlanDayStatus.ACTIVE)
    _create_action_directive(repository)
    client = _client(repository)

    missing = client.post(
        "/api/m2/field-events/worker-acknowledged",
        json=_ack_request(),
    )
    mismatch = _post_ack(
        client,
        _ack_request(),
        worker_id="worker-other",
    )

    assert missing.status_code == 401
    assert mismatch.status_code == 403
    with repository._connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM m2_input_inbox WHERE input_namespace='FIELD_EVENT'"
        ).fetchone()[0] == 0
    assert repository.get_directive_root("directive-api").directive_revision == 0
    assert repository.get_plan_day_root("plan-api").plan_day_revision == 0


def test_ack_missing_canonical_bootstrap_is_503_without_partial_completion(
    tmp_path: Path,
) -> None:
    repository = M2DurableRepository(tmp_path / "ack-missing-bootstrap.db")

    response = _post_ack(_client(repository))

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error_code": "CANONICAL_CONTEXT_NOT_READY",
        "processing_status": "NOT_COMPLETED",
        "retryable": False,
    }
    with repository._connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM m2_worker_registry"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM m2_directive_roots"
        ).fetchone()[0] == 0
    counts = _durable_counts(repository, ACK_EVENT_ID)
    assert counts["status"] == "RECEIVED"
    assert counts["receipts"] == counts["outbox"] == 0


def test_corrupt_directive_state_is_generic_503_and_not_accepted(
    tmp_path: Path,
) -> None:
    repository = _seed(tmp_path / "ack-corrupt.db", status=PlanDayStatus.ACTIVE)
    _create_action_directive(repository)
    with repository._connect() as connection:
        stored = connection.execute(
            "SELECT canonical_root_json FROM m2_directive_roots "
            "WHERE directive_id='directive-api'"
        ).fetchone()[0]
        document = json.loads(stored)
        document["payload"]["unexpected_corruption"] = True
        raw = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        connection.execute("DROP TRIGGER m2_directive_roots_revision_guard")
        connection.execute(
            "UPDATE m2_directive_roots SET canonical_root_json=?, content_sha256=? "
            "WHERE directive_id='directive-api'",
            (raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()),
        )
        connection.commit()

    response = _post_ack(_client(repository))

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error_code": "CANONICAL_STORAGE_UNAVAILABLE",
        "processing_status": "NOT_COMPLETED",
        "retryable": False,
    }
    assert "corruption" not in response.text.lower()
    assert _durable_counts(repository, ACK_EVENT_ID)["inbox"] == 0


def test_late_ack_cannot_roll_confirmed_plan_back_over_newer_ack(
    tmp_path: Path,
) -> None:
    repository = _seed(tmp_path / "late-ack.db", status=PlanDayStatus.ACTIVE)
    _create_action_directive(
        repository,
        directive_id="directive-old",
        proposed_plan_reference="plan-old",
    )
    _create_action_directive(
        repository,
        directive_id="directive-new",
        proposed_plan_reference="plan-new",
        issuance_sequence=2,
    )

    newest = _post_ack(
        _client(repository),
        _ack_request(directive_id="directive-new"),
    )
    late = _post_ack(
        _client(repository, generated_server_id=SECOND_SERVER_EVENT_ID),
        _ack_request(
            event_id=SECOND_ACK_EVENT_ID,
            directive_id="directive-old",
        ),
    )

    assert newest.status_code == late.status_code == 200
    assert repository.get_plan_day_root("plan-api").confirmed_plan_reference == (
        "plan-new"
    )
    assert repository.get_plan_day_root("plan-api").plan_day_revision == 1
    assert repository.get_directive_root("directive-new").directive_revision == 1
    assert repository.get_directive_root("directive-old").directive_revision == 1


def test_ack_stale_cas_is_atomic_and_leaves_input_received(tmp_path: Path) -> None:
    path = tmp_path / "ack-stale.db"
    seeded = _seed(path, status=PlanDayStatus.ACTIVE)
    _create_action_directive(seeded)
    repository = AlwaysStaleRepository(path)

    response = _post_ack(_client(repository))

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error_code": "STALE_CANONICAL_REVISION",
        "processing_status": "RECEIVED",
        "retryable": True,
    }
    assert repository.get_directive_root("directive-api").directive_revision == 0
    assert repository.get_plan_day_root("plan-api").plan_day_revision == 0
    counts = _durable_counts(repository, ACK_EVENT_ID)
    assert counts["status"] == "RECEIVED"
    assert counts["receipts"] == counts["outbox"] == 0


def test_concurrent_distinct_acks_produce_one_applied_and_one_noop(
    tmp_path: Path,
) -> None:
    repository = _seed(tmp_path / "ack-concurrent.db", status=PlanDayStatus.ACTIVE)
    _create_action_directive(repository)

    def send(event_id: str, server_id: str):
        return _post_ack(
            _client(repository, generated_server_id=server_id),
            _ack_request(event_id=event_id),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = tuple(
            pool.map(
                lambda values: send(*values),
                (
                    (ACK_EVENT_ID, SERVER_EVENT_ID),
                    (SECOND_ACK_EVENT_ID, SECOND_SERVER_EVENT_ID),
                ),
            )
        )

    assert sorted(item.status_code for item in responses) == [200, 200]
    assert sorted(item.json()["outcome"] for item in responses) == ["APPLIED", "NOOP"]
    assert repository.get_directive_root("directive-api").directive_revision == 1
    assert repository.get_plan_day_root("plan-api").plan_day_revision == 1
    with repository._connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM m2_effect_outbox WHERE effect_type='DIRECTIVE_ACKED'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM m2_input_inbox "
            "WHERE input_namespace='FIELD_EVENT' AND appended_receipt_json IS NOT NULL"
        ).fetchone()[0] == 2


def test_fresh_repository_reconstructs_ack_state_from_sqlite(tmp_path: Path) -> None:
    path = tmp_path / "ack-repository-restart.db"
    repository = _seed(path, status=PlanDayStatus.ACTIVE)
    _create_action_directive(repository)
    assert _post_ack(_client(repository)).status_code == 200
    del repository

    restarted = M2DurableRepository(path)
    directive = restarted.get_directive_root("directive-api")
    assert directive.delivery_evidence is DeliveryEvidence.ACKED
    assert directive.acknowledged_event_id == ACK_EVENT_ID
    assert restarted.get_plan_day_root("plan-api").confirmed_plan_reference == (
        "plan-confirmed-by-ack"
    )
    assert restarted.load_processed_event_ledger().receipt(ACK_EVENT_ID) is not None


def test_received_ack_is_completed_by_http_retry_with_original_server_claim(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ack-received-retry.db"
    repository = _seed(path, status=PlanDayStatus.ACTIVE)
    _create_action_directive(repository)
    explicit_input = FieldEventInput(
        FieldEventEnvelope(
            ACK_EVENT_ID,
            1,
            FieldEventType.WORKER_ACKNOWLEDGED,
            "worker-api",
            datetime.fromisoformat("2026-09-06T09:59:00+02:00"),
            directive_id="directive-api",
            offline_origin=False,
        ),
        SERVER_EVENT_ID,
    )
    repository.accept_input(
        explicit_input,
        PolicyTimeContext(NOW, M2Policy()),
        received_at=NOW,
    )

    response = _post_ack(
        _client(repository, generated_server_id=SECOND_SERVER_EVENT_ID)
    )

    assert response.status_code == 200
    assert response.json()["server_event_id"] == SERVER_EVENT_ID
    assert response.json()["replayed"] is False
    assert repository.get_directive_root("directive-api").directive_revision == 1
    assert _durable_counts(repository, ACK_EVENT_ID)["outbox"] == 1


def _fresh_process(
    path: Path,
    *,
    event_type: str = "DAY_PLAN_ACTIVATED",
    reason_class: str | None = None,
    directive_id: str | None = None,
):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    command = [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "support" / "m2_api_process_probe.py"),
            str(path),
            "--event-id",
            EVENT_ID,
            "--worker",
            "worker-api",
            "--plan-day",
            "plan-api",
            "--occurred-at",
            "2026-09-06T09:59:00+02:00",
            "--event-type",
            event_type,
        ]
    if reason_class is not None:
        command.extend(("--reason-class", reason_class))
    if directive_id is not None:
        command.extend(("--directive", directive_id))
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return json.loads(completed.stdout)


def test_fresh_process_http_retry_preserves_one_mutation_receipt_and_outbox(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fresh-process.db"
    _seed(path, status=PlanDayStatus.ISSUED)

    first = _fresh_process(path)
    second = _fresh_process(path)

    assert first["status_code"] == second["status_code"] == 200
    assert first["response"]["replayed"] is False
    assert second["response"]["replayed"] is True
    assert second["response"]["server_event_id"] == first["response"]["server_event_id"]
    assert second["response"]["effects"] == first["response"]["effects"]
    assert first["plan_status"] == second["plan_status"] == "ACTIVE"
    assert first["plan_revision"] == second["plan_revision"] == 1
    assert first["counts"] == second["counts"] == {
        "inbox": 1,
        "outbox": 1,
        "receipts": 1,
    }


def test_fresh_process_unavailable_retry_preserves_original_result(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fresh-process-unavailable.db"
    _seed(path, status=PlanDayStatus.ACTIVE)

    first = _fresh_process(
        path,
        event_type="UNAVAILABLE_TODAY_REPORTED",
        reason_class="SICK",
    )
    second = _fresh_process(
        path,
        event_type="UNAVAILABLE_TODAY_REPORTED",
        reason_class="SICK",
    )

    assert first["status_code"] == second["status_code"] == 200
    assert first["response"]["replayed"] is False
    assert second["response"]["replayed"] is True
    assert second["response"]["server_event_id"] == first["response"]["server_event_id"]
    assert second["response"]["effects"] == first["response"]["effects"]
    assert first["worker_available"] is second["worker_available"] is False
    assert first["plan_status"] == second["plan_status"] == "ACTIVE"
    assert first["plan_revision"] == second["plan_revision"] == 1
    assert first["counts"] == second["counts"] == {
        "inbox": 1,
        "outbox": 1,
        "receipts": 1,
    }


def test_fresh_process_ack_retry_preserves_atomic_original_result(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fresh-process-ack.db"
    repository = _seed(path, status=PlanDayStatus.ACTIVE)
    _create_action_directive(repository)

    first = _fresh_process(
        path,
        event_type="WORKER_ACKNOWLEDGED",
        directive_id="directive-api",
    )
    second = _fresh_process(
        path,
        event_type="WORKER_ACKNOWLEDGED",
        directive_id="directive-api",
    )

    assert first["status_code"] == second["status_code"] == 200
    assert first["response"]["replayed"] is False
    assert second["response"]["replayed"] is True
    assert second["response"]["server_event_id"] == first["response"]["server_event_id"]
    assert second["response"]["effects"] == first["response"]["effects"]
    assert first["directive_delivery_evidence"] == "ACKED"
    assert second["directive_delivery_evidence"] == "ACKED"
    assert first["directive_acknowledged_event_id"] == EVENT_ID
    assert second["directive_acknowledged_event_id"] == EVENT_ID
    assert first["directive_revision"] == second["directive_revision"] == 1
    assert first["plan_revision"] == second["plan_revision"] == 1
    assert first["counts"] == second["counts"] == {
        "inbox": 1,
        "outbox": 1,
        "receipts": 1,
    }
