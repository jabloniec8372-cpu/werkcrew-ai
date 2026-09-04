"""Pure deterministic state reducer for the canonical M2 field contract."""

from __future__ import annotations

from dataclasses import replace

from werkcrew_ai.field.models import (
    ActionExceptionReason,
    AssignmentKind,
    AssignmentState,
    CompletionType,
    DelayReason,
    DeliveryEvidence,
    DirectiveClass,
    DirectiveState,
    DirectiveType,
    EffectType,
    EvidenceItem,
    ExplicitInput,
    FieldEventEnvelope,
    FieldEventInput,
    FieldEventType,
    FieldObservation,
    M2Aggregate,
    M2_REDUCER_RULE_VERSION,
    PlanDayState,
    PlanDayStatus,
    PolicyTimeContext,
    ProblemHint,
    ProcessedEventReceipt,
    Reduction,
    ReductionOutcome,
    ResourceKind,
    SiteProblemAction,
    StageStatus,
    StartExceptionReason,
    SystemEffect,
    SystemSignal,
    SystemSignalType,
    TaskState,
    TaskStatus,
    UnavailableReason,
    WorkStartBlockedReason,
)


_COMPLETION_EVENTS = {
    FieldEventType.TASK_COMPLETION_REPORTED,
    FieldEventType.STAGE_COMPLETION_REPORTED,
    FieldEventType.VISIT_COMPLETION_REPORTED,
}


def reduce(
    previous_state: M2Aggregate,
    explicit_input: ExplicitInput,
    policy_time_context: PolicyTimeContext,
) -> Reduction:
    """Return the only legal next value without IO, clocks, IDs, or side effects."""

    if policy_time_context.rule_version != M2_REDUCER_RULE_VERSION:
        return Reduction(
            state=previous_state,
            outcome=ReductionOutcome.REJECTED,
            reason_codes=("UNSUPPORTED_RULE_VERSION",),
        )
    if isinstance(explicit_input, FieldEventInput):
        return _reduce_field_event(previous_state, explicit_input, policy_time_context)
    return _reduce_system_signal(previous_state, explicit_input, policy_time_context)


def _reduce_field_event(
    state: M2Aggregate,
    event_input: FieldEventInput,
    context: PolicyTimeContext,
) -> Reduction:
    event = event_input.event
    existing = state.receipt(event.event_id)
    if existing is not None:
        if existing.event != event:
            return Reduction(
                state=state,
                outcome=ReductionOutcome.REJECTED,
                server_event_id=existing.server_event_id,
                reason_codes=("EVENT_ID_CONFLICT",),
            )
        return Reduction(
            state=state,
            outcome=existing.outcome,
            emitted_effects=(),
            response_effects=existing.response_effects,
            server_event_id=existing.server_event_id,
            missing_requirements=existing.missing_requirements,
            reason_codes=existing.reason_codes,
            replayed=True,
        )

    if any(
        receipt.server_event_id == event_input.server_event_id
        for receipt in state.processed_events
    ):
        return Reduction(
            state=state,
            outcome=ReductionOutcome.REJECTED,
            reason_codes=("SERVER_EVENT_ID_CONFLICT",),
        )

    reference_errors = _reference_errors(state, event)
    if reference_errors:
        effects = _completion_rejection_effects(event, reference_errors)
        return _finish_event(
            state,
            event_input,
            ReductionOutcome.REJECTED,
            effects=effects,
            reasons=reference_errors,
        )

    handlers = {
        FieldEventType.DAY_PLAN_ACTIVATED: _day_plan_activated,
        FieldEventType.START_DELAY_REPORTED: _start_delay_reported,
        FieldEventType.START_EXCEPTION_REPORTED: _start_exception_reported,
        FieldEventType.UNAVAILABLE_TODAY_REPORTED: _unavailable_today_reported,
        FieldEventType.WORK_START_BLOCKED: _work_start_blocked,
        FieldEventType.RESOURCE_MISSING_REPORTED: _resource_missing_reported,
        FieldEventType.SITE_PROBLEM_REPORTED: _site_problem_reported,
        FieldEventType.SCOPE_FACT_REPORTED: _scope_fact_reported,
        FieldEventType.TASK_COMPLETION_REPORTED: _completion_reported,
        FieldEventType.STAGE_COMPLETION_REPORTED: _completion_reported,
        FieldEventType.TECHNICAL_WAIT_REPORTED: _technical_wait_reported,
        FieldEventType.VISIT_COMPLETION_REPORTED: _completion_reported,
        FieldEventType.DAY_CLOSE_REPORTED: _day_close_reported,
        FieldEventType.COMPLETION_DISPUTED: _completion_disputed,
        FieldEventType.WORKER_ACKNOWLEDGED: _worker_acknowledged,
        FieldEventType.WORKER_ACTION_EXCEPTION: _worker_action_exception,
    }
    next_state, outcome, effects, missing, reasons = handlers[event.event_type](
        state,
        event,
        context,
    )
    return _finish_event(
        next_state,
        event_input,
        outcome,
        effects=effects,
        missing=missing,
        reasons=reasons,
    )


def _finish_event(
    state: M2Aggregate,
    event_input: FieldEventInput,
    outcome: ReductionOutcome,
    *,
    effects: tuple[SystemEffect, ...] = (),
    missing: tuple[str, ...] = (),
    reasons: tuple[str, ...] = (),
) -> Reduction:
    receipt = ProcessedEventReceipt(
        event=event_input.event,
        server_event_id=event_input.server_event_id,
        outcome=outcome,
        response_effects=effects,
        missing_requirements=missing,
        reason_codes=reasons,
    )
    final_state = replace(
        state,
        processed_events=(*state.processed_events, receipt),
    )
    return Reduction(
        state=final_state,
        outcome=outcome,
        emitted_effects=effects,
        response_effects=effects,
        server_event_id=event_input.server_event_id,
        missing_requirements=missing,
        reason_codes=reasons,
    )


