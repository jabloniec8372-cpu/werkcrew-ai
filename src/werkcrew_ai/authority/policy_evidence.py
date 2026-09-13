"""Immutable M3-E0 policy, approval, and consent evidence.

Constructing these values does not make them authoritative.  Runtime consumers
must restore them through ``PolicyEvidenceRepository`` so the AUTH-0 root and
principal bindings are independently re-proved.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from werkcrew_ai.authority.models import AuthorityError
from werkcrew_ai.field.serialization import canonical_json, sha256_text


COMPANY_POLICY_PROFILE_SCHEMA_VERSION = "company-policy-profile-v1"
POLICY_ISSUANCE_SCHEMA_VERSION = "policy-issuance-evidence-v1"
OWNER_APPROVAL_SCHEMA_VERSION = "owner-approval-evidence-v1"
WORKER_CONSENT_SCHEMA_VERSION = "worker-consent-evidence-v1"
AUTHORITY_EVIDENCE_SCOPE_SCHEMA_VERSION = "authority-evidence-scope-v1"
HUMAN_ACTION_PROVENANCE_SCHEMA_VERSION = "trusted-human-action-provenance-v1"
HUMAN_ACTION_CAPTURE_ROOT_SCHEMA_VERSION = "trusted-human-action-capture-root-v1"
COMPANY_POLICY_RULE_VERSION = "owner-company-policy-profile-v1"
POLICY_EVIDENCE_RULE_VERSION = "m3e0-authoritative-policy-evidence-v1"
HUMAN_ACTION_CAPTURE_RULE_VERSION = "m3e0-deployment-human-action-capture-v1"
COMPANY_POLICY_GOVERNANCE_REFERENCE = (
    "docs/decisions/0013-owner-company-policy-profile-v1-freeze.md"
)
MAX_ADDITIONAL_INTERNAL_LABOR_COST_EUR = Decimal("50.00")


class PolicyEvidenceValidationError(AuthorityError):
    pass


class PolicyEvidenceBindingError(AuthorityError):
    pass


class PolicyEvidenceStorageError(AuthorityError):
    pass


class PolicyEvidenceConflictError(AuthorityError):
    pass


class EvidenceScopeKind(StrEnum):
    PRIVATE_VEHICLE_USE = "PRIVATE_VEHICLE_USE"
    OWNER_APPROVAL_REQUIRED = "OWNER_APPROVAL_REQUIRED"


class EvidenceSubjectType(StrEnum):
    COMMITMENT = "COMMITMENT"
    RESOURCE = "RESOURCE"
    PRIVATE_VEHICLE = "PRIVATE_VEHICLE"
    DECISION = "DECISION"


class RecordedConsentStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    REQUESTED = "REQUESTED"
    FREELY_GIVEN = "FREELY_GIVEN"
    DECLINED = "DECLINED"
    PRESSURED = "PRESSURED"
    ABSENT = "ABSENT"
    REVOKED = "REVOKED"


class HumanActionKind(StrEnum):
    OWNER_APPROVAL = "OWNER_APPROVAL"
    WORKER_CONSENT = "WORKER_CONSENT"


class HumanActionCaptureProvisioningSource(StrEnum):
    DEPLOYMENT_BOOTSTRAP = "DEPLOYMENT_BOOTSTRAP"


class PolicyIssuanceStatus(StrEnum):
    ISSUED = "ISSUED"
    POLICY_UNSIGNED = "POLICY_UNSIGNED"


def _require(condition: bool, field_name: str) -> None:
    if not condition:
        raise PolicyEvidenceValidationError("INVALID_VALUE", field_name)


def _identity(value: str, field_name: str) -> None:
    _require(type(value) is str and bool(value) and value == value.strip(), field_name)
    _require(
        not any(
            ord(character) < 32
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        ),
        field_name,
    )


def _digest(value: str, field_name: str) -> None:
    _require(
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value),
        field_name,
    )


def _root_identity(authority_root_id: str, fingerprint: str) -> None:
    _identity(authority_root_id, "authority_root_id")
    _digest(fingerprint, "authority_root_fingerprint")
    _require(
        authority_root_id == "auth0-company-authority-root-" + fingerprint,
        "authority_root_id",
    )


def _principal_identity(principal_id: str, fingerprint: str, prefix: str) -> None:
    _identity(principal_id, prefix + "_id")
    _digest(fingerprint, prefix + "_fingerprint")
    _require(
        principal_id == "auth0-trusted-principal-" + fingerprint,
        prefix + "_id",
    )


def _timestamp(value: datetime, field_name: str) -> datetime:
    _require(
        type(value) is datetime
        and value.tzinfo is not None
        and value.utcoffset() is not None,
        field_name,
    )
    return value.astimezone(timezone.utc)


def _capture_key_id(public_key: bytes) -> str:
    _require(type(public_key) is bytes and len(public_key) == 32, "public_key")
    return "m3e0-human-action-key-" + hashlib.sha256(public_key).hexdigest()


def _human_action_unsigned_document(
    *,
    action_kind: HumanActionKind,
    action_value: str,
    authority_root_id: str,
    authority_root_fingerprint: str,
    principal_id: str,
    principal_fingerprint: str,
    scope_id: str,
    scope_fingerprint: str,
    occurred_at: datetime,
    capture_reference: str,
    lineage_sequence: int,
    previous_action_id: str | None,
    previous_action_fingerprint: str | None,
    capture_key_id: str,
    capture_root_id: str,
    capture_root_fingerprint: str,
) -> dict[str, Any]:
    return {
        "action_kind": action_kind.value,
        "action_value": action_value,
        "authority_root_fingerprint": authority_root_fingerprint,
        "authority_root_id": authority_root_id,
        "capture_key_id": capture_key_id,
        "capture_reference": capture_reference,
        "capture_root_fingerprint": capture_root_fingerprint,
        "capture_root_id": capture_root_id,
        "lineage_sequence": lineage_sequence,
        "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
        "previous_action_fingerprint": previous_action_fingerprint,
        "previous_action_id": previous_action_id,
        "principal_fingerprint": principal_fingerprint,
        "principal_id": principal_id,
        "rule_version": HUMAN_ACTION_CAPTURE_RULE_VERSION,
        "schema_version": HUMAN_ACTION_PROVENANCE_SCHEMA_VERSION,
        "scope_fingerprint": scope_fingerprint,
        "scope_id": scope_id,
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class HumanActionCaptureRoot:
    """Deployment-provisioned public verification anchor for one AUTH-0 root."""

    authority_root_id: str
    authority_root_fingerprint: str
    company_id: str
    company_plan_id: str
    company_plan_provenance_reference: str
    verification_key_hex: str
    provisioning_reference: str
    provisioning_source: HumanActionCaptureProvisioningSource = field(
        default=HumanActionCaptureProvisioningSource.DEPLOYMENT_BOOTSTRAP,
        init=False,
    )
    schema_version: str = field(
        default=HUMAN_ACTION_CAPTURE_ROOT_SCHEMA_VERSION,
        init=False,
    )
    rule_version: str = field(
        default=HUMAN_ACTION_CAPTURE_RULE_VERSION,
        init=False,
    )
    capture_key_id: str = field(init=False)
    capture_root_fingerprint: str = field(init=False)
    capture_root_id: str = field(init=False)

    def __post_init__(self) -> None:
        _root_identity(self.authority_root_id, self.authority_root_fingerprint)
        for value, name in (
            (self.company_id, "company_id"),
            (self.company_plan_id, "company_plan_id"),
            (
                self.company_plan_provenance_reference,
                "company_plan_provenance_reference",
            ),
            (self.provisioning_reference, "provisioning_reference"),
        ):
            _identity(value, name)
        _require(
            type(self.verification_key_hex) is str
            and len(self.verification_key_hex) == 64
            and all(
                character in "0123456789abcdef"
                for character in self.verification_key_hex
            ),
            "verification_key_hex",
        )
        key_id = _capture_key_id(bytes.fromhex(self.verification_key_hex))
        object.__setattr__(self, "capture_key_id", key_id)
        digest = sha256_text(human_action_capture_root_semantic_json(self))
        object.__setattr__(self, "capture_root_fingerprint", digest)
        object.__setattr__(self, "capture_root_id", "m3e0-human-action-root-" + digest)


def human_action_capture_root_semantic_json(value: HumanActionCaptureRoot) -> str:
    _require(type(value) is HumanActionCaptureRoot, "human_action_capture_root")
    return canonical_json(
        {
            "authority_root_fingerprint": value.authority_root_fingerprint,
            "authority_root_id": value.authority_root_id,
            "company_id": value.company_id,
            "company_plan_id": value.company_plan_id,
            "company_plan_provenance_reference": (
                value.company_plan_provenance_reference
            ),
            "provisioning_reference": value.provisioning_reference,
            "provisioning_source": value.provisioning_source.value,
            "rule_version": value.rule_version,
            "schema_version": value.schema_version,
            "verification_key_hex": value.verification_key_hex,
        }
    )


def restore_human_action_capture_root(
    raw_semantic: str,
    fingerprint: str,
    capture_root_id: str,
) -> HumanActionCaptureRoot:
    try:
        document = json.loads(raw_semantic)
        _require(
            type(document) is dict and canonical_json(document) == raw_semantic,
            "canonical_semantic_json",
        )
        _require(
            set(document)
            == {
                "authority_root_fingerprint",
                "authority_root_id",
                "company_id",
                "company_plan_id",
                "company_plan_provenance_reference",
                "provisioning_reference",
                "provisioning_source",
                "rule_version",
                "schema_version",
                "verification_key_hex",
            },
            "canonical_semantic_json",
        )
        _require(
            document.pop("schema_version")
            == HUMAN_ACTION_CAPTURE_ROOT_SCHEMA_VERSION,
            "schema_version",
        )
        _require(
            document.pop("rule_version") == HUMAN_ACTION_CAPTURE_RULE_VERSION,
            "rule_version",
        )
        _require(
            document.pop("provisioning_source")
            == HumanActionCaptureProvisioningSource.DEPLOYMENT_BOOTSTRAP.value,
            "provisioning_source",
        )
        value = HumanActionCaptureRoot(**document)
        _require(
            value.capture_root_fingerprint == fingerprint,
            "capture_root_fingerprint",
        )
        _require(value.capture_root_id == capture_root_id, "capture_root_id")
        _require(
            human_action_capture_root_semantic_json(value) == raw_semantic,
            "canonical_semantic_json",
        )
        return value
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        raise PolicyEvidenceStorageError(
            "INVALID_POLICY_EVIDENCE_RECORD", "human_action_capture_root"
        ) from error


@dataclass(frozen=True, slots=True, kw_only=True)
class HumanActionProvenance:
    """A deployment-capture-key attestation of one exact human action.

    The value is only authoritative after repository verification against the
    independently configured public key.  Its principal reference identifies
    the human; the signature proves capture through the trusted ingestion
    boundary.  Neither property substitutes for request authentication.
    """

    action_kind: HumanActionKind
    action_value: str
    authority_root_id: str
    authority_root_fingerprint: str
    principal_id: str
    principal_fingerprint: str
    scope_id: str
    scope_fingerprint: str
    occurred_at: datetime
    capture_reference: str
    lineage_sequence: int
    previous_action_id: str | None
    previous_action_fingerprint: str | None
    capture_key_id: str
    capture_root_id: str
    capture_root_fingerprint: str
    capture_signature: str
    schema_version: str = field(
        default=HUMAN_ACTION_PROVENANCE_SCHEMA_VERSION,
        init=False,
    )
    rule_version: str = field(
        default=HUMAN_ACTION_CAPTURE_RULE_VERSION,
        init=False,
    )
    provenance_fingerprint: str = field(init=False)
    provenance_id: str = field(init=False)

    def __post_init__(self) -> None:
        _require(type(self.action_kind) is HumanActionKind, "action_kind")
        _identity(self.action_value, "action_value")
        if self.action_kind is HumanActionKind.OWNER_APPROVAL:
            _require(self.action_value == "APPROVED", "action_value")
        else:
            _require(
                self.action_value in {status.value for status in RecordedConsentStatus},
                "action_value",
            )
        _root_identity(self.authority_root_id, self.authority_root_fingerprint)
        _principal_identity(
            self.principal_id,
            self.principal_fingerprint,
            "principal",
        )
        _identity(self.scope_id, "scope_id")
        _digest(self.scope_fingerprint, "scope_fingerprint")
        _require(
            self.scope_id == "m3e0-authority-scope-" + self.scope_fingerprint,
            "scope_id",
        )
        object.__setattr__(self, "occurred_at", _timestamp(self.occurred_at, "occurred_at"))
        _identity(self.capture_reference, "capture_reference")
        _require(
            type(self.lineage_sequence) is int and self.lineage_sequence >= 1,
            "lineage_sequence",
        )
        if self.lineage_sequence == 1:
            _require(self.previous_action_id is None, "previous_action_id")
            _require(
                self.previous_action_fingerprint is None,
                "previous_action_fingerprint",
            )
        else:
            _identity(self.previous_action_id, "previous_action_id")
            _digest(
                self.previous_action_fingerprint,
                "previous_action_fingerprint",
            )
            _require(
                self.previous_action_id
                == "m3e0-human-action-" + self.previous_action_fingerprint,
                "previous_action_id",
            )
        _identity(self.capture_key_id, "capture_key_id")
        _require(
            self.capture_key_id.startswith("m3e0-human-action-key-")
            and len(self.capture_key_id) == len("m3e0-human-action-key-") + 64,
            "capture_key_id",
        )
        _digest(
            self.capture_key_id.removeprefix("m3e0-human-action-key-"),
            "capture_key_id",
        )
        _identity(self.capture_root_id, "capture_root_id")
        _digest(self.capture_root_fingerprint, "capture_root_fingerprint")
        _require(
            self.capture_root_id
            == "m3e0-human-action-root-" + self.capture_root_fingerprint,
            "capture_root_id",
        )
        _require(
            type(self.capture_signature) is str
            and len(self.capture_signature) == 128
            and all(character in "0123456789abcdef" for character in self.capture_signature),
            "capture_signature",
        )
        digest = sha256_text(human_action_provenance_semantic_json(self))
        object.__setattr__(self, "provenance_fingerprint", digest)
        object.__setattr__(self, "provenance_id", "m3e0-human-action-" + digest)


def human_action_provenance_unsigned_json(value: HumanActionProvenance) -> str:
    _require(type(value) is HumanActionProvenance, "human_action_provenance")
    return canonical_json(
        _human_action_unsigned_document(
            action_kind=value.action_kind,
            action_value=value.action_value,
            authority_root_id=value.authority_root_id,
            authority_root_fingerprint=value.authority_root_fingerprint,
            principal_id=value.principal_id,
            principal_fingerprint=value.principal_fingerprint,
            scope_id=value.scope_id,
            scope_fingerprint=value.scope_fingerprint,
            occurred_at=value.occurred_at,
            capture_reference=value.capture_reference,
            lineage_sequence=value.lineage_sequence,
            previous_action_id=value.previous_action_id,
            previous_action_fingerprint=value.previous_action_fingerprint,
            capture_key_id=value.capture_key_id,
            capture_root_id=value.capture_root_id,
            capture_root_fingerprint=value.capture_root_fingerprint,
        )
    )


def human_action_provenance_semantic_json(value: HumanActionProvenance) -> str:
    document = json.loads(human_action_provenance_unsigned_json(value))
    document["capture_signature"] = value.capture_signature
    return canonical_json(document)


def _sign_human_action_provenance(
    *,
    signing_key: bytes,
    action_kind: HumanActionKind,
    action_value: str,
    authority_root_id: str,
    authority_root_fingerprint: str,
    principal_id: str,
    principal_fingerprint: str,
    scope_id: str,
    scope_fingerprint: str,
    occurred_at: datetime,
    capture_reference: str,
    lineage_sequence: int,
    previous_action_id: str | None,
    previous_action_fingerprint: str | None,
    capture_root_id: str,
    capture_root_fingerprint: str,
) -> HumanActionProvenance:
    """Sign one action at the internal deployment capture boundary."""

    _require(type(signing_key) is bytes and len(signing_key) == 32, "signing_key")
    private_key = Ed25519PrivateKey.from_private_bytes(signing_key)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    normalized_occurred_at = _timestamp(occurred_at, "occurred_at")
    key_id = _capture_key_id(public_key)
    unsigned = canonical_json(
        _human_action_unsigned_document(
            action_kind=action_kind,
            action_value=action_value,
            authority_root_id=authority_root_id,
            authority_root_fingerprint=authority_root_fingerprint,
            principal_id=principal_id,
            principal_fingerprint=principal_fingerprint,
            scope_id=scope_id,
            scope_fingerprint=scope_fingerprint,
            occurred_at=normalized_occurred_at,
            capture_reference=capture_reference,
            lineage_sequence=lineage_sequence,
            previous_action_id=previous_action_id,
            previous_action_fingerprint=previous_action_fingerprint,
            capture_key_id=key_id,
            capture_root_id=capture_root_id,
            capture_root_fingerprint=capture_root_fingerprint,
        )
    )
    return HumanActionProvenance(
        action_kind=action_kind,
        action_value=action_value,
        authority_root_id=authority_root_id,
        authority_root_fingerprint=authority_root_fingerprint,
        principal_id=principal_id,
        principal_fingerprint=principal_fingerprint,
        scope_id=scope_id,
        scope_fingerprint=scope_fingerprint,
        occurred_at=normalized_occurred_at,
        capture_reference=capture_reference,
        lineage_sequence=lineage_sequence,
        previous_action_id=previous_action_id,
        previous_action_fingerprint=previous_action_fingerprint,
        capture_key_id=key_id,
        capture_root_id=capture_root_id,
        capture_root_fingerprint=capture_root_fingerprint,
        capture_signature=private_key.sign(unsigned.encode("utf-8")).hex(),
    )


def verify_human_action_provenance(
    value: HumanActionProvenance,
    verification_key: bytes,
) -> bool:
    """Verify capture provenance without granting the ability to sign actions."""

    _require(type(value) is HumanActionProvenance, "human_action_provenance")
    _require(
        type(verification_key) is bytes and len(verification_key) == 32,
        "verification_key",
    )
    if value.capture_key_id != _capture_key_id(verification_key):
        return False
    try:
        Ed25519PublicKey.from_public_bytes(verification_key).verify(
            bytes.fromhex(value.capture_signature),
            human_action_provenance_unsigned_json(value).encode("utf-8"),
        )
    except (InvalidSignature, ValueError):
        return False
    return True


def restore_human_action_provenance(
    raw_semantic: str,
    fingerprint: str,
    provenance_id: str,
) -> HumanActionProvenance:
    try:
        document = json.loads(raw_semantic)
        _require(
            type(document) is dict and canonical_json(document) == raw_semantic,
            "canonical_provenance_json",
        )
        _require(
            set(document)
            == {
                "action_kind",
                "action_value",
                "authority_root_fingerprint",
                "authority_root_id",
                "capture_key_id",
                "capture_reference",
                "capture_root_fingerprint",
                "capture_root_id",
                "capture_signature",
                "lineage_sequence",
                "occurred_at",
                "previous_action_fingerprint",
                "previous_action_id",
                "principal_fingerprint",
                "principal_id",
                "rule_version",
                "schema_version",
                "scope_fingerprint",
                "scope_id",
            },
            "canonical_provenance_json",
        )
        _require(
            document.pop("schema_version") == HUMAN_ACTION_PROVENANCE_SCHEMA_VERSION,
            "schema_version",
        )
        _require(
            document.pop("rule_version") == HUMAN_ACTION_CAPTURE_RULE_VERSION,
            "rule_version",
        )
        document["action_kind"] = HumanActionKind(document["action_kind"])
        occurred_at = document["occurred_at"]
        _require(type(occurred_at) is str and occurred_at.endswith("Z"), "occurred_at")
        document["occurred_at"] = datetime.fromisoformat(occurred_at[:-1] + "+00:00")
        value = HumanActionProvenance(**document)
        _require(value.provenance_fingerprint == fingerprint, "provenance_fingerprint")
        _require(value.provenance_id == provenance_id, "provenance_id")
        _require(
            human_action_provenance_semantic_json(value) == raw_semantic,
            "canonical_provenance_json",
        )
        return value
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        raise PolicyEvidenceStorageError(
            "INVALID_POLICY_EVIDENCE_RECORD", "human_action_provenance"
        ) from error


_POLICY_PAYLOAD_V1: dict[str, Any] = {
    "global_rules": {
        "absence_of_rejection_is_approval": False,
        "m3c_rejected_may_be_upgraded": False,
        "missing_evidence_is_consent": False,
        "missing_policy_is_permission": False,
        "owner_may_override_hard_block": False,
        "policy_authority_requirements": [
            "EXACT",
            "SCOPED",
            "VERSIONED",
            "REPLAYABLE",
        ],
        "missing_issuance_state": "POLICY_UNSIGNED",
        "policy_unsigned_autonomous_authority": "NONE",
        "silence_is_consent": False,
        "technical_feasibility_is_authority": False,
        "unknown_equals_false": False,
        "unknown_equals_zero": False,
        "unsigned_fallback_profile": False,
    },
    "p_assign": {
        "all_other_applicable_gates_required": True,
        "authority": "CONSTRAINED_AUTO",
        "independent_act_license": False,
        "other_gate_failure_or_unknown_prevents_autonomy": True,
        "scope": "SAME_OPERATIONAL_DAY_WORKER_REASSIGNMENT",
    },
    "p_cost": {
        "boundary": "INCLUSIVE",
        "currency": "EUR",
        "incomplete_or_unknown_equals_zero": False,
        "incomplete_or_unknown_passes": False,
        "m3d_completeness_required": "COMPLETE",
        "max_additional_internal_labor_cost": format(
            MAX_ADDITIONAL_INTERNAL_LABOR_COST_EUR,
            "f",
        ),
        "negative_delta_allowed": True,
        "positive_delta_above_max_passes": False,
        "zero_delta_allowed": True,
    },
    "p_horizon": {
        "authority_horizon": "INCIDENT_DAY_ONLY",
        "m3c_technical_horizon": "D..D+2",
        "later_day_requires_separate_authority_path": True,
        "technical_horizon_grants_authority": False,
    },
    "p_overtime": {
        "authority": "ASK_ALWAYS",
        "infer_from_missing_evidence": False,
        "unknown_or_incomplete_disposition": "ABSTAIN",
        "worker_refusal_is_risk_trait": False,
    },
    "p_search": {
        "exhausted_with_feasible": {
            "feasible_precedence_preserved": True,
            "truncation_blocks_candidate": False,
            "truncation_penalizes_candidate": False,
        },
        "exhausted_without_feasible": {
            "disposition": "ABSTAIN",
            "external_orchestration_may_expose_blocking_state": True,
            "global_impossibility_claim": False,
            "preserved_outcome": "SEARCH_ENVELOPE_EXHAUSTED",
            "reason": "ENVELOPE_EXHAUSTED",
        },
    },
    "p_tag": {
        "approval_scope": "EXACT",
        "authority": "ASK_ALWAYS",
        "hard_block_override_allowed": False,
        "policy_relation": "SOFT_EXCEPTION",
        "tag": "OWNER_APPROVAL_REQUIRED",
        "valid_scoped_owner_approval_required": True,
    },
    "p_vehicle": {
        "authority": "OWNER_APPROVAL_AND_WORKER_CONSENT",
        "owner_approval_implies_worker_consent": False,
        "scope_dimensions": ["WORKER", "OPERATIONAL_DAY", "JOB"],
        "scope_transferable": False,
        "worker_consent_required": "FREELY_GIVEN",
    },
    "p_window_01": {
        "confirmed": {
            "excluded_knowledge_states": ["ABSENT", "UNKNOWN"],
            "source": "AUTHORITATIVE_C0_CONSTRAINT_KNOWLEDGE",
        },
        "intra_window_slot_shift": {
            "confirmed_exact_start_fact_created": False,
            "definition": "PROPOSED_INTERVAL_SUBSET_OF_SAME_CONFIRMED_WINDOW",
            "may_remain_eligible": True,
            "separate_authority_gate": False,
        },
        "movement_outside_window": {
            "authority_outcome": "ASK",
            "definition": "PROPOSED_INTERVAL_NOT_SUBSET_OF_CONFIRMED_WINDOW",
            "disposition": "ABSTAIN",
            "fresh_upstream_evaluation_required": True,
            "m3c_verdict": "REJECTED",
            "may_act": False,
            "reason": "OWNER_DECISION_REQUIRED_FOR_CUSTOMER_PROMISE",
            "selected_candidate_id": None,
        },
        "unknown_or_absent_required_window_grants_act": False,
    },
}
_CANONICAL_POLICY_PAYLOAD_V1 = canonical_json(_POLICY_PAYLOAD_V1)


def company_policy_payload_v1() -> dict[str, Any]:
    """Return a detached copy of the exact ADR 0013 policy payload."""

    return json.loads(_CANONICAL_POLICY_PAYLOAD_V1)


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorityEvidenceScope:
    scope_kind: EvidenceScopeKind
    subject_type: EvidenceSubjectType
    subject_id: str
    operational_date: date
    job_id: str
    worker_id: str | None
    schema_version: str = field(
        default=AUTHORITY_EVIDENCE_SCOPE_SCHEMA_VERSION,
        init=False,
    )
    rule_version: str = field(default=POLICY_EVIDENCE_RULE_VERSION, init=False)
    scope_fingerprint: str = field(init=False)
    scope_id: str = field(init=False)

    def __post_init__(self) -> None:
        _require(type(self.scope_kind) is EvidenceScopeKind, "scope_kind")
        _require(type(self.subject_type) is EvidenceSubjectType, "subject_type")
        for value, name in (
            (self.subject_id, "subject_id"),
            (self.job_id, "job_id"),
        ):
            _identity(value, name)
        _require(type(self.operational_date) is date, "operational_date")
        if self.worker_id is not None:
            _identity(self.worker_id, "worker_id")
        if self.scope_kind is EvidenceScopeKind.PRIVATE_VEHICLE_USE:
            _require(
                self.subject_type is EvidenceSubjectType.PRIVATE_VEHICLE,
                "subject_type",
            )
            _require(self.worker_id is not None, "worker_id")
        digest = sha256_text(authority_evidence_scope_semantic_json(self))
        object.__setattr__(self, "scope_fingerprint", digest)
        object.__setattr__(self, "scope_id", "m3e0-authority-scope-" + digest)


def authority_evidence_scope_semantic_json(value: AuthorityEvidenceScope) -> str:
    _require(type(value) is AuthorityEvidenceScope, "scope")
    return canonical_json(
        {
            "job_id": value.job_id,
            "operational_date": value.operational_date.isoformat(),
            "rule_version": value.rule_version,
            "schema_version": value.schema_version,
            "scope_kind": value.scope_kind.value,
            "subject_id": value.subject_id,
            "subject_type": value.subject_type.value,
            "worker_id": value.worker_id,
        }
    )


def restore_authority_evidence_scope(
    raw_semantic: str,
    fingerprint: str,
    scope_id: str,
) -> AuthorityEvidenceScope:
    try:
        document = json.loads(raw_semantic)
        _require(
            type(document) is dict and canonical_json(document) == raw_semantic,
            "canonical_scope_json",
        )
        _require(
            set(document)
            == {
                "job_id",
                "operational_date",
                "rule_version",
                "schema_version",
                "scope_kind",
                "subject_id",
                "subject_type",
                "worker_id",
            },
            "canonical_scope_json",
        )
        _require(
            document.pop("schema_version")
            == AUTHORITY_EVIDENCE_SCOPE_SCHEMA_VERSION,
            "schema_version",
        )
        _require(
            document.pop("rule_version") == POLICY_EVIDENCE_RULE_VERSION,
            "rule_version",
        )
        value = AuthorityEvidenceScope(
            scope_kind=EvidenceScopeKind(document["scope_kind"]),
            subject_type=EvidenceSubjectType(document["subject_type"]),
            subject_id=document["subject_id"],
            operational_date=date.fromisoformat(document["operational_date"]),
            job_id=document["job_id"],
            worker_id=document["worker_id"],
        )
        _require(value.scope_fingerprint == fingerprint, "scope_fingerprint")
        _require(value.scope_id == scope_id, "scope_id")
        _require(
            authority_evidence_scope_semantic_json(value) == raw_semantic,
            "canonical_scope_json",
        )
        return value
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        raise PolicyEvidenceStorageError(
            "INVALID_POLICY_EVIDENCE_RECORD", "scope"
        ) from error


@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyPolicyProfile:
    company_id: str
    company_plan_id: str
    company_plan_provenance_reference: str
    authority_root_id: str
    authority_root_fingerprint: str
    schema_version: str = field(
        default=COMPANY_POLICY_PROFILE_SCHEMA_VERSION,
        init=False,
    )
    rule_version: str = field(default=COMPANY_POLICY_RULE_VERSION, init=False)
    governance_reference: str = field(
        default=COMPANY_POLICY_GOVERNANCE_REFERENCE,
        init=False,
    )
    profile_fingerprint: str = field(init=False)
    profile_id: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.company_id, "company_id"),
            (self.company_plan_id, "company_plan_id"),
            (
                self.company_plan_provenance_reference,
                "company_plan_provenance_reference",
            ),
        ):
            _identity(value, name)
        _root_identity(self.authority_root_id, self.authority_root_fingerprint)
        digest = sha256_text(company_policy_profile_semantic_json(self))
        object.__setattr__(self, "profile_fingerprint", digest)
        object.__setattr__(self, "profile_id", "m3e0-company-policy-profile-" + digest)

    @property
    def policy_payload(self) -> dict[str, Any]:
        return company_policy_payload_v1()

    @property
    def max_additional_internal_labor_cost(self) -> Decimal:
        return MAX_ADDITIONAL_INTERNAL_LABOR_COST_EUR


def company_policy_profile_semantic_json(value: CompanyPolicyProfile) -> str:
    _require(type(value) is CompanyPolicyProfile, "profile")
    return canonical_json(
        {
            "authority_root_fingerprint": value.authority_root_fingerprint,
            "authority_root_id": value.authority_root_id,
            "company_id": value.company_id,
            "company_plan_id": value.company_plan_id,
            "company_plan_provenance_reference": (
                value.company_plan_provenance_reference
            ),
            "governance_reference": value.governance_reference,
            "policy_payload": company_policy_payload_v1(),
            "rule_version": value.rule_version,
            "schema_version": value.schema_version,
        }
    )


def restore_company_policy_profile(
    raw_semantic: str,
    fingerprint: str,
    profile_id: str,
) -> CompanyPolicyProfile:
    try:
        document = _canonical_document(raw_semantic)
        _require(
            set(document)
            == {
                "authority_root_fingerprint",
                "authority_root_id",
                "company_id",
                "company_plan_id",
                "company_plan_provenance_reference",
                "governance_reference",
                "policy_payload",
                "rule_version",
                "schema_version",
            },
            "canonical_semantic_json",
        )
        _require(
            document.pop("schema_version") == COMPANY_POLICY_PROFILE_SCHEMA_VERSION,
            "schema_version",
        )
        _require(
            document.pop("rule_version") == COMPANY_POLICY_RULE_VERSION,
            "rule_version",
        )
        _require(
            document.pop("governance_reference")
            == COMPANY_POLICY_GOVERNANCE_REFERENCE,
            "governance_reference",
        )
        _require(
            canonical_json(document.pop("policy_payload"))
            == _CANONICAL_POLICY_PAYLOAD_V1,
            "policy_payload",
        )
        value = CompanyPolicyProfile(**document)
        _validate_restored(
            value,
            raw_semantic,
            fingerprint,
            profile_id,
            "profile_fingerprint",
            "profile_id",
            company_policy_profile_semantic_json,
        )
        return value
    except PolicyEvidenceStorageError:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        raise PolicyEvidenceStorageError(
            "INVALID_POLICY_EVIDENCE_RECORD", "profile"
        ) from error


@dataclass(frozen=True, slots=True, kw_only=True)
class PolicyIssuanceEvidence:
    profile_id: str
    profile_fingerprint: str
    authority_root_id: str
    authority_root_fingerprint: str
    owner_principal_id: str
    owner_principal_fingerprint: str
    company_id: str
    company_plan_id: str
    company_plan_provenance_reference: str
    schema_version: str = field(default=POLICY_ISSUANCE_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=POLICY_EVIDENCE_RULE_VERSION, init=False)
    issuance_fingerprint: str = field(init=False)
    issuance_id: str = field(init=False)

    def __post_init__(self) -> None:
        _identity(self.profile_id, "profile_id")
        _digest(self.profile_fingerprint, "profile_fingerprint")
        _require(
            self.profile_id
            == "m3e0-company-policy-profile-" + self.profile_fingerprint,
            "profile_id",
        )
        _root_identity(self.authority_root_id, self.authority_root_fingerprint)
        _principal_identity(
            self.owner_principal_id,
            self.owner_principal_fingerprint,
            "owner_principal",
        )
        for value, name in (
            (self.company_id, "company_id"),
            (self.company_plan_id, "company_plan_id"),
            (
                self.company_plan_provenance_reference,
                "company_plan_provenance_reference",
            ),
        ):
            _identity(value, name)
        digest = sha256_text(policy_issuance_evidence_semantic_json(self))
        object.__setattr__(self, "issuance_fingerprint", digest)
        object.__setattr__(self, "issuance_id", "m3e0-policy-issuance-" + digest)


def policy_issuance_evidence_semantic_json(value: PolicyIssuanceEvidence) -> str:
    return _flat_semantic_json(
        value,
        ("issuance_fingerprint", "issuance_id"),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class OwnerApprovalEvidence:
    authority_root_id: str
    authority_root_fingerprint: str
    owner_principal_id: str
    owner_principal_fingerprint: str
    company_id: str
    company_plan_id: str
    company_plan_provenance_reference: str
    scope: AuthorityEvidenceScope
    human_action_provenance: HumanActionProvenance
    schema_version: str = field(default=OWNER_APPROVAL_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=POLICY_EVIDENCE_RULE_VERSION, init=False)
    approval_fingerprint: str = field(init=False)
    approval_id: str = field(init=False)

    def __post_init__(self) -> None:
        _root_identity(self.authority_root_id, self.authority_root_fingerprint)
        _principal_identity(
            self.owner_principal_id,
            self.owner_principal_fingerprint,
            "owner_principal",
        )
        for value, name in (
            (self.company_id, "company_id"),
            (self.company_plan_id, "company_plan_id"),
            (
                self.company_plan_provenance_reference,
                "company_plan_provenance_reference",
            ),
        ):
            _identity(value, name)
        _require(type(self.scope) is AuthorityEvidenceScope, "scope")
        _require(
            type(self.human_action_provenance) is HumanActionProvenance,
            "human_action_provenance",
        )
        provenance = self.human_action_provenance
        _require(
            provenance.action_kind is HumanActionKind.OWNER_APPROVAL
            and provenance.action_value == "APPROVED"
            and provenance.authority_root_id == self.authority_root_id
            and provenance.authority_root_fingerprint
            == self.authority_root_fingerprint
            and provenance.principal_id == self.owner_principal_id
            and provenance.principal_fingerprint == self.owner_principal_fingerprint
            and provenance.scope_id == self.scope.scope_id
            and provenance.scope_fingerprint == self.scope.scope_fingerprint
            and provenance.lineage_sequence == 1
            and provenance.previous_action_id is None
            and provenance.previous_action_fingerprint is None,
            "human_action_provenance",
        )
        digest = sha256_text(owner_approval_evidence_semantic_json(self))
        object.__setattr__(self, "approval_fingerprint", digest)
        object.__setattr__(self, "approval_id", "m3e0-owner-approval-" + digest)


def owner_approval_evidence_semantic_json(value: OwnerApprovalEvidence) -> str:
    return _evidence_with_scope_semantic_json(
        value,
        ("approval_fingerprint", "approval_id"),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerConsentEvidence:
    authority_root_id: str
    authority_root_fingerprint: str
    worker_principal_id: str
    worker_principal_fingerprint: str
    company_id: str
    company_plan_id: str
    company_plan_provenance_reference: str
    worker_id: str
    worker_registry_revision: int
    worker_registry_fingerprint: str
    scope: AuthorityEvidenceScope
    consent_status: RecordedConsentStatus
    human_action_provenance: HumanActionProvenance
    schema_version: str = field(default=WORKER_CONSENT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=POLICY_EVIDENCE_RULE_VERSION, init=False)
    consent_fingerprint: str = field(init=False)
    consent_id: str = field(init=False)

    def __post_init__(self) -> None:
        _root_identity(self.authority_root_id, self.authority_root_fingerprint)
        _principal_identity(
            self.worker_principal_id,
            self.worker_principal_fingerprint,
            "worker_principal",
        )
        for value, name in (
            (self.company_id, "company_id"),
            (self.company_plan_id, "company_plan_id"),
            (
                self.company_plan_provenance_reference,
                "company_plan_provenance_reference",
            ),
            (self.worker_id, "worker_id"),
        ):
            _identity(value, name)
        _require(
            type(self.worker_registry_revision) is int
            and self.worker_registry_revision >= 0,
            "worker_registry_revision",
        )
        _digest(self.worker_registry_fingerprint, "worker_registry_fingerprint")
        _require(type(self.scope) is AuthorityEvidenceScope, "scope")
        _require(
            self.scope.scope_kind is EvidenceScopeKind.PRIVATE_VEHICLE_USE,
            "scope.scope_kind",
        )
        _require(self.scope.worker_id == self.worker_id, "scope.worker_id")
        _require(type(self.consent_status) is RecordedConsentStatus, "consent_status")
        _require(
            type(self.human_action_provenance) is HumanActionProvenance,
            "human_action_provenance",
        )
        provenance = self.human_action_provenance
        _require(
            provenance.action_kind is HumanActionKind.WORKER_CONSENT
            and provenance.action_value == self.consent_status.value
            and provenance.authority_root_id == self.authority_root_id
            and provenance.authority_root_fingerprint
            == self.authority_root_fingerprint
            and provenance.principal_id == self.worker_principal_id
            and provenance.principal_fingerprint == self.worker_principal_fingerprint
            and provenance.scope_id == self.scope.scope_id
            and provenance.scope_fingerprint == self.scope.scope_fingerprint,
            "human_action_provenance",
        )
        digest = sha256_text(worker_consent_evidence_semantic_json(self))
        object.__setattr__(self, "consent_fingerprint", digest)
        object.__setattr__(self, "consent_id", "m3e0-worker-consent-" + digest)

    @property
    def records_affirmative_consent(self) -> bool:
        """Whether this historical record is affirmative, not whether it is current."""

        return self.consent_status is RecordedConsentStatus.FREELY_GIVEN


def worker_consent_evidence_semantic_json(value: WorkerConsentEvidence) -> str:
    return _evidence_with_scope_semantic_json(
        value,
        ("consent_fingerprint", "consent_id"),
    )


@dataclass(frozen=True, slots=True)
class PolicyIssuanceResolution:
    status: PolicyIssuanceStatus
    profile: CompanyPolicyProfile | None
    issuance: PolicyIssuanceEvidence | None

    def __post_init__(self) -> None:
        _require(type(self.status) is PolicyIssuanceStatus, "status")
        if self.status is PolicyIssuanceStatus.ISSUED:
            _require(self.profile is not None, "profile")
            _require(self.issuance is not None, "issuance")
        else:
            _require(self.profile is None, "profile")
            _require(self.issuance is None, "issuance")
        if self.issuance is not None and self.profile is not None:
            _require(
                self.issuance.profile_id == self.profile.profile_id,
                "issuance.profile_id",
            )


def _primitive(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    return value


def _flat_semantic_json(value: Any, omitted: tuple[str, ...]) -> str:
    return canonical_json(
        {
            item.name: _primitive(getattr(value, item.name))
            for item in fields(value)
            if item.name not in omitted
        }
    )


def _evidence_with_scope_semantic_json(
    value: Any,
    omitted: tuple[str, ...],
) -> str:
    document = {
        item.name: _primitive(getattr(value, item.name))
        for item in fields(value)
        if item.name not in omitted
        and item.name not in {"scope", "human_action_provenance"}
    }
    document["scope"] = json.loads(
        authority_evidence_scope_semantic_json(value.scope)
    )
    document["scope_fingerprint"] = value.scope.scope_fingerprint
    document["scope_id"] = value.scope.scope_id
    document["human_action_provenance"] = json.loads(
        human_action_provenance_semantic_json(value.human_action_provenance)
    )
    document["human_action_provenance_fingerprint"] = (
        value.human_action_provenance.provenance_fingerprint
    )
    document["human_action_provenance_id"] = (
        value.human_action_provenance.provenance_id
    )
    return canonical_json(document)


def _canonical_document(raw_semantic: str) -> dict[str, Any]:
    document = json.loads(raw_semantic)
    _require(
        type(document) is dict and canonical_json(document) == raw_semantic,
        "canonical_semantic_json",
    )
    return document


def _validate_restored(
    value: Any,
    raw_semantic: str,
    fingerprint: str,
    record_id: str,
    fingerprint_name: str,
    id_name: str,
    semantic_function,
) -> None:
    _require(getattr(value, fingerprint_name) == fingerprint, fingerprint_name)
    _require(getattr(value, id_name) == record_id, id_name)
    _require(semantic_function(value) == raw_semantic, "canonical_semantic_json")


def _restore_flat_evidence(
    raw_semantic: str,
    fingerprint: str,
    record_id: str,
    *,
    expected_schema: str,
    expected_keys: set[str],
    expected_type: type,
    fingerprint_name: str,
    id_name: str,
    semantic_function,
):
    try:
        document = _canonical_document(raw_semantic)
        _require(set(document) == expected_keys, "canonical_semantic_json")
        _require(document.pop("schema_version") == expected_schema, "schema_version")
        _require(
            document.pop("rule_version") == POLICY_EVIDENCE_RULE_VERSION,
            "rule_version",
        )
        value = expected_type(**document)
        _validate_restored(
            value,
            raw_semantic,
            fingerprint,
            record_id,
            fingerprint_name,
            id_name,
            semantic_function,
        )
        return value
    except PolicyEvidenceStorageError:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        raise PolicyEvidenceStorageError(
            "INVALID_POLICY_EVIDENCE_RECORD", expected_type.__name__
        ) from error


def restore_policy_issuance_evidence(
    raw_semantic: str,
    fingerprint: str,
    issuance_id: str,
) -> PolicyIssuanceEvidence:
    return _restore_flat_evidence(
        raw_semantic,
        fingerprint,
        issuance_id,
        expected_schema=POLICY_ISSUANCE_SCHEMA_VERSION,
        expected_keys={
            "authority_root_fingerprint",
            "authority_root_id",
            "company_id",
            "company_plan_id",
            "company_plan_provenance_reference",
            "owner_principal_fingerprint",
            "owner_principal_id",
            "profile_fingerprint",
            "profile_id",
            "rule_version",
            "schema_version",
        },
        expected_type=PolicyIssuanceEvidence,
        fingerprint_name="issuance_fingerprint",
        id_name="issuance_id",
        semantic_function=policy_issuance_evidence_semantic_json,
    )


def _restore_scoped_evidence(
    raw_semantic: str,
    fingerprint: str,
    record_id: str,
    *,
    expected_schema: str,
    expected_keys: set[str],
    expected_type: type,
    fingerprint_name: str,
    id_name: str,
    semantic_function,
    enum_fields: dict[str, type[StrEnum]] | None = None,
):
    try:
        document = _canonical_document(raw_semantic)
        _require(set(document) == expected_keys, "canonical_semantic_json")
        _require(document.pop("schema_version") == expected_schema, "schema_version")
        _require(
            document.pop("rule_version") == POLICY_EVIDENCE_RULE_VERSION,
            "rule_version",
        )
        scope_document = document.pop("scope")
        scope_fingerprint = document.pop("scope_fingerprint")
        scope_id = document.pop("scope_id")
        scope = restore_authority_evidence_scope(
            canonical_json(scope_document),
            scope_fingerprint,
            scope_id,
        )
        provenance_document = document.pop("human_action_provenance")
        provenance_fingerprint = document.pop(
            "human_action_provenance_fingerprint"
        )
        provenance_id = document.pop("human_action_provenance_id")
        provenance = restore_human_action_provenance(
            canonical_json(provenance_document),
            provenance_fingerprint,
            provenance_id,
        )
        if enum_fields:
            for name, enum_type in enum_fields.items():
                document[name] = enum_type(document[name])
        value = expected_type(
            scope=scope,
            human_action_provenance=provenance,
            **document,
        )
        _validate_restored(
            value,
            raw_semantic,
            fingerprint,
            record_id,
            fingerprint_name,
            id_name,
            semantic_function,
        )
        return value
    except PolicyEvidenceStorageError:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        raise PolicyEvidenceStorageError(
            "INVALID_POLICY_EVIDENCE_RECORD", expected_type.__name__
        ) from error


_OWNER_APPROVAL_SEMANTIC_KEYS = {
    "authority_root_fingerprint",
    "authority_root_id",
    "company_id",
    "company_plan_id",
    "company_plan_provenance_reference",
    "owner_principal_fingerprint",
    "owner_principal_id",
    "human_action_provenance",
    "human_action_provenance_fingerprint",
    "human_action_provenance_id",
    "rule_version",
    "schema_version",
    "scope",
    "scope_fingerprint",
    "scope_id",
}
_WORKER_CONSENT_SEMANTIC_KEYS = {
    "authority_root_fingerprint",
    "authority_root_id",
    "company_id",
    "company_plan_id",
    "company_plan_provenance_reference",
    "consent_status",
    "human_action_provenance",
    "human_action_provenance_fingerprint",
    "human_action_provenance_id",
    "rule_version",
    "schema_version",
    "scope",
    "scope_fingerprint",
    "scope_id",
    "worker_id",
    "worker_principal_fingerprint",
    "worker_principal_id",
    "worker_registry_fingerprint",
    "worker_registry_revision",
}


def restore_owner_approval_evidence(
    raw_semantic: str,
    fingerprint: str,
    approval_id: str,
) -> OwnerApprovalEvidence:
    return _restore_scoped_evidence(
        raw_semantic,
        fingerprint,
        approval_id,
        expected_schema=OWNER_APPROVAL_SCHEMA_VERSION,
        expected_keys=_OWNER_APPROVAL_SEMANTIC_KEYS,
        expected_type=OwnerApprovalEvidence,
        fingerprint_name="approval_fingerprint",
        id_name="approval_id",
        semantic_function=owner_approval_evidence_semantic_json,
    )


def restore_worker_consent_evidence(
    raw_semantic: str,
    fingerprint: str,
    consent_id: str,
) -> WorkerConsentEvidence:
    return _restore_scoped_evidence(
        raw_semantic,
        fingerprint,
        consent_id,
        expected_schema=WORKER_CONSENT_SCHEMA_VERSION,
        expected_keys=_WORKER_CONSENT_SEMANTIC_KEYS,
        expected_type=WorkerConsentEvidence,
        fingerprint_name="consent_fingerprint",
        id_name="consent_id",
        semantic_function=worker_consent_evidence_semantic_json,
        enum_fields={"consent_status": RecordedConsentStatus},
    )


def serialize_policy_evidence(value: Any) -> str:
    supported = (
        CompanyPolicyProfile,
        PolicyIssuanceEvidence,
        OwnerApprovalEvidence,
        WorkerConsentEvidence,
        AuthorityEvidenceScope,
        HumanActionCaptureRoot,
        HumanActionProvenance,
    )
    _require(type(value) in supported and replace(value) == value, "evidence")
    if type(value) is CompanyPolicyProfile:
        semantic = json.loads(company_policy_profile_semantic_json(value))
        semantic.update(
            {
                "profile_fingerprint": value.profile_fingerprint,
                "profile_id": value.profile_id,
            }
        )
    elif type(value) is PolicyIssuanceEvidence:
        semantic = json.loads(policy_issuance_evidence_semantic_json(value))
        semantic.update(
            {
                "issuance_fingerprint": value.issuance_fingerprint,
                "issuance_id": value.issuance_id,
            }
        )
    elif type(value) is OwnerApprovalEvidence:
        semantic = json.loads(owner_approval_evidence_semantic_json(value))
        semantic.update(
            {
                "approval_fingerprint": value.approval_fingerprint,
                "approval_id": value.approval_id,
            }
        )
    elif type(value) is WorkerConsentEvidence:
        semantic = json.loads(worker_consent_evidence_semantic_json(value))
        semantic.update(
            {
                "consent_fingerprint": value.consent_fingerprint,
                "consent_id": value.consent_id,
            }
        )
    elif type(value) is AuthorityEvidenceScope:
        semantic = json.loads(authority_evidence_scope_semantic_json(value))
        semantic.update(
            {
                "scope_fingerprint": value.scope_fingerprint,
                "scope_id": value.scope_id,
            }
        )
    elif type(value) is HumanActionCaptureRoot:
        semantic = json.loads(human_action_capture_root_semantic_json(value))
        semantic.update(
            {
                "capture_key_id": value.capture_key_id,
                "capture_root_fingerprint": value.capture_root_fingerprint,
                "capture_root_id": value.capture_root_id,
            }
        )
    else:
        semantic = json.loads(human_action_provenance_semantic_json(value))
        semantic.update(
            {
                "provenance_fingerprint": value.provenance_fingerprint,
                "provenance_id": value.provenance_id,
            }
        )
    return canonical_json(semantic)


__all__ = [
    "AUTHORITY_EVIDENCE_SCOPE_SCHEMA_VERSION",
    "COMPANY_POLICY_PROFILE_SCHEMA_VERSION",
    "COMPANY_POLICY_GOVERNANCE_REFERENCE",
    "COMPANY_POLICY_RULE_VERSION",
    "HUMAN_ACTION_CAPTURE_RULE_VERSION",
    "HUMAN_ACTION_CAPTURE_ROOT_SCHEMA_VERSION",
    "HUMAN_ACTION_PROVENANCE_SCHEMA_VERSION",
    "MAX_ADDITIONAL_INTERNAL_LABOR_COST_EUR",
    "OWNER_APPROVAL_SCHEMA_VERSION",
    "POLICY_EVIDENCE_RULE_VERSION",
    "POLICY_ISSUANCE_SCHEMA_VERSION",
    "WORKER_CONSENT_SCHEMA_VERSION",
    "AuthorityEvidenceScope",
    "CompanyPolicyProfile",
    "EvidenceScopeKind",
    "EvidenceSubjectType",
    "HumanActionKind",
    "HumanActionCaptureProvisioningSource",
    "HumanActionCaptureRoot",
    "HumanActionProvenance",
    "OwnerApprovalEvidence",
    "PolicyEvidenceBindingError",
    "PolicyEvidenceConflictError",
    "PolicyEvidenceStorageError",
    "PolicyEvidenceValidationError",
    "PolicyIssuanceEvidence",
    "PolicyIssuanceResolution",
    "PolicyIssuanceStatus",
    "RecordedConsentStatus",
    "WorkerConsentEvidence",
    "authority_evidence_scope_semantic_json",
    "company_policy_payload_v1",
    "company_policy_profile_semantic_json",
    "human_action_provenance_semantic_json",
    "human_action_provenance_unsigned_json",
    "human_action_capture_root_semantic_json",
    "owner_approval_evidence_semantic_json",
    "policy_issuance_evidence_semantic_json",
    "restore_authority_evidence_scope",
    "restore_company_policy_profile",
    "restore_human_action_provenance",
    "restore_human_action_capture_root",
    "restore_owner_approval_evidence",
    "restore_policy_issuance_evidence",
    "restore_worker_consent_evidence",
    "serialize_policy_evidence",
    "verify_human_action_provenance",
    "worker_consent_evidence_semantic_json",
]
