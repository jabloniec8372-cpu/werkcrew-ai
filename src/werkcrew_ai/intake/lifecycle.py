"""Frozen M1 lifecycle v1 trusted boundary and durable operation ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, TypeAlias

from werkcrew_ai.intake.models import (
    CanonicalFactInput,
    CanonicalJobActivity,
    CanonicalJobIntake,
    JobFactName,
)
from werkcrew_ai.intake.repository import CanonicalJobRepository


FINGERPRINT_VERSION = "m1-operation-v1"
ACTIVITY_STATE_DIMENSION = "ACTIVITY"


class M1OperationKind(StrEnum):
    CREATE_JOB = "CREATE_JOB"
    RECORD_FOLLOW_UP = "RECORD_FOLLOW_UP"
    MARK_DORMANT = "MARK_DORMANT"
    WAKE_JOB = "WAKE_JOB"
    BIND_CONVERSATION = "BIND_CONVERSATION"


class M1OutcomeCode(StrEnum):
    JOB_CREATED = "JOB_CREATED"
    FOLLOW_UP_RECORDED = "FOLLOW_UP_RECORDED"
    JOB_MARKED_DORMANT = "JOB_MARKED_DORMANT"
    JOB_WOKEN = "JOB_WOKEN"
    CONVERSATION_BOUND = "CONVERSATION_BOUND"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    CORRELATION_REQUIRED = "CORRELATION_REQUIRED"
    AMBIGUOUS_CORRELATION = "AMBIGUOUS_CORRELATION"
    CORRELATION_CONFLICT = "CORRELATION_CONFLICT"
    ALREADY_DORMANT = "ALREADY_DORMANT"
    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    FACT_REVISION_CONFLICT = "FACT_REVISION_CONFLICT"
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    EVENT_CONFLICT = "EVENT_CONFLICT"
    JOB_ID_CONFLICT = "JOB_ID_CONFLICT"
    EVIDENCE_NOT_FOUND = "EVIDENCE_NOT_FOUND"


class DormantReason(StrEnum):
    NO_CUSTOMER_RESPONSE = "NO_CUSTOMER_RESPONSE"
    CUSTOMER_PAUSED = "CUSTOMER_PAUSED"
    OWNER_PAUSED = "OWNER_PAUSED"
    OTHER = "OTHER"


class M1LifecycleEventType(StrEnum):
    FOLLOW_UP_RECORDED = "FOLLOW_UP_RECORDED"
    JOB_MARKED_DORMANT = "JOB_MARKED_DORMANT"
    JOB_WOKEN = "JOB_WOKEN"


class FactValidationKind(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    HUMAN = "HUMAN"


def _identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-blank")
    if value != value.strip():
        raise ValueError(f"{field_name} must not contain surrounding whitespace")
    return value


def _optional_identifier(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _identifier(value, field_name)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _optional_text(value: str | None, field_name: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{field_name} must be text or None")
    return value


def canonical_json(value: Any) -> str:
    """Return the exact M1 v1 canonical JSON representation."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_json_sha256(value: Any) -> tuple[str, str]:
    encoded = canonical_json(value)
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class M1OperationEnvelope:
    operation_kind: M1OperationKind
    source_namespace: str
    ingress_event_id: str | None
    actor_id: str
    actor_source: str
    occurred_at: datetime | None
    received_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.source_namespace, "source_namespace")
        if self.ingress_event_id is not None and self.ingress_event_id.strip():
            _identifier(self.ingress_event_id, "ingress_event_id")
        _identifier(self.actor_id, "actor_id")
        _identifier(self.actor_source, "actor_source")
        if self.occurred_at is not None:
            _require_aware(self.occurred_at, "occurred_at")
        _require_aware(self.received_at, "received_at")


@dataclass(frozen=True, slots=True)
class FollowUpFactCandidate:
    fact: CanonicalFactInput
    expected_current_revision: int
    validation_kind: FactValidationKind
    validated_by: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.expected_current_revision, bool)
            or not isinstance(self.expected_current_revision, int)
            or self.expected_current_revision < 0
        ):
            raise ValueError("expected_current_revision must be zero or greater")
        if not isinstance(self.validation_kind, FactValidationKind):
            raise ValueError("validation_kind must be DETERMINISTIC or HUMAN")
        _identifier(self.validated_by, "validated_by")


@dataclass(frozen=True, slots=True)
class CreateJobOperation:
    envelope: M1OperationEnvelope
    job_id: str
    intake: CanonicalJobIntake
    conversation_id: str | None = None

    def __post_init__(self) -> None:
        if self.envelope.operation_kind is not M1OperationKind.CREATE_JOB:
            raise ValueError("Envelope operation_kind must be CREATE_JOB")
        _identifier(self.job_id, "job_id")
        _optional_identifier(self.conversation_id, "conversation_id")


