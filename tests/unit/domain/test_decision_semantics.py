"""Blocking tests for Constitution v1.1 and Decision Semantics Freeze."""

import pytest

from werkcrew_ai.domain import (
    ClaimEvidence,
    ClaimRendering,
    CommunicationKind,
    ConsentStatus,
    DataQuality,
    Disposition,
    ExecutionAuthorization,
    HealthAssessment,
    Objection,
    ObjectionType,
    OverrideStatus,
    OwnerPosture,
    OwnerRecoveryDecision,
    PermissionStatus,
    PolicyRelation,
    PromiseState,
    RecoveryControlState,
    RecoverySignal,
    SafetyStatus,
    ScopedFactQuality,
    SpeechAct,
    TechnicalFeasibility,
    VoiceClaim,
    assess_data_scope,
    assess_objection,
    assess_promise,
    assess_recovery,
    draft_text,
    mark_message_sent,
    record_actual_claim,
    validate_voice,
)


def _promise(**overrides):
    values = {
        "feasibility": TechnicalFeasibility.FEASIBLE,
        "policy_relation": PolicyRelation.WITHIN_ENVELOPE,
        "override_status": OverrideStatus.NOT_REQUIRED,
        "critical_data_quality": DataQuality.SUFFICIENT,
    }
    values.update(overrides)
    return assess_promise(**values)


def test_safe_keeps_primary_fields_and_allows_execution() -> None:
    result = _promise()

    assert result.technical_feasibility is TechnicalFeasibility.FEASIBLE
    assert result.policy_relation is PolicyRelation.WITHIN_ENVELOPE
    assert result.override_status is OverrideStatus.NOT_REQUIRED
    assert result.derived_state is PromiseState.SAFE
    assert result.execution_authorization is ExecutionAuthorization.ALLOWED
    assert result.disposition is Disposition.EXECUTE


def test_forced_is_only_approved_feasible_soft_exception() -> None:
    result = _promise(
        policy_relation=PolicyRelation.SOFT_EXCEPTION,
        override_status=OverrideStatus.APPROVED,
    )

    assert result.derived_state is PromiseState.FORCED
    assert result.execution_authorization is ExecutionAuthorization.ALLOWED
    assert result.disposition is Disposition.EXECUTE_WITH_RECORDED_RISK


@pytest.mark.parametrize(
    ("feasibility", "expected_state"),
    [
        (TechnicalFeasibility.INFEASIBLE, PromiseState.INFEASIBLE),
        (TechnicalFeasibility.UNKNOWN, None),
    ],
)
def test_override_cannot_repaint_infeasible_or_unknown(
    feasibility: TechnicalFeasibility,
    expected_state: PromiseState | None,
) -> None:
    result = _promise(
        feasibility=feasibility,
        policy_relation=PolicyRelation.SOFT_EXCEPTION,
        override_status=OverrideStatus.APPROVED,
    )

    assert result.derived_state is expected_state
    assert result.execution_authorization is ExecutionAuthorization.BLOCKED
    assert result.disposition is Disposition.ABSTAIN


@pytest.mark.parametrize(
    "quality",
    [DataQuality.INSUFFICIENT_DATA, DataQuality.STALE, DataQuality.CONFLICTING],
)
def test_override_cannot_mask_bad_critical_data(quality: DataQuality) -> None:
    result = _promise(
        policy_relation=PolicyRelation.SOFT_EXCEPTION,
        override_status=OverrideStatus.APPROVED,
        critical_data_quality=quality,
    )

    assert result.derived_state is None
    assert result.execution_authorization is ExecutionAuthorization.BLOCKED
    assert result.disposition is Disposition.ABSTAIN


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"policy_relation": PolicyRelation.HARD_BLOCK}, "POLICY_HARD_BLOCK"),
        ({"safety_status": SafetyStatus.HARD_BLOCK}, "HARD_SAFETY_BLOCK"),
        (
            {"permission_status": PermissionStatus.MISSING},
            "REQUIRED_PERMISSION_MISSING",
        ),
    ],
)
def test_known_hard_boundaries_are_forbidden(overrides: dict, reason: str) -> None:
    result = _promise(
        **overrides,
        override_status=OverrideStatus.APPROVED,
    )

    assert result.derived_state is PromiseState.INFEASIBLE
    assert result.execution_authorization is ExecutionAuthorization.BLOCKED
    assert result.disposition is Disposition.FORBIDDEN
    assert reason in result.reason_codes


