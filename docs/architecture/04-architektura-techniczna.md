# Planowana architektura techniczna

## Stos technologiczny

**USTALONE jako planowany kierunek:**

- **Python** — język aplikacji oraz deterministycznych modułów biznesowych,
- **Strands Agents SDK** — warstwa budowy i orkiestracji agenta,
- **Claude Sonnet przez Amazon Bedrock** — planowany model językowy i sposób dostępu do niego,
- **FastAPI** — warstwa HTTP i API,
- **Jinja2 + HTMX** — serwerowo renderowany interfejs webowy z lekką interaktywnością,
- **SQLite** — lokalna baza danych dla MVP,
- **FileSessionManager** — lokalna trwałość sesji Strands dla realnego interrupt/resume; nie zastępuje SQLite business state.

Konfigurację agenta opisują ADR [0005](../decisions/0005-orkiestracja-agenta-strands-m4.md) i [0007](../decisions/0007-real-owner-decision-interrupt-resume-m6.md), pricing ADR [0006](../decisions/0006-deterministic-plan-pricing-m5.md), a trwałość M7 ADR [0008](../decisions/0008-persistent-multi-job-dispatch-recovery-m7.md). Żadne credentials ani runtime databases nie są zapisywane w Git.

## Podział odpowiedzialności

### Model językowy

Model językowy odpowiada za zadania wymagające rozumienia i generowania języka:

- interpretację nieustrukturyzowanego opisu,
- wydobywanie kandydatów na dane wejściowe do dalszej walidacji,
- wskazywanie brakujących informacji,
- prowadzenie rozmowy i objaśnianie wyników,
- koordynowanie wywołań zatwierdzonych narzędzi,
- tworzenie roboczych tekstów na podstawie danych systemowych.

Wynik modelu jest zawsze `DRAFT_TEXT`. Nie orzeka stanu, zgody, legalności ani prawdy. Przed wysłaniem materialny komunikat przechodzi deterministyczny walidator `NO_DECEPTION`.

### Reguły deterministyczne

Kod Pythona odpowiada za wynik wymagający powtarzalności i audytu:

- walidację danych wejściowych,
- sprawdzanie dostępności i konfliktów zasobów,
- dopasowanie wymaganych kompetencji, transportu i narzędzi,
- obliczenia czasu, kosztów, materiałów, VAT i marży,
- reguły pracy wieczornej oraz weekendowej,
- porównanie wariantów według jawnych kryteriów,
- wersjonowanie i ponowne odtworzenie kalkulacji,
- egzekwowanie bramek akceptacji po ich ustaleniu.
- rozdzielenie wykonalności, relacji do polityki, override'u i autoryzacji wykonania;
- klasyfikację sprzeciwu według treści, nie rangi osoby;
- niezależny zapis zdrowia firmy, sygnału recovery i odpowiedzi OWNER;
- walidację siły twierdzeń i wymaganej zgody przed komunikacją.

Te same zwalidowane dane i ta sama wersja reguł muszą dawać ten sam wynik.

### Człowiek

Człowiek odpowiada za decyzje zastrzeżone, miękkie wyjątki i skutki biznesowe. Jego decyzja nie nadpisuje faktów ani twardych granic. Dokładne mapowanie operacji między właściciela, Petera, pracowników terenowych i ewentualne inne role pozostaje **OPEN**.

Istniejące `OwnerDecision` i `PersistentOwnerDecision` dotyczą zatwierdzenia planu lub replanu. Nie są automatycznie `OverrideStatus.APPROVED`; miękki wyjątek wymaga jawnego zakresu, podstawy i osobnej walidacji semantycznej. Obecny `AgentTraceEvent` pozostaje publicznym timeline'em M7, natomiast pełny `decision_record` ma kontrakt w `schemas/decision-trail.schema.json`. Produkcyjne powiązanie i trwałość pełnego rekordu pozostają **OPEN**.

## Granice komponentów

- `api` — kontrakty HTTP, walidacja transportowa i składanie odpowiedzi.
- `agent` — konfiguracja Strands, narzędzia, kontekst, prompty i polityki użycia modelu.
- `planning` — deterministyczne budowanie oraz walidacja planów.
- `pricing` — deterministyczne kalkulacje i rozbicie ceny.
- `field` — przypadki użycia WERKcrew Field.
- `domain` — encje, wartości i reguły niezależne od frameworków.
- `infrastructure` — SQLite, FileSessionManager, Bedrock i pozostałe adaptery.
- `core` — konfiguracja, logowanie i współdzielone mechanizmy techniczne.

**USTALONE:** warstwa API i infrastruktura nie powinny zawierać właściwej logiki planowania ani wyceny. Dostęp do usług zewnętrznych powinien być odizolowany od domeny.

## Dane i sesje

W M7 SQLite jest source of truth business state. Snapshot workflow zawiera dane job-local, natomiast calendar, material readiness, routes, proposals, gates/decisions i trace mają pojedynczych znormalizowanych właścicieli. `FileSessionManager` przechowuje wyłącznie ciągłość Strands. M7 zapewnia transakcje i CAS w pojedynczym lokalnym procesie; retencja, backup, szyfrowanie, HA i produkcyjna multi-process concurrency pozostają **OPEN**.

Repozytorium przechowuje wyłącznie syntetyczne dane demonstracyjne. Lokalne bazy, sesje, logi i sekrety pozostają poza Git.

## Przepływ odpowiedzialności

```text
Użytkownik
    |
    v
FastAPI + Jinja2/HTMX
    |
    +--> agent Strands + Claude Sonnet/Bedrock
    |        |
    |        `--> wywołanie zatwierdzonych narzędzi
    |
    +--> deterministyczne planning/pricing/domain
    |
    `--> infrastruktura: SQLite / FileSessionManager / adaptery
```

Diagram pokazuje granice, nie gotową topologię wdrożenia. Sposób uruchomienia, autoryzacja, hosting i integracje produkcyjne są **OPEN**.