@dataclass(frozen=True, slots=True)
class RecordFollowUpOperation:
    envelope: M1OperationEnvelope
    explicit_job_id: str | None = None
    conversation_id: str | None = None
    raw_text: str | None = None
    raw_payload: str | None = None
    fact_candidates: tuple[FollowUpFactCandidate, ...] = ()

    def __post_init__(self) -> None:
        if self.envelope.operation_kind is not M1OperationKind.RECORD_FOLLOW_UP:
            raise ValueError("Envelope operation_kind must be RECORD_FOLLOW_UP")
        _optional_identifier(self.explicit_job_id, "explicit_job_id")
        _optional_identifier(self.conversation_id, "conversation_id")
        _optional_text(self.raw_text, "raw_text")
        _optional_text(self.raw_payload, "raw_payload")
        names = [candidate.fact.name for candidate in self.fact_candidates]
        if len(names) != len(set(names)):
            raise ValueError("A follow-up fact batch cannot contain duplicate names")


@dataclass(frozen=True, slots=True)
class MarkDormantOperation:
    envelope: M1OperationEnvelope
    job_id: str
    reason: DormantReason
    reason_detail: str | None = None
    supporting_evidence_id: str | None = None

    def __post_init__(self) -> None:
        if self.envelope.operation_kind is not M1OperationKind.MARK_DORMANT:
            raise ValueError("Envelope operation_kind must be MARK_DORMANT")
        _identifier(self.job_id, "job_id")
        _optional_text(self.reason_detail, "reason_detail")
        _optional_identifier(self.supporting_evidence_id, "supporting_evidence_id")
        if self.reason is DormantReason.OTHER and (
            self.reason_detail is None or not self.reason_detail.strip()
        ):
            raise ValueError("OTHER requires nonblank reason_detail")


@dataclass(frozen=True, slots=True)
class WakeJobOperation:
    envelope: M1OperationEnvelope
    job_id: str

    def __post_init__(self) -> None:
        if self.envelope.operation_kind is not M1OperationKind.WAKE_JOB:
            raise ValueError("Envelope operation_kind must be WAKE_JOB")
        _identifier(self.job_id, "job_id")


@dataclass(frozen=True, slots=True)
class BindConversationOperation:
    envelope: M1OperationEnvelope
    job_id: str
    conversation_id: str

    def __post_init__(self) -> None:
        if self.envelope.operation_kind is not M1OperationKind.BIND_CONVERSATION:
            raise ValueError("Envelope operation_kind must be BIND_CONVERSATION")
        _identifier(self.job_id, "job_id")
        _identifier(self.conversation_id, "conversation_id")


M1Operation: TypeAlias = (
    CreateJobOperation
    | RecordFollowUpOperation
    | MarkDormantOperation
    | WakeJobOperation
    | BindConversationOperation
)


@dataclass(frozen=True, slots=True)
class M1OperationResult:
    outcome: M1OutcomeCode
    operation_kind: M1OperationKind
    source_namespace: str
    ingress_event_id: str | None
    received_at: datetime
    completed_at: datetime
    job_id: str | None = None
    activity_state: CanonicalJobActivity | None = None
    evidence_id: str | None = None
    lifecycle_event_ids: tuple[str, ...] = ()
    fact_revisions: tuple[tuple[str, int], ...] = ()
    association_added: bool | None = None


@dataclass(frozen=True, slots=True)
class FollowUpEvidence:
    evidence_id: str
    job_id: str
    source_namespace: str
    ingress_event_id: str
    conversation_id: str | None
    raw_text: str | None
    raw_payload: str | None
    occurred_at: datetime | None
    received_at: datetime


@dataclass(frozen=True, slots=True)
class M1LifecycleEvent:
    lifecycle_event_id: str
    job_sequence: int
    job_id: str
    event_type: M1LifecycleEventType
    state_dimension: str
    from_activity_state: CanonicalJobActivity | None
    to_activity_state: CanonicalJobActivity | None
    server_timestamp: datetime
    occurred_at: datetime | None
    actor_id: str
    actor_source: str
    reason: DormantReason | None
    reason_detail: str | None
    operation_source_namespace: str
    operation_ingress_event_id: str
    evidence_id: str | None


@dataclass(frozen=True, slots=True)
class AdapterReceipt:
    source_namespace: str
    receipt_id: str
    ingress_event_id: str
    minted_at: datetime


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _fact_payload(fact: CanonicalFactInput) -> dict[str, Any]:
    return {
        "name": fact.name.value,
        "value": fact.value,
        "knowledge_state": fact.knowledge_state.value,
        "verification_state": fact.verification_state.value,
        "provenance_source": fact.provenance_source,
    }


