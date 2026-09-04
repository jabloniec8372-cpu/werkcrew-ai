from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone

import pytest

from werkcrew_ai.field.models import (
    ActionExceptionReason,
    AssignmentKind,
    AssignmentState,
    CompletionType,
    DeliveryEvidence,
    DirectiveClass,
    DirectiveDefinition,
    DirectiveRoot,
    DirectiveType,
    EffectType,
    EndOfDayAction,
    EvidenceItem,
    EvidenceKind,
    FieldEventEnvelope,
    FieldEventInput,
    FieldEventType,
    M2JobExecutionRoot,
    M2Policy,
    M2ReductionScope,
    PlanDayRoot,
    PlanDayStatus,
    PolicyTimeContext,
    ProblemHint,
    ProcessedEventReceipt,
    ProcessedEventLedger,
    ReductionOutcome,
    ResourceKind,
    RootKind,
    SiteProblemAction,
    StageState,
    StageStatus,
    StartExceptionReason,
    SystemSignal,
    SystemSignalType,
    TaskDefinition,
    TaskState,
    TaskStatus,
    WorkerIdentityRegistry,
    WorkStartBlockedReason,
)
from werkcrew_ai.field.reducer import reduce
from werkcrew_ai.intake import (
    CanonicalJobActivity,
    CanonicalJobLifecycle,
    M1HandoffProjection,
    M1HandoffPublication,
)


NOW = datetime(2026, 9, 4, 8, 20, tzinfo=timezone.utc)
JOB_ID = "job-m2-critical"
HANDOFF_ID = "m1h-m2-critical"
TASK_ID = "task-site-visit"
TASK_VERSION = "task-site-visit-v1"
STAGE_ID = "stage-day-one"
ASSIGNMENT_ID = "assignment-site-visit"
PLAN_DAY_ID = "plan-day-worker-one-2026-09-04"
NEXT_PLAN_DAY_ID = "plan-day-worker-one-2026-09-05"
WORKER_ID = "worker-one"
LEAD_ID = "worker-lead"
MEMBER_ID = "worker-member"


def _publication(
    *,
    job_id: str = JOB_ID,
    handoff_id: str = HANDOFF_ID,
    source_revision: int = 1,
) -> M1HandoffPublication:
    return M1HandoffPublication(
        handoff_id=handoff_id,
        projection=M1HandoffProjection(
            job_id=job_id,
            source_revision=source_revision,
            lifecycle_state=CanonicalJobLifecycle.RECEIVED,
            activity_state=CanonicalJobActivity.ACTIVE,
            facts=(),
        ),
        content_sha256="a" * 64,
        published_at=NOW - timedelta(days=1),
    )


def _plan_day(
    plan_day_id: str = PLAN_DAY_ID,
    *,
    worker_id: str = WORKER_ID,
    status: PlanDayStatus = PlanDayStatus.ISSUED,
    business_date: date = date(2026, 9, 4),
    confirmed_plan_reference: str | None = "plan-job-a",
) -> PlanDayRoot:
    return PlanDayRoot(
        plan_day_id=plan_day_id,
        worker_id=worker_id,
        business_date=business_date,
        start_at=NOW - timedelta(minutes=50),
        status=status,
        confirmed_plan_reference=confirmed_plan_reference,
    )


def _task(
    publication: M1HandoffPublication,
    *,
    task_id: str = TASK_ID,
    definition_version: str = TASK_VERSION,
    completion_type: CompletionType = CompletionType.STAGE,
    assignment_kind: AssignmentKind = AssignmentKind.SINGLE,
    required_postconditions: tuple[str, ...] = (),
    required_evidence: tuple[EvidenceKind, ...] = (),
    stage_ids: tuple[str, ...] = (STAGE_ID,),
    status: TaskStatus = TaskStatus.OPEN,
) -> TaskState:
    return TaskState(
        definition=TaskDefinition(
            task_id=task_id,
            definition_version=definition_version,
            job_id=publication.job_id,
            source_handoff_id=publication.handoff_id,
            source_revision=publication.source_revision,
            business_meaning="Canonical site work",
            completion_type=completion_type,
            required_postconditions=required_postconditions,
            required_evidence=required_evidence,
            stage_ids=stage_ids,
            assignment_kind=assignment_kind,
        ),
        status=status,
        stages=tuple(StageState(stage_id=item) for item in stage_ids),
    )


def _assignment(
    *,
    assignment_id: str = ASSIGNMENT_ID,
    job_id: str = JOB_ID,
    task_id: str = TASK_ID,
    task_definition_version: str = TASK_VERSION,
    kind: AssignmentKind = AssignmentKind.SINGLE,
    members: tuple[str, ...] = (WORKER_ID,),
    plan_day_ids: tuple[str, ...] = (PLAN_DAY_ID,),
    lead_worker_id: str | None = None,
) -> AssignmentState:
    return AssignmentState(
        assignment_id=assignment_id,
        job_id=job_id,
        task_id=task_id,
        task_definition_version=task_definition_version,
        kind=kind,
        member_worker_ids=members,
        plan_day_ids=plan_day_ids,
        lead_worker_id=lead_worker_id,
    )


def _directive(
    *,
    directive_id: str,
    directive_type: DirectiveType,
    directive_class: DirectiveClass,
    worker_id: str,
    issued_at: datetime,
    escalation_due_at: datetime | None = None,
    job_id: str | None = None,
    task_id: str | None = None,
    assignment_id: str | None = None,
    plan_day_id: str | None = None,
    proposed_plan_reference: str | None = None,
    issuance_sequence: int = 1,
    supersedes_directive_id: str | None = None,
    delivery_evidence: DeliveryEvidence = DeliveryEvidence.QUEUED,
    acknowledged_event_id: str | None = None,
    exception_event_id: str | None = None,
    e1_escalated: bool = False,
    stop_in_force: bool = False,
    directive_revision: int = 0,
) -> DirectiveRoot:
    return DirectiveRoot(
        definition=DirectiveDefinition(
            directive_id=directive_id,
            directive_type=directive_type,
            directive_class=directive_class,
            worker_id=worker_id,
            issued_at=issued_at,
            escalation_due_at=escalation_due_at,
            job_id=job_id,
            task_id=task_id,
            assignment_id=assignment_id,
            plan_day_id=plan_day_id,
            proposed_plan_reference=proposed_plan_reference,
            issuance_sequence=issuance_sequence,
            supersedes_directive_id=supersedes_directive_id,
        ),
        delivery_evidence=delivery_evidence,
        acknowledged_event_id=acknowledged_event_id,
        exception_event_id=exception_event_id,
        e1_escalated=e1_escalated,
        stop_in_force=stop_in_force,
        directive_revision=directive_revision,
    )


def _scope(
    *,
    publication: M1HandoffPublication | None = None,
    publications: tuple[M1HandoffPublication, ...] | None = None,
    workers: tuple[str, ...] = (WORKER_ID,),
    plan_days: tuple[PlanDayRoot, ...] = (),
    tasks: tuple[TaskState, ...] = (),
    assignments: tuple[AssignmentState, ...] = (),
    directives: tuple[DirectiveRoot, ...] = (),
    job_execution_roots: tuple[M2JobExecutionRoot, ...] | None = None,
    processed_events: ProcessedEventLedger | None = None,
) -> M2ReductionScope:
    resolved_publications = (
        publications
        if publications is not None
        else (publication or _publication(),)
    )
    if job_execution_roots is None:
        job_ids = {
            *(item.projection.job_id for item in resolved_publications),
            *(item.definition.job_id for item in tasks),
            *(item.job_id for item in assignments),
        }
        job_execution_roots = tuple(
            M2JobExecutionRoot(
                job_id=job_id,
                tasks=tuple(item for item in tasks if item.definition.job_id == job_id),
                assignments=tuple(item for item in assignments if item.job_id == job_id),
            )
            for job_id in sorted(job_ids)
        )
    return M2ReductionScope(
        publications=resolved_publications,
        worker_registry=WorkerIdentityRegistry(workers),
        job_execution_roots=job_execution_roots,
        plan_day_roots=plan_days,
        directive_roots=directives,
        processed_events=processed_events or ProcessedEventLedger(),
    )


def _replace_job_root(
    scope: M2ReductionScope,
    job_id: str,
    **changes,
) -> M2ReductionScope:
    root = scope.job_execution(job_id)
    assert root is not None
    updated = replace(root, **changes)
    return replace(
        scope,
        job_execution_roots=tuple(
            updated if item.job_id == job_id else item
            for item in scope.job_execution_roots
        ),
    )


def _context(policy: M2Policy | None = None, *, now: datetime = NOW) -> PolicyTimeContext:
    return PolicyTimeContext(now=now, policy=policy or M2Policy())


def _event_input(
    event_id: str,
    event_type: FieldEventType,
    *,
    actor_id: str = WORKER_ID,
    server_event_id: str | None = None,
    **changes,
) -> FieldEventInput:
    return FieldEventInput(
        event=FieldEventEnvelope(
            event_id=event_id,
            schema_version=1,
            event_type=event_type,
            actor_id=actor_id,
            occurred_at=NOW,
            **changes,
        ),
        server_event_id=server_event_id or f"server-{event_id}",
    )


def _effect_types(result) -> tuple[EffectType, ...]:
    return tuple(effect.effect_type for effect in result.emitted_effects)


def test_t01_silence_does_not_create_sickness_or_activate_plan() -> None:
    state = _scope(plan_days=(_plan_day(),))
    signal = SystemSignal(
        signal_id="signal-start-window",
        signal_type=SystemSignalType.START_WINDOW_ELAPSED,
        plan_day_id=PLAN_DAY_ID,
    )

    first = reduce(state, signal, _context())
    deterministic_replay = reduce(state, signal, _context())

    assert first == deterministic_replay
    assert first.outcome is ReductionOutcome.APPLIED
    assert _effect_types(first) == (EffectType.START_UNKNOWN_ESCALATED,)
    assert first.state.plan_day(PLAN_DAY_ID).status is PlanDayStatus.ISSUED
    assert first.state.plan_day(PLAN_DAY_ID).worker_available is True
    assert first.state.processed_events.receipts == ()
    assert EffectType.UNAVAILABLE_TODAY_RECORDED not in _effect_types(first)


