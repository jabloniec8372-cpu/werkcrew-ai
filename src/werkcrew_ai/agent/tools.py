"""Thin Strands tool adapters around the deterministic M1-M3 capabilities."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from strands import tool

from werkcrew_ai.agent.state import AgentActivityStore
from werkcrew_ai.domain import WorkflowState
from werkcrew_ai.infrastructure.demo_workflow_store import DemoWorkflowStore
from werkcrew_ai.planning import assess_job_request


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
        """Read the current DEMO job, site-visit, validation and planning state."""

        snapshot = self.workflow_store.get()
        result = {
            "workflow_state": snapshot.workflow_state.value,
            "job_request": _public_value(snapshot.current_job_request),
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
        """Run the existing deterministic M1 assessment for the current job."""

        snapshot = self.workflow_store.get()
        assessment = assess_job_request(snapshot.current_job_request)
        result = {
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
        """Create the M2 brief and assign an eligible available field assessor."""

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

    def registered(self) -> list[Any]:
        return [
            self.get_job_state,
            self.assess_job,
            self.prepare_site_visit,
            self.get_site_visit_status,
            self.validate_site_visit_report,
            self.generate_crew_plans,
        ]
