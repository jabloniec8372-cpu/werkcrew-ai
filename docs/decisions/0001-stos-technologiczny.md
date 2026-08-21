# 0001 — Planowany stos technologiczny

- **Status:** USTALONE
- **Zakres:** plan architektury MVP

## Kontekst

WERKcrew AI potrzebuje warstwy agenta, deterministycznej logiki biznesowej, prostego lokalnego interfejsu oraz trwałości odpowiedniej dla MVP.

## Decyzja

Planowany stos obejmuje:

- Python,
- Strands Agents SDK,
- Claude Sonnet dostępny przez Amazon Bedrock,
- FastAPI,
- Jinja2 i HTMX,
- SQLite,
- FileSessionManager.

## Konsekwencje

- Integracja modelu ma być odizolowana od logiki domenowej.
- Interfejs MVP ma być renderowany po stronie serwera i nie wymaga osobnego rozbudowanego frontendu SPA.
- SQLite i pliki sesji są rozwiązaniem lokalnym dla MVP, nie automatyczną decyzją o architekturze produkcyjnej.
- Wersje, konfiguracja Bedrock, wariant modelu, region, hosting, uwierzytelnianie i strategia migracji pozostają **OPEN**.
- Sekrety, pliki sesji i lokalne bazy nie mogą być wersjonowane.