def test_t02_transport_exception_does_not_make_worker_unavailable() -> None:
    state = _scope(plan_days=(_plan_day(),))
    event_input = _event_input(
        "event-transport-blocked",
        FieldEventType.START_EXCEPTION_REPORTED,
        plan_day_id=PLAN_DAY_ID,
        reason_class=StartExceptionReason.TRANSPORT_BLOCKED.value,
        text="Kolega nie przyjechał, mogę później autobusem.",
    )

    result = reduce(state, event_input, _context())

    assert result.outcome is ReductionOutcome.REPORTED
    assert result.state.plan_day(PLAN_DAY_ID).worker_available is True
    assert _effect_types(result) == (
        EffectType.START_EXCEPTION_RECORDED,
        EffectType.TRANSPORT_RESOLUTION_REQUIRED,
    )
    assert EffectType.UNAVAILABLE_TODAY_RECORDED not in _effect_types(result)


def test_t03_delay_is_distinct_from_exception_and_keeps_plan_active() -> None:
    state = _scope(plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),))
    event_input = _event_input(
        "event-delay",
        FieldEventType.START_DELAY_REPORTED,
        plan_day_id=PLAN_DAY_ID,
        reason_class="TRAFFIC",
        eta="08:40",
    )

    result = reduce(state, event_input, _context())

    assert result.outcome is ReductionOutcome.REPORTED
    assert result.state.receipt("event-delay").event.event_type is FieldEventType.START_DELAY_REPORTED
    assert result.state.plan_day(PLAN_DAY_ID).status is PlanDayStatus.ACTIVE
    assert result.state.plan_day(PLAN_DAY_ID).worker_available is True
    assert _effect_types(result) == (
        EffectType.START_DELAY_RECORDED,
        EffectType.DELAY_IMPACT_EVALUATION_REQUIRED,
    )
    assert dict(result.emitted_effects[1].details)["eta"] == "08:40"


def test_t04_same_event_id_is_logically_idempotent_and_changed_payload_conflicts() -> None:
    state = _scope(plan_days=(_plan_day(),))
    event_input = _event_input(
        "event-activate",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=PLAN_DAY_ID,
        server_event_id="server-first-activation",
    )

    first = reduce(state, event_input, _context())
    same_call = reduce(state, event_input, _context())
    retry = reduce(
        first.state,
        replace(event_input, server_event_id="server-must-not-replace-first"),
        _context(),
    )
    changed_payload = replace(
        event_input,
        event=replace(event_input.event, occurred_at=NOW + timedelta(minutes=1)),
        server_event_id="server-conflicting-retry",
    )
    conflict = reduce(first.state, changed_payload, _context())

    assert first == same_call
    assert len(first.state.processed_events) == 1
    assert retry.replayed is True
    assert retry.outcome is first.outcome
    assert retry.server_event_id == first.server_event_id == "server-first-activation"
    assert retry.response_effects == first.response_effects
    assert retry.emitted_effects == ()
    assert retry.root_deltas == ()
    assert retry.appended_receipt is None
    assert retry.state == first.state
    assert conflict.outcome is ReductionOutcome.REJECTED
    assert conflict.reason_codes == ("EVENT_ID_CONFLICT",)
    assert conflict.state == first.state
    assert len(conflict.state.processed_events) == 1

    different_event_same_server_id = replace(
        event_input,
        event=replace(event_input.event, event_id="event-activate-other"),
    )
    server_id_conflict = reduce(first.state, different_event_same_server_id, _context())
    assert server_id_conflict.outcome is ReductionOutcome.REJECTED
    assert server_id_conflict.reason_codes == ("SERVER_EVENT_ID_CONFLICT",)
    assert server_id_conflict.state == first.state


def test_t05_crew_completion_without_valid_lead_or_by_non_lead_fails_closed() -> None:
    publication = _publication()
    task = _task(publication, assignment_kind=AssignmentKind.CREW)
    plans = (
        _plan_day(worker_id=LEAD_ID),
        _plan_day(
            "plan-day-member",
            worker_id=MEMBER_ID,
            confirmed_plan_reference="crew-plan",
        ),
    )
    workers = (LEAD_ID, MEMBER_ID)
    missing_lead_state = _scope(
        publication=publication,
        workers=workers,
        plan_days=plans,
        tasks=(task,),
        assignments=(
            _assignment(
                kind=AssignmentKind.CREW,
                members=workers,
                plan_day_ids=(PLAN_DAY_ID, "plan-day-member"),
            ),
        ),
    )
    lead_event = _event_input(
        "event-crew-no-lead",
        FieldEventType.STAGE_COMPLETION_REPORTED,
        actor_id=LEAD_ID,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        stage_id=STAGE_ID,
    )

    missing_lead = reduce(missing_lead_state, lead_event, _context())

    assert missing_lead.outcome is ReductionOutcome.REJECTED
    assert _effect_types(missing_lead) == (
        EffectType.ASSIGNMENT_INVALID,
        EffectType.COMPLETION_REJECTED,
    )
    assert missing_lead.state.task(TASK_ID).stages[0].status is StageStatus.OPEN

    valid_assignment = _assignment(
        kind=AssignmentKind.CREW,
        members=workers,
        plan_day_ids=(PLAN_DAY_ID, "plan-day-member"),
        lead_worker_id=LEAD_ID,
    )
    non_lead_state = _replace_job_root(
        missing_lead_state,
        JOB_ID,
        assignments=(valid_assignment,),
    )
    non_lead_event = _event_input(
        "event-crew-non-lead",
        FieldEventType.STAGE_COMPLETION_REPORTED,
        actor_id=MEMBER_ID,
        plan_day_id="plan-day-member",
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        stage_id=STAGE_ID,
    )
    non_lead = reduce(non_lead_state, non_lead_event, _context())

    assert non_lead.outcome is ReductionOutcome.REJECTED
    assert "COMPLETION_BY_NON_LEAD" in non_lead.reason_codes
    assert non_lead.state.task(TASK_ID).stages[0].status is StageStatus.OPEN

    non_member_lead = replace(valid_assignment, lead_worker_id="worker-outsider")
    invalid_lead_state = _replace_job_root(
        replace(
            missing_lead_state,
            worker_registry=WorkerIdentityRegistry((*workers, "worker-outsider")),
        ),
        JOB_ID,
        assignments=(non_member_lead,),
    )
    invalid_lead = reduce(invalid_lead_state, lead_event, _context())
    assert invalid_lead.outcome is ReductionOutcome.REJECTED
    assert "INVALID_OR_MISSING_LEAD" in invalid_lead.reason_codes
    assert invalid_lead.state.task(TASK_ID).stages[0].status is StageStatus.OPEN


def test_t06_completion_without_required_evidence_stays_reported_and_open() -> None:
    publication = _publication()
    task = _task(
        publication,
        completion_type=CompletionType.VISIT,
        required_postconditions=("REPORT_COMPLETE",),
        required_evidence=(EvidenceKind.PHOTO,),
        stage_ids=(),
    )
    state = _scope(
        publication=publication,
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        tasks=(task,),
        assignments=(_assignment(),),
    )
    event_input = _event_input(
        "event-visit-without-photo",
        FieldEventType.VISIT_COMPLETION_REPORTED,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        satisfied_postconditions=("REPORT_COMPLETE",),
    )

    result = reduce(state, event_input, _context())

    assert result.outcome is ReductionOutcome.REPORTED
    assert result.state.receipt(event_input.event.event_id) is not None
    assert result.state.task(TASK_ID).status is TaskStatus.OPEN
    assert result.state.task(TASK_ID).completion_event_id is None
    assert result.missing_requirements == ("EVIDENCE:PHOTO",)
    assert _effect_types(result) == (EffectType.COMPLETION_REJECTED,)


def test_t07_action_without_ack_keeps_last_confirmed_plan_and_ack_is_directive_scoped() -> None:
    first_action = _directive(
        directive_id="directive-action-one",
        directive_type=DirectiveType.ACTION_REQUIRED,
        directive_class=DirectiveClass.ACTION,
        worker_id=WORKER_ID,
        issued_at=NOW - timedelta(minutes=30),
        escalation_due_at=NOW - timedelta(minutes=5),
        plan_day_id=PLAN_DAY_ID,
        delivery_evidence=DeliveryEvidence.CHANNEL_ACCEPTED,
        proposed_plan_reference="plan-job-b",
    )
    second_action = replace(
        first_action,
        definition=replace(
            first_action.definition,
            directive_id="directive-action-two",
            proposed_plan_reference="plan-job-c",
            issuance_sequence=2,
        ),
    )
    state = _scope(
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        directives=(first_action, second_action),
    )
    elapsed = SystemSignal(
        signal_id="signal-action-e1",
        signal_type=SystemSignalType.DIRECTIVE_E1_ELAPSED,
        directive_id=first_action.directive_id,
    )

    no_ack = reduce(state, elapsed, _context())

    assert _effect_types(no_ack) == (EffectType.ACK_MISSING_ESCALATED,)
    assert no_ack.state.directive_worker_informed(first_action.directive_id) is False
    assert no_ack.state.directive(first_action.directive_id).delivery_evidence is DeliveryEvidence.CHANNEL_ACCEPTED
    assert no_ack.state.plan_day(PLAN_DAY_ID).confirmed_plan_reference == "plan-job-a"

    ack_input = _event_input(
        "event-ack-one",
        FieldEventType.WORKER_ACKNOWLEDGED,
        plan_day_id=PLAN_DAY_ID,
        directive_id=first_action.directive_id,
    )
    acknowledged = reduce(no_ack.state, ack_input, _context())

    assert acknowledged.state.directive_worker_informed(first_action.directive_id) is True
    assert acknowledged.state.directive_worker_informed(second_action.directive_id) is False
    assert acknowledged.state.plan_day(PLAN_DAY_ID).confirmed_plan_reference == "plan-job-b"


def test_t08_stop_without_ack_is_unconfirmed_not_worker_informed() -> None:
    stop = _directive(
        directive_id="directive-stop-unconfirmed",
        directive_type=DirectiveType.STOP_DIRECTIVE,
        directive_class=DirectiveClass.STOP,
        worker_id=WORKER_ID,
        issued_at=NOW - timedelta(minutes=10),
        escalation_due_at=NOW - timedelta(minutes=5),
        plan_day_id=PLAN_DAY_ID,
        delivery_evidence=DeliveryEvidence.CHANNEL_ACCEPTED,
        stop_in_force=True,
    )
    state = _scope(
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        directives=(stop,),
    )
    elapsed = SystemSignal(
        signal_id="signal-stop-e1",
        signal_type=SystemSignalType.DIRECTIVE_E1_ELAPSED,
        directive_id=stop.directive_id,
    )

    result = reduce(state, elapsed, _context())

    assert result.outcome is ReductionOutcome.APPLIED
    assert _effect_types(result) == (EffectType.STOP_UNCONFIRMED,)
    assert result.state.directive_worker_informed(stop.directive_id) is False
    assert result.state.directive(stop.directive_id).delivery_evidence is DeliveryEvidence.CHANNEL_ACCEPTED