def operation_fingerprint_payload(operation: M1Operation) -> dict[str, Any]:
    envelope = operation.envelope
    payload: dict[str, Any] = {
        "operation_kind": envelope.operation_kind.value,
        "source_namespace": envelope.source_namespace,
        "ingress_event_id": envelope.ingress_event_id,
        "actor": {
            "id": envelope.actor_id,
            "source": envelope.actor_source,
        },
        "occurred_at": _timestamp(envelope.occurred_at),
        "target_job_id": None,
        "conversation_id": None,
        "raw_text": None,
        "raw_payload": None,
        "fact_candidates": [],
        "dormant_reason": None,
        "dormant_detail": None,
        "supporting_evidence_id": None,
        "binding_target": None,
    }
    if isinstance(operation, CreateJobOperation):
        payload.update(
            {
                "target_job_id": operation.job_id,
                "conversation_id": operation.conversation_id,
                "raw_text": operation.intake.raw_text,
                "raw_payload": operation.intake.raw_payload,
                "intake_source": operation.intake.intake_source,
                "fact_candidates": [
                    _fact_payload(fact) for fact in operation.intake.facts
                ],
            }
        )
    elif isinstance(operation, RecordFollowUpOperation):
        payload.update(
            {
                "target_job_id": operation.explicit_job_id,
                "conversation_id": operation.conversation_id,
                "raw_text": operation.raw_text,
                "raw_payload": operation.raw_payload,
                "fact_candidates": [
                    {
                        **_fact_payload(candidate.fact),
                        "expected_current_revision": (
                            candidate.expected_current_revision
                        ),
                        "validation_kind": candidate.validation_kind.value,
                        "validated_by": candidate.validated_by,
                    }
                    for candidate in operation.fact_candidates
                ],
            }
        )
    elif isinstance(operation, MarkDormantOperation):
        payload.update(
            {
                "target_job_id": operation.job_id,
                "dormant_reason": operation.reason.value,
                "dormant_detail": operation.reason_detail,
                "supporting_evidence_id": operation.supporting_evidence_id,
            }
        )
    elif isinstance(operation, WakeJobOperation):
        payload["target_job_id"] = operation.job_id
    elif isinstance(operation, BindConversationOperation):
        payload.update(
            {
                "target_job_id": operation.job_id,
                "conversation_id": operation.conversation_id,
                "binding_target": {
                    "source_namespace": envelope.source_namespace,
                    "conversation_id": operation.conversation_id,
                    "job_id": operation.job_id,
                },
            }
        )
    else:  # pragma: no cover - the closed union is validated by callers
        raise TypeError(f"Unsupported M1 operation: {type(operation)!r}")
    return payload


def operation_fingerprint(operation: M1Operation) -> tuple[str, str]:
    return canonical_json_sha256(operation_fingerprint_payload(operation))


def _result_payload(result: M1OperationResult) -> dict[str, Any]:
    return {
        "outcome": result.outcome.value,
        "operation_kind": result.operation_kind.value,
        "source_namespace": result.source_namespace,
        "ingress_event_id": result.ingress_event_id,
        "received_at": result.received_at.isoformat(),
        "completed_at": result.completed_at.isoformat(),
        "job_id": result.job_id,
        "activity_state": (
            result.activity_state.value if result.activity_state is not None else None
        ),
        "evidence_id": result.evidence_id,
        "lifecycle_event_ids": list(result.lifecycle_event_ids),
        "fact_revisions": [list(item) for item in result.fact_revisions],
        "association_added": result.association_added,
    }


def _result_from_json(raw: str) -> M1OperationResult:
    value = json.loads(raw)
    return M1OperationResult(
        outcome=M1OutcomeCode(value["outcome"]),
        operation_kind=M1OperationKind(value["operation_kind"]),
        source_namespace=value["source_namespace"],
        ingress_event_id=value["ingress_event_id"],
        received_at=datetime.fromisoformat(value["received_at"]),
        completed_at=datetime.fromisoformat(value["completed_at"]),
        job_id=value["job_id"],
        activity_state=(
            CanonicalJobActivity(value["activity_state"])
            if value["activity_state"] is not None
            else None
        ),
        evidence_id=value["evidence_id"],
        lifecycle_event_ids=tuple(value["lifecycle_event_ids"]),
        fact_revisions=tuple(
            (str(item[0]), int(item[1])) for item in value["fact_revisions"]
        ),
        association_added=value["association_added"],
    )


