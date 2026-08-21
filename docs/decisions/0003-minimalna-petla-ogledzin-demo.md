# 0003 — Minimalna pętla oględzin terenowych DEMO

- **Status:** USTALONE
- **Zakres:** M2, wyłącznie demonstracyjny workflow oględzin przed planowaniem

## Kontekst

M1 zwraca deterministyczną decyzję `SITE_VISIT_REQUIRED` dla niekompletnego lub ryzykownego zlecenia. M2 potrzebuje działającej pętli, która zamienia ten wynik w zadanie terenowe, zbiera raport i ponownie waliduje dane bez użycia modelu językowego.

## Decyzja

W zakresie M2 obowiązuje następujący minimalny workflow:

```text
SITE_VISIT_REQUIRED
→ SITE_VISIT_SCHEDULED
→ SITE_VISIT_COMPLETED
→ READY_FOR_PLANNING
```

- Brief oględzin powstaje deterministycznie z danych `JobRequest` oraz braków i ryzyk zwróconych przez `job-assessment-v1`.
- Osoba wykonująca oględziny jest wybierana na podstawie aktywności, skillu `site-assessment` oraz dostępnego przedziału kończącego się przed planowaną datą rozpoczęcia zlecenia. Przy wielu kandydatach wybierany jest deterministycznie najwcześniejszy termin, a następnie identyfikator pracownika.
- W obecnych danych syntetycznych warunki spełnia Peter DEMO. Imię nie jest kryterium dopasowania.
- Minimalny raport zawiera wymiary i ilości, stan podłoża, ustalenia dotyczące wilgoci, warunki dostępu, wynik oględzin instalacji, notatkę oraz informację o nierozwiązanym ryzyku.
- Raport aktualizuje istniejący `JobRequest`: potwierdzone pomiary uzupełniają odpowiadające im wymagania. Nie powstaje niezależne zlecenie zastępcze.
- `READY_FOR_PLANNING` jest dostępne wyłącznie wtedy, gdy raport jest kompletny, wszystkie wymagane pomiary zostały przekazane, ponowna ocena nie wykrywa braków i nie pozostało nierozwiązane ryzyko.
- Interaktywny stan DEMO jest przechowywany wyłącznie w pamięci jednego procesu i resetuje się po restarcie aplikacji.

## Konsekwencje

- Peter DEMO pełni rolę wykonawcy oględzin tylko w tym scenariuszu M2. Docelowe role, uprawnienia, zastępstwa i bramki akceptacji pozostają otwarte.
- Dostępny przedział oznacza w M2 możliwość zaplanowania oględzin w jego czasie; długość wizyty i konflikty z innymi typami rezerwacji nie są jeszcze modelowane.
- Widok mobilny M2 jest jednym funkcjonalnym wycinkiem WERKcrew Field. Nie ustala nazw ani zawartości docelowych czterech ekranów MVP.
- M2 nie rozstrzyga obsługi zdjęć, podpisów, offline, synchronizacji, odwołania wizyty ani ręcznej zmiany przydziału.
- Stan `READY_FOR_PLANNING` nie tworzy Planu A/B, wyceny ani rezerwacji zasobów.