def test_t09_stop_action_exception_enters_safe_hold_and_keeps_stop_in_force() -> None:
    publication = _publication()
    task = _task(publication)
    stop = _directive(
        directive_id="directive-stop",
        directive_type=DirectiveType.STOP_DIRECTIVE,
        directive_class=DirectiveClass.STOP,
        worker_id=WORKER_ID,
        issued_at=NOW - timedelta(minutes=1),
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        plan_day_id=PLAN_DAY_ID,
        stop_in_force=True,
    )
    state = _scope(
        publication=publication,
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        tasks=(task,),
        assignments=(_assignment(),),
        directives=(stop,),
    )
    event_input = _event_input(
        "event-stop-exception",
        FieldEventType.WORKER_ACTION_EXCEPTION,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        directive_id=stop.directive_id,
        reason_class=ActionExceptionReason.CONFLICTING_FACT.value,
    )

    result = reduce(state, event_input, _context())

    held = result.state.task(TASK_ID)
    assert result.outcome is ReductionOutcome.APPLIED
    assert _effect_types(result) == (EffectType.SAFE_HOLD_ENTERED,)
    assert held.status is TaskStatus.SAFE_HOLD
    assert held.blocked_pending_resolution is True
    assert held.execution_authorized is False
    assert held.safe_hold_directive_id == stop.directive_id
    assert result.state.directive(stop.directive_id).stop_in_force is True
    assert tuple(
        (delta.root_kind, delta.root_id) for delta in result.root_deltas
    ) == (
        (RootKind.DIRECTIVE, stop.directive_id),
        (RootKind.JOB_EXECUTION, JOB_ID),
    )


def test_t10_site_problem_preserves_evidence_without_inventing_expert_verdict() -> None:
    publication = _publication()
    task = _task(publication)
    photos = (
        EvidenceItem("photo-one", EvidenceKind.PHOTO, "blob://photo-one"),
        EvidenceItem("photo-two", EvidenceKind.PHOTO, "blob://photo-two"),
    )
    state = _scope(
        publication=publication,
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        tasks=(task,),
        assignments=(_assignment(),),
    )
    event_input = _event_input(
        "event-site-problem",
        FieldEventType.SITE_PROBLEM_REPORTED,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        text="Mokro pod wanną.",
        severity_hint=ProblemHint.QUALITY,
        attachments=photos,
    )
    hold_policy = M2Policy(site_problem_action=SiteProblemAction.HOLD_AND_ESCALATE)

    result = reduce(state, event_input, _context(hold_policy))

    observation = result.state.task(TASK_ID).observations[0]
    assert observation.event_id == event_input.event.event_id
    assert observation.actor_id == WORKER_ID
    assert observation.evidence == photos
    assert observation.interpretation is None
    assert result.state.task(TASK_ID).evidence == photos
    assert result.state.task(TASK_ID).status is TaskStatus.BLOCKED
    assert _effect_types(result) == (
        EffectType.SITE_PROBLEM_RECORDED,
        EffectType.TASK_HELD,
        EffectType.SITE_PROBLEM_ESCALATED,
    )
    assert EffectType.STOP_DIRECTIVE_REQUESTED not in _effect_types(result)

    changed_evidence = replace(
        event_input,
        event=replace(
            event_input.event,
            event_id="event-site-problem-evidence-conflict",
            attachments=(
                replace(photos[0], content_reference="blob://modified-photo-one"),
            ),
        ),
        server_event_id="server-event-site-problem-evidence-conflict",
    )
    evidence_conflict = reduce(result.state, changed_evidence, _context(hold_policy))
    assert evidence_conflict.outcome is ReductionOutcome.REJECTED
    assert evidence_conflict.reason_codes == ("EVIDENCE_ID_CONFLICT",)
    assert evidence_conflict.state.task(TASK_ID).evidence == photos

    explicit_stop = reduce(
        state,
        replace(
            event_input,
            event=replace(event_input.event, event_id="event-site-problem-stop"),
            server_event_id="server-event-site-problem-stop",
        ),
        _context(M2Policy(site_problem_action=SiteProblemAction.REQUEST_STOP)),
    )
    assert EffectType.STOP_DIRECTIVE_REQUESTED in _effect_types(explicit_stop)

    completed_stage = replace(task.stages[0], status=StageStatus.DONE)
    conflicting_state = _replace_job_root(
        state,
        JOB_ID,
        tasks=(replace(task, stages=(completed_stage,)),),
    )
    unsafe_conflict = reduce(
        conflicting_state,
        replace(
            event_input,
            event=replace(
                event_input.event,
                event_id="event-site-problem-after-completion",
                stage_id=STAGE_ID,
                severity_hint=ProblemHint.UNSAFE,
            ),
            server_event_id="server-event-site-problem-after-completion",
        ),
        _context(M2Policy(site_problem_action=SiteProblemAction.RECORD_ONLY)),
    )
    assert unsafe_conflict.state.task(TASK_ID).status is TaskStatus.DISPUTED
    assert unsafe_conflict.state.task(TASK_ID).stages[0].status is StageStatus.DISPUTED
    assert EffectType.HUMAN_REVIEW_REQUIRED in _effect_types(unsafe_conflict)


def test_t11_early_finish_offers_only_soft_options_and_never_assigns_punishment_job() -> None:
    publication = _publication()
    task = _task(publication)
    completed_stage = replace(task.stages[0], status=StageStatus.DONE)
    task = replace(task, stages=(completed_stage,))
    assignment = _assignment()
    state = _scope(
        publication=publication,
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        tasks=(task,),
        assignments=(assignment,),
    )
    signal = SystemSignal(
        signal_id="signal-early-finish",
        signal_type=SystemSignalType.EARLY_FINISH_EVALUATED,
        plan_day_id=PLAN_DAY_ID,
        task_id=TASK_ID,
    )
    policy = M2Policy(
        early_finish_options=(
            EndOfDayAction.OFFER_SOFT_OPTIONS,
            EndOfDayAction.END_ON_SITE,
        )
    )

    result = reduce(state, signal, _context(policy))

    assert result.outcome is ReductionOutcome.APPLIED
    assert result.state.assignments == (assignment,)
    assert _effect_types(result) == (EffectType.EARLY_FINISH_SOFT_OPTIONS,)
    assert "GO NOW" not in str(result)
    options = dict(result.emitted_effects[0].details)["options"]
    assert options == "OFFER_SOFT_OPTIONS,END_ON_SITE"


def test_t12_day_close_intent_neither_closes_day_nor_releases_worker_before_policy() -> None:
    state = _scope(plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),))
    policy = M2Policy(end_of_day_action=EndOfDayAction.RETURN_BASE)
    event_input = _event_input(
        "event-day-close",
        FieldEventType.DAY_CLOSE_REPORTED,
        plan_day_id=PLAN_DAY_ID,
    )

    intent = reduce(state, event_input, _context(policy))

    pending = intent.state.plan_day(PLAN_DAY_ID)
    assert intent.outcome is ReductionOutcome.APPLIED
    assert pending.day_close_reported is True
    assert pending.status is PlanDayStatus.ACTIVE
    assert pending.worker_available is True
    assert _effect_types(intent) == (EffectType.END_OF_DAY_POLICY_REQUESTED,)
    assert tuple(delta.root_kind for delta in intent.root_deltas) == (
        RootKind.PLAN_DAY,
    )

    policy_satisfied = SystemSignal(
        signal_id="signal-return-base-complete",
        signal_type=SystemSignalType.END_OF_DAY_POLICY_SATISFIED,
        plan_day_id=PLAN_DAY_ID,
        policy_result=EndOfDayAction.RETURN_BASE,
    )
    closed = reduce(intent.state, policy_satisfied, _context(policy))

    assert closed.state.plan_day(PLAN_DAY_ID).status is PlanDayStatus.CLOSED
    assert closed.state.plan_day(PLAN_DAY_ID).worker_available is False
    assert _effect_types(closed) == (EffectType.DAY_CLOSED,)
    assert tuple(delta.root_kind for delta in closed.root_deltas) == (
        RootKind.PLAN_DAY,
    )


def test_t13_safe_hold_survives_rehydration_sync_retry_rollover_and_new_plan_day() -> None:
    publication = _publication()
    task = replace(
        _task(publication),
        status=TaskStatus.SAFE_HOLD,
        safe_hold_directive_id="directive-stop",
        blocked_pending_resolution=True,
        execution_authorized=False,
    )
    current_plan = _plan_day(status=PlanDayStatus.ACTIVE)
    next_plan = _plan_day(
        NEXT_PLAN_DAY_ID,
        status=PlanDayStatus.ISSUED,
        business_date=date(2026, 9, 5),
    )
    state = _scope(
        publication=publication,
        plan_days=(current_plan, next_plan),
        tasks=(task,),
        assignments=(_assignment(plan_day_ids=(PLAN_DAY_ID, NEXT_PLAN_DAY_ID)),),
    )

    for index, signal_type in enumerate(
        (
            SystemSignalType.STATE_REHYDRATED,
            SystemSignalType.SYNC_REPLAYED,
            SystemSignalType.DAY_ROLLED_OVER,
        ),
        start=1,
    ):
        result = reduce(
            state,
            SystemSignal(signal_id=f"signal-preserve-{index}", signal_type=signal_type),
            _context(),
        )
        assert result.outcome is ReductionOutcome.NOOP
        assert result.state.task(TASK_ID) == task
        state = result.state

    activation = _event_input(
        "event-next-day-activate",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=NEXT_PLAN_DAY_ID,
    )
    activated = reduce(state, activation, _context(now=NOW + timedelta(days=1)))
    retried = reduce(
        activated.state,
        activation,
        _context(now=NOW + timedelta(days=1)),
    )

    assert activated.state.plan_day(NEXT_PLAN_DAY_ID).status is PlanDayStatus.ACTIVE
    assert retried.replayed is True
    assert retried.state.task(TASK_ID).status is TaskStatus.SAFE_HOLD
    assert retried.state.task(TASK_ID).blocked_pending_resolution is True
    assert retried.state.task(TASK_ID).execution_authorized is False

    completion_attempt = _event_input(
        "event-complete-held-task",
        FieldEventType.STAGE_COMPLETION_REPORTED,
        plan_day_id=NEXT_PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        stage_id=STAGE_ID,
    )
    rejected = reduce(
        retried.state,
        completion_attempt,
        _context(now=NOW + timedelta(days=1)),
    )
    assert rejected.outcome is ReductionOutcome.REJECTED
    assert rejected.state.task(TASK_ID).status is TaskStatus.SAFE_HOLD


