# PDF to EPUB Converter (Modern Document AI + Pandoc)

Nowoczesny, powtarzalny pipeline do ekstrakcji treści ze skanów PDF (książek, artykułów, publikacji z wielokolumnowym tekstem, tabelami i grafikami) do edytowalnego formatu **Markdown** oraz kompilacji do standardu **EPUB 3** gotowego do odczytu na czytnikach e-booków (Kindle, Kobo, PocketBook, Apple Books).

## 🚀 Stos technologiczny
* **Język i środowisko:** Python 3.13+ zarządzany przez [`uv`](https://github.com/astral-sh/uv).
* **Document AI & OCR:** [`docling`](https://github.com/DS4SD/docling) z silnikiem `EasyOCR` (wielojęzyczny OCR, detekcja układu kolumn, tabel i wycinanie ilustracji).
* **Kompilator EPUB:** [`pandoc`](https://pandoc.org/) 3.13+.
* **CLI & Terminal:** [`typer`](https://typer.tiangolo.com/) oraz [`rich`](https://github.com/Textualize/rich).

---

## 📁 Struktura projektu

```text
pdf_to_epub/
├── input/
│   └── ksiazka.pdf          # Wejściowy plik PDF ze skanem
├── output/
│   ├── markdown/            # Wyjściowy Markdown + wycięte obrazy (Source of Truth)
│   │   ├── assets/          # Wyekstrahowane grafiki z książki
│   │   └── ksiazka.md       # Czysty tekst pod RAG / LLM / korektę
│   └── epub/
│       └── ksiazka.epub     # Gotowy e-book EPUB 3
├── src/
│   ├── __init__.py
│   └── pipeline.py          # Główny skrypt CLI
├── templates/
│   └── epub.css             # Arkusz stylów dla czytników (responsywne grafiki)
├── metadata.yaml            # Metadane publikacji (tytuł, autor, język)
├── AGENTS.md                # Instrukcja operacyjna dla agentów AI (Claude Code, Cursor, Copilot itp.)
├── pyproject.toml
└── README.md
```

---

## 🛠️ Wymagania i instalacja

### 1. Narzędzia systemowe
Projekt wymaga `pandoc` w wersji 3.0+ oraz `uv`.

Jeśli `pandoc` nie jest zainstalowany w systemie:
```bash
# Ubuntu / Debian / WSL:
sudo apt-get update && sudo apt-get install -y pandoc
# macOS:
brew install pandoc
```

### 2. Instalacja środowiska Pythona 3.13
Dzięki `uv` całe środowisko wraz z najnowszym Pythonem 3.13 i zależnościami instaluje się automatycznie:
```bash
uv sync
```

---

## 📖 Użycie

### 1. Pełny cykl (OCR + EPUB)
Wrzuć plik skanu do `input/ksiazka.pdf`, uzupełnij `metadata.yaml` i uruchom:
```bash
uv run python -m src.pipeline all
```

### 2. Ekstrakcja z PDF do Markdown (Krok 1)
Przetworzenie całego dokumentu lub wybranych stron:
```bash
# Pełny dokument
uv run python -m src.pipeline extract --input-pdf input/ksiazka.pdf

# Szybki test na stronach 1-5
uv run python -m src.pipeline extract --input-pdf input/ksiazka.pdf --pages 1-5
```

### 3. Kompilacja do EPUB (Krok 2)
Po wykonaniu ekstrakcji możesz dokonać korekty tekstu w `output/markdown/ksiazka.md` (ręcznie lub za pomocą Agenta AI), a następnie przebudować plik EPUB bez ponownego uruchamiania OCR:
```bash
uv run python -m src.pipeline build

# Opcjonalnie z własną okładką:
uv run python -m src.pipeline build --cover sciezka/do/okladki.jpg
```

### 4. Uruchamianie testów jednostkowych
Projekt zawiera pełny zestaw testów weryfikujących integrację Pandoc, strukturę generowanego EPUB, opcje OCR oraz CLI:
```bash
uv run pytest
```



