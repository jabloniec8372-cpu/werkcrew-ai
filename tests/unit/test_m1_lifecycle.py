from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from werkcrew_ai.infrastructure.demo_workflow_store import DemoWorkflowStore
from werkcrew_ai.intake import (
    BindConversationOperation,
    CanonicalFactInput,
    CanonicalJobActivity,
    CanonicalJobLifecycle,
    CanonicalM1LifecycleService,
    CreateJobOperation,
    DormantReason,
    FactKnowledgeState,
    FactValidationKind,
    FactVerificationState,
    FollowUpFactCandidate,
    JobFactName,
    M1LifecycleEventType,
    M1OperationEnvelope,
    M1OperationKind,
    M1OutcomeCode,
    MarkDormantOperation,
    RecordFollowUpOperation,
    WakeJobOperation,
    canonical_json_sha256,
    normalize_job_intake,
    operation_fingerprint,
)
from werkcrew_ai.migrations import discover_migrations
from werkcrew_ai.persistence import MIGRATIONS_DIRECTORY


NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
NAMESPACE = "email:werkcrew-inbox-primary"
MIGRATION_IDS = (
    "0001_m7_persistent_dispatch",
    "0002_sequential_migration_history",
    "0003_m1_canonical_job",
    "0004_m1_lifecycle",
    "0005_m1_boundary_publication",
    "0006_m2_durable_inbox",
    "0007_m3_current_plan_bootstrap",
)


@pytest.fixture
def service(tmp_path: Path) -> CanonicalM1LifecycleService:
    value = CanonicalM1LifecycleService(tmp_path / "m1-lifecycle.db")
    assert value.initialize(now=NOW) == MIGRATION_IDS
    return value


def _envelope(
    kind: M1OperationKind,
    event_id: str | None,
    *,
    namespace: str = NAMESPACE,
    occurred_at: datetime | None = NOW,
    received_at: datetime = NOW,
) -> M1OperationEnvelope:
    return M1OperationEnvelope(
        operation_kind=kind,
        source_namespace=namespace,
        ingress_event_id=event_id,
        actor_id="trusted-m1-boundary",
        actor_source="WERKCREW_APPLICATION",
        occurred_at=occurred_at,
        received_at=received_at,
    )


def _create_operation(
    job_id: str,
    event_id: str | None,
    *,
    conversation_id: str | None = None,
    raw_text: str | None = "Original client words",
    raw_payload: str | None = '{"source":"email"}',
    received_at: datetime = NOW,
    **facts: object,
) -> CreateJobOperation:
    return CreateJobOperation(
        envelope=_envelope(
            M1OperationKind.CREATE_JOB,
            event_id,
            received_at=received_at,
        ),
        job_id=job_id,
        conversation_id=conversation_id,
        intake=normalize_job_intake(
            intake_source="CLIENT_EMAIL",
            raw_text=raw_text,
            raw_payload=raw_payload,
            **facts,
        ),
    )


def _create(
    service: CanonicalM1LifecycleService,
    job_id: str,
    event_id: str,
    **values: object,
):
    result = service.execute(_create_operation(job_id, event_id, **values))
    assert result.outcome is M1OutcomeCode.JOB_CREATED
    return result


def _follow_operation(
    event_id: str,
    *,
    job_id: str | None = None,
    conversation_id: str | None = None,
    raw_text: str | None = "Follow-up client words",
    raw_payload: str | None = '{"kind":"follow-up"}',
    fact_candidates: tuple[FollowUpFactCandidate, ...] = (),
    occurred_at: datetime | None = NOW + timedelta(hours=1),
    received_at: datetime = NOW + timedelta(hours=1),
) -> RecordFollowUpOperation:
    return RecordFollowUpOperation(
        envelope=_envelope(
            M1OperationKind.RECORD_FOLLOW_UP,
            event_id,
            occurred_at=occurred_at,
            received_at=received_at,
        ),
        explicit_job_id=job_id,
        conversation_id=conversation_id,
        raw_text=raw_text,
        raw_payload=raw_payload,
        fact_candidates=fact_candidates,
    )


def _mark_operation(
    job_id: str,
    event_id: str,
    *,
    reason: DormantReason = DormantReason.NO_CUSTOMER_RESPONSE,
    detail: str | None = None,
    evidence_id: str | None = None,
    received_at: datetime = NOW + timedelta(days=1),
) -> MarkDormantOperation:
    return MarkDormantOperation(
        envelope=_envelope(
            M1OperationKind.MARK_DORMANT,
            event_id,
            occurred_at=received_at,
            received_at=received_at,
        ),
        job_id=job_id,
        reason=reason,
        reason_detail=detail,
        supporting_evidence_id=evidence_id,
    )


def _wake_operation(
    job_id: str,
    event_id: str,
    *,
    received_at: datetime = NOW + timedelta(days=2),
) -> WakeJobOperation:
    return WakeJobOperation(
        envelope=_envelope(
            M1OperationKind.WAKE_JOB,
            event_id,
            occurred_at=received_at,
            received_at=received_at,
        ),
        job_id=job_id,
    )


def _bind_operation(
    job_id: str,
    conversation_id: str,
    event_id: str,
) -> BindConversationOperation:
    return BindConversationOperation(
        envelope=_envelope(
            M1OperationKind.BIND_CONVERSATION,
            event_id,
            received_at=NOW + timedelta(minutes=10),
        ),
        job_id=job_id,
        conversation_id=conversation_id,
    )


