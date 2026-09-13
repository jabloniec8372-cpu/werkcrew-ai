from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import inspect
import json
import sqlite3
from threading import Barrier
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import werkcrew_ai.authority as authority_package
from werkcrew_ai.authority import (
    AuthorityEvidenceScope,
    CompanyPolicyProfile,
    DeploymentAuthorityBootstrapRepository,
    EvidenceScopeKind,
    EvidenceSubjectType,
    PolicyEvidenceBindingError,
    PolicyEvidenceConflictError,
    PolicyEvidenceRepository,
    PolicyEvidenceStorageError,
    PolicyIssuanceEvidence,
    PolicyIssuanceStatus,
    PrincipalType,
    RecordedConsentStatus,
    TrustedPrincipal,
)
from werkcrew_ai.authority.policy_evidence_repository import (
    DeploymentPolicyEvidenceRecorder,
)
import werkcrew_ai.authority.policy_evidence_repository as recording_module
from werkcrew_ai.field.models import WorkerIdentityRegistry
from werkcrew_ai.field.repository import M2DurableRepository
from werkcrew_ai.field.serialization import canonical_json, sha256_text
from werkcrew_ai.persistence import SqlitePersistence
from werkcrew_ai.planning.current_plan import CompanyPlan, PlanRevision
from werkcrew_ai.planning.current_plan_repository import CurrentPlanRepository


NOW = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
SIGNING_KEY = bytes(range(32))
M3E0_TABLES = (
    "m3e0_company_policy_profiles",
    "m3e0_policy_issuances",
    "m3e0_human_action_capture_roots",
    "m3e0_human_action_capture_consumptions",
    "m3e0_owner_approvals",
    "m3e0_worker_consents",
)


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
    recorder = DeploymentPolicyEvidenceRecorder(
        path,
        trusted_human_action_signing_key=SIGNING_KEY,
    )
    recorder.initialize(now=initialized_at)
    M2DurableRepository(path).create_worker_registry(
        WorkerIdentityRegistry(("worker-a", "worker-b"), registry_revision=0)
    )
    company = add_company_plan(path, "a")
    auth = DeploymentAuthorityBootstrapRepository(path)
    provisioned = auth.provision_company_authority_from_deployment(
        company_id="company-a",
        company_plan_id=company.company_plan_id,
        owner_principal_subject_id="idp-owner-a",
        provisioning_reference="deployment-manifest-a",
    )
    worker_a = auth.provision_worker_principal_from_deployment(
        authority_root_id=provisioned.authority_root.authority_root_id,
        principal_subject_id="idp-worker-a",
        worker_id="worker-a",
    )
    worker_b = auth.provision_worker_principal_from_deployment(
        authority_root_id=provisioned.authority_root.authority_root_id,
        principal_subject_id="idp-worker-b",
        worker_id="worker-b",
    )
    recorder.provision_human_action_capture_from_deployment(
        authority_root_id=provisioned.authority_root.authority_root_id,
        provisioning_reference="deployment-human-action-capture-a",
    )
    return recorder, auth, provisioned, worker_a, worker_b


def reader(path: Path):
    return PolicyEvidenceRepository(path)


def add_second_authority(
    path: Path,
    auth: DeploymentAuthorityBootstrapRepository,
    recorder: DeploymentPolicyEvidenceRecorder | None = None,
):
    company = add_company_plan(path, "b")
    provisioned = auth.provision_company_authority_from_deployment(
        company_id="company-b",
        company_plan_id=company.company_plan_id,
        owner_principal_subject_id="idp-owner-b",
        provisioning_reference="deployment-manifest-b",
    )
    if recorder is not None:
        recorder.provision_human_action_capture_from_deployment(
            authority_root_id=provisioned.authority_root.authority_root_id,
            provisioning_reference="deployment-human-action-capture-b",
        )
    return provisioned


def vehicle_scope(
    *,
    worker_id: str = "worker-a",
    job_id: str = "job-a",
    operational_date: date = date(2026, 9, 12),
    subject_id: str = "private-vehicle-a",
) -> AuthorityEvidenceScope:
    return AuthorityEvidenceScope(
        scope_kind=EvidenceScopeKind.PRIVATE_VEHICLE_USE,
        subject_type=EvidenceSubjectType.PRIVATE_VEHICLE,
        subject_id=subject_id,
        operational_date=operational_date,
        job_id=job_id,
        worker_id=worker_id,
    )


@pytest.mark.parametrize("first_action", ("owner", "worker"))
def test_global_capture_identity_rejects_cross_action_kind_reuse(
    tmp_path: Path,
    first_action: str,
) -> None:
    path = tmp_path / f"global-capture-{first_action}-first.db"
    recorder, _, provisioned, worker, _ = setup(path)
    root = provisioned.authority_root
    owner = provisioned.owner_principal
    scope = vehicle_scope()
    reference = "same-trusted-capture-event"

    def record_owner():
        return recorder.record_owner_approval(
            authority_root_id=root.authority_root_id,
            owner_principal_id=owner.principal_id,
            scope=scope,
            human_action_reference=reference,
            occurred_at=NOW,
        )

    def record_worker():
        return recorder.record_worker_consent(
            authority_root_id=root.authority_root_id,
            worker_principal_id=worker.principal_id,
            worker_id="worker-a",
            scope=scope,
            consent_status=RecordedConsentStatus.FREELY_GIVEN,
            human_action_reference=reference,
            occurred_at=NOW,
        )

    first, second = (
        (record_owner, record_worker)
        if first_action == "owner"
        else (record_worker, record_owner)
    )
    first()
    with pytest.raises(
        PolicyEvidenceConflictError,
        match="HUMAN_ACTION_CAPTURE_REPLAY_CONFLICT",
    ):
        second()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM m3e0_human_action_capture_consumptions"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM m3e0_owner_approvals"
        ).fetchone() == ((1 if first_action == "owner" else 0),)
        assert connection.execute(
            "SELECT count(*) FROM m3e0_worker_consents"
        ).fetchone() == ((1 if first_action == "worker" else 0),)


