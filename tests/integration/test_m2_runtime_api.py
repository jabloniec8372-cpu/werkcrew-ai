from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from werkcrew_ai.api.m2_runtime import (
    m2_repository,
    router,
    server_event_id,
    server_timestamp,
)
from werkcrew_ai.field import (
    M2DurableRepository,
    M2StaleRevisionError,
    PlanDayRoot,
    PlanDayStatus,
    WorkerIdentityRegistry,
)


NOW = datetime(2026, 9, 6, 8, 0, tzinfo=timezone.utc)
EVENT_ID = "018f6f3e-7c45-7ac8-b0b6-6f1f93194c21"
SECOND_EVENT_ID = "018f6f3e-7c45-7ac8-b0b6-6f1f93194c22"
SERVER_EVENT_ID = "6a166438-239d-4b0a-8277-d613db328547"


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


def _fresh_process(path: Path):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    completed = subprocess.run(
        [
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
        ],
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
