"""Public, process-local state for the bounded M4 agent demonstration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from threading import Lock

from werkcrew_ai.domain import WorkflowState


class AgentStatus(StrEnum):
    IDLE = "IDLE"
    ANALYZING = "ANALYZING"
    WAITING_FOR_FIELD_REPORT = "WAITING_FOR_FIELD_REPORT"
    PLANNING = "PLANNING"
    WAITING_FOR_PRICING_INPUT = "WAITING_FOR_PRICING_INPUT"
    WAITING_FOR_OWNER_REVIEW = "WAITING_FOR_OWNER_REVIEW"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class AgentActivity:
    timestamp: datetime
    action: str
    tool: str | None
    public_result: str
    workflow_state: WorkflowState
    rationale: str


@dataclass(frozen=True, slots=True)
class AgentRuntimeState:
    status: AgentStatus = AgentStatus.IDLE
    timeline: tuple[AgentActivity, ...] = ()
    last_action: str = "Agent nie został jeszcze uruchomiony."
    waiting_for: str = "Uruchomienie agenta"
    last_public_message: str = ""
    invoked_tools: tuple[str, ...] = ()


class AgentActivityStore:
    """Keeps the public tool audit trail for the process-local DEMO."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._state = AgentRuntimeState()

    def get(self) -> AgentRuntimeState:
        with self._lock:
            return self._state

    def reset(self) -> AgentRuntimeState:
        with self._lock:
            self._state = AgentRuntimeState()
            return self._state

    def set_status(
        self,
        status: AgentStatus,
        *,
        last_action: str,
        waiting_for: str,
        public_message: str | None = None,
    ) -> AgentRuntimeState:
        with self._lock:
            self._state = replace(
                self._state,
                status=status,
                last_action=last_action,
                waiting_for=waiting_for,
                last_public_message=(
                    public_message
                    if public_message is not None
                    else self._state.last_public_message
                ),
            )
            return self._state

    def record(
        self,
        *,
        action: str,
        public_result: str,
        workflow_state: WorkflowState,
        rationale: str,
        tool: str | None = None,
    ) -> AgentRuntimeState:
        entry = AgentActivity(
            timestamp=datetime.now(timezone.utc),
            action=action,
            tool=tool,
            public_result=public_result,
            workflow_state=workflow_state,
            rationale=rationale,
        )
        with self._lock:
            invoked_tools = self._state.invoked_tools
            if tool is not None:
                invoked_tools = (*invoked_tools, tool)
            self._state = replace(
                self._state,
                timeline=(*self._state.timeline, entry),
                last_action=action,
                invoked_tools=invoked_tools,
            )
            return self._state


demo_agent_activity_store = AgentActivityStore()
