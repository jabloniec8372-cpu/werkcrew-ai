# M2 Contract Tests Queue

## Testy zachowania — Given / When / Then

Konwencja: **Given** = stan świata i polityki. **When** = intent z MobileWC albo cisza albo sync. **Then** = jedyny legalny skutek. Jeśli implementacja robi coś innego, kontrakt jest złamany.

### S1. Cisza nie jest chorobą

**Given** plan dnia Marka, start\_at = 07:30, brak jakiegokolwiek eventu

**When** jest 08:20, Marek milczy

**Then**

- nie istnieje UNAVAILABLE\_TODAY\_REPORTED
- nie istnieje absencja
- istnieje najwyżej START\_UNKNOWN\_ESCALATED
- plan nie został aktywowany

### S2. Reminder nie zgaduje przyczyny

**Given** jak S1, minął start\_at

**When** system wysyła R1

**Then** Marek dostaje tylko: *Tak / Spóźnię się / Problem ze startem / Dzisiaj nie pracuję*

**And** żadna z odpowiedzi nie jest wstępnie zaznaczona

### S3. ON MY WAY aktywuje plan, nie lokalizację

**Given** plan dnia Ani w stanie issued

**When** Ania tapnie ON MY WAY -> DAY\_PLAN\_ACTIVATED

**Then**

- plan\_day.status = active
- brak pola lokalizacji wymaganego do skutku
- reminder startu zdjęty
  **And** agent nie twierdzi „Ania jest u klienta”

### S4. Transport blocked ≠ unavailable

**Given** plan dnia Piotra aktywny albo jeszcze nie

**When** START\_EXCEPTION\_REPORTED(reason=TRANSPORT\_BLOCKED) + głos „kolega nie przyjechał, mogę później autobusem”

**Then**

- nie powstaje UNAVAILABLE\_TODAY
- agent może szukać transportu / przesunąć start
- Piotr pozostaje zasobem dnia, dopóki sam nie powie, że nie pracuje

### S5. Spóźnienie ma osobny event

**Given** plan Oli, start\_at = 08:00

**When** START\_DELAY\_REPORTED(reason=TRAFFIC, eta≈08:40)

**Then**

- event type ≠ START\_EXCEPTION\_REPORTED
- brak absencji
- agent liczy wpływ na okno klienta
- Ola nadal realizuje ten plan

### S6. Unavailable tylko z jawnego gestu

**Given** cisza albo TRANSPORT\_BLOCKED

**When** brak UNAVAILABLE\_TODAY\_REPORTED

**Then** nikt nie zdejmuje workera z dnia jako „nie pracuje”

**When** pada UNAVAILABLE\_TODAY\_REPORTED(SICK)

**Then** dopiero wtedy assignmenty dnia są zdejmowane według polityki

### S7. Retry tego samego `event_id` = ten sam skutek

**Given** telefon był offline; worker tapnął ON MY WAY jeden raz; lokalny `DAY_PLAN_ACTIVATED` został już poprawnie przetworzony przez backend

**When** klient ponawia wysyłkę **tego samego eventu** z identycznym `event_id` (retry/sync po niepewnej odpowiedzi)

**Then**

- backend nie tworzy drugiego eventu ani drugiego skutku biznesowego
- nie powstaje nowy `server_event_id` / nowa tożsamość skutku
- odpowiedź jest semantycznie tym samym wynikiem pierwszego przetworzenia
- stan `plan_day` pozostaje `active` bez ponownego wykonania logiki

### S8. Dwa różne event\_id tego samego intentu

**Given** plan już active po pierwszym DAY\_PLAN\_ACTIVATED

**When** drugi, nowy event\_id z tym samym typem tego samego dnia

**Then** stan się nie dubluje; drugi event zapisany, skutek = no-op

### S9. Crew bez leada nic nie zamyka

**Given** CREW\_ASSIGNMENT bez lead\_worker\_id

**When** ktokolwiek emituje STAGE\_COMPLETION\_REPORTED

**Then**

- ASSIGNMENT\_INVALID
- COMPLETION\_REJECTED albo równoważny fail-closed
- stage/task nie jest DONE

### S10. Lead zamyka, reszta nie

**Given** ekipa Peter=lead, Marek, Ola

**When** Peter -> STAGE\_COMPLETION\_REPORTED i postcondition OK

**Then** stage zamknięty

**When** zamiast tego Marek emituje completion

