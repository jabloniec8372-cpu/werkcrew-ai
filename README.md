# WERKcrew AI — deterministic job assessment

Pierwszy techniczny vertical slice udostępnia aplikację FastAPI i deterministyczną ocenę, czy dane DEMO zlecenia pozwalają przejść do zdalnej wyceny, czy wymagają oględzin. Nie wykonuje wyceny i nie korzysta z LLM ani zewnętrznych API.

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

Przykładowy scenariusz jest dostępny pod adresem:

```text
http://127.0.0.1:8000/api/demo/job-assessment
```

Interaktywna dokumentacja FastAPI:

```text
http://127.0.0.1:8000/docs
```

## Testy

Po aktywowaniu środowiska:

```powershell
python -m pytest
```
