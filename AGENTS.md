# Instrukcja dla Agenta AI w projekcie pdf-to-epub

## Cel projektu
Konwersja dokumentów i skanów PDF (książek z tekstem jedno- i dwukolumnowym, tabelami, przepisami i grafikami) do czystego formatu Markdown (baza wiedzy pod RAG/LLM) oraz kompilacja do walidującego się standardu EPUB 3 z zachowaniem hierarchii nagłówków i grafik.

## Środowisko
- **Python:** 3.13+ zarządzany przez `uv`.
- **Zależności:** `docling[easyocr]`, `pypdfium2`, `typer`, `rich`, `pytest`.
- **Kompilator EPUB:** `pandoc` 3.13+.

## Architektura i niezmienniki procesu
1. **Pipeline 3-etapowy:**
   - **Krok 1: Ekstrakcja (`extract`):** Odczyt struktury Document AI. Jeśli PDF ma warstwę tekstową, domyślnie pomija wolny EasyOCR, zachowując perfekcyjny podział kolumn i ułamki.
   - **Krok 2: Czyszczenie (`clean`):** Moduł `src/cleaner.py` automatycznie wykonuje de-hyphenation (scalanie rozbitych słów), zamienia ułamki Unicode (`½` ➔ `1/2`), naprawia miary kulinarne (`1 2 tsp` ➔ `1/2 tsp`), usuwa artefakty skanera (`■ ^p-`, `FlraS -*`) oraz powtarzające się nagłówki żywej paginy.
   - **Krok 3: Kompilacja (`build`):** Pandoc kompiluje czysty Markdown do EPUB 3 ze stylami `templates/epub.css` i metadanymi `metadata.yaml`.
2. **Niezmiennik źródła prawdy:** Katalog `output/markdown/` oraz wygenerowany w nim plik `<nazwa>.md` stanowią jedyne źródło prawdy. Jeśli poprawiasz treść, edytuj plik `.md`, a następnie uruchom `build`.
3. **Zakaz niepotrzebnego ponownego OCR:** Jeśli plik `.md` już istnieje i pracujesz nad korektą treści, **nie uruchamiaj ponownie** `extract` ani `all`, ponieważ surowy odczyt nadpisze Twoje poprawki! Używaj wtedy `clean` oraz `build`.
4. **Integralność grafik:** Wszystkie wyekstrahowane obrazy znajdują się w `output/markdown/assets/` i są linkowane w Markdownie relatywnie jako `assets/<nazwa>.png`.

## Komendy operacyjne dla Agenta AI
- **Pełny automatyczny przebieg (Ekstrakcja + Czyszczenie + EPUB):**
  ```bash
  uv run python -m src.pipeline all --input-pdf input/ksiazka.pdf
  ```
- **Tylko ekstrakcja (PDF -> surowy Markdown):**
  ```bash
  uv run python -m src.pipeline extract --input-pdf input/ksiazka.pdf
  ```
- **Szybki test ekstrakcji na wybranych stronach:**
  ```bash
  uv run python -m src.pipeline extract --input-pdf input/ksiazka.pdf --pages 1-5
  ```
- **Czyszczenie i normalizacja pliku Markdown:**
  ```bash
  uv run python -m src.pipeline clean
  ```
- **Audyt jakościowy Markdowna (wykrywanie uszkodzeń OCR i braków):**
  ```bash
  uv run python -m src.pipeline audit
  ```
- **Kompilacja EPUB po korekcie Markdowna:**
  ```bash
  uv run python -m src.pipeline build
  ```
- **Kompilacja z własną okładką:**
  ```bash
  uv run python -m src.pipeline build --cover assets/cover.jpg
  ```
- **Uruchomienie testów jednostkowych:**
  ```bash
  uv run pytest
  ```
