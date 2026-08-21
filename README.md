# WERKcrew AI — Strands agent nad deterministycznym vertical slice DEMO

Aplikacja FastAPI udostępnia deterministyczną ocenę zlecenia M1, działającą pętlę oględzin M2 oraz planner zasobów M3. M4 dodaje prawdziwą pętlę `Strands Agent`, która przez Amazon Bedrock wybiera i wywołuje sześć cienkich tools opakowujących istniejącą logikę. LLM orkiestruje; walidatory M1/M2 i planner M3 pozostają źródłem prawdy. Agent nie tworzy raportu Petera, nie wycenia i nie zatwierdza Planu A/B.

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

Na ekranie koordynatora użyj „Uruchom WERKcrew Agent”. Model powinien wywołać tools M1/M2, przydzielić oględziny według reguł i zatrzymać się na `WAITING_FOR_FIELD_REPORT`. Po wysłaniu przez człowieka raportu w WERKcrew Field użyj „Wznów WERKcrew Agent”. Agent waliduje zapisany raport, uruchamia deterministyczny planner i zatrzymuje się na `WAITING_FOR_OWNER_REVIEW`, gdy workflow osiągnie `PLANS_READY_FOR_REVIEW`. Ekran pokazuje Plan A/B, decision trace oraz publiczny activity timeline `AGENT → TOOL → RESULT → NEXT STATE`.

Ręczne przyciski M2/M3 pozostają dostępne do diagnostyki deterministycznych funkcji bez wywołania modelu. W bieżącym środowisku deweloperskim live Bedrock nie został potwierdzony, ponieważ audyt nie znalazł AWS credentials ani skonfigurowanego regionu. Brak konfiguracji jest pokazywany jako status agenta `ERROR`; aplikacja nie przełącza się na fake model.

Stan interaktywnego scenariusza, wygenerowane plany i publiczny activity timeline są przechowywane wyłącznie w pamięci pojedynczego procesu. Restart serwera lub przycisk „Resetuj scenariusz DEMO” przywraca stan początkowy. Wznowienie agenta odczytuje bieżący stan biznesowy; M4 nie implementuje jeszcze trwałej sesji konwersacji, persistence produkcyjnej ani rezerwacji zasobów.

Endpoint M1 pozostaje dostępny pod `/api/demo/job-assessment`, a dokumentacja FastAPI pod `/docs`. Wszystkie linki i redirecty korzystają z bieżącego hosta i portu serwera.

## Testy

Po aktywowaniu środowiska:

```powershell
python -m pytest
```
