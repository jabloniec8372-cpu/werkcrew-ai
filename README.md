# WERKcrew AI

WERKcrew AI to przygotowywany system wspierający planowanie, wycenę i realizację prac terenowych. Repozytorium jest obecnie wyłącznie szkieletem projektu: nie zawiera jeszcze działającej aplikacji, endpointów API, logiki agenta AI ani algorytmów biznesowych.

## Cele architektury

- oddzielenie warstwy FastAPI od logiki domenowej,
- izolacja niedeterministycznych działań agenta AI od deterministycznego planowania i wyceny,
- wydzielenie obszaru WERKcrew Field dla procesów terenowych,
- możliwość testowania logiki biznesowej bez uruchamiania API i usług zewnętrznych,
- bezpieczne przechowywanie lokalnych danych roboczych bez umieszczania ich w Git.

## Struktura projektu

```text
WERKcrew_AI/
|-- src/werkcrew_ai/
|   |-- api/              # przyszłe endpointy, schematy wejścia/wyjścia i zależności FastAPI
|   |-- agent/            # orkiestracja agenta AI, narzędzia, prompty i polityki użycia modeli
|   |-- planning/         # deterministyczne reguły i algorytmy planowania
|   |-- pricing/          # deterministyczne kalkulacje kosztów i wycen
|   |-- field/            # procesy WERKcrew Field i obsługa pracy terenowej
|   |-- core/             # konfiguracja, logowanie i elementy współdzielone
|   |-- domain/           # encje, typy i reguły domenowe niezależne od infrastruktury
|   `-- infrastructure/   # baza danych, integracje i adaptery usług zewnętrznych
|-- data/demo/            # wersjonowane, syntetyczne dane demonstracyjne
|-- docs/
|   |-- architecture/     # opis architektury i przepływów systemu
|   `-- decisions/        # krótkie rejestry decyzji architektonicznych (ADR)
|-- migrations/           # przyszłe migracje schematu bazy danych
|-- scripts/              # pomocnicze skrypty deweloperskie i administracyjne
|-- tests/
|   |-- unit/             # szybkie testy modułów w izolacji
|   |-- integration/      # testy współpracy warstw i adapterów
|   `-- fixtures/         # współdzielone dane i fabryki testowe
`-- var/                  # lokalne bazy SQLite i logi; zawartość ignorowana przez Git
```

## Założone granice

Agent AI może w przyszłości interpretować polecenia i koordynować narzędzia, ale nie powinien samodzielnie wykonywać obliczeń wymagających powtarzalnego wyniku. Planowanie i wycena pozostają deterministycznymi modułami Pythona, które zwracają te same rezultaty dla tych samych danych wejściowych. Warstwa `api` udostępni funkcje systemu, a `infrastructure` odizoluje bazę danych i integracje zewnętrzne od logiki domenowej.

## Start środowiska deweloperskiego

Projekt nie ma jeszcze kodu uruchomieniowego. Gdy rozpocznie się implementacja, bazowe środowisko będzie można przygotować następująco:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Plik `requirements.txt` jest listą startową, a nie zamrożonym zestawem wersji produkcyjnych. Przed pierwszym wdrożeniem zależności należy zweryfikować, ograniczyć wersjami i objąć kontrolą bezpieczeństwa.

## Dane i sekrety

- Do repozytorium wolno dodawać wyłącznie syntetyczne dane demonstracyjne pozbawione danych osobowych.
- Klucze API, hasła i tokeny należy przekazywać przez zmienne środowiskowe lub lokalny plik `.env`, który jest ignorowany przez Git.
- Lokalne pliki SQLite i logi powinny trafiać odpowiednio do `var/db/` i `var/logs/`; ich zawartość nie jest wersjonowana.

## Status

Etap 0: utworzono strukturę repozytorium. Implementacja funkcjonalności WERKcrew AI nie została rozpoczęta.
