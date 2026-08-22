# WERKcrew AI — deterministyczny vertical slice M1–M5

Aplikacja FastAPI udostępnia deterministyczną ocenę zlecenia M1, pętlę oględzin M2, planner zasobów M3 oraz plan pricing M5. M4/M5 używają prawdziwego `Strands Agent` przez Amazon Bedrock i ośmiu cienkich tools. LLM orkiestruje; walidatory, planner i Decimal-only pricing pozostają źródłem prawdy. Agent nie tworzy raportu Petera, nie zmienia ani nie wybiera Planu A/B, nie zatwierdza ceny i nie wysyła oferty.

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

Na ekranie koordynatora użyj „Uruchom WERKcrew Agent”. Model powinien wywołać tools M1/M2, przydzielić oględziny według reguł i zatrzymać się na `WAITING_FOR_FIELD_REPORT`. Po wysłaniu przez człowieka raportu w WERKcrew Field agent uruchamia planner M3 i pricing M5. Owner gate otwiera dopiero `PRICING_READY_FOR_REVIEW` z kompletnymi wynikami; partial pricing zatrzymuje się na `WAITING_FOR_PRICING_INPUT`. Ekran pokazuje Plan A/B, deterministyczne kwoty `DEMO_SYNTHETIC`, porównanie cost driverów, decision trace i publiczny timeline `AGENT → TOOL → RESULT → NEXT STATE`.

Ręczne przyciski M2/M3/M5 pozostają dostępne do diagnostyki deterministycznych funkcji bez wywołania modelu. Brak konfiguracji lub błąd Bedrock jest pokazywany jako status agenta `ERROR`; aplikacja nie przełącza się na fake model.

Stan interaktywnego scenariusza, wygenerowane plany i publiczny activity timeline są przechowywane wyłącznie w pamięci pojedynczego procesu. Restart serwera lub przycisk „Resetuj scenariusz DEMO” przywraca stan początkowy. Wznowienie agenta odczytuje bieżący stan biznesowy; M4 nie implementuje jeszcze trwałej sesji konwersacji, persistence produkcyjnej ani rezerwacji zasobów.

Endpoint M1 pozostaje dostępny pod `/api/demo/job-assessment`, a dokumentacja FastAPI pod `/docs`. Wszystkie linki i redirecty korzystają z bieżącego hosta i portu serwera.

## Testy

Po aktywowaniu środowiska:

```powershell
python -m pytest
```
