from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from werkcrew_ai.infrastructure.demo_workflow_store import DemoWorkflowStore
from werkcrew_ai.intake import (
    CanonicalFactInput,
    CanonicalJobLifecycle,
    CanonicalJobRepository,
    FactKnowledgeState,
    FactRevisionConflictError,
    FactVerificationState,
    JobFactName,
    normalize_job_intake,
)
from werkcrew_ai.migrations import discover_migrations
from werkcrew_ai.persistence import MIGRATIONS_DIRECTORY


NOW = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
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
)


@pytest.fixture
def repository(tmp_path: Path) -> CanonicalJobRepository:
    value = CanonicalJobRepository(tmp_path / "m1.db")
    assert value.initialize(now=NOW) == MIGRATION_IDS
    return value


def _create(
    repository: CanonicalJobRepository,
    *,
    job_id: str,
    **intake_values: object,
):
    intake = normalize_job_intake(
        intake_source="CLIENT_EMAIL",
        raw_text="Original client words",
        raw_payload='{"source":"email"}',
        **intake_values,
    )
    return repository.create_job(job_id=job_id, intake=intake, created_at=NOW)


def test_create_complete_durable_job(repository: CanonicalJobRepository) -> None:
    job = _create(
        repository,
        job_id="job-m1-complete-001",
        client_reference="customer-42",
        client_name="Ada Example",
        address="Main Street 10, Berlin",
        phone="+49 30 123456",
        email="ada@example.test",
        scope="Renovate the bathroom",
        materials="Client-selected tiles",
        preferred_contact_channel="EMAIL",
        verified_facts=(JobFactName.EMAIL, JobFactName.ADDRESS),
    )

    assert job.job_id == "job-m1-complete-001"
    assert job.lifecycle_state is CanonicalJobLifecycle.RECEIVED
    assert {fact.name for fact in job.facts} == set(JobFactName)
    assert job.fact(JobFactName.EMAIL).verification_state is (
        FactVerificationState.VERIFIED
    )


def test_incomplete_intake_keeps_missing_facts_absent(
    repository: CanonicalJobRepository,
) -> None:
    job = _create(
        repository,
        job_id="job-m1-incomplete-001",
        client_name="Incomplete Client",
        address="Known address",
        scope="Rough painting scope",
    )

    assert job.fact(JobFactName.CLIENT_NAME).value == "Incomplete Client"
    assert job.fact(JobFactName.PHONE) is None
    assert job.fact(JobFactName.EMAIL) is None
    assert job.fact(JobFactName.MATERIALS) is None


def test_phone_and_scope_without_address_is_valid(
    repository: CanonicalJobRepository,
) -> None:
    job = _create(
        repository,
        job_id="job-m1-phone-001",
        phone="+49 170 1234567",
        scope="Client requests a site discussion",
    )

    assert job.fact(JobFactName.PHONE).value == "+49 170 1234567"
    assert job.fact(JobFactName.ADDRESS) is None


def test_explicitly_unknown_materials_are_durable_and_unverified(
    repository: CanonicalJobRepository,
) -> None:
    job = _create(
        repository,
        job_id="job-m1-materials-unknown-001",
        email="client@example.test",
        scope="Replace flooring",
        explicitly_unknown=(JobFactName.MATERIALS,),
    )

    materials = job.fact(JobFactName.MATERIALS)
    assert materials.value is None
    assert materials.knowledge_state is FactKnowledgeState.UNKNOWN
    assert materials.verification_state is FactVerificationState.UNVERIFIED
    assert materials.provenance_source == "CLIENT_EMAIL"


def test_raw_intake_is_preserved_exactly(
    repository: CanonicalJobRepository,
) -> None:
    raw_text = "  Client wording\nwith original spacing.  "
    raw_payload = '{ "subject": "Request", "body": "unchanged" }\n'
    intake = normalize_job_intake(
        intake_source="EMAIL_GATEWAY",
        raw_text=raw_text,
        raw_payload=raw_payload,
        scope="  Canonical scope  ",
    )

    job = repository.create_job(
        job_id="job-m1-raw-001",
        intake=intake,
        created_at=NOW,
    )

    assert job.raw_text == raw_text
    assert job.raw_payload == raw_payload
    assert job.fact(JobFactName.SCOPE).value == "Canonical scope"