def _reference_errors(
    state: M2Aggregate,
    event: FieldEventEnvelope,
) -> tuple[str, ...]:
    errors: list[str] = []
    if event.actor_id not in state.worker_ids:
        errors.append("UNKNOWN_ACTOR")

    plan = state.plan_day(event.plan_day_id) if event.plan_day_id is not None else None
    if event.plan_day_id is not None and plan is None:
        errors.append("UNKNOWN_PLAN_DAY")
    elif plan is not None and plan.worker_id != event.actor_id:
        errors.append("PLAN_DAY_ACTOR_MISMATCH")

    canonical_job_id = state.publication.job_id
    if event.job_id is not None and event.job_id != canonical_job_id:
        errors.append("CROSS_JOB_REFERENCE")

    task = state.task(event.task_id) if event.task_id is not None else None
    if event.task_id is not None and task is None:
        errors.append("UNKNOWN_TASK")
    elif task is not None and task.definition.job_id != canonical_job_id:
        errors.append("CROSS_JOB_TASK")

    assignment = (
        state.assignment(event.assignment_id)
        if event.assignment_id is not None
        else None
    )
    if event.assignment_id is not None and assignment is None:
        errors.append("UNKNOWN_ASSIGNMENT")
    elif assignment is not None:
        if assignment.job_id != canonical_job_id:
            errors.append("CROSS_JOB_ASSIGNMENT")
        if event.task_id is not None and assignment.task_id != event.task_id:
            errors.append("CROSS_TASK_ASSIGNMENT")
        if event.job_id is not None and assignment.job_id != event.job_id:
            errors.append("CROSS_JOB_ASSIGNMENT")
        if event.actor_id not in assignment.member_worker_ids:
            errors.append("ACTOR_NOT_ASSIGNED")
        if event.plan_day_id is not None and event.plan_day_id not in assignment.plan_day_ids:
            errors.append("CROSS_PLAN_DAY_ASSIGNMENT")
        if task is not None and assignment.task_definition_version != task.definition.definition_version:
            errors.append("TASK_DEFINITION_VERSION_MISMATCH")
        if any(member not in state.worker_ids for member in assignment.member_worker_ids):
            errors.append("UNKNOWN_ASSIGNMENT_MEMBER")

    if (
        event.event_type is FieldEventType.RESOURCE_MISSING_REPORTED
        and task is not None
        and assignment is None
    ):
        authority_matches = tuple(
            candidate
            for candidate in state.assignments
            if candidate.task_id == task.definition.task_id
            and candidate.job_id == canonical_job_id
            and candidate.task_definition_version
            == task.definition.definition_version
            and event.actor_id in candidate.member_worker_ids
            and all(member in state.worker_ids for member in candidate.member_worker_ids)
            and (
                event.plan_day_id is None
                or event.plan_day_id in candidate.plan_day_ids
            )
        )
        if not authority_matches:
            errors.append("TASK_AUTHORITY_REQUIRED")
        elif len(authority_matches) > 1:
            errors.append("AMBIGUOUS_TASK_AUTHORITY")

    if event.stage_id is not None:
        if task is None or all(stage.stage_id != event.stage_id for stage in task.stages):
            errors.append("UNKNOWN_OR_CROSS_TASK_STAGE")

    directive = (
        state.directive(event.directive_id)
        if event.directive_id is not None
        else None
    )
    if event.directive_id is not None and directive is None:
        errors.append("UNKNOWN_DIRECTIVE")
    elif directive is not None:
        errors.extend(_directive_scope_errors(state, directive))
        if directive.worker_id != event.actor_id:
            errors.append("DIRECTIVE_ACTOR_MISMATCH")
        if event.task_id is not None and directive.task_id != event.task_id:
            errors.append("CROSS_TASK_DIRECTIVE")
        if (
            event.assignment_id is not None
            and directive.assignment_id != event.assignment_id
        ):
            errors.append("CROSS_ASSIGNMENT_DIRECTIVE")
        if event.plan_day_id is not None and directive.plan_day_id != event.plan_day_id:
            errors.append("CROSS_PLAN_DAY_DIRECTIVE")
    evidence_history = [
        (
            item,
            canonical_job_id,
            task_item.definition.task_id,
            None,
        )
        for task_item in state.tasks
        for item in task_item.evidence
    ]
    evidence_history.extend(
        (
            item,
            receipt.event.job_id or canonical_job_id,
            receipt.event.task_id,
            receipt.event.assignment_id,
        )
        for receipt in state.processed_events
        for item in receipt.event.attachments
    )
    current_job_id = event.job_id or canonical_job_id
    for submitted in event.attachments:
        for prior, prior_job_id, prior_task_id, prior_assignment_id in evidence_history:
            if prior.evidence_id != submitted.evidence_id:
                continue
            if prior != submitted:
                errors.append("EVIDENCE_ID_CONFLICT")
            if (
                prior_job_id != current_job_id
                or prior_task_id != event.task_id
                or (
                    prior_assignment_id is not None
                    and event.assignment_id is not None
                    and prior_assignment_id != event.assignment_id
                )
            ):
                errors.append("EVIDENCE_SCOPE_CONFLICT")

    errors.extend(_required_reference_errors(event))
    return tuple(sorted(set(errors)))


