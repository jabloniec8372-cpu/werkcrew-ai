"""Narrow GEN2 M2 HTTP adapters for explicit plan-day field events."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from werkcrew_ai.field import (
    DurableReductionResult,
    FieldEventEnvelope,
    FieldEventInput,
    FieldEventType,
    M2ConflictError,
    M2DurableRepository,
    M2NotFoundError,
    M2Policy,
    M2StaleRevisionError,
    M2StorageIntegrityError,
    PolicyTimeContext,
    ReductionOutcome,
    SystemEffect,
)
from werkcrew_ai.migrations import MigrationError
from werkcrew_ai.persistence import SqliteSettings


NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DayPlanActivatedRequest(BaseModel):
    """The only MobileWC-style input admitted by this first runtime slice."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    schema_version: Literal[1]
    event_type: Literal["DAY_PLAN_ACTIVATED"]
    actor_id: NonBlankText
    occurred_at: datetime
    plan_day_id: NonBlankText
    offline_origin: bool = False

    @field_validator(
        "event_id",
        "event_type",
        "actor_id",
        "plan_day_id",
        mode="before",
    )
    @classmethod
    def string_fields_must_arrive_as_json_strings(cls, value, info):
        if type(value) is not str:
            raise ValueError(f"{info.field_name} must be a JSON string")
        return value

    @field_validator("occurred_at", mode="before")
    @classmethod
    def occurred_at_must_be_aware_iso_string(cls, value):
        if type(value) is not str:
            raise ValueError("occurred_at must be a JSON string")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("occurred_at must be an ISO datetime string") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value

    @field_validator("schema_version", mode="before")
    @classmethod
    def schema_version_must_be_exact_json_integer_one(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be the JSON integer 1")
        return value

    @field_validator("offline_origin", mode="before")
    @classmethod
    def offline_origin_must_be_json_boolean(cls, value):
        if type(value) is not bool:
            raise ValueError("offline_origin must be a JSON boolean")
        return value

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value


class UnavailableTodayReportedRequest(DayPlanActivatedRequest):
    """Explicit worker report that they are unavailable for this plan day."""

    event_type: Literal["UNAVAILABLE_TODAY_REPORTED"]
    reason_class: Literal["SICK", "PERSONAL_EMERGENCY", "OTHER"]

    @field_validator("reason_class", mode="before")
    @classmethod
    def reason_class_must_arrive_as_json_string(cls, value):
        if type(value) is not str:
            raise ValueError("reason_class must be a JSON string")
        return value


class PersistedEffectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effect_type: str
    source_id: str
    plan_day_id: str | None
    job_id: str | None
    task_id: str | None
    assignment_id: str | None
    stage_id: str | None
    directive_id: str | None
    actor_id: str | None
    details: dict[str, str]
    outbox_status: Literal["RECORDED"] = "RECORDED"
    external_delivery: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"


class DayPlanActivatedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_namespace: Literal["FIELD_EVENT"]
    event_id: str
    server_event_id: str
    processing_status: Literal["COMPLETED"] = "COMPLETED"
    outcome: ReductionOutcome
    replayed: bool
    missing_requirements: list[str]
    reason_codes: list[str]
    effects: list[PersistedEffectResponse]


class UnavailableTodayReportedResponse(DayPlanActivatedResponse):
    """Durable result of one explicit unavailable-today report."""


@dataclass(frozen=True, slots=True)
class WorkerPrincipal:
    """Controlled identity placeholder; this is not production authentication."""

    worker_id: str


def controlled_worker_identity_placeholder(
    worker_id: Annotated[
        str | None,
        Header(alias="X-WERKcrew-Worker-ID"),
    ] = None,
) -> WorkerPrincipal:
    """Bind a controlled request to a worker without creating canonical identity."""

    if worker_id is None or not worker_id.strip():
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "WORKER_PRINCIPAL_REQUIRED",
                "identity_assurance": "CONTROLLED_PLACEHOLDER_NOT_PRODUCTION_AUTH",
            },
        )
    return WorkerPrincipal(worker_id.strip())


