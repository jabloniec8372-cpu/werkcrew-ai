"""Real Strands agent loop constrained by deterministic WERKcrew tools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from strands import Agent
from strands.agent.agent_result import AgentResult

from werkcrew_ai.agent.bedrock import build_live_bedrock_model
from werkcrew_ai.agent.session import (
    WERKCREW_AGENT_ID,
    StrandsSessionSettings,
    build_file_session_manager,
    workflow_session_id,
)
from werkcrew_ai.agent.state import AgentActivityStore, AgentRuntimeState, AgentStatus
from werkcrew_ai.agent.tools import WerkcrewAgentTools
from werkcrew_ai.domain import OwnerDecisionGateStatus, WorkflowState
from werkcrew_ai.infrastructure.demo_workflow_store import DemoWorkflowStore


SYSTEM_PROMPT = """
Jesteś agentem orkiestrującym ograniczony scenariusz WERKcrew M1-M6.
Używaj wyłącznie udostępnionych tools jako źródła prawdy biznesowej.
Zawsze najpierw odczytaj stan. Dla nowego zlecenia uruchom ocenę M1 i, gdy
oględziny są wymagane, przygotuj je. Zatrzymaj się po przydziale i czekaj na
raport człowieka. Nigdy nie twórz ani nie uzupełniaj raportu terenowego.
Stan RECEIVED oznacza brak zapisanej oceny M1: wywołaj wtedy assess_job.
prepare_site_visit jest dozwolone dopiero po przejściu do SITE_VISIT_REQUIRED.
Po otrzymaniu raportu sprawdź status, zwaliduj zapisany raport i dopiero gdy
workflow jest READY_FOR_PLANNING uruchom deterministyczny planner.
Planów M3 nie wolno Ci zmieniać. Gdy workflow jest PLANS_READY_FOR_REVIEW,
uruchom calculate_plan_quotes. Wszystkie liczby finansowe przyjmuj wyłącznie
z tools: nie licz ich sam, nie wymyślaj braków, stawek, materiałów ani kilometrów.
Przy INCOMPLETE wskaż brakujące inputy i zatrzymaj się. Przy REVIEW_REQUIRED
wskaż konieczność manual review. Przy COMPLETE objaśnij deterministyczne
różnice A/B, ale nigdy nie wybieraj wariantu, nie zatwierdzaj ceny i nie wysyłaj
oferty.
Gdy PRICING_READY_FOR_REVIEW nie ma aktywnego owner gate, wywołaj
request_owner_decision. Ten tool naprawdę przerywa run przez ToolContext.interrupt.
Nie twórz interruptResponse, nie udawaj kliknięcia właściciela, nie wybieraj planu
i nie twórz OwnerDecision. Po resume respektuj wyłącznie human response z toola.
Po PLAN_APPROVED albo PLANS_REJECTED krótko potwierdź zapis decyzji. Nie generuj
ani nie wysyłaj oferty.
Nigdy nie wybieraj ani nie zatwierdzaj Planu A/B. Nie wymyślaj danych,
dostępności, skillów, wymiarów, ryzyk, cen ani terminów. Nie obchodź
walidatorów. Odpowiedź publiczna ma być krótka i nie może zawierać prywatnego
tokowania rozumowania.
Po kompletnej kalkulacji pricingowej finalna publiczna odpowiedź ma mieć
maksymalnie około 180-200 słów. Podaj tylko status, modeled company cost,
recommended net price i gross price dla Planów A/B, główny deterministyczny
cost driver różnicy oraz informację, że decyzja należy do właściciela. Nie
pokazuj pełnych CostLines w narracji; pozostają one w structured pricing results
i UI.
""".strip()

RUN_PROMPT = """
Przeprowadź teraz właściwy kolejny fragment scenariusza DEMO na podstawie
aktualnego stanu. Wywołaj potrzebne tools, respektuj granice człowieka i
zatrzymaj się po osiągnięciu WAITING_FOR_FIELD_REPORT,
WAITING_FOR_PRICING_INPUT albo prawdziwego owner decision interrupt. Nie wykonuj
czynności za człowieka.
""".strip()


@dataclass(frozen=True, slots=True)
class AgentRunOutcome:
    runtime_state: AgentRuntimeState
    workflow_state: WorkflowState
    public_message: str
    stop_reason: str | None = None
    agent_instance_id: str | None = None
    session_id: str | None = None
    agent_id: str = WERKCREW_AGENT_ID
    interrupt_id: str | None = None
    session_storage: str | None = None


SessionManagerFactory = Callable[[str, str], Any]


class WerkcrewAgentOrchestrator:
    def __init__(
        self,
        workflow_store: DemoWorkflowStore,
        activity_store: AgentActivityStore,
        *,
        model: Any | None = None,
        model_factory: Callable[[], Any] = build_live_bedrock_model,
        session_storage_dir: str | None = None,
        session_manager_factory: SessionManagerFactory = build_file_session_manager,
    ) -> None:
        self.workflow_store = workflow_store
        self.activity_store = activity_store
        self.model = model
        self.model_factory = model_factory
        self.tools = WerkcrewAgentTools(workflow_store, activity_store)
        self.session_storage_dir = (
            session_storage_dir
            if session_storage_dir is not None
            else StrandsSessionSettings.from_environment().storage_dir
        )
        self.session_manager_factory = session_manager_factory
        self.last_agent_instance_id: str | None = None
        self.last_session_id: str | None = None

    def _session_id(self) -> str:
        snapshot = self.workflow_store.get()
        return workflow_session_id(
            snapshot.current_job_request.id,
            snapshot.workflow_instance_id,
        )

    def build_agent(self) -> Agent:
        model = self.model if self.model is not None else self.model_factory()
        session_id = self._session_id()
        session_manager = self.session_manager_factory(
            session_id,
            self.session_storage_dir,
        )
        agent = Agent(
            model=model,
            tools=self.tools.registered(),
            system_prompt=SYSTEM_PROMPT,
            callback_handler=None,
            agent_id=WERKCREW_AGENT_ID,
            session_manager=session_manager,
        )
        instance_id = f"agent-instance-{uuid4().hex}"
        setattr(agent, "werkcrew_instance_id", instance_id)
        setattr(agent, "werkcrew_session_id", session_id)
        setattr(agent, "werkcrew_session_storage", self.session_storage_dir)
        self.last_agent_instance_id = instance_id
        self.last_session_id = session_id
        return agent

    def _record_agent_instance(self, agent: Agent, *, resume: bool) -> None:
        snapshot = self.workflow_store.get()
        instance_id = getattr(agent, "werkcrew_instance_id")
        session_id = getattr(agent, "werkcrew_session_id")
        self.activity_store.record(
            action=(
                "Fresh Agent instance created for resume"
                if resume
                else "Agent instance created"
            ),
            public_result=(
                f"instance={instance_id}; session_id={session_id}; "
                f"agent_id={WERKCREW_AGENT_ID}."
            ),
            workflow_state=snapshot.workflow_state,
            rationale=(
                "Fresh Agent używa persisted Strands session."
                if resume
                else "Agent używa workflow-scoped persisted Strands session."
            ),
        )

    def _error_outcome(
        self,
        exc: Exception,
        *,
        agent_instance_id: str | None,
        session_id: str | None,
    ) -> AgentRunOutcome:
        message = f"Agent/provider error: {type(exc).__name__}: {exc}"
        self.activity_store.set_status(
            AgentStatus.ERROR,
            last_action="Agent run failed",
            waiting_for="Poprawna konfiguracja lub dostępność providera/sesji",
            public_message=message,
        )
        snapshot = self.workflow_store.get()
        self.activity_store.record(
            action="Agent run failed",
            public_result=message,
            workflow_state=snapshot.workflow_state,
            rationale="Błąd providera/sesji nie jest zastępowany pozornym wynikiem.",
        )
        return AgentRunOutcome(
            runtime_state=self.activity_store.get(),
            workflow_state=snapshot.workflow_state,
            public_message=message,
            stop_reason="error",
            agent_instance_id=agent_instance_id,
            session_id=session_id,
            session_storage=self.session_storage_dir,
        )

    def _finish_result(
        self,
        result: AgentResult,
        *,
        agent: Agent,
    ) -> AgentRunOutcome:
        instance_id = getattr(agent, "werkcrew_instance_id")
        session_id = getattr(agent, "werkcrew_session_id")

        if result.stop_reason == "interrupt":
            interrupts = list(result.interrupts or ())
            if len(interrupts) != 1:
                raise RuntimeError(
                    f"Oczekiwano jednego interruptu, otrzymano {len(interrupts)}."
                )
            interrupt = interrupts[0]
            reason = interrupt.reason
            if not isinstance(reason, Mapping) or not isinstance(
                reason.get("gate_id"), str
            ):
                raise RuntimeError("Interrupt nie zawiera technicznego gate_id.")
            gate = self.workflow_store.bind_owner_interrupt(
                gate_id=reason["gate_id"],
                interrupt_id=interrupt.id,
            )
            self.activity_store.record(
                action="Agent interrupted for owner decision",
                public_result=(
                    f"stop_reason=interrupt; interrupt_id={interrupt.id}; "
                    f"gate_id={gate.gate_id}."
                ),
                workflow_state=self.workflow_store.get().workflow_state,
                rationale="Strands zwrócił realny ToolContext.interrupt.",
            )
            public_message = (
                "Wycena jest gotowa. Agent został przerwany i czeka na decyzję "
                "właściciela w Coordinator UI."
            )
            runtime = self.activity_store.set_status(
                AgentStatus.WAITING_FOR_OWNER_REVIEW,
                last_action="Waiting at real owner decision interrupt",
                waiting_for="Decyzja właściciela w Coordinator UI",
                public_message=public_message,
            )
            return AgentRunOutcome(
                runtime_state=runtime,
                workflow_state=self.workflow_store.get().workflow_state,
                public_message=public_message,
                stop_reason=result.stop_reason,
                agent_instance_id=instance_id,
                session_id=session_id,
                interrupt_id=interrupt.id,
                session_storage=self.session_storage_dir,
            )

        public_message = str(result).strip()
        snapshot = self.workflow_store.get()
        if (
            snapshot.workflow_state
            in {WorkflowState.PLAN_APPROVED, WorkflowState.PLANS_REJECTED}
            and snapshot.owner_decision is not None
        ):
            status = AgentStatus.OWNER_DECISION_RECORDED
            action = "Owner decision recorded"
            waiting_for = "Brak dalszego kroku w M6"
            rationale = (
                "Immutable OwnerDecision została zapisana; M6 kończy się bez oferty."
            )
        elif snapshot.workflow_state is WorkflowState.PRICING_READY_FOR_REVIEW:
            gate = snapshot.pending_owner_gate
            if (
                gate is not None
                and gate.status is OwnerDecisionGateStatus.PENDING
                and gate.interrupt_id is not None
            ):
                status = AgentStatus.WAITING_FOR_OWNER_REVIEW
                action = "Waiting at real owner decision interrupt"
                waiting_for = "Decyzja właściciela w Coordinator UI"
                rationale = "Aktywny gate pochodzi z realnego Strands interrupt."
            else:
                status = AgentStatus.ERROR
                action = "Owner interrupt was not established"
                waiting_for = "Poprawny request_owner_decision interrupt"
                rationale = (
                    "PRICING_READY_FOR_REVIEW bez aktywnego realnego interruptu "
                    "nie jest granicą M6."
                )
        elif snapshot.workflow_state is WorkflowState.PLANS_READY_FOR_REVIEW:
            status = AgentStatus.WAITING_FOR_PRICING_INPUT
            action = "Waiting for pricing input"
            waiting_for = "Kompletne jawne dane pricingu dla wszystkich wariantów"
            rationale = (
                "Owner gate pozostaje zamknięty, dopóki wszystkie istniejące "
                "warianty nie mają statusu COMPLETE."
            )
        elif (
            snapshot.site_visit is not None
            and snapshot.site_visit.report is None
            and snapshot.workflow_state
            in {WorkflowState.SITE_VISIT_REQUIRED, WorkflowState.SITE_VISIT_SCHEDULED}
        ):
            status = AgentStatus.WAITING_FOR_FIELD_REPORT
            action = "Waiting for field report"
            waiting_for = "Raport człowieka przez WERKcrew Field"
            rationale = "Agent nie tworzy raportu za osobę wykonującą oględziny."
        elif snapshot.workflow_state is WorkflowState.SITE_VISIT_COMPLETED:
            status = AgentStatus.WAITING_FOR_FIELD_REPORT
            action = "Waiting for corrected field report"
            waiting_for = "Uzupełniony raport człowieka przez WERKcrew Field"
            rationale = "Nierozwiązane braki lub ryzyka blokują planowanie."
        else:
            status = AgentStatus.IDLE
            action = "Agent stopped without reaching a human boundary"
            waiting_for = "Ponowne uruchomienie po sprawdzeniu stanu"
            rationale = "Deterministyczny workflow nie osiągnął granicy M6."

        self.activity_store.record(
            action=action,
            public_result=f"Status agenta: {status.value}.",
            workflow_state=snapshot.workflow_state,
            rationale=rationale,
        )
        runtime = self.activity_store.set_status(
            status,
            last_action=action,
            waiting_for=waiting_for,
            public_message=public_message,
        )
        return AgentRunOutcome(
            runtime_state=runtime,
            workflow_state=snapshot.workflow_state,
            public_message=public_message,
            stop_reason=result.stop_reason,
            agent_instance_id=instance_id,
            session_id=session_id,
            session_storage=self.session_storage_dir,
        )

    def run(self) -> AgentRunOutcome:
        before = self.workflow_store.get()
        starting_status = (
            AgentStatus.PLANNING
            if before.site_visit is not None and before.site_visit.report is not None
            else AgentStatus.ANALYZING
        )
        self.activity_store.set_status(
            starting_status,
            last_action="Received job request",
            waiting_for="Wynik działania agenta",
        )
        self.activity_store.record(
            action="Received job request",
            public_result=f"Rozpoczęto od stanu {before.workflow_state.value}.",
            workflow_state=before.workflow_state,
            rationale="Agent ma dobrać kolejny tool bez zmiany reguł M1-M5.",
        )

        agent: Agent | None = None
        try:
            agent = self.build_agent()
            self._record_agent_instance(agent, resume=False)
            result = agent(
                RUN_PROMPT,
                invocation_state={
                    "session_id": getattr(agent, "werkcrew_session_id"),
                    "agent_id": WERKCREW_AGENT_ID,
                },
            )
            return self._finish_result(result, agent=agent)
        except Exception as exc:
            self.workflow_store.invalidate_unbound_owner_gate()
            return self._error_outcome(
                exc,
                agent_instance_id=(
                    getattr(agent, "werkcrew_instance_id") if agent is not None else None
                ),
                session_id=(
                    getattr(agent, "werkcrew_session_id") if agent is not None else None
                ),
            )

    def resume_owner_decision(
        self,
        *,
        gate_id: str,
        action: str,
        selected_plan_id: str | None,
    ) -> AgentRunOutcome:
        """Claim UI input, build a fresh Agent, and resume the exact interrupt."""

        claim = self.workflow_store.claim_owner_response(
            gate_id=gate_id,
            action=action,
            selected_plan_id=selected_plan_id,
            expected_session_id=self._session_id(),
            expected_agent_id=WERKCREW_AGENT_ID,
        )
        if claim.existing_decision is not None:
            snapshot = self.workflow_store.get()
            runtime = self.activity_store.set_status(
                AgentStatus.OWNER_DECISION_RECORDED,
                last_action="Returned existing owner decision",
                waiting_for="Brak dalszego kroku w M6",
                public_message="Identyczna decyzja właściciela była już zapisana.",
            )
            return AgentRunOutcome(
                runtime_state=runtime,
                workflow_state=snapshot.workflow_state,
                public_message=runtime.last_public_message,
                stop_reason="idempotent",
                session_id=(
                    snapshot.pending_owner_gate.session_id
                    if snapshot.pending_owner_gate is not None
                    else None
                ),
                session_storage=self.session_storage_dir,
            )

        if claim.canonical_response is None or claim.interrupt_id is None:
            raise RuntimeError("Claim nie zawiera danych wymaganych do resume.")
        snapshot = self.workflow_store.get()
        gate = snapshot.pending_owner_gate
        if gate is None:
            raise RuntimeError("Pending gate zniknął po claim.")
        self.activity_store.record(
            action="Human response received",
            public_result=(
                f"gate_id={gate.gate_id}; action={action}; "
                "source=COORDINATOR_UI."
            ),
            workflow_state=snapshot.workflow_state,
            rationale="Browser przekazał tylko gate_id, action i optional plan_id.",
        )

        agent: Agent | None = None
        try:
            agent = self.build_agent()
            if getattr(agent, "werkcrew_session_id") != gate.session_id:
                raise RuntimeError("Fresh Agent odtworzył inną session_id.")
            if agent.agent_id != gate.agent_id:
                raise RuntimeError("Fresh Agent odtworzył inny agent_id.")
            self._record_agent_instance(agent, resume=True)
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
                    "session_id": gate.session_id,
                    "agent_id": gate.agent_id,
                },
            )
            self.activity_store.record(
                action="Pending interrupt resumed",
                public_result=f"interrupt_id={claim.interrupt_id} resumed.",
                workflow_state=self.workflow_store.get().workflow_state,
                rationale="Invocation 2 użyła interruptResponse, nie RUN_PROMPT.",
            )
            return self._finish_result(result, agent=agent)
        except Exception as exc:
            return self._error_outcome(
                exc,
                agent_instance_id=(
                    getattr(agent, "werkcrew_instance_id") if agent is not None else None
                ),
                session_id=(
                    getattr(agent, "werkcrew_session_id")
                    if agent is not None
                    else gate.session_id
                ),
            )
