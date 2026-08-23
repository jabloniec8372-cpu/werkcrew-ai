# 0007 — Prawdziwy owner decision interrupt/resume Strands M6

- **Status:** USTALONE dla M6 DEMO
- **Data:** 2026-08-22

## Kontekst

M5 kończy się na kompletnych, immutable wynikach pricingu i stanie `PRICING_READY_FOR_REVIEW`. Sam status aplikacyjny nie dowodzi jednak Human-in-the-Loop. M6 ma zatrzymać realny agent loop na decyzji właściciela, odtworzyć sesję w świeżej instancji `Agent` i wznowić dokładny pending interrupt bez przekazania decyzji modelowi.

## Decyzja

1. Jedyny nowy tool M6, `request_owner_decision`, używa rzeczywistego `ToolContext.interrupt`. Pierwsza invocation kończy się `AgentResult.stop_reason == "interrupt"`; backend zapisuje dokładny `interrupt_id` zwrócony przez Strands.
2. Każdy reset tworzy nowy `workflow_instance_id`. Stabilny `session_id` składa się z `job_request_id` i `workflow_instance_id`, a jawny `agent_id` zawsze wynosi `werkcrew-coordinator`.
3. `FileSessionManager` zapisuje lokalną sesję Strands w konfigurowalnym `WERKCREW_STRANDS_SESSION_DIR`. Na Windows domyślnie używa zapisywalnego `%LOCALAPPDATA%\WERKcrew_AI\strands-sessions`; repo-local storage pozostaje możliwym jawnym override. Invocation 2 tworzy nowy obiekt managera i nowy obiekt `Agent`, wskazujące ten sam session ID, agent ID i storage.
4. `PendingOwnerDecisionGate` przechowuje techniczną tożsamość pending interruptu, eligible plany, pricing fingerprint oraz stany `PENDING`, `RESUMING`, `RESOLVED`, `INVALIDATED`. `OwnerDecision` nie przechowuje danych technicznych sesji.
5. `pricing_gate_fingerprint` jest deterministycznym hashem jawnego owner-review view: identyfikatorów i fingerprintów wyników, reguły pricingu, statusów, waluty, widocznych category totals, modeled cost, netto, tax treatment, VAT i gross dla wszystkich wariantów posortowanych po `plan_id`.
6. Coordinator UI wysyła wyłącznie `gate_id`, `action` i opcjonalny `plan_id`. Backend pobiera session ID, agent ID, interrupt ID, pricing fingerprint, ceny, workflow instance i timestamp z zaufanego store.
7. Jeden lock `DemoWorkflowStore` atomowo waliduje i claimuje odpowiedź jako `RESUMING` wraz z deterministycznym response fingerprintem. Identyczny request po `RESOLVED` zwraca istniejącą decyzję bez drugiego resume; odpowiedź konfliktowa lub concurrent request są odrzucane.
8. Resume przekazuje wyłącznie właściwy blok `interruptResponse` dla zapisanego interrupt ID. Wznowiony tool ponownie waliduje gate i pricing, po czym atomowo zapisuje jedną immutable `OwnerDecision`, oznacza gate `RESOLVED` i przechodzi do `PLAN_APPROVED` albo `PLANS_REJECTED`.
9. Pricing jest zamrożony podczas `PENDING` i `RESUMING`. Niezgodny fingerprint invaliduje gate; odpowiedź nie dociera wtedy do Strands i decyzja nie powstaje.
10. Po prawidłowym resume agent otrzymuje `OWNER_DECISION_RECORDED`. Błąd narracji po atomowym commicie nie cofa decyzji ani finalnego workflow; błąd przed commitem nie tworzy decyzji.
11. Publiczny activity timeline zapisuje tylko fakty: utworzenie instancji, session/agent ID, interrupt, źródło odpowiedzi, resume, commit i finalny workflow. Nie zapisuje chain-of-thought.

## Granice i uczciwe twierdzenia

- `FileSessionManager` utrwala sesję Strands między osobnymi invocation i pozwala świeżej instancji agenta wznowić pending interrupt.
- `DemoWorkflowStore` nadal jest in-memory. Plik sesji nie odbudowuje workflow po restarcie aplikacji i nie zastępuje persistence domenowej.
- Sesja i workflow nie są zapisywane transakcyjnie razem. Rozwiązanie DEMO nie deklaruje multi-worker safety, produkcyjnego exactly-once delivery ani kryptograficznego uwierzytelnienia właściciela.
- `actor_role=OWNER` oznacza rolę korzystającą z Coordinator UI; źródłem decyzji jest `COORDINATOR_UI`, nie LLM.
- M6 nie tworzy ani nie wysyła oferty, nie generuje faktury, nie replanuje, nie zmienia pricingu i nie implementuje M7.
