from __future__ import annotations

from dataclasses import replace

import pytest

import werkcrew_ai.authority as authority
from werkcrew_ai.authority import (
    AuthorityStorageError,
    AuthorityValidationError,
    CompanyAuthorityRoot,
    PrincipalType,
    TrustedPrincipal,
    company_authority_root_semantic_json,
    restore_company_authority_root,
    restore_trusted_principal,
    serialize_company_authority_root,
    serialize_trusted_principal,
    trusted_principal_semantic_json,
)
from werkcrew_ai.field.models import WorkerIdentityRegistry
from werkcrew_ai.field.serialization import serialize_worker_registry, sha256_text


def root(**changes) -> CompanyAuthorityRoot:
    raw = serialize_worker_registry(
        WorkerIdentityRegistry(("worker-a", "worker-b"), registry_revision=7)
    )
    values = {
        "company_id": "company-a",
        "company_plan_id": "plan-a",
        "company_plan_provenance_reference": "trusted-plan-bootstrap-a",
        "owner_principal_subject_id": "idp-owner-a",
        "provisioning_reference": "deployment-manifest-a",
        "worker_registry_revision": 7,
        "worker_registry_fingerprint": sha256_text(raw),
        "canonical_worker_registry_json": raw,
    }
    values.update(changes)
    return CompanyAuthorityRoot(**values)


def principal(
    authority_root: CompanyAuthorityRoot,
    *,
    principal_type: PrincipalType = PrincipalType.OWNER,
    principal_subject_id: str = "idp-owner-a",
    worker_id: str | None = None,
) -> TrustedPrincipal:
    return TrustedPrincipal(
        authority_root_id=authority_root.authority_root_id,
        authority_root_fingerprint=authority_root.authority_root_fingerprint,
        company_id=authority_root.company_id,
        company_plan_id=authority_root.company_plan_id,
        principal_subject_id=principal_subject_id,
        principal_type=principal_type,
        worker_id=worker_id,
        worker_registry_revision=authority_root.worker_registry_revision,
        worker_registry_fingerprint=authority_root.worker_registry_fingerprint,
    )


def test_company_authority_root_is_deterministic_and_content_addressed() -> None:
    first = root()
    second = root()

    assert first == second
    assert first.authority_root_id == (
        "auth0-company-authority-root-" + first.authority_root_fingerprint
    )
    assert serialize_company_authority_root(first) == serialize_company_authority_root(
        second
    )
    assert restore_company_authority_root(
        company_authority_root_semantic_json(first),
        first.authority_root_fingerprint,
        first.authority_root_id,
    ) == first


def test_company_plan_or_deployment_provenance_changes_root_identity() -> None:
    original = root()

    assert root(company_plan_id="plan-b") != original
    assert root(company_plan_id="plan-b").authority_root_id != original.authority_root_id
    assert root(provisioning_reference="deployment-manifest-b") != original


def test_trusted_principal_is_deterministic_and_content_addressed() -> None:
    authority_root = root()
    first = principal(authority_root)
    second = principal(authority_root)

    assert first == second
    assert first.principal_id == "auth0-trusted-principal-" + first.principal_fingerprint
    assert serialize_trusted_principal(first) == serialize_trusted_principal(second)
    assert restore_trusted_principal(
        trusted_principal_semantic_json(first),
        first.principal_fingerprint,
        first.principal_id,
    ) == first


def test_owner_and_worker_shapes_are_not_interchangeable() -> None:
    authority_root = root()

    with pytest.raises(AuthorityValidationError):
        principal(authority_root, worker_id="worker-a")
    with pytest.raises(AuthorityValidationError):
        principal(
            authority_root,
            principal_type=PrincipalType.WORKER,
            principal_subject_id="idp-worker-a",
            worker_id=None,
        )


def test_refingerprinted_semantic_substitution_does_not_match_claimed_identity() -> None:
    value = root()
    substituted = replace(value, company_id="company-b")

    with pytest.raises(AuthorityStorageError):
        restore_company_authority_root(
            company_authority_root_semantic_json(substituted),
            substituted.authority_root_fingerprint,
            value.authority_root_id,
        )


def test_worker_registry_document_and_fingerprint_are_exact() -> None:
    value = root()
    other_raw = serialize_worker_registry(
        WorkerIdentityRegistry(("worker-a",), registry_revision=7)
    )

    with pytest.raises(AuthorityValidationError):
        replace(value, canonical_worker_registry_json=other_raw)
    with pytest.raises(AuthorityValidationError):
        replace(value, worker_registry_fingerprint=sha256_text(other_raw))


def test_authority_module_has_no_decision_or_evidence_outcomes() -> None:
    forbidden = {
        "ACT",
        "ASK",
        "BLOCK",
        "OwnerApprovalEvidence",
        "WorkerConsentEvidence",
        "CompanyPolicyProfile",
    }

    assert forbidden.isdisjoint(authority.__all__)
    assert all(not hasattr(authority, name) for name in forbidden)
