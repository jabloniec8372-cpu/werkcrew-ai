# 0002 — Granica modelu językowego i reguł deterministycznych

- **Status:** USTALONE
- **Zakres:** odpowiedzialność za rozumowanie językowe i wynik biznesowy

## Kontekst

Agent ma pomagać użytkownikowi w złożonym procesie, ale plan, dostępność zasobów i wycena muszą być powtarzalne, testowalne i możliwe do audytu.

## Decyzja

Model językowy odpowiada za interpretację języka, wykrywanie braków, objaśnianie wyników, przygotowywanie roboczych treści i koordynację zatwierdzonych narzędzi.

Deterministyczny kod Pythona odpowiada za walidację ograniczeń, planowanie, dostępność, kompetencje, transport, narzędzia oraz obliczenia materiałów, kosztów, VAT i marży. Te same dane wejściowe i ta sama wersja reguł mają dawać ten sam wynik.

Człowiek pozostaje źródłem decyzji zastrzeżonych i wyjątków. Dokładne bramki akceptacji są **OPEN**.

## Konsekwencje

- Kwoty i wykonalność planu nie mogą pochodzić wyłącznie z odpowiedzi modelu.
- Dane wyodrębnione przez model muszą zostać zwalidowane przed użyciem w kalkulacji.
- Moduły planowania i wyceny muszą być możliwe do testowania bez modelu i bez zewnętrznego API.
- Agent powinien otrzymywać wyniki obliczeń przez jawne narzędzia, a nie odtwarzać formuły w promptach.
- Wersje reguł i dane wejściowe kalkulacji powinny umożliwiać późniejsze wyjaśnienie wyniku.
