# WERKcrew AI — deterministyczny vertical slice DEMO

Aplikacja FastAPI udostępnia deterministyczną ocenę zlecenia M1, działającą pętlę oględzin M2 oraz planner zasobów M3. Po kompletnym raporcie terenowym planner sprawdza jawne zadania SYNTHETIC, skille, aktywność i dostępność pracowników, zależności oraz pojazdy. Zwraca maksymalnie dwa odmienne wykonalne warianty i decision trace. Nie wykonuje wyceny, nie zatwierdza planu i nie korzysta z LLM ani zewnętrznych API.

## Uruchomienie w PowerShell na Windows

Wymagany jest Python 3.11 lub nowszy. W katalogu repozytorium:

```powershell
cd C:\WERKcrew_AI
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn werkcrew_ai.api.app:app --reload --port 8010
```

Ekran koordynatora jest dostępny pod ścieżką:

```text
/demo/coordinator
```

WERKcrew Field jest dostępny po utworzeniu zadania oględzin pod ścieżką:

```text
/demo/field
```

Na ekranie koordynatora wybierz „Utwórz i przydziel oględziny”, przejdź do WERKcrew Field i wyślij wypełniony raport DEMO. Po stanie `READY_FOR_PLANNING` użyj „Wygeneruj warianty realizacji”. Ekran pokaże Plan A, Plan B, kolejność przydziałów, pojazdy, ograniczenia i sekcję „Dlaczego nie inni?”, a workflow przejdzie do `PLANS_READY_FOR_REVIEW`. Plan pozostaje do późniejszego przeglądu właściciela.

Stan interaktywnego scenariusza i wygenerowane plany są przechowywane wyłącznie w pamięci pojedynczego procesu. Restart serwera lub przycisk „Resetuj scenariusz DEMO” przywraca stan początkowy. Nie jest to persistence produkcyjna ani rezerwacja zasobów.

Endpoint M1 pozostaje dostępny pod `/api/demo/job-assessment`, a dokumentacja FastAPI pod `/docs`. Wszystkie linki i redirecty korzystają z bieżącego hosta i portu serwera.

## Testy

Po aktywowaniu środowiska:

```powershell
python -m pytest
```
