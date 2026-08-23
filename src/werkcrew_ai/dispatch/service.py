"""Application service coordinating persistent facts and deterministic M7 planning."""

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from werkcrew_ai.dispatch.models import (
    DISPATCH_RULE_VERSION,
    AgentTraceEvent,
    DispatchPlanResult,
    DispatchPlanStatus,
    MaterialReadinessStatus,
    ReplanProposal,
    TraceActor,
)
from werkcrew_ai.dispatch.planner import DailyDispatchPlanner
from werkcrew_ai.persistence import SqliteBusinessRepository


class PersistentDispatchService:
    def __init__(
        self,
        repository: SqliteBusinessRepository,
        *,
        planner: DailyDispatchPlanner | None = None,
    ) -> None:
        self.repository = repository
        self.planner = planner or DailyDispatchPlanner()

    def record_material_delay(
        self,
        *,
        job_id: str,
        scheduled_task_id: str,
        expected_revision: int,
        available_at: datetime,
        now: datetime,
        source: str = "CONTROLLED_DEMO_INPUT",
    ):
        updated = self.repository.update_material_readiness_cas(
            job_id=job_id,
            scheduled_task_id=scheduled_task_id,
            expected_revision=expected_revision,
            status=MaterialReadinessStatus.EXPECTED,
            available_at=available_at,
            blocking=True,
            source=source,
            now=now,
        )
        workflow = self.repository.workflows_for_jobs((job_id,))[0]
        self.repository.append_trace(
            AgentTraceEvent(
                event_id=f"trace-{uuid4().hex}",
                timestamp=now,
                job_id=job_id,
                workflow_instance_id=workflow.workflow_instance_id,
                actor=TraceActor.TOOL,
                action="Material readiness updated",
                redacted_input_summary=(
                    f"Task {scheduled_task_id}; controlled readiness event."
                ),
                result_summary=f"Material expected at {available_at.isoformat()}.",
                previous_state="READY",
                next_state="EXPECTED",
                reason_code="MATERIAL_NOT_READY_UNTIL",
                rule_version=None,
                external_data_source=source,
                success=True,
                gate_id=None,
                owner_decision_id=None,
                proposal_id=None,
                operation_id=None,
                correlation_id=f"material:{job_id}:{scheduled_task_id}:r{updated.revision}",
            )
        )
        return updated

    def propose_dispatch_replan(
        self,
        *,
        initiating_workflow_instance_id: str,
        affected_job_ids: tuple[str, ...],
        worker_id: str,
        planning_date: date,
        now: datetime,
        workday_start: datetime,
        workday_end: datetime,
    ) -> DispatchPlanResult:
        initiating = self.repository.get_workflow(initiating_workflow_instance_id)
        if initiating.job_id not in affected_job_ids:
            raise ValueError("Initiating workflow job must be explicitly affected")
        workflows = self.repository.workflows_for_jobs(affected_job_ids)
        workflow_by_job = {item.job_id: item for item in workflows}
        if set(workflow_by_job) != set(affected_job_ids):
            raise ValueError("Every affected job must have an explicit workflow")
        assignments = self.repository.calendar_for_jobs(affected_job_ids)
        if any(
            assignment.workflow_instance_id
            != workflow_by_job[assignment.job_id].workflow_instance_id
            for assignment in assignments
        ):
            raise ValueError("Cross-job calendar/workflow reference mismatch")
        vehicles = tuple(
            self.repository.get_vehicle(vehicle_id)
            for vehicle_id in sorted(
                {item.vehicle_id for item in assignments if item.vehicle_id is not None}
            )
        )
        result = self.planner.plan(
            initiating_workflow_instance_id=initiating_workflow_instance_id,
            planning_date=planning_date,
            now=now,
            workday_start=workday_start,
            workday_end=workday_end,
            jobs=tuple(self.repository.get_job(item) for item in affected_job_ids),
            workflow_revisions={
                item.workflow_instance_id: item.revision for item in workflows
            },
            assignments=assignments,
            materials=self.repository.materials_for_jobs(affected_job_ids),
            routes=self.repository.routes_for_job_pairs(affected_job_ids),
            worker=self.repository.get_worker(worker_id),
            vehicles=vehicles,
        )
        if result.status is not DispatchPlanStatus.PROPOSAL_CREATED:
            return result
        proposal = self.repository.save_proposal(result.proposal)
        correlation_id = f"replan:{proposal.proposal_id}"
        workflow_by_id = {
            item.workflow_instance_id: item for item in workflows
        }
        job_workflow = {
            item.job_id: item.workflow_instance_id for item in workflows
        }
        for job_id in proposal.affected_job_ids:
            self.repository.append_trace(
                AgentTraceEvent(
                    event_id=f"trace-{uuid4().hex}",
                    timestamp=now,
                    job_id=job_id,
                    workflow_instance_id=job_workflow[job_id],
                    actor=TraceActor.TOOL,
                    action="Daily dispatch proposal created",
                    redacted_input_summary=(
                        "Existing same-worker assignments, persisted readiness and route facts."
                    ),
                    result_summary=(
                        "Alternative order is feasible and awaits owner approval."
                    ),
                    previous_state=workflow_by_id[job_workflow[job_id]].state,
                    next_state=workflow_by_id[job_workflow[job_id]].state,
                    reason_code="OWNER_APPROVAL_REQUIRED",
                    rule_version=DISPATCH_RULE_VERSION,
                    external_data_source="PERSISTED_ROUTE_SNAPSHOTS",
                    success=True,
                    gate_id=None,
                    owner_decision_id=None,
                    proposal_id=proposal.proposal_id,
                    operation_id=None,
                    correlation_id=correlation_id,
                )
            )
        return DispatchPlanResult(
            status=result.status,
            proposal=proposal,
            reason_codes=result.reason_codes,
            public_summary=result.public_summary,
        )