def _directive_scope_errors(
    state: M2Aggregate,
    directive: DirectiveState,
) -> tuple[str, ...]:
    errors: list[str] = []
    canonical_job_id = state.publication.job_id
    if directive.job_id is not None and directive.job_id != canonical_job_id:
        errors.append("CROSS_JOB_DIRECTIVE")
    if directive.worker_id not in state.worker_ids:
        errors.append("UNKNOWN_DIRECTIVE_WORKER")

    task = state.task(directive.task_id) if directive.task_id is not None else None
    if directive.task_id is not None and task is None:
        errors.append("UNKNOWN_DIRECTIVE_TASK")

    assignment = (
        state.assignment(directive.assignment_id)
        if directive.assignment_id is not None
        else None
    )
    if directive.assignment_id is not None and assignment is None:
        errors.append("UNKNOWN_DIRECTIVE_ASSIGNMENT")
    elif assignment is not None:
        if assignment.job_id != canonical_job_id:
            errors.append("CROSS_JOB_DIRECTIVE_ASSIGNMENT")
        if any(member not in state.worker_ids for member in assignment.member_worker_ids):
            errors.append("UNKNOWN_DIRECTIVE_ASSIGNMENT_MEMBER")
        if directive.task_id is not None and assignment.task_id != directive.task_id:
            errors.append("CROSS_TASK_DIRECTIVE_ASSIGNMENT")
        if (
            task is not None
            and assignment.task_definition_version
            != task.definition.definition_version
        ):
            errors.append("DIRECTIVE_TASK_DEFINITION_VERSION_MISMATCH")
        if directive.worker_id not in assignment.member_worker_ids:
            errors.append("DIRECTIVE_WORKER_NOT_ASSIGNED")

    plan = state.plan_day(directive.plan_day_id) if directive.plan_day_id is not None else None
    if directive.plan_day_id is not None and plan is None:
        errors.append("UNKNOWN_DIRECTIVE_PLAN_DAY")
    elif plan is not None:
        if plan.worker_id != directive.worker_id:
            errors.append("DIRECTIVE_PLAN_DAY_WORKER_MISMATCH")
        if assignment is not None and plan.plan_day_id not in assignment.plan_day_ids:
            errors.append("CROSS_PLAN_DAY_DIRECTIVE_ASSIGNMENT")
    return tuple(sorted(set(errors)))


def _required_reference_errors(event: FieldEventEnvelope) -> tuple[str, ...]:
    errors: list[str] = []
    plan_events = {
        FieldEventType.DAY_PLAN_ACTIVATED,
        FieldEventType.START_DELAY_REPORTED,
        FieldEventType.START_EXCEPTION_REPORTED,
        FieldEventType.UNAVAILABLE_TODAY_REPORTED,
        FieldEventType.DAY_CLOSE_REPORTED,
    }
    task_assignment_events = {
        FieldEventType.WORK_START_BLOCKED,
        FieldEventType.SITE_PROBLEM_REPORTED,
        FieldEventType.TASK_COMPLETION_REPORTED,
        FieldEventType.STAGE_COMPLETION_REPORTED,
        FieldEventType.TECHNICAL_WAIT_REPORTED,
        FieldEventType.VISIT_COMPLETION_REPORTED,
        FieldEventType.COMPLETION_DISPUTED,
    }
    if event.event_type in plan_events and event.plan_day_id is None:
        errors.append("PLAN_DAY_REQUIRED")
    if event.event_type in task_assignment_events:
        if event.job_id is None:
            errors.append("JOB_REQUIRED")
        if event.plan_day_id is None:
            errors.append("PLAN_DAY_REQUIRED")
        if event.task_id is None:
            errors.append("TASK_REQUIRED")
        if event.assignment_id is None:
            errors.append("ASSIGNMENT_REQUIRED")
    if event.event_type is FieldEventType.STAGE_COMPLETION_REPORTED and event.stage_id is None:
        errors.append("STAGE_REQUIRED")
    if event.event_type in {
        FieldEventType.WORKER_ACKNOWLEDGED,
        FieldEventType.WORKER_ACTION_EXCEPTION,
    } and event.directive_id is None:
        errors.append("DIRECTIVE_REQUIRED")
    if event.event_type is FieldEventType.COMPLETION_DISPUTED and event.against_event_id is None:
        errors.append("AGAINST_EVENT_REQUIRED")
    if event.event_type is FieldEventType.COMPLETION_DISPUTED and not (
        (event.reason_class and event.reason_class.strip())
        or (event.text and event.text.strip())
        or event.attachments
    ):
        errors.append("DISPUTE_REASON_OR_EVIDENCE_REQUIRED")
    if (
        event.event_type is FieldEventType.WORK_START_BLOCKED
        and not (event.reason_class and event.reason_class.strip())
    ):
        errors.append("WORK_START_BLOCKED_REASON_REQUIRED")
    if event.event_type is FieldEventType.SITE_PROBLEM_REPORTED and not (
        event.text and event.text.strip()
    ) and not event.attachments:
        errors.append("SITE_PROBLEM_EVIDENCE_REQUIRED")
    return tuple(errors)


def _completion_rejection_effects(
    event: FieldEventEnvelope,
    reasons: tuple[str, ...],
) -> tuple[SystemEffect, ...]:
    if event.event_type not in _COMPLETION_EVENTS:
        return ()
    return (
        _effect(
            event,
            EffectType.COMPLETION_REJECTED,
            details=(("reasons", ",".join(reasons)),),
        ),
    )


def _day_plan_activated(state, event, _context):
    plan = state.plan_day(event.plan_day_id)
    assert plan is not None
    if plan.status is PlanDayStatus.ACTIVE:
        return state, ReductionOutcome.NOOP, (), (), ()
    if plan.status not in {PlanDayStatus.DRAFT, PlanDayStatus.ISSUED}:
        return state, ReductionOutcome.REJECTED, (), (), ("PLAN_DAY_NOT_ACTIVATABLE",)
    updated = replace(plan, status=PlanDayStatus.ACTIVE)
    next_state = _with_plan_day(state, updated)
    return (
        next_state,
        ReductionOutcome.APPLIED,
        (_effect(event, EffectType.PLAN_DAY_ACTIVATED),),
        (),
        (),
    )