def test_adversarial_a_site_problem_cannot_weaken_safe_hold() -> None:
    publication = _publication()
    base = _task(publication)
    held = replace(
        base,
        status=TaskStatus.SAFE_HOLD,
        stages=(replace(base.stages[0], status=StageStatus.DONE),),
        safe_hold_directive_id="directive-stop",
        blocked_pending_resolution=True,
        execution_authorized=False,
    )
    state = _scope(
        publication=publication,
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        tasks=(held,),
        assignments=(_assignment(),),
    )
    problem = _event_input(
        "event-safe-hold-problem",
        FieldEventType.SITE_PROBLEM_REPORTED,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        stage_id=STAGE_ID,
        text="Nowy konflikt na zablokowanym froncie",
        severity_hint=ProblemHint.UNSAFE,
    )

    result = reduce(state, problem, _context())

    task = result.state.task(TASK_ID)
    assert task.status is TaskStatus.SAFE_HOLD
    assert task.blocked_pending_resolution is True
    assert task.execution_authorized is False
    assert task.safe_hold_directive_id == "directive-stop"


def test_adversarial_b_unsafe_before_completion_wins_fail_closed() -> None:
    publication = _publication()
    state = _scope(
        publication=publication,
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        tasks=(_task(publication),),
        assignments=(_assignment(),),
    )
    unsafe = _event_input(
        "event-unsafe-before-completion",
        FieldEventType.SITE_PROBLEM_REPORTED,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        stage_id=STAGE_ID,
        text="Może być niebezpiecznie",
        severity_hint=ProblemHint.UNSAFE,
    )
    reported = reduce(state, unsafe, _context())
    completion = _event_input(
        "event-completion-after-unsafe",
        FieldEventType.STAGE_COMPLETION_REPORTED,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        stage_id=STAGE_ID,
    )

    result = reduce(reported.state, completion, _context())

    assert result.outcome is ReductionOutcome.REJECTED
    assert "UNRESOLVED_UNSAFE_REPORT" in result.reason_codes
    assert EffectType.COMPLETION_ACCEPTED not in _effect_types(result)
    assert result.state.task(TASK_ID).status is TaskStatus.DISPUTED
    assert result.state.task(TASK_ID).stages[0].status is StageStatus.DISPUTED


def test_adversarial_c_completion_requires_execution_authority() -> None:
    publication = _publication()
    unauthorized = replace(_task(publication), execution_authorized=False)
    state = _scope(
        publication=publication,
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        tasks=(unauthorized,),
        assignments=(_assignment(),),
    )
    completion = _event_input(
        "event-completion-without-authority",
        FieldEventType.STAGE_COMPLETION_REPORTED,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        stage_id=STAGE_ID,
    )

    result = reduce(state, completion, _context())

    assert result.outcome is ReductionOutcome.REJECTED
    assert "EXECUTION_NOT_AUTHORIZED" in result.reason_codes
    assert result.state.task(TASK_ID).stages[0].status is StageStatus.OPEN


def test_adversarial_d_completion_is_rejected_while_stop_is_in_force() -> None:
    publication = _publication()
    stop = _directive(
        directive_id="directive-active-stop",
        directive_type=DirectiveType.STOP_DIRECTIVE,
        directive_class=DirectiveClass.STOP,
        worker_id=WORKER_ID,
        issued_at=NOW - timedelta(minutes=1),
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        plan_day_id=PLAN_DAY_ID,
        stop_in_force=True,
    )
    state = _scope(
        publication=publication,
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        tasks=(_task(publication),),
        assignments=(_assignment(),),
        directives=(stop,),
    )
    completion = _event_input(
        "event-completion-under-stop",
        FieldEventType.STAGE_COMPLETION_REPORTED,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        stage_id=STAGE_ID,
    )

    result = reduce(state, completion, _context())

    assert result.outcome is ReductionOutcome.REJECTED
    assert "STOP_IN_FORCE" in result.reason_codes
    assert EffectType.COMPLETION_ACCEPTED not in _effect_types(result)
    assert result.state.task(TASK_ID).stages[0].status is StageStatus.OPEN


def test_adversarial_e_new_event_cannot_repeat_or_overwrite_completion() -> None:
    publication = _publication()
    state = _scope(
        publication=publication,
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        tasks=(_task(publication),),
        assignments=(_assignment(),),
    )
    first_input = _event_input(
        "event-first-stage-completion",
        FieldEventType.STAGE_COMPLETION_REPORTED,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        stage_id=STAGE_ID,
    )
    first = reduce(state, first_input, _context())
    repeated_input = replace(
        first_input,
        event=replace(first_input.event, event_id="event-second-stage-completion"),
        server_event_id="server-event-second-stage-completion",
    )

    repeated = reduce(first.state, repeated_input, _context())

    assert repeated.outcome is ReductionOutcome.NOOP
    assert repeated.emitted_effects == ()
    assert repeated.state.task(TASK_ID).stages[0].completion_event_id == first_input.event.event_id
    assert repeated.state.receipt(repeated_input.event.event_id) is not None


def test_adversarial_f_evidence_identity_cannot_cross_task_scope() -> None:
    publication = _publication()
    first_task = _task(publication)
    second_definition = replace(
        first_task.definition,
        task_id="task-second",
        definition_version="task-second-v1",
        stage_ids=("stage-second",),
    )
    second_task = TaskState(
        definition=second_definition,
        stages=(StageState("stage-second"),),
    )
    second_assignment = replace(
        _assignment(),
        assignment_id="assignment-second",
        task_id="task-second",
        task_definition_version="task-second-v1",
    )
    evidence = EvidenceItem("evidence-shared", EvidenceKind.PHOTO, "blob://shared")
    state = _scope(
        publication=publication,
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        tasks=(first_task, second_task),
        assignments=(_assignment(), second_assignment),
    )
    first_report = _event_input(
        "event-evidence-first-task",
        FieldEventType.SITE_PROBLEM_REPORTED,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        text="Pierwszy scope",
        attachments=(evidence,),
    )
    recorded = reduce(state, first_report, _context())
    cross_scope = _event_input(
        "event-evidence-second-task",
        FieldEventType.SITE_PROBLEM_REPORTED,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id="task-second",
        assignment_id="assignment-second",
        text="Drugi scope",
        attachments=(evidence,),
    )

    result = reduce(recorded.state, cross_scope, _context())

    assert result.outcome is ReductionOutcome.REJECTED
    assert "EVIDENCE_SCOPE_CONFLICT" in result.reason_codes
    assert result.state.task("task-second").evidence == ()


def test_adversarial_g_dispute_requires_membership_in_completion_assignment() -> None:
    publication = _publication()
    outsider_id = "worker-outsider"
    crew = _assignment(
        kind=AssignmentKind.CREW,
        members=(LEAD_ID, MEMBER_ID),
        plan_day_ids=(PLAN_DAY_ID, "plan-day-member"),
        lead_worker_id=LEAD_ID,
    )
    state = _scope(
        publication=publication,
        workers=(LEAD_ID, MEMBER_ID, outsider_id),
        plan_days=(
            _plan_day(worker_id=LEAD_ID, status=PlanDayStatus.ACTIVE),
            _plan_day("plan-day-member", worker_id=MEMBER_ID, status=PlanDayStatus.ACTIVE),
            _plan_day("plan-day-outsider", worker_id=outsider_id, status=PlanDayStatus.ACTIVE),
        ),
        tasks=(_task(publication, assignment_kind=AssignmentKind.CREW),),
        assignments=(crew,),
    )
    completion = _event_input(
        "event-crew-completed-for-dispute",
        FieldEventType.STAGE_COMPLETION_REPORTED,
        actor_id=LEAD_ID,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        stage_id=STAGE_ID,
    )
    completed = reduce(state, completion, _context())
    dispute = _event_input(
        "event-outsider-dispute",
        FieldEventType.COMPLETION_DISPUTED,
        actor_id=outsider_id,
        plan_day_id="plan-day-outsider",
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        stage_id=STAGE_ID,
        against_event_id=completion.event.event_id,
        text="Nie zgadzam się",
    )

    result = reduce(completed.state, dispute, _context())

    assert result.outcome is ReductionOutcome.REJECTED
    assert "ACTOR_NOT_ASSIGNED" in result.reason_codes
    assert result.state.task(TASK_ID).stages[0].status is StageStatus.DONE


def test_adversarial_h_dispute_requires_reason_or_evidence() -> None:
    publication = _publication()
    crew = _assignment(
        kind=AssignmentKind.CREW,
        members=(LEAD_ID, MEMBER_ID),
        plan_day_ids=(PLAN_DAY_ID, "plan-day-member"),
        lead_worker_id=LEAD_ID,
    )
    state = _scope(
        publication=publication,
        workers=(LEAD_ID, MEMBER_ID),
        plan_days=(
            _plan_day(worker_id=LEAD_ID, status=PlanDayStatus.ACTIVE),
            _plan_day("plan-day-member", worker_id=MEMBER_ID, status=PlanDayStatus.ACTIVE),
        ),
        tasks=(_task(publication, assignment_kind=AssignmentKind.CREW),),
        assignments=(crew,),
    )
    completion = _event_input(
        "event-completed-before-empty-dispute",
        FieldEventType.STAGE_COMPLETION_REPORTED,
        actor_id=LEAD_ID,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        stage_id=STAGE_ID,
    )
    completed = reduce(state, completion, _context())
    dispute = _event_input(
        "event-empty-dispute",
        FieldEventType.COMPLETION_DISPUTED,
        actor_id=MEMBER_ID,
        plan_day_id="plan-day-member",
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        stage_id=STAGE_ID,
        against_event_id=completion.event.event_id,
    )

    result = reduce(completed.state, dispute, _context())

    assert result.outcome is ReductionOutcome.REJECTED
    assert "DISPUTE_REASON_OR_EVIDENCE_REQUIRED" in result.reason_codes
    assert result.state.task(TASK_ID).stages[0].status is StageStatus.DONE