def test_exact_capture_retry_is_idempotent_for_both_action_kinds(
    tmp_path: Path,
) -> None:
    path = tmp_path / "global-capture-exact-retry.db"
    recorder, _, provisioned, worker, _ = setup(path)
    root = provisioned.authority_root
    scope = vehicle_scope()
    owner_arguments = {
        "authority_root_id": root.authority_root_id,
        "owner_principal_id": provisioned.owner_principal.principal_id,
        "scope": scope,
        "human_action_reference": "owner-exact-retry",
        "occurred_at": NOW,
    }
    worker_arguments = {
        "authority_root_id": root.authority_root_id,
        "worker_principal_id": worker.principal_id,
        "worker_id": "worker-a",
        "scope": scope,
        "consent_status": RecordedConsentStatus.FREELY_GIVEN,
        "human_action_reference": "worker-exact-retry",
        "occurred_at": NOW,
    }

    first_approval = recorder.record_owner_approval(**owner_arguments)
    assert recorder.record_owner_approval(**owner_arguments) == first_approval
    first_consent = recorder.record_worker_consent(**worker_arguments)
    assert recorder.record_worker_consent(**worker_arguments) == first_consent

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM m3e0_human_action_capture_consumptions"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM m3e0_owner_approvals"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM m3e0_worker_consents"
        ).fetchone() == (1,)


def test_same_capture_reference_under_different_key_is_a_distinct_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "global-capture-different-key.db"
    first_recorder, auth, first, _, _ = setup(path)
    second = add_second_authority(path, auth)
    second_recorder = DeploymentPolicyEvidenceRecorder(
        path,
        trusted_human_action_signing_key=bytes(range(1, 33)),
    )
    second_recorder.provision_human_action_capture_from_deployment(
        authority_root_id=second.authority_root.authority_root_id,
        provisioning_reference="deployment-human-action-capture-second-key",
    )
    reference = "same-reference-different-trusted-key"

    first_recorder.record_owner_approval(
        authority_root_id=first.authority_root.authority_root_id,
        owner_principal_id=first.owner_principal.principal_id,
        scope=vehicle_scope(),
        human_action_reference=reference,
        occurred_at=NOW,
    )
    second_recorder.record_owner_approval(
        authority_root_id=second.authority_root.authority_root_id,
        owner_principal_id=second.owner_principal.principal_id,
        scope=vehicle_scope(job_id="job-b", subject_id="vehicle-b"),
        human_action_reference=reference,
        occurred_at=NOW,
    )

    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT capture_key_id, capture_reference
            FROM m3e0_human_action_capture_consumptions
            WHERE capture_reference=?
            """,
            (reference,),
        ).fetchall()
    assert len(rows) == 2
    assert len({row[0] for row in rows}) == 2


@pytest.mark.parametrize("conflict", ("principal_and_worker", "scope"))
def test_global_capture_identity_rejects_changed_principal_worker_or_scope(
    tmp_path: Path,
    conflict: str,
) -> None:
    path = tmp_path / f"global-capture-changed-{conflict}.db"
    recorder, _, provisioned, worker_a, worker_b = setup(path)
    root = provisioned.authority_root
    reference = "worker-capture-replay-must-be-identical"
    recorder.record_worker_consent(
        authority_root_id=root.authority_root_id,
        worker_principal_id=worker_a.principal_id,
        worker_id="worker-a",
        scope=vehicle_scope(),
        consent_status=RecordedConsentStatus.FREELY_GIVEN,
        human_action_reference=reference,
        occurred_at=NOW,
    )
    if conflict == "principal_and_worker":
        principal = worker_b
        worker_id = "worker-b"
        changed_scope = vehicle_scope(
            worker_id="worker-b",
            subject_id="vehicle-worker-b",
        )
    else:
        principal = worker_a
        worker_id = "worker-a"
        changed_scope = vehicle_scope(job_id="job-b")

    with pytest.raises(
        PolicyEvidenceConflictError,
        match="HUMAN_ACTION_CAPTURE_REPLAY_CONFLICT",
    ):
        recorder.record_worker_consent(
            authority_root_id=root.authority_root_id,
            worker_principal_id=principal.principal_id,
            worker_id=worker_id,
            scope=changed_scope,
            consent_status=RecordedConsentStatus.FREELY_GIVEN,
            human_action_reference=reference,
            occurred_at=NOW,
        )


def test_global_capture_identity_rejects_cross_root_and_company_reuse(
    tmp_path: Path,
) -> None:
    path = tmp_path / "global-capture-cross-root.db"
    recorder, auth, first, _, _ = setup(path)
    second = add_second_authority(path, auth, recorder)
    reference = "same-key-reference-cross-company"
    recorder.record_owner_approval(
        authority_root_id=first.authority_root.authority_root_id,
        owner_principal_id=first.owner_principal.principal_id,
        scope=vehicle_scope(),
        human_action_reference=reference,
        occurred_at=NOW,
    )

    with pytest.raises(
        PolicyEvidenceConflictError,
        match="HUMAN_ACTION_CAPTURE_REPLAY_CONFLICT",
    ):
        recorder.record_owner_approval(
            authority_root_id=second.authority_root.authority_root_id,
            owner_principal_id=second.owner_principal.principal_id,
            scope=vehicle_scope(job_id="job-b", subject_id="vehicle-b"),
            human_action_reference=reference,
            occurred_at=NOW,
        )


def test_global_capture_identity_survives_restart_and_reopen(
    tmp_path: Path,
) -> None:
    path = tmp_path / "global-capture-restart.db"
    recorder, _, provisioned, _, _ = setup(path)
    root = provisioned.authority_root
    arguments = {
        "authority_root_id": root.authority_root_id,
        "owner_principal_id": provisioned.owner_principal.principal_id,
        "scope": vehicle_scope(),
        "human_action_reference": "capture-persists-across-restart",
        "occurred_at": NOW,
    }
    approval = recorder.record_owner_approval(**arguments)

    reopened = DeploymentPolicyEvidenceRecorder(
        path,
        trusted_human_action_signing_key=SIGNING_KEY,
    )
    assert reopened.initialize(now=NOW + timedelta(minutes=1)) == ()
    assert reopened.record_owner_approval(**arguments) == approval
    assert reader(path).get_owner_approval(approval.approval_id) == approval
    with pytest.raises(
        PolicyEvidenceConflictError,
        match="HUMAN_ACTION_CAPTURE_REPLAY_CONFLICT",
    ):
        reopened.record_owner_approval(
            **{
                **arguments,
                "scope": vehicle_scope(job_id="changed-after-restart"),
            }
        )


def test_concurrent_conflicting_capture_consumption_has_one_winner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "global-capture-concurrent.db"
    recorder, _, provisioned, worker, _ = setup(path)
    competing_recorder = DeploymentPolicyEvidenceRecorder(
        path,
        trusted_human_action_signing_key=SIGNING_KEY,
    )
    root = provisioned.authority_root
    scope = vehicle_scope()
    reference = "concurrent-single-capture"
    barrier = Barrier(2)

    def attempt(action: str) -> tuple[str, str]:
        barrier.wait()
        try:
            if action == "owner":
                recorder.record_owner_approval(
                    authority_root_id=root.authority_root_id,
                    owner_principal_id=provisioned.owner_principal.principal_id,
                    scope=scope,
                    human_action_reference=reference,
                    occurred_at=NOW,
                )
            else:
                competing_recorder.record_worker_consent(
                    authority_root_id=root.authority_root_id,
                    worker_principal_id=worker.principal_id,
                    worker_id="worker-a",
                    scope=scope,
                    consent_status=RecordedConsentStatus.FREELY_GIVEN,
                    human_action_reference=reference,
                    occurred_at=NOW,
                )
        except PolicyEvidenceConflictError as error:
            return action, error.code
        return action, "SUCCESS"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(attempt, ("owner", "worker")))

    assert sorted(result for _, result in results) == [
        "HUMAN_ACTION_CAPTURE_REPLAY_CONFLICT",
        "SUCCESS",
    ]
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM m3e0_human_action_capture_consumptions"
        ).fetchone() == (1,)
        assert connection.execute(
            """
            SELECT
                (SELECT count(*) FROM m3e0_owner_approvals)
                + (SELECT count(*) FROM m3e0_worker_consents)
            """
        ).fetchone() == (1,)


def test_refingerprinted_capture_ledger_reinterpretation_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "global-capture-ledger-tamper.db"
    recorder, _, provisioned, _, _ = setup(path)
    root = provisioned.authority_root
    scope = vehicle_scope()
    approval = recorder.record_owner_approval(
        authority_root_id=root.authority_root_id,
        owner_principal_id=provisioned.owner_principal.principal_id,
        scope=scope,
        human_action_reference="signed-owner-capture-only",
        occurred_at=NOW,
    )
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT consumption_id, canonical_semantic_json
            FROM m3e0_human_action_capture_consumptions
            """
        ).fetchone()
        document = json.loads(row[1])
        document["action_kind"] = "WORKER_CONSENT"
        document["action_value"] = "FREELY_GIVEN"
        tampered_raw = canonical_json(document)
        tampered_fingerprint = sha256_text(tampered_raw)
        tampered_id = "m3e0-human-action-consumption-" + tampered_fingerprint
        connection.execute(
            "DROP TRIGGER m3e0_human_action_capture_consumptions_no_update"
        )
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            """
            UPDATE m3e0_human_action_capture_consumptions
            SET consumption_id=?, consumption_fingerprint=?,
                action_kind='WORKER_CONSENT', action_value='FREELY_GIVEN',
                canonical_semantic_json=?
            WHERE consumption_id=?
            """,
            (tampered_id, tampered_fingerprint, tampered_raw, row[0]),
        )

    with pytest.raises(
        PolicyEvidenceStorageError,
        match="HUMAN_ACTION_CAPTURE_CONSUMPTION_MISMATCH",
    ):
        reader(path).get_owner_approval(approval.approval_id)
    with pytest.raises(
        PolicyEvidenceStorageError,
        match="HUMAN_ACTION_CAPTURE_CONSUMPTION_MISMATCH",
    ):
        recorder.record_owner_approval(
            authority_root_id=root.authority_root_id,
            owner_principal_id=provisioned.owner_principal.principal_id,
            scope=scope,
            human_action_reference="signed-owner-capture-only",
            occurred_at=NOW,
        )


