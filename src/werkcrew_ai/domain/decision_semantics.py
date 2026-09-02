"""Deterministic decision semantics frozen by Constitution v1.1.

The module deliberately keeps truth, authority, and execution as separate
inputs and outputs. It does not replace the existing M6/M7 owner-decision
models: approving a plan is not automatically a semantic override.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DataQuality(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    STALE = "STALE"
    CONFLICTING = "CONFLICTING"


@dataclass(frozen=True, slots=True)
class ScopedFactQuality:
    """Quality of one fact, explicitly scoped to dependent actions."""

    fact_id: str
    quality: DataQuality
    dependent_actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DataScopeAssessment:
    action_id: str
    critical_data_quality: DataQuality
    blocking_fact_ids: tuple[str, ...]

    @property
    def blocked(self) -> bool:
        return self.critical_data_quality is not DataQuality.SUFFICIENT


_DATA_QUALITY_PRECEDENCE = {
    DataQuality.SUFFICIENT: 0,
    DataQuality.INSUFFICIENT_DATA: 1,
    DataQuality.STALE: 2,
    DataQuality.CONFLICTING: 3,
}


def assess_data_scope(
    action_id: str,
    facts: tuple[ScopedFactQuality, ...],
) -> DataScopeAssessment:
    """Assess only facts that explicitly declare the action as dependent."""

    relevant = tuple(fact for fact in facts if action_id in fact.dependent_actions)
    blocking = tuple(
        fact.fact_id for fact in relevant if fact.quality is not DataQuality.SUFFICIENT
    )
    quality = max(
        (fact.quality for fact in relevant),
        key=_DATA_QUALITY_PRECEDENCE.__getitem__,
        default=DataQuality.SUFFICIENT,
    )
    return DataScopeAssessment(
        action_id=action_id,
        critical_data_quality=quality,
        blocking_fact_ids=blocking,
    )


class TechnicalFeasibility(StrEnum):
    FEASIBLE = "FEASIBLE"
    CONDITIONAL = "CONDITIONAL"
    INFEASIBLE = "INFEASIBLE"
    UNKNOWN = "UNKNOWN"


class PolicyRelation(StrEnum):
    WITHIN_ENVELOPE = "WITHIN_ENVELOPE"
    SOFT_EXCEPTION = "SOFT_EXCEPTION"
    HARD_BLOCK = "HARD_BLOCK"


class OverrideStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ExecutionAuthorization(StrEnum):
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"


class PromiseState(StrEnum):
    SAFE = "SAFE"
    CONDITIONAL = "CONDITIONAL"
    INFEASIBLE = "INFEASIBLE"
    FORCED = "FORCED"


class Disposition(StrEnum):
    EXECUTE = "EXECUTE"
    EXECUTE_WITH_RECORDED_RISK = "EXECUTE_WITH_RECORDED_RISK"
    ABSTAIN = "ABSTAIN"
    FORBIDDEN = "FORBIDDEN"
    HALT = "HALT"


class SafetyStatus(StrEnum):
    CLEAR = "CLEAR"
    UNVERIFIED_CONCERN = "UNVERIFIED_CONCERN"
    HARD_BLOCK = "HARD_BLOCK"


class PermissionStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    VERIFIED = "VERIFIED"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"


class ConsentStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    REQUESTED = "REQUESTED"
    FREELY_GIVEN = "FREELY_GIVEN"
    DECLINED = "DECLINED"
    PRESSURED = "PRESSURED"
    ABSENT = "ABSENT"


@dataclass(frozen=True, slots=True)
class PromiseAssessment:
    technical_feasibility: TechnicalFeasibility
    policy_relation: PolicyRelation
    override_status: OverrideStatus
    critical_data_quality: DataQuality
    safety_status: SafetyStatus
    permission_status: PermissionStatus
    consent_status: ConsentStatus
    execution_authorization: ExecutionAuthorization
    derived_state: PromiseState | None
    disposition: Disposition
    reason_codes: tuple[str, ...]


def _promise_result(
    *,
    feasibility: TechnicalFeasibility,
    policy_relation: PolicyRelation,
    override_status: OverrideStatus,
    critical_data_quality: DataQuality,
    safety_status: SafetyStatus,
    permission_status: PermissionStatus,
    consent_status: ConsentStatus,
    state: PromiseState | None,
    authorization: ExecutionAuthorization,
    disposition: Disposition,
    reasons: tuple[str, ...] = (),
) -> PromiseAssessment:
    return PromiseAssessment(
        technical_feasibility=feasibility,
        policy_relation=policy_relation,
        override_status=override_status,
        critical_data_quality=critical_data_quality,
        safety_status=safety_status,
        permission_status=permission_status,
        consent_status=consent_status,
        execution_authorization=authorization,
        derived_state=state,
        disposition=disposition,
        reason_codes=reasons,
    )


def assess_promise(
    *,
    feasibility: TechnicalFeasibility,
    policy_relation: PolicyRelation,
    override_status: OverrideStatus,
    critical_data_quality: DataQuality,
    safety_status: SafetyStatus = SafetyStatus.CLEAR,
    permission_status: PermissionStatus = PermissionStatus.NOT_REQUIRED,
    consent_status: ConsentStatus = ConsentStatus.NOT_REQUIRED,
) -> PromiseAssessment:
    """Project independent facts and authority onto execution semantics.

    ``FORCED`` is emitted only for a feasible, approved soft exception with
    sufficient critical data and every safety/permission/consent gate met.
    """

    common = {
        "feasibility": feasibility,
        "policy_relation": policy_relation,
        "override_status": override_status,
        "critical_data_quality": critical_data_quality,
        "safety_status": safety_status,
        "permission_status": permission_status,
        "consent_status": consent_status,
    }

    hard_reasons: list[str] = []
    if policy_relation is PolicyRelation.HARD_BLOCK:
        hard_reasons.append("POLICY_HARD_BLOCK")
    if safety_status is SafetyStatus.HARD_BLOCK:
        hard_reasons.append("HARD_SAFETY_BLOCK")
    if permission_status is PermissionStatus.MISSING:
        hard_reasons.append("REQUIRED_PERMISSION_MISSING")
    if hard_reasons:
        return _promise_result(
            **common,
            state=PromiseState.INFEASIBLE,
            authorization=ExecutionAuthorization.BLOCKED,
            disposition=Disposition.FORBIDDEN,
            reasons=tuple(hard_reasons),
        )

    if critical_data_quality is not DataQuality.SUFFICIENT:
        return _promise_result(
            **common,
            state=None,
            authorization=ExecutionAuthorization.BLOCKED,
            disposition=Disposition.ABSTAIN,
            reasons=(f"CRITICAL_DATA_{critical_data_quality.value}",),
        )

    if safety_status is SafetyStatus.UNVERIFIED_CONCERN:
        return _promise_result(
            **common,
            state=PromiseState.CONDITIONAL,
            authorization=ExecutionAuthorization.BLOCKED,
            disposition=Disposition.ABSTAIN,
            reasons=("SAFETY_OR_TECHNICAL_CONCERN_REQUIRES_VERIFICATION",),
        )

    if permission_status is PermissionStatus.UNKNOWN:
        return _promise_result(
            **common,
            state=None,
            authorization=ExecutionAuthorization.BLOCKED,
            disposition=Disposition.ABSTAIN,
            reasons=("REQUIRED_PERMISSION_UNKNOWN",),
        )

    if consent_status not in {ConsentStatus.NOT_REQUIRED, ConsentStatus.FREELY_GIVEN}:
        return _promise_result(
            **common,
            state=PromiseState.CONDITIONAL,
            authorization=ExecutionAuthorization.BLOCKED,
            disposition=Disposition.ABSTAIN,
            reasons=(f"REQUIRED_CONSENT_{consent_status.value}",),
        )

    if feasibility is TechnicalFeasibility.UNKNOWN:
        return _promise_result(
            **common,
            state=None,
            authorization=ExecutionAuthorization.BLOCKED,
            disposition=Disposition.ABSTAIN,
            reasons=("FEASIBILITY_UNKNOWN",),
        )

    if feasibility is TechnicalFeasibility.INFEASIBLE:
        return _promise_result(
            **common,
            state=PromiseState.INFEASIBLE,
            authorization=ExecutionAuthorization.BLOCKED,
            disposition=Disposition.ABSTAIN,
            reasons=("TECHNICALLY_INFEASIBLE",),
        )

    if feasibility is TechnicalFeasibility.CONDITIONAL:
        return _promise_result(
            **common,
            state=PromiseState.CONDITIONAL,
            authorization=ExecutionAuthorization.BLOCKED,
            disposition=Disposition.ABSTAIN,
            reasons=("NAMED_CONDITION_OUTSTANDING",),
        )

    if policy_relation is PolicyRelation.WITHIN_ENVELOPE:
        return _promise_result(
            **common,
            state=PromiseState.SAFE,
            authorization=ExecutionAuthorization.ALLOWED,
            disposition=Disposition.EXECUTE,
        )

    if override_status is OverrideStatus.APPROVED:
        return _promise_result(
            **common,
            state=PromiseState.FORCED,
            authorization=ExecutionAuthorization.ALLOWED,
            disposition=Disposition.EXECUTE_WITH_RECORDED_RISK,
            reasons=("OWNER_APPROVED_SOFT_EXCEPTION",),
        )

    reason = (
        "SOFT_EXCEPTION_REJECTED"
        if override_status is OverrideStatus.REJECTED
        else "SOFT_EXCEPTION_REQUIRES_OWNER"
    )
    return _promise_result(
        **common,
        state=PromiseState.CONDITIONAL,
        authorization=ExecutionAuthorization.BLOCKED,
        disposition=Disposition.ABSTAIN,
        reasons=(reason,),
    )


class HealthAssessment(StrEnum):
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    HEALTHY = "HEALTHY"
    LOAD_TIGHT = "LOAD_TIGHT"
    STRAINED = "STRAINED"
    UNSTABLE = "UNSTABLE"


class RecoverySignal(StrEnum):
    NONE = "NONE"
    RECOVERY_NEEDED = "RECOVERY_NEEDED"


class OwnerRecoveryDecision(StrEnum):
    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING = "PENDING"
    ACTIVATED = "ACTIVATED"
    DECLINED = "DECLINED"


class RecoveryControlState(StrEnum):
    INACTIVE = "INACTIVE"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class OwnerPosture(StrEnum):
    STABILITY = "STABILITY"
    GROWTH = "GROWTH"
    PEAK = "PEAK"
    RECOVERY_POSTURE = "RECOVERY_POSTURE"


@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    health_assessment: HealthAssessment
    recovery_signal: RecoverySignal
    owner_recovery_decision: OwnerRecoveryDecision
    recovery_control_state: RecoveryControlState
    owner_posture: OwnerPosture


def assess_recovery(
    *,
    health_assessment: HealthAssessment,
    recovery_signal: RecoverySignal,
    owner_recovery_decision: OwnerRecoveryDecision,
    owner_posture: OwnerPosture,
) -> RecoveryAssessment:
    """Preserve observed health independently from the owner's response."""

    control_state = (
        RecoveryControlState.RECOVERY_REQUIRED
        if owner_recovery_decision is OwnerRecoveryDecision.ACTIVATED
        else RecoveryControlState.INACTIVE
    )
    return RecoveryAssessment(
        health_assessment=health_assessment,
        recovery_signal=recovery_signal,
        owner_recovery_decision=owner_recovery_decision,
        recovery_control_state=control_state,
        owner_posture=owner_posture,
    )