def test_adversarial_i_against_event_must_match_exact_assignment_scope() -> None:
    publication = _publication()
    second_member = "worker-second-assignment"
    source_assignment = _assignment(
        kind=AssignmentKind.CREW,
        members=(LEAD_ID, MEMBER_ID),
        plan_day_ids=(PLAN_DAY_ID, "plan-day-member"),
        lead_worker_id=LEAD_ID,
    )
    other_assignment = replace(
        source_assignment,
        assignment_id="assignment-other-scope",
        member_worker_ids=(second_member,),
        plan_day_ids=("plan-day-other-scope",),
        lead_worker_id=second_member,
    )
    state = _scope(
        publication=publication,
        workers=(LEAD_ID, MEMBER_ID, second_member),
        plan_days=(
            _plan_day(worker_id=LEAD_ID, status=PlanDayStatus.ACTIVE),
            _plan_day("plan-day-member", worker_id=MEMBER_ID, status=PlanDayStatus.ACTIVE),
            _plan_day("plan-day-other-scope", worker_id=second_member, status=PlanDayStatus.ACTIVE),
        ),
        tasks=(_task(publication, assignment_kind=AssignmentKind.CREW),),
        assignments=(source_assignment, other_assignment),
    )
    completion = _event_input(
        "event-completion-source-scope",
        FieldEventType.STAGE_COMPLETION_REPORTED,
        actor_id=LEAD_ID,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        stage_id=STAGE_ID,
    )
    completed = reduce(state, completion, _context())
    dispute = _event_input(
        "event-dispute-other-scope",
        FieldEventType.COMPLETION_DISPUTED,
        actor_id=second_member,
        plan_day_id="plan-day-other-scope",
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id="assignment-other-scope",
        stage_id=STAGE_ID,
        against_event_id=completion.event.event_id,
        text="Spór z innego assignmentu",
    )

    result = reduce(completed.state, dispute, _context())

    assert result.outcome is ReductionOutcome.REJECTED
    assert result.reason_codes == ("INVALID_AGAINST_EVENT",)
    assert result.state.task(TASK_ID).stages[0].status is StageStatus.DONE


def test_adversarial_j_forged_rehydrated_ack_is_not_informed() -> None:
    forged = _directive(
        directive_id="directive-forged-ack",
        directive_type=DirectiveType.ACTION_REQUIRED,
        directive_class=DirectiveClass.ACTION,
        worker_id=WORKER_ID,
        issued_at=NOW - timedelta(minutes=30),
        escalation_due_at=NOW - timedelta(minutes=1),
        delivery_evidence=DeliveryEvidence.ACKED,
        acknowledged_event_id="event-missing-ack-receipt",
    )
    state = _scope(directives=(forged,))

    assert state.directive_worker_informed(forged.directive_id) is False

    result = reduce(
        state,
        SystemSignal(
            signal_id="signal-forged-ack-e1",
            signal_type=SystemSignalType.DIRECTIVE_E1_ELAPSED,
            directive_id=forged.directive_id,
        ),
        _context(),
    )
    assert _effect_types(result) == (EffectType.ACK_MISSING_ESCALATED,)


def test_adversarial_k_cross_job_directive_system_signal_is_rejected() -> None:
    directive = _directive(
        directive_id="directive-cross-job",
        directive_type=DirectiveType.ACTION_REQUIRED,
        directive_class=DirectiveClass.ACTION,
        worker_id=WORKER_ID,
        issued_at=NOW - timedelta(minutes=30),
        escalation_due_at=NOW - timedelta(minutes=1),
        job_id="job-other",
        delivery_evidence=DeliveryEvidence.CHANNEL_ACCEPTED,
    )
    state = _scope(directives=(directive,))

    result = reduce(
        state,
        SystemSignal(
            signal_id="signal-cross-job-e1",
            signal_type=SystemSignalType.DIRECTIVE_E1_ELAPSED,
            directive_id=directive.directive_id,
        ),
        _context(),
    )

    assert result.outcome is ReductionOutcome.REJECTED
    assert "CROSS_JOB_DIRECTIVE" in result.reason_codes
    assert result.emitted_effects == ()


def test_adversarial_l_unassigned_worker_cannot_mutate_task_with_problem_events() -> None:
    publication = _publication()
    outsider_id = "worker-unassigned"
    outsider_plan_id = "plan-day-unassigned"
    original_task = _task(publication)
    state = _scope(
        publication=publication,
        workers=(WORKER_ID, outsider_id),
        plan_days=(
            _plan_day(status=PlanDayStatus.ACTIVE),
            _plan_day(outsider_plan_id, worker_id=outsider_id, status=PlanDayStatus.ACTIVE),
        ),
        tasks=(original_task,),
        assignments=(_assignment(),),
    )
    resource_missing = _event_input(
        "event-unassigned-resource",
        FieldEventType.RESOURCE_MISSING_REPORTED,
        actor_id=outsider_id,
        plan_day_id=outsider_plan_id,
        task_id=TASK_ID,
        reason_class=ResourceKind.MATERIAL.value,
    )
    work_blocked = _event_input(
        "event-unassigned-work-blocked",
        FieldEventType.WORK_START_BLOCKED,
        actor_id=outsider_id,
        plan_day_id=outsider_plan_id,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        reason_class=WorkStartBlockedReason.NO_ACCESS.value,
    )
    site_problem = _event_input(
        "event-unassigned-site-problem",
        FieldEventType.SITE_PROBLEM_REPORTED,
        actor_id=outsider_id,
        plan_day_id=outsider_plan_id,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        text="Cudzy task",
    )

    resource_result = reduce(state, resource_missing, _context())
    blocked_result = reduce(state, work_blocked, _context())
    problem_result = reduce(state, site_problem, _context())

    assert resource_result.outcome is ReductionOutcome.REJECTED
    assert "TASK_AUTHORITY_REQUIRED" in resource_result.reason_codes
    assert resource_result.state.task(TASK_ID) == original_task
    assert blocked_result.outcome is ReductionOutcome.REJECTED
    assert "ACTOR_NOT_ASSIGNED" in blocked_result.reason_codes
    assert blocked_result.state.task(TASK_ID) == original_task
    assert problem_result.outcome is ReductionOutcome.REJECTED
    assert "ACTOR_NOT_ASSIGNED" in problem_result.reason_codes
    assert problem_result.state.task(TASK_ID) == original_task


def test_adversarial_m_work_block_requires_reason_and_delay_allows_optional_context() -> None:
    publication = _publication()
    original_task = _task(publication)
    state = _scope(
        publication=publication,
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        tasks=(original_task,),
        assignments=(_assignment(),),
    )
    missing_reason = _event_input(
        "event-work-blocked-without-reason",
        FieldEventType.WORK_START_BLOCKED,
        plan_day_id=PLAN_DAY_ID,
        job_id=JOB_ID,
        task_id=TASK_ID,
        assignment_id=ASSIGNMENT_ID,
    )

    rejected = reduce(state, missing_reason, _context())

    assert rejected.outcome is ReductionOutcome.REJECTED
    assert "WORK_START_BLOCKED_REASON_REQUIRED" in rejected.reason_codes
    assert rejected.state.task(TASK_ID) == original_task

    missing_resource_kind = _event_input(
        "event-resource-without-kind",
        FieldEventType.RESOURCE_MISSING_REPORTED,
        plan_day_id=PLAN_DAY_ID,
        task_id=TASK_ID,
    )
    resource_rejected = reduce(state, missing_resource_kind, _context())
    assert resource_rejected.outcome is ReductionOutcome.REJECTED
    assert resource_rejected.reason_codes == ("INVALID_RESOURCE_KIND",)
    assert resource_rejected.state.task(TASK_ID) == original_task

    optional_delay = _event_input(
        "event-delay-without-reason-or-eta",
        FieldEventType.START_DELAY_REPORTED,
        plan_day_id=PLAN_DAY_ID,
    )
    delay_result = reduce(state, optional_delay, _context())
    assert delay_result.outcome is ReductionOutcome.REPORTED
    assert _effect_types(delay_result) == (
        EffectType.START_DELAY_RECORDED,
        EffectType.DELAY_IMPACT_EVALUATION_REQUIRED,
    )


def test_t19_multi_job_assignments_share_one_canonical_plan_day_root() -> None:
    publication_a = _publication(job_id="job-A", handoff_id="handoff-A")
    publication_b = _publication(job_id="job-B", handoff_id="handoff-B")
    task_a = _task(publication_a, task_id="task-A", definition_version="task-A-v1")
    task_b = _task(publication_b, task_id="task-B", definition_version="task-B-v1")
    assignment_a = _assignment(
        assignment_id="assignment-A",
        job_id="job-A",
        task_id="task-A",
        task_definition_version="task-A-v1",
    )
    assignment_b = _assignment(
        assignment_id="assignment-B",
        job_id="job-B",
        task_id="task-B",
        task_definition_version="task-B-v1",
    )
    roots = (
        M2JobExecutionRoot("job-A", (task_a,), (assignment_a,), 4),
        M2JobExecutionRoot("job-B", (task_b,), (assignment_b,), 7),
    )
    scope = _scope(
        publications=(publication_a, publication_b),
        plan_days=(_plan_day(),),
        job_execution_roots=roots,
    )

    activated = reduce(
        scope,
        _event_input(
            "event-shared-plan-activated",
            FieldEventType.DAY_PLAN_ACTIVATED,
            plan_day_id=PLAN_DAY_ID,
        ),
        _context(),
    )

    assert len(activated.state.plan_day_roots) == 1
    assert activated.state.job_execution_roots == roots
    assert tuple(delta.root_kind for delta in activated.root_deltas) == (
        RootKind.PLAN_DAY,
    )
    assert activated.root_deltas[0].root_id == PLAN_DAY_ID
    assert activated.root_deltas[0].expected_revision == 0
    assert activated.root_deltas[0].resulting_revision == 1

    day_close = reduce(
        activated.state,
        _event_input(
            "event-shared-plan-close-intent",
            FieldEventType.DAY_CLOSE_REPORTED,
            plan_day_id=PLAN_DAY_ID,
        ),
        _context(),
    )
    assert tuple(delta.root_kind for delta in day_close.root_deltas) == (
        RootKind.PLAN_DAY,
    )
    assert day_close.state.job_execution_roots == roots
    assert day_close.state.plan_day(PLAN_DAY_ID).plan_day_revision == 2

    with pytest.raises(ValueError, match="plan_day_roots identities must be unique"):
        _scope(
            publications=(publication_a, publication_b),
            plan_days=(_plan_day(), _plan_day()),
            job_execution_roots=roots,
        )