def m2_repository() -> M2DurableRepository:
    return M2DurableRepository(SqliteSettings.from_environment().database_path)


def server_timestamp() -> datetime:
    return datetime.now(timezone.utc)


def server_event_id() -> str:
    return str(uuid4())


def m2_policy() -> M2Policy:
    return M2Policy()


def _effect_response(effect: SystemEffect) -> PersistedEffectResponse:
    return PersistedEffectResponse(
        effect_type=effect.effect_type.value,
        source_id=effect.source_id,
        plan_day_id=effect.plan_day_id,
        job_id=effect.job_id,
        task_id=effect.task_id,
        assignment_id=effect.assignment_id,
        stage_id=effect.stage_id,
        directive_id=effect.directive_id,
        actor_id=effect.actor_id,
        details=dict(effect.details),
    )


def response_from_result(
    request: DayPlanActivatedRequest,
    result: DurableReductionResult,
) -> DayPlanActivatedResponse:
    """Map the durable response proof, never the first-run emitted-effects list."""

    if result.server_event_id is None:
        raise M2StorageIntegrityError("completed field result has no server event identity")
    return DayPlanActivatedResponse(
        input_namespace="FIELD_EVENT",
        event_id=str(request.event_id),
        server_event_id=result.server_event_id,
        outcome=result.outcome,
        replayed=result.replayed,
        missing_requirements=list(result.missing_requirements),
        reason_codes=list(result.reason_codes),
        effects=[_effect_response(effect) for effect in result.response_effects],
    )


def unavailable_response_from_result(
    request: UnavailableTodayReportedRequest,
    result: DurableReductionResult,
) -> UnavailableTodayReportedResponse:
    """Map the durable unavailable result from historical response effects."""

    response = response_from_result(request, result)
    return UnavailableTodayReportedResponse(**response.model_dump())


def _failure(
    status_code: int,
    error_code: str,
    *,
    processing_status: str,
    retryable: bool,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "error_code": error_code,
            "processing_status": processing_status,
            "retryable": retryable,
        },
    )


def _canonical_storage_unavailable() -> HTTPException:
    return _failure(
        503,
        "CANONICAL_STORAGE_UNAVAILABLE",
        processing_status="NOT_COMPLETED",
        retryable=False,
    )


router = APIRouter(prefix="/api/m2", tags=["GEN2 M2 runtime"])


def _execute_durable_field_event(
    repository: M2DurableRepository,
    explicit_input: FieldEventInput,
    context: PolicyTimeContext,
    received_at: datetime,
) -> DurableReductionResult:
    """Run the existing durable boundary without interpreting event semantics."""

    try:
        repository.initialize(now=received_at)
        return repository.execute(
            explicit_input,
            context,
            received_at=received_at,
        )
    except M2ConflictError:
        raise _failure(
            409,
            "M2_INPUT_CONFLICT",
            processing_status="CONFLICT_RECORDED",
            retryable=False,
        ) from None
    except M2StaleRevisionError:
        raise _failure(
            409,
            "STALE_CANONICAL_REVISION",
            processing_status="RECEIVED",
            retryable=True,
        ) from None
    except M2NotFoundError:
        raise _failure(
            503,
            "CANONICAL_CONTEXT_NOT_READY",
            processing_status="NOT_COMPLETED",
            retryable=False,
        ) from None
    except (M2StorageIntegrityError, MigrationError, sqlite3.DatabaseError, OSError):
        raise _canonical_storage_unavailable() from None