class CanonicalM1LifecycleService(CanonicalJobRepository):
    """Trusted deterministic boundary for every frozen M1 v1 operation."""

    def execute(self, operation: M1Operation) -> M1OperationResult:
        envelope = operation.envelope
        if envelope.ingress_event_id is None or not envelope.ingress_event_id.strip():
            return self._result(
                operation,
                M1OutcomeCode.IDEMPOTENCY_KEY_REQUIRED,
            )

        request_json, fingerprint = operation_fingerprint(operation)
        with self.transaction() as connection:
            existing = connection.execute(
                """
                SELECT fingerprint_version, fingerprint_sha256, result_json
                FROM m1_operations
                WHERE source_namespace = ? AND ingress_event_id = ?
                """,
                (envelope.source_namespace, envelope.ingress_event_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["fingerprint_version"] == FINGERPRINT_VERSION
                    and existing["fingerprint_sha256"] == fingerprint
                ):
                    return _result_from_json(existing["result_json"])
                conflict = connection.execute(
                    """
                    SELECT result_json FROM m1_operation_conflicts
                    WHERE source_namespace = ? AND ingress_event_id = ?
                      AND conflicting_fingerprint_sha256 = ?
                    """,
                    (
                        envelope.source_namespace,
                        envelope.ingress_event_id,
                        fingerprint,
                    ),
                ).fetchone()
                if conflict is not None:
                    return _result_from_json(conflict["result_json"])
                result = self._result(operation, M1OutcomeCode.EVENT_CONFLICT)
                connection.execute(
                    """
                    INSERT INTO m1_operation_conflicts(
                        source_namespace, ingress_event_id,
                        conflicting_fingerprint_sha256, fingerprint_version,
                        canonical_request_json, received_at, result_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        envelope.source_namespace,
                        envelope.ingress_event_id,
                        fingerprint,
                        FINGERPRINT_VERSION,
                        request_json,
                        envelope.received_at.isoformat(),
                        canonical_json(_result_payload(result)),
                    ),
                )
                return result

            result = self._apply_first_seen(connection, operation)
            result_json = canonical_json(_result_payload(result))
            connection.execute(
                """
                INSERT INTO m1_operations(
                    source_namespace, ingress_event_id, operation_kind,
                    fingerprint_version, fingerprint_sha256,
                    canonical_request_json, received_at, completed_at,
                    outcome_code, job_id, result_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope.source_namespace,
                    envelope.ingress_event_id,
                    envelope.operation_kind.value,
                    FINGERPRINT_VERSION,
                    fingerprint,
                    request_json,
                    envelope.received_at.isoformat(),
                    result.completed_at.isoformat(),
                    result.outcome.value,
                    result.job_id,
                    result_json,
                ),
            )
            self._before_commit(connection, operation, result)
            return result

    def _before_commit(
        self,
        _connection: sqlite3.Connection,
        _operation: M1Operation,
        _result: M1OperationResult,
    ) -> None:
        """Test seam for proving rollback before commit."""

    def _apply_first_seen(
        self,
        connection: sqlite3.Connection,
        operation: M1Operation,
    ) -> M1OperationResult:
        if isinstance(operation, CreateJobOperation):
            return self._create_job_operation(connection, operation)
        if isinstance(operation, RecordFollowUpOperation):
            return self._record_follow_up(connection, operation)
        if isinstance(operation, MarkDormantOperation):
            return self._mark_dormant(connection, operation)
        if isinstance(operation, WakeJobOperation):
            return self._wake_job(connection, operation)
        if isinstance(operation, BindConversationOperation):
            return self._bind_conversation(connection, operation)
        raise TypeError(f"Unsupported M1 operation: {type(operation)!r}")

    def _create_job_operation(
        self,
        connection: sqlite3.Connection,
        operation: CreateJobOperation,
    ) -> M1OperationResult:
        exists = connection.execute(
            "SELECT 1 FROM canonical_jobs WHERE job_id = ?",
            (operation.job_id,),
        ).fetchone()
        if exists is not None:
            return self._result(
                operation,
                M1OutcomeCode.JOB_ID_CONFLICT,
                job_id=operation.job_id,
            )

        timestamp = operation.envelope.received_at.isoformat()
        connection.execute(
            """
            INSERT INTO canonical_jobs(
                job_id, lifecycle_state, activity_state, created_at, updated_at
            ) VALUES(?, 'RECEIVED', 'ACTIVE', ?, ?)
            """,
            (operation.job_id, timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO canonical_job_intake(
                job_id, intake_source, raw_text, raw_payload, received_at
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                operation.job_id,
                operation.intake.intake_source,
                operation.intake.raw_text,
                operation.intake.raw_payload,
                timestamp,
            ),
        )
        for fact in operation.intake.facts:
            self._insert_fact(
                connection,
                job_id=operation.job_id,
                fact=fact,
                revision=1,
                recorded_at=operation.envelope.received_at,
            )

        association_added: bool | None = None
        if operation.conversation_id is not None:
            association_added = self._insert_association(
                connection,
                source_namespace=operation.envelope.source_namespace,
                conversation_id=operation.conversation_id,
                job_id=operation.job_id,
                envelope=operation.envelope,
            )
        return self._result(
            operation,
            M1OutcomeCode.JOB_CREATED,
            job_id=operation.job_id,
            activity_state=CanonicalJobActivity.ACTIVE,
            association_added=association_added,
        )

    def _record_follow_up(
        self,
        connection: sqlite3.Connection,
        operation: RecordFollowUpOperation,
    ) -> M1OperationResult:
        target, rejection = self._correlate_follow_up(connection, operation)
        if rejection is not None:
            return self._result(
                operation,
                rejection,
                job_id=operation.explicit_job_id,
            )
        assert target is not None

        row = connection.execute(
            "SELECT activity_state, updated_at FROM canonical_jobs WHERE job_id = ?",
            (target,),
        ).fetchone()
        assert row is not None
        evidence_id = f"m1e-{uuid.uuid4().hex}"
        envelope = operation.envelope
        connection.execute(
            """
            INSERT INTO m1_follow_up_evidence(
                evidence_id, job_id, source_namespace, ingress_event_id,
                conversation_id, raw_text, raw_payload, occurred_at, received_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                target,
                envelope.source_namespace,
                envelope.ingress_event_id,
                operation.conversation_id,
                operation.raw_text,
                operation.raw_payload,
                _timestamp(envelope.occurred_at),
                envelope.received_at.isoformat(),
            ),
        )
        lifecycle_ids = [
            self._append_lifecycle_event(
                connection,
                job_id=target,
                event_type=M1LifecycleEventType.FOLLOW_UP_RECORDED,
                envelope=envelope,
                evidence_id=evidence_id,
            )
        ]

        activity = CanonicalJobActivity(row["activity_state"])
        if activity is CanonicalJobActivity.DORMANT:
            activity = CanonicalJobActivity.ACTIVE
            connection.execute(
                "UPDATE canonical_jobs SET activity_state = 'ACTIVE' WHERE job_id = ?",
                (target,),
            )
            lifecycle_ids.append(
                self._append_lifecycle_event(
                    connection,
                    job_id=target,
                    event_type=M1LifecycleEventType.JOB_WOKEN,
                    envelope=envelope,
                    from_activity_state=CanonicalJobActivity.DORMANT,
                    to_activity_state=CanonicalJobActivity.ACTIVE,
                    evidence_id=evidence_id,
                )
            )

        conflict = False
        current_revisions: dict[JobFactName, int] = {}
        for candidate in operation.fact_candidates:
            current = connection.execute(
                """
                SELECT MAX(revision) AS revision
                FROM canonical_job_facts
                WHERE job_id = ? AND fact_name = ?
                """,
                (target, candidate.fact.name.value),
            ).fetchone()["revision"]
            current_revision = int(current) if current is not None else 0
            current_revisions[candidate.fact.name] = current_revision
            if current_revision != candidate.expected_current_revision:
                conflict = True

        fact_revisions: list[tuple[str, int]] = []
        if not conflict:
            for candidate in operation.fact_candidates:
                revision = current_revisions[candidate.fact.name] + 1
                self._insert_fact(
                    connection,
                    job_id=target,
                    fact=candidate.fact,
                    revision=revision,
                    recorded_at=envelope.received_at,
                    follow_up_evidence_id=evidence_id,
                )
                fact_revisions.append((candidate.fact.name.value, revision))

        self._touch_job(connection, target, envelope.received_at, row["updated_at"])
        return self._result(
            operation,
            (
                M1OutcomeCode.FACT_REVISION_CONFLICT
                if conflict
                else M1OutcomeCode.FOLLOW_UP_RECORDED
            ),
            job_id=target,
            activity_state=activity,
            evidence_id=evidence_id,
            lifecycle_event_ids=tuple(lifecycle_ids),
            fact_revisions=tuple(fact_revisions),
        )

    def _mark_dormant(
        self,
        connection: sqlite3.Connection,
        operation: MarkDormantOperation,
    ) -> M1OperationResult:
        row = connection.execute(
            "SELECT activity_state, updated_at FROM canonical_jobs WHERE job_id = ?",
            (operation.job_id,),
        ).fetchone()
        if row is None:
            return self._result(
                operation,
                M1OutcomeCode.TARGET_NOT_FOUND,
                job_id=operation.job_id,
            )
        if operation.supporting_evidence_id is not None:
            evidence = connection.execute(
                "SELECT job_id FROM m1_follow_up_evidence WHERE evidence_id = ?",
                (operation.supporting_evidence_id,),
            ).fetchone()
            if evidence is None or evidence["job_id"] != operation.job_id:
                return self._result(
                    operation,
                    M1OutcomeCode.EVIDENCE_NOT_FOUND,
                    job_id=operation.job_id,
                    activity_state=CanonicalJobActivity(row["activity_state"]),
                )
        activity = CanonicalJobActivity(row["activity_state"])
        if activity is CanonicalJobActivity.DORMANT:
            return self._result(
                operation,
                M1OutcomeCode.ALREADY_DORMANT,
                job_id=operation.job_id,
                activity_state=activity,
            )
        connection.execute(
            "UPDATE canonical_jobs SET activity_state = 'DORMANT' WHERE job_id = ?",
            (operation.job_id,),
        )
        self._touch_job(
            connection,
            operation.job_id,
            operation.envelope.received_at,
            row["updated_at"],
        )
        lifecycle_id = self._append_lifecycle_event(
            connection,
            job_id=operation.job_id,
            event_type=M1LifecycleEventType.JOB_MARKED_DORMANT,
            envelope=operation.envelope,
            from_activity_state=CanonicalJobActivity.ACTIVE,
            to_activity_state=CanonicalJobActivity.DORMANT,
            reason=operation.reason,
            reason_detail=operation.reason_detail,
            evidence_id=operation.supporting_evidence_id,
        )
        return self._result(
            operation,
            M1OutcomeCode.JOB_MARKED_DORMANT,
            job_id=operation.job_id,
            activity_state=CanonicalJobActivity.DORMANT,
            lifecycle_event_ids=(lifecycle_id,),
        )

    def _wake_job(
        self,
        connection: sqlite3.Connection,
        operation: WakeJobOperation,
    ) -> M1OperationResult:
        row = connection.execute(
            "SELECT activity_state, updated_at FROM canonical_jobs WHERE job_id = ?",
            (operation.job_id,),
        ).fetchone()
        if row is None:
            return self._result(
                operation,
                M1OutcomeCode.TARGET_NOT_FOUND,
                job_id=operation.job_id,
            )
        activity = CanonicalJobActivity(row["activity_state"])
        if activity is CanonicalJobActivity.ACTIVE:
            return self._result(
                operation,
                M1OutcomeCode.ALREADY_ACTIVE,
                job_id=operation.job_id,
                activity_state=activity,
            )
        connection.execute(
            "UPDATE canonical_jobs SET activity_state = 'ACTIVE' WHERE job_id = ?",
            (operation.job_id,),
        )
        self._touch_job(
            connection,
            operation.job_id,
            operation.envelope.received_at,
            row["updated_at"],
        )
        lifecycle_id = self._append_lifecycle_event(
            connection,
            job_id=operation.job_id,
            event_type=M1LifecycleEventType.JOB_WOKEN,
            envelope=operation.envelope,
            from_activity_state=CanonicalJobActivity.DORMANT,
            to_activity_state=CanonicalJobActivity.ACTIVE,
        )
        return self._result(
            operation,
            M1OutcomeCode.JOB_WOKEN,
            job_id=operation.job_id,
            activity_state=CanonicalJobActivity.ACTIVE,
            lifecycle_event_ids=(lifecycle_id,),
        )

    def _bind_conversation(
        self,
        connection: sqlite3.Connection,
        operation: BindConversationOperation,
    ) -> M1OperationResult:
        exists = connection.execute(
            "SELECT 1 FROM canonical_jobs WHERE job_id = ?",
            (operation.job_id,),
        ).fetchone()
        if exists is None:
            return self._result(
                operation,
                M1OutcomeCode.TARGET_NOT_FOUND,
                job_id=operation.job_id,
            )
        added = self._insert_association(
            connection,
            source_namespace=operation.envelope.source_namespace,
            conversation_id=operation.conversation_id,
            job_id=operation.job_id,
            envelope=operation.envelope,
        )
        return self._result(
            operation,
            M1OutcomeCode.CONVERSATION_BOUND,
            job_id=operation.job_id,
            activity_state=CanonicalJobActivity(
                connection.execute(
                    "SELECT activity_state FROM canonical_jobs WHERE job_id = ?",
                    (operation.job_id,),
                ).fetchone()["activity_state"]
            ),
            association_added=added,
        )

    @staticmethod
    def _correlate_follow_up(
        connection: sqlite3.Connection,
        operation: RecordFollowUpOperation,
    ) -> tuple[str | None, M1OutcomeCode | None]:
        explicit = operation.explicit_job_id
        if explicit is not None:
            exists = connection.execute(
                "SELECT 1 FROM canonical_jobs WHERE job_id = ?",
                (explicit,),
            ).fetchone()
            if exists is None:
                return None, M1OutcomeCode.TARGET_NOT_FOUND
        associations: tuple[str, ...] = ()
        if operation.conversation_id is not None:
            associations = tuple(
                row["job_id"]
                for row in connection.execute(
                    """
                    SELECT job_id FROM m1_conversation_jobs
                    WHERE source_namespace = ? AND conversation_id = ?
                    ORDER BY job_id
                    """,
                    (
                        operation.envelope.source_namespace,
                        operation.conversation_id,
                    ),
                ).fetchall()
            )
        if explicit is not None:
            if associations and explicit not in associations:
                return None, M1OutcomeCode.CORRELATION_CONFLICT
            return explicit, None
        if len(associations) == 0:
            return None, M1OutcomeCode.CORRELATION_REQUIRED
        if len(associations) > 1:
            return None, M1OutcomeCode.AMBIGUOUS_CORRELATION
        return associations[0], None

    @staticmethod
    def _insert_association(
        connection: sqlite3.Connection,
        *,
        source_namespace: str,
        conversation_id: str,
        job_id: str,
        envelope: M1OperationEnvelope,
    ) -> bool:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO m1_conversation_jobs(
                source_namespace, conversation_id, job_id, associated_at,
                operation_source_namespace, operation_ingress_event_id
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                source_namespace,
                conversation_id,
                job_id,
                envelope.received_at.isoformat(),
                envelope.source_namespace,
                envelope.ingress_event_id,
            ),
        )
        return cursor.rowcount == 1

    @staticmethod
    def _touch_job(
        connection: sqlite3.Connection,
        job_id: str,
        received_at: datetime,
        prior_updated_at: str,
    ) -> None:
        prior = datetime.fromisoformat(prior_updated_at)
        value = received_at if received_at > prior else prior
        connection.execute(
            "UPDATE canonical_jobs SET updated_at = ? WHERE job_id = ?",
            (value.isoformat(), job_id),
        )

    @staticmethod
    def _append_lifecycle_event(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        event_type: M1LifecycleEventType,
        envelope: M1OperationEnvelope,
        from_activity_state: CanonicalJobActivity | None = None,
        to_activity_state: CanonicalJobActivity | None = None,
        reason: DormantReason | None = None,
        reason_detail: str | None = None,
        evidence_id: str | None = None,
    ) -> str:
        sequence = connection.execute(
            """
            SELECT COALESCE(MAX(job_sequence), 0) + 1 AS next_sequence
            FROM m1_lifecycle_history WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()["next_sequence"]
        lifecycle_event_id = f"m1l-{uuid.uuid4().hex}"
        connection.execute(
            """
            INSERT INTO m1_lifecycle_history(
                lifecycle_event_id, job_id, job_sequence, event_type,
                state_dimension, from_activity_state, to_activity_state,
                server_timestamp, occurred_at, actor_id, actor_source,
                reason_code, reason_detail, operation_source_namespace,
                operation_ingress_event_id, evidence_id
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lifecycle_event_id,
                job_id,
                sequence,
                event_type.value,
                ACTIVITY_STATE_DIMENSION,
                from_activity_state.value if from_activity_state else None,
                to_activity_state.value if to_activity_state else None,
                envelope.received_at.isoformat(),
                _timestamp(envelope.occurred_at),
                envelope.actor_id,
                envelope.actor_source,
                reason.value if reason else None,
                reason_detail,
                envelope.source_namespace,
                envelope.ingress_event_id,
                evidence_id,
            ),
        )
        return lifecycle_event_id

    @staticmethod
    def _result(
        operation: M1Operation,
        outcome: M1OutcomeCode,
        *,
        job_id: str | None = None,
        activity_state: CanonicalJobActivity | None = None,
        evidence_id: str | None = None,
        lifecycle_event_ids: tuple[str, ...] = (),
        fact_revisions: tuple[tuple[str, int], ...] = (),
        association_added: bool | None = None,
    ) -> M1OperationResult:
        envelope = operation.envelope
        return M1OperationResult(
            outcome=outcome,
            operation_kind=envelope.operation_kind,
            source_namespace=envelope.source_namespace,
            ingress_event_id=envelope.ingress_event_id,
            received_at=envelope.received_at,
            completed_at=envelope.received_at,
            job_id=job_id,
            activity_state=activity_state,
            evidence_id=evidence_id,
            lifecycle_event_ids=lifecycle_event_ids,
            fact_revisions=fact_revisions,
            association_added=association_added,
        )

    def operation_result(
        self,
        source_namespace: str,
        ingress_event_id: str,
    ) -> M1OperationResult | None:
        _identifier(source_namespace, "source_namespace")
        _identifier(ingress_event_id, "ingress_event_id")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT result_json FROM m1_operations
                WHERE source_namespace = ? AND ingress_event_id = ?
                """,
                (source_namespace, ingress_event_id),
            ).fetchone()
        return _result_from_json(row["result_json"]) if row is not None else None

    def conversation_job_ids(
        self,
        source_namespace: str,
        conversation_id: str,
    ) -> tuple[str, ...]:
        _identifier(source_namespace, "source_namespace")
        _identifier(conversation_id, "conversation_id")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT job_id FROM m1_conversation_jobs
                WHERE source_namespace = ? AND conversation_id = ?
                ORDER BY job_id
                """,
                (source_namespace, conversation_id),
            ).fetchall()
        return tuple(row["job_id"] for row in rows)

    def follow_up_evidence(self, job_id: str) -> tuple[FollowUpEvidence, ...]:
        _identifier(job_id, "job_id")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT evidence.*
                FROM m1_follow_up_evidence AS evidence
                JOIN m1_lifecycle_history AS history
                  ON history.evidence_id = evidence.evidence_id
                 AND history.event_type = 'FOLLOW_UP_RECORDED'
                WHERE evidence.job_id = ?
                ORDER BY history.job_sequence
                """,
                (job_id,),
            ).fetchall()
        return tuple(
            FollowUpEvidence(
                evidence_id=row["evidence_id"],
                job_id=row["job_id"],
                source_namespace=row["source_namespace"],
                ingress_event_id=row["ingress_event_id"],
                conversation_id=row["conversation_id"],
                raw_text=row["raw_text"],
                raw_payload=row["raw_payload"],
                occurred_at=(
                    datetime.fromisoformat(row["occurred_at"])
                    if row["occurred_at"] is not None
                    else None
                ),
                received_at=datetime.fromisoformat(row["received_at"]),
            )
            for row in rows
        )

    def lifecycle_history(self, job_id: str) -> tuple[M1LifecycleEvent, ...]:
        _identifier(job_id, "job_id")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM m1_lifecycle_history
                WHERE job_id = ? ORDER BY job_sequence
                """,
                (job_id,),
            ).fetchall()
        return tuple(
            M1LifecycleEvent(
                lifecycle_event_id=row["lifecycle_event_id"],
                job_sequence=row["job_sequence"],
                job_id=row["job_id"],
                event_type=M1LifecycleEventType(row["event_type"]),
                state_dimension=row["state_dimension"],
                from_activity_state=(
                    CanonicalJobActivity(row["from_activity_state"])
                    if row["from_activity_state"] is not None
                    else None
                ),
                to_activity_state=(
                    CanonicalJobActivity(row["to_activity_state"])
                    if row["to_activity_state"] is not None
                    else None
                ),
                server_timestamp=datetime.fromisoformat(row["server_timestamp"]),
                occurred_at=(
                    datetime.fromisoformat(row["occurred_at"])
                    if row["occurred_at"] is not None
                    else None
                ),
                actor_id=row["actor_id"],
                actor_source=row["actor_source"],
                reason=(
                    DormantReason(row["reason_code"])
                    if row["reason_code"] is not None
                    else None
                ),
                reason_detail=row["reason_detail"],
                operation_source_namespace=row["operation_source_namespace"],
                operation_ingress_event_id=row["operation_ingress_event_id"],
                evidence_id=row["evidence_id"],
            )
            for row in rows
        )

    def mint_adapter_receipt(
        self,
        *,
        source_namespace: str,
        minted_at: datetime,
    ) -> AdapterReceipt:
        namespace = _identifier(source_namespace, "source_namespace")
        _require_aware(minted_at, "minted_at")
        receipt_id = f"m1r-{uuid.uuid4().hex}"
        ingress_event_id = receipt_id
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO m1_adapter_receipts(
                    source_namespace, receipt_id, ingress_event_id, minted_at
                ) VALUES(?, ?, ?, ?)
                """,
                (namespace, receipt_id, ingress_event_id, minted_at.isoformat()),
            )
        return AdapterReceipt(
            source_namespace=namespace,
            receipt_id=receipt_id,
            ingress_event_id=ingress_event_id,
            minted_at=minted_at,
        )

    def adapter_receipt(
        self,
        source_namespace: str,
        receipt_id: str,
    ) -> AdapterReceipt | None:
        namespace = _identifier(source_namespace, "source_namespace")
        canonical_receipt_id = _identifier(receipt_id, "receipt_id")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM m1_adapter_receipts
                WHERE source_namespace = ? AND receipt_id = ?
                """,
                (namespace, canonical_receipt_id),
            ).fetchone()
        if row is None:
            return None
        return AdapterReceipt(
            source_namespace=row["source_namespace"],
            receipt_id=row["receipt_id"],
            ingress_event_id=row["ingress_event_id"],
            minted_at=datetime.fromisoformat(row["minted_at"]),
        )
