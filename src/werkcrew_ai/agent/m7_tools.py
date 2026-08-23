"""Thin Strands tools for persistent M7 dispatch and replan HITL."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from strands import tool
from strands.types.tools import ToolContext

from werkcrew_ai.dispatch.service import PersistentDispatchService
from werkcrew_ai.persistence import SqliteBusinessRepository


class PersistentDispatchAgentTools:
    def __init__(
        self,
        repository: SqliteBusinessRepository,
        *,
        now: datetime,
        planning_date: date,
        workday_start: datetime,
        workday_end: datetime,
    ) -> None:
        self.repository = repository
        self.service = PersistentDispatchService(repository)
        self.now = now
        self.planning_date = planning_date
        self.workday_start = workday_start
        self.workday_end = workday_end

    @tool
    def get_multi_job_dispatch_state(
        self,
        initiating_workflow_instance_id: str,
        affected_job_ids: list[str],
    ) -> dict[str, Any]:
        """Read explicitly scoped jobs, calendar, material and route facts without PII."""

        initiating = self.repository.get_workflow(initiating_workflow_instance_id)
        job_ids = tuple(affected_job_ids)
        if initiating.job_id not in job_ids:
            raise ValueError("Initiating workflow job is outside affected_job_ids")
        workflows = self.repository.workflows_for_jobs(job_ids)
        assignments = self.repository.calendar_for_jobs(job_ids)
        materials = self.repository.materials_for_jobs(job_ids)
        routes = self.repository.routes_for_job_pairs(job_ids)
        return {
            "initiating_workflow_instance_id": initiating_workflow_instance_id,
            "workflows": [
                {
                    "job_id": item.job_id,
                    "workflow_instance_id": item.workflow_instance_id,
                    "revision": item.revision,
                    "state": item.state,
                }
                for item in workflows
            ],
            "assignments": [
                {
                    "assignment_id": item.assignment_id,
                    "job_id": item.job_id,
                    "scheduled_task_id": item.scheduled_task_id,
                    "worker_id": item.worker_id,
                    "vehicle_id": item.vehicle_id,
                    "start_at": item.start_at.isoformat(),
                    "end_at": item.end_at.isoformat(),
                    "status": item.status.value,
                    "revision": item.revision,
                }
                for item in assignments
            ],
            "material_readiness": [
                {
                    "job_id": item.job_id,
                    "scheduled_task_id": item.scheduled_task_id,
                    "status": item.status.value,
                    "available_at": (
                        item.available_at.isoformat() if item.available_at else None
                    ),
                    "blocking": item.blocking,
                    "source": item.source,
                    "revision": item.revision,
                }
                for item in materials
            ],
            "route_snapshots": [
                {
                    "route_snapshot_id": item.route_snapshot_id,
                    "origin_reference": item.origin_reference,
                    "destination_reference": item.destination_reference,
                    "travel_duration_seconds": item.travel_duration_seconds,
                    "distance_meters": item.distance_meters,
                    "provider": item.provider,
                    "status": item.status.value,
                }
                for item in routes
            ],
        }

    @tool
    def update_material_readiness(
        self,
        job_id: str,
        scheduled_task_id: str,
        expected_revision: int,
        available_at: str,
    ) -> dict[str, Any]:
        """Persist a controlled EXPECTED material event for one explicit job/task."""

        parsed = datetime.fromisoformat(available_at)
        updated = self.service.record_material_delay(
            job_id=job_id,
            scheduled_task_id=scheduled_task_id,
            expected_revision=expected_revision,
            available_at=parsed,
            now=self.now,
        )
        return {
            "job_id": updated.job_id,
            "scheduled_task_id": updated.scheduled_task_id,
            "status": updated.status.value,
            "available_at": updated.available_at.isoformat(),
            "revision": updated.revision,
            "source": updated.source,
        }

    @tool
    def create_daily_replan_proposal(
        self,
        initiating_workflow_instance_id: str,
        affected_job_ids: list[str],
        worker_id: str,
    ) -> dict[str, Any]:
        """Run deterministic bounded dispatch planning; never apply the proposal."""

        result = self.service.propose_dispatch_replan(
            initiating_workflow_instance_id=initiating_workflow_instance_id,
            affected_job_ids=tuple(affected_job_ids),
            worker_id=worker_id,
            planning_date=self.planning_date,
            now=self.now,
            workday_start=self.workday_start,
            workday_end=self.workday_end,
        )
        proposal = result.proposal
        return {
            "status": result.status.value,
            "reason_codes": list(result.reason_codes),
            "public_summary": result.public_summary,
            "proposal": (
                {
                    "proposal_id": proposal.proposal_id,
                    "status": proposal.status.value,
                    "affected_job_ids": list(proposal.affected_job_ids),
                    "affected_assignment_ids": list(proposal.affected_assignment_ids),
                    "before_order": [
                        item.job_id for item in proposal.before_schedule
                    ],
                    "after_order": [item.job_id for item in proposal.after_schedule],
                    "worker_idle_time_before_seconds": proposal.worker_idle_time_before_seconds,
                    "worker_idle_time_after_seconds": proposal.worker_idle_time_after_seconds,
                    "travel_time_before_seconds": proposal.travel_time_before_seconds,
                    "travel_time_after_seconds": proposal.travel_time_after_seconds,
                    "deadline_impact": proposal.deadline_impact,
                    "conditional": proposal.conditional,
                }
                if proposal is not None
                else None
            ),
        }

    @tool(context=True)
    def request_replan_approval(
        self,
        proposal_id: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Use real ToolContext.interrupt for one immutable replan proposal."""

        invocation = tool_context.invocation_state
        workflow_id = invocation.get("workflow_instance_id")
        session_id = invocation.get("session_id")
        agent_id = invocation.get("agent_id")
        proposal = self.repository.get_proposal(proposal_id)
        if proposal.initiating_workflow_instance_id != workflow_id:
            raise ValueError("Proposal belongs to another workflow")
        gate = self.repository.create_replan_gate(
            proposal_id=proposal_id,
            session_id=session_id,
            agent_id=agent_id,
            now=self.now,
        )
        response = tool_context.interrupt(
            name=f"replan-owner-decision-{gate.gate_id}",
            reason={
                "gate_id": gate.gate_id,
                "proposal_id": proposal.proposal_id,
                "proposal_fingerprint": proposal.proposal_fingerprint,
                "affected_job_ids": list(proposal.affected_job_ids),
            },
        )
        decision = self.repository.apply_replan_response(response, now=self.now)
        applied = self.repository.get_proposal(proposal_id)
        return {
            "status": "DECISION_RECORDED",
            "decision_id": decision.decision_id,
            "action": decision.action,
            "proposal_id": proposal_id,
            "proposal_status": applied.status.value,
            "source": decision.source,
            "actor_role": decision.actor_role,
        }

    def registered(self) -> list[Any]:
        return [
            self.get_multi_job_dispatch_state,
            self.update_material_readiness,
            self.create_daily_replan_proposal,
            self.request_replan_approval,
        ]
