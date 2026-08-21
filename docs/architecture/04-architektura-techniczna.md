# Planowana architektura techniczna

## Stos technologiczny

**USTALONE jako planowany kierunek:**

- **Python** — język aplikacji oraz deterministycznych modułów biznesowych,
- **Strands Agents SDK** — warstwa budowy i orkiestracji agenta,
- **Claude Sonnet przez Amazon Bedrock** — planowany model językowy i sposób dostępu do niego,
- **FastAPI** — warstwa HTTP i API,
- **Jinja2 + HTMX** — serwerowo renderowany interfejs webowy z lekką interaktywnością,
- **SQLite** — lokalna baza danych dla MVP,
- **FileSessionManager** — planowany mechanizm trwałości sesji agenta.

Konkretne wersje bibliotek, wariant Claude Sonnet, region i konfiguracja Amazon Bedrock, sposób uwierzytelniania, schemat bazy oraz strategia migracji pozostają **OPEN**. Żadne dane dostępowe nie mogą być zapisywane w repozytorium.

## Podział odpowiedzialności

### Model językowy

Model językowy odpowiada za zadania wymagające rozumienia i generowania języka:

- interpretację nieustrukturyzowanego opisu,
- wydobywanie kandydatów na dane wejściowe do dalszej walidacji,
- wskazywanie brakujących informacji,
- prowadzenie rozmowy i objaśnianie wyników,
- koordynowanie wywołań zatwierdzonych narzędzi,
- tworzenie roboczych tekstów na podstawie danych systemowych.

Wynik modelu jest propozycją lub warstwą prezentacji, dopóki nie zostanie zwalidowany przez regułę, użytkownika albo system będący źródłem prawdy.

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

Te same zwalidowane dane i ta sama wersja reguł muszą dawać ten sam wynik.

### Człowiek

Człowiek odpowiada za decyzje zastrzeżone, wyjątki i skutki biznesowe. Dokładny podział między właściciela, Petera, pracowników terenowych i ewentualne inne role jest **OPEN**.

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

SQLite oraz pliki sesji są planowane dla lokalnego MVP. **OPEN:** relacja między stanem biznesowym zlecenia a stanem rozmowy agenta, retencja, współbieżność, kopie zapasowe, migracje, szyfrowanie i sposób usuwania danych.

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
