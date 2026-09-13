from __future__ import annotations

import inspect
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import werkcrew_ai.authority.policy_evidence as policy_module
from werkcrew_ai.authority import (
    AuthorityEvidenceScope,
    CompanyAuthorityRoot,
    CompanyPolicyProfile,
    EvidenceScopeKind,
    EvidenceSubjectType,
    HumanActionCaptureRoot,
    HumanActionKind,
    MAX_ADDITIONAL_INTERNAL_LABOR_COST_EUR,
    OwnerApprovalEvidence,
    PolicyEvidenceStorageError,
    PolicyEvidenceValidationError,
    PolicyIssuanceEvidence,
    RecordedConsentStatus,
    TrustedPrincipal,
    WorkerConsentEvidence,
    authority_evidence_scope_semantic_json,
    company_policy_payload_v1,
    company_policy_profile_semantic_json,
    human_action_capture_root_semantic_json,
    owner_approval_evidence_semantic_json,
    policy_issuance_evidence_semantic_json,
    restore_authority_evidence_scope,
    restore_company_policy_profile,
    restore_human_action_capture_root,
    restore_owner_approval_evidence,
    restore_policy_issuance_evidence,
    restore_worker_consent_evidence,
    serialize_policy_evidence,
    worker_consent_evidence_semantic_json,
)
from werkcrew_ai.authority.models import PrincipalType
from werkcrew_ai.field.models import WorkerIdentityRegistry
from werkcrew_ai.field.serialization import canonical_json, serialize_worker_registry, sha256_text


SIGNING_KEY = bytes(range(32))
ACTION_TIME = datetime(2026, 9, 12, 7, 30, tzinfo=timezone.utc)


def root(*, suffix: str = "a") -> CompanyAuthorityRoot:
    raw_registry = serialize_worker_registry(
        WorkerIdentityRegistry(("worker-a", "worker-b"), registry_revision=3)
    )
    return CompanyAuthorityRoot(
        company_id=f"company-{suffix}",
        company_plan_id=f"company-plan-{suffix}",
        company_plan_provenance_reference=f"company-plan-provenance-{suffix}",
        owner_principal_subject_id=f"idp-owner-{suffix}",
        provisioning_reference=f"deployment-manifest-{suffix}",
        worker_registry_revision=3,
        worker_registry_fingerprint=sha256_text(raw_registry),
        canonical_worker_registry_json=raw_registry,
    )


def principal(
    authority_root: CompanyAuthorityRoot,
    *,
    principal_type: PrincipalType,
    worker_id: str | None = None,
) -> TrustedPrincipal:
    return TrustedPrincipal(
        authority_root_id=authority_root.authority_root_id,
        authority_root_fingerprint=authority_root.authority_root_fingerprint,
        company_id=authority_root.company_id,
        company_plan_id=authority_root.company_plan_id,
        principal_subject_id=(
            authority_root.owner_principal_subject_id
            if principal_type is PrincipalType.OWNER
            else f"idp-{worker_id}"
        ),
        principal_type=principal_type,
        worker_id=worker_id,
        worker_registry_revision=authority_root.worker_registry_revision,
        worker_registry_fingerprint=authority_root.worker_registry_fingerprint,
    )


def profile(authority_root: CompanyAuthorityRoot | None = None) -> CompanyPolicyProfile:
    authority_root = authority_root or root()
    return CompanyPolicyProfile(
        company_id=authority_root.company_id,
        company_plan_id=authority_root.company_plan_id,
        company_plan_provenance_reference=(
            authority_root.company_plan_provenance_reference
        ),
        authority_root_id=authority_root.authority_root_id,
        authority_root_fingerprint=authority_root.authority_root_fingerprint,
    )


