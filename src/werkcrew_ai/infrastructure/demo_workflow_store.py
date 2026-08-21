"""Process-local state adapter for the interactive M2/M3 DEMO only."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from threading import Lock

from werkcrew_ai.domain import (
    DecisionTraceEntry,
    Employee,
    JobRequest,
    PlanningResult,
    PlanningWorkItem,
    PostVisitValidation,
    SiteVisit,
    SiteVisitReport,
    Vehicle,
    WorkflowState,
)
from werkcrew_ai.field import complete_site_visit, create_site_visit
from werkcrew_ai.infrastructure.demo_repository import (
    load_demo_job_request,
    load_demo_planning_data,
    load_demo_workforce,
)
from werkcrew_ai.planning import (
    JobAssessment,
    assess_job_request,
    generate_plan_variants,
)


@dataclass(frozen=True, slots=True)
class DemoWorkflowSnapshot:
    original_job_request: JobRequest
    current_job_request: JobRequest
    initial_assessment: JobAssessment
    employees: tuple[Employee, ...]
    vehicles: tuple[Vehicle, ...]
    planning_work_items: tuple[PlanningWorkItem, ...]
    planning_window_end: date
    workflow_state: WorkflowState
    site_visit: SiteVisit | None = None
    assignment_message: str = "Oględziny nie zostały jeszcze utworzone."
    post_visit_validation: PostVisitValidation | None = None
    planning_result: PlanningResult | None = None

    @property
    def assigned_employee(self) -> Employee | None:
        if self.site_visit is None or self.site_visit.assigned_employee_id is None:
            return None
        return next(
            (
                employee
                for employee in self.employees
                if employee.id == self.site_visit.assigned_employee_id
            ),
            None,
        )

    def employee_name(self, employee_id: str) -> str:
        employee = next(
            (item for item in self.employees if item.id == employee_id), None
        )
        return employee.name if employee is not None else employee_id

    def vehicle_name(self, vehicle_id: str) -> str:
        vehicle = next((item for item in self.vehicles if item.id == vehicle_id), None)
        return vehicle.name if vehicle is not None else vehicle_id

    @property
    def planning_rejections(self) -> tuple[DecisionTraceEntry, ...]:
        if self.planning_result is None:
            return ()
        return tuple(
            entry
            for entry in self.planning_result.decision_trace
            if entry.outcome == "REJECTED"
        )


class DemoWorkflowStore:
    """Tiny in-memory store; restarting the process resets the scenario."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._snapshot = self._new_snapshot()

    @staticmethod
    def _new_snapshot() -> DemoWorkflowSnapshot:
        job_request = load_demo_job_request()
        _, employees, vehicles = load_demo_workforce()
        planning_work_items, planning_window_end = load_demo_planning_data()
        assessment = assess_job_request(job_request)
        return DemoWorkflowSnapshot(
            original_job_request=job_request,
            current_job_request=job_request,
            initial_assessment=assessment,
            employees=employees,
            vehicles=vehicles,
            planning_work_items=planning_work_items,
            planning_window_end=planning_window_end,
            workflow_state=WorkflowState.SITE_VISIT_REQUIRED,
        )

    def get(self) -> DemoWorkflowSnapshot:
        with self._lock:
            return self._snapshot

    def reset(self) -> DemoWorkflowSnapshot:
        with self._lock:
            self._snapshot = self._new_snapshot()
            return self._snapshot

    def create_site_visit(self) -> DemoWorkflowSnapshot:
        with self._lock:
            if self._snapshot.site_visit is not None:
                return self._snapshot
            site_visit, assignment = create_site_visit(
                self._snapshot.original_job_request,
                self._snapshot.initial_assessment,
                self._snapshot.employees,
            )
            self._snapshot = replace(
                self._snapshot,
                workflow_state=site_visit.status,
                site_visit=site_visit,
                assignment_message=assignment.message,
            )
            return self._snapshot

    def submit_report(self, report: SiteVisitReport) -> DemoWorkflowSnapshot:
        with self._lock:
            if self._snapshot.site_visit is None:
                raise ValueError("Najpierw utwórz i przydziel zadanie oględzin")
            completed_visit, validation = complete_site_visit(
                self._snapshot.site_visit,
                self._snapshot.original_job_request,
                report,
            )
            self._snapshot = replace(
                self._snapshot,
                current_job_request=validation.updated_job_request,
                workflow_state=validation.workflow_state,
                site_visit=completed_visit,
                post_visit_validation=validation,
                planning_result=None,
            )
            return self._snapshot

    def validate_stored_report(self) -> DemoWorkflowSnapshot:
        """Idempotently validate the human-submitted Field report with M2 rules."""

        with self._lock:
            site_visit = self._snapshot.site_visit
            if site_visit is None or site_visit.report is None:
                raise ValueError("Brak raportu człowieka do walidacji")
            completed_visit, validation = complete_site_visit(
                site_visit,
                self._snapshot.original_job_request,
                site_visit.report,
            )
            self._snapshot = replace(
                self._snapshot,
                current_job_request=validation.updated_job_request,
                workflow_state=validation.workflow_state,
                site_visit=completed_visit,
                post_visit_validation=validation,
                planning_result=None,
            )
            return self._snapshot

    def generate_plans(self) -> DemoWorkflowSnapshot:
        with self._lock:
            if self._snapshot.workflow_state is WorkflowState.PLANS_READY_FOR_REVIEW:
                return self._snapshot
            if self._snapshot.workflow_state is not WorkflowState.READY_FOR_PLANNING:
                raise ValueError(
                    "Plany można generować dopiero po stanie READY_FOR_PLANNING"
                )
            planning_result = generate_plan_variants(
                job_request=self._snapshot.current_job_request,
                work_items=self._snapshot.planning_work_items,
                employees=self._snapshot.employees,
                vehicles=self._snapshot.vehicles,
                planning_window_end=self._snapshot.planning_window_end,
            )
            workflow_state = (
                WorkflowState.PLANS_READY_FOR_REVIEW
                if planning_result.plans
                else WorkflowState.READY_FOR_PLANNING
            )
            self._snapshot = replace(
                self._snapshot,
                workflow_state=workflow_state,
                planning_result=planning_result,
            )
            return self._snapshot


demo_workflow_store = DemoWorkflowStore()