def test_unverified_safety_concern_abstains_until_checked() -> None:
    result = _promise(safety_status=SafetyStatus.UNVERIFIED_CONCERN)

    assert result.derived_state is PromiseState.CONDITIONAL
    assert result.disposition is Disposition.ABSTAIN


@pytest.mark.parametrize(
    "consent",
    [
        ConsentStatus.REQUESTED,
        ConsentStatus.DECLINED,
        ConsentStatus.PRESSURED,
        ConsentStatus.ABSENT,
    ],
)
def test_required_consent_must_be_freely_given(consent: ConsentStatus) -> None:
    result = _promise(
        policy_relation=PolicyRelation.SOFT_EXCEPTION,
        override_status=OverrideStatus.APPROVED,
        consent_status=consent,
    )

    assert result.derived_state is not PromiseState.FORCED
    assert result.execution_authorization is ExecutionAuthorization.BLOCKED
    assert result.disposition is Disposition.ABSTAIN


def test_unknown_permission_abstains_but_known_missing_permission_forbids() -> None:
    unknown = _promise(permission_status=PermissionStatus.UNKNOWN)
    missing = _promise(permission_status=PermissionStatus.MISSING)

    assert unknown.disposition is Disposition.ABSTAIN
    assert missing.disposition is Disposition.FORBIDDEN


def test_soft_exception_waits_for_owner_without_becoming_forced() -> None:
    result = _promise(
        policy_relation=PolicyRelation.SOFT_EXCEPTION,
        override_status=OverrideStatus.PENDING,
    )

    assert result.derived_state is PromiseState.CONDITIONAL
    assert result.execution_authorization is ExecutionAuthorization.BLOCKED
    assert result.disposition is Disposition.ABSTAIN


def test_data_gap_blocks_only_declared_dependent_action() -> None:
    facts = (
        ScopedFactQuality(
            fact_id="gps-team-b",
            quality=DataQuality.INSUFFICIENT_DATA,
            dependent_actions=("route-team-b",),
        ),
        ScopedFactQuality(
            fact_id="dom-cover-on-site",
            quality=DataQuality.SUFFICIENT,
            dependent_actions=("protect-dom",),
        ),
    )

    route = assess_data_scope("route-team-b", facts)
    protect_dom = assess_data_scope("protect-dom", facts)

    assert route.blocked is True
    assert route.blocking_fact_ids == ("gps-team-b",)
    assert protect_dom.blocked is False


def test_declining_recovery_does_not_repaint_company_health() -> None:
    result = assess_recovery(
        health_assessment=HealthAssessment.UNSTABLE,
        recovery_signal=RecoverySignal.RECOVERY_NEEDED,
        owner_recovery_decision=OwnerRecoveryDecision.DECLINED,
        owner_posture=OwnerPosture.GROWTH,
    )

    assert result.health_assessment is HealthAssessment.UNSTABLE
    assert result.recovery_signal is RecoverySignal.RECOVERY_NEEDED
    assert result.owner_recovery_decision is OwnerRecoveryDecision.DECLINED
    assert result.recovery_control_state is RecoveryControlState.INACTIVE
    assert result.owner_posture is OwnerPosture.GROWTH


def test_recovery_required_only_appears_after_owner_activation() -> None:
    pending = assess_recovery(
        health_assessment=HealthAssessment.STRAINED,
        recovery_signal=RecoverySignal.RECOVERY_NEEDED,
        owner_recovery_decision=OwnerRecoveryDecision.PENDING,
        owner_posture=OwnerPosture.STABILITY,
    )
    activated = assess_recovery(
        health_assessment=HealthAssessment.STRAINED,
        recovery_signal=RecoverySignal.RECOVERY_NEEDED,
        owner_recovery_decision=OwnerRecoveryDecision.ACTIVATED,
        owner_posture=OwnerPosture.RECOVERY_POSTURE,
    )

    assert pending.recovery_control_state is RecoveryControlState.INACTIVE
    assert activated.recovery_control_state is RecoveryControlState.RECOVERY_REQUIRED


