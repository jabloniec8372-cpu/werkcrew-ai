# 0004 — Deterministyczne warianty planowania zasobów DEMO

- **Status:** USTALONE
- **Zakres:** M3, wyłącznie syntetyczne planowanie jednego zlecenia DEMO

## Kontekst

M2 kończy się stanem `READY_FOR_PLANNING`, gdy raport oględzin uzupełnił wymagania i nie pozostawił istotnych ryzyk. M3 ma potwierdzić, że z tych danych oraz jawnej dostępności zasobów można bez LLM utworzyć wykonalne, odmienne warianty realizacji.

## Decyzja

- Zakres prac M3 jest zapisany jawnie jako dane `DEMO_SYNTHETIC`. Każde zadanie wskazuje wymaganie zlecenia, wymagane skille, orientacyjny czas, poprzedników oraz opcjonalny typ pojazdu. Planner nie wydobywa tych wartości z opisu językiem naturalnym.
- Zależności zadań tworzą acykliczny graf. Zadanie może rozpocząć się dopiero po zakończeniu wszystkich wskazanych poprzedników.
- Dostępność pracownika i pojazdu jest traktowana jako jawny przedział czasu. Całe zadanie musi mieścić się w jednym dostępnym przedziale. M3 nie dzieli zadania pomiędzy zmiany i nie dodaje nadgodzin ani weekendów.
- Do zadania może zostać przypisany wyłącznie aktywny pracownik posiadający wszystkie wymagane skille. Ten sam pracownik ani pojazd nie może obsługiwać nakładających się zadań w jednym wariancie.
- Jeżeli zadanie wymaga pojazdu, musi istnieć aktywny pojazd właściwego typu, dostępny przez cały czas zadania.
- Planner przeszukuje ograniczone okno zapisane w danych DEMO i odrzuca harmonogramy niespełniające walidatora wykonalności.
- `PLAN A` to najwyżej sklasyfikowany wariant: najwcześniejszy start, następnie najwcześniejsze zakończenie i mniejsza liczba osób. `PLAN B` jest następnym odmiennym harmonogramem według tego samego deterministycznego porządku. Jeśli drugi wariant nie istnieje, system jawnie o tym informuje i nie tworzy jego kopii.
- Decision trace zapisuje rozważenie, odrzucenie i wybór kandydatów wraz z kodem oraz czytelnym powodem. Jest to ślad demonstracyjny, a nie docelowy audyt produkcyjny.
- Powstanie co najmniej jednego poprawnego wariantu zmienia workflow na `PLANS_READY_FOR_REVIEW`. Żaden plan nie jest automatycznie zatwierdzany ani nie rezerwuje zasobów.

## Konsekwencje

- Dane czasu i kolejności w M3 są świadomym scenariuszem SYNTHETIC, nie estymacją ani pełnym modelem produkcyjnym.
- Planner nie obsługuje tras, materiałów magazynowych, narzędzi, częściowego czasu pracy, przerw, prawa pracy, kosztu, wyceny ani ręcznego nadpisania.
- Ogólna semantyka Planów A/B, model dostępności i reguły akceptacji poza zakresem tego DEMO pozostają otwarte.
