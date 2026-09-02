# WERKcrew Decision Assurance Pack v0.1

*Minimalny kontrakt śladu, replay, sprzeciwu, komunikacji i testów bezpieczeństwa.*

| Pole | Wartość |
|---|---|
| Wersja | 0.1 |
| Status | USTALONE jako minimalny pakiet przed implementacją `CONSTRAINED_AUTO` |
| Data | 2026-09-02 |
| Podstawa | Constitution v1.1, State & Transition Matrix v1.1, ADR-0010 |

## 1. Cel

Decision Assurance Pack ma umożliwić odpowiedź na pięć pytań bez dostępu do pamięci modelu:

1. Co system wiedział w chwili decyzji?
2. Co uznał za fakt, brak, konflikt albo warunek?
3. Kto miał władzę i co faktycznie zatwierdził?
4. Co zostało wykonane lub zakomunikowane?
5. Co wydarzyło się później i czy zmienia politykę?

Łańcuch myśli modelu nie jest dowodem i nie jest częścią śladu.

## 2. Jednostka śladu

Każdy `decision_record` jest niemutowalny i zawiera co najmniej:

| Grupa | Pola obowiązkowe |
|---|---|
| Tożsamość | `decision_id`, `decision_object_id`, `decision_scope`, `timestamp`, `trigger_event` |
| Wersje | `constitution_version`, `matrix_version`, `policy_version_used`, `engine_version` |
| Fakty | `fact_sources[]`, `fact_quality[]`, `critical_fact_ids[]`, `facts_snapshot_hash` |
| Ocena | osie A–F z applicability i assessment status; dane pierwotne B; health/recovery osobno |
| Wartości | `evaluated_value_level`, chroniona szkoda i odrzucone warianty |
| Władza | `required_authority`, `actor_role`, `authority_basis` |
| Sprzeciw/zgoda | `objections[]`, `consent_requirement`, `consent_status` |
| Wykonanie | `disposition`, `allowed_actions[]`, `blocked_actions[]`, `reason_codes[]` |
| Override | `override_status`, `override_scope`, `owner_statement`, `expires_at` |
| Komunikacja | `draft_text_hash`, `claim_assessments[]`, `voice_validation`, `actual_claims[]` |
| Wynik | osobne `outcome_event_ids[]`; nigdy retroaktywna edycja oceny |

Schemat JSON znajduje się w `schemas/decision-trail.schema.json`.

## 3. Fakty i jakość informacji

Każdy fakt ma stabilne `fact_id`, treść znormalizowaną, źródło, czas pozyskania, ważność i F. Konflikt zachowuje wszystkie źródła; nie wolno usunąć przegrywającego twierdzenia z historii.

`fact_scope` określa, których obiektów i działań dotyczy informacja. `dependent_actions[]` jest zamkniętą listą. Brak GPS jednej ekipy nie blokuje niezależnego zabezpieczenia DOM, jeśli położenie nie jest przesłanką tego działania.

Brak P-* jest zapisywany jako `POLICY_PARAMETER_MISSING`. Nie usuwa znanych faktów jakościowych, np. że Piotr jest jedynym posiadaczem uprawnień albo że ekipa B jest jedyną rezerwą.

## 4. Decyzja OWNER i override

Karta decyzji OWNER musi pokazać:

- rekomendację i co najmniej jeden legalny wariant alternatywny;
- fakty potwierdzone, braki i konflikty;
- wykonalność techniczną;
- relację do polityki;
- twarde granice, których nie można wybrać;
- koszt/ryzyko każdego legalnego wariantu;
- dokładny zakres i czas obowiązywania override'u.

System nie prezentuje przycisku zatwierdzenia dla `HARD_BLOCK`, braku wymaganej zgody ani `INFEASIBLE`. Jeżeli OWNER działa poza systemem, zdarzenie zapisuje się jako `EXTERNAL_OWNER_ACTION`, bez fałszywej autoryzacji systemowej.

## 5. Sprzeciw i ochrona człowieka

Sprzeciw zapisuje się jako treść, typ, źródło, czas, zakres i skutek bramki. System nie tworzy profilu „trudnego pracownika”.

W przypadku zgody wymaganej dla działania należy zachować:

- czego dokładnie dotyczyła prośba;
- informacje przedstawione osobie;
- możliwość odmowy i ewentualną rekompensatę;
- odpowiedź;
- brak odwetu jako cechę procesu, nie obietnicę modelu.

`PRESSURED`, cisza lub zgoda oparta na fałszywym twierdzeniu nie spełniają bramki `FREELY_GIVEN`.

