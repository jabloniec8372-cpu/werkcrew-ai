"""Deterministic crew and vehicle planner for the bounded M3 DEMO scope."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from werkcrew_ai.domain import (
    DecisionTraceEntry,
    Employee,
    JobRequest,
    PlanVariant,
    PlanningResult,
    PlanningWorkItem,
    ScheduledTask,
    Vehicle,
)

OUTCOME_CONSIDERED = "CONSIDERED"
OUTCOME_REJECTED = "REJECTED"
OUTCOME_SELECTED = "SELECTED"

REASON_ELIGIBLE = "ELIGIBLE"
REASON_INACTIVE = "INACTIVE"
REASON_MISSING_SKILL = "MISSING_SKILL"
REASON_NO_AVAILABILITY = "NO_AVAILABILITY"
REASON_TIME_CONFLICT = "TIME_CONFLICT"
REASON_VEHICLE_INACTIVE = "VEHICLE_INACTIVE"
REASON_VEHICLE_TYPE_MISMATCH = "VEHICLE_TYPE_MISMATCH"
REASON_VEHICLE_UNAVAILABLE = "VEHICLE_UNAVAILABLE"
REASON_VEHICLE_CONFLICT = "VEHICLE_CONFLICT"
REASON_SELECTED = "SELECTED"


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    eligible: bool
    reason_code: str
    reason: str


class _TraceCollector:
    def __init__(self) -> None:
        self._entries: list[DecisionTraceEntry] = []
        self._keys: set[tuple[str, ...]] = set()

    def add(
        self,
        work_item: PlanningWorkItem,
        candidate_type: str,
        candidate_id: str,
        candidate_name: str,
        outcome: str,
        reason_code: str,
        reason: str,
    ) -> None:
        key = (
            work_item.id,
            candidate_type,
            candidate_id,
            outcome,
            reason_code,
            reason,
        )
        if key in self._keys:
            return
        self._keys.add(key)
        self._entries.append(
            DecisionTraceEntry(
                work_item_id=work_item.id,
                work_item_name=work_item.name,
                candidate_type=candidate_type,
                candidate_id=candidate_id,
                candidate_name=candidate_name,
                outcome=outcome,
                reason_code=reason_code,
                reason=reason,
            )
        )

    def entries(self) -> tuple[DecisionTraceEntry, ...]:
        return tuple(self._entries)


def intervals_overlap(
    first_start: datetime,
    first_end: datetime,
    second_start: datetime,
    second_end: datetime,
) -> bool:
    return first_start < second_end and second_start < first_end


def _duration(work_item: PlanningWorkItem) -> timedelta:
    seconds = int(work_item.estimated_hours * Decimal("3600"))
    if seconds <= 0:
        raise ValueError(f"Czas zadania {work_item.id} musi być dodatni")
    return timedelta(seconds=seconds)


def _availability_covers(
    availability: tuple,
    start_at: datetime,
    end_at: datetime,
) -> bool:
    return any(
        slot.is_available and slot.start_at <= start_at and slot.end_at >= end_at
        for slot in availability
    )


def evaluate_employee_candidate(
    work_item: PlanningWorkItem,
    employee: Employee,
    window_start: datetime,
    window_end: datetime,
) -> CandidateEvaluation:
    if not employee.is_active:
        return CandidateEvaluation(
            eligible=False,
            reason_code=REASON_INACTIVE,
            reason="Pracownik jest nieaktywny.",
        )
    missing_skills = tuple(
        skill_id
        for skill_id in work_item.required_skill_ids
        if skill_id not in employee.skill_ids
    )
    if missing_skills:
        return CandidateEvaluation(
            eligible=False,
            reason_code=REASON_MISSING_SKILL,
            reason=f"Brak wymaganych skillów: {', '.join(missing_skills)}.",
        )
    duration = _duration(work_item)
    has_window = any(
        slot.is_available
        and max(slot.start_at, window_start) + duration <= min(slot.end_at, window_end)
        for slot in employee.availability
    )
    if not has_window:
        return CandidateEvaluation(
            eligible=False,
            reason_code=REASON_NO_AVAILABILITY,
            reason="Brak wystarczającego przedziału dostępności w oknie planowania.",
        )
    return CandidateEvaluation(
        eligible=True,
        reason_code=REASON_ELIGIBLE,
        reason="Aktywny pracownik ma wymagane skille i dostępność.",
    )


def _topological_order(
    work_items: tuple[PlanningWorkItem, ...],
) -> tuple[PlanningWorkItem, ...]:
    by_id = {item.id: item for item in work_items}
    if len(by_id) != len(work_items):
        raise ValueError("Identyfikatory zadań planistycznych muszą być unikalne")
    for item in work_items:
        unknown = set(item.predecessor_ids) - set(by_id)
        if unknown:
            raise ValueError(
                f"Zadanie {item.id} wskazuje nieznanych poprzedników: {sorted(unknown)}"
            )

    ordered: list[PlanningWorkItem] = []
    remaining = list(work_items)
    resolved: set[str] = set()
    while remaining:
        ready = sorted(
            (
                item
                for item in remaining
                if set(item.predecessor_ids).issubset(resolved)
            ),
            key=lambda item: item.id,
        )
        if not ready:
            raise ValueError("Zależności zadań planistycznych zawierają cykl")
        for item in ready:
            ordered.append(item)
            resolved.add(item.id)
            remaining.remove(item)
    return tuple(ordered)


def _timezone_for_resources(
    employees: tuple[Employee, ...], vehicles: tuple[Vehicle, ...]
):
    for resource in (*employees, *vehicles):
        for slot in resource.availability:
            if slot.start_at.tzinfo is not None:
                return slot.start_at.tzinfo
    return timezone.utc


def _employee_options(
    work_item: PlanningWorkItem,
    employee: Employee,
    earliest_start: datetime,
    window_end: datetime,
    assignments: tuple[ScheduledTask, ...],
    trace: _TraceCollector,
) -> tuple[tuple[datetime, datetime, Employee], ...]:
    duration = _duration(work_item)
    occupied = tuple(
        assignment for assignment in assignments if assignment.employee_id == employee.id
    )
    options: list[tuple[datetime, datetime, Employee]] = []
    for slot in employee.availability:
        if not slot.is_available:
            continue
        first_start = max(slot.start_at, earliest_start)
        possible_starts = {first_start}
        possible_starts.update(
            assignment.end_at
            for assignment in occupied
            if slot.start_at <= assignment.end_at < slot.end_at
        )
        for start_at in sorted(possible_starts):
            end_at = start_at + duration
            if end_at > slot.end_at or end_at > window_end:
                continue
            if any(
                intervals_overlap(
                    start_at,
                    end_at,
                    assignment.start_at,
                    assignment.end_at,
                )
                for assignment in occupied
            ):
                trace.add(
                    work_item,
                    "EMPLOYEE",
                    employee.id,
                    employee.name,
                    OUTCOME_REJECTED,
                    REASON_TIME_CONFLICT,
                    "Pracownik ma w tym czasie inne zadanie w tym wariancie.",
                )
                continue
            options.append((start_at, end_at, employee))
    return tuple(sorted(set(options), key=lambda option: (option[0], option[2].id)))


def _vehicle_options(
    work_item: PlanningWorkItem,
    start_at: datetime,
    end_at: datetime,
    vehicles: tuple[Vehicle, ...],
    assignments: tuple[ScheduledTask, ...],
    trace: _TraceCollector,
) -> tuple[Vehicle | None, ...]:
    if work_item.required_vehicle_type is None:
        return (None,)

    options: list[Vehicle] = []
    for vehicle in vehicles:
        if not vehicle.is_active:
            trace.add(
                work_item,
                "VEHICLE",
                vehicle.id,
                vehicle.name,
                OUTCOME_REJECTED,
                REASON_VEHICLE_INACTIVE,
                "Pojazd jest nieaktywny.",
            )
            continue
        if vehicle.vehicle_type != work_item.required_vehicle_type:
            trace.add(
                work_item,
                "VEHICLE",
                vehicle.id,
                vehicle.name,
                OUTCOME_REJECTED,
                REASON_VEHICLE_TYPE_MISMATCH,
                f"Wymagany typ pojazdu: {work_item.required_vehicle_type}.",
            )
            continue
        if not _availability_covers(vehicle.availability, start_at, end_at):
            trace.add(
                work_item,
                "VEHICLE",
                vehicle.id,
                vehicle.name,
                OUTCOME_REJECTED,
                REASON_VEHICLE_UNAVAILABLE,
                "Pojazd jest niedostępny w terminie zadania.",
            )
            continue
        vehicle_assignments = tuple(
            assignment
            for assignment in assignments
            if assignment.vehicle_id == vehicle.id
        )
        if any(
            intervals_overlap(
                start_at,
                end_at,
                assignment.start_at,
                assignment.end_at,
            )
            for assignment in vehicle_assignments
        ):
            trace.add(
                work_item,
                "VEHICLE",
                vehicle.id,
                vehicle.name,
                OUTCOME_REJECTED,
                REASON_VEHICLE_CONFLICT,
                "Pojazd jest już używany przez inne zadanie w tym wariancie.",
            )
            continue
        options.append(vehicle)
    return tuple(sorted(options, key=lambda vehicle: vehicle.id))


def validate_plan(
    plan: PlanVariant,
    work_items: tuple[PlanningWorkItem, ...],
    employees: tuple[Employee, ...],
    vehicles: tuple[Vehicle, ...],
) -> tuple[str, ...]:
    errors: list[str] = []
    work_by_id = {item.id: item for item in work_items}
    employee_by_id = {employee.id: employee for employee in employees}
    vehicle_by_id = {vehicle.id: vehicle for vehicle in vehicles}
    assignment_by_item = {
        assignment.work_item_id: assignment for assignment in plan.assignments
    }

    if set(assignment_by_item) != set(work_by_id):
        errors.append("Plan nie zawiera dokładnie jednego przypisania dla każdego zadania.")

    for assignment in plan.assignments:
        work_item = work_by_id.get(assignment.work_item_id)
        employee = employee_by_id.get(assignment.employee_id)
        if work_item is None or employee is None:
            errors.append(f"Nieznane zadanie lub pracownik: {assignment.work_item_id}.")
            continue
        if not employee.is_active:
            errors.append(f"Nieaktywny pracownik: {employee.id}.")
        if not set(work_item.required_skill_ids).issubset(employee.skill_ids):
            errors.append(f"Brak wymaganych skillów: {employee.id}/{work_item.id}.")
        if not _availability_covers(
            employee.availability, assignment.start_at, assignment.end_at
        ):
            errors.append(f"Pracownik niedostępny: {employee.id}/{work_item.id}.")

        if work_item.required_vehicle_type is not None:
            vehicle = vehicle_by_id.get(assignment.vehicle_id or "")
            if vehicle is None:
                errors.append(f"Brak pojazdu dla zadania: {work_item.id}.")
            elif (
                not vehicle.is_active
                or vehicle.vehicle_type != work_item.required_vehicle_type
                or not _availability_covers(
                    vehicle.availability, assignment.start_at, assignment.end_at
                )
            ):
                errors.append(f"Niewykonalny przydział pojazdu: {vehicle.id}.")

        for predecessor_id in work_item.predecessor_ids:
            predecessor = assignment_by_item.get(predecessor_id)
            if predecessor is None or predecessor.end_at > assignment.start_at:
                errors.append(
                    f"Naruszona kolejność: {predecessor_id} -> {work_item.id}."
                )

    for index, first in enumerate(plan.assignments):
        for second in plan.assignments[index + 1 :]:
            if not intervals_overlap(
                first.start_at, first.end_at, second.start_at, second.end_at
            ):
                continue
            if first.employee_id == second.employee_id:
                errors.append(f"Konflikt pracownika: {first.employee_id}.")
            if first.vehicle_id is not None and first.vehicle_id == second.vehicle_id:
                errors.append(f"Konflikt pojazdu: {first.vehicle_id}.")

    return tuple(sorted(set(errors)))


def _plan_signature(assignments: tuple[ScheduledTask, ...]) -> tuple:
    return tuple(
        (
            assignment.work_item_id,
            assignment.employee_id,
            assignment.vehicle_id or "",
            assignment.start_at.isoformat(),
            assignment.end_at.isoformat(),
        )
        for assignment in assignments
    )


def generate_plan_variants(
    job_request: JobRequest,
    work_items: tuple[PlanningWorkItem, ...],
    employees: tuple[Employee, ...],
    vehicles: tuple[Vehicle, ...],
    planning_window_end: date,
) -> PlanningResult:
    trace = _TraceCollector()
    inability_reasons: list[str] = []
    if job_request.desired_start_date is None:
        return PlanningResult(
            plans=(),
            decision_trace=(),
            inability_reasons=("Brak planowanej daty rozpoczęcia zlecenia.",),
        )
    if job_request.missing_information or job_request.reported_risks:
        return PlanningResult(
            plans=(),
            decision_trace=(),
            inability_reasons=("Zlecenie nadal zawiera braki lub ryzyka.",),
        )
    if any(not requirement.is_confirmed for requirement in job_request.requirements):
        return PlanningResult(
            plans=(),
            decision_trace=(),
            inability_reasons=("Nie wszystkie wymagania zlecenia są potwierdzone.",),
        )

    requirement_ids = {requirement.id for requirement in job_request.requirements}
    referenced_requirements = {item.job_requirement_id for item in work_items}
    if referenced_requirements != requirement_ids:
        return PlanningResult(
            plans=(),
            decision_trace=(),
            inability_reasons=(
                "Jawne zadania planistyczne nie pokrywają dokładnie wymagań zlecenia.",
            ),
        )

    ordered_items = _topological_order(work_items)
    tzinfo = _timezone_for_resources(employees, vehicles)
    window_start = datetime.combine(
        job_request.desired_start_date, time.min, tzinfo=tzinfo
    )
    window_end = datetime.combine(planning_window_end, time.max, tzinfo=tzinfo)
    if window_end < window_start:
        raise ValueError("Koniec okna planowania nie może poprzedzać jego początku")

    employee_evaluations: dict[tuple[str, str], CandidateEvaluation] = {}
    for work_item in ordered_items:
        for employee in employees:
            evaluation = evaluate_employee_candidate(
                work_item, employee, window_start, window_end
            )
            employee_evaluations[(work_item.id, employee.id)] = evaluation
            trace.add(
                work_item,
                "EMPLOYEE",
                employee.id,
                employee.name,
                OUTCOME_CONSIDERED if evaluation.eligible else OUTCOME_REJECTED,
                evaluation.reason_code,
                evaluation.reason,
            )

    complete_schedules: list[tuple[ScheduledTask, ...]] = []

    def search(index: int, assignments: tuple[ScheduledTask, ...]) -> None:
        if len(complete_schedules) >= 1000:
            return
        if index == len(ordered_items):
            complete_schedules.append(assignments)
            return
        work_item = ordered_items[index]
        assignment_by_item = {
            assignment.work_item_id: assignment for assignment in assignments
        }
        predecessor_ends = tuple(
            assignment_by_item[predecessor_id].end_at
            for predecessor_id in work_item.predecessor_ids
        )
        earliest_start = max((window_start, *predecessor_ends))

        for employee in sorted(employees, key=lambda candidate: candidate.id):
            evaluation = employee_evaluations[(work_item.id, employee.id)]
            if not evaluation.eligible:
                continue
            employee_options = _employee_options(
                work_item,
                employee,
                earliest_start,
                window_end,
                assignments,
                trace,
            )
            for start_at, end_at, selected_employee in employee_options:
                vehicle_options = _vehicle_options(
                    work_item,
                    start_at,
                    end_at,
                    vehicles,
                    assignments,
                    trace,
                )
                for vehicle in vehicle_options:
                    assignment = ScheduledTask(
                        work_item_id=work_item.id,
                        work_item_name=work_item.name,
                        employee_id=selected_employee.id,
                        start_at=start_at,
                        end_at=end_at,
                        vehicle_id=vehicle.id if vehicle is not None else None,
                    )
                    search(index + 1, (*assignments, assignment))

    search(0, ())

    unique_schedules = {
        _plan_signature(schedule): schedule for schedule in complete_schedules
    }
    ranked_schedules = sorted(
        unique_schedules.values(),
        key=lambda schedule: (
            min(assignment.start_at for assignment in schedule),
            max(assignment.end_at for assignment in schedule),
            len({assignment.employee_id for assignment in schedule}),
            _plan_signature(schedule),
        ),
    )

    plans: list[PlanVariant] = []
    for position, schedule in enumerate(ranked_schedules[:2]):
        label = "PLAN A" if position == 0 else "PLAN B"
        plan = PlanVariant(
            id=f"plan-{job_request.id}-{'a' if position == 0 else 'b'}",
            job_request_id=job_request.id,
            label=label,
            start_at=min(assignment.start_at for assignment in schedule),
            end_at=max(assignment.end_at for assignment in schedule),
            assignments=schedule,
            employee_ids=tuple(
                sorted({assignment.employee_id for assignment in schedule})
            ),
            vehicle_ids=tuple(
                sorted(
                    {
                        assignment.vehicle_id
                        for assignment in schedule
                        if assignment.vehicle_id is not None
                    }
                )
            ),
            rationale=(
                "Najwcześniejszy wykonalny wariant z najmniejszą liczbą osób "
                "wśród harmonogramów o tym samym terminie."
                if position == 0
                else "Drugi najwyżej sklasyfikowany, rzeczywiście odmienny wariant wykonalny."
            ),
            limitations=(
                "Plan opiera się wyłącznie na syntetycznych przedziałach dostępności DEMO.",
                "Nie uwzględnia nadgodzin, weekendów ani optymalizacji tras.",
                "Wariant wymaga późniejszego przeglądu przez właściciela.",
            ),
        )
        validation_errors = validate_plan(plan, work_items, employees, vehicles)
        if validation_errors:
            inability_reasons.extend(validation_errors)
            continue
        plans.append(plan)
        work_by_id = {item.id: item for item in work_items}
        employee_by_id = {employee.id: employee for employee in employees}
        vehicle_by_id = {vehicle.id: vehicle for vehicle in vehicles}
        for assignment in schedule:
            work_item = work_by_id[assignment.work_item_id]
            employee = employee_by_id[assignment.employee_id]
            trace.add(
                work_item,
                "EMPLOYEE",
                employee.id,
                employee.name,
                OUTCOME_SELECTED,
                REASON_SELECTED,
                f"Wybrany do {label} w dostępnym terminie.",
            )
            if assignment.vehicle_id is not None:
                vehicle = vehicle_by_id[assignment.vehicle_id]
                trace.add(
                    work_item,
                    "VEHICLE",
                    vehicle.id,
                    vehicle.name,
                    OUTCOME_SELECTED,
                    REASON_SELECTED,
                    f"Wybrany do {label}; typ i dostępność są zgodne.",
                )

    if not plans:
        inability_reasons.append(
            "Nie znaleziono wykonalnego wariantu w podanym oknie planowania."
        )
    elif len(plans) == 1:
        inability_reasons.append(
            "Znaleziono tylko jeden unikalny wykonalny wariant; Plan B nie powstał."
        )

    return PlanningResult(
        plans=tuple(plans),
        decision_trace=trace.entries(),
        inability_reasons=tuple(sorted(set(inability_reasons))),
    )