def test_real_persistence_restart_round_trip_and_unsigned_resolution(
    tmp_path: Path,
) -> None:
    path = tmp_path / "policy-evidence.db"
    recorder, _, provisioned, worker, _ = setup(path)
    root = provisioned.authority_root
    owner = provisioned.owner_principal

    absent = reader(path).resolve_policy_issuance(
        root.authority_root_id
    )
    assert absent.status is PolicyIssuanceStatus.POLICY_UNSIGNED
    assert absent.profile is None
    assert absent.issuance is None

    profile = recorder.record_company_policy_profile(
        authority_root_id=root.authority_root_id
    )
    unsigned = reader(path).resolve_policy_issuance(
        root.authority_root_id
    )
    assert unsigned.status is PolicyIssuanceStatus.POLICY_UNSIGNED
    assert unsigned.profile is None
    assert unsigned.issuance is None

    issuance = recorder.issue_company_policy_profile(
        profile_id=profile.profile_id,
        owner_principal_id=owner.principal_id,
    )
    scope = vehicle_scope()
    approval = recorder.record_owner_approval(
        authority_root_id=root.authority_root_id,
        owner_principal_id=owner.principal_id,
        scope=scope,
        human_action_reference="capture-owner-approval-round-trip",
        occurred_at=NOW,
    )
    consent = recorder.record_worker_consent(
        authority_root_id=root.authority_root_id,
        worker_principal_id=worker.principal_id,
        worker_id="worker-a",
        scope=scope,
        consent_status=RecordedConsentStatus.FREELY_GIVEN,
        human_action_reference="capture-worker-consent-round-trip",
        occurred_at=NOW,
    )

    restarted = reader(path)
    resolved = restarted.resolve_policy_issuance(root.authority_root_id)
    assert resolved.status is PolicyIssuanceStatus.ISSUED
    assert resolved.profile == profile
    assert resolved.issuance == issuance
    assert restarted.get_company_policy_profile(profile.profile_id) == profile
    assert restarted.get_policy_issuance(issuance.issuance_id) == issuance
    assert restarted.get_owner_approval(approval.approval_id) == approval
    assert restarted.get_worker_consent(consent.consent_id) == consent
    assert restarted.resolve_owner_approval(
        approval.approval_id,
        authority_root_id=root.authority_root_id,
        expected_scope=scope,
    ) == approval
    assert restarted.resolve_worker_consent(
        consent.consent_id,
        authority_root_id=root.authority_root_id,
        worker_id="worker-a",
        expected_scope=scope,
    ) == consent


