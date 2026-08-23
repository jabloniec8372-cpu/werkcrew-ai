# 0009 — Frozen M8.0 business configuration

- **Status:** USTALONE dla M8.0 DEMO
- **Data:** 2026-08-23

## Kontekst

Późniejsze części M8 potrzebują jednego, wersjonowanego źródła parametrów symulatora. M8.0 nie uruchamia nowego intake, planowania ani pricingu; zamraża dane wejściowe, zanim kolejne slice'y zaczną ich używać.

## Decyzja

1. Katalog zawiera dokładnie 24 jawne SKU. Jedno zamówione SKU nigdy nie tworzy innego SKU ani nie rozszerza zakresu.
2. `SkuPlanningProfile` używa `Decimal`, dokładnego SKU jako `requiredSkillKey`, poziomu 2 albo 3, jawnej klasy pojazdu i flagi site verification.
3. WERKcrew DEMO productivity profiles v1 are deterministic simulator parameters. They are not universal construction productivity standards.
4. Skala skilla to 0–3. Poziom 1 oznacza wyłącznie pomoc i w M8 nie jest schedulable. Każdy przyszły `ScheduledTask` nadal ma dokładnie jednego pracownika.
5. `predecessorIfPresent` działa tylko dla jawnych JobItems w tym samym jobie. Nie tworzy brakującego zakresu; wszystkie pasujące JobItems poprzednika blokują dependent do końca swojego ostatniego chunka.
6. Wspólny limit chunka wynosi 8 godzin. Jednostki ciągłe dzielą się godzinowo, a `PIECE` i `ROOM` wyłącznie na całe jednostki. Chunki jednego JobItem tworzą ścisły łańcuch.
7. Flota, pojazdy prywatne, rental capabilities, stawka rental 95 EUR/dzień oraz kolejności preferencji są `DEMO_SYNTHETIC`. M8.0 jedynie je koduje — nie uruchamia wyboru ani approval.
8. Canonical config jest walidowany przy imporcie. Brak, duplikat, cykl albo niespójna wartość zatrzymują start z kodowanymi problemami; validator niczego nie naprawia.

## Wersje

- `m8-service-catalog-v1`
- `m8-sku-planning-v1`
- `m8-demo-skills-v1`
- `m8-vehicle-policy-v1`
- `m8-catalog-i18n-v1`

## Granice

M8.0 nie udostępnia Jury Lab ani New Job UI, nie podłącza dynamicznego JobItem do M3, nie uruchamia `CATALOG_QUOTE_V1`, geocodingu, nowego routingu, HITL, Nachtrag, disruptions, email ani Field/Trace UI. Istniejące konfiguracje i fixture M0–M7 pozostają bez zmian.
