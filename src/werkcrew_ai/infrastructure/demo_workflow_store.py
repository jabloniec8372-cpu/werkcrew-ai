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
    load_demo_pricing_data,
    load_demo_workforce,
)
from werkcrew_ai.planning import (
    JobAssessment,
    assess_job_request,
    generate_plan_variants,
)
from werkcrew_ai.pricing import (
    DEMO_DATA_CLASSIFICATION,
    EmployeeRateSnapshot,
    JobPricingContext,
    PlanPricingComparison,
    PlanPricingInput,
    PlanPricingResult,
    PricingStatus,
    VehicleRateSnapshot,
    VehicleUsageInput,
    calculate_plan_quotes as calculate_quotes,
    compare_plan_pricing,
)


@dataclass(frozen=True, slots=True)
class DemoWorkflowSnapshot:
    original_job_request: JobRequest
    current_job_request: JobRequest
    initial_assessment: JobAssessment | None
    employees: tuple[Employee, ...]
    vehicles: tuple[Vehicle, ...]
    planning_work_items: tuple[PlanningWorkItem, ...]
    planning_window_end: date
    pricing_context: JobPricingContext
    vehicle_usages: tuple[VehicleUsageInput, ...]
    workflow_state: WorkflowState
    site_visit: SiteVisit | None = None
    assignment_message: str = "Oględziny nie zostały jeszcze utworzone."
    post_visit_validation: PostVisitValidation | None = None
    planning_result: PlanningResult | None = None
    pricing_results: tuple[PlanPricingResult, ...] = ()
    pricing_comparison: PlanPricingComparison | None = None

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

    def pricing_result_for(self, plan_id: str) -> PlanPricingResult | None:
        return next(
            (item for item in self.pricing_results if item.plan_id == plan_id),
            None,
        )

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

    def __init__(
        self,
        *,
        pricing_context: JobPricingContext | None = None,
        vehicle_usages: tuple[VehicleUsageInput, ...] | None = None,
    ) -> None:
        self._lock = Lock()
        self._pricing_context_override = pricing_context
        self._vehicle_usages_override = vehicle_usages
        self._snapshot = self._new_snapshot()

    def _new_snapshot(self) -> DemoWorkflowSnapshot:
        job_request = load_demo_job_request()
        _, employees, vehicles = load_demo_workforce()
        planning_work_items, planning_window_end = load_demo_planning_data()
        demo_pricing_context, demo_vehicle_usages = load_demo_pricing_data()
        return DemoWorkflowSnapshot(
            original_job_request=job_request,
            current_job_request=job_request,
            initial_assessment=None,
            employees=employees,
            vehicles=vehicles,
            planning_work_items=planning_work_items,
            planning_window_end=planning_window_end,
            pricing_context=(
                self._pricing_context_override or demo_pricing_context
            ),
            vehicle_usages=(
                self._vehicle_usages_override
                if self._vehicle_usages_override is not None
                else demo_vehicle_usages
            ),
            workflow_state=WorkflowState.RECEIVED,
        )

    def get(self) -> DemoWorkflowSnapshot:
        with self._lock:
            return self._snapshot

    def reset(self) -> DemoWorkflowSnapshot:
        with self._lock:
            self._snapshot = self._new_snapshot()
            return self._snapshot

    def assess_job(self) -> DemoWorkflowSnapshot:
        """Persist the deterministic M1 result and advance a fresh request once."""

        with self._lock:
            if self._snapshot.initial_assessment is not None:
                return self._snapshot
            assessment = assess_job_request(self._snapshot.current_job_request)
            workflow_state = (
                WorkflowState.SITE_VISIT_REQUIRED
                if assessment.site_visit_required
                else WorkflowState.READY_FOR_PLANNING
            )
            self._snapshot = replace(
                self._snapshot,
                initial_assessment=assessment,
                workflow_state=workflow_state,
            )
            return self._snapshot

    def create_site_visit(self) -> DemoWorkflowSnapshot:
        with self._lock:
            if self._snapshot.site_visit is not None:
                return self._snapshot
            assessment = self._snapshot.initial_assessment
            if assessment is None or self._snapshot.workflow_state is WorkflowState.RECEIVED:
                raise ValueError("Najpierw wykonaj ocenę M1 dla świeżego zlecenia")
            if self._snapshot.workflow_state is not WorkflowState.SITE_VISIT_REQUIRED:
                raise ValueError(
                    "Oględziny można przygotować tylko w stanie SITE_VISIT_REQUIRED"
                )
            site_visit, assignment = create_site_visit(
                self._snapshot.original_job_request,
                assessment,
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
                pricing_results=(),
                pricing_comparison=None,
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
                pricing_results=(),
                pricing_comparison=None,
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
                pricing_results=(),
                pricing_comparison=None,
            )
            return self._snapshot

    def calculate_plan_quotes(self) -> DemoWorkflowSnapshot:
        """Price current M3 variants independently without changing their plans."""

        with self._lock:
            planning_result = self._snapshot.planning_result
            if planning_result is None or not planning_result.plans:
                raise ValueError("NO_PLANS: brak wariantów M3 do wyceny")
            if self._snapshot.workflow_state not in {
                WorkflowState.PLANS_READY_FOR_REVIEW,
                WorkflowState.PRICING_READY_FOR_REVIEW,
            }:
                raise ValueError(
                    "Pricing można uruchomić dopiero po PLANS_READY_FOR_REVIEW"
                )
            employee_by_id = {item.id: item for item in self._snapshot.employees}
            vehicle_by_id = {item.id: item for item in self._snapshot.vehicles}
            plan_inputs = []
            for plan in planning_result.plans:
                employee_rates = tuple(
                    EmployeeRateSnapshot(
                        employee_id=employee.id,
                        employee_name=employee.name,
                        hourly_cost_amount=employee.hourly_cost_amount,
                        currency=employee.cost_currency,
                        data_classification=DEMO_DATA_CLASSIFICATION,
                    )
                    for employee_id in plan.employee_ids
                    if (employee := employee_by_id.get(employee_id)) is not None
                )
                vehicle_rates = tuple(
                    VehicleRateSnapshot(
                        vehicle_id=vehicle.id,
                        vehicle_name=vehicle.name,
                        cost_per_km_amount=vehicle.cost_per_km_amount,
                        currency=vehicle.cost_currency,
                        data_classification=DEMO_DATA_CLASSIFICATION,
                    )
                    for vehicle_id in plan.vehicle_ids
                    if (vehicle := vehicle_by_id.get(vehicle_id)) is not None
                )
                plan_inputs.append(
                    PlanPricingInput(
                        job_request_id=self._snapshot.current_job_request.id,
                        plan=plan,
                        planning_rule_version=planning_result.rule_version,
                        employee_rates=employee_rates,
                        vehicle_rates=vehicle_rates,
                        vehicle_usages=tuple(
                            item
                            for item in self._snapshot.vehicle_usages
                            if item.plan_id == plan.id
                        ),
                        plan_specific_direct_costs=(),
                        data_classification=DEMO_DATA_CLASSIFICATION,
                    )
                )
            results = calculate_quotes(
                self._snapshot.pricing_context,
                tuple(plan_inputs),
                self._snapshot.pricing_results,
            )
            all_complete = all(
                item.status is PricingStatus.COMPLETE for item in results
            )
            self._snapshot = replace(
                self._snapshot,
                workflow_state=(
                    WorkflowState.PRICING_READY_FOR_REVIEW
                    if all_complete
                    else WorkflowState.PLANS_READY_FOR_REVIEW
                ),
                pricing_results=results,
                pricing_comparison=(
                    compare_plan_pricing(results) if all_complete else None
                ),
            )
            return self._snapshot


demo_workflow_store = DemoWorkflowStore()