def _start_delay_reported(state, event, _context):
    if event.reason_class is not None and event.reason_class not in {
        item.value for item in DelayReason
    }:
        return state, ReductionOutcome.REJECTED, (), (), ("INVALID_DELAY_REASON",)
    details = ()
    if event.reason_class is not None:
        details = (("reason", event.reason_class),)
    if event.eta is not None:
        details = (*details, ("eta", event.eta))
    effects = (
        _effect(event, EffectType.START_DELAY_RECORDED, details=details),
        _effect(
            event,
            EffectType.DELAY_IMPACT_EVALUATION_REQUIRED,
            details=details,
        ),
    )
    return state, ReductionOutcome.REPORTED, effects, (), ()


def _start_exception_reported(state, event, _context):
    if event.reason_class not in {item.value for item in StartExceptionReason}:
        return state, ReductionOutcome.REJECTED, (), (), ("INVALID_START_EXCEPTION_REASON",)
    effects = [
        _effect(
            event,
            EffectType.START_EXCEPTION_RECORDED,
            details=(("reason", event.reason_class),),
        )
    ]
    if event.reason_class == StartExceptionReason.TRANSPORT_BLOCKED.value:
        effects.append(_effect(event, EffectType.TRANSPORT_RESOLUTION_REQUIRED))
    return state, ReductionOutcome.REPORTED, tuple(effects), (), ()


def _unavailable_today_reported(state, event, _context):
    if event.reason_class not in {item.value for item in UnavailableReason}:
        return state, ReductionOutcome.REJECTED, (), (), ("INVALID_UNAVAILABLE_REASON",)
    plan = state.plan_day(event.plan_day_id)
    assert plan is not None
    updated = replace(plan, worker_available=False)
    return (
        _with_plan_day(state, updated),
        ReductionOutcome.APPLIED,
        (
            _effect(
                event,
                EffectType.UNAVAILABLE_TODAY_RECORDED,
                details=(("reason", event.reason_class),),
            ),
        ),
        (),
        (),
    )


def _work_start_blocked(state, event, _context):
    if event.reason_class not in {item.value for item in WorkStartBlockedReason}:
        return state, ReductionOutcome.REJECTED, (), (), ("INVALID_WORK_START_BLOCKED_REASON",)
    task = state.task(event.task_id)
    assert task is not None
    updated = _ordinary_task_status(task, TaskStatus.BLOCKED)
    return (
        _with_task(state, updated),
        ReductionOutcome.APPLIED,
        (_effect(event, EffectType.TASK_HELD),),
        (),
        (),
    )


def _resource_missing_reported(state, event, _context):
    if event.task_id is None and event.plan_day_id is None:
        return state, ReductionOutcome.REJECTED, (), (), ("TASK_OR_PLAN_DAY_REQUIRED",)
    if event.reason_class not in {item.value for item in ResourceKind}:
        return state, ReductionOutcome.REJECTED, (), (), ("INVALID_RESOURCE_KIND",)
    effects = [_effect(event, EffectType.RESOURCE_MISSING_RECORDED)]
    next_state = state
    if event.task_id is not None:
        task = state.task(event.task_id)
        assert task is not None
        next_state = _with_task(state, _ordinary_task_status(task, TaskStatus.BLOCKED))
        effects.append(_effect(event, EffectType.TASK_HELD))
    return next_state, ReductionOutcome.APPLIED, tuple(effects), (), ()


def _site_problem_reported(state, event, context):
    task = state.task(event.task_id) if event.task_id is not None else None
    next_state = state
    effects = [_effect(event, EffectType.SITE_PROBLEM_RECORDED)]
    if task is not None:
        observation = FieldObservation(
            event_id=event.event_id,
            actor_id=event.actor_id,
            text=event.text,
            evidence=event.attachments,
            interpretation=None,
        )
        updated = replace(
            task,
            evidence=_merge_evidence(task.evidence, event.attachments),
            observations=(*task.observations, observation),
        )
        conflicting_completion = task.status is TaskStatus.DONE or (
            event.stage_id is not None
            and any(
                stage.stage_id == event.stage_id and stage.status is StageStatus.DONE
                for stage in task.stages
            )
        )
        safe_hold_active = (
            task.status is TaskStatus.SAFE_HOLD
            or task.blocked_pending_resolution
            or any(stage.status is StageStatus.SAFE_HOLD for stage in task.stages)
        )
        if safe_hold_active:
            if conflicting_completion:
                effects.append(_effect(event, EffectType.HUMAN_REVIEW_REQUIRED))
        elif conflicting_completion:
            stages = tuple(
                replace(stage, status=StageStatus.DISPUTED)
                if event.stage_id is not None and stage.stage_id == event.stage_id
                else stage
                for stage in updated.stages
            )
            updated = replace(updated, status=TaskStatus.DISPUTED, stages=stages)
            effects.extend(
                (
                    _effect(event, EffectType.TASK_HELD),
                    _effect(event, EffectType.HUMAN_REVIEW_REQUIRED),
                )
            )
        elif context.policy.site_problem_action in {
            SiteProblemAction.HOLD,
            SiteProblemAction.HOLD_AND_ESCALATE,
        }:
            updated = _ordinary_task_status(updated, TaskStatus.BLOCKED)
            effects.append(_effect(event, EffectType.TASK_HELD))
        next_state = _with_task(state, updated)

    if context.policy.site_problem_action in {
        SiteProblemAction.ESCALATE,
        SiteProblemAction.HOLD_AND_ESCALATE,
    }:
        effects.append(_effect(event, EffectType.SITE_PROBLEM_ESCALATED))
    elif context.policy.site_problem_action is SiteProblemAction.REQUEST_STOP:
        effects.append(_effect(event, EffectType.STOP_DIRECTIVE_REQUESTED))
    return next_state, ReductionOutcome.APPLIED, tuple(effects), (), ()


