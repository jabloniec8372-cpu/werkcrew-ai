"""Thin Strands tool adapters around the deterministic M1-M3 capabilities."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from strands import tool
from strands.types.tools import ToolContext

from werkcrew_ai.agent.state import AgentActivityStore
from werkcrew_ai.domain import OwnerDecisionGateStatus, WorkflowState
from werkcrew_ai.infrastructure.demo_workflow_store import DemoWorkflowStore


def _public_value(value: Any) -> Any:
    if is_dataclass(value):
        return _public_value(asdict(value))
    if isinstance(value, dict):
        return {key: _public_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_public_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


class WerkcrewAgentTools:
    """Registered tools; business decisions remain in the existing services."""

    def __init__(
        self,
        workflow_store: DemoWorkflowStore,
        activity_store: AgentActivityStore,
    ) -> None:
        self.workflow_store = workflow_store
        self.activity_store = activity_store

    @tool
    def get_job_state(self) -> dict[str, Any]:
        """Read state, persisted M1 assessment, and allowed next workflow actions."""

        snapshot = self.workflow_store.get()
        if snapshot.workflow_state is WorkflowState.RECEIVED:
            allowed_next_actions = ["assess_job"]
        elif (
            snapshot.workflow_state is WorkflowState.SITE_VISIT_REQUIRED
            and snapshot.site_visit is None
        ):
            allowed_next_actions = ["prepare_site_visit"]
        elif snapshot.workflow_state is WorkflowState.READY_FOR_PLANNING:
            allowed_next_actions = ["generate_crew_plans"]
        elif snapshot.workflow_state is WorkflowState.PLANS_READY_FOR_REVIEW:
            allowed_next_actions = ["calculate_plan_quotes"]
        elif snapshot.workflow_state is WorkflowState.PRICING_READY_FOR_REVIEW:
            gate = snapshot.pending_owner_gate
            if snapshot.owner_decision is not None:
                allowed_next_actions = []
            elif gate is None:
                allowed_next_actions = ["request_owner_decision"]
            elif gate.status in {
                OwnerDecisionGateStatus.PENDING,
                OwnerDecisionGateStatus.RESUMING,
            }:
                allowed_next_actions = ["get_pricing_results"]
            else:
                allowed_next_actions = []
        elif snapshot.workflow_state in {
            WorkflowState.PLAN_APPROVED,
            WorkflowState.PLANS_REJECTED,
        }:
            allowed_next_actions = []
        elif snapshot.site_visit is not None and snapshot.site_visit.report is not None:
            allowed_next_actions = ["validate_site_visit_report"]
        else:
            allowed_next_actions = ["get_site_visit_status"]
        result = {
            "workflow_state": snapshot.workflow_state.value,
            "job_request": _public_value(snapshot.current_job_request),
            "assessment_completed": snapshot.initial_assessment is not None,
            "assessment": _public_value(snapshot.initial_assessment),
            "allowed_next_actions": allowed_next_actions,
            "site_visit": _public_value(snapshot.site_visit),
            "post_visit_validation": _public_value(snapshot.post_visit_validation),
            "plans_exist": bool(
                snapshot.planning_result and snapshot.planning_result.plans
            ),
            "plan_count": (
                len(snapshot.planning_result.plans)
                if snapshot.planning_result is not None
                else 0
            ),
            "pricing_results_exist": bool(snapshot.pricing_results),
            "pricing_statuses": {
                item.plan_id: item.status.value for item in snapshot.pricing_results
            },
            "pricing_issues": {
                item.plan_id: _public_value(item.issues)
                for item in snapshot.pricing_results
                if item.issues
            },
            "owner_gate_status": (
                snapshot.pending_owner_gate.status.value
                if snapshot.pending_owner_gate is not None
                else None
            ),
            "owner_decision_recorded": snapshot.owner_decision is not None,
        }
        self.activity_store.record(
            action="Read current job state",
            tool="get_job_state",
            public_result=f"Workflow: {snapshot.workflow_state.value}.",
            workflow_state=snapshot.workflow_state,
            rationale="Odczyt nie zmienił danych ani stanu workflow.",
        )
        return result

    @tool
    def assess_job(self) -> dict[str, Any]:
        """Persist required M1 assessment for RECEIVED; idempotent if already assessed."""

        snapshot = self.workflow_store.assess_job()
        assessment = snapshot.initial_assessment
        if assessment is None:
            raise RuntimeError("Deterministyczna ocena M1 nie zwróciła wyniku.")
        result = {
            "workflow_state": snapshot.workflow_state.value,
            "decision": assessment.decision.value,
            "missing_information": list(assessment.missing_information),
            "detected_risks": list(assessment.detected_risks),
            "site_visit_required": assessment.site_visit_required,
            "rationale": assessment.rationale,
            "rule_version": assessment.rule_version,
        }
        self.activity_store.record(
            action="Assessed job",
            tool="assess_job",
            public_result=(
                f"{assessment.decision.value}: "
                f"{len(assessment.missing_information)} braków, "
                f"{len(assessment.detected_risks)} ryzyka."
            ),
            workflow_state=snapshot.workflow_state,
            rationale=assessment.rationale,
        )
        return result

    @tool
    def prepare_site_visit(self) -> dict[str, Any]:
        """Create M2 visit only after persisted M1 state says SITE_VISIT_REQUIRED."""

        snapshot = self.workflow_store.create_site_visit()
        visit = snapshot.site_visit
        if visit is None:
            raise RuntimeError("Deterministyczny workflow nie utworzył oględzin.")
        employee = snapshot.assigned_employee
        result = {
            "workflow_state": snapshot.workflow_state.value,
            "site_visit_id": visit.id,
            "brief": _public_value(visit.brief),
            "assigned_employee": (
                _public_value(employee) if employee is not None else None
            ),
            "scheduled_at": (
                visit.scheduled_at.isoformat() if visit.scheduled_at else None
            ),
            "assignment_message": snapshot.assignment_message,
        }
        self.activity_store.record(
            action="Prepared site visit",
            tool="prepare_site_visit",
            public_result=(
                f"Oględziny {visit.id}; przydział: "
                f"{employee.name if employee else 'brak automatycznego przydziału'}."
            ),
            workflow_state=snapshot.workflow_state,
            rationale=snapshot.assignment_message,
        )
        return result

    @tool
    def get_site_visit_status(self) -> dict[str, Any]:
        """Read assignment, report receipt and unresolved post-visit issues."""

        snapshot = self.workflow_store.get()
        visit = snapshot.site_visit
        validation = snapshot.post_visit_validation
        result = {
            "workflow_state": snapshot.workflow_state.value,
            "site_visit_status": visit.status.value if visit else None,
            "assigned_employee": (
                snapshot.assigned_employee.name if snapshot.assigned_employee else None
            ),
            "scheduled_at": (
                visit.scheduled_at.isoformat() if visit and visit.scheduled_at else None
            ),
            "report_received": bool(visit and visit.report),
            "remaining_missing_information": (
                list(validation.remaining_missing_information) if validation else []
            ),
            "unresolved_risks": (
                list(validation.unresolved_risks) if validation else []
            ),
        }
        self.activity_store.record(
            action="Checked site visit status",
            tool="get_site_visit_status",
            public_result=(
                "Raport terenowy otrzymany."
                if result["report_received"]
                else "Raport terenowy nie został jeszcze otrzymany."
            ),
            workflow_state=snapshot.workflow_state,
            rationale="Status pochodzi z deterministycznego store M2.",
        )
        return result

    @tool
    def validate_site_visit_report(self) -> dict[str, Any]:
        """Revalidate the already submitted human Field report with M2 rules."""

        snapshot = self.workflow_store.validate_stored_report()
        validation = snapshot.post_visit_validation
        if validation is None:
            raise RuntimeError("Walidacja raportu nie zwróciła wyniku.")
        result = {
            "workflow_state": snapshot.workflow_state.value,
            "ready_for_planning": validation.ready_for_planning,
            "remaining_missing_information": list(
                validation.remaining_missing_information
            ),
            "unresolved_risks": list(validation.unresolved_risks),
            "updated_job_request": _public_value(validation.updated_job_request),
            "rationale": validation.rationale,
        }
        self.activity_store.record(
            action="Validated site visit report",
            tool="validate_site_visit_report",
            public_result=(
                f"{snapshot.workflow_state.value}: "
                f"{len(validation.remaining_missing_information)} braków, "
                f"{len(validation.unresolved_risks)} ryzyka."
            ),
            workflow_state=snapshot.workflow_state,
            rationale=validation.rationale,
        )
        return result

    @tool
    def generate_crew_plans(self) -> dict[str, Any]:
        """Run the deterministic M3 planner and return variants and trace."""

        snapshot = self.workflow_store.generate_plans()
        planning_result = snapshot.planning_result
        if planning_result is None:
            raise RuntimeError("Deterministyczny planner nie zwrócił wyniku.")
        plans = []
        for plan in planning_result.plans:
            payload = _public_value(plan)
            payload["employees"] = [
                snapshot.employee_name(employee_id)
                for employee_id in plan.employee_ids
            ]
            payload["vehicles"] = [
                snapshot.vehicle_name(vehicle_id) for vehicle_id in plan.vehicle_ids
            ]
            plans.append(payload)
        result = {
            "workflow_state": snapshot.workflow_state.value,
            "plans": plans,
            "decision_trace": _public_value(planning_result.decision_trace),
            "rejected_candidates_and_resources": _public_value(
                snapshot.planning_rejections
            ),
            "inability_reasons": list(planning_result.inability_reasons),
            "rule_version": planning_result.rule_version,
        }
        self.activity_store.record(
            action="Generated crew plans",
            tool="generate_crew_plans",
            public_result=(
                f"Deterministyczny planner {planning_result.rule_version} "
                f"utworzył {len(planning_result.plans)} warianty."
            ),
            workflow_state=snapshot.workflow_state,
            rationale=(
                "Warianty i decision trace pochodzą z M3; agent ich nie obliczał."
            ),
        )
        return result

    @tool
    def calculate_plan_quotes(self) -> dict[str, Any]:
        """Deterministically price existing M3 plans; never modify or choose them."""

        snapshot = self.workflow_store.calculate_plan_quotes()
        result = {
            "workflow_state": snapshot.workflow_state.value,
            "pricing_results": _public_value(snapshot.pricing_results),
            "all_existing_variants_complete": bool(snapshot.pricing_results)
            and all(item.status.value == "COMPLETE" for item in snapshot.pricing_results),
            "variants_comparable": snapshot.pricing_comparison is not None,
            "comparison": _public_value(snapshot.pricing_comparison),
        }
        statuses = ", ".join(
            f"{item.plan_label}={item.status.value}"
            for item in snapshot.pricing_results
        )
        self.activity_store.record(
            action="Calculated plan quotes",
            tool="calculate_plan_quotes",
            public_result=f"Deterministyczny pricing M5: {statuses}.",
            workflow_state=snapshot.workflow_state,
            rationale=(
                "Kwoty pochodzą z Decimal, jawnej PricingPolicy i zapisanych "
                "snapshotów wejścia; plany M3 nie zostały zmienione."
            ),
        )
        return result

    @tool
    def get_pricing_results(self) -> dict[str, Any]:
        """Read saved immutable pricing results without recalculating them."""

        snapshot = self.workflow_store.get()
        result = {
            "workflow_state": snapshot.workflow_state.value,
            "pricing_results": _public_value(snapshot.pricing_results),
            "variants_comparable": snapshot.pricing_comparison is not None,
            "comparison": _public_value(snapshot.pricing_comparison),
        }
        self.activity_store.record(
            action="Read pricing results",
            tool="get_pricing_results",
            public_result=f"Odczytano {len(snapshot.pricing_results)} wyników pricingu.",
            workflow_state=snapshot.workflow_state,
            rationale="Odczyt nie przeliczył ani nie zmienił zapisanych wyników M5.",
        )
        return result

    @tool(context=True)
    def request_owner_decision(
        self,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Interrupt for the Coordinator UI owner choice; never choose a plan."""

        snapshot = self.workflow_store.get()
        if snapshot.owner_decision is not None:
            decision = snapshot.owner_decision
            return {
                "status": "ALREADY_DECIDED",
                "workflow_state": snapshot.workflow_state.value,
                "decision_id": decision.decision_id,
                "action": decision.action.value,
                "selected_plan_id": decision.selected_plan_id,
            }

        session_id = tool_context.invocation_state.get("session_id")
        agent_id = tool_context.invocation_state.get("agent_id")
        if not isinstance(session_id, str) or not isinstance(agent_id, str):
            raise RuntimeError("Brak zaufanej tożsamości sesji M6 w invocation_state.")
        if tool_context.agent.agent_id != agent_id:
            raise RuntimeError("Agent ID nie odpowiada invocation_state.")

        gate = self.workflow_store.prepare_owner_gate(
            session_id=session_id,
            agent_id=agent_id,
        )
        self.activity_store.record(
            action="Requested owner decision",
            tool="request_owner_decision",
            public_result=f"Owner gate {gate.gate_id}; human input required.",
            workflow_state=snapshot.workflow_state,
            rationale=(
                "ToolContext.interrupt zatrzymuje agent loop; agent nie wybiera planu."
            ),
        )
        response = tool_context.interrupt(
            name=f"owner-decision-{gate.gate_id}",
            reason={
                "gate_id": gate.gate_id,
                "pricing_gate_fingerprint": gate.pricing_gate_fingerprint,
                "eligible_plan_ids": list(gate.eligible_plan_ids),
            },
        )
        decision = self.workflow_store.commit_owner_decision(response)
        final_snapshot = self.workflow_store.get()
        self.activity_store.record(
            action="Committed owner decision",
            tool="request_owner_decision",
            public_result=(
                f"{decision.action.value}; decision_id={decision.decision_id}; "
                "source=COORDINATOR_UI."
            ),
            workflow_state=final_snapshot.workflow_state,
            rationale=(
                "Wznowiony tool zapisał jedną immutable OwnerDecision po walidacji."
            ),
        )
        return {
            "status": "DECISION_RECORDED",
            "workflow_state": final_snapshot.workflow_state.value,
            "decision_id": decision.decision_id,
            "action": decision.action.value,
            "selected_plan_id": decision.selected_plan_id,
            "actor_role": decision.actor_role,
            "source": decision.source,
        }

    def registered(self) -> list[Any]:
        return [
            self.get_job_state,
            self.assess_job,
            self.prepare_site_visit,
            self.get_site_visit_status,
            self.validate_site_visit_report,
            self.generate_crew_plans,
            self.calculate_plan_quotes,
            self.get_pricing_results,
            self.request_owner_decision,
        ]
