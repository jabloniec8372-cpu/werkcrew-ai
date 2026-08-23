"""Process-local state adapter for the interactive M2/M3 DEMO only."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from werkcrew_ai.domain import (
    DecisionTraceEntry,
    Employee,
    JobRequest,
    OwnerDecision,
    OwnerDecisionAction,
    OwnerDecisionGateStatus,
    PendingOwnerDecisionGate,
    PlanningResult,
    PlanningWorkItem,
    PostVisitValidation,
    SiteVisit,
    SiteVisitReport,
    Vehicle,
    WorkflowState,
    owner_response_fingerprint,
    pricing_gate_fingerprint,
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


class OwnerDecisionInputError(ValueError):
    """Malformed owner response that must be rejected before Strands resume."""


class OwnerDecisionConflictError(RuntimeError):
    """Stale or conflicting owner response that must not reach Strands."""


@dataclass(frozen=True, slots=True)
class OwnerDecisionResumeClaim:
    canonical_response: dict[str, Any] | None
    interrupt_id: str | None
    existing_decision: OwnerDecision | None

    @property
    def requires_resume(self) -> bool:
        return self.canonical_response is not None


@dataclass(frozen=True, slots=True)
class DemoWorkflowSnapshot:
    original_job_request: JobRequest
    current_job_request: JobRequest
    workflow_instance_id: str
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
    owner_decision: OwnerDecision | None = None
    pending_owner_gate: PendingOwnerDecisionGate | None = None

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
            workflow_instance_id=uuid4().hex,
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
            gate = self._snapshot.pending_owner_gate
            if gate is not None and gate.status in {
                OwnerDecisionGateStatus.PENDING,
                OwnerDecisionGateStatus.RESUMING,
            }:
                raise OwnerDecisionConflictError(
                    "Pricing jest zamrożony podczas aktywnego owner decision gate."
                )
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

    def _pricing_gate_data_locked(
        self,
    ) -> tuple[str, tuple[str, ...]]:
        snapshot = self._snapshot
        if snapshot.workflow_state is not WorkflowState.PRICING_READY_FOR_REVIEW:
            raise OwnerDecisionConflictError(
                "Owner gate wymaga stanu PRICING_READY_FOR_REVIEW."
            )
        planning_result = snapshot.planning_result
        if planning_result is None or not planning_result.plans:
            raise OwnerDecisionConflictError(
                "Owner gate wymaga co najmniej jednego istniejącego wariantu."
            )
        plan_ids = tuple(sorted(item.id for item in planning_result.plans))
        result_by_plan = {item.plan_id: item for item in snapshot.pricing_results}
        if len(result_by_plan) != len(snapshot.pricing_results):
            raise OwnerDecisionConflictError(
                "Wyniki pricingu zawierają zduplikowany plan_id."
            )
        if set(result_by_plan) != set(plan_ids):
            raise OwnerDecisionConflictError(
                "Każdy istniejący wariant musi mieć dokładnie jeden wynik pricingu."
            )
        if not all(
            result_by_plan[plan_id].status is PricingStatus.COMPLETE
            for plan_id in plan_ids
        ):
            raise OwnerDecisionConflictError(
                "Owner gate nie powstaje dla INCOMPLETE lub REVIEW_REQUIRED."
            )
        fingerprint = pricing_gate_fingerprint(
            snapshot.current_job_request.id,
            snapshot.pricing_results,
        )
        return fingerprint, plan_ids

    def _invalidate_gate_locked(
        self,
        gate: PendingOwnerDecisionGate,
    ) -> None:
        self._snapshot = replace(
            self._snapshot,
            pending_owner_gate=replace(
                gate,
                status=OwnerDecisionGateStatus.INVALIDATED,
            ),
        )

    def _validate_gate_is_current_locked(
        self,
        gate: PendingOwnerDecisionGate,
    ) -> None:
        snapshot = self._snapshot
        if gate.workflow_instance_id != snapshot.workflow_instance_id:
            self._invalidate_gate_locked(gate)
            raise OwnerDecisionConflictError("Nieaktualny workflow_instance_id gate.")
        if gate.job_request_id != snapshot.current_job_request.id:
            self._invalidate_gate_locked(gate)
            raise OwnerDecisionConflictError("Owner gate dotyczy innego zlecenia.")
        try:
            fingerprint, eligible_plan_ids = self._pricing_gate_data_locked()
        except OwnerDecisionConflictError:
            self._invalidate_gate_locked(gate)
            raise
        if (
            gate.pricing_gate_fingerprint != fingerprint
            or gate.eligible_plan_ids != eligible_plan_ids
        ):
            self._invalidate_gate_locked(gate)
            raise OwnerDecisionConflictError(
                "Pricing zmienił się po utworzeniu owner gate."
            )

    def prepare_owner_gate(
        self,
        *,
        session_id: str,
        agent_id: str,
    ) -> PendingOwnerDecisionGate:
        """Create one gate for the current COMPLETE pricing view."""

        with self._lock:
            if self._snapshot.owner_decision is not None:
                raise OwnerDecisionConflictError("Decyzja właściciela już istnieje.")
            fingerprint, eligible_plan_ids = self._pricing_gate_data_locked()
            existing = self._snapshot.pending_owner_gate
            if existing is not None:
                if existing.status is OwnerDecisionGateStatus.INVALIDATED:
                    raise OwnerDecisionConflictError("Owner gate jest INVALIDATED.")
                if existing.status is OwnerDecisionGateStatus.RESOLVED:
                    raise OwnerDecisionConflictError("Owner gate jest już RESOLVED.")
                self._validate_gate_is_current_locked(existing)
                if existing.session_id != session_id or existing.agent_id != agent_id:
                    self._invalidate_gate_locked(existing)
                    raise OwnerDecisionConflictError(
                        "Session ID lub agent ID nie odpowiada aktywnemu gate."
                    )
                return existing

            gate = PendingOwnerDecisionGate(
                gate_id=f"owner-gate-{uuid4().hex}",
                workflow_instance_id=self._snapshot.workflow_instance_id,
                job_request_id=self._snapshot.current_job_request.id,
                pricing_gate_fingerprint=fingerprint,
                eligible_plan_ids=eligible_plan_ids,
                session_id=session_id,
                agent_id=agent_id,
                interrupt_id=None,
                status=OwnerDecisionGateStatus.PENDING,
                response_fingerprint=None,
                decision_id=None,
                created_at=datetime.now(timezone.utc),
                resolved_at=None,
            )
            self._snapshot = replace(self._snapshot, pending_owner_gate=gate)
            return gate

    def bind_owner_interrupt(
        self,
        *,
        gate_id: str,
        interrupt_id: str,
    ) -> PendingOwnerDecisionGate:
        """Bind the real interrupt identifier returned by Strands to the gate."""

        with self._lock:
            gate = self._snapshot.pending_owner_gate
            if gate is None or gate.gate_id != gate_id:
                raise OwnerDecisionConflictError("Brak owner gate dla interruptu.")
            if gate.status is not OwnerDecisionGateStatus.PENDING:
                raise OwnerDecisionConflictError(
                    "Interrupt można przypisać wyłącznie do PENDING gate."
                )
            self._validate_gate_is_current_locked(gate)
            if not interrupt_id:
                raise OwnerDecisionConflictError("Strands nie zwrócił interrupt_id.")
            if gate.interrupt_id is not None and gate.interrupt_id != interrupt_id:
                self._invalidate_gate_locked(gate)
                raise OwnerDecisionConflictError(
                    "Gate jest już związany z innym interrupt_id."
                )
            bound = replace(gate, interrupt_id=interrupt_id)
            self._snapshot = replace(self._snapshot, pending_owner_gate=bound)
            return bound

    def invalidate_unbound_owner_gate(self) -> None:
        """Prevent UI resume when an invocation failed before exposing interrupt_id."""

        with self._lock:
            gate = self._snapshot.pending_owner_gate
            if (
                gate is not None
                and gate.status is OwnerDecisionGateStatus.PENDING
                and gate.interrupt_id is None
            ):
                self._invalidate_gate_locked(gate)

    @staticmethod
    def _parse_owner_action(
        action: str,
        selected_plan_id: str | None,
    ) -> OwnerDecisionAction:
        try:
            parsed = OwnerDecisionAction(action)
        except (TypeError, ValueError) as exc:
            raise OwnerDecisionInputError("Nieznana owner decision action.") from exc
        if parsed is OwnerDecisionAction.APPROVE_PLAN and not selected_plan_id:
            raise OwnerDecisionInputError(
                "APPROVE_PLAN wymaga selected_plan_id."
            )
        if parsed is OwnerDecisionAction.REJECT_ALL and selected_plan_id is not None:
            raise OwnerDecisionInputError(
                "REJECT_ALL nie może zawierać selected_plan_id."
            )
        return parsed

    def claim_owner_response(
        self,
        *,
        gate_id: str,
        action: str,
        selected_plan_id: str | None,
        expected_session_id: str,
        expected_agent_id: str,
    ) -> OwnerDecisionResumeClaim:
        """Atomically validate and claim one Coordinator UI response before resume."""

        parsed_action = self._parse_owner_action(action, selected_plan_id)
        with self._lock:
            gate = self._snapshot.pending_owner_gate
            if gate is None or gate.gate_id != gate_id:
                raise OwnerDecisionConflictError("Nieaktualny lub nieznany gate_id.")
            canonical_response = {
                "gate_id": gate.gate_id,
                "action": parsed_action.value,
                "selected_plan_id": selected_plan_id,
                "pricing_gate_fingerprint": gate.pricing_gate_fingerprint,
            }
            response_fingerprint = owner_response_fingerprint(canonical_response)

            if gate.status is OwnerDecisionGateStatus.RESOLVED:
                decision = self._snapshot.owner_decision
                if (
                    decision is not None
                    and gate.response_fingerprint == response_fingerprint
                    and gate.decision_id == decision.decision_id
                ):
                    return OwnerDecisionResumeClaim(None, None, decision)
                raise OwnerDecisionConflictError(
                    "Owner gate został rozwiązany inną odpowiedzią."
                )
            if gate.status is OwnerDecisionGateStatus.RESUMING:
                raise OwnerDecisionConflictError(
                    "Owner response jest już przetwarzana."
                )
            if gate.status is OwnerDecisionGateStatus.INVALIDATED:
                raise OwnerDecisionConflictError("Owner gate jest INVALIDATED.")

            self._validate_gate_is_current_locked(gate)
            if (
                gate.session_id != expected_session_id
                or gate.agent_id != expected_agent_id
            ):
                self._invalidate_gate_locked(gate)
                raise OwnerDecisionConflictError(
                    "Owner gate nie odpowiada aktualnej session_id/agent_id."
                )
            if not gate.session_id or not gate.agent_id or not gate.interrupt_id:
                raise OwnerDecisionConflictError(
                    "Owner gate nie zawiera kompletnej tożsamości sesji/interruptu."
                )
            if (
                parsed_action is OwnerDecisionAction.APPROVE_PLAN
                and selected_plan_id not in gate.eligible_plan_ids
            ):
                raise OwnerDecisionInputError(
                    "Wybrany plan nie należy do eligible_plan_ids."
                )
            if parsed_action is OwnerDecisionAction.APPROVE_PLAN:
                pricing = next(
                    (
                        item
                        for item in self._snapshot.pricing_results
                        if item.plan_id == selected_plan_id
                    ),
                    None,
                )
                if pricing is None or pricing.status is not PricingStatus.COMPLETE:
                    raise OwnerDecisionInputError(
                        "Wybrany plan nie ma COMPLETE pricing result."
                    )

            claimed = replace(
                gate,
                status=OwnerDecisionGateStatus.RESUMING,
                response_fingerprint=response_fingerprint,
            )
            self._snapshot = replace(self._snapshot, pending_owner_gate=claimed)
            return OwnerDecisionResumeClaim(
                canonical_response=canonical_response,
                interrupt_id=claimed.interrupt_id,
                existing_decision=None,
            )

    def commit_owner_decision(
        self,
        response: Mapping[str, Any],
    ) -> OwnerDecision:
        """Atomically commit one validated human response after interrupt resume."""

        if not isinstance(response, Mapping):
            raise OwnerDecisionInputError("Human response musi być obiektem.")
        gate_id = response.get("gate_id")
        action = response.get("action")
        selected_plan_id = response.get("selected_plan_id")
        supplied_fingerprint = response.get("pricing_gate_fingerprint")
        if not isinstance(gate_id, str) or not isinstance(action, str):
            raise OwnerDecisionInputError("Human response nie zawiera gate_id/action.")
        if selected_plan_id is not None and not isinstance(selected_plan_id, str):
            raise OwnerDecisionInputError("selected_plan_id musi być tekstem lub null.")
        parsed_action = self._parse_owner_action(action, selected_plan_id)

        with self._lock:
            gate = self._snapshot.pending_owner_gate
            if gate is None or gate.gate_id != gate_id:
                raise OwnerDecisionConflictError("Human response dotyczy innego gate.")
            if gate.status is not OwnerDecisionGateStatus.RESUMING:
                raise OwnerDecisionConflictError(
                    "Owner decision commit wymaga gate RESUMING."
                )
            self._validate_gate_is_current_locked(gate)
            if supplied_fingerprint != gate.pricing_gate_fingerprint:
                self._invalidate_gate_locked(gate)
                raise OwnerDecisionConflictError(
                    "Human response ma nieaktualny pricing fingerprint."
                )
            canonical_response = {
                "gate_id": gate.gate_id,
                "action": parsed_action.value,
                "selected_plan_id": selected_plan_id,
                "pricing_gate_fingerprint": gate.pricing_gate_fingerprint,
            }
            if owner_response_fingerprint(canonical_response) != gate.response_fingerprint:
                raise OwnerDecisionConflictError(
                    "Human response nie odpowiada atomowo claimowanej odpowiedzi."
                )
            if (
                parsed_action is OwnerDecisionAction.APPROVE_PLAN
                and selected_plan_id not in gate.eligible_plan_ids
            ):
                raise OwnerDecisionInputError("Wybrany plan nie jest eligible.")

            decided_at = datetime.now(timezone.utc)
            decision = OwnerDecision(
                decision_id=f"owner-decision-{uuid4().hex}",
                gate_id=gate.gate_id,
                job_request_id=gate.job_request_id,
                action=parsed_action,
                selected_plan_id=selected_plan_id,
                pricing_gate_fingerprint=gate.pricing_gate_fingerprint,
                decided_at=decided_at,
            )
            resolved_gate = replace(
                gate,
                status=OwnerDecisionGateStatus.RESOLVED,
                decision_id=decision.decision_id,
                resolved_at=decided_at,
            )
            final_state = (
                WorkflowState.PLAN_APPROVED
                if parsed_action is OwnerDecisionAction.APPROVE_PLAN
                else WorkflowState.PLANS_REJECTED
            )
            self._snapshot = replace(
                self._snapshot,
                workflow_state=final_state,
                owner_decision=decision,
                pending_owner_gate=resolved_gate,
            )
            return decision


demo_workflow_store = DemoWorkflowStore()