class ObjectionType(StrEnum):
    PREFERENCE = "PREFERENCE"
    FACT_CORRECTION = "FACT_CORRECTION"
    PLAN_UNWORKABLE = "PLAN_UNWORKABLE"
    AVAILABILITY_LIMIT = "AVAILABILITY_LIMIT"
    SAFETY_OR_TECHNICAL_CONCERN = "SAFETY_OR_TECHNICAL_CONCERN"
    HARD_SAFETY_BLOCK = "HARD_SAFETY_BLOCK"


@dataclass(frozen=True, slots=True)
class Objection:
    objection_type: ObjectionType
    actor_id: str
    actor_role: str
    scope: str


@dataclass(frozen=True, slots=True)
class ObjectionAssessment:
    disposition: Disposition
    requires_reassessment: bool
    reason_code: str


def assess_objection(objection: Objection | ObjectionType) -> ObjectionAssessment:
    """Classify objection content; actor identity and rank never change it."""

    objection_type = (
        objection.objection_type if isinstance(objection, Objection) else objection
    )
    if objection_type is ObjectionType.PREFERENCE:
        return ObjectionAssessment(
            Disposition.EXECUTE,
            False,
            "PREFERENCE_RECORDED_NO_VETO",
        )
    if objection_type is ObjectionType.HARD_SAFETY_BLOCK:
        return ObjectionAssessment(
            Disposition.FORBIDDEN,
            False,
            "HARD_SAFETY_BLOCK",
        )
    if objection_type is ObjectionType.AVAILABILITY_LIMIT:
        return ObjectionAssessment(
            Disposition.ABSTAIN,
            False,
            "CONSENT_REQUIRED",
        )
    return ObjectionAssessment(
        Disposition.ABSTAIN,
        True,
        objection_type.value,
    )


