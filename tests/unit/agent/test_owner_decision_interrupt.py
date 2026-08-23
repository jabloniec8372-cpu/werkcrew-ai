from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from strands.session.file_session_manager import FileSessionManager

from werkcrew_ai.agent import (
    WERKCREW_AGENT_ID,
    AgentActivityStore,
    AgentStatus,
    WerkcrewAgentOrchestrator,
    WerkcrewAgentTools,
    workflow_session_id,
)
from werkcrew_ai.domain import (
    OwnerDecisionAction,
    OwnerDecisionGateStatus,
    WorkflowState,
)
from werkcrew_ai.infrastructure.demo_repository import (
    load_demo_pricing_data,
    load_demo_site_visit_report,
)
from werkcrew_ai.infrastructure.demo_workflow_store import (
    DemoWorkflowStore,
    OwnerDecisionConflictError,
    OwnerDecisionInputError,
)

from .fakes import ErrorModel, owner_interrupt_model, owner_resume_model


def priced_store(**kwargs) -> DemoWorkflowStore:
    store = DemoWorkflowStore(**kwargs)
    store.assess_job()
    store.create_site_visit()
    store.submit_report(load_demo_site_visit_report())
    store.generate_plans()
    store.calculate_plan_quotes()
    return store


def start_real_local_interrupt(
    store: DemoWorkflowStore,
    activity: AgentActivityStore,
    storage_dir: Path,
):
    outcome = WerkcrewAgentOrchestrator(
        store,
        activity,
        model=owner_interrupt_model(),
        session_storage_dir=str(storage_dir),
    ).run()
    gate = store.get().pending_owner_gate
    assert outcome.stop_reason == "interrupt"
    assert gate is not None
    assert gate.status is OwnerDecisionGateStatus.PENDING
    assert gate.interrupt_id == outcome.interrupt_id
    return outcome, gate


@pytest.mark.parametrize(
    ("action", "plan_position", "expected_state"),
    [
        (OwnerDecisionAction.APPROVE_PLAN, 0, WorkflowState.PLAN_APPROVED),
        (OwnerDecisionAction.APPROVE_PLAN, 1, WorkflowState.PLAN_APPROVED),
        (OwnerDecisionAction.REJECT_ALL, None, WorkflowState.PLANS_REJECTED),
    ],
)
def test_real_interrupt_fresh_agent_resume_happy_paths(
    tmp_path: Path,
    action: OwnerDecisionAction,
    plan_position: int | None,
    expected_state: WorkflowState,
) -> None:
    store = priced_store()
    activity = AgentActivityStore()
    first, gate = start_real_local_interrupt(store, activity, tmp_path)
    plans = store.get().planning_result.plans
    selected_plan_id = plans[plan_position].id if plan_position is not None else None

    second = WerkcrewAgentOrchestrator(
        store,
        activity,
        model=owner_resume_model(),
        session_storage_dir=str(tmp_path),
    ).resume_owner_decision(
        gate_id=gate.gate_id,
        action=action.value,
        selected_plan_id=selected_plan_id,
    )

    snapshot = store.get()
    assert first.agent_instance_id != second.agent_instance_id
    assert first.session_id == second.session_id == gate.session_id
    assert first.agent_id == second.agent_id == gate.agent_id == WERKCREW_AGENT_ID
    assert snapshot.workflow_state is expected_state
    assert snapshot.owner_decision is not None
    assert snapshot.owner_decision.action is action
    assert snapshot.owner_decision.selected_plan_id == selected_plan_id
    assert snapshot.owner_decision.actor_role == "OWNER"
    assert snapshot.owner_decision.source == "COORDINATOR_UI"
    assert snapshot.pending_owner_gate.status is OwnerDecisionGateStatus.RESOLVED
    assert snapshot.pending_owner_gate.interrupt_id == first.interrupt_id
    assert second.runtime_state.status is AgentStatus.OWNER_DECISION_RECORDED
    assert (
        tmp_path / f"session_{first.session_id}" / "session.json"
    ).is_file()
    assert any(
        entry.action == "Pending interrupt resumed" for entry in activity.get().timeline
    )


