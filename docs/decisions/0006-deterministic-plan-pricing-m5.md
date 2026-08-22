# 0006 — Deterministyczna wycena wariantów planu M5

- **Status:** USTALONE dla M5 DEMO
- **Data:** 2026-08-22

## Kontekst

M3 tworzy wykonalne `PlanVariant`, a M4 pozwala agentowi Strands uruchamiać deterministyczne funkcje. M5 ma wycenić istniejące warianty bez replanningu i bez przekazywania modelowi językowemu matematyki finansowej, polityki podatkowej ani decyzji właściciela.

## Decyzja

1. Granica M3/M5 jest ścisła: M3 ustala pracowników, pojazdy, zadania, kolejność i terminy; M5 jedynie odczytuje każdy zapisany `ScheduledTask` i `PlanVariant`.
2. Wszystkie wejścia konkursowego scenariusza są jawnie sklasyfikowane jako `DEMO_SYNTHETIC`. Nie są stawkami, polityką cenową ani poradą podatkową dla prawdziwej firmy.
3. `PricingPolicy` zamraża walutę EUR, overhead 15%, risk reserve 5%, target margin 20%, `ROUND_HALF_UP` i skalę 0.01. Marża, overhead oraz risk reserve są stosowane dokładnie raz.
4. Rozłączne kategorie kosztów to: `LABOR`, `VEHICLE_TRAVEL`, `DELIVERY`, `MATERIAL`, `TOOL_RENTAL`, `DISPOSAL`, `SUBCONTRACTOR` i `PARKING_TOLL`. Scope wspólny dla joba nie tworzy nowej kategorii.
5. Materiały są wspólnym wejściem zlecenia i są naliczane atomowo jako `quantity_base × (1 + waste_rate) × purchase_net_unit_cost`. Każda linia jest zaokrąglana do 0.01 EUR przed agregacją.
6. Praca wynika z czasu zapisanego w zadaniu i snapshotu `Employee.hourly_cost_amount`. Pojazd wynika z jawnego `VehicleUsageInput.distance_km_total` i snapshotu `Vehicle.cost_per_km_amount`; M5 nie wykonuje routingu ani nie dopisuje kilometrów.
7. Wzory M5 są kolejno: direct cost; overhead od direct cost; risk reserve od direct cost plus overhead; modeled company cost; cena netto przez podzielenie przez `1 - target_margin_rate`.
8. `TaxTreatment` jest jawnie ustawianą granicą. `STANDARD_19` finalizuje wyłącznie syntetyczne VAT i gross DEMO. `MANUAL_REVIEW` oraz `REVERSE_CHARGE_13B` zachowują koszt i netto, ale nie finalizują VAT ani gross i zwracają `REVIEW_REQUIRED`.
9. `PlanPricingResult` jest immutable snapshotem stawek, materiałów, wejść logistycznych, polityki, linii kosztowych, wyników, issues oraz fingerprintu. Ten sam fingerprint jest idempotentny; zmienione wejście tworzy nowe `result_id` bez zmiany znaczenia starego wyniku.
10. Każdy wariant jest liczony niezależnie. Zweryfikowane linie wyniku `INCOMPLETE` pozostają widoczne, lecz braki nie stają się zerem i nie powstaje końcowa suma. Warianty nie są wtedy porównywalne, workflow pozostaje `PLANS_READY_FOR_REVIEW`, a agent czeka w `WAITING_FOR_PRICING_INPUT`.
11. Dopiero kompletność wszystkich istniejących wariantów otwiera `PRICING_READY_FOR_REVIEW` i `WAITING_FOR_OWNER_REVIEW`. Jeden legalny wariant wystarcza; M5 nie tworzy fikcyjnego Planu B.
12. Porównanie A/B jest deterministyczne i wskazuje różnice kosztu, netto oraz największy cost driver z category totals. Nie wybiera wariantu.
13. Agent ma dokładnie dwa nowe tools: `calculate_plan_quotes` oraz read-only `get_pricing_results`. LLM objaśnia strukturalny wynik, lecz nie liczy finansów, nie ustala podatku, nie zatwierdza ceny, nie wybiera planu i nie wysyła oferty.
14. Activity timeline przechowuje tylko publiczne akcje, tools, skróty wyników i przejścia workflow — bez prywatnego toku rozumowania.

## Ograniczenia i konsekwencje

- M3 zapisuje jednego pracownika na `ScheduledTask`; M5 deterministycznie wycenia każdy taki przydział i nie rozszerza planera do ekip wieloosobowych.
- Widoczne płytki, armatura i wyposażenie wybierane przez klienta nie mają znanej ceny w tym DEMO i nie są udawane jako wycenione.
- Poza M5 pozostają prawdziwe stawki firmy, cenniki dostawców, magazyn, routing, material markup, procurement fee, rate cards, pełna interpretacja §13b, faktury, wysyłanie ofert, approvals, plan-vs-actual i trwałe sesje.
- UI używa określenia „Szacowany koszt firmy — dane demonstracyjne” i jawnie pokazuje `DEMO_SYNTHETIC`.
