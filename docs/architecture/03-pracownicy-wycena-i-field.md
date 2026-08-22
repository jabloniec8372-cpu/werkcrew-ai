# Pracownicy, wycena i WERKcrew Field

## Profile pracowników i zasobów

**USTALONE:** planowanie ma uwzględniać profile pracowników, ich dostępność, kompetencje, transport i narzędzia.

Minimalne grupy informacji do zaprojektowania:

- **profil pracownika** — identyfikacja operacyjna i status aktywności,
- **dostępność** — przedziały czasu, nieobecności i istniejące rezerwacje,
- **kompetencje** — kwalifikacje potrzebne do wykonania określonych prac,
- **transport** — możliwość dojazdu oraz dostępność wymaganych pojazdów,
- **narzędzia** — wymagane i dostępne wyposażenie, w tym konflikty rezerwacji.

**OPEN:** szczegółowy schemat danych, poziomy kompetencji, ważność uprawnień, jednostka planowania czasu, lokalizacja startowa, pojemność pojazdów, współdzielenie narzędzi oraz źródło prawdy dla dostępności.

Deterministyczny moduł planowania powinien docelowo otrzymywać zwalidowane dane i jawne ograniczenia. Model językowy może objaśnić wynik lub wskazać brak danych, lecz nie może uznać niedostępnej osoby, pojazdu albo narzędzia za dostępne.

## Zasady wyceny

**USTALONE:** wycena ma być deterministyczna i uwzględniać co najmniej materiały, koszty, VAT oraz marżę. Model językowy nie oblicza ani nie koryguje samodzielnie wartości finansowych.

Obszary wymagające jawnych reguł:

- robocizna i sposób rozliczania czasu,
- materiały: ilości, ceny źródłowe, odpady, narzut i zaokrąglenia,
- transport, dojazd, narzędzia, podwykonawcy i pozostałe koszty,
- koszty bezpośrednie i pośrednie,
- podstawa oraz sposób naliczania marży,
- stawki VAT i zasady wyboru właściwej stawki,
- rabaty, minimalna wartość zlecenia i reguły zaokrągleń,
- wersjonowanie cenników oraz możliwość odtworzenia kalkulacji.

**USTALONE wyłącznie dla M5 DEMO:** zamrożone syntetyczne stawki, wzory, kolejność naliczania, waluta, definicja target margin, rounding i tax boundary opisuje ADR [0006](../decisions/0006-deterministic-plan-pricing-m5.md). Reguły prawdziwej firmy, rabaty, minima, progi i uprawnienia do korekty pozostają **OPEN**.

Każda przyszła wycena powinna przechowywać dane wejściowe, wersję reguł oraz rozbicie wyniku, aby można ją było powtórzyć i wyjaśnić bez udziału modelu językowego.

## Praca wieczorna i weekendowa

**USTALONE:** planowanie i wycena muszą uwzględniać zasady pracy wieczornej oraz weekendowej.

**OPEN:** godziny definiujące pracę wieczorną, definicja weekendu i święta, dopuszczalność takich prac, wymagane zgody, limity czasu pracy, dodatki kosztowe i cenowe, minimalny czas naliczenia oraz pierwszeństwo reguł przy nakładaniu się okresów.

Nie należy kodować domyślnych mnożników ani przedziałów godzinowych przed zatwierdzeniem tych zasad.

## WERKcrew Field

**USTALONE:** WERKcrew Field jest obszarem produktu przeznaczonym do obsługi pracy terenowej. MVP ma składać się z czterech ekranów.

**OPEN:** nie ustalono nazw, dokładnego celu ani zawartości żadnego z czterech ekranów. Do czasu podjęcia decyzji są one zapisywane wyłącznie jako:

1. **Ekran 1 — OPEN**
2. **Ekran 2 — OPEN**
3. **Ekran 3 — OPEN**
4. **Ekran 4 — OPEN**

Przed implementacją każdego ekranu trzeba określić użytkownika, cel, dane wejściowe, możliwe działania, dane wyjściowe, zachowanie offline, uprawnienia i stany błędów.

## Field a Nachtrag

**USTALONE:** dodatkowy zakres prac i Nachtrag muszą być uwzględnione w doświadczeniu terenowym.

**OPEN:** nie ustalono, czy zgłoszenie Nachtrag będzie częścią jednego z czterech ekranów MVP, oddzielnym przepływem, czy funkcją odłożoną na później. Nie ustalono także obsługi zdjęć, podpisu, akceptacji klienta, pracy offline ani synchronizacji.