def test_fresh_agents_use_distinct_managers_with_same_storage_and_session(
    tmp_path: Path,
) -> None:
    managers = []

    def tracking_factory(session_id: str, storage_dir: str):
        manager = FileSessionManager(session_id=session_id, storage_dir=storage_dir)
        managers.append(manager)
        return manager

    store = priced_store()
    activity = AgentActivityStore()
    first = WerkcrewAgentOrchestrator(
        store,
        activity,
        model=owner_interrupt_model(),
        session_storage_dir=str(tmp_path),
        session_manager_factory=tracking_factory,
    ).run()
    gate = store.get().pending_owner_gate
    second = WerkcrewAgentOrchestrator(
        store,
        activity,
        model=owner_resume_model(),
        session_storage_dir=str(tmp_path),
        session_manager_factory=tracking_factory,
    ).resume_owner_decision(
        gate_id=gate.gate_id,
        action="REJECT_ALL",
        selected_plan_id=None,
    )

    assert len(managers) == 2
    assert managers[0] is not managers[1]
    assert managers[0].session_id == managers[1].session_id == first.session_id
    assert managers[0].storage_dir == managers[1].storage_dir == str(tmp_path)
    assert first.agent_instance_id != second.agent_instance_id


def test_stale_pricing_invalidates_gate_without_decision(tmp_path: Path) -> None:
    store = priced_store()
    activity = AgentActivityStore()
    _, gate = start_real_local_interrupt(store, activity, tmp_path)
    snapshot = store.get()
    changed = replace(
        snapshot.pricing_results[0],
        gross_price=snapshot.pricing_results[0].gross_price + Decimal("0.01"),
    )
    store._snapshot = replace(  # test-only stale external-state simulation
        snapshot,
        pricing_results=(changed, *snapshot.pricing_results[1:]),
    )

    with pytest.raises(OwnerDecisionConflictError, match="Pricing zmienił"):
        store.claim_owner_response(
            gate_id=gate.gate_id,
            action="APPROVE_PLAN",
            selected_plan_id=gate.eligible_plan_ids[0],
            expected_session_id=gate.session_id,
            expected_agent_id=gate.agent_id,
        )

    assert store.get().owner_decision is None
    assert store.get().pending_owner_gate.status is OwnerDecisionGateStatus.INVALIDATED


def test_identical_duplicate_returns_same_decision_without_second_resume(
    tmp_path: Path,
) -> None:
    store = priced_store()
    activity = AgentActivityStore()
    _, gate = start_real_local_interrupt(store, activity, tmp_path)
    plan_id = gate.eligible_plan_ids[0]
    orchestrator = WerkcrewAgentOrchestrator(
        store,
        activity,
        model=owner_resume_model(),
        session_storage_dir=str(tmp_path),
    )
    first = orchestrator.resume_owner_decision(
        gate_id=gate.gate_id,
        action="APPROVE_PLAN",
        selected_plan_id=plan_id,
    )
    decision = store.get().owner_decision

    duplicate = WerkcrewAgentOrchestrator(
        store,
        activity,
        model=ErrorModel(),
        session_storage_dir=str(tmp_path),
    ).resume_owner_decision(
        gate_id=gate.gate_id,
        action="APPROVE_PLAN",
        selected_plan_id=plan_id,
    )

    assert first.stop_reason == "end_turn"
    assert duplicate.stop_reason == "idempotent"
    assert duplicate.agent_instance_id is None
    assert store.get().owner_decision is decision


def test_conflicting_duplicate_is_rejected(tmp_path: Path) -> None:
    store = priced_store()
    activity = AgentActivityStore()
    _, gate = start_real_local_interrupt(store, activity, tmp_path)
    WerkcrewAgentOrchestrator(
        store,
        activity,
        model=owner_resume_model(),
        session_storage_dir=str(tmp_path),
    ).resume_owner_decision(
        gate_id=gate.gate_id,
        action="APPROVE_PLAN",
        selected_plan_id=gate.eligible_plan_ids[0],
    )

    with pytest.raises(OwnerDecisionConflictError, match="inną odpowiedzią"):
        WerkcrewAgentOrchestrator(
            store,
            activity,
            model=ErrorModel(),
            session_storage_dir=str(tmp_path),
        ).resume_owner_decision(
            gate_id=gate.gate_id,
            action="REJECT_ALL",
            selected_plan_id=None,
        )