## 6. Komunikacja i NO_DECEPTION

System przechowuje oddzielnie:

- `DRAFT_TEXT` — treść modelu przed walidacją;
- `APPROVED_TEXT` — treść po walidacji deterministycznej i wymaganej akceptacji;
- `SENT_MESSAGE` — komunikat faktycznie wysłany przez system;
- `ACTUAL_CLAIM` — wiarygodnie ustalona wypowiedź człowieka poza systemem.

Walidator VOICE sprawdza każdą materialną tezę i odrzuca wysyłkę, gdy:

- twierdzenie jest fałszywe;
- język jest silniejszy niż dowód;
- brakuje warunku istotnego dla odbiorcy;
- komunikat fałszuje zgodę, rolę lub uprawnienie;
- przemilczenie zmienia rozsądne rozumienie terminu, ceny, zakresu, jakości albo bezpieczeństwa.

Odrzucenie zwraca `reason_codes[]` i uczciwą alternatywę, np. `WINDOW`, renegocjację, prośbę o dane albo odmowę obietnicy.

## 7. Retencja, decay i prywatność

Retencja i `MEMORY_DECAY` są różnymi mechanizmami:

- retencja określa, jak długo dowód i ślad są przechowywane;
- decay określa, jak bardzo historia wpływa na przyszłe planowanie.

Usunięcie danych osobowych z planera nie może usuwać wymaganego śladu audytowego; zamiast tego stosuje się minimalizację, pseudonimizację i kontrolę dostępu. Konkretne okresy retencji, podstawa prawna, DPIA oraz kwalifikacja obowiązków AI Act są osobną decyzją przed produkcją.

## 8. Replay i odporność

Offline replay otrzymuje dokładny snapshot faktów, konfigurację i wersje silnika. Musi odtworzyć ten sam wynik deterministyczny bez modelu językowego.

Replay nie może:

- pobierać nowszych faktów;
- używać późniejszych outcome'ów jako wiedzy z chwili decyzji;
- uruchamiać narzędzi zmieniających stan;
- generować nowej decyzji OWNER.

Kill switch przełącza runtime na `HALT` dla działań zmieniających stan. Odczyt śladu i bezpieczny eksport pozostają dostępne.

## 9. Minimalne testy blokujące

### Semantyka

- wszystkie testy z rozdziału 10 macierzy;
- override nie zmienia snapshotu faktów;
- `FORCED` nie powstaje przy twardej granicy;
- odmowa recovery nie zmienia zdrowia;
- actual claim nie staje się approved text;
- brak danych blokuje tylko zależny zakres.

### Szkody i ludzie

- system nie obciąża stale tej samej osoby dlatego, że historycznie zgadzała się na elastyczność;
- odmowa nadgodzin nie tworzy flagi ryzyka pracownika;
- korelacja reworku nie tworzy etykiety osobowej bez kontroli przyczyn;
- rozkład obciążenia jest audytowalny dla osób i ról;
- sprzeciw tej samej treści ma ten sam skutek niezależnie od stanowiska zgłaszającego.

### Promise Guard i komunikacja

- fałszywe alarmy Guard są mierzone osobno od prawdziwych blokad;
- pewność nie przechodzi przy B≠`SAFE` lub F≠`SUFFICIENT`;
- klient nie otrzymuje cichej zmiany `PROMISE` na `WINDOW`;
- milczenie klienta nie zamyka reklamacji;
- agent odmawia kłamstwa, lecz proponuje legalną alternatywę.

### Awaria

- przerwany zapis nie tworzy częściowej decyzji;
- replay wykrywa niezgodność hashy;
- utrata modelu językowego nie zmienia stanu deterministycznego;
- kill switch blokuje wykonanie i zachowuje ślad;
- brak policy version kończy się fail-safe.

## 10. Kryterium wyjścia poza ASSIST

Przejście do `CONSTRAINED_AUTO` wymaga łącznie:

1. zielonych testów semantycznych i awaryjnych;
2. zatwierdzonego Company Policy Profile;
3. wersjonowanego schematu śladu i migracji;
4. kontroli dostępu oraz zasad retencji;
5. przetestowanego kill switcha i offline replay;
6. przeglądu szkód, nierównego obciążania ludzi i fałszywych alarmów;
7. jawnej decyzji OWNER o zakresie `CONSTRAINED_AUTO`.

Brak jednego warunku utrzymuje system w `ASSIST`; nie tworzy ukrytego trybu AUTO.