def _scalar(database_path: str, sql: str, values: tuple[object, ...] = ()):
    with sqlite3.connect(database_path) as connection:
        return connection.execute(sql, values).fetchone()[0]


def test_fingerprint_uses_frozen_canonical_json_and_excludes_received_at() -> None:
    value = {"z": [True, None, 2, {"é": " exact "}], "a": 1}
    encoded, digest = canonical_json_sha256(value)
    assert encoded == '{"a":1,"z":[true,null,2,{"é":" exact "}]}'
    assert digest == hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    first = _create_operation("job-fingerprint", "event-fingerprint")
    retry = replace(
        first,
        envelope=replace(first.envelope, received_at=NOW + timedelta(days=1)),
    )
    changed = replace(first, intake=replace(first.intake, raw_text="changed"))
    assert operation_fingerprint(first) == operation_fingerprint(retry)
    assert operation_fingerprint(first) != operation_fingerprint(changed)


def test_same_webhook_twice_returns_exact_result_and_one_effect(
    service: CanonicalM1LifecycleService,
) -> None:
    operation = _create_operation("job-idempotent", "event-idempotent")
    first = service.execute(operation)
    retry = service.execute(
        replace(
            operation,
            envelope=replace(
                operation.envelope,
                received_at=NOW + timedelta(hours=4),
            ),
        )
    )
    assert retry == first
    assert service.operation_result(NAMESPACE, "event-idempotent") == first
    assert _scalar(service.database_path, "SELECT COUNT(*) FROM canonical_jobs") == 1
    assert _scalar(service.database_path, "SELECT COUNT(*) FROM m1_operations") == 1


def test_concurrent_same_key_creates_one_result_and_effect(
    service: CanonicalM1LifecycleService,
) -> None:
    operation = _create_operation("job-concurrent-key", "event-concurrent-key")

    def execute_once():
        return CanonicalM1LifecycleService(service.database_path).execute(operation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _item: execute_once(), range(2)))

    assert results[0] == results[1]
    assert _scalar(service.database_path, "SELECT COUNT(*) FROM canonical_jobs") == 1
    assert _scalar(service.database_path, "SELECT COUNT(*) FROM m1_operations") == 1


def test_same_key_altered_payload_returns_event_conflict(
    service: CanonicalM1LifecycleService,
) -> None:
    original = _create_operation("job-event-conflict", "event-conflict")
    first = service.execute(original)
    conflict = service.execute(
        replace(original, intake=replace(original.intake, raw_text="altered"))
    )
    conflict_retry_operation = replace(
        original,
        envelope=replace(
            original.envelope,
            received_at=NOW + timedelta(days=3),
        ),
        intake=replace(original.intake, raw_text="altered"),
    )
    assert conflict.outcome is M1OutcomeCode.EVENT_CONFLICT
    assert service.execute(conflict_retry_operation) == conflict
    assert service.operation_result(NAMESPACE, "event-conflict") == first
    assert service.get_job("job-event-conflict").raw_text == "Original client words"
    assert _scalar(
        service.database_path,
        "SELECT COUNT(*) FROM m1_operation_conflicts",
    ) == 1