def test_t20_two_handoffs_contribute_exactly_provenanced_tasks_to_one_job_root() -> None:
    publication_a = _publication(handoff_id="handoff-A", source_revision=1)
    publication_b = _publication(handoff_id="handoff-B", source_revision=2)
    task_a = _task(publication_a, task_id="task-A", definition_version="task-A-v1")
    task_b = _task(publication_b, task_id="task-B", definition_version="task-B-v1")
    root = M2JobExecutionRoot(JOB_ID, (task_a, task_b), ())

    scope = _scope(
        publications=(publication_a, publication_b),
        job_execution_roots=(root,),
    )
    result = reduce(
        scope,
        SystemSignal("signal-provenance-check", SystemSignalType.STATE_REHYDRATED),
        _context(),
    )

    assert result.outcome is ReductionOutcome.NOOP
    assert result.root_deltas == ()
    assert scope.publication(JOB_ID, 1, "handoff-A") == publication_a
    assert scope.publication(JOB_ID, 2, "handoff-B") == publication_b
    assert scope.task("task-A").definition.source_handoff_id == "handoff-A"
    assert scope.task("task-B").definition.source_handoff_id == "handoff-B"

    for invalid_definition in (
        replace(task_a.definition, source_handoff_id="handoff-unknown"),
        replace(task_a.definition, source_revision=3),
    ):
        invalid_task = replace(task_a, definition=invalid_definition)
        with pytest.raises(ValueError, match="exactly one canonical M1 publication"):
            _scope(
                publications=(publication_a, publication_b),
                job_execution_roots=(
                    M2JobExecutionRoot(JOB_ID, (invalid_task, task_b), ()),
                ),
            )

    with pytest.raises(ValueError, match="belong to its job execution root"):
        M2JobExecutionRoot(
            JOB_ID,
            (replace(task_a, definition=replace(task_a.definition, job_id="job-other")),),
            (),
        )


def test_t21_late_event_targets_historical_assignment_after_newer_handoff() -> None:
    publication_a = _publication(handoff_id="handoff-A", source_revision=1)
    publication_b = _publication(handoff_id="handoff-B", source_revision=2)
    task_a = _task(publication_a, task_id="task-A", definition_version="task-A-v1")
    task_b = _task(publication_b, task_id="task-B", definition_version="task-B-v1")
    assignment_a = _assignment(
        assignment_id="assignment-A",
        task_id="task-A",
        task_definition_version="task-A-v1",
    )
    assignment_b = _assignment(
        assignment_id="assignment-B",
        task_id="task-B",
        task_definition_version="task-B-v1",
    )
    scope = _scope(
        publications=(publication_a, publication_b),
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        job_execution_roots=(
            M2JobExecutionRoot(
                JOB_ID,
                (task_a, task_b),
                (assignment_a, assignment_b),
                11,
            ),
        ),
    )

    result = reduce(
        scope,
        _event_input(
            "event-late-assignment-A",
            FieldEventType.WORK_START_BLOCKED,
            plan_day_id=PLAN_DAY_ID,
            job_id=JOB_ID,
            task_id="task-A",
            assignment_id="assignment-A",
            reason_class=WorkStartBlockedReason.NO_ACCESS.value,
        ),
        _context(),
    )

    assert result.state.task("task-A").status is TaskStatus.BLOCKED
    assert result.state.task("task-B") == task_b
    assert result.state.assignment("assignment-B") == assignment_b
    assert len(result.root_deltas) == 1
    assert result.root_deltas[0].root_kind is RootKind.JOB_EXECUTION
    assert result.root_deltas[0].root_id == JOB_ID
    assert result.root_deltas[0].expected_revision == 11
    assert result.root_deltas[0].resulting_revision == 12

    waiting = reduce(
        scope,
        _event_input(
            "event-wait-assignment-A",
            FieldEventType.TECHNICAL_WAIT_REPORTED,
            plan_day_id=PLAN_DAY_ID,
            job_id=JOB_ID,
            task_id="task-A",
            assignment_id="assignment-A",
            wait_condition="Await access",
        ),
        _context(),
    )
    assert waiting.state.task("task-A").status is TaskStatus.WAITING
    assert waiting.state.assignment("assignment-A").released_worker_ids == (WORKER_ID,)
    assert waiting.state.task("task-B") == task_b
    assert len(waiting.root_deltas) == 1
    assert waiting.root_deltas[0].root_kind is RootKind.JOB_EXECUTION
    assert waiting.root_deltas[0].expected_revision == 11
    assert waiting.root_deltas[0].resulting_revision == 12


def test_t22_newer_handoff_task_cannot_clear_historical_safe_hold() -> None:
    publication_a = _publication(handoff_id="handoff-A", source_revision=1)
    publication_b = _publication(handoff_id="handoff-B", source_revision=2)
    evidence = EvidenceItem("evidence-safe-hold-A", EvidenceKind.PHOTO, "blob://unsafe-A")
    base_a = _task(publication_a, task_id="task-A", definition_version="task-A-v1")
    task_a = replace(
        base_a,
        status=TaskStatus.SAFE_HOLD,
        evidence=(evidence,),
        blocked_pending_resolution=True,
        execution_authorized=False,
    )
    base_b = _task(publication_b, task_id="task-B", definition_version="task-B-v1")
    task_b = replace(
        base_b,
        definition=replace(base_b.definition, supersedes_task_id="task-A"),
    )
    scope = _scope(
        publications=(publication_a, publication_b),
        job_execution_roots=(
            M2JobExecutionRoot(JOB_ID, (task_a, task_b), (), 9),
        ),
    )

    result = reduce(
        scope,
        SystemSignal("signal-after-B-materialized", SystemSignalType.STATE_REHYDRATED),
        _context(),
    )

    historical = result.state.task("task-A")
    assert historical.status is TaskStatus.SAFE_HOLD
    assert historical.evidence == (evidence,)
    assert historical.blocked_pending_resolution is True
    assert historical.execution_authorized is False
    assert result.state.task("task-B") == task_b
    assert result.state.job_execution(JOB_ID).job_execution_revision == 9
    assert result.root_deltas == ()


def test_t23_plan_only_directive_needs_no_synthetic_job_root() -> None:
    action = _directive(
        directive_id="directive-plan-action",
        directive_type=DirectiveType.ACTION_REQUIRED,
        directive_class=DirectiveClass.ACTION,
        worker_id=WORKER_ID,
        issued_at=NOW - timedelta(minutes=20),
        escalation_due_at=NOW - timedelta(minutes=5),
        plan_day_id=PLAN_DAY_ID,
        proposed_plan_reference="plan-only-confirmed",
        issuance_sequence=1,
        directive_revision=3,
    )
    stop = _directive(
        directive_id="directive-plan-stop",
        directive_type=DirectiveType.STOP_DIRECTIVE,
        directive_class=DirectiveClass.STOP,
        worker_id=WORKER_ID,
        issued_at=NOW - timedelta(minutes=10),
        escalation_due_at=NOW - timedelta(minutes=1),
        plan_day_id=PLAN_DAY_ID,
        issuance_sequence=2,
        directive_revision=6,
    )
    scope = _scope(
        publications=(),
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        directives=(action, stop),
        job_execution_roots=(),
    )

    acknowledged = reduce(
        scope,
        _event_input(
            "event-plan-only-ack",
            FieldEventType.WORKER_ACKNOWLEDGED,
            plan_day_id=PLAN_DAY_ID,
            directive_id=action.directive_id,
        ),
        _context(),
    )

    assert acknowledged.state.job_execution_roots == ()
    assert acknowledged.state.publications == ()
    assert acknowledged.state.directive_worker_informed(action.directive_id) is True
    assert acknowledged.state.plan_day(PLAN_DAY_ID).confirmed_plan_reference == "plan-only-confirmed"
    assert tuple(
        (delta.root_kind, delta.root_id) for delta in acknowledged.root_deltas
    ) == (
        (RootKind.DIRECTIVE, action.directive_id),
        (RootKind.PLAN_DAY, PLAN_DAY_ID),
    )
    assert acknowledged.state.directive(action.directive_id).directive_revision == 4
    assert acknowledged.state.plan_day(PLAN_DAY_ID).plan_day_revision == 1
    directive_delta = next(
        delta
        for delta in acknowledged.root_deltas
        if delta.root_kind is RootKind.DIRECTIVE
    )
    assert directive_delta.next_root == acknowledged.state.directive(action.directive_id)
    assert acknowledged.state.directive_worker_informed(action.directive_id) is True

    elapsed = reduce(
        scope,
        SystemSignal(
            "signal-plan-only-stop-e1",
            SystemSignalType.DIRECTIVE_E1_ELAPSED,
            plan_day_id=PLAN_DAY_ID,
            directive_id=stop.directive_id,
        ),
        _context(),
    )
    assert elapsed.outcome is ReductionOutcome.APPLIED
    assert _effect_types(elapsed) == (EffectType.STOP_UNCONFIRMED,)
    assert tuple(
        (delta.root_kind, delta.root_id) for delta in elapsed.root_deltas
    ) == ((RootKind.DIRECTIVE, stop.directive_id),)
    assert elapsed.state.directive(stop.directive_id).directive_revision == 7