def _scope_fact_reported(state, event, _context):
    if not ((event.text and event.text.strip()) or event.attachments):
        return state, ReductionOutcome.REJECTED, (), (), ("SCOPE_DESCRIPTION_REQUIRED",)
    return (
        state,
        ReductionOutcome.REPORTED,
        (_effect(event, EffectType.NON_BINDING_SCOPE_CHANGE_RECORDED),),
        (),
        (),
    )


def _completion_reported(state, event, _context):
    task = state.task(event.task_id)
    assignment = state.assignment(event.assignment_id)
    assert task is not None and assignment is not None

    invalid_authority = _completion_authority_errors(task, assignment, event)
    if invalid_authority:
        effects = (
            _effect(
                event,
                EffectType.ASSIGNMENT_INVALID,
                details=(("reasons", ",".join(invalid_authority)),),
            ),
            _effect(
                event,
                EffectType.COMPLETION_REJECTED,
                details=(("reasons", ",".join(invalid_authority)),),
            ),
        )
        return state, ReductionOutcome.REJECTED, effects, (), invalid_authority

    expected_type = {
        FieldEventType.TASK_COMPLETION_REPORTED: CompletionType.TASK,
        FieldEventType.STAGE_COMPLETION_REPORTED: CompletionType.STAGE,
        FieldEventType.VISIT_COMPLETION_REPORTED: CompletionType.VISIT,
    }[event.event_type]
    if task.definition.completion_type is not expected_type:
        reasons = ("COMPLETION_TYPE_MISMATCH",)
        return (
            state,
            ReductionOutcome.REJECTED,
            _completion_rejection_effects(event, reasons),
            (),
            reasons,
        )

    target_stage = (
        next(
            (stage for stage in task.stages if stage.stage_id == event.stage_id),
            None,
        )
        if event.event_type is FieldEventType.STAGE_COMPLETION_REPORTED
        else None
    )
    safe_hold_active = (
        task.status is TaskStatus.SAFE_HOLD
        or task.blocked_pending_resolution
        or any(stage.status is StageStatus.SAFE_HOLD for stage in task.stages)
    )
    unsafe_conflict = _has_unresolved_unsafe_report(state, event)
    gate_reasons: list[str] = []
    if not task.execution_authorized:
        gate_reasons.append("EXECUTION_NOT_AUTHORIZED")
    if task.blocked_pending_resolution:
        gate_reasons.append("BLOCKED_PENDING_RESOLUTION")
    if safe_hold_active:
        gate_reasons.append("SAFE_HOLD_HAS_PRECEDENCE")
    if _completion_blocked_by_stop(state, event):
        gate_reasons.append("STOP_IN_FORCE")
    if unsafe_conflict:
        gate_reasons.append("UNRESOLVED_UNSAFE_REPORT")
    if gate_reasons:
        reasons = tuple(sorted(set(gate_reasons)))
        next_state = state
        effects = list(_completion_rejection_effects(event, reasons))
        if unsafe_conflict:
            if not safe_hold_active:
                stages = tuple(
                    replace(stage, status=StageStatus.DISPUTED)
                    if event.stage_id is not None and stage.stage_id == event.stage_id
                    else stage
                    for stage in task.stages
                )
                next_state = _with_task(
                    state,
                    replace(task, status=TaskStatus.DISPUTED, stages=stages),
                )
            effects.extend(
                (
                    _effect(event, EffectType.TASK_HELD),
                    _effect(event, EffectType.HUMAN_REVIEW_REQUIRED),
                )
            )
        return (
            next_state,
            ReductionOutcome.REJECTED,
            tuple(effects),
            (),
            reasons,
        )

    already_completed = task.status is TaskStatus.DONE or (
        target_stage is not None and target_stage.status is StageStatus.DONE
    )
    if already_completed:
        return (
            state,
            ReductionOutcome.NOOP,
            (),
            (),
            ("COMPLETION_ALREADY_RECORDED",),
        )
    if task.status in {
        TaskStatus.BLOCKED,
        TaskStatus.WAITING,
        TaskStatus.DISPUTED,
        TaskStatus.SAFE_HOLD,
    }:
        reasons = ("TASK_NOT_COMPLETABLE",)
        return (
            state,
            ReductionOutcome.REJECTED,
            _completion_rejection_effects(event, reasons),
            (),
            reasons,
        )
    if target_stage is not None and target_stage.status in {
        StageStatus.DISPUTED,
        StageStatus.SAFE_HOLD,
    }:
        reasons = ("STAGE_NOT_COMPLETABLE",)
        return (
            state,
            ReductionOutcome.REJECTED,
            _completion_rejection_effects(event, reasons),
            (),
            reasons,
        )

    missing = _missing_completion_requirements(task, event)
    with_evidence = replace(
        task,
        evidence=_merge_evidence(task.evidence, event.attachments),
    )
    if missing:
        effects = (
            _effect(
                event,
                EffectType.COMPLETION_REJECTED,
                details=(("missing", ",".join(missing)),),
            ),
        )
        return (
            _with_task(state, with_evidence),
            ReductionOutcome.REPORTED,
            effects,
            missing,
            ("POSTCONDITIONS_NOT_MET",),
        )

    if event.event_type is FieldEventType.STAGE_COMPLETION_REPORTED:
        stages = tuple(
            replace(stage, status=StageStatus.DONE, completion_event_id=event.event_id)
            if stage.stage_id == event.stage_id
            else stage
            for stage in with_evidence.stages
        )
        completed = replace(with_evidence, stages=stages)
    else:
        completed = replace(
            with_evidence,
            status=TaskStatus.DONE,
            completion_event_id=event.event_id,
        )
    return (
        _with_task(state, completed),
        ReductionOutcome.APPLIED,
        (_effect(event, EffectType.COMPLETION_ACCEPTED),),
        (),
        (),
    )


