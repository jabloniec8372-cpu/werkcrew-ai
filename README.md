# WERKcrew AI — job assessment i oględziny terenowe DEMO

Aplikacja FastAPI udostępnia deterministyczną ocenę zlecenia M1 oraz działającą pętlę oględzin M2: brief, przydział osoby po skillu i dostępności, mobilny raport oraz ponowną walidację do `READY_FOR_PLANNING`. Nie wykonuje planowania ani wyceny i nie korzysta z LLM ani zewnętrznych API.

## Uruchomienie w PowerShell na Windows

Wymagany jest Python 3.11 lub nowszy. W katalogu repozytorium:

```powershell
cd C:\WERKcrew_AI
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn werkcrew_ai.api.app:app --reload
```

Ekran koordynatora jest dostępny pod ścieżką:

```text
/demo/coordinator
```

WERKcrew Field jest dostępny po utworzeniu zadania oględzin pod ścieżką:

```text
/demo/field
```

Na ekranie koordynatora wybierz „Utwórz i przydziel oględziny”, przejdź do WERKcrew Field, a następnie wyślij wypełniony raport DEMO. Workflow zmieni się z `SITE_VISIT_REQUIRED` przez `SITE_VISIT_SCHEDULED` i `SITE_VISIT_COMPLETED` do `READY_FOR_PLANNING`.

Stan interaktywnego scenariusza jest przechowywany wyłącznie w pamięci pojedynczego procesu. Restart serwera lub przycisk „Resetuj scenariusz DEMO” przywraca stan początkowy. Nie jest to persistence produkcyjna.

Endpoint M1 pozostaje dostępny pod `/api/demo/job-assessment`, a dokumentacja FastAPI pod `/docs`. Wszystkie linki i redirecty korzystają z bieżącego hosta i portu serwera.

## Testy

Po aktywowaniu środowiska:

```powershell
python -m pytest
```
