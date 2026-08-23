from werkcrew_ai.agent import (
    AgentActivityStore,
    AgentStatus,
    WerkcrewAgentOrchestrator,
    WerkcrewAgentTools,
)
from werkcrew_ai.domain import WorkflowState
from werkcrew_ai.infrastructure.demo_repository import (
    load_demo_pricing_data,
    load_demo_site_visit_report,
)
from werkcrew_ai.infrastructure.demo_workflow_store import DemoWorkflowStore
from werkcrew_ai.pricing import PricingStatus

from .fakes import calculate_and_interrupt_model, calculate_pricing_model


def planned_store(**kwargs) -> DemoWorkflowStore:
    store = DemoWorkflowStore(**kwargs)
    store.assess_job()
    store.create_site_visit()
    store.submit_report(load_demo_site_visit_report())
    store.generate_plans()
    return store


def test_get_job_state_routes_plans_to_pricing_and_complete_pricing_to_read() -> None:
    store = planned_store()
    tools = WerkcrewAgentTools(store, AgentActivityStore())

    before = tools.get_job_state()
    assert before["workflow_state"] == "PLANS_READY_FOR_REVIEW"
    assert before["allowed_next_actions"] == ["calculate_plan_quotes"]

    tools.calculate_plan_quotes()
    after = tools.get_job_state()
    assert after["workflow_state"] == "PRICING_READY_FOR_REVIEW"
    assert after["allowed_next_actions"] == ["request_owner_decision"]
    assert set(after["pricing_statuses"].values()) == {"COMPLETE"}


def test_calculate_plan_quotes_keeps_m3_plans_immutable() -> None:
    store = planned_store()
    before = store.get().planning_result

    result = WerkcrewAgentTools(store, AgentActivityStore()).calculate_plan_quotes()

    assert store.get().planning_result == before
    assert result["workflow_state"] == "PRICING_READY_FOR_REVIEW"
    assert result["variants_comparable"] is True
    assert result["comparison"]["largest_cost_driver"] == "LABOR"


def test_get_pricing_results_is_read_only() -> None:
    store = planned_store()
    store.calculate_plan_quotes()
    before = store.get()

    result = WerkcrewAgentTools(store, AgentActivityStore()).get_pricing_results()

    assert result["workflow_state"] == "PRICING_READY_FOR_REVIEW"
    assert len(result["pricing_results"]) == 2
    assert store.get() == before


def test_store_calculation_is_idempotent_for_same_fingerprints() -> None:
    store = planned_store()
    first = store.calculate_plan_quotes().pricing_results
    second = store.calculate_plan_quotes().pricing_results
    assert second[0] is first[0]
    assert second[1] is first[1]


def test_partial_pricing_keeps_owner_gate_closed() -> None:
    context, usages = load_demo_pricing_data()
    store = planned_store(
        pricing_context=context,
        vehicle_usages=tuple(item for item in usages if item.plan_id.endswith("-a")),
    )
    activity = AgentActivityStore()

    outcome = WerkcrewAgentOrchestrator(
        store,
        activity,
        model=calculate_pricing_model(),
    ).run()

    assert store.get().workflow_state is WorkflowState.PLANS_READY_FOR_REVIEW
    assert [item.status for item in store.get().pricing_results] == [
        PricingStatus.COMPLETE,
        PricingStatus.INCOMPLETE,
    ]
    assert store.get().pricing_comparison is None
    assert outcome.runtime_state.status is AgentStatus.WAITING_FOR_PRICING_INPUT
    assert "calculate_plan_quotes" in outcome.runtime_state.invoked_tools


def test_complete_pricing_opens_owner_gate_without_selection_or_approval() -> None:
    store = planned_store()
    activity = AgentActivityStore()
    outcome = WerkcrewAgentOrchestrator(
        store,
        activity,
        model=calculate_and_interrupt_model(),
    ).run()

    assert store.get().workflow_state is WorkflowState.PRICING_READY_FOR_REVIEW
    assert outcome.runtime_state.status is AgentStatus.WAITING_FOR_OWNER_REVIEW
    tool_names = set(
        WerkcrewAgentOrchestrator(
            store, activity, model=calculate_pricing_model()
        ).build_agent().tool_names
    )
    assert not any("choose" in item or "approve" in item for item in tool_names)
