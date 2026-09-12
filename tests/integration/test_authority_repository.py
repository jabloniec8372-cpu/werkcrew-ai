from __future__ import annotations

import inspect
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from werkcrew_ai.api.m2_runtime import WorkerPrincipal
from werkcrew_ai.authority import (
    AuthorityBindingError,
    AuthorityConflictError,
    AuthorityStorageError,
    AuthorityValidationError,
    DeploymentAuthorityBootstrapRepository,
    PrincipalType,
    TrustedAuthorityRepository,
    TrustedPrincipal,
)
from werkcrew_ai.field.models import WorkerIdentityRegistry
from werkcrew_ai.field.repository import M2DurableRepository
from werkcrew_ai.field.serialization import serialize_worker_registry, sha256_text
from werkcrew_ai.persistence import SqlitePersistence
from werkcrew_ai.planning.current_plan import CompanyPlan, PlanRevision
from werkcrew_ai.planning.current_plan_repository import CurrentPlanRepository


NOW = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)


def add_company_plan(path: Path, suffix: str) -> CompanyPlan:
    company = CompanyPlan(
        company_plan_id=f"company-plan-{suffix}",
        provenance_reference=f"trusted-company-plan-bootstrap-{suffix}",
    )
    revision = PlanRevision(
        company_plan_id=company.company_plan_id,
        revision=0,
        previous_revision_id=None,
        provenance_reference=f"trusted-empty-plan-revision-{suffix}",
        plan_days=(),
        commitments=(),
        dependencies=(),
    )
    CurrentPlanRepository(path).import_revision(company, revision)
    return company


def setup(path: Path, *, initialized_at: datetime = NOW):
    bootstrap = DeploymentAuthorityBootstrapRepository(path)
    bootstrap.initialize(now=initialized_at)
    M2DurableRepository(path).create_worker_registry(
        WorkerIdentityRegistry(("worker-a", "worker-b"), registry_revision=0)
    )
    company = add_company_plan(path, "a")
    provisioned = bootstrap.provision_company_authority_from_deployment(
        company_id="company-a",
        company_plan_id=company.company_plan_id,
        owner_principal_subject_id="idp-owner-a",
        provisioning_reference="deployment-manifest-a",
    )
    return bootstrap, company, provisioned


def test_persistence_restart_round_trip_and_idempotent_replay(tmp_path: Path) -> None:
    path = tmp_path / "authority.db"
    bootstrap, company, provisioned = setup(path)
    worker = bootstrap.provision_worker_principal_from_deployment(
        authority_root_id=provisioned.authority_root.authority_root_id,
        principal_subject_id="idp-worker-a",
        worker_id="worker-a",
    )

    assert bootstrap.provision_company_authority_from_deployment(
        company_id="company-a",
        company_plan_id=company.company_plan_id,
        owner_principal_subject_id="idp-owner-a",
        provisioning_reference="deployment-manifest-a",
    ) == provisioned
    assert bootstrap.provision_worker_principal_from_deployment(
        authority_root_id=provisioned.authority_root.authority_root_id,
        principal_subject_id="idp-worker-a",
        worker_id="worker-a",
    ) == worker

    restarted = TrustedAuthorityRepository(path)
    assert restarted.get_authority_root(
        provisioned.authority_root.authority_root_id
    ) == provisioned.authority_root
    assert restarted.get_principal(
        provisioned.owner_principal.principal_id
    ) == provisioned.owner_principal
    assert restarted.resolve_worker_principal(
        provisioned.authority_root.authority_root_id,
        worker.principal_id,
        "worker-a",
    ) == worker