def test_provenance_and_verification_are_retrievable(
    repository: CanonicalJobRepository,
) -> None:
    intake = normalize_job_intake(
        intake_source="PHONE_CALL",
        raw_text="Call notes",
        client_name="Client Reported Name",
        address="Owner-confirmed address",
        provenance_by_fact={JobFactName.ADDRESS: "OWNER"},
        verified_facts=(JobFactName.ADDRESS,),
    )
    job = repository.create_job(
        job_id="job-m1-provenance-001",
        intake=intake,
        created_at=NOW,
    )

    name = job.fact(JobFactName.CLIENT_NAME)
    address = job.fact(JobFactName.ADDRESS)
    assert name.provenance_source == "PHONE_CALL"
    assert name.verification_state is FactVerificationState.UNVERIFIED
    assert address.provenance_source == "OWNER"
    assert address.verification_state is FactVerificationState.VERIFIED


def test_job_and_stable_id_survive_new_repository_connection(
    repository: CanonicalJobRepository,
) -> None:
    created = _create(
        repository,
        job_id="job-m1-restart-001",
        client_name="Persistent Client",
        scope="Persistent scope",
    )

    reopened = CanonicalJobRepository(repository.database_path)
    loaded = reopened.get_job(created.job_id)

    assert loaded == created
    assert loaded.job_id == "job-m1-restart-001"


def test_later_fact_append_preserves_history_and_new_provenance(
    repository: CanonicalJobRepository,
) -> None:
    _create(
        repository,
        job_id="job-m1-revision-001",
        address="Client-reported address",
    )
    later = NOW + timedelta(hours=1)
    updated = repository.record_fact(
        job_id="job-m1-revision-001",
        fact=CanonicalFactInput(
            name=JobFactName.ADDRESS,
            value="Owner-verified address",
            knowledge_state=FactKnowledgeState.KNOWN,
            verification_state=FactVerificationState.VERIFIED,
            provenance_source="OWNER",
        ),
        recorded_at=later,
        expected_revision=1,
    )

    history = repository.fact_history(
        "job-m1-revision-001", JobFactName.ADDRESS
    )
    assert updated.revision == 2
    assert [item.value for item in history] == [
        "Client-reported address",
        "Owner-verified address",
    ]
    assert [item.provenance_source for item in history] == [
        "CLIENT_EMAIL",
        "OWNER",
    ]
    assert repository.get_job("job-m1-revision-001").fact(
        JobFactName.ADDRESS
    ) == updated


def test_known_fact_cannot_be_overwritten_without_revision_claim(
    repository: CanonicalJobRepository,
) -> None:
    _create(
        repository,
        job_id="job-m1-conflict-001",
        phone="first number",
    )

    with pytest.raises(FactRevisionConflictError, match="revision 1"):
        repository.record_fact(
            job_id="job-m1-conflict-001",
            fact=CanonicalFactInput(
                name=JobFactName.PHONE,
                value="different number",
                knowledge_state=FactKnowledgeState.KNOWN,
                verification_state=FactVerificationState.UNVERIFIED,
                provenance_source="WORKER",
            ),
            recorded_at=NOW + timedelta(minutes=5),
        )

    assert len(
        repository.fact_history("job-m1-conflict-001", JobFactName.PHONE)
    ) == 1


def test_new_job_persistence_does_not_invoke_demo_workflow_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_demo_store(*_args, **_kwargs):
        raise AssertionError("DemoWorkflowStore must not own canonical M1 jobs")

    monkeypatch.setattr(DemoWorkflowStore, "__init__", forbidden_demo_store)
    repository = CanonicalJobRepository(tmp_path / "sqlite-only.db")
    repository.initialize(now=NOW)

    job = _create(
        repository,
        job_id="job-m1-sqlite-only-001",
        scope="SQLite-owned scope",
    )

    assert job.job_id == "job-m1-sqlite-only-001"


def test_upgrade_from_current_0002_database_applies_later_migrations(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "upgrade-from-0002.db"
    migrations = discover_migrations(MIGRATIONS_DIRECTORY)
    first, second = migrations[:2]
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

    repository = CanonicalJobRepository(database_path)
    assert repository.initialize(now=NOW) == MIGRATION_IDS[2:]
    assert repository.applied_migration_ids() == MIGRATION_IDS


def test_repeated_initialization_after_m1_migration_is_idempotent(
    tmp_path: Path,
) -> None:
    repository = CanonicalJobRepository(tmp_path / "idempotent.db")

    assert repository.initialize(now=NOW) == MIGRATION_IDS
    assert repository.initialize(now=NOW) == ()
    assert repository.applied_migration_ids() == MIGRATION_IDS