**Then** nic nie zamknięte; event odrzucony uprawnieniem

### S11. Spór ekipy

**Given** Peter zamknął stage

**When** Ola -> COMPLETION\_DISPUTED

**Then** stage nie zostaje milcząco DONE; stan disputed; karta człowieka

**And** agent nie wybiera, kto kłamie

### S12. Completion bez evidence

**Given** wizyta wymaga zdjęcia albo głosu

**When** VISIT\_COMPLETION\_REPORTED bez evidence

**Then**

- event istnieje (reported)
- visit **nie** jest DONE
- MobileWC żąda jednego gestu evidence, nie formularza

### S13. STAGE nie zamyka joba

**Given** płytki 3 dni, stage = dzień 1

**When** STAGE\_COMPLETION\_REPORTED OK

**Then** stage zamknięty, job żywy, worker może podlegać polityce końca dnia / early finish

### S14. WAITING uwalnia człowieka, nie task

**Given** hydroizolacja

**When** TECHNICAL\_WAIT\_REPORTED(wait\_until=jutro)

**Then** worker wolny, task waiting, to nie failure, nie karny job

### S15. ACTION bez ACK = stary potwierdzony plan

**Given** Marek zACK-ował plan: Job A, auto 1

**When** agent wysyła ACTION\_REQUIRED: jedź do Job B

**And** brak WORKER\_ACKNOWLEDGED

**Then**

- evidence ≠ ACKED
- nie wolno twierdzić worker\_informed
- obowiązujący plan Marka = Job A
- po E1: ACK\_MISSING\_ESCALATED

### S16. STOP bez ACK = UNCONFIRMED, nie sukces

**Given** STOP\_DIRECTIVE: nie ruszaj instalacji

**When** push przyjęty przez kanał, brak ACK

**Then**

- delivery\_evidence najwyżej CHANNEL\_ACCEPTED
- stan firmy: STOP\_UNCONFIRMED
- nikt nie zapisuje „Marek poinformowany”

### S17. STOP + exception = SAFE HOLD

**Given** task „podłącz rozdzielnicę” był planem; padł STOP\_DIRECTIVE

**When** WORKER\_ACTION\_EXCEPTION(directive=STOP, reason=CONFLICTING\_FACT)

**Then**

- STOP **nadal obowiązuje**
- task -> SAFE\_HOLD / BLOCKED\_PENDING\_RESOLUTION
- **nie** wraca uprawnienie ruszać instalację
- M4 resolvuje, nie worker

### S18. ACTION + exception nie wraca w niebezpieczny stary krok

**Given** zACK-owany plan: dojedź Caddy na Job B

**When** ACTION\_REQUIRED: weź Caddy, jedź do Job C

**And** WORKER\_ACTION\_EXCEPTION(VEHICLE\_UNSAFE)

**Then**

- Caddy nie jest używany
- Marek nie jest zmuszony jechać do Job C tym autem
- Marek nie jest zmuszony udawać, że wykonuje akcję
- obowiązuje ostatni **bezpieczny** potwierdzony stan + replan M4

### S19. Exception nie jest buntem

**Given** S17 albo S18

**When** wyjątek zapisany

**Then** brak worker score, brak kary, event jest nowym faktem

### S20. Zdjęcie problemu to evidence

**Given** glazurnik zgłasza mokro pod wanną + 2 zdjęcia

**When** SITE\_PROBLEM\_REPORTED

**Then**

- oryginał zdjęć i aktora (skill=tiler) zostaje
- brak werdyktu „instalacja nieszczelna”
- agent może wstrzymać front / wezwać specjalistę / dać STOP według polityki
- nie zamyka sprawy jako ekspertyza

### S21. Early finish bez karnego joba

**Given** etap dnia skończony 13:40, polityka early finish = on, najbliższy obcy job 40 km

**When** system liczy opcje

**Then**

- brak auto-assign „GO NOW”
- opcje tylko z drzewa polityki i kagańca (dojazd/czas, zero nowej obietnicy)
- „kończę” jest legalne i kończy dostępność według H, nie według chęci agenta

### S22. Intencja końca dnia ≠ DAY\_CLOSED

**Given** auto firmowe musi nocować w bazie

**When** DAY\_CLOSE\_REPORTED

**Then**