def _completion_authority_errors(
    task: TaskState,
    assignment: AssignmentState,
    event: FieldEventEnvelope,
) -> tuple[str, ...]:
    errors: list[str] = []
    if assignment.kind is not task.definition.assignment_kind:
        errors.append("ASSIGNMENT_KIND_MISMATCH")
    if assignment.kind is AssignmentKind.CREW:
        if (
            assignment.lead_worker_id is None
            or assignment.lead_worker_id not in assignment.member_worker_ids
        ):
            errors.append("INVALID_OR_MISSING_LEAD")
        elif event.actor_id != assignment.lead_worker_id:
            errors.append("COMPLETION_BY_NON_LEAD")
    elif event.actor_id not in assignment.member_worker_ids:
        errors.append("COMPLETION_BY_UNASSIGNED_WORKER")
    return tuple(sorted(errors))


def _completion_blocked_by_stop(
    state: M2Aggregate,
    event: FieldEventEnvelope,
) -> bool:
    canonical_job_id = state.publication.job_id
    for directive in state.directives:
        if (
            directive.directive_class is not DirectiveClass.STOP
            or not directive.stop_in_force
            or directive.job_id not in {None, canonical_job_id}
        ):
            continue
        if directive.task_id is not None:
            if directive.task_id == event.task_id:
                return True
            continue
        if directive.assignment_id is not None:
            if directive.assignment_id == event.assignment_id:
                return True
            continue
        if directive.plan_day_id is not None:
            if directive.plan_day_id == event.plan_day_id:
                return True
            continue
        if directive.worker_id == event.actor_id:
            return True
    return False


def _has_unresolved_unsafe_report(
    state: M2Aggregate,
    event: FieldEventEnvelope,
) -> bool:
    return any(
        receipt.outcome in {ReductionOutcome.APPLIED, ReductionOutcome.REPORTED}
        and receipt.event.event_type is FieldEventType.SITE_PROBLEM_REPORTED
        and receipt.event.severity_hint is ProblemHint.UNSAFE
        and receipt.event.job_id == event.job_id
        and receipt.event.task_id == event.task_id
        and (
            event.stage_id is None
            or receipt.event.stage_id is None
            or receipt.event.stage_id == event.stage_id
        )
        for receipt in state.processed_events
    )


def _missing_completion_requirements(
    task: TaskState,
    event: FieldEventEnvelope,
) -> tuple[str, ...]:
    provided_postconditions = set(event.satisfied_postconditions)
    evidence_kinds = {item.kind for item in (*task.evidence, *event.attachments)}
    missing = [
        f"POSTCONDITION:{item}"
        for item in task.definition.required_postconditions
        if item not in provided_postconditions
    ]
    missing.extend(
        f"EVIDENCE:{item.value}"
        for item in task.definition.required_evidence
        if item not in evidence_kinds
    )
    if task.definition.requires_quantity and not (event.quantity and event.unit):
        missing.append("QUANTITY_AND_UNIT")
    return tuple(sorted(missing))


def _technical_wait_reported(state, event, _context):
    if not event.wait_condition and event.wait_until is None:
        return state, ReductionOutcome.REJECTED, (), (), ("WAIT_CONDITION_REQUIRED",)
    task = state.task(event.task_id)
    assignment = state.assignment(event.assignment_id)
    assert task is not None and assignment is not None
    if (
        task.status is TaskStatus.SAFE_HOLD
        or task.blocked_pending_resolution
        or any(stage.status is StageStatus.SAFE_HOLD for stage in task.stages)
    ):
        return state, ReductionOutcome.REJECTED, (), (), ("SAFE_HOLD_HAS_PRECEDENCE",)
    updated_task = replace(task, status=TaskStatus.WAITING)
    released = tuple(sorted(set((*assignment.released_worker_ids, event.actor_id))))
    updated_assignment = replace(assignment, released_worker_ids=released)
    next_state = _with_assignment(_with_task(state, updated_task), updated_assignment)
    return (
        next_state,
        ReductionOutcome.APPLIED,
        (_effect(event, EffectType.TECHNICAL_WAIT_RECORDED),),
        (),
        (),
    )


def _day_close_reported(state, event, _context):
    plan = state.plan_day(event.plan_day_id)
    assert plan is not None
    if plan.status is PlanDayStatus.CLOSED:
        return state, ReductionOutcome.NOOP, (), (), ()
    updated = replace(plan, day_close_reported=True)
    return (
        _with_plan_day(state, updated),
        ReductionOutcome.APPLIED,
        (
            _effect(
                event,
                EffectType.END_OF_DAY_POLICY_REQUESTED,
                details=(("policy", "company.policy.end_of_day"),),
            ),
        ),
        (),
        (),
    )


def _completion_disputed(state, event, _context):
    assert event.against_event_id is not None
    source = state.receipt(event.against_event_id)
    source_assignment = (
        state.assignment(source.event.assignment_id)
        if source is not None and source.event.assignment_id is not None
        else None
    )
    if (
        source is None
        or source.outcome is not ReductionOutcome.APPLIED
        or source.event.event_type not in _COMPLETION_EVENTS
        or source.event.job_id != event.job_id
        or source.event.task_id != event.task_id
        or source.event.assignment_id != event.assignment_id
        or source.event.stage_id != event.stage_id
        or source.event.actor_id == event.actor_id
        or source_assignment is None
        or event.actor_id not in source_assignment.member_worker_ids
    ):
        reasons = ("INVALID_AGAINST_EVENT",)
        return state, ReductionOutcome.REJECTED, (), (), reasons
    task = state.task(event.task_id)
    assert task is not None
    if (
        task.status is TaskStatus.SAFE_HOLD
        or task.blocked_pending_resolution
        or any(
            stage.status is StageStatus.SAFE_HOLD
            and (event.stage_id is None or stage.stage_id == event.stage_id)
            for stage in task.stages
        )
    ):
        updated = task
    elif event.stage_id is not None:
        stages = tuple(
            replace(stage, status=StageStatus.DISPUTED)
            if stage.stage_id == event.stage_id
            else stage
            for stage in task.stages
        )
        updated = replace(task, stages=stages)
    else:
        updated = replace(task, status=TaskStatus.DISPUTED)
    return (
        _with_task(state, updated),
        ReductionOutcome.APPLIED,
        (
            _effect(event, EffectType.COMPLETION_DISPUTED),
            _effect(event, EffectType.HUMAN_REVIEW_REQUIRED),
        ),
        (),
        (),
    )


