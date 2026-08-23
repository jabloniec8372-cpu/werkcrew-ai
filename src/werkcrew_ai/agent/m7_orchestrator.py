"""Persistent M7 Strands orchestration reusing the M6 interrupt/session pattern."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable
from uuid import uuid4

from strands import Agent

from werkcrew_ai.agent.bedrock import build_live_bedrock_model
from werkcrew_ai.agent.m7_tools import PersistentDispatchAgentTools
from werkcrew_ai.agent.session import (
    WERKCREW_AGENT_ID,
    StrandsSessionSettings,
    build_file_session_manager,
)
from werkcrew_ai.agent.state import AgentStatus
from werkcrew_ai.persistence import SqliteBusinessRepository


M7_SYSTEM_PROMPT = """
You orchestrate the bounded WERKcrew M7 dispatch scenario using tools as the only
business source of truth. Read explicitly scoped state, record only the controlled
material event, invoke the deterministic daily dispatch planner, and request owner
approval for every proposal. Never calculate routes or schedules yourself. Never
reassign a worker or vehicle, never apply a proposal without human response, never
invent readiness, route or address data, and never expose chain-of-thought, full
addresses or coordinates. ToolContext.interrupt is a real human boundary. After
resume, briefly confirm the persisted owner action; do not quote, send or replan.
""".strip()


@dataclass(frozen=True, slots=True)
class M7AgentOutcome:
    stop_reason: str | None
    agent_status: AgentStatus
    public_message: str
    agent_instance_id: str | None
    session_id: str
    agent_id: str
    session_storage: str
    interrupt_id: str | None = None
    gate_id: str | None = None


class PersistentDispatchOrchestrator:
    def __init__(
        self,
        repository: SqliteBusinessRepository,
        *,
        initiating_workflow_instance_id: str,
        now: datetime,
        planning_date: date,
        workday_start: datetime,
        workday_end: datetime,
        model: Any | None = None,
        model_factory: Callable[[], Any] = build_live_bedrock_model,
        session_storage_dir: str | None = None,
        session_manager_factory=build_file_session_manager,
    ) -> None:
        self.repository = repository
        self.workflow_instance_id = initiating_workflow_instance_id
        self.now = now
        self.model = model
        self.model_factory = model_factory
        self.tools = PersistentDispatchAgentTools(
            repository,
            now=now,
            planning_date=planning_date,
            workday_start=workday_start,
            workday_end=workday_end,
        )
        self.session_storage_dir = (
            session_storage_dir
            if session_storage_dir is not None
            else StrandsSessionSettings.from_environment().storage_dir
        )
        self.session_manager_factory = session_manager_factory

    def _binding(self):
        return self.repository.get_session_binding(self.workflow_instance_id)

    def build_agent(self) -> Agent:
        binding = self._binding()
        model = self.model if self.model is not None else self.model_factory()
        manager = self.session_manager_factory(
            binding.session_id,
            self.session_storage_dir,
        )
        agent = Agent(
            model=model,
            tools=self.tools.registered(),
            system_prompt=M7_SYSTEM_PROMPT,
            callback_handler=None,
            agent_id=binding.agent_id,
            session_manager=manager,
        )
        setattr(agent, "werkcrew_instance_id", f"agent-instance-{uuid4().hex}")
        return agent

    def run_material_delay_scenario(
        self,
        *,
        job_id: str,
        scheduled_task_id: str,
        available_at: datetime,
        affected_job_ids: tuple[str, ...],
        worker_id: str,
    ) -> M7AgentOutcome:
        binding = self._binding()
        prompt = (
            "Controlled DEMO event: material readiness for "
            f"job_id={job_id}, scheduled_task_id={scheduled_task_id} changes from "
            f"revision 0 to EXPECTED at {available_at.isoformat()}. Inspect only "
            f"affected_job_ids={list(affected_job_ids)} for worker_id={worker_id}; "
            "create a deterministic proposal if feasible and request owner approval."
        )
        agent: Agent | None = None
        try:
            agent = self.build_agent()
            result = agent(
                prompt,
                invocation_state={
                    "workflow_instance_id": self.workflow_instance_id,
                    "session_id": binding.session_id,
                    "agent_id": binding.agent_id,
                },
            )
            return self._finish_interrupt_or_turn(result, agent)
        except Exception as exc:
            return M7AgentOutcome(
                "error",
                AgentStatus.ERROR,
                f"Agent/provider error: {type(exc).__name__}: {exc}",
                getattr(agent, "werkcrew_instance_id", None),
                binding.session_id,
                binding.agent_id,
                self.session_storage_dir,
            )

    def _finish_interrupt_or_turn(self, result, agent: Agent) -> M7AgentOutcome:
        binding = self._binding()
        if result.stop_reason == "interrupt":
            interrupts = list(result.interrupts or ())
            if len(interrupts) != 1:
                raise RuntimeError("Expected exactly one replan interrupt")
            interrupt = interrupts[0]
            reason = interrupt.reason
            if not isinstance(reason, Mapping) or not isinstance(reason.get("gate_id"), str):
                raise RuntimeError("Replan interrupt has no gate_id")
            gate = self.repository.bind_gate_interrupt(
                gate_id=reason["gate_id"],
                interrupt_id=interrupt.id,
            )
            return M7AgentOutcome(
                "interrupt",
                AgentStatus.WAITING_FOR_OWNER_REVIEW,
                "Replan proposal is waiting for owner decision.",
                getattr(agent, "werkcrew_instance_id"),
                binding.session_id,
                binding.agent_id,
                self.session_storage_dir,
                interrupt.id,
                gate.gate_id,
            )
        proposal = None
        gate = self.repository.pending_gate_for_workflow(self.workflow_instance_id)
        if gate and gate.proposal_id:
            proposal = self.repository.get_proposal(gate.proposal_id)
        status = (
            AgentStatus.OWNER_DECISION_RECORDED
            if proposal is not None and proposal.status.value in {"APPLIED", "REJECTED"}
            else AgentStatus.IDLE
        )
        return M7AgentOutcome(
            result.stop_reason,
            status,
            str(result).strip(),
            getattr(agent, "werkcrew_instance_id"),
            binding.session_id,
            binding.agent_id,
            self.session_storage_dir,
        )

    def resume_replan_decision(self, *, gate_id: str, action: str) -> M7AgentOutcome:
        binding = self._binding()
        claim = self.repository.claim_replan_response(gate_id=gate_id, action=action)
        if claim.existing_decision is not None:
            return M7AgentOutcome(
                "idempotent",
                AgentStatus.OWNER_DECISION_RECORDED,
                "Identical owner decision was already recorded.",
                None,
                binding.session_id,
                binding.agent_id,
                self.session_storage_dir,
                gate_id=gate_id,
            )
        agent: Agent | None = None
        try:
            agent = self.build_agent()
            result = agent(
                [
                    {
                        "interruptResponse": {
                            "interruptId": claim.interrupt_id,
                            "response": claim.canonical_response,
                        }
                    }
                ],
                invocation_state={
                    "workflow_instance_id": self.workflow_instance_id,
                    "session_id": binding.session_id,
                    "agent_id": binding.agent_id,
                },
            )
            outcome = self._finish_interrupt_or_turn(result, agent)
            resolved_gate = self.repository.get_gate(gate_id)
            final_status = (
                AgentStatus.OWNER_DECISION_RECORDED
                if resolved_gate.status.value == "RESOLVED"
                else outcome.agent_status
            )
            return M7AgentOutcome(
                outcome.stop_reason,
                final_status,
                outcome.public_message,
                outcome.agent_instance_id,
                outcome.session_id,
                outcome.agent_id,
                outcome.session_storage,
                claim.interrupt_id,
                gate_id,
            )
        except Exception as exc:
            return M7AgentOutcome(
                "error",
                AgentStatus.ERROR,
                f"Agent/provider error: {type(exc).__name__}: {exc}",
                getattr(agent, "werkcrew_instance_id", None),
                binding.session_id,
                binding.agent_id,
                self.session_storage_dir,
                claim.interrupt_id,
                gate_id,
            )