def test_owner_principal_is_exactly_bound_and_cross_company_substitution_fails(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cross-company.db"
    bootstrap, _, first = setup(path)
    second_plan = add_company_plan(path, "b")
    second = bootstrap.provision_company_authority_from_deployment(
        company_id="company-b",
        company_plan_id=second_plan.company_plan_id,
        owner_principal_subject_id="idp-owner-b",
        provisioning_reference="deployment-manifest-b",
    )
    reader = TrustedAuthorityRepository(path)

    assert first.owner_principal.principal_type is PrincipalType.OWNER
    with pytest.raises(
        AuthorityBindingError,
        match="CROSS_COMPANY_PRINCIPAL_SUBSTITUTION",
    ):
        reader.resolve_principal(
            second.authority_root.authority_root_id,
            first.owner_principal.principal_id,
        )


def test_unknown_worker_and_worker_substitution_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "workers.db"
    bootstrap, _, provisioned = setup(path)

    with pytest.raises(
        AuthorityBindingError,
        match="WORKER_NOT_IN_AUTHORITY_REGISTRY",
    ):
        bootstrap.provision_worker_principal_from_deployment(
            authority_root_id=provisioned.authority_root.authority_root_id,
            principal_subject_id="idp-unknown",
            worker_id="worker-unknown",
        )

    worker = bootstrap.provision_worker_principal_from_deployment(
        authority_root_id=provisioned.authority_root.authority_root_id,
        principal_subject_id="idp-worker-a",
        worker_id="worker-a",
    )
    with pytest.raises(AuthorityBindingError, match="WORKER_PRINCIPAL_SUBSTITUTION"):
        TrustedAuthorityRepository(path).resolve_worker_principal(
            provisioned.authority_root.authority_root_id,
            worker.principal_id,
            "worker-b",
        )


def test_same_subject_or_worker_cannot_be_reprovisioned_differently(
    tmp_path: Path,
) -> None:
    path = tmp_path / "principal-conflicts.db"
    bootstrap, _, provisioned = setup(path)
    bootstrap.provision_worker_principal_from_deployment(
        authority_root_id=provisioned.authority_root.authority_root_id,
        principal_subject_id="idp-worker-a",
        worker_id="worker-a",
    )

    with pytest.raises(AuthorityConflictError, match="PRINCIPAL_REPLAY_CONFLICT"):
        bootstrap.provision_worker_principal_from_deployment(
            authority_root_id=provisioned.authority_root.authority_root_id,
            principal_subject_id="idp-worker-a",
            worker_id="worker-b",
        )
    with pytest.raises(AuthorityConflictError, match="PRINCIPAL_PROVISIONING_CONFLICT"):
        bootstrap.provision_worker_principal_from_deployment(
            authority_root_id=provisioned.authority_root.authority_root_id,
            principal_subject_id="second-subject-for-worker-a",
            worker_id="worker-a",
        )


def test_corrupted_authority_root_is_rejected_on_read(tmp_path: Path) -> None:
    path = tmp_path / "corrupt-root.db"
    _, _, provisioned = setup(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER auth0_company_authority_roots_no_update")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            """
            UPDATE auth0_company_authority_roots
            SET canonical_semantic_json='{}'
            WHERE authority_root_id=?
            """,
            (provisioned.authority_root.authority_root_id,),
        )

    with pytest.raises(AuthorityStorageError, match="INVALID_AUTHORITY_RECORD"):
        TrustedAuthorityRepository(path).get_authority_root(
            provisioned.authority_root.authority_root_id
        )


def test_corrupted_principal_is_rejected_on_read(tmp_path: Path) -> None:
    path = tmp_path / "corrupt-principal.db"
    _, _, provisioned = setup(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER auth0_trusted_principals_no_update")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            """
            UPDATE auth0_trusted_principals
            SET canonical_semantic_json='{}'
            WHERE principal_id=?
            """,
            (provisioned.owner_principal.principal_id,),
        )

    with pytest.raises(AuthorityStorageError, match="INVALID_AUTHORITY_RECORD"):
        TrustedAuthorityRepository(path).get_principal(
            provisioned.owner_principal.principal_id
        )


def test_root_insert_requires_exact_current_canonical_worker_registry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry-substitution.db"
    bootstrap, _, provisioned = setup(path)
    other_plan = add_company_plan(path, "other")
    substituted_registry = serialize_worker_registry(
        WorkerIdentityRegistry(("worker-a",), registry_revision=0)
    )
    substituted_root = type(provisioned.authority_root)(
        company_id="company-other",
        company_plan_id=other_plan.company_plan_id,
        company_plan_provenance_reference=other_plan.provenance_reference,
        owner_principal_subject_id="idp-owner-other",
        provisioning_reference="deployment-manifest-other",
        worker_registry_revision=0,
        worker_registry_fingerprint=sha256_text(substituted_registry),
        canonical_worker_registry_json=substituted_registry,
    )

    with pytest.raises(sqlite3.IntegrityError, match="exact CompanyPlan and worker"):
        with SqlitePersistence(path).transaction() as connection:
            bootstrap._insert_root(connection, substituted_root)


@pytest.mark.parametrize("recursive", [0, 1])
@pytest.mark.parametrize(
    "table",
    ["auth0_company_authority_roots", "auth0_trusted_principals"],
)
def test_authority_tables_are_immutable(
    tmp_path: Path,
    recursive: int,
    table: str,
) -> None:
    path = tmp_path / f"guards-{recursive}-{table}.db"
    bootstrap, _, _ = setup(path)
    for operation in (
        f"DELETE FROM {table}",
        f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}",
        f"UPDATE {table} SET schema_version=schema_version",
    ):
        with SqlitePersistence(path).transaction() as connection:
            connection.execute(f"PRAGMA recursive_triggers={recursive}")
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(operation)


def test_normal_runtime_and_placeholder_principal_cannot_provision_owner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "no-runtime-provision.db"
    _, _, provisioned = setup(path)
    runtime = TrustedAuthorityRepository(path)
    placeholder = WorkerPrincipal("worker-a")

    assert not hasattr(runtime, "provision_company_authority_from_deployment")
    assert not hasattr(runtime, "provision_worker_principal_from_deployment")
    assert not hasattr(runtime, "transaction")
    assert not hasattr(runtime, "initialize")
    assert not isinstance(placeholder, TrustedPrincipal)
    with pytest.raises(AuthorityValidationError):
        runtime.resolve_principal(
            provisioned.authority_root.authority_root_id,
            placeholder,  # type: ignore[arg-type]
        )
    signature = inspect.signature(
        DeploymentAuthorityBootstrapRepository.provision_company_authority_from_deployment
    )
    assert "role" not in signature.parameters
    assert "principal_type" not in signature.parameters


def test_identity_is_independent_of_migration_or_process_time(tmp_path: Path) -> None:
    first_path = tmp_path / "time-a.db"
    second_path = tmp_path / "time-b.db"
    first = setup(first_path, initialized_at=NOW)[2]
    second = setup(second_path, initialized_at=NOW + timedelta(days=3650))[2]

    assert first == second
    assert first.authority_root.authority_root_id == second.authority_root.authority_root_id
    assert first.owner_principal.principal_id == second.owner_principal.principal_id
