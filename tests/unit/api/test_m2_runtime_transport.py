from __future__ import annotations

from datetime import datetime, timezone

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
    DurableReductionResult,
    EffectType,
    ReductionOutcome,
    SystemEffect,
)


NOW = datetime(2026, 9, 6, 8, 0, tzinfo=timezone.utc)
EVENT_ID = "018f6f3e-7c45-7ac8-b0b6-6f1f93194c21"
SERVER_EVENT_ID = "6a166438-239d-4b0a-8277-d613db328547"


def _request(**changes):
    value = {
        "event_id": EVENT_ID,
        "schema_version": 1,
        "event_type": "DAY_PLAN_ACTIVATED",
        "actor_id": "worker-api",
        "occurred_at": "2026-09-06T09:59:00+02:00",
        "plan_day_id": "plan-api",
        "offline_origin": False,
    }
    value.update(changes)
    return value


class RecordingRepository:
    def __init__(self, result: DurableReductionResult) -> None:
        self.result = result
        self.initialized_at = None
        self.call = None

    def initialize(self, *, now):
        self.initialized_at = now
        return ()

    def execute(self, explicit_input, context, *, received_at):
        self.call = (explicit_input, context, received_at)
        return self.result


class InitializationFailureRepository(RecordingRepository):
    def __init__(self, error: BaseException) -> None:
        super().__init__(_result())
        self.error = error

    def initialize(self, *, now):
        self.initialized_at = now
        raise self.error


def _result() -> DurableReductionResult:
    response_effect = SystemEffect(
        EffectType.PLAN_DAY_ACTIVATED,
        EVENT_ID,
        plan_day_id="plan-api",
        actor_id="worker-api",
    )
    return DurableReductionResult(
        input_namespace="FIELD_EVENT",
        input_id=EVENT_ID,
        outcome=ReductionOutcome.APPLIED,
        root_deltas=(),
        appended_receipt=None,
        emitted_effects=(),
        response_effects=(response_effect,),
        server_event_id=SERVER_EVENT_ID,
        missing_requirements=(),
        reason_codes=(),
        replayed=True,
    )


def _client(repository: RecordingRepository) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[m2_repository] = lambda: repository
    app.dependency_overrides[server_timestamp] = lambda: NOW
    app.dependency_overrides[server_event_id] = lambda: SERVER_EVENT_ID
    return TestClient(app)


def test_valid_request_maps_exact_event_and_uses_one_server_timestamp() -> None:
    repository = RecordingRepository(_result())
    response = _client(repository).post(
        "/api/m2/field-events/day-plan-activated",
        headers={"X-WERKcrew-Worker-ID": "worker-api"},
        json=_request(),
    )

    assert response.status_code == 200
    explicit_input, context, received_at = repository.call
    assert repository.initialized_at == received_at == context.now == NOW
    assert explicit_input.server_event_id == SERVER_EVENT_ID
    assert explicit_input.event.event_id == EVENT_ID
    assert explicit_input.event.event_type.value == "DAY_PLAN_ACTIVATED"
    assert explicit_input.event.schema_version == 1
    assert explicit_input.event.actor_id == "worker-api"
    assert explicit_input.event.plan_day_id == "plan-api"
    assert explicit_input.event.job_id is None
    assert explicit_input.event.task_id is None
    assert explicit_input.event.assignment_id is None
    assert explicit_input.event.directive_id is None

    body = response.json()
    assert body["server_event_id"] == SERVER_EVENT_ID
    assert body["replayed"] is True
    assert body["effects"] == [
        {
            "effect_type": "PLAN_DAY_ACTIVATED",
            "source_id": EVENT_ID,
            "plan_day_id": "plan-api",
            "job_id": None,
            "task_id": None,
            "assignment_id": None,
            "stage_id": None,
            "directive_id": None,
            "actor_id": "worker-api",
            "details": {},
            "outbox_status": "RECORDED",
            "external_delivery": "NOT_IMPLEMENTED",
        }
    ]
    # emitted_effects was deliberately empty: the mapper must use response_effects.
    assert body["effects"][0]["effect_type"] == "PLAN_DAY_ACTIVATED"