def test_foreign_owner_and_worker_cannot_issue_policy(tmp_path: Path) -> None:
    path = tmp_path / "foreign-issuer.db"
    recorder, auth, provisioned, worker, _ = setup(path)
    foreign = add_second_authority(path, auth)
    profile = recorder.record_company_policy_profile(
        authority_root_id=provisioned.authority_root.authority_root_id
    )

    for invalid_principal_id in (
        foreign.owner_principal.principal_id,
        worker.principal_id,
    ):
        with pytest.raises(
            PolicyEvidenceBindingError,
            match="POLICY_ISSUER_NOT_AUTHORITY_ROOT_OWNER",
        ):
            recorder.issue_company_policy_profile(
                profile_id=profile.profile_id,
                owner_principal_id=invalid_principal_id,
            )

    forged_issuance = PolicyIssuanceEvidence(
        profile_id=profile.profile_id,
        profile_fingerprint=profile.profile_fingerprint,
        authority_root_id=foreign.authority_root.authority_root_id,
        authority_root_fingerprint=(
            foreign.authority_root.authority_root_fingerprint
        ),
        owner_principal_id=foreign.owner_principal.principal_id,
        owner_principal_fingerprint=foreign.owner_principal.principal_fingerprint,
        company_id=foreign.authority_root.company_id,
        company_plan_id=foreign.authority_root.company_plan_id,
        company_plan_provenance_reference=(
            foreign.authority_root.company_plan_provenance_reference
        ),
    )
    with pytest.raises(sqlite3.IntegrityError, match="exact AUTH-0 OWNER"):
        with SqlitePersistence(path).transaction() as connection:
            recorder._insert_issuance(connection, forged_issuance)


def test_caller_constructed_or_substituted_profile_is_not_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "forged-profile.db"
    recorder, auth, provisioned, _, _ = setup(path)
    foreign = add_second_authority(path, auth)
    forged = CompanyPolicyProfile(
        company_id=foreign.authority_root.company_id,
        company_plan_id=foreign.authority_root.company_plan_id,
        company_plan_provenance_reference=(
            foreign.authority_root.company_plan_provenance_reference
        ),
        authority_root_id=provisioned.authority_root.authority_root_id,
        authority_root_fingerprint=(
            provisioned.authority_root.authority_root_fingerprint
        ),
    )

    with pytest.raises(PolicyEvidenceStorageError, match="POLICY_PROFILE_NOT_FOUND"):
        reader(path).get_company_policy_profile(forged.profile_id)
    with pytest.raises(sqlite3.IntegrityError, match="exact AUTH-0 root"):
        with SqlitePersistence(path).transaction() as connection:
            recorder._insert_profile(connection, forged)


def test_refingerprinted_policy_corruption_is_rejected_on_read(
    tmp_path: Path,
) -> None:
    path = tmp_path / "refingerprinted-policy.db"
    recorder, _, provisioned, _, _ = setup(path)
    profile = recorder.record_company_policy_profile(
        authority_root_id=provisioned.authority_root.authority_root_id
    )
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER m3e0_company_policy_profiles_no_update")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        document = json.loads(
            connection.execute(
                "SELECT canonical_semantic_json "
                "FROM m3e0_company_policy_profiles WHERE profile_id=?",
                (profile.profile_id,),
            ).fetchone()[0]
        )
        document["policy_payload"]["p_cost"][
            "max_additional_internal_labor_cost"
        ] = "80.00"
        raw = canonical_json(document)
        fingerprint = sha256_text(raw)
        corrupted_id = "m3e0-company-policy-profile-" + fingerprint
        connection.execute(
            """
            UPDATE m3e0_company_policy_profiles
            SET profile_id=?, profile_fingerprint=?, canonical_semantic_json=?
            WHERE profile_id=?
            """,
            (corrupted_id, fingerprint, raw, profile.profile_id),
        )

    with pytest.raises(
        PolicyEvidenceStorageError,
        match="INVALID_POLICY_EVIDENCE_RECORD",
    ):
        reader(path).get_company_policy_profile(corrupted_id)


def test_owner_approval_is_exact_scope_and_foreign_owner_fails(
    tmp_path: Path,
) -> None:
    path = tmp_path / "approval-scope.db"
    recorder, auth, provisioned, _, _ = setup(path)
    foreign = add_second_authority(path, auth)
    root = provisioned.authority_root
    scope = vehicle_scope()
    approval = recorder.record_owner_approval(
        authority_root_id=root.authority_root_id,
        owner_principal_id=provisioned.owner_principal.principal_id,
        scope=scope,
        human_action_reference="capture-owner-approval-scope",
        occurred_at=NOW,
    )

    for changed_scope in (
        vehicle_scope(worker_id="worker-b"),
        vehicle_scope(job_id="job-b"),
        vehicle_scope(operational_date=date(2026, 9, 13)),
        vehicle_scope(subject_id="private-vehicle-b"),
    ):
        with pytest.raises(
            PolicyEvidenceBindingError,
            match="OWNER_APPROVAL_SCOPE_MISMATCH",
        ):
            reader(path).resolve_owner_approval(
                approval.approval_id,
                authority_root_id=root.authority_root_id,
                expected_scope=changed_scope,
            )
    with pytest.raises(
        PolicyEvidenceBindingError,
        match="APPROVER_NOT_AUTHORITY_ROOT_OWNER",
    ):
        recorder.record_owner_approval(
            authority_root_id=root.authority_root_id,
            owner_principal_id=foreign.owner_principal.principal_id,
            scope=scope,
            human_action_reference="capture-foreign-owner-approval",
            occurred_at=NOW,
        )
    with pytest.raises(
        PolicyEvidenceBindingError,
        match="OWNER_APPROVAL_SCOPE_MISMATCH",
    ):
        reader(path).resolve_owner_approval(
            approval.approval_id,
            authority_root_id=foreign.authority_root.authority_root_id,
            expected_scope=scope,
        )