def test_same_key_reused_across_operation_kind_returns_event_conflict(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(service, "job-kind-conflict", "shared-event")
    conflict = service.execute(_wake_operation("job-kind-conflict", "shared-event"))
    assert conflict.outcome is M1OutcomeCode.EVENT_CONFLICT
    assert service.operation_result(NAMESPACE, "shared-event").outcome is (
        M1OutcomeCode.JOB_CREATED
    )


def test_identical_content_with_different_event_ids_is_distinct(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(service, "job-distinct-events", "create-distinct")
    first = service.execute(
        _follow_operation("follow-distinct-1", job_id="job-distinct-events")
    )
    second = service.execute(
        _follow_operation("follow-distinct-2", job_id="job-distinct-events")
    )
    assert first.evidence_id != second.evidence_id
    assert len(service.follow_up_evidence("job-distinct-events")) == 2


def test_retried_rejection_is_immutable_after_later_binding(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(service, "job-later-binding", "create-later-binding")
    rejected_operation = _follow_operation(
        "follow-before-binding",
        conversation_id="conversation-later",
    )
    rejected = service.execute(rejected_operation)
    assert rejected.outcome is M1OutcomeCode.CORRELATION_REQUIRED
    service.execute(
        _bind_operation(
            "job-later-binding",
            "conversation-later",
            "bind-later",
        )
    )
    retry = service.execute(
        replace(
            rejected_operation,
            envelope=replace(
                rejected_operation.envelope,
                received_at=NOW + timedelta(days=5),
            ),
        )
    )
    assert retry == rejected
    assert service.follow_up_evidence("job-later-binding") == ()


def test_missing_trustworthy_id_requires_idempotency_key(
    service: CanonicalM1LifecycleService,
) -> None:
    result = service.execute(_create_operation("job-no-id", None))
    assert result.outcome is M1OutcomeCode.IDEMPOTENCY_KEY_REQUIRED
    assert _scalar(service.database_path, "SELECT COUNT(*) FROM canonical_jobs") == 0
    assert _scalar(service.database_path, "SELECT COUNT(*) FROM m1_operations") == 0


def test_durable_adapter_receipt_survives_restart_and_deduplicates(
    service: CanonicalM1LifecycleService,
) -> None:
    receipt = service.mint_adapter_receipt(
        source_namespace=NAMESPACE,
        minted_at=NOW,
    )
    reopened = CanonicalM1LifecycleService(service.database_path)
    assert reopened.adapter_receipt(NAMESPACE, receipt.receipt_id) == receipt
    operation = _create_operation("job-receipt", receipt.ingress_event_id)
    first = service.execute(operation)
    assert reopened.execute(operation) == first
    assert _scalar(service.database_path, "SELECT COUNT(*) FROM canonical_jobs") == 1


def test_explicit_valid_job_targets_same_job_without_silent_binding(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(service, "job-explicit", "create-explicit")
    result = service.execute(
        _follow_operation(
            "follow-explicit",
            job_id="job-explicit",
            conversation_id="unbound-conversation",
        )
    )
    assert result.outcome is M1OutcomeCode.FOLLOW_UP_RECORDED
    assert result.job_id == "job-explicit"
    assert service.conversation_job_ids(NAMESPACE, "unbound-conversation") == ()


def test_explicit_missing_job_returns_persisted_target_not_found(
    service: CanonicalM1LifecycleService,
) -> None:
    operation = _follow_operation("follow-missing", job_id="missing-job")
    result = service.execute(operation)
    assert result.outcome is M1OutcomeCode.TARGET_NOT_FOUND
    assert service.operation_result(NAMESPACE, "follow-missing") == result
    assert _scalar(
        service.database_path,
        "SELECT COUNT(*) FROM m1_follow_up_evidence",
    ) == 0


def test_zero_associations_requires_correlation(
    service: CanonicalM1LifecycleService,
) -> None:
    result = service.execute(
        _follow_operation("follow-zero", conversation_id="conversation-zero")
    )
    assert result.outcome is M1OutcomeCode.CORRELATION_REQUIRED


def test_exactly_one_conversation_association_correlates(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(
        service,
        "job-one-association",
        "create-one-association",
        conversation_id="conversation-one",
    )
    result = service.execute(
        _follow_operation("follow-one", conversation_id="conversation-one")
    )
    assert result.outcome is M1OutcomeCode.FOLLOW_UP_RECORDED
    assert result.job_id == "job-one-association"


def test_same_customer_contact_and_address_can_create_two_jobs(
    service: CanonicalM1LifecycleService,
) -> None:
    values = {
        "client_name": "Same Client",
        "email": "same@example.test",
        "phone": "+49 111",
        "address": "Same address",
        "scope": "Same words",
    }
    _create(service, "job-same-customer-1", "create-same-1", **values)
    _create(service, "job-same-customer-2", "create-same-2", **values)
    assert _scalar(service.database_path, "SELECT COUNT(*) FROM canonical_jobs") == 2


def test_matching_hints_alone_cause_no_mutation(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(
        service,
        "job-hints",
        "create-hints",
        client_name="Hint Client",
        address="Hint address",
    )
    result = service.execute(
        _follow_operation(
            "follow-hints",
            raw_text="Hint Client asks about Hint address",
        )
    )
    assert result.outcome is M1OutcomeCode.CORRELATION_REQUIRED
    assert service.follow_up_evidence("job-hints") == ()


def test_second_job_in_same_conversation_creates_second_association(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(
        service,
        "job-conversation-1",
        "create-conversation-1",
        conversation_id="conversation-many",
    )
    _create(
        service,
        "job-conversation-2",
        "create-conversation-2",
        conversation_id="conversation-many",
    )
    assert service.conversation_job_ids(NAMESPACE, "conversation-many") == (
        "job-conversation-1",
        "job-conversation-2",
    )


def test_ambiguous_conversation_without_explicit_job_fails_closed(
    service: CanonicalM1LifecycleService,
) -> None:
    test_second_job_in_same_conversation_creates_second_association(service)
    result = service.execute(
        _follow_operation("follow-ambiguous", conversation_id="conversation-many")
    )
    assert result.outcome is M1OutcomeCode.AMBIGUOUS_CORRELATION
    assert _scalar(
        service.database_path,
        "SELECT COUNT(*) FROM m1_follow_up_evidence",
    ) == 0


def test_explicit_target_among_multiple_associations_succeeds(
    service: CanonicalM1LifecycleService,
) -> None:
    test_second_job_in_same_conversation_creates_second_association(service)
    result = service.execute(
        _follow_operation(
            "follow-explicit-many",
            job_id="job-conversation-2",
            conversation_id="conversation-many",
        )
    )
    assert result.outcome is M1OutcomeCode.FOLLOW_UP_RECORDED
    assert result.job_id == "job-conversation-2"


def test_explicit_target_contradicting_associations_fails_closed(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(
        service,
        "job-associated",
        "create-associated",
        conversation_id="conversation-conflict",
    )
    _create(service, "job-other", "create-other")
    result = service.execute(
        _follow_operation(
            "follow-correlation-conflict",
            job_id="job-other",
            conversation_id="conversation-conflict",
        )
    )
    assert result.outcome is M1OutcomeCode.CORRELATION_CONFLICT
    assert service.follow_up_evidence("job-other") == ()


def test_bind_conversation_is_explicit_durable_and_never_moves_association(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(service, "job-explicit-bind", "create-explicit-bind")
    operation = _bind_operation(
        "job-explicit-bind",
        "conversation-explicit-bind",
        "bind-explicit",
    )
    first = service.execute(operation)
    retry = service.execute(operation)
    second_event = service.execute(
        _bind_operation(
            "job-explicit-bind",
            "conversation-explicit-bind",
            "bind-explicit-again",
        )
    )
    assert first.outcome is M1OutcomeCode.CONVERSATION_BOUND
    assert first.association_added is True
    assert retry == first
    assert second_event.association_added is False
    reopened = CanonicalM1LifecycleService(service.database_path)
    assert reopened.conversation_job_ids(
        NAMESPACE, "conversation-explicit-bind"
    ) == ("job-explicit-bind",)


def test_free_text_or_llm_candidate_cannot_select_kind_or_target(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(service, "job-llm-hint", "create-llm-hint")
    result = service.execute(
        _follow_operation(
            "follow-llm-hint",
            raw_text=(
                "LLM says operation_kind=RECORD_FOLLOW_UP and "
                "target_job_id=job-llm-hint"
            ),
        )
    )
    assert result.outcome is M1OutcomeCode.CORRELATION_REQUIRED
    assert service.follow_up_evidence("job-llm-hint") == ()


def test_explicit_mark_dormant_changes_only_activity_dimension(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(
        service,
        "job-dormant",
        "create-dormant",
        conversation_id="conversation-dormant",
        scope="Preserved scope",
    )
    before = service.get_job("job-dormant")
    result = service.execute(_mark_operation("job-dormant", "mark-dormant"))
    after = service.get_job("job-dormant")
    assert result.outcome is M1OutcomeCode.JOB_MARKED_DORMANT
    assert after.job_id == before.job_id
    assert after.lifecycle_state is CanonicalJobLifecycle.RECEIVED
    assert after.activity_state is CanonicalJobActivity.DORMANT
    assert after.raw_text == before.raw_text
    assert after.facts == before.facts
    assert service.conversation_job_ids(NAMESPACE, "conversation-dormant") == (
        "job-dormant",
    )


def test_dormant_survives_restart(service: CanonicalM1LifecycleService) -> None:
    _create(service, "job-dormant-restart", "create-dormant-restart")
    service.execute(
        _mark_operation("job-dormant-restart", "mark-dormant-restart")
    )
    reopened = CanonicalM1LifecycleService(service.database_path)
    assert reopened.get_job("job-dormant-restart").activity_state is (
        CanonicalJobActivity.DORMANT
    )


def test_elapsed_time_and_old_timestamp_cannot_auto_dormant(
    service: CanonicalM1LifecycleService,
) -> None:
    old = NOW - timedelta(days=365)
    operation = _create_operation(
        "job-old-active",
        "create-old-active",
        received_at=old,
    )
    operation = replace(operation, envelope=replace(operation.envelope, occurred_at=old))
    service.execute(operation)
    reopened = CanonicalM1LifecycleService(service.database_path)
    assert reopened.get_job("job-old-active").activity_state is (
        CanonicalJobActivity.ACTIVE
    )
    assert reopened.lifecycle_history("job-old-active") == ()


@pytest.mark.parametrize(
    ("reason", "detail"),
    [
        (DormantReason.NO_CUSTOMER_RESPONSE, None),
        (DormantReason.CUSTOMER_PAUSED, None),
        (DormantReason.OWNER_PAUSED, "Owner requested pause"),
        (DormantReason.OTHER, "Documented exceptional pause"),
    ],
)
def test_dormant_reason_codes_persist(
    service: CanonicalM1LifecycleService,
    reason: DormantReason,
    detail: str | None,
) -> None:
    suffix = reason.value.lower()
    job_id = f"job-reason-{suffix}"
    _create(service, job_id, f"create-reason-{suffix}")
    service.execute(
        _mark_operation(
            job_id,
            f"mark-reason-{suffix}",
            reason=reason,
            detail=detail,
        )
    )
    event = service.lifecycle_history(job_id)[0]
    assert event.reason is reason
    assert event.reason_detail == detail


def test_other_without_detail_is_rejected_before_mutation(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(service, "job-other-reason", "create-other-reason")
    with pytest.raises(ValueError, match="OTHER requires"):
        _mark_operation(
            "job-other-reason",
            "mark-other-reason",
            reason=DormantReason.OTHER,
            detail="   ",
        )
    assert service.get_job("job-other-reason").activity_state is (
        CanonicalJobActivity.ACTIVE
    )


def test_second_mark_dormant_is_noop_with_no_second_transition(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(service, "job-double-dormant", "create-double-dormant")
    first = service.execute(
        _mark_operation("job-double-dormant", "mark-double-dormant-1")
    )
    second = service.execute(
        _mark_operation("job-double-dormant", "mark-double-dormant-2")
    )
    assert first.outcome is M1OutcomeCode.JOB_MARKED_DORMANT
    assert second.outcome is M1OutcomeCode.ALREADY_DORMANT
    assert [event.event_type for event in service.lifecycle_history("job-double-dormant")] == [
        M1LifecycleEventType.JOB_MARKED_DORMANT
    ]


def test_authoritative_follow_up_wakes_same_job_id(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(service, "job-follow-wake", "create-follow-wake")
    service.execute(_mark_operation("job-follow-wake", "mark-follow-wake"))
    result = service.execute(
        _follow_operation("follow-wake", job_id="job-follow-wake")
    )
    job = service.get_job("job-follow-wake")
    assert result.job_id == "job-follow-wake"
    assert job.job_id == "job-follow-wake"
    assert job.activity_state is CanonicalJobActivity.ACTIVE
    assert [event.event_type for event in service.lifecycle_history(job.job_id)] == [
        M1LifecycleEventType.JOB_MARKED_DORMANT,
        M1LifecycleEventType.FOLLOW_UP_RECORDED,
        M1LifecycleEventType.JOB_WOKEN,
    ]


def test_months_later_authoritative_follow_up_wakes_identically(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(service, "job-months-later", "create-months-later")
    service.execute(_mark_operation("job-months-later", "mark-months-later"))
    months_later = NOW + timedelta(days=180)
    result = service.execute(
        _follow_operation(
            "follow-months-later",
            job_id="job-months-later",
            occurred_at=months_later,
            received_at=months_later,
        )
    )
    assert result.activity_state is CanonicalJobActivity.ACTIVE
    assert result.job_id == "job-months-later"


def test_wake_preserves_unrelated_workflow_safe_hold_and_bindings(
    service: CanonicalM1LifecycleService,
) -> None:
    job_id = "job-safe-hold-preserved"
    _create(
        service,
        job_id,
        "create-safe-hold",
        conversation_id="conversation-safe-hold",
    )
    with sqlite3.connect(service.database_path) as connection:
        timestamp = NOW.isoformat()
        connection.execute(
            """
            INSERT INTO jobs(
                job_id, title, address_status, address_json, created_at, updated_at
            ) VALUES(?, 'Separate workflow', 'UNCONFIRMED', '{}', ?, ?)
            """,
            (job_id, timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO workflow_instances(
                workflow_instance_id, job_id, revision, schema_version,
                state, snapshot_json, created_at, updated_at
            ) VALUES('workflow-safe-hold', ?, 7, 1, 'SAFE_HOLD',
                     '{"safe_hold":true}', ?, ?)
            """,
            (job_id, timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO session_bindings(
                workflow_instance_id, session_id, agent_id, created_at
            ) VALUES('workflow-safe-hold', 'session-safe-hold',
                     'agent-safe-hold', ?)
            """,
            (timestamp,),
        )
    service.execute(_mark_operation(job_id, "mark-safe-hold"))
    service.execute(_wake_operation(job_id, "wake-safe-hold"))
    with sqlite3.connect(service.database_path) as connection:
        assert connection.execute(
            "SELECT state, revision, snapshot_json FROM workflow_instances"
        ).fetchone() == ("SAFE_HOLD", 7, '{"safe_hold":true}')
        assert connection.execute(
            "SELECT session_id FROM session_bindings"
        ).fetchone() == ("session-safe-hold",)
    assert service.conversation_job_ids(NAMESPACE, "conversation-safe-hold") == (
        job_id,
    )


def test_same_follow_up_retry_creates_one_evidence_and_one_wake(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(service, "job-follow-retry", "create-follow-retry")
    service.execute(_mark_operation("job-follow-retry", "mark-follow-retry"))
    operation = _follow_operation("follow-retry", job_id="job-follow-retry")
    first = service.execute(operation)
    retry = service.execute(operation)
    assert retry == first
    assert len(service.follow_up_evidence("job-follow-retry")) == 1
    assert sum(
        event.event_type is M1LifecycleEventType.JOB_WOKEN
        for event in service.lifecycle_history("job-follow-retry")
    ) == 1


def test_follow_up_against_active_records_no_woken_event(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(service, "job-active-follow", "create-active-follow")
    result = service.execute(
        _follow_operation("follow-active", job_id="job-active-follow")
    )
    assert result.outcome is M1OutcomeCode.FOLLOW_UP_RECORDED
    assert [
        event.event_type for event in service.lifecycle_history("job-active-follow")
    ] == [M1LifecycleEventType.FOLLOW_UP_RECORDED]


def test_wake_retry_returns_exact_first_result(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(service, "job-wake-retry", "create-wake-retry")
    service.execute(_mark_operation("job-wake-retry", "mark-wake-retry"))
    operation = _wake_operation("job-wake-retry", "wake-retry")
    first = service.execute(operation)
    retry = service.execute(operation)
    assert first.outcome is M1OutcomeCode.JOB_WOKEN
    assert retry == first
    assert sum(
        event.event_type is M1LifecycleEventType.JOB_WOKEN
        for event in service.lifecycle_history("job-wake-retry")
    ) == 1


def test_different_wake_on_active_returns_already_active(
    service: CanonicalM1LifecycleService,
) -> None:
    _create(service, "job-already-active", "create-already-active")
    result = service.execute(_wake_operation("job-already-active", "wake-active"))
    assert result.outcome is M1OutcomeCode.ALREADY_ACTIVE
    assert service.lifecycle_history("job-already-active") == ()
    assert service.operation_result(NAMESPACE, "wake-active") == result


def test_concurrent_wake_and_follow_up_create_one_woken_event(
    service: CanonicalM1LifecycleService,
) -> None:
    job_id = "job-concurrent-wake"
    _create(service, job_id, "create-concurrent-wake")
    service.execute(_mark_operation(job_id, "mark-concurrent-wake"))
    operations = (
        _wake_operation(job_id, "wake-concurrent"),
        _follow_operation("follow-concurrent-wake", job_id=job_id),
    )

    def execute(operation):
        return CanonicalM1LifecycleService(service.database_path).execute(operation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        tuple(pool.map(execute, operations))

    assert len(service.follow_up_evidence(job_id)) == 1
    assert sum(
        event.event_type is M1LifecycleEventType.JOB_WOKEN
        for event in service.lifecycle_history(job_id)
    ) == 1
    assert service.get_job(job_id).activity_state is CanonicalJobActivity.ACTIVE


def test_two_concurrent_follow_ups_record_two_evidence_and_one_wake(
    service: CanonicalM1LifecycleService,
) -> None:
    job_id = "job-two-concurrent-followups"
    _create(service, job_id, "create-two-followups")
    service.execute(_mark_operation(job_id, "mark-two-followups"))
    operations = (
        _follow_operation("follow-concurrent-1", job_id=job_id, raw_text="one"),
        _follow_operation("follow-concurrent-2", job_id=job_id, raw_text="two"),
    )

    def execute(operation):
        return CanonicalM1LifecycleService(service.database_path).execute(operation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        tuple(pool.map(execute, operations))

    assert {item.raw_text for item in service.follow_up_evidence(job_id)} == {
        "one",
        "two",
    }
    assert sum(
        event.event_type is M1LifecycleEventType.JOB_WOKEN
        for event in service.lifecycle_history(job_id)
    ) == 1


def test_original_intake_remains_byte_exact_after_follow_up(
    service: CanonicalM1LifecycleService,
) -> None:
    raw_text = "  Original\r\nclient bytes and spacing.  "
    raw_payload = '{ "body": "original", "spacing": true }\r\n'
    _create(
        service,
        "job-raw-preserved",
        "create-raw-preserved",
        raw_text=raw_text,
        raw_payload=raw_payload,
    )
    service.execute(
        _follow_operation(
            "follow-raw-preserved",
            job_id="job-raw-preserved",
            raw_text="new evidence",
            raw_payload='{"new":true}',
        )
    )
    job = service.get_job("job-raw-preserved")
    assert job.raw_text == raw_text
    assert job.raw_payload == raw_payload


def test_multiple_follow_ups_are_independently_retrievable_in_order(
    service: CanonicalM1LifecycleService,
) -> None:
    job_id = "job-evidence-order"
    _create(service, job_id, "create-evidence-order")
    service.execute(
        _follow_operation(
            "follow-order-1",
            job_id=job_id,
            raw_text="first",
            received_at=NOW + timedelta(hours=1),
        )
    )
    service.execute(
        _follow_operation(
            "follow-order-2",
            job_id=job_id,
            raw_text="second",
            received_at=NOW + timedelta(hours=2),
        )
    )
    evidence = service.follow_up_evidence(job_id)
    assert [item.raw_text for item in evidence] == ["first", "second"]
    assert evidence[0].evidence_id != evidence[1].evidence_id


def test_valid_follow_up_fact_appends_revision_linked_to_evidence(
    service: CanonicalM1LifecycleService,
) -> None:
    job_id = "job-fact-link"
    _create(service, job_id, "create-fact-link", address="Initial address")
    candidate = FollowUpFactCandidate(
        fact=CanonicalFactInput(
            name=JobFactName.ADDRESS,
            value="Verified follow-up address",
            knowledge_state=FactKnowledgeState.KNOWN,
            verification_state=FactVerificationState.VERIFIED,
            provenance_source="OWNER",
        ),
        expected_current_revision=1,
        validation_kind=FactValidationKind.HUMAN,
        validated_by="owner-user-1",
    )
    result = service.execute(
        _follow_operation(
            "follow-fact-link",
            job_id=job_id,
            fact_candidates=(candidate,),
        )
    )
    history = service.fact_history(job_id, JobFactName.ADDRESS)
    assert result.fact_revisions == ((JobFactName.ADDRESS.value, 2),)
    assert [item.revision for item in history] == [1, 2]
    assert history[1].follow_up_evidence_id == result.evidence_id
    assert history[0].follow_up_evidence_id is None


def test_follow_up_fact_candidate_requires_deterministic_or_human_validation() -> None:
    fact = CanonicalFactInput(
        JobFactName.SCOPE,
        "LLM candidate",
        FactKnowledgeState.KNOWN,
        FactVerificationState.UNVERIFIED,
        "LLM_EXTRACTION",
    )
    with pytest.raises(ValueError, match="validation_kind"):
        FollowUpFactCandidate(
            fact,
            0,
            "LLM_ONLY",  # type: ignore[arg-type]
            "untrusted-model-output",
        )


def test_fact_batch_is_all_or_none_but_evidence_and_wake_persist(
    service: CanonicalM1LifecycleService,
) -> None:
    job_id = "job-fact-batch"
    _create(service, job_id, "create-fact-batch", phone="initial phone")
    service.execute(_mark_operation(job_id, "mark-fact-batch"))
    stale_phone = FollowUpFactCandidate(
        CanonicalFactInput(
            JobFactName.PHONE,
            "new phone",
            FactKnowledgeState.KNOWN,
            FactVerificationState.VERIFIED,
            "OWNER",
        ),
        0,
        FactValidationKind.HUMAN,
        "owner-user-batch",
    )
    new_scope = FollowUpFactCandidate(
        CanonicalFactInput(
            JobFactName.SCOPE,
            "new scope",
            FactKnowledgeState.KNOWN,
            FactVerificationState.UNVERIFIED,
            "CLIENT_EMAIL",
        ),
        0,
        FactValidationKind.DETERMINISTIC,
        "validated-intake-parser-v1",
    )
    conflict = service.execute(
        _follow_operation(
            "follow-fact-batch-stale",
            job_id=job_id,
            fact_candidates=(stale_phone, new_scope),
        )
    )
    assert conflict.outcome is M1OutcomeCode.FACT_REVISION_CONFLICT
    assert conflict.evidence_id is not None
    assert conflict.fact_revisions == ()
    assert service.get_job(job_id).activity_state is CanonicalJobActivity.ACTIVE
    assert service.get_job(job_id).fact(JobFactName.SCOPE) is None
    assert len(service.fact_history(job_id, JobFactName.PHONE)) == 1
    assert [event.event_type for event in service.lifecycle_history(job_id)] == [
        M1LifecycleEventType.JOB_MARKED_DORMANT,
        M1LifecycleEventType.FOLLOW_UP_RECORDED,
        M1LifecycleEventType.JOB_WOKEN,
    ]

    corrected = service.execute(
        _follow_operation(
            "follow-fact-batch-corrected",
            job_id=job_id,
            fact_candidates=(
                replace(stale_phone, expected_current_revision=1),
                new_scope,
            ),
        )
    )
    assert corrected.outcome is M1OutcomeCode.FOLLOW_UP_RECORDED
    assert corrected.fact_revisions == (
        (JobFactName.PHONE.value, 2),
        (JobFactName.SCOPE.value, 1),
    )


def test_concurrent_same_fact_cas_has_one_winner_and_preserves_stale_evidence(
    service: CanonicalM1LifecycleService,
) -> None:
    job_id = "job-concurrent-fact"
    _create(service, job_id, "create-concurrent-fact", phone="initial")
    candidate_one = FollowUpFactCandidate(
        CanonicalFactInput(
            JobFactName.PHONE,
            "winner one",
            FactKnowledgeState.KNOWN,
            FactVerificationState.UNVERIFIED,
            "CALL_ONE",
        ),
        1,
        FactValidationKind.DETERMINISTIC,
        "validated-phone-parser-v1",
    )
    candidate_two = replace(
        candidate_one,
        fact=replace(
            candidate_one.fact,
            value="winner two",
            provenance_source="CALL_TWO",
        ),
    )
    operations = (
        _follow_operation(
            "follow-fact-cas-1",
            job_id=job_id,
            fact_candidates=(candidate_one,),
        ),
        _follow_operation(
            "follow-fact-cas-2",
            job_id=job_id,
            fact_candidates=(candidate_two,),
        ),
    )

    def execute(operation):
        return CanonicalM1LifecycleService(service.database_path).execute(operation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(execute, operations))

    assert {result.outcome for result in results} == {
        M1OutcomeCode.FOLLOW_UP_RECORDED,
        M1OutcomeCode.FACT_REVISION_CONFLICT,
    }
    assert len(service.follow_up_evidence(job_id)) == 2
    assert len(service.fact_history(job_id, JobFactName.PHONE)) == 2
    stale = next(
        result
        for result in results
        if result.outcome is M1OutcomeCode.FACT_REVISION_CONFLICT
    )
    assert stale.evidence_id is not None
    assert stale.fact_revisions == ()
    assert service.get_job(job_id).source_revision == 2


def test_missing_unknown_known_and_verification_semantics_are_preserved(
    service: CanonicalM1LifecycleService,
) -> None:
    job_id = "job-knowledge-semantics"
    _create(
        service,
        job_id,
        "create-knowledge-semantics",
        explicitly_unknown=(JobFactName.MATERIALS,),
    )
    before = service.get_job(job_id)
    assert before.fact(JobFactName.EMAIL) is None
    assert before.fact(JobFactName.MATERIALS).knowledge_state is (
        FactKnowledgeState.UNKNOWN
    )
    candidate = FollowUpFactCandidate(
        CanonicalFactInput(
            JobFactName.MATERIALS,
            "Owner-selected materials",
            FactKnowledgeState.KNOWN,
            FactVerificationState.VERIFIED,
            "OWNER",
        ),
        1,
        FactValidationKind.HUMAN,
        "owner-user-2",
    )
    service.execute(
        _follow_operation(
            "follow-knowledge-semantics",
            job_id=job_id,
            fact_candidates=(candidate,),
        )
    )
    after = service.get_job(job_id)
    assert after.fact(JobFactName.EMAIL) is None
    assert after.fact(JobFactName.MATERIALS).knowledge_state is (
        FactKnowledgeState.KNOWN
    )
    assert after.fact(JobFactName.MATERIALS).verification_state is (
        FactVerificationState.VERIFIED
    )
    assert [item.knowledge_state for item in service.fact_history(
        job_id, JobFactName.MATERIALS
    )] == [FactKnowledgeState.UNKNOWN, FactKnowledgeState.KNOWN]


def test_failure_before_commit_leaves_no_partial_effect(
    service: CanonicalM1LifecycleService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_before_commit(*_args):
        raise RuntimeError("simulated failure before commit")

    monkeypatch.setattr(service, "_before_commit", fail_before_commit)
    with pytest.raises(RuntimeError, match="simulated failure"):
        service.execute(_create_operation("job-rollback", "create-rollback"))
    assert _scalar(service.database_path, "SELECT COUNT(*) FROM canonical_jobs") == 0
    assert _scalar(service.database_path, "SELECT COUNT(*) FROM m1_operations") == 0


def test_lost_response_after_commit_retry_has_no_duplicate_effect(
    service: CanonicalM1LifecycleService,
) -> None:
    operation = _create_operation("job-lost-response", "create-lost-response")
    persisted_first_result = service.execute(operation)
    reopened = CanonicalM1LifecycleService(service.database_path)
    recovered = reopened.execute(operation)
    assert recovered == persisted_first_result
    assert _scalar(service.database_path, "SELECT COUNT(*) FROM canonical_jobs") == 1
    assert _scalar(service.database_path, "SELECT COUNT(*) FROM m1_operations") == 1


def test_lifecycle_history_is_append_only_sequenced_and_linked(
    service: CanonicalM1LifecycleService,
) -> None:
    job_id = "job-history"
    _create(service, job_id, "create-history")
    follow = service.execute(
        _follow_operation("follow-history", job_id=job_id)
    )
    dormant = service.execute(
        _mark_operation(
            job_id,
            "mark-history",
            reason=DormantReason.OWNER_PAUSED,
            detail="Owner audit detail",
            evidence_id=follow.evidence_id,
        )
    )
    wake = service.execute(_wake_operation(job_id, "wake-history"))
    history = service.lifecycle_history(job_id)
    assert [event.job_sequence for event in history] == [1, 2, 3]
    assert [event.event_type for event in history] == [
        M1LifecycleEventType.FOLLOW_UP_RECORDED,
        M1LifecycleEventType.JOB_MARKED_DORMANT,
        M1LifecycleEventType.JOB_WOKEN,
    ]
    assert history[0].evidence_id == follow.evidence_id
    assert history[1].evidence_id == follow.evidence_id
    assert history[1].reason is DormantReason.OWNER_PAUSED
    assert history[1].reason_detail == "Owner audit detail"
    assert history[0].from_activity_state is None
    assert history[0].to_activity_state is None
    assert history[1].from_activity_state is CanonicalJobActivity.ACTIVE
    assert history[1].to_activity_state is CanonicalJobActivity.DORMANT
    assert history[2].from_activity_state is CanonicalJobActivity.DORMANT
    assert history[2].to_activity_state is CanonicalJobActivity.ACTIVE
    assert dormant.lifecycle_event_ids == (history[1].lifecycle_event_id,)
    assert wake.lifecycle_event_ids == (history[2].lifecycle_event_id,)


def test_upgrade_from_exact_schema_at_0003_applies_only_later_migrations(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "schema-at-0003.db"
    first, second, third, *_later = discover_migrations(MIGRATIONS_DIRECTORY)
    with sqlite3.connect(database_path) as connection:
        connection.executescript(first.sql)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(1, ?)",
            (NOW.isoformat(),),
        )
        connection.executescript(second.sql)
        connection.execute(
            """
            INSERT INTO schema_migrations(
                migration_id, version, name, checksum_sha256, applied_at
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                second.migration_id,
                second.version,
                second.name,
                second.checksum_sha256,
                NOW.isoformat(),
            ),
        )
        connection.executescript(third.sql)
        connection.execute(
            """
            INSERT INTO schema_migrations(
                migration_id, version, name, checksum_sha256, applied_at
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                third.migration_id,
                third.version,
                third.name,
                third.checksum_sha256,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO canonical_jobs(
                job_id, lifecycle_state, created_at, updated_at
            ) VALUES('job-existing-0003', 'RECEIVED', ?, ?)
            """,
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            """
            INSERT INTO canonical_job_intake(
                job_id, intake_source, raw_text, raw_payload, received_at
            ) VALUES('job-existing-0003', 'EMAIL', 'old raw', NULL, ?)
            """,
            (NOW.isoformat(),),
        )

    upgraded = CanonicalM1LifecycleService(database_path)
    assert upgraded.initialize(now=NOW) == MIGRATION_IDS[3:]
    assert upgraded.applied_migration_ids() == MIGRATION_IDS
    assert upgraded.get_job("job-existing-0003").activity_state is (
        CanonicalJobActivity.ACTIVE
    )


def test_repeated_initialization_after_0004_is_idempotent(
    tmp_path: Path,
) -> None:
    service = CanonicalM1LifecycleService(tmp_path / "idempotent-0004.db")
    assert service.initialize(now=NOW) == MIGRATION_IDS
    assert service.initialize(now=NOW) == ()
    assert service.applied_migration_ids() == MIGRATION_IDS


def test_demo_workflow_store_is_never_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("DemoWorkflowStore must not participate in M1 v1")

    monkeypatch.setattr(DemoWorkflowStore, "__init__", forbidden)
    service = CanonicalM1LifecycleService(tmp_path / "sqlite-only-lifecycle.db")
    service.initialize(now=NOW)
    _create(service, "job-no-demo-store", "create-no-demo-store")
    service.execute(
        _mark_operation("job-no-demo-store", "mark-no-demo-store")
    )
    service.execute(_wake_operation("job-no-demo-store", "wake-no-demo-store"))
    assert service.get_job("job-no-demo-store").activity_state is (
        CanonicalJobActivity.ACTIVE
    )