def vehicle_scope(
    *,
    worker_id: str = "worker-a",
    job_id: str = "job-a",
    operational_date: date = date(2026, 9, 12),
    subject_id: str = "private-vehicle-worker-a",
) -> AuthorityEvidenceScope:
    return AuthorityEvidenceScope(
        scope_kind=EvidenceScopeKind.PRIVATE_VEHICLE_USE,
        subject_type=EvidenceSubjectType.PRIVATE_VEHICLE,
        subject_id=subject_id,
        operational_date=operational_date,
        job_id=job_id,
        worker_id=worker_id,
    )


def human_action(
    authority_root: CompanyAuthorityRoot,
    actor: TrustedPrincipal,
    scope: AuthorityEvidenceScope,
    *,
    action_kind: HumanActionKind,
    action_value: str,
    capture_reference: str,
):
    verification_key = Ed25519PrivateKey.from_private_bytes(
        SIGNING_KEY
    ).public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    capture_root = HumanActionCaptureRoot(
        authority_root_id=authority_root.authority_root_id,
        authority_root_fingerprint=authority_root.authority_root_fingerprint,
        company_id=authority_root.company_id,
        company_plan_id=authority_root.company_plan_id,
        company_plan_provenance_reference=(
            authority_root.company_plan_provenance_reference
        ),
        verification_key_hex=verification_key.hex(),
        provisioning_reference="deployment-human-action-capture-a",
    )
    return policy_module._sign_human_action_provenance(
        signing_key=SIGNING_KEY,
        action_kind=action_kind,
        action_value=action_value,
        authority_root_id=authority_root.authority_root_id,
        authority_root_fingerprint=authority_root.authority_root_fingerprint,
        principal_id=actor.principal_id,
        principal_fingerprint=actor.principal_fingerprint,
        scope_id=scope.scope_id,
        scope_fingerprint=scope.scope_fingerprint,
        occurred_at=ACTION_TIME,
        capture_reference=capture_reference,
        lineage_sequence=1,
        previous_action_id=None,
        previous_action_fingerprint=None,
        capture_root_id=capture_root.capture_root_id,
        capture_root_fingerprint=capture_root.capture_root_fingerprint,
    )


def test_profile_is_deterministic_content_addressed_and_bound_to_root() -> None:
    authority_root = root()
    first = profile(authority_root)
    second = profile(authority_root)

    assert first == second
    assert first.profile_id == "m3e0-company-policy-profile-" + first.profile_fingerprint
    assert company_policy_profile_semantic_json(first) == (
        company_policy_profile_semantic_json(second)
    )
    assert restore_company_policy_profile(
        company_policy_profile_semantic_json(first),
        first.profile_fingerprint,
        first.profile_id,
    ) == first
    assert profile(root(suffix="b")).profile_id != first.profile_id


def test_human_action_capture_root_is_deterministic_and_auth0_bound() -> None:
    authority_root = root()
    public_key = Ed25519PrivateKey.from_private_bytes(
        SIGNING_KEY
    ).public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    first = HumanActionCaptureRoot(
        authority_root_id=authority_root.authority_root_id,
        authority_root_fingerprint=authority_root.authority_root_fingerprint,
        company_id=authority_root.company_id,
        company_plan_id=authority_root.company_plan_id,
        company_plan_provenance_reference=(
            authority_root.company_plan_provenance_reference
        ),
        verification_key_hex=public_key.hex(),
        provisioning_reference="deployment-human-action-capture-a",
    )
    second = HumanActionCaptureRoot(
        authority_root_id=authority_root.authority_root_id,
        authority_root_fingerprint=authority_root.authority_root_fingerprint,
        company_id=authority_root.company_id,
        company_plan_id=authority_root.company_plan_id,
        company_plan_provenance_reference=(
            authority_root.company_plan_provenance_reference
        ),
        verification_key_hex=public_key.hex(),
        provisioning_reference="deployment-human-action-capture-a",
    )

    assert first == second
    raw = human_action_capture_root_semantic_json(first)
    assert restore_human_action_capture_root(
        raw,
        first.capture_root_fingerprint,
        first.capture_root_id,
    ) == first
    assert replace(first, company_id="company-b").capture_root_id != first.capture_root_id


