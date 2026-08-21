# MVP i prezentacja dla jury

## Co ma zobaczyć jury

**OPEN:** nie ustalono jeszcze zatwierdzonej zawartości prezentacji dla jury. Kierunek produktu zakłada pokazanie WERKcrew AI jako systemu wspierającego planowanie, wycenę i wykonanie pracy terenowej, z widocznym rozdziałem między warstwą językową a deterministycznymi regułami.

Na podstawie obecnego zakresu poniższe elementy tworzą listę do rozstrzygnięcia, a nie zatwierdzony scenariusz:

- rolę agenta w prowadzeniu procesu i pracy na kontekście zlecenia,
- przebieg zlecenia od danych wejściowych do realizacji terenowej,
- użycie danych o pracownikach, dostępności, kompetencjach, transporcie i narzędziach,
- Plan A i Plan B,
- przejrzystą, deterministyczną kalkulację ceny,
- WERKcrew Field w formie czterech ekranów MVP,
- obsługę zmiany zakresu i Nachtrag,
- kontrolę człowieka nad decyzjami biznesowymi.

**OPEN:** dokładny scenariusz demonstracyjny, dane demo, czas prezentacji, kolejność ekranów, poziom działania „na żywo” oraz kryteria sukcesu jury. Nie ustalono, które elementy muszą być faktycznie interaktywne, a które mogą być pokazane jako udokumentowany dalszy przepływ.

## Zakres MVP

**USTALONE jako kierunek produktu, nie jako zamknięty backlog:**

- agent oparty na Strands Agents SDK i Claude Sonnet przez Amazon Bedrock,
- aplikacja Python/FastAPI z interfejsem Jinja2/HTMX,
- lokalna trwałość przez SQLite i FileSessionManager,
- deterministyczne obszary planowania i wyceny,
- model zasobów obejmujący pracowników, dostępność, kompetencje, transport i narzędzia,
- WERKcrew Field z czterema ekranami,
- uwzględnienie oględzin, Planów A/B i Nachtrag.

**OPEN:** granica funkcjonalna każdego z tych punktów oraz ich priorytet. Powyższa lista nie przesądza, że cały docelowy proces znajdzie się w pierwszym działającym wydaniu.

## Odłożone na później

**OPEN:** nie zatwierdzono jeszcze listy funkcji odłożonych na później. W szczególności nie ma decyzji dotyczących:

- hostingu i topologii produkcyjnej,
- wielu firm lub wielu oddziałów,
- integracji z systemami księgowymi, kalendarzami, pocztą lub komunikatorami,
- płatności, fakturowania i księgowości,
- natywnych aplikacji mobilnych,
- zaawansowanej pracy offline i synchronizacji,
- optymalizacji tras oraz geolokalizacji,
- automatycznego wysyłania ofert i wiadomości,
- analityki historycznej i prognozowania,
- zarządzania magazynem i zakupami,
- wielojęzyczności oraz zaawansowanego systemu uprawnień.

Ta lista jest rejestrem nierozstrzygniętych obszarów, a nie decyzją o ich budowie.