def _worker_acknowledged(state, event, _context):
    directive = state.directive(event.directive_id)
    assert directive is not None
    if directive.worker_informed:
        return state, ReductionOutcome.NOOP, (), (), ()
    updated_directive = replace(
        directive,
        delivery_evidence=DeliveryEvidence.ACKED,
        acknowledged_event_id=event.event_id,
    )
    next_state = _with_directive(state, updated_directive)
    if (
        directive.directive_class is DirectiveClass.ACTION
        and directive.plan_day_id is not None
        and directive.proposed_plan_reference is not None
    ):
        plan = next_state.plan_day(directive.plan_day_id)
        if plan is not None:
            next_state = _with_plan_day(
                next_state,
                replace(plan, confirmed_plan_reference=directive.proposed_plan_reference),
            )
    return (
        next_state,
        ReductionOutcome.APPLIED,
        (_effect(event, EffectType.DIRECTIVE_ACKED),),
        (),
        (),
    )


def _worker_action_exception(state, event, _context):
    if event.reason_class not in {item.value for item in ActionExceptionReason}:
        return state, ReductionOutcome.REJECTED, (), (), ("INVALID_ACTION_EXCEPTION_REASON",)
    directive = state.directive(event.directive_id)
    assert directive is not None
    if directive.directive_type not in {
        DirectiveType.ACTION_REQUIRED,
        DirectiveType.STOP_DIRECTIVE,
    }:
        return state, ReductionOutcome.REJECTED, (), (), ("DIRECTIVE_NOT_ACTION_OR_STOP",)
    updated_directive = replace(directive, exception_event_id=event.event_id)
    next_state = _with_directive(state, updated_directive)
    if directive.directive_class is DirectiveClass.STOP:
        if directive.task_id is None:
            return state, ReductionOutcome.REJECTED, (), (), ("STOP_TASK_REQUIRED",)
        task = state.task(directive.task_id)
        if task is None:
            return state, ReductionOutcome.REJECTED, (), (), ("UNKNOWN_STOP_TASK",)
        updated_directive = replace(updated_directive, stop_in_force=True)
        next_state = _with_directive(next_state, updated_directive)
        next_state = _with_task(
            next_state,
            _safe_hold_task(task, directive_id=directive.directive_id),
        )
        effects = (_effect(event, EffectType.SAFE_HOLD_ENTERED),)
    else:
        effects = (_effect(event, EffectType.ACTION_REEVALUATION_REQUIRED),)
    return next_state, ReductionOutcome.APPLIED, effects, (), ()


def _reduce_system_signal(
    state: M2Aggregate,
    signal: SystemSignal,
    context: PolicyTimeContext,
) -> Reduction:
    if signal.signal_type in {
        SystemSignalType.STATE_REHYDRATED,
        SystemSignalType.SYNC_REPLAYED,
        SystemSignalType.DAY_ROLLED_OVER,
    }:
        return Reduction(state=state, outcome=ReductionOutcome.NOOP)

    if signal.signal_type is SystemSignalType.START_WINDOW_ELAPSED:
        plan = state.plan_day(signal.plan_day_id) if signal.plan_day_id else None
        if plan is None:
            return _system_rejection(state, "UNKNOWN_PLAN_DAY")
        if (
            not context.policy.start_unknown_escalation_enabled
            or context.now < plan.start_at
            or plan.status is PlanDayStatus.ACTIVE
            or plan.status is PlanDayStatus.CLOSED
            or plan.start_unknown_escalated
        ):
            return Reduction(state=state, outcome=ReductionOutcome.NOOP)
        updated = replace(plan, start_unknown_escalated=True)
        effect = _signal_effect(signal, EffectType.START_UNKNOWN_ESCALATED)
        return _system_applied(_with_plan_day(state, updated), (effect,))

    if signal.signal_type is SystemSignalType.DIRECTIVE_E1_ELAPSED:
        directive = state.directive(signal.directive_id) if signal.directive_id else None
        if directive is None:
            return _system_rejection(state, "UNKNOWN_DIRECTIVE")
        scope_errors = list(_directive_scope_errors(state, directive))
        if (
            signal.plan_day_id is not None
            and signal.plan_day_id != directive.plan_day_id
        ):
            scope_errors.append("CROSS_PLAN_DAY_SYSTEM_SIGNAL")
        if signal.task_id is not None and signal.task_id != directive.task_id:
            scope_errors.append("CROSS_TASK_SYSTEM_SIGNAL")
        if scope_errors:
            return _system_rejection_reasons(state, tuple(sorted(set(scope_errors))))
        if (
            directive.worker_informed
            or directive.e1_escalated
            or directive.escalation_due_at is None
            or context.now < directive.escalation_due_at
        ):
            return Reduction(state=state, outcome=ReductionOutcome.NOOP)
        if directive.directive_class is DirectiveClass.ACTION:
            effect_type = EffectType.ACK_MISSING_ESCALATED
        elif directive.directive_class is DirectiveClass.STOP:
            effect_type = EffectType.STOP_UNCONFIRMED
        else:
            return Reduction(state=state, outcome=ReductionOutcome.NOOP)
        updated = replace(directive, e1_escalated=True)
        effect = _signal_effect(
            signal,
            effect_type,
            plan_day_id=directive.plan_day_id,
            task_id=directive.task_id,
            assignment_id=directive.assignment_id,
            directive_id=directive.directive_id,
        )
        return _system_applied(_with_directive(state, updated), (effect,))

    if signal.signal_type is SystemSignalType.EARLY_FINISH_EVALUATED:
        plan = state.plan_day(signal.plan_day_id) if signal.plan_day_id else None
        if plan is None:
            return _system_rejection(state, "UNKNOWN_PLAN_DAY")
        options = context.policy.early_finish_options
        if not options:
            return Reduction(state=state, outcome=ReductionOutcome.NOOP)
        effect = _signal_effect(
            signal,
            EffectType.EARLY_FINISH_SOFT_OPTIONS,
            plan_day_id=plan.plan_day_id,
            details=(("options", ",".join(item.value for item in options)),),
        )
        return _system_applied(state, (effect,))

    if signal.signal_type is SystemSignalType.END_OF_DAY_POLICY_SATISFIED:
        plan = state.plan_day(signal.plan_day_id) if signal.plan_day_id else None
        if plan is None:
            return _system_rejection(state, "UNKNOWN_PLAN_DAY")
        if not plan.day_close_reported:
            return _system_rejection(state, "DAY_CLOSE_NOT_REPORTED")
        if signal.policy_result is not context.policy.end_of_day_action:
            return _system_rejection(state, "END_OF_DAY_POLICY_RESULT_MISMATCH")
        if plan.status is PlanDayStatus.CLOSED:
            return Reduction(state=state, outcome=ReductionOutcome.NOOP)
        updated = replace(
            plan,
            status=PlanDayStatus.CLOSED,
            worker_available=False,
        )
        effect = _signal_effect(
            signal,
            EffectType.DAY_CLOSED,
            plan_day_id=plan.plan_day_id,
            details=(("policy_result", signal.policy_result.value),),
        )
        return _system_applied(_with_plan_day(state, updated), (effect,))

    return _system_rejection(state, "UNSUPPORTED_SYSTEM_SIGNAL")