def test_worker_consent_binds_exact_worker_and_missing_is_not_grant(
    tmp_path: Path,
) -> None:
    path = tmp_path / "worker-consent.db"
    recorder, _, provisioned, worker_a, worker_b = setup(path)
    root = provisioned.authority_root
    scope = vehicle_scope()
    consent = recorder.record_worker_consent(
        authority_root_id=root.authority_root_id,
        worker_principal_id=worker_a.principal_id,
        worker_id="worker-a",
        scope=scope,
        consent_status=RecordedConsentStatus.FREELY_GIVEN,
        human_action_reference="capture-worker-a-consent",
        occurred_at=NOW,
    )
    evidence_reader = reader(path)

    assert consent.records_affirmative_consent is True
    assert evidence_reader.resolve_worker_consent(
        None,
        authority_root_id=root.authority_root_id,
        worker_id="worker-a",
        expected_scope=scope,
    ) is None
    for expected_worker, changed_scope in (
        ("worker-b", vehicle_scope(worker_id="worker-b")),
        ("worker-a", vehicle_scope(job_id="job-b")),
        ("worker-a", vehicle_scope(operational_date=date(2026, 9, 13))),
        ("worker-a", vehicle_scope(subject_id="private-vehicle-b")),
    ):
        with pytest.raises(
            PolicyEvidenceBindingError,
            match="WORKER_CONSENT_SCOPE_MISMATCH",
        ):
            evidence_reader.resolve_worker_consent(
                consent.consent_id,
                authority_root_id=root.authority_root_id,
                worker_id=expected_worker,
                expected_scope=changed_scope,
            )
    with pytest.raises(
        PolicyEvidenceBindingError,
        match="WORKER_CONSENT_PRINCIPAL_OR_SCOPE_MISMATCH",
    ):
        recorder.record_worker_consent(
            authority_root_id=root.authority_root_id,
            worker_principal_id=worker_b.principal_id,
            worker_id="worker-a",
            scope=scope,
            consent_status=RecordedConsentStatus.FREELY_GIVEN,
            human_action_reference="capture-worker-substitution",
            occurred_at=NOW,
        )
    with pytest.raises(
        PolicyEvidenceBindingError,
        match="WORKER_CONSENT_PRINCIPAL_OR_SCOPE_MISMATCH",
    ):
        recorder.record_worker_consent(
            authority_root_id=root.authority_root_id,
            worker_principal_id=worker_a.principal_id,
            worker_id="worker-unknown",
            scope=AuthorityEvidenceScope(
                scope_kind=EvidenceScopeKind.PRIVATE_VEHICLE_USE,
                subject_type=EvidenceSubjectType.PRIVATE_VEHICLE,
                subject_id="private-vehicle-unknown",
                operational_date=date(2026, 9, 12),
                job_id="job-a",
                worker_id="worker-unknown",
            ),
            consent_status=RecordedConsentStatus.FREELY_GIVEN,
            human_action_reference="capture-unknown-worker",
            occurred_at=NOW,
        )


def test_non_affirmative_consent_round_trips_but_is_not_usable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "declined-consent.db"
    recorder, _, provisioned, worker, _ = setup(path)
    scope = vehicle_scope()
    declined = recorder.record_worker_consent(
        authority_root_id=provisioned.authority_root.authority_root_id,
        worker_principal_id=worker.principal_id,
        worker_id="worker-a",
        scope=scope,
        consent_status=RecordedConsentStatus.DECLINED,
        human_action_reference="capture-declined-consent",
        occurred_at=NOW,
    )

    restored = reader(path).get_worker_consent(
        declined.consent_id
    )
    assert restored == declined
    assert restored.records_affirmative_consent is False
    assert reader(path).resolve_worker_consent(
        declined.consent_id,
        authority_root_id=provisioned.authority_root.authority_root_id,
        worker_id="worker-a",
        expected_scope=scope,
    ) is None


def test_historical_evidence_does_not_cross_authority_root(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical-root.db"
    recorder, auth, first, _, _ = setup(path)
    second = add_second_authority(path, auth)
    profile = recorder.record_company_policy_profile(
        authority_root_id=first.authority_root.authority_root_id
    )
    issuance = recorder.issue_company_policy_profile(
        profile_id=profile.profile_id,
        owner_principal_id=first.owner_principal.principal_id,
    )

    restarted = reader(path)
    assert restarted.resolve_policy_issuance(
        first.authority_root.authority_root_id
    ).issuance == issuance
    unsigned = restarted.resolve_policy_issuance(second.authority_root.authority_root_id)
    assert unsigned.status is PolicyIssuanceStatus.POLICY_UNSIGNED
    assert unsigned.profile is None


@pytest.mark.parametrize("recursive", [0, 1])
@pytest.mark.parametrize("table", M3E0_TABLES)
def test_m3e0_tables_reject_update_delete_and_replace(
    tmp_path: Path,
    recursive: int,
    table: str,
) -> None:
    path = tmp_path / f"immutable-{recursive}-{table}.db"
    recorder, _, provisioned, worker, _ = setup(path)
    root = provisioned.authority_root
    profile = recorder.record_company_policy_profile(
        authority_root_id=root.authority_root_id
    )
    recorder.issue_company_policy_profile(
        profile_id=profile.profile_id,
        owner_principal_id=provisioned.owner_principal.principal_id,
    )
    scope = vehicle_scope()
    recorder.record_owner_approval(
        authority_root_id=root.authority_root_id,
        owner_principal_id=provisioned.owner_principal.principal_id,
        scope=scope,
        human_action_reference="capture-immutable-owner-approval",
        occurred_at=NOW,
    )
    recorder.record_worker_consent(
        authority_root_id=root.authority_root_id,
        worker_principal_id=worker.principal_id,
        worker_id="worker-a",
        scope=scope,
        consent_status=RecordedConsentStatus.FREELY_GIVEN,
        human_action_reference="capture-immutable-worker-consent",
        occurred_at=NOW,
    )

    for operation in (
        f"DELETE FROM {table}",
        f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}",
        f"UPDATE {table} SET schema_version=schema_version",
    ):
        with SqlitePersistence(path).transaction() as connection:
            connection.execute(f"PRAGMA recursive_triggers={recursive}")
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(operation)