- nie ma jeszcze DAY\_CLOSED
- wypada END\_OF\_DAY\_OPTIONS / ACTION RETURN\_BASE **When** polityka spełniona (powrót zgłoszony albo owner zamyka wyjątek)
  **Then** dopiero DAY\_CLOSED i zdjęta dostępność

### S23. Koniec dnia bez auta

**Given** polityka END\_ON\_SITE, brak obowiązkowego zwrotu

**When** DAY\_CLOSE\_REPORTED

**Then** wolno przejść do DAY\_CLOSED bez nowej pracy

### S24. Scope fact nie jest obietnicą

**Given** klient pyta o malowanie kuchni

**When** SCOPE\_FACT\_REPORTED

**Then**

- powstaje non-binding LEAD albo CHANGE\_REQUEST
- brak ceny, braku terminu, braku wpięcia w dzisiejsze zobowiązanie

### S25. Owner w terenie nie jest świętą krową

**Given** Stefan owner jedzie na oględziny

**When** emituje DAY\_PLAN\_ACTIVATED i VISIT\_COMPLETION\_REPORTED

**Then** te same reguły co worker

**And** nie może emitować DAY\_PLAN\_ACTIVATED za Marka

### S26. Owner ASK jest inną szyną

**Given** odchylenie psuje termin klienta

**When** agent potrzebuje decyzji

**Then** HUMAN\_DECISION\_REQUESTED, nie ping „jak idzie u Müller”

**And** MobileWC workera nie dostaje KPI

### S27. sent ≠ informed

**Given** dowolna dyrektywa ACTION/STOP

**When** delivery\_evidence ∈ {QUEUED, CHANNEL\_ACCEPTED, APP\_OBSERVED}

**Then** worker\_informed = false

**When** WORKER\_ACKNOWLEDGED

**Then** worker\_informed = true wyłącznie dla tego directive\_id

### S28. STOP po powrocie sieci pierwszy

**Given** telefon offline; w kolejce INFO i STOP

**When** sieć wraca

**Then** STOP renderuje się i blokuje zanim worker zobaczy INFO

### S29. Cisza w środku dnia jest legalna

**Given** plan aktywny, nic nadzwyczajnego

**When** 6 godzin bez eventu

**Then** brak pytań „jak idzie”, brak auto-statusów, plan pozostaje aktywny

### S30. Fail-closed przy konflikcie eventów

**Given** STAGE\_COMPLETION\_REPORTED i niemal równolegle SITE\_PROBLEM\_REPORTED(UNSAFE) z tym samym stage

**Then** stage nie zostaje DONE; wygrywa wstrzymanie / dispute / SAFE\_HOLD, nie sukces


### S31. SAFE_HOLD jest trwały przez restart i nowy dzień

**Given** task jest w `SAFE_HOLD / BLOCKED_PENDING_RESOLUTION` z powodu STOP lub safety exception

**When** następuje restart procesu/backendu, cold start aplikacji/urządzenia, retry/sync albo worker następnego dnia emituje `DAY_PLAN_ACTIVATED` dla nowego `plan_day`

**Then**

- affected task nadal jest `SAFE_HOLD / BLOCKED_PENDING_RESOLUTION`
- rozpoczęcie nowego dnia nie przywraca prawa wykonania zablokowanego kroku
- rollover dnia, restart, sync i flaky network nie są exitami z blokady
- blokadę zdejmuje wyłącznie jawny, autoryzowany resolution/replan zgodny z kontraktem


---

## Minimalny zestaw, bez którego nie ma implementacji

Jeśli ktoś ma czas napisać tylko część, te 13 muszą przejść pierwsze:

1. S1 cisza ≠ chory
2. S4 transport ≠ unavailable
3. S5 delay rozłączny z exception
4. S7 retry tego samego event_id
5. S9 crew bez leada
6. S12 completion bez evidence
7. S15 ACTION bez ACK
8. S16 STOP bez ACK
9. S17 STOP + exception -> SAFE HOLD
10. S20 zdjęcie ≠ ekspertyza
11. S21 brak karnego joba
12. S22 DAY\_CLOSE\_REPORTED ≠ DAY\_CLOSED
13. S31 SAFE_HOLD przetrwa restart / nowy dzień

---

Filozofowanie o MobileWC jest skończone. Kolejny suchy krok to przepisanie tych 31 scenariuszy na testy w repo (nazwy eventów już stabilne) — JSON Schema dopiero z zielonych testów, żeby schemat nie zalegalizował dziury, której test jeszcze nie złapał.