def test_preference_is_recorded_without_veto() -> None:
    result = assess_objection(ObjectionType.PREFERENCE)

    assert result.disposition is Disposition.EXECUTE
    assert result.requires_reassessment is False


@pytest.mark.parametrize(
    "objection_type",
    [ObjectionType.FACT_CORRECTION, ObjectionType.PLAN_UNWORKABLE],
)
def test_new_fact_or_unworkable_plan_requires_reassessment(
    objection_type: ObjectionType,
) -> None:
    result = assess_objection(objection_type)

    assert result.disposition is Disposition.ABSTAIN
    assert result.requires_reassessment is True


def test_same_objection_content_has_same_result_after_role_swap() -> None:
    worker = Objection(
        ObjectionType.SAFETY_OR_TECHNICAL_CONCERN,
        actor_id="anna",
        actor_role="WORKER",
        scope="loft-method",
    )
    owner = Objection(
        ObjectionType.SAFETY_OR_TECHNICAL_CONCERN,
        actor_id="owner",
        actor_role="OWNER",
        scope="loft-method",
    )

    assert assess_objection(worker) == assess_objection(owner)


def test_voice_rejects_false_fact_certainty_consent_authority_and_omission() -> None:
    claim = VoiceClaim(
        claim_id="availability",
        evidence=ClaimEvidence.FALSE,
        rendering=ClaimRendering.FACT,
    )

    result = validate_voice(
        draft_text("sha256:draft"),
        (claim,),
        promise_state=None,
        critical_data_quality=DataQuality.SUFFICIENT,
        contains_material_omission=True,
        contains_false_certainty=True,
        misrepresents_consent=True,
        misrepresents_authority=True,
    )

    assert result.approved is False
    assert result.approved_text is None
    assert "MATERIAL_OMISSION" in result.reason_codes
    assert "FALSE_CERTAINTY" in result.reason_codes
    assert "FALSE_CONSENT" in result.reason_codes
    assert "FALSE_AUTHORITY" in result.reason_codes
    assert "availability:FALSE_CLAIM" in result.reason_codes


def test_voice_rejects_guarantee_when_promise_is_not_safe() -> None:
    claim = VoiceClaim(
        claim_id="loft-start",
        evidence=ClaimEvidence.CONDITIONAL,
        rendering=ClaimRendering.GUARANTEE,
        named_condition="licensed worker confirmed",
    )

    result = validate_voice(
        draft_text("sha256:loft-draft"),
        (claim,),
        promise_state=PromiseState.FORCED,
        critical_data_quality=DataQuality.SUFFICIENT,
    )

    assert result.approved is False
    assert "loft-start:UNSUPPORTED_GUARANTEE" in result.reason_codes


def test_named_condition_can_be_approved_and_sent() -> None:
    claim = VoiceClaim(
        claim_id="loft-start",
        evidence=ClaimEvidence.CONDITIONAL,
        rendering=ClaimRendering.CONDITION,
        named_condition="licensed worker confirmed by 16:00",
    )

    result = validate_voice(
        draft_text("sha256:loft-conditional"),
        (claim,),
        promise_state=PromiseState.CONDITIONAL,
        critical_data_quality=DataQuality.SUFFICIENT,
    )

    assert result.approved is True
    assert result.approved_text is not None
    assert result.approved_text.kind is CommunicationKind.APPROVED_TEXT
    sent = mark_message_sent(result.approved_text)
    assert sent.kind is CommunicationKind.SENT_MESSAGE


def test_actual_owner_promise_is_recorded_but_never_system_approved() -> None:
    actual = record_actual_claim(
        claim_id="owner-loft-promise",
        speaker_role="OWNER",
        speech_act=SpeechAct.PROMISE,
        text_hash="sha256:actual-owner-claim",
        contradiction_fact_ids=("piotr-unavailable",),
    )

    assert actual.speech_act is SpeechAct.PROMISE
    assert actual.communication.kind is CommunicationKind.ACTUAL_CLAIM
    assert actual.communication.approved_by_system is False
    assert actual.contradiction_fact_ids == ("piotr-unavailable",)


def test_draft_cannot_be_marked_as_sent_without_approval() -> None:
    with pytest.raises(ValueError, match="Only APPROVED_TEXT"):
        mark_message_sent(draft_text("sha256:unsafe"))
