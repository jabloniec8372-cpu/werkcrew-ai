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
| [Otwarte decyzje](OPEN.md) | OPEN | nierozstrzygnięte zasady produktu, procesu i implementacji |

Nowy dokument decyzji powinien opisywać kontekst, decyzję, konsekwencje i status. Punktu `OPEN` nie należy zamieniać w implementację bez wcześniejszego zatwierdzenia.
