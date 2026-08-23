# WERKcrew AI — deterministyczny vertical slice M1–M7

Aplikacja FastAPI udostępnia deterministyczną ocenę M1, oględziny M2, planner M3, pricing M5, realny owner interrupt/resume M6 oraz persistent multi-job dispatch M7. M7 przechowuje business state w SQLite, a Strands `FileSessionManager` nadal odpowiada wyłącznie za ciągłość sesji i HITL. LLM orkiestruje tools; nie wylicza harmonogramu, nie zmienia pracownika/pojazdu i nie stosuje replanu bez decyzji właściciela.

## Uruchomienie w PowerShell na Windows

Wymagany jest Python 3.11 lub nowszy. W katalogu repozytorium:

```powershell
cd C:\WERKcrew_AI
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
$env:WERKCREW_BEDROCK_MODEL_ID = "<model-id-lub-inference-profile-dostępny-na-koncie>"
$env:WERKCREW_AWS_REGION = "<region>"
$env:WERKCREW_BEDROCK_MAX_TOKENS = "1400"
# Opcjonalny katalog sesji. Na Windows domyślnie:
# %LOCALAPPDATA%\WERKcrew_AI\strands-sessions
$env:WERKCREW_STRANDS_SESSION_DIR = "$PWD\var\strands-sessions"
# Opcjonalna baza business state M7; domyślnie w %LOCALAPPDATA%\WERKcrew_AI:
$env:WERKCREW_DB_PATH = "$PWD\var\db\werkcrew-business.db"
# Opcjonalnie, jeżeli nie jest używany profil default:
$env:AWS_PROFILE = "<profil>"
python -m uvicorn werkcrew_ai.api.app:app --reload --port 8010
```

Projekt używa dokładnie `strands-agents==1.50.2` i `strands.models.BedrockModel`. Model ID oraz region nie mają fikcyjnych wartości domyślnych: muszą wskazywać rzeczywiście dostępny model lub inference profile na skonfigurowanym koncie AWS. Nazwy zmiennych znajdują się również w `.env.example`; aplikacja nie wczytuje automatycznie pliku `.env`.

Ekran koordynatora jest dostępny pod ścieżką:

```text
/demo/coordinator
```

WERKcrew Field jest dostępny po utworzeniu zadania oględzin pod ścieżką:

```text
/demo/field
```

Na ekranie koordynatora użyj „Uruchom WERKcrew Agent”. Model powinien wywołać tools M1/M2, przydzielić oględziny według reguł i zatrzymać się na `WAITING_FOR_FIELD_REPORT`. Po raporcie człowieka agent uruchamia planner M3 i pricing M5. Przy kompletnych wynikach M6 wywołuje `request_owner_decision`; Strands zwraca `stop_reason=interrupt`, a UI pokazuje wyłącznie legalne decyzje. Kliknięcie właściciela tworzy świeżą instancję Agent, odtwarza tę samą sesję plikową i wznawia dokładny interrupt. Flow kończy się na `PLAN_APPROVED` albo `PLANS_REJECTED`, bez utworzenia lub wysłania oferty.

Ręczne przyciski M2/M3/M5 pozostają dostępne do diagnostyki deterministycznych funkcji bez wywołania modelu. Brak konfiguracji lub błąd Bedrock jest pokazywany jako status agenta `ERROR`; aplikacja nie przełącza się na fake model.

Canonical demo M1–M6 zachowuje dotychczasowy in-memory adapter. M7 używa SQLite jako source of truth dla wielu workflow, kalendarza, material readiness, route snapshots, propozycji, gate/decisions i publicznego trace. `FileSessionManager` nie duplikuje business state; przy rozbieżności SQLite i pliku sesji recovery kończy się fail-safe.

Na Windows domyślny storage sesji znajduje się w zapisywalnym katalogu użytkownika `%LOCALAPPDATA%\WERKcrew_AI\strands-sessions`. `WERKCREW_STRANDS_SESSION_DIR` pozostaje jawnym override, np. dla repo-local storage, jeżeli wskazany katalog ma odpowiednie uprawnienia. Pliki sesji nie są częścią repozytorium.

Endpoint M1 pozostaje dostępny pod `/api/demo/job-assessment`, a dokumentacja FastAPI pod `/docs`. Wszystkie linki i redirecty korzystają z bieżącego hosta i portu serwera.

## M7 — ograniczony dispatch

Widok `/demo/m7` pokazuje trzy jawne zlecenia DEMO, calendar/material facts, immutable `ReplanProposal` i per-job public trace. `DailyDispatchPlanner` porównuje najwyżej sześć jawnych kolejności maksymalnie trzech istniejących `ScheduledTask` tego samego pracownika. Nie tworzy crew, nie zmienia worker/vehicle assignment, nie rusza `IN_PROGRESS` i odrzuca naruszenia hard deadline.

`READY`, `EXPECTED` i `BLOCKED` odpowiadają wyłącznie na pytanie, czy zadanie może zacząć się w proponowanym czasie. Routing to zapisany `RouteSnapshot` z point-to-point Amazon Location lub jawnego fixture; planner nigdy nie konsumuje surowej odpowiedzi AWS. M7 nie implementuje geocodingu, Route Matrix, OptimizeWaypoints, globalnego schedulera, procurement/inventory, worker reassignment ani automatycznej zmiany confirmed calendar.

## Testy

Po aktywowaniu środowiska:

```powershell
python -m pytest
```