def test_second_decision_after_reject_all_is_rejected(tmp_path: Path) -> None:
    store = priced_store()
    activity = AgentActivityStore()
    _, gate = start_real_local_interrupt(store, activity, tmp_path)
    WerkcrewAgentOrchestrator(
        store,
        activity,
        model=owner_resume_model(),
        session_storage_dir=str(tmp_path),
    ).resume_owner_decision(
        gate_id=gate.gate_id,
        action="REJECT_ALL",
        selected_plan_id=None,
    )

    with pytest.raises(OwnerDecisionConflictError, match="inną odpowiedzią"):
        WerkcrewAgentOrchestrator(
            store,
            activity,
            model=ErrorModel(),
            session_storage_dir=str(tmp_path),
        ).resume_owner_decision(
            gate_id=gate.gate_id,
            action="APPROVE_PLAN",
            selected_plan_id=gate.eligible_plan_ids[0],
        )


def test_allowed_next_actions_do_not_offer_second_gate(tmp_path: Path) -> None:
    store = priced_store()
    activity = AgentActivityStore()
    tools = WerkcrewAgentTools(store, activity)
    assert tools.get_job_state()["allowed_next_actions"] == [
        "request_owner_decision"
    ]
    _, gate = start_real_local_interrupt(store, activity, tmp_path)
    pending = tools.get_job_state()
    assert "request_owner_decision" not in pending["allowed_next_actions"]

    WerkcrewAgentOrchestrator(
        store,
        activity,
        model=owner_resume_model(),
        session_storage_dir=str(tmp_path),
    ).resume_owner_decision(
        gate_id=gate.gate_id,
        action="REJECT_ALL",
        selected_plan_id=None,
    )
    assert tools.get_job_state()["allowed_next_actions"] == []


def test_wrong_gate_and_nonexistent_plan_never_resume(tmp_path: Path) -> None:
    store = priced_store()
    activity = AgentActivityStore()
    _, gate = start_real_local_interrupt(store, activity, tmp_path)
    orchestrator = WerkcrewAgentOrchestrator(
        store,
        activity,
        model=ErrorModel(),
        session_storage_dir=str(tmp_path),
    )
    with pytest.raises(OwnerDecisionConflictError, match="gate_id"):
        orchestrator.resume_owner_decision(
            gate_id="owner-gate-wrong",
            action="REJECT_ALL",
            selected_plan_id=None,
        )
    with pytest.raises(OwnerDecisionInputError, match="eligible_plan_ids"):
        orchestrator.resume_owner_decision(
            gate_id=gate.gate_id,
            action="APPROVE_PLAN",
            selected_plan_id="plan-does-not-exist",
        )
    assert store.get().owner_decision is None
    assert store.get().pending_owner_gate.status is OwnerDecisionGateStatus.PENDING


def test_incomplete_pricing_cannot_create_owner_gate() -> None:
    context, usages = load_demo_pricing_data()
    store = DemoWorkflowStore(
        pricing_context=context,
        vehicle_usages=tuple(item for item in usages if item.plan_id.endswith("-a")),
    )
    store.assess_job()
    store.create_site_visit()
    store.submit_report(load_demo_site_visit_report())
    store.generate_plans()
    store.calculate_plan_quotes()

    with pytest.raises(OwnerDecisionConflictError, match="PRICING_READY_FOR_REVIEW"):
        store.prepare_owner_gate(session_id="session", agent_id=WERKCREW_AGENT_ID)
    assert store.get().pending_owner_gate is None


def test_single_complete_variant_has_one_legal_approve_option(tmp_path: Path) -> None:
    store = DemoWorkflowStore()
    store.assess_job()
    store.create_site_visit()
    store.submit_report(load_demo_site_visit_report())
    planned = store.generate_plans()
    only_a = replace(planned.planning_result, plans=(planned.planning_result.plans[0],))
    store._snapshot = replace(planned, planning_result=only_a)  # test scenario
    store.calculate_plan_quotes()
    _, gate = start_real_local_interrupt(store, AgentActivityStore(), tmp_path)

    assert gate.eligible_plan_ids == (only_a.plans[0].id,)


