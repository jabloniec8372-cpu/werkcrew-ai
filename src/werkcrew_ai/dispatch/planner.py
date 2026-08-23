"""Bounded deterministic reordering of existing same-worker assignments."""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from werkcrew_ai.dispatch.models import (
    DISPATCH_RULE_VERSION,
    AddressStatus,
    CalendarAssignment,
    CalendarStatus,
    DispatchPlanResult,
    DispatchPlanStatus,
    DispatchVehicle,
    DispatchWorker,
    MaterialReadiness,
    MaterialReadinessStatus,
    PersistentJob,
    ReplanProposal,
    ReplanProposalStatus,
    RouteSnapshot,
    RouteSnapshotStatus,
    SchedulePlacement,
    require_aware,
)


@dataclass(frozen=True, slots=True)
class _Candidate:
    placements: tuple[SchedulePlacement, ...]
    changed_commitments: int
    idle_seconds: int
    travel_seconds: int
    route_ids: tuple[str, ...]
    conditional: bool


def _hash(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _covers(start: datetime, end: datetime, windows: tuple) -> bool:
    return any(window.start_at <= start and window.end_at >= end for window in windows)


class DailyDispatchPlanner:
    """Compare at most six explicit orderings; never reassign people or vehicles."""

    rule_version = DISPATCH_RULE_VERSION

    def plan(
        self,
        *,
        initiating_workflow_instance_id: str,
        planning_date: date,
        now: datetime,
        workday_start: datetime,
        workday_end: datetime,
        jobs: tuple[PersistentJob, ...],
        workflow_revisions: dict[str, int],
        assignments: tuple[CalendarAssignment, ...],
        materials: tuple[MaterialReadiness, ...],
        routes: tuple[RouteSnapshot, ...],
        worker: DispatchWorker,
        vehicles: tuple[DispatchVehicle, ...] = (),
    ) -> DispatchPlanResult:
        require_aware(now, "now")
        require_aware(workday_start, "workday_start")
        require_aware(workday_end, "workday_end")
        if workday_start.date() != planning_date or workday_end.date() != planning_date:
            raise ValueError("Workday bounds must match planning_date")
        if workday_end <= workday_start:
            raise ValueError("Working hours are invalid")

        candidates = tuple(
            item
            for item in assignments
            if item.status not in {CalendarStatus.COMPLETED, CalendarStatus.CANCELLED}
            and item.start_at.astimezone(workday_start.tzinfo).date() == planning_date
        )
        if not candidates:
            return DispatchPlanResult(
                DispatchPlanStatus.NO_FEASIBLE_REPLAN,
                None,
                ("NO_UNFINISHED_TASKS",),
                "No unfinished assignments exist for the planning day.",
            )
        if len(candidates) > 3:
            return DispatchPlanResult(
                DispatchPlanStatus.NO_FEASIBLE_REPLAN,
                None,
                ("BOUNDED_PLANNER_LIMIT",),
                "M7 compares at most three explicit same-worker assignments.",
            )
        if any(item.status is CalendarStatus.IN_PROGRESS for item in candidates):
            return DispatchPlanResult(
                DispatchPlanStatus.NO_FEASIBLE_REPLAN,
                None,
                ("IN_PROGRESS_IMMOVABLE",),
                "An IN_PROGRESS assignment cannot be moved.",
            )
        if {item.worker_id for item in candidates} != {worker.worker_id}:
            return DispatchPlanResult(
                DispatchPlanStatus.NO_FEASIBLE_REPLAN,
                None,
                ("WORKER_REASSIGNMENT_FORBIDDEN",),
                "All reordered assignments must retain the same worker.",
            )

        job_by_id = {job.job_id: job for job in jobs}
        if any(
            item.job_id not in job_by_id
            or job_by_id[item.job_id].address_status is not AddressStatus.CONFIRMED
            or job_by_id[item.job_id].coordinates is None
            for item in candidates
        ):
            return DispatchPlanResult(
                DispatchPlanStatus.NO_FEASIBLE_REPLAN,
                None,
                ("ADDRESS_NOT_CONFIRMED",),
                "Routing requires CONFIRMED addresses and fixture coordinates.",
            )
        if any(
            not set(item.required_skill_ids).issubset(worker.skill_ids)
            for item in candidates
        ):
            return DispatchPlanResult(
                DispatchPlanStatus.NO_FEASIBLE_REPLAN,
                None,
                ("ASSIGNED_WORKER_MISSING_SKILL",),
                "The existing assigned worker lacks a required skill.",
            )

        material_by_task = {
            (item.job_id, item.scheduled_task_id): item for item in materials
        }
        vehicle_by_id = {item.vehicle_id: item for item in vehicles}
        route_by_pair = {
            (item.origin_reference, item.destination_reference): item
            for item in routes
            if item.status is RouteSnapshotStatus.VALID
        }
        if len(route_by_pair) != len(
            [item for item in routes if item.status is RouteSnapshotStatus.VALID]
        ):
            return DispatchPlanResult(
                DispatchPlanStatus.NO_FEASIBLE_REPLAN,
                None,
                ("AMBIGUOUS_ROUTE_SNAPSHOT",),
                "More than one valid route exists for the same ordered pair.",
            )

        evaluated: list[_Candidate] = []
        failure_codes: set[str] = set()
        original_order = tuple(
            item.assignment_id
            for item in sorted(candidates, key=lambda item: (item.start_at, item.assignment_id))
        )
        for ordering in itertools.permutations(candidates):
            candidate, failures = self._evaluate_order(
                ordering=ordering,
                workday_start=max(workday_start, now),
                workday_end=workday_end,
                materials=material_by_task,
                routes=route_by_pair,
                worker=worker,
                vehicles=vehicle_by_id,
            )
            failure_codes.update(failures)
            if candidate is not None:
                evaluated.append(candidate)

        if not evaluated:
            reason_codes = tuple(sorted(failure_codes)) or ("NO_FEASIBLE_ORDERING",)
            return DispatchPlanResult(
                DispatchPlanStatus.NO_FEASIBLE_REPLAN,
                None,
                reason_codes,
                "No ordering satisfies material, route, availability, working-hour and hard-deadline constraints.",
            )

        evaluated.sort(
            key=lambda item: (
                item.changed_commitments,
                item.idle_seconds,
                item.travel_seconds,
                tuple(
                    (placement.job_id, placement.scheduled_task_id)
                    for placement in item.placements
                ),
            )
        )
        best = evaluated[0]
        best_order = tuple(item.assignment_id for item in best.placements)
        if best_order == original_order:
            unchanged = all(
                placement.start_at == assignment.start_at
                and placement.end_at == assignment.end_at
                for placement, assignment in zip(
                    best.placements,
                    sorted(candidates, key=lambda item: (item.start_at, item.assignment_id)),
                    strict=True,
                )
            )
            return DispatchPlanResult(
                (
                    DispatchPlanStatus.NO_CHANGE
                    if unchanged
                    else DispatchPlanStatus.NO_FEASIBLE_REPLAN
                ),
                None,
                (
                    ("CURRENT_ORDER_ALREADY_FEASIBLE",)
                    if unchanged
                    else ("NO_FEASIBLE_REORDER",)
                ),
                (
                    "The confirmed order is already feasible."
                    if unchanged
                    else "No feasible change to the order of existing assignments exists."
                ),
            )

        baseline = next(
            (
                item
                for item in evaluated
                if tuple(p.assignment_id for p in item.placements) == original_order
            ),
            None,
        )
        before = tuple(
            SchedulePlacement(
                assignment_id=item.assignment_id,
                job_id=item.job_id,
                scheduled_task_id=item.scheduled_task_id,
                worker_id=item.worker_id,
                vehicle_id=item.vehicle_id,
                start_at=item.start_at,
                end_at=item.end_at,
            )
            for item in sorted(candidates, key=lambda item: (item.start_at, item.assignment_id))
        )
        affected = tuple(
            placement.assignment_id
            for placement in best.placements
            if next(item for item in candidates if item.assignment_id == placement.assignment_id).start_at
            != placement.start_at
            or next(item for item in candidates if item.assignment_id == placement.assignment_id).end_at
            != placement.end_at
        )
        affected_jobs = tuple(sorted({item.job_id for item in candidates if item.assignment_id in affected}))
        affected_workflows = tuple(
            sorted(
                {
                    item.workflow_instance_id
                    for item in candidates
                    if item.assignment_id in affected
                }
            )
        )
        expected_workflow_revisions = tuple(
            sorted((item, workflow_revisions[item]) for item in affected_workflows)
        )
        expected_calendar_revisions = tuple(
            sorted(
                (item.assignment_id, item.revision)
                for item in candidates
                if item.assignment_id in affected
            )
        )
        material_refs = tuple(
            sorted(
                f"{item.job_id}:{item.scheduled_task_id}:r{item.revision}"
                for item in materials
                if item.job_id in affected_jobs
            )
        )
        payload = {
            "schema": "werkcrew-replan-proposal-v1",
            "initiating_workflow_instance_id": initiating_workflow_instance_id,
            "affected_jobs": affected_jobs,
            "affected_workflows": affected_workflows,
            "affected_assignments": affected,
            "before": [
                [p.assignment_id, p.start_at.isoformat(), p.end_at.isoformat()]
                for p in before
            ],
            "after": [
                [p.assignment_id, p.start_at.isoformat(), p.end_at.isoformat()]
                for p in best.placements
            ],
            "workflow_revisions": expected_workflow_revisions,
            "calendar_revisions": expected_calendar_revisions,
            "material_references": material_refs,
            "route_references": best.route_ids,
            "rule_version": self.rule_version,
        }
        fingerprint = _hash(payload)
        reason_codes = (
            "MATERIAL_NOT_READY_UNTIL",
            "ALTERNATIVE_TASK_READY",
            "ROUTE_FEASIBLE",
            "HARD_DEADLINE_PRESERVED",
            "CONFIRMED_COMMITMENT_CHANGE",
            "OWNER_APPROVAL_REQUIRED",
        )
        proposal = ReplanProposal(
            proposal_id=f"replan-{fingerprint.removeprefix('sha256:')[:24]}",
            initiating_workflow_instance_id=initiating_workflow_instance_id,
            status=ReplanProposalStatus.PENDING_OWNER_APPROVAL,
            affected_job_ids=affected_jobs,
            affected_workflow_instance_ids=affected_workflows,
            affected_assignment_ids=affected,
            before_schedule=before,
            after_schedule=best.placements,
            reason_codes=reason_codes,
            material_readiness_references=material_refs,
            route_snapshot_references=best.route_ids,
            expected_workflow_revisions=expected_workflow_revisions,
            expected_calendar_revisions=expected_calendar_revisions,
            worker_idle_time_before_seconds=(
                baseline.idle_seconds if baseline is not None else -1
            ),
            worker_idle_time_after_seconds=best.idle_seconds,
            travel_time_before_seconds=(
                baseline.travel_seconds if baseline is not None else -1
            ),
            travel_time_after_seconds=best.travel_seconds,
            deadline_impact="All true hard deadlines remain satisfied.",
            conditional=best.conditional,
            proposal_fingerprint=fingerprint,
            created_at=now,
        )
        return DispatchPlanResult(
            DispatchPlanStatus.PROPOSAL_CREATED,
            proposal,
            reason_codes,
            "A deterministic change to the order of existing same-worker assignments requires owner approval.",
        )

    def _evaluate_order(
        self,
        *,
        ordering: tuple[CalendarAssignment, ...],
        workday_start: datetime,
        workday_end: datetime,
        materials: dict[tuple[str, str], MaterialReadiness],
        routes: dict[tuple[str, str], RouteSnapshot],
        worker: DispatchWorker,
        vehicles: dict[str, DispatchVehicle],
    ) -> tuple[_Candidate | None, tuple[str, ...]]:
        cursor = workday_start
        previous_job: str | None = None
        placements: list[SchedulePlacement] = []
        route_ids: list[str] = []
        failures: list[str] = []
        idle_seconds = 0
        travel_seconds = 0
        conditional = False

        for assignment in ordering:
            if previous_job is not None and previous_job != assignment.job_id:
                route = routes.get((previous_job, assignment.job_id))
                if route is None:
                    failures.append("ROUTE_SNAPSHOT_MISSING")
                    return None, tuple(failures)
                cursor += timedelta(seconds=route.travel_duration_seconds)
                travel_seconds += route.travel_duration_seconds
                route_ids.append(route.route_snapshot_id)

            material = materials.get((assignment.job_id, assignment.scheduled_task_id))
            if material is None:
                failures.append("MATERIAL_READINESS_MISSING")
                return None, tuple(failures)
            earliest = cursor
            if material.blocking:
                if material.status is MaterialReadinessStatus.BLOCKED:
                    failures.append("MATERIAL_BLOCKED")
                    return None, tuple(failures)
                if material.status is MaterialReadinessStatus.EXPECTED:
                    conditional = True
                    earliest = max(earliest, material.available_at)
            idle_seconds += int((earliest - cursor).total_seconds())
            duration = assignment.end_at - assignment.start_at
            end = earliest + duration
            if end > workday_end:
                failures.append("WORKING_HOURS_EXCEEDED")
                return None, tuple(failures)
            if assignment.hard_deadline is not None and end > assignment.hard_deadline:
                failures.append("HARD_DEADLINE_VIOLATION")
                return None, tuple(failures)
            if not _covers(earliest, end, worker.availability):
                failures.append("WORKER_UNAVAILABLE")
                return None, tuple(failures)
            if assignment.vehicle_id is not None:
                vehicle = vehicles.get(assignment.vehicle_id)
                if vehicle is None or not _covers(earliest, end, vehicle.availability):
                    failures.append("VEHICLE_UNAVAILABLE")
                    return None, tuple(failures)
            placements.append(
                SchedulePlacement(
                    assignment_id=assignment.assignment_id,
                    job_id=assignment.job_id,
                    scheduled_task_id=assignment.scheduled_task_id,
                    worker_id=assignment.worker_id,
                    vehicle_id=assignment.vehicle_id,
                    start_at=earliest,
                    end_at=end,
                )
            )
            cursor = end
            previous_job = assignment.job_id

        changed = sum(
            placement.start_at != assignment.start_at
            or placement.end_at != assignment.end_at
            for placement in placements
            for assignment in ordering
            if placement.assignment_id == assignment.assignment_id
        )
        return (
            _Candidate(
                placements=tuple(placements),
                changed_commitments=changed,
                idle_seconds=idle_seconds,
                travel_seconds=travel_seconds,
                route_ids=tuple(route_ids),
                conditional=conditional,
            ),
            (),
        )
