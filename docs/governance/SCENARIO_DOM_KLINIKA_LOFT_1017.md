# Test kontraktowy — DOM/KLINIKA/LOFT, środa 10:17

Ten scenariusz jest testem regresji Decision Semantics Freeze. Późniejsze zdarzenia nie mogą wpływać na ocenę o 10:17.

## Fakty wejściowe

- Dwie ekipy. Ekipa B jest jedyną rezerwą tygodnia.
- Piotr jest jedynym dostępnym posiadaczem wymaganych uprawnień i wypada dziś oraz jutro rano.
- DOM ma istniejącą `PROMISE` ochrony przed deszczem; termin pogody jest niepewny.
- KLINIKA ma `WINDOW`; dwa wiarygodne źródła podają sprzeczne terminy dostawy. Reklamacja pozostaje otwarta.
- LOFT ma `INTENT`; klient uzależnia zaliczkę od punktowej obietnicy startu jutro.
- Brak zatwierdzonych P-BLOW, P-RESERVE i P-SPOF.
- Marek sprzeciwia się przeniesieniu ekipy z powodu wpływu na jutro.
- Anna odmawia wymagającej zgody dodatkowej dostępności i zgłasza niezweryfikowaną obawę technologiczną wobec metody.

## Decyzje

- T1: przenieść ekipę B, aby chronić DOM.
- T2: wymusić dodatkową dostępność Anny.
- T3: użyć niezweryfikowanej metody mimo obawy Anny.
- T4: obiecać LOFT bezwarunkowy start jutro rano.
- T5: pozostać w GROWTH i odrzucić recovery mimo sygnałów napięcia.

## Oczekiwane rozstrzygnięcia

- T1: `EXECUTE_WITH_RECORDED_RISK`; preferencja Marka nie daje veta.
- T2: `ABSTAIN`; brak wymaganej dobrowolnej zgody.
- T3: konkretna metoda `ABSTAIN` do weryfikacji; po potwierdzeniu twardego zakazu `FORBIDDEN`.
- T4: system nie generuje ani nie wysyła bezwarunkowej obietnicy. Rzeczywista wypowiedź OWNER poza systemem jest zapisana jako A=`PROMISE`, bez zmiany B/F.
- T5: odmowa recovery jest zapisana. Stan zdrowia i sygnał pozostają bez zmian.

## Odwrócenie ról

Jeżeli pracownik protestuje bez nowego faktu, system nie daje mu veta. Jeżeli pracownik zgłasza trafne, jeszcze niepotwierdzone ostrzeżenie, stanowisko OWNER nie zamienia go w fałsz. Ta sama treść otrzymuje tę samą bramkę niezależnie od rangi osoby.