def test_exact_adr_0013_policy_values_are_frozen_without_float() -> None:
    payload = company_policy_payload_v1()

    assert payload["p_assign"]["authority"] == "CONSTRAINED_AUTO"
    window = payload["p_window_01"]
    assert window["confirmed"]["excluded_knowledge_states"] == ["ABSENT", "UNKNOWN"]
    assert window["movement_outside_window"]["m3c_verdict"] == "REJECTED"
    assert window["movement_outside_window"]["may_act"] is False
    assert window["movement_outside_window"]["reason"] == (
        "OWNER_DECISION_REQUIRED_FOR_CUSTOMER_PROMISE"
    )
    assert window["intra_window_slot_shift"]["separate_authority_gate"] is False
    assert window["intra_window_slot_shift"]["may_remain_eligible"] is True
    cost = payload["p_cost"]
    assert cost["max_additional_internal_labor_cost"] == "50.00"
    assert Decimal(cost["max_additional_internal_labor_cost"]) == Decimal("50.00")
    assert MAX_ADDITIONAL_INTERNAL_LABOR_COST_EUR == Decimal("50.00")
    assert profile().max_additional_internal_labor_cost == Decimal("50.00")
    assert cost["currency"] == "EUR"
    assert cost["boundary"] == "INCLUSIVE"
    assert cost["m3d_completeness_required"] == "COMPLETE"
    assert cost["zero_delta_allowed"] is True
    assert cost["negative_delta_allowed"] is True
    assert payload["p_overtime"]["authority"] == "ASK_ALWAYS"
    assert payload["p_vehicle"]["authority"] == (
        "OWNER_APPROVAL_AND_WORKER_CONSENT"
    )
    assert payload["p_tag"] == {
        "approval_scope": "EXACT",
        "authority": "ASK_ALWAYS",
        "hard_block_override_allowed": False,
        "policy_relation": "SOFT_EXCEPTION",
        "tag": "OWNER_APPROVAL_REQUIRED",
        "valid_scoped_owner_approval_required": True,
    }
    assert payload["p_horizon"]["authority_horizon"] == "INCIDENT_DAY_ONLY"
    assert payload["p_search"]["exhausted_without_feasible"] == {
        "disposition": "ABSTAIN",
        "external_orchestration_may_expose_blocking_state": True,
        "global_impossibility_claim": False,
        "preserved_outcome": "SEARCH_ENVELOPE_EXHAUSTED",
        "reason": "ENVELOPE_EXHAUSTED",
    }
    assert payload["p_search"]["exhausted_with_feasible"] == {
        "feasible_precedence_preserved": True,
        "truncation_blocks_candidate": False,
        "truncation_penalizes_candidate": False,
    }
    assert not any(isinstance(value, float) for value in _walk(payload))


def _walk(value):
    if isinstance(value, dict):
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)
    else:
        yield value


def test_policy_payload_is_detached_and_cannot_mutate_profile_identity() -> None:
    value = profile()
    before = company_policy_profile_semantic_json(value)
    detached = value.policy_payload
    detached["p_cost"]["max_additional_internal_labor_cost"] = "80.00"

    assert value.policy_payload["p_cost"]["max_additional_internal_labor_cost"] == "50.00"
    assert company_policy_profile_semantic_json(value) == before


def test_refingerprinted_policy_substitution_is_rejected() -> None:
    value = profile()
    document = json.loads(company_policy_profile_semantic_json(value))
    document["policy_payload"]["p_cost"][
        "max_additional_internal_labor_cost"
    ] = "80.00"
    corrupted = canonical_json(document)
    fingerprint = sha256_text(corrupted)

    with pytest.raises(PolicyEvidenceStorageError, match="INVALID_POLICY_EVIDENCE_RECORD"):
        restore_company_policy_profile(
            corrupted,
            fingerprint,
            "m3e0-company-policy-profile-" + fingerprint,
        )