def test_runtime_reader_has_no_recording_or_generic_write_boundary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "read-only-api.db"
    setup(path)
    runtime = reader(path)

    assert not hasattr(authority_package, "DeploymentPolicyEvidenceRecorder")
    assert "DeploymentPolicyEvidenceRecorder" not in recording_module.__all__

    for name in (
        "record_company_policy_profile",
        "issue_company_policy_profile",
        "provision_human_action_capture_from_deployment",
        "record_owner_approval",
        "record_worker_consent",
        "transaction",
        "initialize",
    ):
        assert not hasattr(runtime, name)
    for method in (
        DeploymentPolicyEvidenceRecorder.record_company_policy_profile,
        DeploymentPolicyEvidenceRecorder.issue_company_policy_profile,
        DeploymentPolicyEvidenceRecorder.record_owner_approval,
        DeploymentPolicyEvidenceRecorder.record_worker_consent,
    ):
        parameters = inspect.signature(method).parameters
        assert "role" not in parameters
        assert "principal_type" not in parameters
        assert "signed" not in parameters
        assert "approved" not in parameters
        assert "freely_given" not in parameters
        assert "threshold" not in parameters
        assert "policy_payload" not in parameters


def test_identity_is_independent_of_process_time(tmp_path: Path) -> None:
    first_path = tmp_path / "time-a.db"
    second_path = tmp_path / "time-b.db"
    first = setup(first_path, initialized_at=NOW)
    second = setup(second_path, initialized_at=NOW + timedelta(days=3650))

    first_profile = first[0].record_company_policy_profile(
        authority_root_id=first[2].authority_root.authority_root_id
    )
    second_profile = second[0].record_company_policy_profile(
        authority_root_id=second[2].authority_root.authority_root_id
    )
    first_issuance = first[0].issue_company_policy_profile(
        profile_id=first_profile.profile_id,
        owner_principal_id=first[2].owner_principal.principal_id,
    )
    second_issuance = second[0].issue_company_policy_profile(
        profile_id=second_profile.profile_id,
        owner_principal_id=second[2].owner_principal.principal_id,
    )
    scope = vehicle_scope()
    first_approval = first[0].record_owner_approval(
        authority_root_id=first[2].authority_root.authority_root_id,
        owner_principal_id=first[2].owner_principal.principal_id,
        scope=scope,
        human_action_reference="capture-stable-owner-approval",
        occurred_at=NOW,
    )
    second_approval = second[0].record_owner_approval(
        authority_root_id=second[2].authority_root.authority_root_id,
        owner_principal_id=second[2].owner_principal.principal_id,
        scope=scope,
        human_action_reference="capture-stable-owner-approval",
        occurred_at=NOW,
    )
    first_consent = first[0].record_worker_consent(
        authority_root_id=first[2].authority_root.authority_root_id,
        worker_principal_id=first[3].principal_id,
        worker_id="worker-a",
        scope=scope,
        consent_status=RecordedConsentStatus.FREELY_GIVEN,
        human_action_reference="capture-stable-worker-consent",
        occurred_at=NOW,
    )
    second_consent = second[0].record_worker_consent(
        authority_root_id=second[2].authority_root.authority_root_id,
        worker_principal_id=second[3].principal_id,
        worker_id="worker-a",
        scope=scope,
        consent_status=RecordedConsentStatus.FREELY_GIVEN,
        human_action_reference="capture-stable-worker-consent",
        occurred_at=NOW,
    )

    assert first_profile == second_profile
    assert first_issuance == second_issuance
    assert first_profile.profile_id == second_profile.profile_id
    assert first_issuance.issuance_id == second_issuance.issuance_id
    assert first_approval == second_approval
    assert first_consent == second_consent


def test_database_and_authority_ids_cannot_manufacture_trusted_human_action(
    tmp_path: Path,
) -> None:
    path = tmp_path / "capture-key-boundary.db"
    recorder, _, provisioned, worker, _ = setup(path)
    root = provisioned.authority_root

    with pytest.raises(TypeError):
        DeploymentPolicyEvidenceRecorder(path)  # type: ignore[call-arg]

    scope = vehicle_scope()
    approval = recorder.record_owner_approval(
        authority_root_id=root.authority_root_id,
        owner_principal_id=provisioned.owner_principal.principal_id,
        scope=scope,
        human_action_reference="trusted-owner-capture",
        occurred_at=NOW,
    )
    consent = recorder.record_worker_consent(
        authority_root_id=root.authority_root_id,
        worker_principal_id=worker.principal_id,
        worker_id="worker-a",
        scope=scope,
        consent_status=RecordedConsentStatus.FREELY_GIVEN,
        human_action_reference="trusted-worker-capture",
        occurred_at=NOW,
    )

    runtime = PolicyEvidenceRepository(path)
    assert runtime.get_owner_approval(approval.approval_id) == approval
    assert runtime.get_worker_consent(consent.consent_id) == consent

    rogue_key = bytes(range(1, 33))
    rogue = DeploymentPolicyEvidenceRecorder(
        path,
        trusted_human_action_signing_key=rogue_key,
    )
    rogue_scope = vehicle_scope(job_id="job-rogue", subject_id="vehicle-rogue")
    for operation in (
        lambda: rogue.provision_human_action_capture_from_deployment(
            authority_root_id=root.authority_root_id,
            provisioning_reference="rogue-capture-root",
        ),
        lambda: rogue.record_owner_approval(
            authority_root_id=root.authority_root_id,
            owner_principal_id=provisioned.owner_principal.principal_id,
            scope=rogue_scope,
            human_action_reference="rogue-owner-capture",
            occurred_at=NOW,
        ),
        lambda: rogue.record_worker_consent(
            authority_root_id=root.authority_root_id,
            worker_principal_id=worker.principal_id,
            worker_id="worker-a",
            scope=rogue_scope,
            consent_status=RecordedConsentStatus.FREELY_GIVEN,
            human_action_reference="rogue-worker-capture",
            occurred_at=NOW,
        ),
    ):
        with pytest.raises(
            (PolicyEvidenceBindingError, authority_package.PolicyEvidenceConflictError),
            match="HUMAN_ACTION_CAPTURE_(?:KEY_MISMATCH|ROOT_REPLAY_CONFLICT)",
        ):
            operation()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM m3e0_owner_approvals"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM m3e0_worker_consents"
        ).fetchone()[0] == 1


