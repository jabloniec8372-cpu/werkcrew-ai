# 0005 — Ograniczona orkiestracja agenta Strands w M4

- **Status:** USTALONE dla M4 DEMO
- **Data:** 2026-08-21

## Kontekst

M1–M3.1 dostarczają deterministyczną ocenę zlecenia, pętlę oględzin i planner zasobów. M4 ma potwierdzić, że prawdziwy agent Strands z modelem Amazon Bedrock potrafi wybierać i wywoływać te funkcje, nie przejmując ich odpowiedzialności biznesowej. Pełny model autonomii, trwałych sesji i owner approval pozostaje poza M4.

## Decyzja

1. Agent używa `strands-agents==1.50.2` i providera `BedrockModel` konfigurowanego przez środowisko. Identyfikator modelu, region, profil i credentials nie mają wartości domyślnych w repozytorium.
2. Katalog narzędzi M4 jest ograniczony do:
   - `get_job_state`,
   - `assess_job`,
   - `prepare_site_visit`,
   - `get_site_visit_status`,
   - `validate_site_visit_report`,
   - `generate_crew_plans`.
3. Narzędzia są cienkimi adapterami. Ocena, brief, przydział, walidacja raportu oraz planowanie nadal wykonywane są przez istniejący kod M1–M3.
4. Agent kończy pierwszy przebieg na `WAITING_FOR_FIELD_REPORT`, a po otrzymaniu raportu i wygenerowaniu planów na `WAITING_FOR_OWNER_REVIEW`.
5. Agent nie ma narzędzia do utworzenia raportu terenowego ani zatwierdzenia planu. Raport pochodzi z WERKcrew Field, a Plan A/B pozostaje decyzją właściciela.
6. Timeline zapisuje wyłącznie publiczne zdarzenia: akcję, tool, rezultat, stan workflow, czas i krótkie uzasadnienie. Nie zapisuje prywatnego chain-of-thought modelu.
7. Stan workflow oraz timeline pozostają dla tego DEMO w pamięci jednego procesu. „Wznowienie” oznacza nowy przebieg agenta odczytujący aktualny deterministyczny stan, nie trwałą sesję konwersacji.
8. Testy używają kontrolowanego modelu implementującego abstrakcję Strands. Aplikacja uruchomieniowa zawsze buduje prawdziwy `BedrockModel`; nie ma trybu prezentacyjnego z fake AI.

## Konsekwencje

- Model wybiera narzędzia i objaśnia rezultat, lecz nie może zmienić prawdy biznesowej zwracanej przez walidatory i planner.
- Brak konfiguracji lub błąd Bedrock jest widoczny jako `ERROR`; system nie symuluje sukcesu.
- M4 nie rozstrzyga docelowego katalogu narzędzi, pełnej polityki autonomii, trwałości sesji ani właścicielskiego approve/reject.
- Faktyczne użycie konkretnego modelu/inference profile wymaga wartości potwierdzonej dla danego konta AWS; nie jest zaszyte w kodzie.
