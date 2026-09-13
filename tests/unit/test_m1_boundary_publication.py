from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

from werkcrew_ai.intake import (
    BindConversationOperation,
    CanonicalFactInput,
    CanonicalJobActivity,
    CanonicalM1LifecycleService,
    DormantReason,
    FactKnowledgeState,
    FactValidationKind,
    FactVerificationState,
    FollowUpFactCandidate,
    HANDOFF_PROJECTION_SCHEMA_VERSION,
    HandoffProjectionMismatchError,
    HandoffPublicationConflictError,
    M1BoundaryPublicationRepository,
    M1HandoffProjection,
    M1OperationEnvelope,
    M1OperationKind,
    M1OutcomeCode,
    MarkDormantOperation,
    RecordFollowUpOperation,
    WakeJobOperation,
    CreateJobOperation,
    JobFactName,
    normalize_job_intake,
)
from werkcrew_ai.migrations import discover_migrations
from werkcrew_ai.persistence import MIGRATIONS_DIRECTORY


NOW = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
MIGRATION_IDS = (
    "0001_m7_persistent_dispatch",
    "0002_sequential_migration_history",
    "0003_m1_canonical_job",
    "0004_m1_lifecycle",
    "0005_m1_boundary_publication",
    "0006_m2_durable_inbox",
    "0007_m3_current_plan_bootstrap",
    "0008_m3_evaluation_input",
    "0009_m3_feasibility_support",
    "0010_m5_internal_labor_cost_support",
    "0011_auth0_trusted_principals",
    "0012_m3e0_authoritative_policy_evidence",
)


@pytest.fixture
def boundary(
    tmp_path: Path,
) -> tuple[CanonicalM1LifecycleService, M1BoundaryPublicationRepository]:
    database_path = tmp_path / "boundary.db"
    lifecycle = CanonicalM1LifecycleService(database_path)
    assert lifecycle.initialize(now=NOW) == MIGRATION_IDS
    return lifecycle, M1BoundaryPublicationRepository(database_path)


def _envelope(
    kind: M1OperationKind,
    event_id: str,
    *,
    minutes: int = 0,
) -> M1OperationEnvelope:
    timestamp = NOW + timedelta(minutes=minutes)
    return M1OperationEnvelope(
        operation_kind=kind,
        source_namespace="boundary-tests",
        ingress_event_id=event_id,
        actor_id="test-actor",
        actor_source="PYTEST",
        occurred_at=timestamp,
        received_at=timestamp,
    )


def _create(
    lifecycle: CanonicalM1LifecycleService,
    job_id: str,
    event_id: str,
    *,
    scope: str = "Initial scope",
) -> None:
    result = lifecycle.execute(
        CreateJobOperation(
            envelope=_envelope(M1OperationKind.CREATE_JOB, event_id),
            job_id=job_id,
            intake=normalize_job_intake(
                intake_source="CLIENT_EMAIL",
                raw_text="Original client words",
                raw_payload='{"source":"email"}',
                scope=scope,
            ),
        )
    )
    assert result.outcome is M1OutcomeCode.JOB_CREATED


def _fact(
    name: JobFactName,
    value: str,
    *,
    source: str = "OWNER",
) -> CanonicalFactInput:
    return CanonicalFactInput(
        name=name,
        value=value,
        knowledge_state=FactKnowledgeState.KNOWN,
        verification_state=FactVerificationState.VERIFIED,
        provenance_source=source,
    )


def _mark_dormant(
    lifecycle: CanonicalM1LifecycleService,
    job_id: str,
    event_id: str,
    *,
    minutes: int,
):
    return lifecycle.execute(
        MarkDormantOperation(
            envelope=_envelope(
                M1OperationKind.MARK_DORMANT,
                event_id,
                minutes=minutes,
            ),
            job_id=job_id,
            reason=DormantReason.OWNER_PAUSED,
        )
    )


