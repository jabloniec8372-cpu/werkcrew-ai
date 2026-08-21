# Model operacyjny i agent

## Główna rola agenta

**USTALONE:** agent AI ma interpretować informacje i polecenia oraz koordynować użycie narzędzi systemu. Ma wspierać planowanie, wycenę i realizację prac terenowych, ale nie zastępuje deterministycznych modułów biznesowych.

Docelowo agent ma łączyć kontekst zlecenia z danymi o pracownikach, dostępności, kompetencjach, transporcie, narzędziach, materiałach i przebiegu pracy. Jego rolą jest prowadzenie użytkownika przez proces i przygotowywanie informacji do dalszych kroków. Szczegółowy katalog narzędzi agenta pozostaje **OPEN**.

Agent może w warstwie językowej:

- porządkować nieustrukturyzowany opis zlecenia,
- wykrywać braki i formułować pytania uzupełniające,
- objaśniać wyniki zwrócone przez moduły deterministyczne,
- zestawiać warianty planu bez samodzielnego liczenia ich kosztu,
- przygotowywać robocze komunikaty i podsumowania,
- koordynować przejścia między etapami procesu zgodnie z zatwierdzonymi regułami.

To, które z tych działań agent będzie mógł wykonywać bez potwierdzenia człowieka, jest **OPEN**.

## Granice autonomii

**USTALONE:** obliczenia wymagające powtarzalnego wyniku — zwłaszcza planowanie i wycena — należą do deterministycznych modułów Pythona. Model językowy nie może być źródłem prawdy dla kwot, VAT, marży, kosztów, dostępności ani zgodności planu z ograniczeniami zasobów.

Następujące granice wymagają decyzji i mają status **OPEN**:

- które działania agent tylko rekomenduje, a które może zatwierdzić,
- czy agent może wysłać ofertę lub wiadomość do klienta,
- czy agent może rezerwować pracowników, pojazdy, narzędzia lub terminy,
- czy agent może zainicjować albo zaakceptować Nachtrag,
- progi kwotowe i ryzyka wymagające eskalacji,
- sposób wycofania decyzji i ślad audytowy działań agenta.

Do czasu rozstrzygnięcia tych punktów bezpiecznym opisem produktu jest „system wspierający decyzje”, a nie „system samodzielnie zarządzający firmą”.

## Rola właściciela firmy

**OPEN:** nie ustalono jeszcze pełnej listy decyzji zastrzeżonych dla właściciela firmy ani punktów obowiązkowej akceptacji.

Właściciel jest przewidziany jako człowiek odpowiedzialny za firmę i ostateczne skutki decyzji. Dokumentacja nie przesądza jednak jeszcze, czy zatwierdza każdy plan i każdą wycenę, wyłącznie wyjątki, czy tylko działania przekraczające określone progi. Do ustalenia pozostają również:

- zakres widoczności i możliwość ręcznej korekty rekomendacji,
- zatwierdzanie Planów A/B,
- zatwierdzanie ceny, marży, rabatu i warunków handlowych,
- rozstrzyganie konfliktów dostępności i kompetencji,
- zatwierdzanie prac dodatkowych i Nachtrag,
- odpowiedzialność za zamknięcie i rozliczenie zlecenia.

## Peter i oględziny

**USTALONE:** Peter jest osobą, której rola ma być uwzględniona w procesie oględzin.

**OPEN:** nie ustalono, czy Peter wykonuje oględziny, zatwierdza ich potrzebę, weryfikuje wynik, czy pełni inną funkcję. Nie ustalono również jego uprawnień ani zastępstwa na czas niedostępności.

Przed implementacją trzeba określić:

- kryteria kierowania zlecenia na oględziny,
- kto może zlecić i odwołać oględziny,
- wymagany zakres protokołu, zdjęć, pomiarów i uwag,
- wpływ oględzin na Plan A/B oraz wycenę,
- kto akceptuje wynik oględzin,
- co dzieje się, gdy oględziny są niemożliwe lub niepełne.