def test_t24_worker_registry_is_the_only_canonical_worker_namespace() -> None:
    publication_a = _publication(job_id="job-A", handoff_id="handoff-A")
    publication_b = _publication(job_id="job-B", handoff_id="handoff-B")
    task_a = _task(publication_a, task_id="task-A", definition_version="task-A-v1")
    task_b = _task(publication_b, task_id="task-B", definition_version="task-B-v1")
    assignment_a = _assignment(
        assignment_id="assignment-A",
        job_id="job-A",
        task_id="task-A",
        task_definition_version="task-A-v1",
    )
    assignment_b = _assignment(
        assignment_id="assignment-B",
        job_id="job-B",
        task_id="task-B",
        task_definition_version="task-B-v1",
    )
    directive = _directive(
        directive_id="directive-shared-worker",
        directive_type=DirectiveType.INFO_NOTICE,
        directive_class=DirectiveClass.INFO,
        worker_id=WORKER_ID,
        issued_at=NOW,
        plan_day_id=PLAN_DAY_ID,
    )
    roots = (
        M2JobExecutionRoot("job-A", (task_a,), (assignment_a,), 2),
        M2JobExecutionRoot("job-B", (task_b,), (assignment_b,), 5),
    )
    scope = _scope(
        publications=(publication_a, publication_b),
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        directives=(directive,),
        job_execution_roots=roots,
    )

    applied = reduce(
        scope,
        _event_input(
            "event-worker-job-A",
            FieldEventType.WORK_START_BLOCKED,
            plan_day_id=PLAN_DAY_ID,
            job_id="job-A",
            task_id="task-A",
            assignment_id="assignment-A",
            reason_class=WorkStartBlockedReason.NO_ACCESS.value,
        ),
        _context(),
    )
    assert scope.worker_registry.worker_ids == (WORKER_ID,)
    assert all(not hasattr(root, "worker_ids") for root in roots)
    assert applied.state.job_execution("job-A").job_execution_revision == 3
    assert applied.state.job_execution("job-B").job_execution_revision == 5
    assert tuple(delta.root_id for delta in applied.root_deltas) == ("job-A",)

    unknown_actor = reduce(
        scope,
        _event_input(
            "event-unknown-worker",
            FieldEventType.DAY_PLAN_ACTIVATED,
            actor_id="worker-unknown",
            plan_day_id=PLAN_DAY_ID,
        ),
        _context(),
    )
    assert unknown_actor.outcome is ReductionOutcome.REJECTED
    assert "UNKNOWN_ACTOR" in unknown_actor.reason_codes
    assert unknown_actor.root_deltas == ()

    unknown_member = replace(assignment_a, member_worker_ids=("worker-unknown",))
    with pytest.raises(ValueError, match="assignment member"):
        _scope(
            publications=(publication_a,),
            job_execution_roots=(
                M2JobExecutionRoot("job-A", (task_a,), (unknown_member,)),
            ),
        )

    unknown_lead = replace(
        assignment_a,
        kind=AssignmentKind.CREW,
        lead_worker_id="worker-unknown",
    )
    with pytest.raises(ValueError, match="assignment lead"):
        _scope(
            publications=(publication_a,),
            job_execution_roots=(
                M2JobExecutionRoot("job-A", (task_a,), (unknown_lead,)),
            ),
        )

    with pytest.raises(ValueError, match="plan day worker"):
        _scope(
            publications=(),
            plan_days=(_plan_day(worker_id="worker-unknown"),),
            job_execution_roots=(),
        )


def test_blocker_a_late_ack_cannot_restore_an_older_confirmed_plan() -> None:
    old = _directive(
        directive_id="directive-plan-old",
        directive_type=DirectiveType.ACTION_REQUIRED,
        directive_class=DirectiveClass.ACTION,
        worker_id=WORKER_ID,
        issued_at=NOW - timedelta(minutes=20),
        plan_day_id=PLAN_DAY_ID,
        proposed_plan_reference="plan-old",
        issuance_sequence=1,
    )
    new = _directive(
        directive_id="directive-plan-new",
        directive_type=DirectiveType.ACTION_REQUIRED,
        directive_class=DirectiveClass.ACTION,
        worker_id=WORKER_ID,
        issued_at=NOW - timedelta(minutes=10),
        plan_day_id=PLAN_DAY_ID,
        proposed_plan_reference="plan-new",
        issuance_sequence=2,
    )
    scope = _scope(
        publications=(),
        plan_days=(_plan_day(status=PlanDayStatus.ACTIVE),),
        directives=(old, new),
        job_execution_roots=(),
    )

    latest_ack = reduce(
        scope,
        _event_input(
            "event-ack-plan-new",
            FieldEventType.WORKER_ACKNOWLEDGED,
            plan_day_id=PLAN_DAY_ID,
            directive_id=new.directive_id,
        ),
        _context(),
    )
    late_old_ack = reduce(
        latest_ack.state,
        _event_input(
            "event-late-ack-plan-old",
            FieldEventType.WORKER_ACKNOWLEDGED,
            plan_day_id=PLAN_DAY_ID,
            directive_id=old.directive_id,
        ),
        _context(),
    )

    assert latest_ack.state.plan_day(PLAN_DAY_ID).confirmed_plan_reference == "plan-new"
    assert tuple(
        (delta.root_kind, delta.root_id) for delta in latest_ack.root_deltas
    ) == (
        (RootKind.DIRECTIVE, new.directive_id),
        (RootKind.PLAN_DAY, PLAN_DAY_ID),
    )
    assert late_old_ack.outcome is ReductionOutcome.APPLIED
    assert late_old_ack.state.directive_worker_informed(old.directive_id) is True
    assert late_old_ack.state.plan_day(PLAN_DAY_ID).confirmed_plan_reference == "plan-new"
    assert tuple(
        (delta.root_kind, delta.root_id) for delta in late_old_ack.root_deltas
    ) == ((RootKind.DIRECTIVE, old.directive_id),)
    old_delta = late_old_ack.root_deltas[0]
    assert old_delta.expected_revision == 0
    assert old_delta.resulting_revision == 1
    assert late_old_ack.state.plan_day(PLAN_DAY_ID).plan_day_revision == 1


def test_blocker_b_directive_sequence_is_unique_only_within_its_stream() -> None:
    first = _directive(
        directive_id="directive-sequence-first",
        directive_type=DirectiveType.ACTION_REQUIRED,
        directive_class=DirectiveClass.ACTION,
        worker_id=WORKER_ID,
        issued_at=NOW,
        plan_day_id=PLAN_DAY_ID,
        proposed_plan_reference="plan-first",
        issuance_sequence=4,
    )
    duplicate = replace(
        first,
        definition=replace(
            first.definition,
            directive_id="directive-sequence-duplicate",
            proposed_plan_reference="plan-duplicate",
        ),
    )
    with pytest.raises(ValueError, match="unique within a directive stream"):
        _scope(
            publications=(),
            plan_days=(_plan_day(),),
            directives=(first, duplicate),
            job_execution_roots=(),
        )

    other_plan_id = "plan-day-worker-one-2026-09-06"
    other_stream = replace(
        duplicate,
        definition=replace(
            duplicate.definition,
            plan_day_id=other_plan_id,
        ),
    )
    valid = _scope(
        publications=(),
        plan_days=(
            _plan_day(),
            _plan_day(
                other_plan_id,
                business_date=date(2026, 9, 6),
                confirmed_plan_reference="plan-other",
            ),
        ),
        directives=(first, other_stream),
        job_execution_roots=(),
    )
    assert len(valid.directive_roots) == 2


def test_blocker_c_job_directive_cannot_mutate_foreign_job_plan() -> None:
    publication_a = _publication(job_id="job-A", handoff_id="handoff-A")
    publication_b = _publication(job_id="job-B", handoff_id="handoff-B")
    task_a = _task(publication_a, task_id="task-A", definition_version="task-A-v1")
    task_b = _task(publication_b, task_id="task-B", definition_version="task-B-v1")
    plan_a_id = "plan-day-job-A"
    plan_b_id = "plan-day-job-B"
    assignment_a = _assignment(
        assignment_id="assignment-A",
        job_id="job-A",
        task_id="task-A",
        task_definition_version="task-A-v1",
        plan_day_ids=(plan_a_id,),
    )
    assignment_b = _assignment(
        assignment_id="assignment-B",
        job_id="job-B",
        task_id="task-B",
        task_definition_version="task-B-v1",
        plan_day_ids=(plan_b_id,),
    )
    foreign = _directive(
        directive_id="directive-job-A-foreign-plan",
        directive_type=DirectiveType.ACTION_REQUIRED,
        directive_class=DirectiveClass.ACTION,
        worker_id=WORKER_ID,
        issued_at=NOW,
        job_id="job-A",
        plan_day_id=plan_b_id,
        proposed_plan_reference="foreign-plan-mutation",
    )
    scope = _scope(
        publications=(publication_a, publication_b),
        plan_days=(
            _plan_day(plan_a_id, confirmed_plan_reference="plan-A"),
            _plan_day(plan_b_id, confirmed_plan_reference="plan-B"),
        ),
        directives=(foreign,),
        job_execution_roots=(
            M2JobExecutionRoot("job-A", (task_a,), (assignment_a,)),
            M2JobExecutionRoot("job-B", (task_b,), (assignment_b,)),
        ),
    )

    result = reduce(
        scope,
        _event_input(
            "event-ack-job-A-foreign-plan",
            FieldEventType.WORKER_ACKNOWLEDGED,
            job_id="job-A",
            plan_day_id=plan_b_id,
            directive_id=foreign.directive_id,
        ),
        _context(),
    )

    assert result.outcome is ReductionOutcome.REJECTED
    assert "DIRECTIVE_EXECUTION_CONTEXT_REQUIRED" in result.reason_codes
    assert result.root_deltas == ()
    assert result.state.plan_day(plan_b_id).confirmed_plan_reference == "plan-B"
    assert result.state.directive(foreign.directive_id).delivery_evidence is DeliveryEvidence.QUEUED


def test_blocker_d_task_directive_requires_an_exact_assignment_plan_context() -> None:
    publication_a = _publication(job_id="job-A", handoff_id="handoff-A")
    publication_b = _publication(job_id="job-B", handoff_id="handoff-B")
    task_a = _task(publication_a, task_id="task-A", definition_version="task-A-v1")
    task_b = _task(publication_b, task_id="task-B", definition_version="task-B-v1")
    plan_a_id = "plan-day-task-A"
    plan_b_id = "plan-day-task-B"
    assignment_a = _assignment(
        assignment_id="assignment-A",
        job_id="job-A",
        task_id="task-A",
        task_definition_version="task-A-v1",
        plan_day_ids=(plan_a_id,),
    )
    assignment_b = _assignment(
        assignment_id="assignment-B",
        job_id="job-B",
        task_id="task-B",
        task_definition_version="task-B-v1",
        plan_day_ids=(plan_b_id,),
    )
    foreign = _directive(
        directive_id="directive-task-A-foreign-plan",
        directive_type=DirectiveType.ACTION_REQUIRED,
        directive_class=DirectiveClass.ACTION,
        worker_id=WORKER_ID,
        issued_at=NOW,
        job_id="job-A",
        task_id="task-A",
        plan_day_id=plan_b_id,
        proposed_plan_reference="foreign-task-plan",
    )
    scope = _scope(
        publications=(publication_a, publication_b),
        plan_days=(
            _plan_day(plan_a_id, confirmed_plan_reference="plan-A"),
            _plan_day(plan_b_id, confirmed_plan_reference="plan-B"),
        ),
        directives=(foreign,),
        job_execution_roots=(
            M2JobExecutionRoot("job-A", (task_a,), (assignment_a,)),
            M2JobExecutionRoot("job-B", (task_b,), (assignment_b,)),
        ),
    )

    result = reduce(
        scope,
        _event_input(
            "event-ack-task-A-foreign-plan",
            FieldEventType.WORKER_ACKNOWLEDGED,
            job_id="job-A",
            task_id="task-A",
            plan_day_id=plan_b_id,
            directive_id=foreign.directive_id,
        ),
        _context(),
    )

    assert result.outcome is ReductionOutcome.REJECTED
    assert "DIRECTIVE_EXECUTION_CONTEXT_REQUIRED" in result.reason_codes
    assert result.root_deltas == ()
    assert result.state.plan_day(plan_b_id).confirmed_plan_reference == "plan-B"