def _canonical_projection_content(projection: M1HandoffProjection) -> str:
    return json.dumps(
        {
            "activity_state": projection.activity_state.value,
            "facts": [
                {
                    "evidence_id": fact.evidence_id,
                    "knowledge_state": fact.knowledge_state.value,
                    "name": fact.name.value,
                    "provenance_source": fact.provenance_source,
                    "revision": fact.revision,
                    "value": fact.value,
                    "verification_state": fact.verification_state.value,
                }
                for fact in projection.facts
            ],
            "job_id": projection.job_id,
            "lifecycle_state": projection.lifecycle_state.value,
            "schema_version": projection.schema_version,
            "source_revision": projection.source_revision,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _direct_insert_publication(
    publisher: M1BoundaryPublicationRepository,
    projection: M1HandoffProjection,
    *,
    handoff_id: str,
    insert_form: str = "INSERT INTO",
) -> None:
    if insert_form not in {"INSERT INTO", "INSERT OR REPLACE INTO", "REPLACE INTO"}:
        raise ValueError("unsupported test insert form")
    content = _canonical_projection_content(projection)
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with publisher.transaction() as connection:
        connection.execute(
            f"""
            {insert_form} m1_handoff_publications(
                job_id, source_revision, handoff_id,
                projection_schema_version, content_sha256,
                canonical_content_json, published_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                projection.job_id,
                projection.source_revision,
                handoff_id,
                projection.schema_version,
                content_sha256,
                content,
                NOW.isoformat(),
            ),
        )


def test_first_publication_contains_current_canonical_m1_state(
    boundary,
) -> None:
    lifecycle, publisher = boundary
    _create(lifecycle, "job-first", "create-first")

    projection = publisher.current_projection("job-first")
    publication = publisher.publish_handoff(projection, published_at=NOW)

    assert projection.schema_version == HANDOFF_PROJECTION_SCHEMA_VERSION
    assert projection.source_revision == 1
    assert projection.activity_state is CanonicalJobActivity.ACTIVE
    assert [(fact.name, fact.value) for fact in projection.facts] == [
        (JobFactName.SCOPE, "Initial scope")
    ]
    assert publication.handoff_id.startswith("m1h-")
    assert publisher.get_handoff("job-first", 1) == publication


def test_no_revision_bump_without_canonical_change(boundary) -> None:
    lifecycle, _publisher = boundary
    job_id = "job-no-change"
    _create(lifecycle, job_id, "create-no-change")

    binding = BindConversationOperation(
        envelope=_envelope(M1OperationKind.BIND_CONVERSATION, "bind", minutes=1),
        job_id=job_id,
        conversation_id="conversation-1",
    )
    lifecycle.execute(binding)
    evidence_only = RecordFollowUpOperation(
        envelope=_envelope(M1OperationKind.RECORD_FOLLOW_UP, "evidence", minutes=2),
        explicit_job_id=job_id,
        raw_text="Thanks, received.",
    )
    first = lifecycle.execute(evidence_only)
    retry = lifecycle.execute(evidence_only)
    reaffirmed = lifecycle.execute(
        RecordFollowUpOperation(
            envelope=_envelope(
                M1OperationKind.RECORD_FOLLOW_UP,
                "reaffirmed-fact",
                minutes=3,
            ),
            explicit_job_id=job_id,
            raw_text="The scope is unchanged.",
            fact_candidates=(
                FollowUpFactCandidate(
                    fact=CanonicalFactInput(
                        name=JobFactName.SCOPE,
                        value="Initial scope",
                        knowledge_state=FactKnowledgeState.KNOWN,
                        verification_state=FactVerificationState.UNVERIFIED,
                        provenance_source="CLIENT_EMAIL",
                    ),
                    expected_current_revision=1,
                    validation_kind=FactValidationKind.DETERMINISTIC,
                    validated_by="same-value-validator",
                ),
            ),
        )
    )

    assert first == retry
    assert reaffirmed.fact_revisions == ()
    assert lifecycle.get_job(job_id).source_revision == 1


def test_canonical_fact_change_bumps_revision_and_identical_fact_is_noop(
    boundary,
) -> None:
    lifecycle, publisher = boundary
    job_id = "job-fact-change"
    _create(lifecycle, job_id, "create-fact-change")
    changed = _fact(JobFactName.SCOPE, "Owner verified scope")

    revision_two = publisher.record_fact(
        job_id=job_id,
        fact=changed,
        recorded_at=NOW + timedelta(minutes=1),
        expected_revision=1,
    )
    unchanged = publisher.record_fact(
        job_id=job_id,
        fact=changed,
        recorded_at=NOW + timedelta(minutes=2),
        expected_revision=2,
    )

    assert revision_two.revision == 2
    assert unchanged == revision_two
    assert publisher.get_job(job_id).source_revision == 2


def test_one_fact_batch_produces_one_source_revision(boundary) -> None:
    lifecycle, _publisher = boundary
    job_id = "job-fact-batch"
    _create(lifecycle, job_id, "create-fact-batch")
    result = lifecycle.execute(
        RecordFollowUpOperation(
            envelope=_envelope(
                M1OperationKind.RECORD_FOLLOW_UP,
                "fact-batch",
                minutes=1,
            ),
            explicit_job_id=job_id,
            raw_text="Two verified corrections",
            fact_candidates=(
                FollowUpFactCandidate(
                    fact=_fact(JobFactName.SCOPE, "Corrected scope"),
                    expected_current_revision=1,
                    validation_kind=FactValidationKind.HUMAN,
                    validated_by="owner",
                ),
                FollowUpFactCandidate(
                    fact=_fact(JobFactName.PHONE, "+49 30 123456"),
                    expected_current_revision=0,
                    validation_kind=FactValidationKind.HUMAN,
                    validated_by="owner",
                ),
            ),
        )
    )

    assert len(result.fact_revisions) == 2
    assert lifecycle.get_job(job_id).source_revision == 2


def test_active_dormant_transitions_bump_only_on_real_transition(
    boundary,
) -> None:
    lifecycle, _publisher = boundary
    job_id = "job-activity"
    _create(lifecycle, job_id, "create-activity")

    assert _mark_dormant(
        lifecycle, job_id, "dormant-1", minutes=1
    ).outcome is M1OutcomeCode.JOB_MARKED_DORMANT
    assert lifecycle.get_job(job_id).source_revision == 2
    assert _mark_dormant(
        lifecycle, job_id, "dormant-2", minutes=2
    ).outcome is M1OutcomeCode.ALREADY_DORMANT
    assert lifecycle.get_job(job_id).source_revision == 2

    wake = WakeJobOperation(
        envelope=_envelope(M1OperationKind.WAKE_JOB, "wake-1", minutes=3),
        job_id=job_id,
    )
    assert lifecycle.execute(wake).outcome is M1OutcomeCode.JOB_WOKEN
    assert lifecycle.get_job(job_id).source_revision == 3
    second_wake = WakeJobOperation(
        envelope=_envelope(M1OperationKind.WAKE_JOB, "wake-2", minutes=4),
        job_id=job_id,
    )
    assert lifecycle.execute(second_wake).outcome is M1OutcomeCode.ALREADY_ACTIVE
    assert lifecycle.get_job(job_id).source_revision == 3


def test_storage_rejects_non_sequential_source_revision(boundary) -> None:
    lifecycle, publisher = boundary
    job_id = "job-revision-storage-guard"
    _create(lifecycle, job_id, "create-revision-storage-guard")

    with sqlite3.connect(publisher.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="advance by one"):
            connection.execute(
                """
                UPDATE canonical_jobs
                SET source_revision = source_revision + 2
                WHERE job_id = ?
                """,
                (job_id,),
            )

    assert publisher.get_job(job_id).source_revision == 1


def test_waking_follow_up_bumps_once_with_changes_or_stale_fact_batch(
    boundary,
) -> None:
    lifecycle, _publisher = boundary
    job_id = "job-waking-follow-up"
    _create(lifecycle, job_id, "create-waking")
    _mark_dormant(lifecycle, job_id, "dormant-waking-1", minutes=1)

    changed = lifecycle.execute(
        RecordFollowUpOperation(
            envelope=_envelope(
                M1OperationKind.RECORD_FOLLOW_UP,
                "wake-with-fact",
                minutes=2,
            ),
            explicit_job_id=job_id,
            fact_candidates=(
                FollowUpFactCandidate(
                    fact=_fact(JobFactName.ADDRESS, "Verified address"),
                    expected_current_revision=0,
                    validation_kind=FactValidationKind.HUMAN,
                    validated_by="owner",
                ),
            ),
        )
    )
    assert changed.outcome is M1OutcomeCode.FOLLOW_UP_RECORDED
    assert lifecycle.get_job(job_id).source_revision == 3

    _mark_dormant(lifecycle, job_id, "dormant-waking-2", minutes=3)
    stale = lifecycle.execute(
        RecordFollowUpOperation(
            envelope=_envelope(
                M1OperationKind.RECORD_FOLLOW_UP,
                "wake-with-stale-fact",
                minutes=4,
            ),
            explicit_job_id=job_id,
            fact_candidates=(
                FollowUpFactCandidate(
                    fact=_fact(JobFactName.SCOPE, "Stale correction"),
                    expected_current_revision=0,
                    validation_kind=FactValidationKind.HUMAN,
                    validated_by="owner",
                ),
            ),
        )
    )
    assert stale.outcome is M1OutcomeCode.FACT_REVISION_CONFLICT
    assert lifecycle.get_job(job_id).source_revision == 5


def test_same_revision_retry_returns_original_publication(boundary) -> None:
    lifecycle, publisher = boundary
    _create(lifecycle, "job-retry", "create-retry")
    projection = publisher.current_projection("job-retry")

    first = publisher.publish_handoff(projection, published_at=NOW)
    retry = publisher.publish_handoff(
        projection,
        published_at=NOW + timedelta(days=1),
    )

    assert retry == first
    assert publisher.handoffs_for_job("job-retry") == (first,)


def test_conflicting_same_revision_publication_fails_closed(boundary) -> None:
    lifecycle, publisher = boundary
    _create(lifecycle, "job-conflict", "create-conflict")
    projection = publisher.current_projection("job-conflict")
    original = publisher.publish_handoff(projection, published_at=NOW)
    conflicting = replace(
        projection,
        activity_state=CanonicalJobActivity.DORMANT,
    )

    with pytest.raises(HandoffPublicationConflictError):
        publisher.publish_handoff(
            conflicting,
            published_at=NOW + timedelta(minutes=1),
        )

    assert publisher.handoffs_for_job("job-conflict") == (original,)


def test_unpublished_stale_projection_fails_closed(boundary) -> None:
    lifecycle, publisher = boundary
    job_id = "job-stale-projection"
    _create(lifecycle, job_id, "create-stale-projection")
    stale = publisher.current_projection(job_id)
    publisher.record_fact(
        job_id=job_id,
        fact=_fact(JobFactName.SCOPE, "New canonical scope"),
        recorded_at=NOW + timedelta(minutes=1),
        expected_revision=1,
    )

    with pytest.raises(HandoffProjectionMismatchError):
        publisher.publish_handoff(stale, published_at=NOW + timedelta(minutes=2))

    assert publisher.handoffs_for_job(job_id) == ()


def test_published_handoff_cannot_be_updated_or_deleted(boundary) -> None:
    lifecycle, publisher = boundary
    job_id = "job-storage-immutable"
    _create(lifecycle, job_id, "create-storage-immutable")
    original = publisher.publish_handoff(
        publisher.current_projection(job_id),
        published_at=NOW,
    )

    with sqlite3.connect(publisher.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                UPDATE m1_handoff_publications
                SET canonical_content_json = '{}'
                WHERE job_id = ? AND source_revision = 1
                """,
                (job_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                """
                DELETE FROM m1_handoff_publications
                WHERE job_id = ? AND source_revision = 1
                """,
                (job_id,),
            )

    assert publisher.get_handoff(job_id, 1) == original


@pytest.mark.parametrize(
    "insert_form",
    ("INSERT OR REPLACE INTO", "REPLACE INTO"),
)
def test_replace_forms_cannot_overwrite_existing_publication(
    boundary,
    insert_form: str,
) -> None:
    lifecycle, publisher = boundary
    job_id = f"job-no-replace-{insert_form.split()[0].lower()}"
    _create(lifecycle, job_id, f"create-{job_id}")
    projection = publisher.current_projection(job_id)
    original = publisher.publish_handoff(projection, published_at=NOW)

    with pytest.raises(sqlite3.IntegrityError, match="cannot be replaced"):
        _direct_insert_publication(
            publisher,
            projection,
            handoff_id="m1h-forged-replacement",
            insert_form=insert_form,
        )

    assert publisher.get_handoff(job_id, 1) == original


def test_direct_duplicate_publication_and_reused_handoff_id_are_rejected(
    boundary,
) -> None:
    lifecycle, publisher = boundary
    _create(lifecycle, "job-duplicate-a", "create-duplicate-a")
    _create(lifecycle, "job-duplicate-b", "create-duplicate-b")
    projection_a = publisher.current_projection("job-duplicate-a")
    projection_b = publisher.current_projection("job-duplicate-b")
    _direct_insert_publication(
        publisher,
        projection_a,
        handoff_id="m1h-reused",
    )

    with pytest.raises(sqlite3.IntegrityError, match="cannot be replaced"):
        _direct_insert_publication(
            publisher,
            projection_a,
            handoff_id="m1h-second-for-same-revision",
        )
    with pytest.raises(sqlite3.IntegrityError, match="cannot be replaced"):
        _direct_insert_publication(
            publisher,
            projection_b,
            handoff_id="m1h-reused",
            insert_form="INSERT OR REPLACE INTO",
        )

    assert publisher.get_handoff("job-duplicate-a", 1).handoff_id == "m1h-reused"
    assert publisher.handoffs_for_job("job-duplicate-b") == ()


def test_direct_valid_current_publication_is_accepted(boundary) -> None:
    lifecycle, publisher = boundary
    job_id = "job-direct-valid"
    _create(lifecycle, job_id, "create-direct-valid")
    projection = publisher.current_projection(job_id)

    _direct_insert_publication(
        publisher,
        projection,
        handoff_id="m1h-direct-valid",
    )

    assert publisher.get_handoff(job_id, 1).projection == projection


def test_direct_future_and_nonexistent_revisions_are_rejected(boundary) -> None:
    lifecycle, publisher = boundary
    job_id = "job-direct-future"
    _create(lifecycle, job_id, "create-direct-future")
    current = publisher.current_projection(job_id)

    for revision in (2, 999):
        with pytest.raises(sqlite3.IntegrityError, match="is not current"):
            _direct_insert_publication(
                publisher,
                replace(current, source_revision=revision),
                handoff_id=f"m1h-future-{revision}",
            )

    assert publisher.handoffs_for_job(job_id) == ()


def test_direct_publication_for_nonexistent_job_is_rejected(boundary) -> None:
    lifecycle, publisher = boundary
    _create(lifecycle, "job-existing-source", "create-existing-source")
    forged = replace(
        publisher.current_projection("job-existing-source"),
        job_id="job-does-not-exist",
    )

    with pytest.raises(sqlite3.IntegrityError, match="does not exist"):
        _direct_insert_publication(
            publisher,
            forged,
            handoff_id="m1h-nonexistent-job",
        )

    assert publisher.handoffs_for_job("job-existing-source") == ()


def test_direct_stale_revision_is_rejected(boundary) -> None:
    lifecycle, publisher = boundary
    job_id = "job-direct-stale"
    _create(lifecycle, job_id, "create-direct-stale")
    stale = publisher.current_projection(job_id)
    publisher.record_fact(
        job_id=job_id,
        fact=_fact(JobFactName.SCOPE, "Current scope"),
        recorded_at=NOW + timedelta(minutes=1),
        expected_revision=1,
    )

    with pytest.raises(sqlite3.IntegrityError, match="is not current"):
        _direct_insert_publication(
            publisher,
            stale,
            handoff_id="m1h-stale",
        )

    assert publisher.handoffs_for_job(job_id) == ()


def test_direct_activity_mismatch_is_rejected(boundary) -> None:
    lifecycle, publisher = boundary
    job_id = "job-direct-activity-mismatch"
    _create(lifecycle, job_id, "create-direct-activity-mismatch")
    current = publisher.current_projection(job_id)
    forged = replace(current, activity_state=CanonicalJobActivity.DORMANT)

    with pytest.raises(sqlite3.IntegrityError, match="canonical job"):
        _direct_insert_publication(
            publisher,
            forged,
            handoff_id="m1h-activity-mismatch",
        )

    assert publisher.handoffs_for_job(job_id) == ()


def test_direct_forged_fact_projection_with_valid_hash_is_rejected(boundary) -> None:
    lifecycle, publisher = boundary
    job_id = "job-direct-forged-fact"
    _create(lifecycle, job_id, "create-direct-forged-fact")
    current = publisher.current_projection(job_id)
    forged = replace(
        current,
        facts=(replace(current.facts[0], value="Forged scope"),),
    )

    with pytest.raises(sqlite3.IntegrityError, match="canonical facts"):
        _direct_insert_publication(
            publisher,
            forged,
            handoff_id="m1h-forged-fact",
        )

    assert publisher.handoffs_for_job(job_id) == ()


def test_historical_handoff_remains_readable_after_new_revision(boundary) -> None:
    lifecycle, publisher = boundary
    job_id = "job-history"
    _create(lifecycle, job_id, "create-history", scope="Version one")
    first = publisher.publish_handoff(
        publisher.current_projection(job_id),
        published_at=NOW,
    )

    publisher.record_fact(
        job_id=job_id,
        fact=_fact(JobFactName.SCOPE, "Version two"),
        recorded_at=NOW + timedelta(minutes=1),
        expected_revision=1,
    )
    second = publisher.publish_handoff(
        publisher.current_projection(job_id),
        published_at=NOW + timedelta(minutes=2),
    )

    assert [item.source_revision for item in publisher.handoffs_for_job(job_id)] == [
        1,
        2,
    ]
    assert publisher.get_handoff(job_id, 1) == first
    assert first.projection.facts[0].value == "Version one"
    assert second.projection.facts[0].value == "Version two"


def test_restart_recovers_revision_and_exact_publication(boundary) -> None:
    lifecycle, publisher = boundary
    job_id = "job-restart"
    _create(lifecycle, job_id, "create-restart")
    published = publisher.publish_handoff(
        publisher.current_projection(job_id),
        published_at=NOW,
    )

    reopened = M1BoundaryPublicationRepository(publisher.database_path)

    assert reopened.get_job(job_id).source_revision == 1
    assert reopened.get_handoff(job_id, 1) == published
    assert reopened.publish_handoff(
        reopened.current_projection(job_id),
        published_at=NOW + timedelta(hours=1),
    ) == published


def test_concurrent_identical_publications_create_one_handoff(boundary) -> None:
    lifecycle, publisher = boundary
    job_id = "job-concurrent-publication"
    _create(lifecycle, job_id, "create-concurrent-publication")
    projection = publisher.current_projection(job_id)
    barrier = Barrier(2)

    def publish():
        barrier.wait()
        return publisher.publish_handoff(projection, published_at=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = tuple(pool.map(lambda _index: publish(), range(2)))

    assert first == second
    assert publisher.handoffs_for_job(job_id) == (first,)


def test_competing_publication_content_can_never_create_two_handoffs(
    boundary,
) -> None:
    lifecycle, publisher = boundary
    job_id = "job-competing-content"
    _create(lifecycle, job_id, "create-competing-content")
    valid = publisher.current_projection(job_id)
    invalid = replace(valid, activity_state=CanonicalJobActivity.DORMANT)
    barrier = Barrier(2)

    def attempt(projection):
        barrier.wait()
        try:
            return publisher.publish_handoff(projection, published_at=NOW)
        except (
            HandoffProjectionMismatchError,
            HandoffPublicationConflictError,
        ) as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(attempt, (valid, invalid)))

    publications = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(publications) == 1
    assert len(failures) == 1
    assert publisher.handoffs_for_job(job_id) == (publications[0],)


def test_concurrent_update_and_publication_is_serialized_fail_closed(
    boundary,
) -> None:
    lifecycle, publisher = boundary
    job_id = "job-update-publish-race"
    _create(lifecycle, job_id, "create-update-publish-race")
    revision_one = publisher.current_projection(job_id)
    barrier = Barrier(2)

    def publish():
        barrier.wait()
        try:
            return publisher.publish_handoff(revision_one, published_at=NOW)
        except HandoffProjectionMismatchError as error:
            return error

    def update():
        barrier.wait()
        return _mark_dormant(
            lifecycle,
            job_id,
            "race-dormant",
            minutes=1,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        published_result = pool.submit(publish)
        updated_result = pool.submit(update)
        publication = published_result.result()
        transition = updated_result.result()

    assert transition.outcome is M1OutcomeCode.JOB_MARKED_DORMANT
    assert lifecycle.get_job(job_id).source_revision == 2
    handoffs = publisher.handoffs_for_job(job_id)
    assert len(handoffs) <= 1
    if handoffs:
        assert publication == handoffs[0]
        assert handoffs[0].projection == revision_one
    else:
        assert isinstance(publication, HandoffProjectionMismatchError)


def test_multiple_jobs_have_independent_revision_sequences(boundary) -> None:
    lifecycle, publisher = boundary
    _create(lifecycle, "job-a", "create-job-a")
    _create(lifecycle, "job-b", "create-job-b")

    publisher.record_fact(
        job_id="job-a",
        fact=_fact(JobFactName.SCOPE, "A changed"),
        recorded_at=NOW + timedelta(minutes=1),
        expected_revision=1,
    )
    _mark_dormant(lifecycle, "job-a", "dormant-job-a", minutes=2)

    assert publisher.get_job("job-a").source_revision == 3
    assert publisher.get_job("job-b").source_revision == 1


def test_upgrade_from_exact_0004_preserves_current_multi_job_state_as_revision_one(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "upgrade-0004.db"
    migrations = discover_migrations(MIGRATIONS_DIRECTORY)
    with sqlite3.connect(database_path) as connection:
        connection.executescript(migrations[0].sql)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(1, ?)",
            (NOW.isoformat(),),
        )
        for migration in migrations[1:4]:
            connection.executescript(migration.sql)
            connection.execute(
                """
                INSERT INTO schema_migrations(
                    migration_id, version, name, checksum_sha256, applied_at
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (
                    migration.migration_id,
                    migration.version,
                    migration.name,
                    migration.checksum_sha256,
                    NOW.isoformat(),
                ),
            )
        for job_id, activity_state in (
            ("job-upgrade-active", "ACTIVE"),
            ("job-upgrade-dormant", "DORMANT"),
            ("job-upgrade-without-facts", "ACTIVE"),
        ):
            connection.execute(
                """
                INSERT INTO canonical_jobs(
                    job_id, lifecycle_state, activity_state, created_at, updated_at
                ) VALUES(?, 'RECEIVED', ?, ?, ?)
                """,
                (job_id, activity_state, NOW.isoformat(), NOW.isoformat()),
            )
            connection.execute(
                """
                INSERT INTO canonical_job_intake(
                    job_id, intake_source, raw_text, raw_payload, received_at
                ) VALUES(?, 'EMAIL', ?, NULL, ?)
                """,
                (job_id, f"existing {job_id}", NOW.isoformat()),
            )
        connection.execute(
            """
            INSERT INTO m1_operations(
                source_namespace, ingress_event_id, operation_kind,
                fingerprint_version, fingerprint_sha256,
                canonical_request_json, received_at, completed_at,
                outcome_code, job_id, result_json
            ) VALUES(
                'upgrade-test', 'existing-follow-up', 'RECORD_FOLLOW_UP',
                'm1-operation-v1', ?, '{}', ?, ?,
                'FOLLOW_UP_RECORDED', 'job-upgrade-dormant', '{}'
            )
            """,
            ("a" * 64, NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            """
            INSERT INTO m1_follow_up_evidence(
                evidence_id, job_id, source_namespace, ingress_event_id,
                conversation_id, raw_text, raw_payload, occurred_at, received_at
            ) VALUES(
                'existing-evidence', 'job-upgrade-dormant',
                'upgrade-test', 'existing-follow-up', NULL,
                'durable existing evidence', NULL, ?, ?
            )
            """,
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            """
            INSERT INTO canonical_job_facts(
                job_id, fact_name, revision, fact_value,
                knowledge_state, verification_state,
                provenance_source, recorded_at, follow_up_evidence_id
            ) VALUES(
                'job-upgrade-active', 'SCOPE', 1, 'active current scope',
                'KNOWN', 'UNVERIFIED', 'LEGACY', ?, NULL
            )
            """,
            (NOW.isoformat(),),
        )
        connection.execute(
            """
            INSERT INTO canonical_job_facts(
                job_id, fact_name, revision, fact_value,
                knowledge_state, verification_state,
                provenance_source, recorded_at, follow_up_evidence_id
            ) VALUES(
                'job-upgrade-dormant', 'SCOPE', 1, 'old scope',
                'KNOWN', 'UNVERIFIED', 'LEGACY', ?, NULL
            )
            """,
            (NOW.isoformat(),),
        )
        connection.execute(
            """
            INSERT INTO canonical_job_facts(
                job_id, fact_name, revision, fact_value,
                knowledge_state, verification_state,
                provenance_source, recorded_at, follow_up_evidence_id
            ) VALUES(
                'job-upgrade-dormant', 'SCOPE', 2, 'dormant current scope',
                'KNOWN', 'VERIFIED', 'FOLLOW_UP', ?, 'existing-evidence'
            )
            """,
            (NOW.isoformat(),),
        )

    publisher = M1BoundaryPublicationRepository(database_path)
    assert publisher.initialize(now=NOW) == MIGRATION_IDS[4:]
    active = publisher.current_projection("job-upgrade-active")
    dormant = publisher.current_projection("job-upgrade-dormant")
    without_facts = publisher.current_projection("job-upgrade-without-facts")

    assert active.source_revision == dormant.source_revision == 1
    assert without_facts.source_revision == 1
    assert active.activity_state is CanonicalJobActivity.ACTIVE
    assert dormant.activity_state is CanonicalJobActivity.DORMANT
    assert active.facts[0].value == "active current scope"
    assert dormant.facts[0].revision == 2
    assert dormant.facts[0].value == "dormant current scope"
    assert dormant.facts[0].evidence_id == "existing-evidence"
    assert without_facts.facts == ()

    publications = tuple(
        publisher.publish_handoff(projection, published_at=NOW)
        for projection in (active, dormant, without_facts)
    )
    assert [publication.source_revision for publication in publications] == [1, 1, 1]
    assert len(publisher.fact_history("job-upgrade-dormant", JobFactName.SCOPE)) == 2
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM m1_follow_up_evidence"
        ).fetchone()[0] == 1
