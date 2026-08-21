"""Process-local state adapter for the interactive M2 DEMO only."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from werkcrew_ai.domain import (
    Employee,
    JobRequest,
    PostVisitValidation,
    SiteVisit,
    SiteVisitReport,
    WorkflowState,
)
from werkcrew_ai.field import complete_site_visit, create_site_visit
from werkcrew_ai.infrastructure.demo_repository import (
    load_demo_job_request,
    load_demo_workforce,
)
from werkcrew_ai.planning import JobAssessment, assess_job_request


@dataclass(frozen=True, slots=True)
class DemoWorkflowSnapshot:
    original_job_request: JobRequest
    current_job_request: JobRequest
    initial_assessment: JobAssessment
    employees: tuple[Employee, ...]
    workflow_state: WorkflowState
    site_visit: SiteVisit | None = None
    assignment_message: str = "Oględziny nie zostały jeszcze utworzone."
    post_visit_validation: PostVisitValidation | None = None

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


class DemoWorkflowStore:
    """Tiny in-memory store; restarting the process resets the scenario."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._snapshot = self._new_snapshot()

    @staticmethod
    def _new_snapshot() -> DemoWorkflowSnapshot:
        job_request = load_demo_job_request()
        _, employees, _ = load_demo_workforce()
        assessment = assess_job_request(job_request)
        return DemoWorkflowSnapshot(
            original_job_request=job_request,
            current_job_request=job_request,
            initial_assessment=assessment,
            employees=employees,
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
            self._snapshot = DemoWorkflowSnapshot(
                original_job_request=self._snapshot.original_job_request,
                current_job_request=self._snapshot.current_job_request,
                initial_assessment=self._snapshot.initial_assessment,
                employees=self._snapshot.employees,
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
            self._snapshot = DemoWorkflowSnapshot(
                original_job_request=self._snapshot.original_job_request,
                current_job_request=validation.updated_job_request,
                initial_assessment=self._snapshot.initial_assessment,
                employees=self._snapshot.employees,
                workflow_state=validation.workflow_state,
                site_visit=completed_visit,
                assignment_message=self._snapshot.assignment_message,
                post_visit_validation=validation,
            )
            return self._snapshot


demo_workflow_store = DemoWorkflowStore()