@pytest.mark.parametrize(
    ("changes", "expected_fragment"),
    [
        ({"event_id": "not-a-uuid"}, "event_id"),
        ({"event_id": 123}, "event_id"),
        ({"event_id": True}, "event_id"),
        ({"event_id": None}, "event_id"),
        ({"schema_version": True}, "schema_version"),
        ({"schema_version": False}, "schema_version"),
        ({"schema_version": 1.0}, "schema_version"),
        ({"schema_version": "1"}, "schema_version"),
        ({"schema_version": 2}, "schema_version"),
        ({"event_type": "START_DELAY_REPORTED"}, "event_type"),
        ({"event_type": 1}, "event_type"),
        ({"occurred_at": "2026-09-06T08:00:00"}, "occurred_at"),
        ({"occurred_at": "0"}, "occurred_at"),
        ({"occurred_at": "1.5"}, "occurred_at"),
        ({"occurred_at": "1234567890"}, "occurred_at"),
        ({"occurred_at": 0}, "occurred_at"),
        ({"occurred_at": 1234567890}, "occurred_at"),
        ({"occurred_at": 1.5}, "occurred_at"),
        ({"occurred_at": True}, "occurred_at"),
        ({"actor_id": "   "}, "actor_id"),
        ({"actor_id": 123}, "actor_id"),
        ({"plan_day_id": "   "}, "plan_day_id"),
        ({"plan_day_id": 123}, "plan_day_id"),
        ({"offline_origin": "true"}, "offline_origin"),
        ({"offline_origin": "false"}, "offline_origin"),
        ({"offline_origin": "yes"}, "offline_origin"),
        ({"offline_origin": "no"}, "offline_origin"),
        ({"offline_origin": 0}, "offline_origin"),
        ({"offline_origin": 1}, "offline_origin"),
        ({"offline_origin": None}, "offline_origin"),
    ],
)
def test_malformed_transport_is_rejected_before_durable_acceptance(
    changes,
    expected_fragment,
) -> None:
    repository = RecordingRepository(_result())
    response = _client(repository).post(
        "/api/m2/field-events/day-plan-activated",
        headers={"X-WERKcrew-Worker-ID": "worker-api"},
        json=_request(**changes),
    )

    assert response.status_code == 422
    assert expected_fragment in response.text
    assert repository.initialized_at is None
    assert repository.call is None


@pytest.mark.parametrize(
    "occurred_at",
    ["2026-09-06T08:00:00Z", "2026-09-06T10:00:00+02:00"],
)
def test_aware_iso_datetime_strings_remain_accepted(occurred_at: str) -> None:
    repository = RecordingRepository(_result())
    response = _client(repository).post(
        "/api/m2/field-events/day-plan-activated",
        headers={"X-WERKcrew-Worker-ID": "worker-api"},
        json=_request(occurred_at=occurred_at),
    )

    assert response.status_code == 200
    assert repository.initialized_at == NOW
    assert repository.call is not None


@pytest.mark.parametrize("offline_origin", [False, True])
def test_literal_json_booleans_remain_accepted(offline_origin: bool) -> None:
    repository = RecordingRepository(_result())
    response = _client(repository).post(
        "/api/m2/field-events/day-plan-activated",
        headers={"X-WERKcrew-Worker-ID": "worker-api"},
        json=_request(offline_origin=offline_origin),
    )

    assert response.status_code == 200
    assert repository.call[0].event.offline_origin is offline_origin


@pytest.mark.parametrize(
    "field",
    [
        "job_id",
        "task_id",
        "assignment_id",
        "directive_id",
        "server_event_id",
        "received_at",
        "revision",
        "policy_context",
        "gps",
        "location",
    ],
)
def test_client_controlled_or_out_of_scope_fields_are_forbidden(field: str) -> None:
    repository = RecordingRepository(_result())
    response = _client(repository).post(
        "/api/m2/field-events/day-plan-activated",
        headers={"X-WERKcrew-Worker-ID": "worker-api"},
        json=_request(**{field: "client-value"}),
    )

    assert response.status_code == 422
    assert repository.call is None


def test_missing_principal_is_401_and_not_durable() -> None:
    repository = RecordingRepository(_result())
    response = _client(repository).post(
        "/api/m2/field-events/day-plan-activated",
        json=_request(),
    )

    assert response.status_code == 401
    assert response.json()["detail"]["identity_assurance"] == (
        "CONTROLLED_PLACEHOLDER_NOT_PRODUCTION_AUTH"
    )
    assert repository.call is None


def test_principal_mismatch_is_403_and_not_durable() -> None:
    repository = RecordingRepository(_result())
    response = _client(repository).post(
        "/api/m2/field-events/day-plan-activated",
        headers={"X-WERKcrew-Worker-ID": "another-worker"},
        json=_request(),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "WORKER_PRINCIPAL_MISMATCH"
    assert repository.call is None


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(r"access denied: C:\private\secret.db"),
        OSError(r"disk unavailable at C:\private\secret.db"),
    ],
)
def test_storage_os_failures_are_generic_503_without_execute_or_leakage(
    error: OSError,
) -> None:
    repository = InitializationFailureRepository(error)
    response = _client(repository).post(
        "/api/m2/field-events/day-plan-activated",
        headers={"X-WERKcrew-Worker-ID": "worker-api"},
        json=_request(),
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error_code": "CANONICAL_STORAGE_UNAVAILABLE",
        "processing_status": "NOT_COMPLETED",
        "retryable": False,
    }
    assert repository.initialized_at == NOW
    assert repository.call is None
    assert "secret.db" not in response.text
    assert "private" not in response.text
    assert str(error) not in response.text


def test_non_storage_programming_error_is_not_swallowed_by_storage_mapping() -> None:
    repository = InitializationFailureRepository(RuntimeError("programming defect"))

    with pytest.raises(RuntimeError, match="programming defect"):
        _client(repository).post(
            "/api/m2/field-events/day-plan-activated",
            headers={"X-WERKcrew-Worker-ID": "worker-api"},
            json=_request(),
        )

    assert repository.call is None