def test_scope_is_exact_content_addressed_and_non_transferable() -> None:
    first = vehicle_scope()
    same = vehicle_scope()

    assert first == same
    assert restore_authority_evidence_scope(
        authority_evidence_scope_semantic_json(first),
        first.scope_fingerprint,
        first.scope_id,
    ) == first
    assert vehicle_scope(worker_id="worker-b").scope_id != first.scope_id
    assert vehicle_scope(job_id="job-b").scope_id != first.scope_id
    assert vehicle_scope(operational_date=date(2026, 9, 13)).scope_id != first.scope_id
    assert vehicle_scope(subject_id="private-vehicle-b").scope_id != first.scope_id
    with pytest.raises(PolicyEvidenceValidationError):
        AuthorityEvidenceScope(
            scope_kind=EvidenceScopeKind.PRIVATE_VEHICLE_USE,
            subject_type=EvidenceSubjectType.PRIVATE_VEHICLE,
            subject_id="vehicle",
            operational_date=date(2026, 9, 12),
            job_id="job",
            worker_id=None,
        )


def test_human_action_provenance_is_signed_deterministic_and_not_refingerprintable() -> None:
    authority_root = root()
    owner = principal(authority_root, principal_type=PrincipalType.OWNER)
    scope = vehicle_scope()
    provenance = human_action(
        authority_root,
        owner,
        scope,
        action_kind=HumanActionKind.OWNER_APPROVAL,
        action_value="APPROVED",
        capture_reference="owner-action-signature-test",
    )
    same = human_action(
        authority_root,
        owner,
        scope,
        action_kind=HumanActionKind.OWNER_APPROVAL,
        action_value="APPROVED",
        capture_reference="owner-action-signature-test",
    )
    verification_key = Ed25519PrivateKey.from_private_bytes(SIGNING_KEY).public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    assert provenance == same
    assert policy_module.verify_human_action_provenance(
        provenance,
        verification_key,
    )
    refingerprinted = replace(
        provenance,
        capture_reference="forged-different-human-action",
    )
    assert refingerprinted.provenance_id != provenance.provenance_id
    assert not policy_module.verify_human_action_provenance(
        refingerprinted,
        verification_key,
    )


def test_issuance_approval_and_consent_are_deterministic_and_restore_exactly() -> None:
    authority_root = root()
    owner = principal(authority_root, principal_type=PrincipalType.OWNER)
    worker = principal(
        authority_root,
        principal_type=PrincipalType.WORKER,
        worker_id="worker-a",
    )
    policy = profile(authority_root)
    scope = vehicle_scope()
    issuance = PolicyIssuanceEvidence(
        profile_id=policy.profile_id,
        profile_fingerprint=policy.profile_fingerprint,
        authority_root_id=authority_root.authority_root_id,
        authority_root_fingerprint=authority_root.authority_root_fingerprint,
        owner_principal_id=owner.principal_id,
        owner_principal_fingerprint=owner.principal_fingerprint,
        company_id=authority_root.company_id,
        company_plan_id=authority_root.company_plan_id,
        company_plan_provenance_reference=(
            authority_root.company_plan_provenance_reference
        ),
    )
    approval = OwnerApprovalEvidence(
        authority_root_id=authority_root.authority_root_id,
        authority_root_fingerprint=authority_root.authority_root_fingerprint,
        owner_principal_id=owner.principal_id,
        owner_principal_fingerprint=owner.principal_fingerprint,
        company_id=authority_root.company_id,
        company_plan_id=authority_root.company_plan_id,
        company_plan_provenance_reference=(
            authority_root.company_plan_provenance_reference
        ),
        scope=scope,
        human_action_provenance=human_action(
            authority_root,
            owner,
            scope,
            action_kind=HumanActionKind.OWNER_APPROVAL,
            action_value="APPROVED",
            capture_reference="owner-action-1",
        ),
    )
    consent = WorkerConsentEvidence(
        authority_root_id=authority_root.authority_root_id,
        authority_root_fingerprint=authority_root.authority_root_fingerprint,
        worker_principal_id=worker.principal_id,
        worker_principal_fingerprint=worker.principal_fingerprint,
        company_id=authority_root.company_id,
        company_plan_id=authority_root.company_plan_id,
        company_plan_provenance_reference=(
            authority_root.company_plan_provenance_reference
        ),
        worker_id="worker-a",
        worker_registry_revision=authority_root.worker_registry_revision,
        worker_registry_fingerprint=authority_root.worker_registry_fingerprint,
        scope=scope,
        consent_status=RecordedConsentStatus.FREELY_GIVEN,
        human_action_provenance=human_action(
            authority_root,
            worker,
            scope,
            action_kind=HumanActionKind.WORKER_CONSENT,
            action_value=RecordedConsentStatus.FREELY_GIVEN.value,
            capture_reference="worker-action-1",
        ),
    )

    assert restore_policy_issuance_evidence(
        policy_issuance_evidence_semantic_json(issuance),
        issuance.issuance_fingerprint,
        issuance.issuance_id,
    ) == issuance
    assert restore_owner_approval_evidence(
        owner_approval_evidence_semantic_json(approval),
        approval.approval_fingerprint,
        approval.approval_id,
    ) == approval
    assert restore_worker_consent_evidence(
        worker_consent_evidence_semantic_json(consent),
        consent.consent_fingerprint,
        consent.consent_id,
    ) == consent
    assert consent.records_affirmative_consent is True
    for value in (
        policy,
        issuance,
        approval,
        consent,
        scope,
        approval.human_action_provenance,
        consent.human_action_provenance,
    ):
        assert serialize_policy_evidence(value) == serialize_policy_evidence(
            replace(value)
        )


