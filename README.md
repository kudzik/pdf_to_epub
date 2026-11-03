# PDF to EPUB Converter (Modern Document AI + Pandoc)

Nowoczesny, powtarzalny pipeline do ekstrakcji treści ze skanów PDF (książek, artykułów, publikacji kulinarnych z wielokolumnowym tekstem, tabelami i grafikami) do edytowalnego formatu **Markdown** (jako Source of Truth / RAG) oraz kompilacji do standardu **EPUB 3** gotowego do odczytu na czytnikach e-booków (Kindle, Kobo, PocketBook, Apple Books).

## 🚀 Stos technologiczny
* **Język i środowisko:** Python 3.13+ zarządzany przez [`uv`](https://github.com/astral-sh/uv).
* **Document AI & Ekstrakcja:** [`docling`](https://github.com/DS4SD/docling) (analiza układu RT-DETR, detekcja kolumn, tabel i grafik) + [`pypdfium2`](https://github.com/pypdfium2-team/pypdfium2) (inteligentna detekcja warstwy tekstu).
* **Sanitizer & Normalizer:** Dedykowany moduł `src/cleaner.py` (de-hyphenation, normalizacja ułamków kulinarnych i miar, filtr szumu skanera, deduplikacja nagłówków).
* **Kompilator EPUB:** [`pandoc`](https://pandoc.org/) 3.13+.
* **CLI & Terminal:** [`typer`](https://typer.tiangolo.com/) oraz [`rich`](https://github.com/Textualize/rich).
* **Testy:** [`pytest`](https://docs.pytest.org/).

---

## 📁 Struktura projektu

```text
pdf_to_epub/
├── input/
│   └── ksiazka.pdf          # Wejściowy plik PDF
├── output/
│   ├── markdown/            # Wyjściowy Markdown + wycięte obrazy (Source of Truth)
│   │   ├── assets/          # Wyekstrahowane grafiki z książki
│   │   └── ksiazka.md       # Czysty tekst pod RAG / LLM / korektę
│   └── epub/
│       └── ksiazka.epub     # Gotowy e-book EPUB 3
├── src/
│   ├── __init__.py
│   ├── cleaner.py           # Sanitizer Markdowna (de-hyphenation, ułamki, szumy)
│   └── pipeline.py          # Główny skrypt CLI (extract, clean, build, all)
├── templates/
│   └── epub.css             # Arkusz stylów dla czytników (responsywne grafiki)
├── metadata.yaml            # Metadane publikacji (tytuł, autor, język)
├── tests/                   # Zestaw 20 testów jednostkowych i integracyjnych
├── AGENTS.md                # Instrukcja operacyjna dla agentów AI
├── pyproject.toml
└── README.md
```

---

## 🛠️ Architektura procesu

```text
       ┌───────────────┐
       │ Wejściowy PDF │
       └───────┬───────┘
               │
               ▼
    [1. extract] Inteligentna ekstrakcja (Docling Layout AI)
    - Auto-detekcja warstwy tekstowej (pomija powolny OCR, zachowując 2 kolumny)
    - Opcjonalny fallback do OCR (EasyOCR) dla czystych bitmap
               │
               ▼
    [2. clean] Sanitizer Markdowna & Normalizacja kulinarna
    - De-hyphenation (scalanie rozbitych wyrazów: cul- tural ➔ cultural)
    - Normalizacja ułamków kulinarnych (½ ➔ 1/2, 1 2 tsp ➔ 1/2 tsp)
    - Usuwanie szumów skanera (losowe znaczki: ■ ^p-, FlraS -*)
    - Deduplikacja żywej paginy (powtarzające się nagłówki stron)
               │
               ▼
    [3. Source of Truth] Czysty plik output/markdown/ksiazka.md
               │
               ▼
    [4. build] Kompilacja Pandoc (split-level 2, style CSS, okładka)
               │
               ▼
       ┌───────────────┐
       │ Gotowy EPUB 3 │
       └───────────────┘
```

---

## 📖 Użycie

### 1. Pełny cykl end-to-end
```bash
uv run python -m src.pipeline all --input-pdf input/ksiazka.pdf
```

### 2. Krok po kroku

* **Krok 1: Ekstrakcja z PDF do Markdown:**
  ```bash
  # Cały dokument (z automatyczną detekcją warstwy tekstowej)
  uv run python -m src.pipeline extract --input-pdf input/ksiazka.pdf

  # Szybki test na stronach 1-5
  uv run python -m src.pipeline extract --input-pdf input/ksiazka.pdf --pages 1-5

  # Wymuszenie silnika OCR na bitmapach
  uv run python -m src.pipeline extract --input-pdf input/ksiazka.pdf --ocr
  ```

* **Krok 2: Czyszczenie Markdowna:**
  ```bash
  uv run python -m src.pipeline clean
  ```

* **Krok 3: Kompilacja do EPUB:**
  ```bash
  uv run python -m src.pipeline build

  # Opcjonalnie z własną okładką:
  uv run python -m src.pipeline build --cover sciezka/do/okladki.jpg
  ```

### 3. Uruchamianie testów
```bash
uv run pytest
```