class ClaimEvidence(StrEnum):
    VERIFIED = "VERIFIED"
    CONDITIONAL = "CONDITIONAL"
    UNKNOWN = "UNKNOWN"
    FALSE = "FALSE"


class ClaimRendering(StrEnum):
    FACT = "FACT"
    CONDITION = "CONDITION"
    ESTIMATE = "ESTIMATE"
    GUARANTEE = "GUARANTEE"


class CommunicationKind(StrEnum):
    DRAFT_TEXT = "DRAFT_TEXT"
    APPROVED_TEXT = "APPROVED_TEXT"
    SENT_MESSAGE = "SENT_MESSAGE"
    ACTUAL_CLAIM = "ACTUAL_CLAIM"


class SpeechAct(StrEnum):
    FIELD_TALK = "FIELD_TALK"
    UNCONFIRMED_BINDING_CLAIM = "UNCONFIRMED_BINDING_CLAIM"
    INTENT = "INTENT"
    WINDOW = "WINDOW"
    PROMISE = "PROMISE"


@dataclass(frozen=True, slots=True)
class CommunicationRecord:
    kind: CommunicationKind
    text_hash: str
    approved_by_system: bool

    def __post_init__(self) -> None:
        if self.kind in {CommunicationKind.DRAFT_TEXT, CommunicationKind.ACTUAL_CLAIM}:
            if self.approved_by_system:
                raise ValueError(f"{self.kind.value} cannot be system-approved")
        elif not self.approved_by_system:
            raise ValueError(f"{self.kind.value} requires deterministic approval")