@pytest.mark.parametrize(
    "status",
    [
        status
        for status in RecordedConsentStatus
        if status is not RecordedConsentStatus.FREELY_GIVEN
    ],
)
def test_only_freely_given_consent_is_usable(status: RecordedConsentStatus) -> None:
    authority_root = root()
    worker = principal(
        authority_root,
        principal_type=PrincipalType.WORKER,
        worker_id="worker-a",
    )
    scope = vehicle_scope()
    value = WorkerConsentEvidence(
        authority_root_id=authority_root.authority_root_id,
        authority_root_fingerprint=authority_root.authority_root_fingerprint,
        worker_principal_id=worker.principal_id,
        worker_principal_fingerprint=worker.principal_fingerprint,
        company_id=authority_root.company_id,
        company_plan_id=authority_root.company_plan_id,
        company_plan_provenance_reference=(
            authority_root.company_plan_provenance_reference
        ),
        worker_id="worker-a",
        worker_registry_revision=authority_root.worker_registry_revision,
        worker_registry_fingerprint=authority_root.worker_registry_fingerprint,
        scope=scope,
        consent_status=status,
        human_action_provenance=human_action(
            authority_root,
            worker,
            scope,
            action_kind=HumanActionKind.WORKER_CONSENT,
            action_value=status.value,
            capture_reference="worker-non-affirmative-action",
        ),
    )

    assert value.records_affirmative_consent is False


def test_public_constructors_and_module_expose_no_authority_flags_or_verdicts() -> None:
    profile_parameters = inspect.signature(CompanyPolicyProfile).parameters
    assert "max_additional_internal_labor_cost" not in profile_parameters
    assert "policy_payload" not in profile_parameters
    for evidence_type in (
        PolicyIssuanceEvidence,
        OwnerApprovalEvidence,
        WorkerConsentEvidence,
    ):
        parameters = inspect.signature(evidence_type).parameters
        assert "signed" not in parameters
        assert "approved" not in parameters
        assert "freely_given" not in parameters
        assert "role" not in parameters

    forbidden = {"ACT", "ASK", "BLOCK"}
    assert forbidden.isdisjoint(policy_module.__all__)
    assert all(not hasattr(policy_module, name) for name in forbidden)