def test_later_revocation_makes_old_affirmative_consent_not_currently_usable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "revocation.db"
    recorder, _, provisioned, worker, _ = setup(path)
    root = provisioned.authority_root
    scope = vehicle_scope()
    granted = recorder.record_worker_consent(
        authority_root_id=root.authority_root_id,
        worker_principal_id=worker.principal_id,
        worker_id="worker-a",
        scope=scope,
        consent_status=RecordedConsentStatus.FREELY_GIVEN,
        human_action_reference="worker-a-granted-a",
        occurred_at=NOW,
    )
    revoked = recorder.record_worker_consent(
        authority_root_id=root.authority_root_id,
        worker_principal_id=worker.principal_id,
        worker_id="worker-a",
        scope=scope,
        consent_status=RecordedConsentStatus.REVOKED,
        human_action_reference="worker-a-revoked-a",
        occurred_at=NOW + timedelta(minutes=1),
    )

    assert granted.records_affirmative_consent is True
    assert revoked.records_affirmative_consent is False
    assert granted.human_action_provenance.lineage_sequence == 1
    assert revoked.human_action_provenance.lineage_sequence == 2
    assert revoked.human_action_provenance.previous_action_id == (
        granted.human_action_provenance.provenance_id
    )
    for _ in range(3):
        replay = reader(path)
        assert replay.get_worker_consent(granted.consent_id) == granted
        assert replay.get_worker_consent(revoked.consent_id) == revoked
        assert replay.resolve_worker_consent(
            granted.consent_id,
            authority_root_id=root.authority_root_id,
            worker_id="worker-a",
            expected_scope=scope,
        ) is None
        assert replay.resolve_worker_consent(
            revoked.consent_id,
            authority_root_id=root.authority_root_id,
            worker_id="worker-a",
            expected_scope=scope,
        ) is None


def test_revocation_isolated_by_exact_scope_and_worker(tmp_path: Path) -> None:
    path = tmp_path / "revocation-isolation.db"
    recorder, auth, provisioned, worker_a, worker_b = setup(path)
    root = provisioned.authority_root
    scope_a = vehicle_scope()
    granted_a = recorder.record_worker_consent(
        authority_root_id=root.authority_root_id,
        worker_principal_id=worker_a.principal_id,
        worker_id="worker-a",
        scope=scope_a,
        consent_status=RecordedConsentStatus.FREELY_GIVEN,
        human_action_reference="worker-a-granted-scope-a",
        occurred_at=NOW,
    )
    changed_scopes = (
        vehicle_scope(job_id="job-b"),
        vehicle_scope(operational_date=date(2026, 9, 13)),
        vehicle_scope(subject_id="vehicle-b"),
    )
    for index, changed_scope in enumerate(changed_scopes, start=1):
        recorder.record_worker_consent(
            authority_root_id=root.authority_root_id,
            worker_principal_id=worker_a.principal_id,
            worker_id="worker-a",
            scope=changed_scope,
            consent_status=RecordedConsentStatus.REVOKED,
            human_action_reference=f"worker-a-revoked-other-scope-{index}",
            occurred_at=NOW + timedelta(minutes=index),
        )
    worker_b_scope = vehicle_scope(
        worker_id="worker-b",
        subject_id="vehicle-worker-b",
    )
    recorder.record_worker_consent(
        authority_root_id=root.authority_root_id,
        worker_principal_id=worker_b.principal_id,
        worker_id="worker-b",
        scope=worker_b_scope,
        consent_status=RecordedConsentStatus.REVOKED,
        human_action_reference="worker-b-revoked-own-scope",
        occurred_at=NOW + timedelta(minutes=4),
    )
    foreign = add_second_authority(path, auth, recorder)
    foreign_worker = auth.provision_worker_principal_from_deployment(
        authority_root_id=foreign.authority_root.authority_root_id,
        principal_subject_id="idp-worker-a-company-b",
        worker_id="worker-a",
    )
    recorder.record_worker_consent(
        authority_root_id=foreign.authority_root.authority_root_id,
        worker_principal_id=foreign_worker.principal_id,
        worker_id="worker-a",
        scope=scope_a,
        consent_status=RecordedConsentStatus.REVOKED,
        human_action_reference="company-b-worker-a-revoked-same-looking-scope",
        occurred_at=NOW + timedelta(minutes=5),
    )

    assert reader(path).resolve_worker_consent(
        granted_a.consent_id,
        authority_root_id=root.authority_root_id,
        worker_id="worker-a",
        expected_scope=scope_a,
    ) == granted_a


def _refingerprinted_provenance_and_evidence(
    raw_evidence: str,
    new_scope: AuthorityEvidenceScope,
):
    evidence = json.loads(raw_evidence)
    scope_document = json.loads(
        authority_package.authority_evidence_scope_semantic_json(new_scope)
    )
    provenance = evidence["human_action_provenance"]
    provenance["scope_id"] = new_scope.scope_id
    provenance["scope_fingerprint"] = new_scope.scope_fingerprint
    provenance_raw = canonical_json(provenance)
    provenance_fingerprint = sha256_text(provenance_raw)
    provenance_id = "m3e0-human-action-" + provenance_fingerprint
    evidence["scope"] = scope_document
    evidence["scope_id"] = new_scope.scope_id
    evidence["scope_fingerprint"] = new_scope.scope_fingerprint
    evidence["human_action_provenance"] = provenance
    evidence["human_action_provenance_id"] = provenance_id
    evidence["human_action_provenance_fingerprint"] = provenance_fingerprint
    evidence_raw = canonical_json(evidence)
    return (
        evidence_raw,
        sha256_text(evidence_raw),
        provenance_raw,
        provenance_fingerprint,
        provenance_id,
    )


