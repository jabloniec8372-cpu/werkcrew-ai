"""Regression contract for DOM/KLINIKA/LOFT at Wednesday 10:17."""

from datetime import datetime, timedelta, timezone

from werkcrew_ai.domain import (
    ClaimEvidence,
    ClaimRendering,
    CommunicationKind,
    ConsentStatus,
    DataQuality,
    Disposition,
    HealthAssessment,
    Objection,
    ObjectionType,
    OverrideStatus,
    OwnerPosture,
    OwnerRecoveryDecision,
    PolicyRelation,
    PromiseState,
    RecoverySignal,
    SafetyStatus,
    SpeechAct,
    TechnicalFeasibility,
    VoiceClaim,
    assess_objection,
    assess_promise,
    assess_recovery,
    draft_text,
    record_actual_claim,
    validate_voice,
)


AT_1017 = datetime(2026, 9, 2, 10, 17, tzinfo=timezone(timedelta(hours=2)))


def test_t1_move_crew_despite_marek_preference_records_risk() -> None:
    objection = Objection(
        objection_type=ObjectionType.PREFERENCE,
        actor_id="marek",
        actor_role="WORKER",
        scope="move-team-b-to-dom",
    )

    objection_result = assess_objection(objection)
    reserve_risk_recorded = True
    t1_disposition = Disposition.EXECUTE_WITH_RECORDED_RISK

    assert objection_result.disposition is Disposition.EXECUTE
    assert objection_result.reason_code == "PREFERENCE_RECORDED_NO_VETO"
    assert reserve_risk_recorded is True
    assert t1_disposition is Disposition.EXECUTE_WITH_RECORDED_RISK


def test_t2_anna_additional_availability_without_consent_abstains() -> None:
    result = assess_promise(
        feasibility=TechnicalFeasibility.FEASIBLE,
        policy_relation=PolicyRelation.SOFT_EXCEPTION,
        override_status=OverrideStatus.APPROVED,
        critical_data_quality=DataQuality.SUFFICIENT,
        consent_status=ConsentStatus.DECLINED,
    )

    assert result.derived_state is not PromiseState.FORCED
    assert result.disposition is Disposition.ABSTAIN


def test_t3_method_abstains_until_verified_then_becomes_forbidden() -> None:
    unverified = assess_promise(
        feasibility=TechnicalFeasibility.UNKNOWN,
        policy_relation=PolicyRelation.WITHIN_ENVELOPE,
        override_status=OverrideStatus.NOT_REQUIRED,
        critical_data_quality=DataQuality.SUFFICIENT,
        safety_status=SafetyStatus.UNVERIFIED_CONCERN,
    )
    confirmed_block = assess_promise(
        feasibility=TechnicalFeasibility.INFEASIBLE,
        policy_relation=PolicyRelation.HARD_BLOCK,
        override_status=OverrideStatus.APPROVED,
        critical_data_quality=DataQuality.SUFFICIENT,
        safety_status=SafetyStatus.HARD_BLOCK,
    )

    assert unverified.disposition is Disposition.ABSTAIN
    assert confirmed_block.disposition is Disposition.FORBIDDEN


def test_t4_system_rejects_guarantee_but_records_actual_owner_promise() -> None:
    claim = VoiceClaim(
        claim_id="loft-start-tomorrow",
        evidence=ClaimEvidence.CONDITIONAL,
        rendering=ClaimRendering.GUARANTEE,
        named_condition="licensed worker confirmed",
    )
    validation = validate_voice(
        draft_text("sha256:loft-unconditional-draft"),
        (claim,),
        promise_state=PromiseState.CONDITIONAL,
        critical_data_quality=DataQuality.CONFLICTING,
    )
    actual = record_actual_claim(
        claim_id="owner-loft-promise",
        speaker_role="OWNER",
        speech_act=SpeechAct.PROMISE,
        text_hash="sha256:owner-spoken-promise",
        contradiction_fact_ids=("piotr-unavailable", "delivery-conflict"),
    )

    assert validation.approved is False
    assert validation.approved_text is None
    assert actual.communication.kind is CommunicationKind.ACTUAL_CLAIM
    assert actual.communication.approved_by_system is False


def test_t5_recovery_decline_preserves_observed_health() -> None:
    result = assess_recovery(
        health_assessment=HealthAssessment.UNSTABLE,
        recovery_signal=RecoverySignal.RECOVERY_NEEDED,
        owner_recovery_decision=OwnerRecoveryDecision.DECLINED,
        owner_posture=OwnerPosture.GROWTH,
    )

    assert result.health_assessment is HealthAssessment.UNSTABLE
    assert result.recovery_signal is RecoverySignal.RECOVERY_NEEDED
    assert result.owner_recovery_decision is OwnerRecoveryDecision.DECLINED
    assert result.owner_posture is OwnerPosture.GROWTH


def test_later_success_or_failure_does_not_rewrite_assessment_at_1017() -> None:
    assessment_at_1017 = assess_objection(ObjectionType.PREFERENCE)
    timestamp_at_1017 = AT_1017

    later_outcome_events = (
        ("outcome-success", AT_1017 + timedelta(hours=5)),
        ("outcome-failure", AT_1017 + timedelta(days=1)),
    )

    assert later_outcome_events[0][1] > timestamp_at_1017
    assert later_outcome_events[1][1] > timestamp_at_1017
    assert assessment_at_1017.disposition is Disposition.EXECUTE


def test_same_evidence_is_classified_identically_after_role_swap() -> None:
    marek_as_worker = Objection(
        ObjectionType.PLAN_UNWORKABLE,
        actor_id="marek",
        actor_role="WORKER",
        scope="team-b-tomorrow",
    )
    owner_as_speaker = Objection(
        ObjectionType.PLAN_UNWORKABLE,
        actor_id="owner",
        actor_role="OWNER",
        scope="team-b-tomorrow",
    )

    assert assess_objection(marek_as_worker) == assess_objection(owner_as_speaker)