def test_blocker_e_task_supersession_cannot_cross_job_roots() -> None:
    publication_a = _publication(job_id="job-A", handoff_id="handoff-A")
    publication_b = _publication(job_id="job-B", handoff_id="handoff-B")
    task_a = _task(publication_a, task_id="task-A", definition_version="task-A-v1")
    task_b = _task(publication_b, task_id="task-B", definition_version="task-B-v1")
    cross_job_successor = replace(
        task_a,
        definition=replace(task_a.definition, supersedes_task_id="task-B"),
    )

    with pytest.raises(ValueError, match="same job execution root"):
        M2JobExecutionRoot("job-A", (cross_job_successor,), ())

    assert M2JobExecutionRoot("job-B", (task_b,), ()).task("task-B") == task_b

    publication_a_newer = _publication(
        job_id="job-A",
        handoff_id="handoff-A-newer",
        source_revision=2,
    )
    newer_task = _task(
        publication_a_newer,
        task_id="task-A-newer",
        definition_version="task-A-v2",
    )
    invalid_older_successor = replace(
        task_a,
        definition=replace(
            task_a.definition,
            task_id="task-A-older-successor",
            definition_version="task-A-older-successor-v1",
            supersedes_task_id=newer_task.definition.task_id,
        ),
    )
    with pytest.raises(ValueError, match="cannot supersede a newer source revision"):
        M2JobExecutionRoot(
            "job-A",
            (newer_task, invalid_older_successor),
            (),
        )


def test_blocker_f_assignment_supersession_cannot_cross_job_roots() -> None:
    publication_a = _publication(job_id="job-A", handoff_id="handoff-A")
    publication_b = _publication(job_id="job-B", handoff_id="handoff-B")
    task_a = _task(publication_a, task_id="task-A", definition_version="task-A-v1")
    task_b = _task(publication_b, task_id="task-B", definition_version="task-B-v1")
    assignment_a = _assignment(
        assignment_id="assignment-A",
        job_id="job-A",
        task_id="task-A",
        task_definition_version="task-A-v1",
    )
    assignment_b = _assignment(
        assignment_id="assignment-B",
        job_id="job-B",
        task_id="task-B",
        task_definition_version="task-B-v1",
    )
    cross_job_successor = replace(
        assignment_a,
        supersedes_assignment_id=assignment_b.assignment_id,
    )

    with pytest.raises(ValueError, match="same job execution root"):
        M2JobExecutionRoot("job-A", (task_a,), (cross_job_successor,))

    assert (
        M2JobExecutionRoot("job-B", (task_b,), (assignment_b,)).assignment(
            "assignment-B"
        )
        == assignment_b
    )

    unrelated_task = _task(
        publication_a,
        task_id="task-unrelated",
        definition_version="task-unrelated-v1",
    )
    unrelated_assignment = _assignment(
        assignment_id="assignment-unrelated",
        job_id="job-A",
        task_id="task-unrelated",
        task_definition_version="task-unrelated-v1",
    )
    unrelated_successor = replace(
        unrelated_assignment,
        supersedes_assignment_id=assignment_a.assignment_id,
    )
    with pytest.raises(ValueError, match="compatible task lineage"):
        M2JobExecutionRoot(
            "job-A",
            (task_a, unrelated_task),
            (assignment_a, unrelated_successor),
        )


def test_blocker_g_directive_supersession_requires_compatible_earlier_scope() -> None:
    old = _directive(
        directive_id="directive-job-A-old",
        directive_type=DirectiveType.ACTION_REQUIRED,
        directive_class=DirectiveClass.ACTION,
        worker_id=WORKER_ID,
        issued_at=NOW - timedelta(minutes=10),
        job_id="job-A",
        issuance_sequence=1,
    )
    cross_job = _directive(
        directive_id="directive-job-B-new",
        directive_type=DirectiveType.ACTION_REQUIRED,
        directive_class=DirectiveClass.ACTION,
        worker_id=WORKER_ID,
        issued_at=NOW,
        job_id="job-B",
        issuance_sequence=2,
        supersedes_directive_id=old.directive_id,
    )
    with pytest.raises(ValueError, match="compatible scope"):
        _scope(
            publications=(),
            directives=(old, cross_job),
            job_execution_roots=(
                M2JobExecutionRoot("job-A", (), ()),
                M2JobExecutionRoot("job-B", (), ()),
            ),
        )

    cross_task = replace(
        cross_job,
        definition=replace(
            cross_job.definition,
            directive_id="directive-job-A-other-task",
            job_id="job-A",
            task_id="task-other",
        ),
    )
    old_task = replace(
        old,
        definition=replace(old.definition, task_id="task-original"),
    )
    with pytest.raises(ValueError, match="compatible scope"):
        _scope(
            publications=(),
            directives=(old_task, cross_task),
            job_execution_roots=(M2JobExecutionRoot("job-A", (), ()),),
        )

    cross_worker = replace(
        cross_job,
        definition=replace(
            cross_job.definition,
            directive_id="directive-other-worker",
            job_id="job-A",
            worker_id=MEMBER_ID,
        ),
    )
    with pytest.raises(ValueError, match="same worker"):
        _scope(
            publications=(),
            workers=(WORKER_ID, MEMBER_ID),
            directives=(old, cross_worker),
            job_execution_roots=(M2JobExecutionRoot("job-A", (), ()),),
        )

    future = replace(
        cross_job,
        definition=replace(
            cross_job.definition,
            directive_id="directive-job-A-not-later",
            job_id="job-A",
            issuance_sequence=1,
        ),
    )
    with pytest.raises(ValueError, match="earlier issuance sequence"):
        _scope(
            publications=(),
            directives=(old, future),
            job_execution_roots=(M2JobExecutionRoot("job-A", (), ()),),
        )


def test_blocker_h_malformed_ack_ledger_never_grants_informed_authority() -> None:
    directive = _directive(
        directive_id="directive-rehydrated-ack",
        directive_type=DirectiveType.ACTION_REQUIRED,
        directive_class=DirectiveClass.ACTION,
        worker_id=WORKER_ID,
        issued_at=NOW - timedelta(minutes=20),
        escalation_due_at=NOW - timedelta(minutes=1),
        plan_day_id=PLAN_DAY_ID,
        delivery_evidence=DeliveryEvidence.ACKED,
        acknowledged_event_id="event-rehydrated-ack",
    )
    valid_envelope = _event_input(
        "event-rehydrated-ack",
        FieldEventType.WORKER_ACKNOWLEDGED,
        plan_day_id=PLAN_DAY_ID,
        directive_id=directive.directive_id,
    ).event
    malformed_events = (
        replace(valid_envelope, directive_id="directive-other"),
        replace(valid_envelope, actor_id=MEMBER_ID),
        replace(valid_envelope, plan_day_id="plan-day-other"),
    )

    for malformed_event in malformed_events:
        receipt = ProcessedEventReceipt(
            event=malformed_event,
            server_event_id=f"server-{malformed_event.actor_id}-{malformed_event.plan_day_id}",
            outcome=ReductionOutcome.APPLIED,
            response_effects=(),
        )
        scope = _scope(
            publications=(),
            workers=(WORKER_ID, MEMBER_ID),
            plan_days=(_plan_day(),),
            directives=(directive,),
            job_execution_roots=(),
            processed_events=ProcessedEventLedger((receipt,)),
        )
        assert scope.directive_worker_informed(directive.directive_id) is False

    elapsed = reduce(
        scope,
        SystemSignal(
            "signal-malformed-ack-e1",
            SystemSignalType.DIRECTIVE_E1_ELAPSED,
            directive_id=directive.directive_id,
        ),
        _context(),
    )
    assert elapsed.outcome is ReductionOutcome.APPLIED
    assert _effect_types(elapsed) == (EffectType.ACK_MISSING_ESCALATED,)


def test_blocker_i_fresh_canonical_reconstruction_derives_valid_ack_from_ledger() -> None:
    directive = _directive(
        directive_id="directive-canonical-ack",
        directive_type=DirectiveType.ACTION_REQUIRED,
        directive_class=DirectiveClass.ACTION,
        worker_id=WORKER_ID,
        issued_at=NOW - timedelta(minutes=20),
        escalation_due_at=NOW - timedelta(minutes=1),
        plan_day_id=PLAN_DAY_ID,
        proposed_plan_reference="plan-canonical-ack",
    )
    initial = _scope(
        publications=(),
        plan_days=(_plan_day(),),
        directives=(directive,),
        job_execution_roots=(),
    )
    acknowledged = reduce(
        initial,
        _event_input(
            "event-canonical-ack",
            FieldEventType.WORKER_ACKNOWLEDGED,
            plan_day_id=PLAN_DAY_ID,
            directive_id=directive.directive_id,
        ),
        _context(),
    )
    canonical = acknowledged.state.directive(directive.directive_id)
    assert canonical is not None
    assert "_ack_history_validated" not in asdict(canonical)

    reconstructed = _scope(
        publications=(),
        workers=tuple(acknowledged.state.worker_registry.worker_ids),
        plan_days=tuple(replace(plan) for plan in acknowledged.state.plan_day_roots),
        directives=(replace(canonical, definition=replace(canonical.definition)),),
        job_execution_roots=(),
        processed_events=ProcessedEventLedger(
            tuple(replace(receipt) for receipt in acknowledged.state.processed_events)
        ),
    )

    assert reconstructed.directive_worker_informed(directive.directive_id) is True
    elapsed = reduce(
        reconstructed,
        SystemSignal(
            "signal-canonical-ack-e1",
            SystemSignalType.DIRECTIVE_E1_ELAPSED,
            directive_id=directive.directive_id,
        ),
        _context(),
    )
    assert elapsed.outcome is ReductionOutcome.NOOP
    assert elapsed.root_deltas == ()
