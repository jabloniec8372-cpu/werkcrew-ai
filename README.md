# WERKcrew AI — deterministyczny vertical slice M1–M6

Aplikacja FastAPI udostępnia deterministyczną ocenę zlecenia M1, pętlę oględzin M2, planner zasobów M3, plan pricing M5 oraz realny owner decision interrupt/resume M6. Strands Agent przez Amazon Bedrock używa dziewięciu cienkich tools; `request_owner_decision` zatrzymuje prawdziwy agent loop przez `ToolContext.interrupt`. LLM orkiestruje, lecz nie tworzy raportu Petera, nie zmienia ani nie wybiera Planu A/B, nie zatwierdza ceny i nie wysyła oferty.

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

Stan biznesowy, plany, pricing i decyzja pozostają wyłącznie w pamięci pojedynczego procesu. `FileSessionManager` utrwala tylko sesję Strands potrzebną do świeżego-Agent resume; nie odbudowuje workflow po restarcie i nie jest produkcyjną persistence. Reset tworzy nowy `workflow_instance_id` oraz inny session ID, więc stary gate nie może zostać użyty dla nowego przebiegu.

Na Windows domyślny storage sesji znajduje się w zapisywalnym katalogu użytkownika `%LOCALAPPDATA%\WERKcrew_AI\strands-sessions`. `WERKCREW_STRANDS_SESSION_DIR` pozostaje jawnym override, np. dla repo-local storage, jeżeli wskazany katalog ma odpowiednie uprawnienia. Pliki sesji nie są częścią repozytorium.

Endpoint M1 pozostaje dostępny pod `/api/demo/job-assessment`, a dokumentacja FastAPI pod `/docs`. Wszystkie linki i redirecty korzystają z bieżącego hosta i portu serwera.

## Testy

Po aktywowaniu środowiska:

```powershell
python -m pytest
```
