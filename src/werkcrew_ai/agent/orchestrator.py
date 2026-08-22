"""Real Strands agent loop constrained by deterministic WERKcrew tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from strands import Agent

from werkcrew_ai.agent.bedrock import build_live_bedrock_model
from werkcrew_ai.agent.state import AgentActivityStore, AgentRuntimeState, AgentStatus
from werkcrew_ai.agent.tools import WerkcrewAgentTools
from werkcrew_ai.domain import WorkflowState
from werkcrew_ai.infrastructure.demo_workflow_store import DemoWorkflowStore

SYSTEM_PROMPT = """
Jesteś agentem orkiestrującym ograniczony scenariusz WERKcrew M1-M5.
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
oferty. Nigdy nie wybieraj ani nie zatwierdzaj Planu A/B. Nie wymyślaj danych,
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
WAITING_FOR_PRICING_INPUT albo WAITING_FOR_OWNER_REVIEW. Nie wykonuj czynności
za człowieka.
""".strip()


@dataclass(frozen=True, slots=True)
class AgentRunOutcome:
    runtime_state: AgentRuntimeState
    workflow_state: WorkflowState
    public_message: str


class WerkcrewAgentOrchestrator:
    def __init__(
        self,
        workflow_store: DemoWorkflowStore,
        activity_store: AgentActivityStore,
        *,
        model: Any | None = None,
        model_factory: Callable[[], Any] = build_live_bedrock_model,
    ) -> None:
        self.workflow_store = workflow_store
        self.activity_store = activity_store
        self.model = model
        self.model_factory = model_factory
        self.tools = WerkcrewAgentTools(workflow_store, activity_store)

    def build_agent(self) -> Agent:
        model = self.model if self.model is not None else self.model_factory()
        return Agent(
            model=model,
            tools=self.tools.registered(),
            system_prompt=SYSTEM_PROMPT,
            callback_handler=None,
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
            rationale="Agent ma dobrać kolejny tool bez zmiany reguł M1-M3.",
        )

        try:
            result = self.build_agent()(RUN_PROMPT)
            public_message = str(result).strip()
        except Exception as exc:
            message = f"Agent/provider error: {type(exc).__name__}: {exc}"
            self.activity_store.set_status(
                AgentStatus.ERROR,
                last_action="Agent run failed",
                waiting_for="Poprawna konfiguracja lub dostępność providera",
                public_message=message,
            )
            snapshot = self.workflow_store.get()
            self.activity_store.record(
                action="Agent run failed",
                public_result=message,
                workflow_state=snapshot.workflow_state,
                rationale="Błąd providera nie jest zastępowany pozornym wynikiem.",
            )
            return AgentRunOutcome(
                runtime_state=self.activity_store.get(),
                workflow_state=snapshot.workflow_state,
                public_message=message,
            )

        snapshot = self.workflow_store.get()
        if snapshot.workflow_state is WorkflowState.PRICING_READY_FOR_REVIEW:
            status = AgentStatus.WAITING_FOR_OWNER_REVIEW
            action = "Waiting for owner review"
            waiting_for = "Przegląd wycenionych Planów A/B przez właściciela"
            rationale = (
                "Kompletne wyniki M5 są gotowe; agent nie wybiera wariantu "
                "ani nie zatwierdza ceny."
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
            rationale = "Deterministyczny workflow nie osiągnął granicy M4."

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
        )
