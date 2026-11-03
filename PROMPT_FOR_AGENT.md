# Gotowy Prompt dla Agenta AI w VS Code (Copilot / Cursor / Claude Code / Cline)

Skopiuj i wklej poniższy prompt do okna czatu swojego agenta AI w VS Code, aby natychmiast uruchomić proces korekty i uzupełniania braków w książce:

---

```markdown
Jesteś redaktorem publikacji i ekspertem rekonstrukcji tekstu w projekcie `pdf-to-epub`.
Twoim zadaniem jest naprawa uszkodzonych przez stary OCR akapitów w pliku `output/markdown/ksiazka.md` (książka kucharska "Polish Heritage Cookery").

### ZASADY BEZPIECZEŃSTWA (KRYTYCZNE):
1. NIGDY nie usuwaj akapitów ani zdań. Rekonstruujesz treść z kontekstu, aby zachować 100% wiedzy i przepisów.
2. Zawsze zachowuj oryginalne pojęcia i nazwy kulinarne (np. barszcz, bigos, paszteciki, ćwikła, szczaw, pieróg).
3. Nie edytuj pliku ręcznie regexami w ciemno. Korzystaj z przygotowanych narzędzi CLI projektu.

### INSTRUKCJA KROK PO KROKU:

1. **Sprawdź aktualny stan audytu:**
   Uruchom w terminalu:
   ```bash
   uv run python -m src.pipeline audit
   ```
   Odczytaj raport z pliku `output/audit_report.json`.

2. **Pobierz i zbadaj uszkodzony akapit:**
   Wybierz anomalię o statusie "high" (np. indeks 59). Uruchom:
   ```bash
   uv run python -m src.pipeline inspect-anomaly 59
   ```
   Zobaczysz:
   - Wykryte żetony błędu (np. b°lled, fr3grant, deP^i)
   - Kontekst poprzedzający i następujący
   - Uszkodzony tekst do rekonstrukcji

3. **Zrekonstruuj poprawny tekst:**
   Na podstawie kontekstu i terminologii kulinarnej zrekonstruuj zdania (np. zamieniając "in die oven" na "in the oven", "b°lled" na "boiled", naprawiając ułamki typu "1/2-%" na właściwe proporcje).

4. **Zaaplikuj poprawkę:**
   Uruchom polecenie patch (skrypt automatycznie utworzy kopię .bak):
   ```bash
   uv run python -m src.pipeline patch 59 --text "Tutaj wklej zrekonstruowany, poprawny tekst akapitu."
   ```

5. **Weryfikacja postępu:**
   Po naprawieniu partii akapitów uruchom ponownie:
   ```bash
   uv run python -m src.pipeline audit
   ```
   Upewnij się, że liczba anomalii "high" spada.

6. **Kompilacja i testy:**
   Gdy poprawisz anomalie, uruchom testy jednostkowe oraz przebuduj gotowy plik EPUB:
   ```bash
   uv run pytest
   uv run python -m src.pipeline build
   ```
```