def _system_applied(
    state: M2Aggregate,
    effects: tuple[SystemEffect, ...],
) -> Reduction:
    return Reduction(
        state=state,
        outcome=ReductionOutcome.APPLIED,
        emitted_effects=effects,
        response_effects=effects,
    )


def _system_rejection(state: M2Aggregate, reason: str) -> Reduction:
    return _system_rejection_reasons(state, (reason,))


def _system_rejection_reasons(
    state: M2Aggregate,
    reasons: tuple[str, ...],
) -> Reduction:
    return Reduction(
        state=state,
        outcome=ReductionOutcome.REJECTED,
        reason_codes=reasons,
    )


def _effect(
    event: FieldEventEnvelope,
    effect_type: EffectType,
    *,
    details: tuple[tuple[str, str], ...] = (),
) -> SystemEffect:
    return SystemEffect(
        effect_type=effect_type,
        source_id=event.event_id,
        plan_day_id=event.plan_day_id,
        job_id=event.job_id,
        task_id=event.task_id,
        assignment_id=event.assignment_id,
        stage_id=event.stage_id,
        directive_id=event.directive_id,
        actor_id=event.actor_id,
        details=details,
    )


def _signal_effect(
    signal: SystemSignal,
    effect_type: EffectType,
    *,
    plan_day_id: str | None = None,
    task_id: str | None = None,
    assignment_id: str | None = None,
    directive_id: str | None = None,
    details: tuple[tuple[str, str], ...] = (),
) -> SystemEffect:
    return SystemEffect(
        effect_type=effect_type,
        source_id=signal.signal_id,
        plan_day_id=plan_day_id,
        task_id=task_id,
        assignment_id=assignment_id,
        directive_id=directive_id,
        details=details,
    )


def _ordinary_task_status(task: TaskState, status: TaskStatus) -> TaskState:
    if (
        task.status is TaskStatus.SAFE_HOLD
        or task.blocked_pending_resolution
        or any(stage.status is StageStatus.SAFE_HOLD for stage in task.stages)
    ):
        return task
    return replace(task, status=status)


def _merge_evidence(
    existing: tuple[EvidenceItem, ...],
    submitted: tuple[EvidenceItem, ...],
) -> tuple[EvidenceItem, ...]:
    by_id = {item.evidence_id: item for item in existing}
    for item in submitted:
        by_id.setdefault(item.evidence_id, item)
    return tuple(sorted(by_id.values(), key=lambda item: item.evidence_id))


def _safe_hold_task(
    task: TaskState,
    *,
    directive_id: str | None,
    stage_id: str | None = None,
) -> TaskState:
    stages = tuple(
        replace(stage, status=StageStatus.SAFE_HOLD)
        if stage_id is not None and stage.stage_id == stage_id
        else stage
        for stage in task.stages
    )
    return replace(
        task,
        status=TaskStatus.SAFE_HOLD,
        stages=stages,
        safe_hold_directive_id=directive_id or task.safe_hold_directive_id,
        blocked_pending_resolution=True,
        execution_authorized=False,
    )


def _with_plan_day(state: M2Aggregate, updated: PlanDayState) -> M2Aggregate:
    return replace(
        state,
        plan_days=tuple(
            updated if item.plan_day_id == updated.plan_day_id else item
            for item in state.plan_days
        ),
    )


def _with_task(state: M2Aggregate, updated: TaskState) -> M2Aggregate:
    return replace(
        state,
        tasks=tuple(
            updated if item.definition.task_id == updated.definition.task_id else item
            for item in state.tasks
        ),
    )


def _with_assignment(
    state: M2Aggregate,
    updated: AssignmentState,
) -> M2Aggregate:
    return replace(
        state,
        assignments=tuple(
            updated if item.assignment_id == updated.assignment_id else item
            for item in state.assignments
        ),
    )


def _with_directive(state: M2Aggregate, updated: DirectiveState) -> M2Aggregate:
    return replace(
        state,
        directives=tuple(
            updated if item.directive_id == updated.directive_id else item
            for item in state.directives
        ),
    )