def test_workflow_or_session_mismatch_invalidates_gate(tmp_path: Path) -> None:
    store = priced_store()
    activity = AgentActivityStore()
    _, gate = start_real_local_interrupt(store, activity, tmp_path)
    snapshot = store.get()
    store._snapshot = replace(
        snapshot,
        pending_owner_gate=replace(gate, session_id="wrong-session"),
    )

    with pytest.raises(OwnerDecisionConflictError, match="session_id/agent_id"):
        store.claim_owner_response(
            gate_id=gate.gate_id,
            action="REJECT_ALL",
            selected_plan_id=None,
            expected_session_id=gate.session_id,
            expected_agent_id=gate.agent_id,
        )
    assert store.get().pending_owner_gate.status is OwnerDecisionGateStatus.INVALIDATED


def test_resume_without_pending_gate_is_rejected(tmp_path: Path) -> None:
    store = priced_store()
    with pytest.raises(OwnerDecisionConflictError, match="gate_id"):
        WerkcrewAgentOrchestrator(
            store,
            AgentActivityStore(),
            model=ErrorModel(),
            session_storage_dir=str(tmp_path),
        ).resume_owner_decision(
            gate_id="none",
            action="REJECT_ALL",
            selected_plan_id=None,
        )


@pytest.mark.parametrize(
    ("action", "plan_id", "message"),
    [
        ("MALFORMED", None, "Nieznana"),
        ("APPROVE_PLAN", None, "wymaga"),
        ("REJECT_ALL", "plan-id", "nie może"),
    ],
)
def test_malformed_owner_actions_are_rejected_before_resume(
    tmp_path: Path,
    action: str,
    plan_id: str | None,
    message: str,
) -> None:
    store = priced_store()
    activity = AgentActivityStore()
    _, gate = start_real_local_interrupt(store, activity, tmp_path)
    with pytest.raises(OwnerDecisionInputError, match=message):
        WerkcrewAgentOrchestrator(
            store,
            activity,
            model=ErrorModel(),
            session_storage_dir=str(tmp_path),
        ).resume_owner_decision(
            gate_id=gate.gate_id,
            action=action,
            selected_plan_id=plan_id,
        )
    assert store.get().pending_owner_gate.status is OwnerDecisionGateStatus.PENDING
    assert store.get().owner_decision is None


def test_file_session_manager_unavailable_initial_has_no_fake_fallback(
    tmp_path: Path,
) -> None:
    store = priced_store()

    def unavailable(_session_id: str, _storage_dir: str):
        raise OSError("session storage unavailable")

    outcome = WerkcrewAgentOrchestrator(
        store,
        AgentActivityStore(),
        model=owner_interrupt_model(),
        session_storage_dir=str(tmp_path),
        session_manager_factory=unavailable,
    ).run()

    assert outcome.runtime_state.status is AgentStatus.ERROR
    assert store.get().pending_owner_gate is None
    assert store.get().owner_decision is None


def test_file_session_manager_unavailable_on_resume_commits_no_decision(
    tmp_path: Path,
) -> None:
    store = priced_store()
    activity = AgentActivityStore()
    _, gate = start_real_local_interrupt(store, activity, tmp_path)

    def unavailable(_session_id: str, _storage_dir: str):
        raise OSError("resume storage unavailable")

    outcome = WerkcrewAgentOrchestrator(
        store,
        activity,
        model=owner_resume_model(),
        session_storage_dir=str(tmp_path),
        session_manager_factory=unavailable,
    ).resume_owner_decision(
        gate_id=gate.gate_id,
        action="REJECT_ALL",
        selected_plan_id=None,
    )

    assert outcome.runtime_state.status is AgentStatus.ERROR
    assert store.get().workflow_state is WorkflowState.PRICING_READY_FOR_REVIEW
    assert store.get().owner_decision is None
    assert store.get().pending_owner_gate.status is OwnerDecisionGateStatus.RESUMING


