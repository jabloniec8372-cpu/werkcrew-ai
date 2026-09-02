# Governance i Decision Assurance

Ten katalog zawiera wersjonowane kontrakty semantyczne WERKcrew. Markdown jest źródłem do przeglądu zmian w Git. Pliki DOCX i XLSX w `releases/` są publikacyjnymi projekcjami tych samych reguł.

## Hierarchia

1. `WERKcrew_Constitution_v1.1.md` — prawo nadrzędne i granice systemu.
2. `WERKcrew_State_Transition_Matrix_v1.1.md` — stany, bramki i przejścia.
3. `WERKcrew_Decision_Assurance_Pack_v0.1.md` — ślad, replay, prywatność i testy.
4. Company Policy Profile — przyszłe wartości P-* zatwierdzane przez OWNER.

Kontrakty v0.1 ODC, OIC i Operating Envelope pozostają materiałem źródłowym. Constitution rozstrzyga ich kolizje terminologiczne. Liczby przykładowe nie stają się polityką bez jawnej wartości w Company Policy Profile.

## Release v1.1

Katalog `releases/v1.1/` zawiera publikacyjne DOCX Constitution, State & Transition Matrix i Decision Assurance Pack oraz publikacyjny XLSX macierzy. Artefakty binarne są projekcjami; w razie rozbieżności wiążą odpowiednie pliki Markdown.

## Freeze

Decyzję o rozdzieleniu prawdy, władzy i wykonania zapisuje ADR [`0010`](../decisions/0010-decision-semantics-freeze.md). Implementacja nie może doprecyzować reguły inaczej niż kontrakty i testy blokujące.