def test_refingerprinted_owner_approval_scope_corruption_fails_signature(
    tmp_path: Path,
) -> None:
    path = tmp_path / "refingerprinted-approval.db"
    recorder, _, provisioned, _, _ = setup(path)
    root = provisioned.authority_root
    approval = recorder.record_owner_approval(
        authority_root_id=root.authority_root_id,
        owner_principal_id=provisioned.owner_principal.principal_id,
        scope=vehicle_scope(),
        human_action_reference="owner-approval-authenticated-source-a",
        occurred_at=NOW,
    )
    new_scope = vehicle_scope(job_id="job-b")
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT canonical_semantic_json FROM m3e0_owner_approvals WHERE approval_id=?",
            (approval.approval_id,),
        ).fetchone()
        evidence_raw, evidence_fingerprint, provenance_raw, provenance_fingerprint, provenance_id = (
            _refingerprinted_provenance_and_evidence(row[0], new_scope)
        )
        corrupted_id = "m3e0-owner-approval-" + evidence_fingerprint
        connection.execute("DROP TRIGGER m3e0_owner_approvals_no_update")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            """
            UPDATE m3e0_owner_approvals
            SET approval_id=?, approval_fingerprint=?, scope_id=?,
                scope_fingerprint=?, canonical_scope_json=?,
                human_action_provenance_id=?,
                human_action_provenance_fingerprint=?,
                canonical_human_action_provenance_json=?,
                canonical_semantic_json=?
            WHERE approval_id=?
            """,
            (
                corrupted_id,
                evidence_fingerprint,
                new_scope.scope_id,
                new_scope.scope_fingerprint,
                authority_package.authority_evidence_scope_semantic_json(new_scope),
                provenance_id,
                provenance_fingerprint,
                provenance_raw,
                evidence_raw,
                approval.approval_id,
            ),
        )

    with pytest.raises(
        PolicyEvidenceStorageError,
        match="HUMAN_ACTION_PROVENANCE_INVALID",
    ):
        reader(path).get_owner_approval(corrupted_id)


def test_refingerprinted_worker_consent_scope_corruption_fails_signature(
    tmp_path: Path,
) -> None:
    path = tmp_path / "refingerprinted-consent.db"
    recorder, _, provisioned, worker, _ = setup(path)
    root = provisioned.authority_root
    consent = recorder.record_worker_consent(
        authority_root_id=root.authority_root_id,
        worker_principal_id=worker.principal_id,
        worker_id="worker-a",
        scope=vehicle_scope(),
        consent_status=RecordedConsentStatus.FREELY_GIVEN,
        human_action_reference="worker-consent-authenticated-source-a",
        occurred_at=NOW,
    )
    new_scope = vehicle_scope(job_id="job-b")
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT canonical_semantic_json FROM m3e0_worker_consents WHERE consent_id=?",
            (consent.consent_id,),
        ).fetchone()
        evidence_raw, evidence_fingerprint, provenance_raw, provenance_fingerprint, provenance_id = (
            _refingerprinted_provenance_and_evidence(row[0], new_scope)
        )
        corrupted_id = "m3e0-worker-consent-" + evidence_fingerprint
        connection.execute("DROP TRIGGER m3e0_worker_consents_no_update")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            """
            UPDATE m3e0_worker_consents
            SET consent_id=?, consent_fingerprint=?, scope_id=?,
                scope_fingerprint=?, canonical_scope_json=?,
                human_action_provenance_id=?,
                human_action_provenance_fingerprint=?,
                canonical_human_action_provenance_json=?,
                canonical_semantic_json=?
            WHERE consent_id=?
            """,
            (
                corrupted_id,
                evidence_fingerprint,
                new_scope.scope_id,
                new_scope.scope_fingerprint,
                authority_package.authority_evidence_scope_semantic_json(new_scope),
                provenance_id,
                provenance_fingerprint,
                provenance_raw,
                evidence_raw,
                consent.consent_id,
            ),
        )

    with pytest.raises(
        PolicyEvidenceStorageError,
        match="HUMAN_ACTION_PROVENANCE_INVALID",
    ):
        reader(path).get_worker_consent(corrupted_id)


def test_refingerprinted_consent_history_reorder_is_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "history-reorder.db"
    recorder, _, provisioned, worker, _ = setup(path)
    root = provisioned.authority_root
    scope = vehicle_scope()
    recorder.record_worker_consent(
        authority_root_id=root.authority_root_id,
        worker_principal_id=worker.principal_id,
        worker_id="worker-a",
        scope=scope,
        consent_status=RecordedConsentStatus.FREELY_GIVEN,
        human_action_reference="history-first",
        occurred_at=NOW,
    )
    revoked = recorder.record_worker_consent(
        authority_root_id=root.authority_root_id,
        worker_principal_id=worker.principal_id,
        worker_id="worker-a",
        scope=scope,
        consent_status=RecordedConsentStatus.REVOKED,
        human_action_reference="history-second",
        occurred_at=NOW + timedelta(minutes=1),
    )
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT canonical_semantic_json FROM m3e0_worker_consents WHERE consent_id=?",
            (revoked.consent_id,),
        ).fetchone()
        evidence = json.loads(row[0])
        provenance = evidence["human_action_provenance"]
        provenance["lineage_sequence"] = 3
        provenance_raw = canonical_json(provenance)
        provenance_fingerprint = sha256_text(provenance_raw)
        provenance_id = "m3e0-human-action-" + provenance_fingerprint
        evidence["human_action_provenance"] = provenance
        evidence["human_action_provenance_id"] = provenance_id
        evidence["human_action_provenance_fingerprint"] = provenance_fingerprint
        evidence_raw = canonical_json(evidence)
        evidence_fingerprint = sha256_text(evidence_raw)
        corrupted_id = "m3e0-worker-consent-" + evidence_fingerprint
        connection.execute("DROP TRIGGER m3e0_worker_consents_no_update")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            """
            UPDATE m3e0_worker_consents
            SET consent_id=?, consent_fingerprint=?,
                human_action_lineage_sequence=3,
                human_action_provenance_id=?,
                human_action_provenance_fingerprint=?,
                canonical_human_action_provenance_json=?,
                canonical_semantic_json=?
            WHERE consent_id=?
            """,
            (
                corrupted_id,
                evidence_fingerprint,
                provenance_id,
                provenance_fingerprint,
                provenance_raw,
                evidence_raw,
                revoked.consent_id,
            ),
        )

    with pytest.raises(
        PolicyEvidenceStorageError,
        match="HUMAN_ACTION_PROVENANCE_INVALID|WORKER_CONSENT_CHRONOLOGY_INVALID",
    ):
        reader(path).get_worker_consent(corrupted_id)
