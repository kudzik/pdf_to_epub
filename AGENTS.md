# Instrukcja dla Agenta AI w projekcie pdf-to-epub

## Cel projektu
Konwersja skanowanych dokumentów PDF (książek z tekstem, tabelami i ilustracjami) do struktury Markdown (jako Source of Truth oraz bazy wiedzy pod RAG/LLM) oraz kompilacja do walidującego się standardu EPUB 3 z zachowaniem hierarchii nagłówków i grafik.

## Środowisko
- **Python:** 3.13+ zarządzany przez `uv`.
- **Zależności:** `docling[easyocr]`, `typer`, `rich`.
- **Kompilator EPUB:** `pandoc` 3.13+.

## Architektura i niezmienniki procesu
1. **Niezmiennik źródła prawdy:** Katalog `output/markdown/` oraz wygenerowany w nim plik `<nazwa>.md` stanowią jedyne źródło prawdy. Jeśli poprawiasz literówki OCR, błędy formatowania nagłówków czy podział akapitów, edytuj plik `.md`.
2. **Zakaz niepotrzebnego ponownego OCR:** Jeśli plik `.md` już istnieje i pracujesz nad korektą treści, **nie uruchamiaj ponownie** `extract` ani `all`, ponieważ surowy OCR z Doclinga bezpowrotnie nadpisze Twoje poprawki! Używaj wtedy wyłącznie `build`.
3. **Integralność grafik:** Wszystkie wyekstrahowane obrazy znajdują się w `output/markdown/assets/` i są linkowane w Markdownie relatywnie jako `assets/<nazwa>.png`. Nie zmieniaj ich ścieżek, chyba że usuwasz niepotrzebny artefakt graficzny.
4. **Style i metadane:** Metadane publikacji (autor, tytuł, język `pl-PL`) znajdują się w `metadata.yaml`, a style CSS w `templates/epub.css`.

## Komendy operacyjne dla Agenta AI
- **Pierwszy pełny przebieg (OCR + kompilacja EPUB):**
  ```bash
  uv run python -m src.pipeline all --input-pdf input/ksiazka.pdf
  ```
- **Tylko ekstrakcja i OCR (PDF -> Markdown + grafiki):**
  ```bash
  uv run python -m src.pipeline extract --input-pdf input/ksiazka.pdf
  ```
- **Szybki test ekstrakcji na pierwszych 3 stronach:**
  ```bash
  uv run python -m src.pipeline extract --input-pdf input/ksiazka.pdf --pages 1-3
  ```
- **Kompilacja / Przebudowa EPUB po korekcie Markdowna:**
  ```bash
  uv run python -m src.pipeline build
  ```
- **Kompilacja z własną okładką:**
  ```bash
  uv run python -m src.pipeline build --cover assets/cover.jpg
  ```