def test_bedrock_error_before_commit_leaves_workflow_unchanged(tmp_path: Path) -> None:
    store = priced_store()
    outcome = WerkcrewAgentOrchestrator(
        store,
        AgentActivityStore(),
        model=ErrorModel(),
        session_storage_dir=str(tmp_path),
    ).run()
    assert outcome.runtime_state.status is AgentStatus.ERROR
    assert store.get().workflow_state is WorkflowState.PRICING_READY_FOR_REVIEW
    assert store.get().owner_decision is None


def test_bedrock_error_after_atomic_commit_preserves_decision(tmp_path: Path) -> None:
    store = priced_store()
    activity = AgentActivityStore()
    _, gate = start_real_local_interrupt(store, activity, tmp_path)
    outcome = WerkcrewAgentOrchestrator(
        store,
        activity,
        model=ErrorModel(),
        session_storage_dir=str(tmp_path),
    ).resume_owner_decision(
        gate_id=gate.gate_id,
        action="APPROVE_PLAN",
        selected_plan_id=gate.eligible_plan_ids[0],
    )
    assert outcome.runtime_state.status is AgentStatus.ERROR
    assert store.get().workflow_state is WorkflowState.PLAN_APPROVED
    assert store.get().owner_decision is not None
    assert store.get().pending_owner_gate.status is OwnerDecisionGateStatus.RESOLVED


def test_reset_same_job_id_gets_new_instance_and_rejects_old_gate(
    tmp_path: Path,
) -> None:
    store = priced_store()
    activity = AgentActivityStore()
    first, gate = start_real_local_interrupt(store, activity, tmp_path)
    old_instance = store.get().workflow_instance_id
    old_job_id = store.get().current_job_request.id

    reset = store.reset()

    assert reset.current_job_request.id == old_job_id
    assert reset.workflow_instance_id != old_instance
    assert workflow_session_id(old_job_id, reset.workflow_instance_id) != first.session_id
    with pytest.raises(OwnerDecisionConflictError):
        store.claim_owner_response(
            gate_id=gate.gate_id,
            action="REJECT_ALL",
            selected_plan_id=None,
            expected_session_id=workflow_session_id(
                reset.current_job_request.id,
                reset.workflow_instance_id,
            ),
            expected_agent_id=WERKCREW_AGENT_ID,
        )


def test_tool_reinvoked_after_decision_returns_already_decided(tmp_path: Path) -> None:
    store = priced_store()
    activity = AgentActivityStore()
    _, gate = start_real_local_interrupt(store, activity, tmp_path)
    WerkcrewAgentOrchestrator(
        store,
        activity,
        model=owner_resume_model(),
        session_storage_dir=str(tmp_path),
    ).resume_owner_decision(
        gate_id=gate.gate_id,
        action="REJECT_ALL",
        selected_plan_id=None,
    )

    result = WerkcrewAgentTools(store, activity).request_owner_decision(None)
    assert result["status"] == "ALREADY_DECIDED"
    assert store.get().owner_decision.decision_id == result["decision_id"]


def test_two_concurrent_clicks_accept_at_most_one_claim(tmp_path: Path) -> None:
    store = priced_store()
    activity = AgentActivityStore()
    _, gate = start_real_local_interrupt(store, activity, tmp_path)

    def claim():
        try:
            return store.claim_owner_response(
                gate_id=gate.gate_id,
                action="APPROVE_PLAN",
                selected_plan_id=gate.eligible_plan_ids[0],
                expected_session_id=gate.session_id,
                expected_agent_id=gate.agent_id,
            )
        except OwnerDecisionConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: claim(), range(2)))

    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert store.get().pending_owner_gate.status is OwnerDecisionGateStatus.RESUMING


def test_pricing_is_read_only_while_gate_is_active(tmp_path: Path) -> None:
    store = priced_store()
    activity = AgentActivityStore()
    _, _ = start_real_local_interrupt(store, activity, tmp_path)
    before = store.get().pricing_results

    result = WerkcrewAgentTools(store, activity).get_pricing_results()
    with pytest.raises(OwnerDecisionConflictError, match="zamrożony"):
        store.calculate_plan_quotes()

    assert store.get().pricing_results is before
    assert len(result["pricing_results"]) == 2
