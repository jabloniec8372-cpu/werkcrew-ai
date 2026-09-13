"""Privileged M3-E0 evidence recording and strict read-only replay.

``DeploymentPolicyEvidenceRecorder`` is an internal deployment-held capability.
It is not an HTTP dependency and accepts no caller-owned role, policy value,
approval flag, or freely-given boolean.  ``PolicyEvidenceRepository`` is the
runtime read boundary and exposes no transaction or recording operation.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from werkcrew_ai.authority.models import PrincipalType
from werkcrew_ai.authority.policy_evidence import (
    AuthorityEvidenceScope,
    CompanyPolicyProfile,
    EvidenceScopeKind,
    HumanActionCaptureRoot,
    HumanActionKind,
    HumanActionProvenance,
    OwnerApprovalEvidence,
    PolicyEvidenceBindingError,
    PolicyEvidenceConflictError,
    PolicyEvidenceStorageError,
    PolicyEvidenceValidationError,
    PolicyIssuanceEvidence,
    PolicyIssuanceResolution,
    PolicyIssuanceStatus,
    RecordedConsentStatus,
    WorkerConsentEvidence,
    authority_evidence_scope_semantic_json,
    company_policy_profile_semantic_json,
    human_action_capture_root_semantic_json,
    human_action_provenance_semantic_json,
    owner_approval_evidence_semantic_json,
    policy_issuance_evidence_semantic_json,
    restore_authority_evidence_scope,
    restore_company_policy_profile,
    restore_human_action_capture_root,
    restore_human_action_provenance,
    restore_owner_approval_evidence,
    restore_policy_issuance_evidence,
    restore_worker_consent_evidence,
    _sign_human_action_provenance,
    verify_human_action_provenance,
    worker_consent_evidence_semantic_json,
)
from werkcrew_ai.authority.repository import TrustedAuthorityRepository
from werkcrew_ai.field.serialization import canonical_json, sha256_text
from werkcrew_ai.persistence import SqlitePersistence


def _identity(value: str, field_name: str) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise PolicyEvidenceValidationError("INVALID_VALUE", field_name)


_CAPTURE_CONSUMPTION_SCHEMA_VERSION = "human-action-capture-consumption-v1"
_CAPTURE_CONSUMPTION_ID_PREFIX = "m3e0-human-action-consumption-"


@dataclass(frozen=True, slots=True)
class _CaptureConsumption:
    """Private durable ownership record for one trusted capture identity."""

    consumption_id: str
    consumption_fingerprint: str
    evidence_record_id: str
    evidence_record_fingerprint: str
    worker_id: str | None
    provenance: HumanActionProvenance


def _capture_consumption_semantic_json(
    *,
    evidence_record_id: str,
    evidence_record_fingerprint: str,
    worker_id: str | None,
    provenance: HumanActionProvenance,
) -> str:
    return canonical_json(
        {
            "action_kind": provenance.action_kind.value,
            "action_value": provenance.action_value,
            "authority_root_fingerprint": provenance.authority_root_fingerprint,
            "authority_root_id": provenance.authority_root_id,
            "capture_key_id": provenance.capture_key_id,
            "capture_reference": provenance.capture_reference,
            "evidence_record_fingerprint": evidence_record_fingerprint,
            "evidence_record_id": evidence_record_id,
            "human_action_provenance": json.loads(
                human_action_provenance_semantic_json(provenance)
            ),
            "human_action_provenance_fingerprint": (
                provenance.provenance_fingerprint
            ),
            "human_action_provenance_id": provenance.provenance_id,
            "occurred_at": provenance.occurred_at.isoformat().replace(
                "+00:00", "Z"
            ),
            "principal_fingerprint": provenance.principal_fingerprint,
            "principal_id": provenance.principal_id,
            "rule_version": provenance.rule_version,
            "schema_version": _CAPTURE_CONSUMPTION_SCHEMA_VERSION,
            "scope_fingerprint": provenance.scope_fingerprint,
            "scope_id": provenance.scope_id,
            "worker_id": worker_id,
        }
    )


def _new_capture_consumption(
    *,
    evidence_record_id: str,
    evidence_record_fingerprint: str,
    worker_id: str | None,
    provenance: HumanActionProvenance,
) -> _CaptureConsumption:
    semantic = _capture_consumption_semantic_json(
        evidence_record_id=evidence_record_id,
        evidence_record_fingerprint=evidence_record_fingerprint,
        worker_id=worker_id,
        provenance=provenance,
    )
    fingerprint = sha256_text(semantic)
    return _CaptureConsumption(
        consumption_id=_CAPTURE_CONSUMPTION_ID_PREFIX + fingerprint,
        consumption_fingerprint=fingerprint,
        evidence_record_id=evidence_record_id,
        evidence_record_fingerprint=evidence_record_fingerprint,
        worker_id=worker_id,
        provenance=provenance,
    )


class PolicyEvidenceRepository(TrustedAuthorityRepository):
    """Runtime read-only M3-E0 evidence boundary."""

    @staticmethod
    def _capture_root_record(row) -> HumanActionCaptureRoot:
        value = restore_human_action_capture_root(
            row["canonical_semantic_json"],
            row["capture_root_fingerprint"],
            row["capture_root_id"],
        )
        actual = (
            row["schema_version"],
            row["rule_version"],
            row["authority_root_id"],
            row["authority_root_fingerprint"],
            row["company_id"],
            row["company_plan_id"],
            row["company_plan_provenance_reference"],
            row["verification_key_hex"],
            row["capture_key_id"],
            row["provisioning_source"],
            row["provisioning_reference"],
        )
        expected = (
            value.schema_version,
            value.rule_version,
            value.authority_root_id,
            value.authority_root_fingerprint,
            value.company_id,
            value.company_plan_id,
            value.company_plan_provenance_reference,
            value.verification_key_hex,
            value.capture_key_id,
            value.provisioning_source.value,
            value.provisioning_reference,
        )
        if actual != expected:
            raise PolicyEvidenceStorageError(
                "HUMAN_ACTION_CAPTURE_ROOT_METADATA_MISMATCH",
                "human_action_capture_root",
            )
        return value

    @staticmethod
    def _provenance_record(row) -> HumanActionProvenance:
        return restore_human_action_provenance(
            row["canonical_human_action_provenance_json"],
            row["human_action_provenance_fingerprint"],
            row["human_action_provenance_id"],
        )

    @staticmethod
    def _capture_consumption_record(row) -> _CaptureConsumption:
        provenance = PolicyEvidenceRepository._provenance_record(row)
        if provenance.action_kind is HumanActionKind.OWNER_APPROVAL:
            evidence_prefix = "m3e0-owner-approval-"
        elif provenance.action_kind is HumanActionKind.WORKER_CONSENT:
            evidence_prefix = "m3e0-worker-consent-"
        else:  # pragma: no cover - the restored closed enum already rejects this
            raise PolicyEvidenceStorageError(
                "HUMAN_ACTION_CAPTURE_CONSUMPTION_INVALID",
                "action_kind",
            )
        evidence_fingerprint = row["evidence_record_fingerprint"]
        evidence_record_id = row["evidence_record_id"]
        worker_id = row["worker_id"]
        if (
            type(evidence_fingerprint) is not str
            or len(evidence_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in evidence_fingerprint
            )
            or evidence_record_id != evidence_prefix + evidence_fingerprint
            or (
                worker_id is not None
                and (
                    type(worker_id) is not str
                    or not worker_id
                    or worker_id != worker_id.strip()
                )
            )
        ):
            raise PolicyEvidenceStorageError(
                "HUMAN_ACTION_CAPTURE_CONSUMPTION_INVALID",
                "capture_consumption",
            )
        value = _new_capture_consumption(
            evidence_record_id=evidence_record_id,
            evidence_record_fingerprint=evidence_fingerprint,
            worker_id=worker_id,
            provenance=provenance,
        )
        actual = (
            row["consumption_id"],
            row["consumption_fingerprint"],
            row["schema_version"],
            row["rule_version"],
            row["capture_key_id"],
            row["capture_reference"],
            row["authority_root_id"],
            row["authority_root_fingerprint"],
            row["action_kind"],
            row["action_value"],
            row["principal_id"],
            row["principal_fingerprint"],
            worker_id,
            row["scope_id"],
            row["scope_fingerprint"],
            evidence_record_id,
            evidence_fingerprint,
            row["human_action_provenance_id"],
            row["human_action_provenance_fingerprint"],
            row["human_action_occurred_at"],
            row["canonical_human_action_provenance_json"],
            row["canonical_semantic_json"],
        )
        expected = (
            value.consumption_id,
            value.consumption_fingerprint,
            _CAPTURE_CONSUMPTION_SCHEMA_VERSION,
            provenance.rule_version,
            provenance.capture_key_id,
            provenance.capture_reference,
            provenance.authority_root_id,
            provenance.authority_root_fingerprint,
            provenance.action_kind.value,
            provenance.action_value,
            provenance.principal_id,
            provenance.principal_fingerprint,
            value.worker_id,
            provenance.scope_id,
            provenance.scope_fingerprint,
            value.evidence_record_id,
            value.evidence_record_fingerprint,
            provenance.provenance_id,
            provenance.provenance_fingerprint,
            provenance.occurred_at.isoformat().replace("+00:00", "Z"),
            human_action_provenance_semantic_json(provenance),
            _capture_consumption_semantic_json(
                evidence_record_id=value.evidence_record_id,
                evidence_record_fingerprint=value.evidence_record_fingerprint,
                worker_id=value.worker_id,
                provenance=provenance,
            ),
        )
        if actual != expected:
            raise PolicyEvidenceStorageError(
                "HUMAN_ACTION_CAPTURE_CONSUMPTION_MISMATCH",
                "capture_consumption",
            )
        return value

    def _load_capture_consumption(
        self,
        connection,
        capture_key_id: str,
        capture_reference: str,
    ) -> _CaptureConsumption:
        rows = connection.execute(
            """
            SELECT * FROM m3e0_human_action_capture_consumptions
            WHERE capture_key_id=? AND capture_reference=?
            """,
            (capture_key_id, capture_reference),
        ).fetchall()
        if len(rows) != 1:
            raise PolicyEvidenceStorageError(
                "HUMAN_ACTION_CAPTURE_CONSUMPTION_CARDINALITY_INVALID",
                "capture_consumption",
            )
        return self._capture_consumption_record(rows[0])

    def _verify_capture_consumption(
        self,
        connection,
        *,
        evidence_record_id: str,
        evidence_record_fingerprint: str,
        worker_id: str | None,
        provenance: HumanActionProvenance,
    ) -> None:
        consumption = self._load_capture_consumption(
            connection,
            provenance.capture_key_id,
            provenance.capture_reference,
        )
        self._verify_human_action(connection, consumption.provenance)
        if consumption != _new_capture_consumption(
            evidence_record_id=evidence_record_id,
            evidence_record_fingerprint=evidence_record_fingerprint,
            worker_id=worker_id,
            provenance=provenance,
        ):
            raise PolicyEvidenceStorageError(
                "HUMAN_ACTION_CAPTURE_CONSUMPTION_BINDING_MISMATCH",
                "capture_consumption",
            )

    def _load_capture_root(
        self,
        connection,
        authority_root_id: str,
    ) -> HumanActionCaptureRoot:
        row = connection.execute(
            """
            SELECT * FROM m3e0_human_action_capture_roots
            WHERE authority_root_id=?
            """,
            (authority_root_id,),
        ).fetchone()
        if row is None:
            raise PolicyEvidenceStorageError(
                "HUMAN_ACTION_CAPTURE_ROOT_NOT_FOUND", "authority_root_id"
            )
        value = self._capture_root_record(row)
        root = self._load_root(connection, authority_root_id)
        if (
            value.authority_root_fingerprint,
            value.company_id,
            value.company_plan_id,
            value.company_plan_provenance_reference,
        ) != (
            root.authority_root_fingerprint,
            root.company_id,
            root.company_plan_id,
            root.company_plan_provenance_reference,
        ):
            raise PolicyEvidenceStorageError(
                "HUMAN_ACTION_CAPTURE_ROOT_BINDING_MISMATCH",
                "human_action_capture_root",
            )
        return value

    def _verify_human_action(
        self,
        connection,
        value: HumanActionProvenance,
    ) -> None:
        capture_root = self._load_capture_root(connection, value.authority_root_id)
        if (
            value.capture_root_id != capture_root.capture_root_id
            or value.capture_root_fingerprint
            != capture_root.capture_root_fingerprint
            or value.capture_key_id != capture_root.capture_key_id
        ):
            raise PolicyEvidenceStorageError(
                "HUMAN_ACTION_CAPTURE_ROOT_MISMATCH",
                "human_action_provenance",
            )
        if not verify_human_action_provenance(
            value,
            bytes.fromhex(capture_root.verification_key_hex),
        ):
            raise PolicyEvidenceStorageError(
                "HUMAN_ACTION_PROVENANCE_INVALID",
                "human_action_provenance",
            )

    def get_human_action_capture_root(
        self,
        authority_root_id: str,
    ) -> HumanActionCaptureRoot:
        _identity(authority_root_id, "authority_root_id")
        with self._read_snapshot() as connection:
            return self._load_capture_root(connection, authority_root_id)

    @staticmethod
    def _profile_record(row) -> CompanyPolicyProfile:
        value = restore_company_policy_profile(
            row["canonical_semantic_json"],
            row["profile_fingerprint"],
            row["profile_id"],
        )
        actual = (
            row["schema_version"],
            row["rule_version"],
            row["governance_reference"],
            row["authority_root_id"],
            row["authority_root_fingerprint"],
            row["company_id"],
            row["company_plan_id"],
            row["company_plan_provenance_reference"],
        )
        expected = (
            value.schema_version,
            value.rule_version,
            value.governance_reference,
            value.authority_root_id,
            value.authority_root_fingerprint,
            value.company_id,
            value.company_plan_id,
            value.company_plan_provenance_reference,
        )
        if actual != expected:
            raise PolicyEvidenceStorageError(
                "POLICY_PROFILE_METADATA_MISMATCH", "profile"
            )
        return value

    @staticmethod
    def _issuance_record(row) -> PolicyIssuanceEvidence:
        value = restore_policy_issuance_evidence(
            row["canonical_semantic_json"],
            row["issuance_fingerprint"],
            row["issuance_id"],
        )
        actual = (
            row["schema_version"],
            row["rule_version"],
            row["profile_id"],
            row["profile_fingerprint"],
            row["authority_root_id"],
            row["authority_root_fingerprint"],
            row["owner_principal_id"],
            row["owner_principal_fingerprint"],
            row["company_id"],
            row["company_plan_id"],
            row["company_plan_provenance_reference"],
        )
        expected = (
            value.schema_version,
            value.rule_version,
            value.profile_id,
            value.profile_fingerprint,
            value.authority_root_id,
            value.authority_root_fingerprint,
            value.owner_principal_id,
            value.owner_principal_fingerprint,
            value.company_id,
            value.company_plan_id,
            value.company_plan_provenance_reference,
        )
        if actual != expected:
            raise PolicyEvidenceStorageError(
                "POLICY_ISSUANCE_METADATA_MISMATCH", "issuance"
            )
        return value

    @staticmethod
    def _scope_record(row) -> AuthorityEvidenceScope:
        return restore_authority_evidence_scope(
            row["canonical_scope_json"],
            row["scope_fingerprint"],
            row["scope_id"],
        )

    @classmethod
    def _approval_record(cls, row) -> OwnerApprovalEvidence:
        value = restore_owner_approval_evidence(
            row["canonical_semantic_json"],
            row["approval_fingerprint"],
            row["approval_id"],
        )
        scope = cls._scope_record(row)
        provenance = cls._provenance_record(row)
        actual = (
            row["schema_version"],
            row["rule_version"],
            row["authority_root_id"],
            row["authority_root_fingerprint"],
            row["owner_principal_id"],
            row["owner_principal_fingerprint"],
            row["company_id"],
            row["company_plan_id"],
            row["company_plan_provenance_reference"],
            row["scope_id"],
            row["scope_fingerprint"],
            row["human_action_provenance_id"],
            row["human_action_provenance_fingerprint"],
            row["human_action_capture_key_id"],
            row["human_action_capture_reference"],
            row["human_action_occurred_at"],
            row["human_action_lineage_sequence"],
            row["previous_human_action_id"],
            row["previous_human_action_fingerprint"],
        )
        expected = (
            value.schema_version,
            value.rule_version,
            value.authority_root_id,
            value.authority_root_fingerprint,
            value.owner_principal_id,
            value.owner_principal_fingerprint,
            value.company_id,
            value.company_plan_id,
            value.company_plan_provenance_reference,
            value.scope.scope_id,
            value.scope.scope_fingerprint,
            value.human_action_provenance.provenance_id,
            value.human_action_provenance.provenance_fingerprint,
            value.human_action_provenance.capture_key_id,
            value.human_action_provenance.capture_reference,
            value.human_action_provenance.occurred_at.isoformat().replace(
                "+00:00", "Z"
            ),
            value.human_action_provenance.lineage_sequence,
            value.human_action_provenance.previous_action_id,
            value.human_action_provenance.previous_action_fingerprint,
        )
        if (
            actual != expected
            or value.scope != scope
            or value.human_action_provenance != provenance
        ):
            raise PolicyEvidenceStorageError(
                "OWNER_APPROVAL_METADATA_MISMATCH", "approval"
            )
        return value

    @classmethod
    def _consent_record(cls, row) -> WorkerConsentEvidence:
        value = restore_worker_consent_evidence(
            row["canonical_semantic_json"],
            row["consent_fingerprint"],
            row["consent_id"],
        )
        scope = cls._scope_record(row)
        provenance = cls._provenance_record(row)
        actual = (
            row["schema_version"],
            row["rule_version"],
            row["authority_root_id"],
            row["authority_root_fingerprint"],
            row["worker_principal_id"],
            row["worker_principal_fingerprint"],
            row["company_id"],
            row["company_plan_id"],
            row["company_plan_provenance_reference"],
            row["worker_id"],
            row["worker_registry_revision"],
            row["worker_registry_fingerprint"],
            row["scope_id"],
            row["scope_fingerprint"],
            row["consent_status"],
            row["human_action_provenance_id"],
            row["human_action_provenance_fingerprint"],
            row["human_action_capture_key_id"],
            row["human_action_capture_reference"],
            row["human_action_occurred_at"],
            row["human_action_lineage_sequence"],
            row["previous_human_action_id"],
            row["previous_human_action_fingerprint"],
        )
        expected = (
            value.schema_version,
            value.rule_version,
            value.authority_root_id,
            value.authority_root_fingerprint,
            value.worker_principal_id,
            value.worker_principal_fingerprint,
            value.company_id,
            value.company_plan_id,
            value.company_plan_provenance_reference,
            value.worker_id,
            value.worker_registry_revision,
            value.worker_registry_fingerprint,
            value.scope.scope_id,
            value.scope.scope_fingerprint,
            value.consent_status.value,
            value.human_action_provenance.provenance_id,
            value.human_action_provenance.provenance_fingerprint,
            value.human_action_provenance.capture_key_id,
            value.human_action_provenance.capture_reference,
            value.human_action_provenance.occurred_at.isoformat().replace(
                "+00:00", "Z"
            ),
            value.human_action_provenance.lineage_sequence,
            value.human_action_provenance.previous_action_id,
            value.human_action_provenance.previous_action_fingerprint,
        )
        if (
            actual != expected
            or value.scope != scope
            or value.human_action_provenance != provenance
        ):
            raise PolicyEvidenceStorageError(
                "WORKER_CONSENT_METADATA_MISMATCH", "consent"
            )
        return value

    def _load_principal(self, connection, principal_id: str):
        row = connection.execute(
            "SELECT * FROM auth0_trusted_principals WHERE principal_id=?",
            (principal_id,),
        ).fetchone()
        if row is None:
            raise PolicyEvidenceStorageError("PRINCIPAL_NOT_FOUND", "principal_id")
        principal = self._principal_record(row)
        root = self._load_root(connection, principal.authority_root_id)
        self._principal_binding(connection, root, principal)
        return principal

    def _load_profile(self, connection, profile_id: str) -> CompanyPolicyProfile:
        row = connection.execute(
            "SELECT * FROM m3e0_company_policy_profiles WHERE profile_id=?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise PolicyEvidenceStorageError("POLICY_PROFILE_NOT_FOUND", "profile_id")
        profile = self._profile_record(row)
        root = self._load_root(connection, profile.authority_root_id)
        if (
            profile.authority_root_fingerprint,
            profile.company_id,
            profile.company_plan_id,
            profile.company_plan_provenance_reference,
        ) != (
            root.authority_root_fingerprint,
            root.company_id,
            root.company_plan_id,
            root.company_plan_provenance_reference,
        ):
            raise PolicyEvidenceStorageError(
                "POLICY_PROFILE_ROOT_MISMATCH", "profile"
            )
        return profile

    def _load_issuance(self, connection, issuance_id: str) -> PolicyIssuanceEvidence:
        row = connection.execute(
            "SELECT * FROM m3e0_policy_issuances WHERE issuance_id=?",
            (issuance_id,),
        ).fetchone()
        if row is None:
            raise PolicyEvidenceStorageError(
                "POLICY_ISSUANCE_NOT_FOUND", "issuance_id"
            )
        issuance = self._issuance_record(row)
        profile = self._load_profile(connection, issuance.profile_id)
        root = self._load_root(connection, issuance.authority_root_id)
        owner = self._load_principal(connection, issuance.owner_principal_id)
        if (
            issuance.profile_fingerprint,
            issuance.authority_root_fingerprint,
            issuance.company_id,
            issuance.company_plan_id,
            issuance.company_plan_provenance_reference,
            issuance.owner_principal_fingerprint,
        ) != (
            profile.profile_fingerprint,
            root.authority_root_fingerprint,
            root.company_id,
            root.company_plan_id,
            root.company_plan_provenance_reference,
            owner.principal_fingerprint,
        ) or profile.authority_root_id != root.authority_root_id:
            raise PolicyEvidenceStorageError(
                "POLICY_ISSUANCE_BINDING_MISMATCH", "issuance"
            )
        if (
            owner.principal_type is not PrincipalType.OWNER
            or owner.authority_root_id != root.authority_root_id
        ):
            raise PolicyEvidenceStorageError(
                "POLICY_ISSUER_NOT_OWNER", "owner_principal_id"
            )
        return issuance

    def _load_approval(self, connection, approval_id: str) -> OwnerApprovalEvidence:
        row = connection.execute(
            "SELECT * FROM m3e0_owner_approvals WHERE approval_id=?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise PolicyEvidenceStorageError("OWNER_APPROVAL_NOT_FOUND", "approval_id")
        approval = self._approval_record(row)
        root = self._load_root(connection, approval.authority_root_id)
        owner = self._load_principal(connection, approval.owner_principal_id)
        if (
            approval.authority_root_fingerprint,
            approval.company_id,
            approval.company_plan_id,
            approval.company_plan_provenance_reference,
            approval.owner_principal_fingerprint,
        ) != (
            root.authority_root_fingerprint,
            root.company_id,
            root.company_plan_id,
            root.company_plan_provenance_reference,
            owner.principal_fingerprint,
        ) or owner.principal_type is not PrincipalType.OWNER:
            raise PolicyEvidenceStorageError(
                "OWNER_APPROVAL_BINDING_MISMATCH", "approval"
            )
        if approval.scope.worker_id is not None and (
            approval.scope.worker_id not in root.worker_ids
        ):
            raise PolicyEvidenceStorageError(
                "OWNER_APPROVAL_WORKER_MISMATCH", "scope.worker_id"
            )
        self._verify_human_action(connection, approval.human_action_provenance)
        self._verify_capture_consumption(
            connection,
            evidence_record_id=approval.approval_id,
            evidence_record_fingerprint=approval.approval_fingerprint,
            worker_id=approval.scope.worker_id,
            provenance=approval.human_action_provenance,
        )
        return approval

    def _load_consent(self, connection, consent_id: str) -> WorkerConsentEvidence:
        row = connection.execute(
            "SELECT * FROM m3e0_worker_consents WHERE consent_id=?",
            (consent_id,),
        ).fetchone()
        if row is None:
            raise PolicyEvidenceStorageError("WORKER_CONSENT_NOT_FOUND", "consent_id")
        consent = self._consent_record(row)
        root = self._load_root(connection, consent.authority_root_id)
        principal = self._load_principal(connection, consent.worker_principal_id)
        if (
            consent.authority_root_fingerprint,
            consent.company_id,
            consent.company_plan_id,
            consent.company_plan_provenance_reference,
            consent.worker_principal_fingerprint,
            consent.worker_registry_revision,
            consent.worker_registry_fingerprint,
        ) != (
            root.authority_root_fingerprint,
            root.company_id,
            root.company_plan_id,
            root.company_plan_provenance_reference,
            principal.principal_fingerprint,
            root.worker_registry_revision,
            root.worker_registry_fingerprint,
        ):
            raise PolicyEvidenceStorageError(
                "WORKER_CONSENT_BINDING_MISMATCH", "consent"
            )
        if (
            principal.principal_type is not PrincipalType.WORKER
            or principal.worker_id != consent.worker_id
            or consent.scope.worker_id != consent.worker_id
        ):
            raise PolicyEvidenceStorageError(
                "WORKER_CONSENT_PRINCIPAL_MISMATCH", "worker_id"
            )
        self._verify_human_action(connection, consent.human_action_provenance)
        self._verify_capture_consumption(
            connection,
            evidence_record_id=consent.consent_id,
            evidence_record_fingerprint=consent.consent_fingerprint,
            worker_id=consent.worker_id,
            provenance=consent.human_action_provenance,
        )
        return consent

    def _load_consent_lineage(
        self,
        connection,
        consent: WorkerConsentEvidence,
    ) -> tuple[WorkerConsentEvidence, ...]:
        rows = connection.execute(
            """
            SELECT consent_id
            FROM m3e0_worker_consents
            WHERE authority_root_id=? AND worker_principal_id=?
              AND worker_id=? AND scope_id=?
            ORDER BY human_action_lineage_sequence
            """,
            (
                consent.authority_root_id,
                consent.worker_principal_id,
                consent.worker_id,
                consent.scope.scope_id,
            ),
        ).fetchall()
        lineage = tuple(
            self._load_consent(connection, row["consent_id"]) for row in rows
        )
        previous: WorkerConsentEvidence | None = None
        for sequence, current in enumerate(lineage, start=1):
            provenance = current.human_action_provenance
            expected_previous_id = (
                None
                if previous is None
                else previous.human_action_provenance.provenance_id
            )
            expected_previous_fingerprint = (
                None
                if previous is None
                else previous.human_action_provenance.provenance_fingerprint
            )
            if (
                provenance.lineage_sequence != sequence
                or provenance.previous_action_id != expected_previous_id
                or provenance.previous_action_fingerprint
                != expected_previous_fingerprint
            ):
                raise PolicyEvidenceStorageError(
                    "WORKER_CONSENT_CHRONOLOGY_INVALID",
                    "human_action_provenance",
                )
            previous = current
        if not lineage or consent not in lineage:
            raise PolicyEvidenceStorageError(
                "WORKER_CONSENT_CHRONOLOGY_INVALID", "consent_id"
            )
        return lineage

    def get_company_policy_profile(self, profile_id: str) -> CompanyPolicyProfile:
        _identity(profile_id, "profile_id")
        with self._read_snapshot() as connection:
            return self._load_profile(connection, profile_id)

    def get_policy_issuance(self, issuance_id: str) -> PolicyIssuanceEvidence:
        _identity(issuance_id, "issuance_id")
        with self._read_snapshot() as connection:
            return self._load_issuance(connection, issuance_id)

    def get_owner_approval(self, approval_id: str) -> OwnerApprovalEvidence:
        _identity(approval_id, "approval_id")
        with self._read_snapshot() as connection:
            return self._load_approval(connection, approval_id)

    def get_worker_consent(self, consent_id: str) -> WorkerConsentEvidence:
        _identity(consent_id, "consent_id")
        with self._read_snapshot() as connection:
            consent = self._load_consent(connection, consent_id)
            self._load_consent_lineage(connection, consent)
            return consent

    def resolve_policy_issuance(
        self,
        authority_root_id: str,
    ) -> PolicyIssuanceResolution:
        _identity(authority_root_id, "authority_root_id")
        with self._read_snapshot() as connection:
            self._load_root(connection, authority_root_id)
            profiles = connection.execute(
                "SELECT profile_id FROM m3e0_company_policy_profiles WHERE authority_root_id=?",
                (authority_root_id,),
            ).fetchall()
            if len(profiles) > 1:
                raise PolicyEvidenceStorageError(
                    "POLICY_PROFILE_CARDINALITY_INVALID", "authority_root_id"
                )
            if not profiles:
                return PolicyIssuanceResolution(
                    PolicyIssuanceStatus.POLICY_UNSIGNED,
                    None,
                    None,
                )
            profile = self._load_profile(connection, profiles[0]["profile_id"])
            issuances = connection.execute(
                "SELECT issuance_id FROM m3e0_policy_issuances WHERE profile_id=?",
                (profile.profile_id,),
            ).fetchall()
            if len(issuances) > 1:
                raise PolicyEvidenceStorageError(
                    "POLICY_ISSUANCE_CARDINALITY_INVALID", "profile_id"
                )
            if not issuances:
                return PolicyIssuanceResolution(
                    PolicyIssuanceStatus.POLICY_UNSIGNED,
                    None,
                    None,
                )
            issuance = self._load_issuance(connection, issuances[0]["issuance_id"])
            return PolicyIssuanceResolution(
                PolicyIssuanceStatus.ISSUED,
                profile,
                issuance,
            )

    def resolve_owner_approval(
        self,
        approval_id: str | None,
        *,
        authority_root_id: str,
        expected_scope: AuthorityEvidenceScope,
    ) -> OwnerApprovalEvidence | None:
        _identity(authority_root_id, "authority_root_id")
        if approval_id is None:
            return None
        _identity(approval_id, "approval_id")
        if type(expected_scope) is not AuthorityEvidenceScope:
            raise PolicyEvidenceValidationError("INVALID_VALUE", "expected_scope")
        approval = self.get_owner_approval(approval_id)
        if (
            approval.authority_root_id != authority_root_id
            or approval.scope != expected_scope
        ):
            raise PolicyEvidenceBindingError(
                "OWNER_APPROVAL_SCOPE_MISMATCH", "approval_id"
            )
        return approval

    def resolve_worker_consent(
        self,
        consent_id: str | None,
        *,
        authority_root_id: str,
        worker_id: str,
        expected_scope: AuthorityEvidenceScope,
    ) -> WorkerConsentEvidence | None:
        _identity(authority_root_id, "authority_root_id")
        _identity(worker_id, "worker_id")
        if consent_id is None:
            return None
        _identity(consent_id, "consent_id")
        if type(expected_scope) is not AuthorityEvidenceScope:
            raise PolicyEvidenceValidationError("INVALID_VALUE", "expected_scope")
        with self._read_snapshot() as connection:
            consent = self._load_consent(connection, consent_id)
            if (
                consent.authority_root_id != authority_root_id
                or consent.worker_id != worker_id
                or consent.scope != expected_scope
            ):
                raise PolicyEvidenceBindingError(
                    "WORKER_CONSENT_SCOPE_MISMATCH", "consent_id"
                )
            lineage = self._load_consent_lineage(connection, consent)
            current = lineage[-1]
            if (
                current.consent_id != consent.consent_id
                or not current.records_affirmative_consent
            ):
                return None
            return current


class DeploymentPolicyEvidenceRecorder(PolicyEvidenceRepository):
    """Internal writer backed by a deployment-held human-action signing key."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        trusted_human_action_signing_key: bytes,
        migrations_directory: str | Path | None = None,
    ) -> None:
        if (
            type(trusted_human_action_signing_key) is not bytes
            or len(trusted_human_action_signing_key) != 32
        ):
            raise PolicyEvidenceValidationError(
                "INVALID_VALUE", "trusted_human_action_signing_key"
            )
        private_key = Ed25519PrivateKey.from_private_bytes(
            trusted_human_action_signing_key
        )
        verification_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        super().__init__(database_path)
        self._human_action_signing_key = trusted_human_action_signing_key
        self._human_action_verification_key = verification_key
        self._human_action_capture_key_id = (
            "m3e0-human-action-key-" + hashlib.sha256(verification_key).hexdigest()
        )
        keyword = (
            {}
            if migrations_directory is None
            else {"migrations_directory": migrations_directory}
        )
        self._persistence = SqlitePersistence(self.database_path, **keyword)

    def initialize(self, *, now: datetime) -> tuple[str, ...]:
        return self._persistence.initialize(now=now)

    @staticmethod
    def _insert_capture_root(
        connection,
        value: HumanActionCaptureRoot,
    ) -> None:
        connection.execute(
            """
            INSERT INTO m3e0_human_action_capture_roots(
                capture_root_id, capture_root_fingerprint, schema_version,
                rule_version, authority_root_id, authority_root_fingerprint,
                company_id, company_plan_id,
                company_plan_provenance_reference, verification_key_hex,
                capture_key_id, provisioning_source, provisioning_reference,
                canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                value.capture_root_id,
                value.capture_root_fingerprint,
                value.schema_version,
                value.rule_version,
                value.authority_root_id,
                value.authority_root_fingerprint,
                value.company_id,
                value.company_plan_id,
                value.company_plan_provenance_reference,
                value.verification_key_hex,
                value.capture_key_id,
                value.provisioning_source.value,
                value.provisioning_reference,
                human_action_capture_root_semantic_json(value),
            ),
        )

    def provision_human_action_capture_from_deployment(
        self,
        *,
        authority_root_id: str,
        provisioning_reference: str,
    ) -> HumanActionCaptureRoot:
        """Bind this deployment-held capture key to one exact AUTH-0 root."""

        _identity(authority_root_id, "authority_root_id")
        _identity(provisioning_reference, "provisioning_reference")
        try:
            with self._persistence.transaction() as connection:
                root = self._load_root(connection, authority_root_id)
                capture_root = HumanActionCaptureRoot(
                    authority_root_id=root.authority_root_id,
                    authority_root_fingerprint=root.authority_root_fingerprint,
                    company_id=root.company_id,
                    company_plan_id=root.company_plan_id,
                    company_plan_provenance_reference=(
                        root.company_plan_provenance_reference
                    ),
                    verification_key_hex=self._human_action_verification_key.hex(),
                    provisioning_reference=provisioning_reference,
                )
                row = connection.execute(
                    """
                    SELECT * FROM m3e0_human_action_capture_roots
                    WHERE authority_root_id=?
                    """,
                    (authority_root_id,),
                ).fetchone()
                if row is not None:
                    stored = self._capture_root_record(row)
                    if stored != capture_root:
                        raise PolicyEvidenceConflictError(
                            "HUMAN_ACTION_CAPTURE_ROOT_REPLAY_CONFLICT",
                            "authority_root_id",
                        )
                    return stored
                self._insert_capture_root(connection, capture_root)
                return capture_root
        except sqlite3.IntegrityError as error:
            raise PolicyEvidenceConflictError(
                "HUMAN_ACTION_CAPTURE_ROOT_RECORDING_CONFLICT",
                "human_action_capture_root",
            ) from error

    def _require_recorder_capture_root(
        self,
        connection,
        authority_root_id: str,
    ) -> HumanActionCaptureRoot:
        capture_root = self._load_capture_root(connection, authority_root_id)
        if capture_root.capture_key_id != self._human_action_capture_key_id:
            raise PolicyEvidenceBindingError(
                "HUMAN_ACTION_CAPTURE_KEY_MISMATCH",
                "trusted_human_action_signing_key",
            )
        return capture_root

    @staticmethod
    def _insert_profile(connection, value: CompanyPolicyProfile) -> None:
        connection.execute(
            """
            INSERT INTO m3e0_company_policy_profiles(
                profile_id, profile_fingerprint, schema_version, rule_version,
                governance_reference, authority_root_id,
                authority_root_fingerprint, company_id, company_plan_id,
                company_plan_provenance_reference, canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                value.profile_id,
                value.profile_fingerprint,
                value.schema_version,
                value.rule_version,
                value.governance_reference,
                value.authority_root_id,
                value.authority_root_fingerprint,
                value.company_id,
                value.company_plan_id,
                value.company_plan_provenance_reference,
                company_policy_profile_semantic_json(value),
            ),
        )

    @staticmethod
    def _insert_issuance(connection, value: PolicyIssuanceEvidence) -> None:
        connection.execute(
            """
            INSERT INTO m3e0_policy_issuances(
                issuance_id, issuance_fingerprint, schema_version, rule_version,
                profile_id, profile_fingerprint, authority_root_id,
                authority_root_fingerprint, owner_principal_id,
                owner_principal_fingerprint, company_id, company_plan_id,
                company_plan_provenance_reference, canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                value.issuance_id,
                value.issuance_fingerprint,
                value.schema_version,
                value.rule_version,
                value.profile_id,
                value.profile_fingerprint,
                value.authority_root_id,
                value.authority_root_fingerprint,
                value.owner_principal_id,
                value.owner_principal_fingerprint,
                value.company_id,
                value.company_plan_id,
                value.company_plan_provenance_reference,
                policy_issuance_evidence_semantic_json(value),
            ),
        )

    @staticmethod
    def _insert_capture_consumption(
        connection,
        *,
        evidence_record_id: str,
        evidence_record_fingerprint: str,
        worker_id: str | None,
        provenance: HumanActionProvenance,
    ) -> None:
        value = _new_capture_consumption(
            evidence_record_id=evidence_record_id,
            evidence_record_fingerprint=evidence_record_fingerprint,
            worker_id=worker_id,
            provenance=provenance,
        )
        connection.execute(
            """
            INSERT INTO m3e0_human_action_capture_consumptions(
                consumption_id, consumption_fingerprint, schema_version,
                rule_version, capture_key_id, capture_reference,
                authority_root_id, authority_root_fingerprint, action_kind,
                action_value, principal_id, principal_fingerprint, worker_id,
                scope_id, scope_fingerprint, evidence_record_id,
                evidence_record_fingerprint, human_action_provenance_id,
                human_action_provenance_fingerprint, human_action_occurred_at,
                canonical_human_action_provenance_json, canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                value.consumption_id,
                value.consumption_fingerprint,
                _CAPTURE_CONSUMPTION_SCHEMA_VERSION,
                provenance.rule_version,
                provenance.capture_key_id,
                provenance.capture_reference,
                provenance.authority_root_id,
                provenance.authority_root_fingerprint,
                provenance.action_kind.value,
                provenance.action_value,
                provenance.principal_id,
                provenance.principal_fingerprint,
                value.worker_id,
                provenance.scope_id,
                provenance.scope_fingerprint,
                value.evidence_record_id,
                value.evidence_record_fingerprint,
                provenance.provenance_id,
                provenance.provenance_fingerprint,
                provenance.occurred_at.isoformat().replace("+00:00", "Z"),
                human_action_provenance_semantic_json(provenance),
                _capture_consumption_semantic_json(
                    evidence_record_id=value.evidence_record_id,
                    evidence_record_fingerprint=value.evidence_record_fingerprint,
                    worker_id=value.worker_id,
                    provenance=provenance,
                ),
            ),
        )

    def _find_capture_replay(
        self,
        connection,
        *,
        capture_key_id: str,
        capture_reference: str,
        action_kind: HumanActionKind,
        action_value: str,
        authority_root_id: str,
        authority_root_fingerprint: str,
        principal_id: str,
        principal_fingerprint: str,
        worker_id: str | None,
        scope: AuthorityEvidenceScope,
        occurred_at: datetime,
    ) -> OwnerApprovalEvidence | WorkerConsentEvidence | None:
        rows = connection.execute(
            """
            SELECT * FROM m3e0_human_action_capture_consumptions
            WHERE capture_key_id=? AND capture_reference=?
            """,
            (capture_key_id, capture_reference),
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise PolicyEvidenceStorageError(
                "HUMAN_ACTION_CAPTURE_CONSUMPTION_CARDINALITY_INVALID",
                "capture_consumption",
            )
        consumption = self._capture_consumption_record(rows[0])
        provenance = consumption.provenance
        self._verify_human_action(connection, provenance)
        actual = (
            provenance.action_kind,
            provenance.action_value,
            provenance.authority_root_id,
            provenance.authority_root_fingerprint,
            provenance.principal_id,
            provenance.principal_fingerprint,
            consumption.worker_id,
            provenance.scope_id,
            provenance.scope_fingerprint,
            provenance.occurred_at,
        )
        expected = (
            action_kind,
            action_value,
            authority_root_id,
            authority_root_fingerprint,
            principal_id,
            principal_fingerprint,
            worker_id,
            scope.scope_id,
            scope.scope_fingerprint,
            occurred_at,
        )
        if actual != expected:
            raise PolicyEvidenceConflictError(
                "HUMAN_ACTION_CAPTURE_REPLAY_CONFLICT",
                "human_action_reference",
            )
        if action_kind is HumanActionKind.OWNER_APPROVAL:
            return self._load_approval(connection, consumption.evidence_record_id)
        return self._load_consent(connection, consumption.evidence_record_id)

    @staticmethod
    def _insert_approval(connection, value: OwnerApprovalEvidence) -> None:
        provenance = value.human_action_provenance
        connection.execute(
            """
            INSERT INTO m3e0_owner_approvals(
                approval_id, approval_fingerprint, schema_version, rule_version,
                authority_root_id, authority_root_fingerprint,
                owner_principal_id, owner_principal_fingerprint, company_id,
                company_plan_id, company_plan_provenance_reference, scope_id,
                scope_fingerprint, canonical_scope_json,
                human_action_provenance_id,
                human_action_provenance_fingerprint,
                human_action_capture_key_id, human_action_capture_reference,
                human_action_occurred_at, human_action_lineage_sequence,
                previous_human_action_id, previous_human_action_fingerprint,
                canonical_human_action_provenance_json, canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                value.approval_id,
                value.approval_fingerprint,
                value.schema_version,
                value.rule_version,
                value.authority_root_id,
                value.authority_root_fingerprint,
                value.owner_principal_id,
                value.owner_principal_fingerprint,
                value.company_id,
                value.company_plan_id,
                value.company_plan_provenance_reference,
                value.scope.scope_id,
                value.scope.scope_fingerprint,
                authority_evidence_scope_semantic_json(value.scope),
                provenance.provenance_id,
                provenance.provenance_fingerprint,
                provenance.capture_key_id,
                provenance.capture_reference,
                provenance.occurred_at.isoformat().replace("+00:00", "Z"),
                provenance.lineage_sequence,
                provenance.previous_action_id,
                provenance.previous_action_fingerprint,
                human_action_provenance_semantic_json(provenance),
                owner_approval_evidence_semantic_json(value),
            ),
        )

    @staticmethod
    def _insert_consent(connection, value: WorkerConsentEvidence) -> None:
        provenance = value.human_action_provenance
        connection.execute(
            """
            INSERT INTO m3e0_worker_consents(
                consent_id, consent_fingerprint, schema_version, rule_version,
                authority_root_id, authority_root_fingerprint,
                worker_principal_id, worker_principal_fingerprint, company_id,
                company_plan_id, company_plan_provenance_reference, worker_id,
                worker_registry_revision, worker_registry_fingerprint, scope_id,
                scope_fingerprint, canonical_scope_json, consent_status,
                human_action_provenance_id,
                human_action_provenance_fingerprint,
                human_action_capture_key_id, human_action_capture_reference,
                human_action_occurred_at, human_action_lineage_sequence,
                previous_human_action_id, previous_human_action_fingerprint,
                canonical_human_action_provenance_json, canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                value.consent_id,
                value.consent_fingerprint,
                value.schema_version,
                value.rule_version,
                value.authority_root_id,
                value.authority_root_fingerprint,
                value.worker_principal_id,
                value.worker_principal_fingerprint,
                value.company_id,
                value.company_plan_id,
                value.company_plan_provenance_reference,
                value.worker_id,
                value.worker_registry_revision,
                value.worker_registry_fingerprint,
                value.scope.scope_id,
                value.scope.scope_fingerprint,
                authority_evidence_scope_semantic_json(value.scope),
                value.consent_status.value,
                provenance.provenance_id,
                provenance.provenance_fingerprint,
                provenance.capture_key_id,
                provenance.capture_reference,
                provenance.occurred_at.isoformat().replace("+00:00", "Z"),
                provenance.lineage_sequence,
                provenance.previous_action_id,
                provenance.previous_action_fingerprint,
                human_action_provenance_semantic_json(provenance),
                worker_consent_evidence_semantic_json(value),
            ),
        )

    def record_company_policy_profile(
        self,
        *,
        authority_root_id: str,
    ) -> CompanyPolicyProfile:
        """Record only the fixed ADR 0013 profile for a restored AUTH-0 root."""

        _identity(authority_root_id, "authority_root_id")
        try:
            with self._persistence.transaction() as connection:
                root = self._load_root(connection, authority_root_id)
                profile = CompanyPolicyProfile(
                    company_id=root.company_id,
                    company_plan_id=root.company_plan_id,
                    company_plan_provenance_reference=(
                        root.company_plan_provenance_reference
                    ),
                    authority_root_id=root.authority_root_id,
                    authority_root_fingerprint=root.authority_root_fingerprint,
                )
                row = connection.execute(
                    "SELECT * FROM m3e0_company_policy_profiles WHERE authority_root_id=?",
                    (authority_root_id,),
                ).fetchone()
                if row is not None:
                    stored = self._profile_record(row)
                    if stored != profile:
                        raise PolicyEvidenceConflictError(
                            "POLICY_PROFILE_REPLAY_CONFLICT", "authority_root_id"
                        )
                    return stored
                self._insert_profile(connection, profile)
                return profile
        except sqlite3.IntegrityError as error:
            raise PolicyEvidenceConflictError(
                "POLICY_PROFILE_RECORDING_CONFLICT", "profile"
            ) from error

    def issue_company_policy_profile(
        self,
        *,
        profile_id: str,
        owner_principal_id: str,
    ) -> PolicyIssuanceEvidence:
        _identity(profile_id, "profile_id")
        _identity(owner_principal_id, "owner_principal_id")
        try:
            with self._persistence.transaction() as connection:
                profile = self._load_profile(connection, profile_id)
                root = self._load_root(connection, profile.authority_root_id)
                owner = self._load_principal(connection, owner_principal_id)
                if (
                    owner.principal_type is not PrincipalType.OWNER
                    or owner.authority_root_id != root.authority_root_id
                ):
                    raise PolicyEvidenceBindingError(
                        "POLICY_ISSUER_NOT_AUTHORITY_ROOT_OWNER",
                        "owner_principal_id",
                    )
                issuance = PolicyIssuanceEvidence(
                    profile_id=profile.profile_id,
                    profile_fingerprint=profile.profile_fingerprint,
                    authority_root_id=root.authority_root_id,
                    authority_root_fingerprint=root.authority_root_fingerprint,
                    owner_principal_id=owner.principal_id,
                    owner_principal_fingerprint=owner.principal_fingerprint,
                    company_id=root.company_id,
                    company_plan_id=root.company_plan_id,
                    company_plan_provenance_reference=(
                        root.company_plan_provenance_reference
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM m3e0_policy_issuances WHERE profile_id=?",
                    (profile.profile_id,),
                ).fetchone()
                if row is not None:
                    stored = self._issuance_record(row)
                    if stored != issuance:
                        raise PolicyEvidenceConflictError(
                            "POLICY_ISSUANCE_REPLAY_CONFLICT", "profile_id"
                        )
                    return stored
                self._insert_issuance(connection, issuance)
                return issuance
        except sqlite3.IntegrityError as error:
            raise PolicyEvidenceConflictError(
                "POLICY_ISSUANCE_RECORDING_CONFLICT", "issuance"
            ) from error

    def record_owner_approval(
        self,
        *,
        authority_root_id: str,
        owner_principal_id: str,
        scope: AuthorityEvidenceScope,
        human_action_reference: str,
        occurred_at: datetime,
    ) -> OwnerApprovalEvidence:
        _identity(authority_root_id, "authority_root_id")
        _identity(owner_principal_id, "owner_principal_id")
        _identity(human_action_reference, "human_action_reference")
        if type(scope) is not AuthorityEvidenceScope:
            raise PolicyEvidenceValidationError("INVALID_VALUE", "scope")
        if (
            type(occurred_at) is not datetime
            or occurred_at.tzinfo is None
            or occurred_at.utcoffset() is None
        ):
            raise PolicyEvidenceValidationError("INVALID_VALUE", "occurred_at")
        normalized_occurred_at = occurred_at.astimezone(timezone.utc)
        try:
            with self._persistence.transaction() as connection:
                root = self._load_root(connection, authority_root_id)
                capture_root = self._require_recorder_capture_root(
                    connection,
                    root.authority_root_id,
                )
                owner = self._load_principal(connection, owner_principal_id)
                if (
                    owner.principal_type is not PrincipalType.OWNER
                    or owner.authority_root_id != root.authority_root_id
                ):
                    raise PolicyEvidenceBindingError(
                        "APPROVER_NOT_AUTHORITY_ROOT_OWNER", "owner_principal_id"
                    )
                if scope.worker_id is not None and scope.worker_id not in root.worker_ids:
                    raise PolicyEvidenceBindingError(
                        "APPROVAL_SCOPE_WORKER_UNKNOWN", "scope.worker_id"
                    )
                replay = self._find_capture_replay(
                    connection,
                    capture_key_id=capture_root.capture_key_id,
                    capture_reference=human_action_reference,
                    action_kind=HumanActionKind.OWNER_APPROVAL,
                    action_value="APPROVED",
                    authority_root_id=root.authority_root_id,
                    authority_root_fingerprint=root.authority_root_fingerprint,
                    principal_id=owner.principal_id,
                    principal_fingerprint=owner.principal_fingerprint,
                    worker_id=scope.worker_id,
                    scope=scope,
                    occurred_at=normalized_occurred_at,
                )
                if replay is not None:
                    if type(replay) is not OwnerApprovalEvidence:
                        raise PolicyEvidenceStorageError(
                            "HUMAN_ACTION_CAPTURE_CONSUMPTION_BINDING_MISMATCH",
                            "capture_consumption",
                        )
                    return replay
                provenance = _sign_human_action_provenance(
                    signing_key=self._human_action_signing_key,
                    action_kind=HumanActionKind.OWNER_APPROVAL,
                    action_value="APPROVED",
                    authority_root_id=root.authority_root_id,
                    authority_root_fingerprint=root.authority_root_fingerprint,
                    principal_id=owner.principal_id,
                    principal_fingerprint=owner.principal_fingerprint,
                    scope_id=scope.scope_id,
                    scope_fingerprint=scope.scope_fingerprint,
                    occurred_at=normalized_occurred_at,
                    capture_reference=human_action_reference,
                    lineage_sequence=1,
                    previous_action_id=None,
                    previous_action_fingerprint=None,
                    capture_root_id=capture_root.capture_root_id,
                    capture_root_fingerprint=capture_root.capture_root_fingerprint,
                )
                approval = OwnerApprovalEvidence(
                    authority_root_id=root.authority_root_id,
                    authority_root_fingerprint=root.authority_root_fingerprint,
                    owner_principal_id=owner.principal_id,
                    owner_principal_fingerprint=owner.principal_fingerprint,
                    company_id=root.company_id,
                    company_plan_id=root.company_plan_id,
                    company_plan_provenance_reference=(
                        root.company_plan_provenance_reference
                    ),
                    scope=scope,
                    human_action_provenance=provenance,
                )
                row = connection.execute(
                    "SELECT * FROM m3e0_owner_approvals WHERE authority_root_id=? AND scope_id=?",
                    (root.authority_root_id, scope.scope_id),
                ).fetchone()
                if row is not None:
                    stored = self._load_approval(connection, row["approval_id"])
                    if stored != approval:
                        raise PolicyEvidenceConflictError(
                            "OWNER_APPROVAL_REPLAY_CONFLICT", "scope"
                        )
                    return stored
                self._insert_capture_consumption(
                    connection,
                    evidence_record_id=approval.approval_id,
                    evidence_record_fingerprint=approval.approval_fingerprint,
                    worker_id=approval.scope.worker_id,
                    provenance=provenance,
                )
                self._insert_approval(connection, approval)
                return approval
        except sqlite3.IntegrityError as error:
            raise PolicyEvidenceConflictError(
                "OWNER_APPROVAL_RECORDING_CONFLICT", "approval"
            ) from error

    def record_worker_consent(
        self,
        *,
        authority_root_id: str,
        worker_principal_id: str,
        worker_id: str,
        scope: AuthorityEvidenceScope,
        consent_status: RecordedConsentStatus,
        human_action_reference: str,
        occurred_at: datetime,
    ) -> WorkerConsentEvidence:
        for value, name in (
            (authority_root_id, "authority_root_id"),
            (worker_principal_id, "worker_principal_id"),
            (worker_id, "worker_id"),
            (human_action_reference, "human_action_reference"),
        ):
            _identity(value, name)
        if type(scope) is not AuthorityEvidenceScope:
            raise PolicyEvidenceValidationError("INVALID_VALUE", "scope")
        if type(consent_status) is not RecordedConsentStatus:
            raise PolicyEvidenceValidationError("INVALID_VALUE", "consent_status")
        if (
            type(occurred_at) is not datetime
            or occurred_at.tzinfo is None
            or occurred_at.utcoffset() is None
        ):
            raise PolicyEvidenceValidationError("INVALID_VALUE", "occurred_at")
        normalized_occurred_at = occurred_at.astimezone(timezone.utc)
        try:
            with self._persistence.transaction() as connection:
                root = self._load_root(connection, authority_root_id)
                capture_root = self._require_recorder_capture_root(
                    connection,
                    root.authority_root_id,
                )
                principal = self._load_principal(connection, worker_principal_id)
                if (
                    principal.principal_type is not PrincipalType.WORKER
                    or principal.authority_root_id != root.authority_root_id
                    or principal.worker_id != worker_id
                    or scope.worker_id != worker_id
                ):
                    raise PolicyEvidenceBindingError(
                        "WORKER_CONSENT_PRINCIPAL_OR_SCOPE_MISMATCH", "worker_id"
                    )
                replay = self._find_capture_replay(
                    connection,
                    capture_key_id=capture_root.capture_key_id,
                    capture_reference=human_action_reference,
                    action_kind=HumanActionKind.WORKER_CONSENT,
                    action_value=consent_status.value,
                    authority_root_id=root.authority_root_id,
                    authority_root_fingerprint=root.authority_root_fingerprint,
                    principal_id=principal.principal_id,
                    principal_fingerprint=principal.principal_fingerprint,
                    worker_id=worker_id,
                    scope=scope,
                    occurred_at=normalized_occurred_at,
                )
                if replay is not None:
                    if type(replay) is not WorkerConsentEvidence:
                        raise PolicyEvidenceStorageError(
                            "HUMAN_ACTION_CAPTURE_CONSUMPTION_BINDING_MISMATCH",
                            "capture_consumption",
                        )
                    return replay
                prior_rows = connection.execute(
                    """
                    SELECT consent_id FROM m3e0_worker_consents
                    WHERE authority_root_id=? AND worker_principal_id=?
                      AND worker_id=? AND scope_id=?
                    ORDER BY human_action_lineage_sequence
                    """,
                    (
                        root.authority_root_id,
                        principal.principal_id,
                        worker_id,
                        scope.scope_id,
                    ),
                ).fetchall()
                prior = tuple(
                    self._load_consent(connection, row["consent_id"])
                    for row in prior_rows
                )
                if prior:
                    self._load_consent_lineage(connection, prior[-1])
                previous = prior[-1] if prior else None
                provenance = _sign_human_action_provenance(
                    signing_key=self._human_action_signing_key,
                    action_kind=HumanActionKind.WORKER_CONSENT,
                    action_value=consent_status.value,
                    authority_root_id=root.authority_root_id,
                    authority_root_fingerprint=root.authority_root_fingerprint,
                    principal_id=principal.principal_id,
                    principal_fingerprint=principal.principal_fingerprint,
                    scope_id=scope.scope_id,
                    scope_fingerprint=scope.scope_fingerprint,
                    occurred_at=normalized_occurred_at,
                    capture_reference=human_action_reference,
                    lineage_sequence=len(prior) + 1,
                    previous_action_id=(
                        None
                        if previous is None
                        else previous.human_action_provenance.provenance_id
                    ),
                    previous_action_fingerprint=(
                        None
                        if previous is None
                        else previous.human_action_provenance.provenance_fingerprint
                    ),
                    capture_root_id=capture_root.capture_root_id,
                    capture_root_fingerprint=capture_root.capture_root_fingerprint,
                )
                consent = WorkerConsentEvidence(
                    authority_root_id=root.authority_root_id,
                    authority_root_fingerprint=root.authority_root_fingerprint,
                    worker_principal_id=principal.principal_id,
                    worker_principal_fingerprint=principal.principal_fingerprint,
                    company_id=root.company_id,
                    company_plan_id=root.company_plan_id,
                    company_plan_provenance_reference=(
                        root.company_plan_provenance_reference
                    ),
                    worker_id=worker_id,
                    worker_registry_revision=root.worker_registry_revision,
                    worker_registry_fingerprint=root.worker_registry_fingerprint,
                    scope=scope,
                    consent_status=consent_status,
                    human_action_provenance=provenance,
                )
                self._insert_capture_consumption(
                    connection,
                    evidence_record_id=consent.consent_id,
                    evidence_record_fingerprint=consent.consent_fingerprint,
                    worker_id=consent.worker_id,
                    provenance=provenance,
                )
                self._insert_consent(connection, consent)
                return consent
        except sqlite3.IntegrityError as error:
            raise PolicyEvidenceConflictError(
                "WORKER_CONSENT_RECORDING_CONFLICT", "consent"
            ) from error


__all__ = [
    "PolicyEvidenceRepository",
]
