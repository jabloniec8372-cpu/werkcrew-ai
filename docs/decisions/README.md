# Rejestr decyzji WERKcrew AI

Katalog zawiera decyzje architektoniczne oraz jawny rejestr tematów, których jeszcze nie rozstrzygnięto. Dokumenty opisują kierunek projektu; nie są dowodem implementacji.

## Statusy

- **USTALONE** — decyzja wynika z dotychczasowych ustaleń.
- **OPEN** — decyzja czeka na świadome rozstrzygnięcie.
- **ZASTĄPIONE** — wcześniejsza decyzja została zastąpiona nowszą (obecnie brak).

## Indeks

| Dokument | Status | Zakres |
|---|---|---|
| [0001 — Planowany stos technologiczny](0001-stos-technologiczny.md) | USTALONE | Python, Strands, Bedrock/Claude, FastAPI, Jinja2/HTMX, SQLite i FileSessionManager |
| [0002 — Granica LLM i reguł deterministycznych](0002-granica-llm-i-regul.md) | USTALONE | odpowiedzialność modelu, kodu i człowieka |
| [0003 — Minimalna pętla oględzin terenowych DEMO](0003-minimalna-petla-ogledzin-demo.md) | USTALONE | brief, przydział po skillu i dostępności, raport oraz gotowość do planowania w M2 |
| [0004 — Deterministyczne warianty planowania zasobów DEMO](0004-deterministyczne-warianty-planowania-demo.md) | USTALONE | jawne zadania, zasoby, Plan A/B, walidacja wykonalności i decision trace M3 |
| [0005 — Ograniczona orkiestracja agenta Strands w M4](0005-orkiestracja-agenta-strands-m4.md) | USTALONE | tools M1–M3, granice człowieka, publiczny timeline i konfiguracja Bedrock |
| [0006 — Deterministyczna wycena wariantów planu M5](0006-deterministic-plan-pricing-m5.md) | USTALONE | polityka pricingu, snapshoty, partial pricing, tax boundary i owner gate |
| [0007 — Prawdziwy owner decision interrupt/resume Strands M6](0007-real-owner-decision-interrupt-resume-m6.md) | USTALONE | ToolContext.interrupt, FileSessionManager, fresh-Agent resume, idempotentna decyzja właściciela |
| [0008 — Persistent multi-job dispatch i recovery M7](0008-persistent-multi-job-dispatch-recovery-m7.md) | USTALONE | SQLite ownership, CAS, material/route facts, immutable replan, restart recovery i public trace |
| [0009 — Frozen M8.0 business configuration](0009-frozen-m8-business-configuration.md) | USTALONE | 24 SKU, planning profiles, skill matrix, vehicle policy, i18n, chunking i fail-fast validation |
| [Otwarte decyzje](OPEN.md) | OPEN | nierozstrzygnięte zasady produktu, procesu i implementacji |

Nowy dokument decyzji powinien opisywać kontekst, decyzję, konsekwencje i status. Punktu `OPEN` nie należy zamieniać w implementację bez wcześniejszego zatwierdzenia.