@dataclass(frozen=True, slots=True)
class VoiceClaim:
    claim_id: str
    evidence: ClaimEvidence
    rendering: ClaimRendering
    material_to_recipient: bool = True
    named_condition: str | None = None


@dataclass(frozen=True, slots=True)
class ActualClaim:
    claim_id: str
    speaker_role: str
    speech_act: SpeechAct
    communication: CommunicationRecord
    contradiction_fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VoiceAssessment:
    approved: bool
    reason_codes: tuple[str, ...]
    approved_text: CommunicationRecord | None


def draft_text(text_hash: str) -> CommunicationRecord:
    return CommunicationRecord(
        kind=CommunicationKind.DRAFT_TEXT,
        text_hash=text_hash,
        approved_by_system=False,
    )


def validate_voice(
    draft: CommunicationRecord,
    claims: tuple[VoiceClaim, ...],
    *,
    promise_state: PromiseState | None,
    critical_data_quality: DataQuality,
    contains_material_omission: bool = False,
    contains_false_certainty: bool = False,
    misrepresents_consent: bool = False,
    misrepresents_authority: bool = False,
) -> VoiceAssessment:
    """Apply NO_DECEPTION before a draft can become approved text."""

    reasons: list[str] = []
    if draft.kind is not CommunicationKind.DRAFT_TEXT:
        reasons.append("VOICE_INPUT_NOT_DRAFT_TEXT")
    if contains_material_omission:
        reasons.append("MATERIAL_OMISSION")
    if contains_false_certainty:
        reasons.append("FALSE_CERTAINTY")
    if misrepresents_consent:
        reasons.append("FALSE_CONSENT")
    if misrepresents_authority:
        reasons.append("FALSE_AUTHORITY")

    for claim in claims:
        if not claim.material_to_recipient:
            continue
        if claim.evidence is ClaimEvidence.FALSE:
            reasons.append(f"{claim.claim_id}:FALSE_CLAIM")
        if claim.rendering is ClaimRendering.FACT and claim.evidence is not ClaimEvidence.VERIFIED:
            reasons.append(f"{claim.claim_id}:UNVERIFIED_AS_FACT")
        if claim.rendering is ClaimRendering.GUARANTEE and (
            claim.evidence is not ClaimEvidence.VERIFIED
            or promise_state is not PromiseState.SAFE
            or critical_data_quality is not DataQuality.SUFFICIENT
        ):
            reasons.append(f"{claim.claim_id}:UNSUPPORTED_GUARANTEE")
        if claim.evidence in {ClaimEvidence.CONDITIONAL, ClaimEvidence.UNKNOWN} and (
            claim.rendering is not ClaimRendering.CONDITION or not claim.named_condition
        ):
            reasons.append(f"{claim.claim_id}:CONDITION_NOT_NAMED")

    reason_codes = tuple(dict.fromkeys(reasons))
    approved_text = None
    if not reason_codes:
        approved_text = CommunicationRecord(
            kind=CommunicationKind.APPROVED_TEXT,
            text_hash=draft.text_hash,
            approved_by_system=True,
        )
    return VoiceAssessment(
        approved=not reason_codes,
        reason_codes=reason_codes,
        approved_text=approved_text,
    )


def mark_message_sent(approved_text: CommunicationRecord) -> CommunicationRecord:
    if approved_text.kind is not CommunicationKind.APPROVED_TEXT:
        raise ValueError("Only APPROVED_TEXT can become SENT_MESSAGE")
    return CommunicationRecord(
        kind=CommunicationKind.SENT_MESSAGE,
        text_hash=approved_text.text_hash,
        approved_by_system=True,
    )


def record_actual_claim(
    *,
    claim_id: str,
    speaker_role: str,
    speech_act: SpeechAct,
    text_hash: str,
    contradiction_fact_ids: tuple[str, ...] = (),
) -> ActualClaim:
    """Record a human statement without presenting it as system approval."""

    return ActualClaim(
        claim_id=claim_id,
        speaker_role=speaker_role,
        speech_act=speech_act,
        communication=CommunicationRecord(
            kind=CommunicationKind.ACTUAL_CLAIM,
            text_hash=text_hash,
            approved_by_system=False,
        ),
        contradiction_fact_ids=contradiction_fact_ids,
    )