@router.post(
    "/field-events/day-plan-activated",
    response_model=DayPlanActivatedResponse,
    responses={401: {}, 403: {}, 409: {}, 422: {}, 503: {}},
)
def activate_plan_day(
    request: DayPlanActivatedRequest,
    principal: Annotated[
        WorkerPrincipal,
        Depends(controlled_worker_identity_placeholder),
    ],
    repository: Annotated[M2DurableRepository, Depends(m2_repository)],
    received_at: Annotated[datetime, Depends(server_timestamp)],
    generated_server_event_id: Annotated[str, Depends(server_event_id)],
    policy: Annotated[M2Policy, Depends(m2_policy)],
):
    """Persist and reduce one explicit DAY_PLAN_ACTIVATED field event."""

    if principal.worker_id != request.actor_id:
        raise _failure(
            403,
            "WORKER_PRINCIPAL_MISMATCH",
            processing_status="NOT_ACCEPTED",
            retryable=False,
        )

    explicit_input = FieldEventInput(
        FieldEventEnvelope(
            event_id=str(request.event_id),
            schema_version=request.schema_version,
            event_type=FieldEventType.DAY_PLAN_ACTIVATED,
            actor_id=request.actor_id,
            occurred_at=request.occurred_at,
            plan_day_id=request.plan_day_id,
            offline_origin=request.offline_origin,
        ),
        generated_server_event_id,
    )
    context = PolicyTimeContext(now=received_at, policy=policy)

    result = _execute_durable_field_event(
        repository,
        explicit_input,
        context,
        received_at,
    )
    try:
        response = response_from_result(request, result)
    except M2StorageIntegrityError:
        raise _canonical_storage_unavailable() from None

    if result.outcome is ReductionOutcome.REJECTED:
        return JSONResponse(status_code=409, content=jsonable_encoder(response))
    return response


@router.post(
    "/field-events/unavailable-today-reported",
    response_model=UnavailableTodayReportedResponse,
    responses={401: {}, 403: {}, 409: {}, 422: {}, 503: {}},
)
def report_unavailable_today(
    request: UnavailableTodayReportedRequest,
    principal: Annotated[
        WorkerPrincipal,
        Depends(controlled_worker_identity_placeholder),
    ],
    repository: Annotated[M2DurableRepository, Depends(m2_repository)],
    received_at: Annotated[datetime, Depends(server_timestamp)],
    generated_server_event_id: Annotated[str, Depends(server_event_id)],
    policy: Annotated[M2Policy, Depends(m2_policy)],
):
    """Persist and reduce one explicit UNAVAILABLE_TODAY_REPORTED event."""

    if principal.worker_id != request.actor_id:
        raise _failure(
            403,
            "WORKER_PRINCIPAL_MISMATCH",
            processing_status="NOT_ACCEPTED",
            retryable=False,
        )

    explicit_input = FieldEventInput(
        FieldEventEnvelope(
            event_id=str(request.event_id),
            schema_version=request.schema_version,
            event_type=FieldEventType.UNAVAILABLE_TODAY_REPORTED,
            actor_id=request.actor_id,
            occurred_at=request.occurred_at,
            plan_day_id=request.plan_day_id,
            reason_class=request.reason_class,
            offline_origin=request.offline_origin,
        ),
        generated_server_event_id,
    )
    context = PolicyTimeContext(now=received_at, policy=policy)
    result = _execute_durable_field_event(
        repository,
        explicit_input,
        context,
        received_at,
    )
    try:
        response = unavailable_response_from_result(request, result)
    except M2StorageIntegrityError:
        raise _canonical_storage_unavailable() from None

    if result.outcome is ReductionOutcome.REJECTED:
        return JSONResponse(status_code=409, content=jsonable_encoder(response))
    return response


__all__ = [
    "DayPlanActivatedRequest",
    "DayPlanActivatedResponse",
    "PersistedEffectResponse",
    "UnavailableTodayReportedRequest",
    "UnavailableTodayReportedResponse",
    "WorkerPrincipal",
    "controlled_worker_identity_placeholder",
    "m2_policy",
    "m2_repository",
    "response_from_result",
    "router",
    "server_event_id",
    "server_timestamp",
    "unavailable_response_from_result",
]
