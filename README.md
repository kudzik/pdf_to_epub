# PDF to EPUB & RAG Knowledge Base Converter

Kompleksowy, przemysłowy pipeline do rekonstrukcji skanów i dokumentów PDF (w tym trudnych publikacji dwukolumnowych, tabel, przepisów kulinarnych i grafik) do czystego, semantycznego formatu **Markdown** (jako jedno źródło prawdy / baza wiedzy pod RAG) oraz kompilacji do weryfikowanego standardu **EPUB 3** oraz wektorowej bazy wiedzy dla modeli LLM.

Szczegółowa dokumentacja pipeline'u i workflow narzędziowego: [docs/WORKFLOW_KONWERSJI_PDF_DO_EPUB.md](docs/WORKFLOW_KONWERSJI_PDF_DO_EPUB.md).  
Szczegółowy plan rekonstrukcji i historia bramek jakościowych: [docs/PLAN_REKONSTRUKCJI.md](docs/PLAN_REKONSTRUKCJI.md).  
Końcowy raport certyfikacji projektu (Gate G0–G5): [docs/FINAL_PROJECT_REPORT.md](docs/FINAL_PROJECT_REPORT.md).

---

## 🚀 Kluczowe Osiągnięcia i Wyniki (Projekt Ukończony)

* **100% pokrycia korpusu:** 914 z 914 stron książki *Polish Heritage Cookery* zrekonstruowane bezstratnie (0 braków stron).
* **327 762 słowa i 13 362 akapity:** Eliminacja 32 528 słów zduplikowanych nagłówków, szumów skanera i artefaktów.
* **100% integralności grafik:** Wszystkie **113 ilustracji** z `output/markdown/assets/*.png` bezstratnie zachowane i poprawnie zalinkowane w publikacji Markdown oraz w kontenerze EPUB 3.
* **Wierność jednostek i miar kulinarnych:** Precyzyjne wyekstrahowanie **18 729 miar** (rozróżnianie `t.` łyżeczka vs `T.` łyżka, `1/2`, `1/4`, temperatury `°F` / `°C`).
* **Zwalidowany kontener EPUB 3:** Wygenerowany plik `output/epub/ksiazka.epub` (6.39 MB) pomyślnie przeszedł rygorystyczny audyt specyfikacji IDPF/W3C (werdykt: **PASS**).
* **Baza wiedzy RAG:** Wygenerowano **717 chunków semantycznych** (679 ustrukturyzowanych przepisów) w formacie JSONL ze sprzętową wektoryzacją (768-dim) na karcie **NVIDIA GeForce RTX 4080 SUPER**.
* **100% pokrycia testami:** **163 testy Pytest** (Python 3.13) + **28 testów Node.js** (Astro Studio) zdanych bezbłędnie.

---

## 🛠️ Stos Technologiczny

| Warstwa | Technologia / Narzędzie | Rola w systemie |
| --- | --- | --- |
| **Zarządzanie środowiskiem** | Python 3.13+ (`uv`), Node.js 20+ | Nowoczesny, deterministyczny menedżer zależności |
| **Document AI** | `docling` 2.146.0 (RT-DETR, TableFormer) | Detekcja układu strony, bloków, tabel i bounding boxów |
| **Silnik OCR** | `rapidocr-onnxruntime` + `pypdfium2` | Błyskawiczny, deterministyczny OCR z renderingiem 300 DPI |
| **Geometria i układy** | `src/geometry.py` | Autorski dewarping (wariancja rzutów), detekcja guttera i separacja 2 kolumn |
| **Kontrola i jakość** | `src/block_validator.py`, `src/write_control.py` | Rygorystyczny filtr niezmienników miar, ochrona grafik i blokady zapisu |
| **Reconciliacja i migracja** | `src/migration.py` | Mapowanie tokenowe shingli, bezpieczna publikacja i atomowy rollback |
| **Kompilator EPUB 3** | `pandoc` 3.13+ | Kompilacja Markdown do EPUB 3 ze stylami `templates/epub.css` |
| **Walidator EPUB 3** | `src/epub_validator.py` | Audyt kontenera, `mimetype`, `container.xml`, `content.opf`, nawigacji i mediów |
| **Eksporter RAG / LLM** | `src/rag_exporter.py` | Chunking semantyczny, metadane przepisów i eksport JSONL |
| **Akceleracja GPU** | NVIDIA RTX 4080 SUPER + Ollama | Generowanie embeddingów modelem `nomic-embed-text` |
| **Studio Web & UI** | Astro 7 (SSR) + Tailwind CSS v4 | Wizualna inspekcja skanów, katalog wad i Autopilot naprawczy |

---

## 📁 Struktura Projektu

```text
pdf_to_epub/
├── input/
│   └── ksiazka.pdf                   # Wejściowy plik PDF (914 stron)
├── output/
│   ├── markdown/                     # Produkcyjny Markdown (Jedno Źródło Prawdy)
│   │   ├── assets/                   # 113 wyekstrahowanych ilustracji
│   │   └── ksiazka.md                # 327k słów, 13k akapitów
│   ├── epub/
│   │   └── ksiazka.epub              # Zwalidowany e-book EPUB 3 (6.39 MB)
│   ├── rag/
│   │   ├── ksiazka_rag_chunks.jsonl  # 717 chunków z embeddingami GPU
│   │   └── ksiazka_rag_summary.json  # Raport podsumowujący bazy wiedzy
│   ├── candidates/                   # Izolowane przebiegi kandydata przed publikacją
│   ├── snapshots/                    # Trwałe migawki stanu bazowego z sumami SHA-256
│   └── benchmarks/                   # Raporty jakości, CER/WER, bramki G1–G5
├── src/
│   ├── block_validator.py            # Walidacja niezmienników (miary kulinarne, temperatury)
│   ├── cleaner.py                    # Sanitizer tekstu (de-hyphenation, ułamki, szumy)
│   ├── epub_validator.py             # Walidator kontenera EPUB 3
│   ├── extraction_adapter.py         # Adapter Docling i wycinanie wycinków bloków
│   ├── full_candidate_runner.py      # Biegacz 914 stron z checkpointami partii
│   ├── geometry.py                   # Dewarping, prostowanie, detekcja kolumn
│   ├── ground_truth.py               # Wzorce referencyjne ze skanów 300 DPI
│   ├── history.py                    # Księga decyzji i kotwice indeksów akapitów
│   ├── migration.py                  # Reconciliacja, manifest grafik, publikacja, rollback
│   ├── ocr_modes.py                  # Jawne silniki OCR (native, rapidocr, full_page)
│   ├── pilot_evaluation.py           # Pilot 50 stron i ekstrapolacja kosztu
│   ├── pipeline.py                   # Główny interfejs CLI (wszystkie polecenia)
│   ├── rag_exporter.py               # Podział semantyczny i embeddingi GPU Ollama
│   ├── snapshot.py                   # Zarządzanie snapshotami bazy
│   └── write_control.py              # Atomowy zapis z blokadami fcntl.flock
├── web/                              # Interfejs Astro Studio UI (port 4343)
├── benchmarks/fixtures/              # Kanoniczny manifest stron i wzorce Ground Truth
├── templates/epub.css                # Arkusz stylów CSS dla EPUB
├── metadata.yaml                     # Metadane publikacji
├── docs/PLAN_REKONSTRUKCJI.md        # Szczegółowa specyfikacja i dziennik decyzji
├── AGENTS.md                         # Instrukcja operacyjna dla agentów AI
└── pyproject.toml                    # Zależności i konfiguracja pytest
```

---

## ⚡ Szybki Start i Podstawowe Komendy CLI

Wszystkie komendy uruchamiane są deterministycznie przez `uv run`:

### 1. Kompilacja i Walidacja EPUB 3
```bash
# Kompilacja publikacji produkcyjnej do formatu EPUB 3 (trwa ~6 sekund):
uv run python -m src.pipeline build --book ksiazka

# Rygorystyczna walidacja kontenera EPUB 3 (mimetype, container, opf, spine, nav, assets):
uv run python -m src.pipeline epub-validate --epub output/epub/ksiazka.epub

# Otwarcie folderu z e-bookiem bezpośrednio w Eksploratorze Windows (dla WSL):
uv run python -m src.pipeline open-windows --folder epub
```

### 2. Eksport Bazy Wiedzy RAG (z akceleracją GPU)
```bash
# Podział na ustrukturyzowane chunki pod RAG (format JSONL i JSON):
uv run python -m src.pipeline rag-export --input-md output/markdown/ksiazka.md

# Pełny eksport z generowaniem embeddingów na GPU (RTX 4080 SUPER) modelem nomic-embed-text:
uv run python -m src.pipeline rag-export --input-md output/markdown/ksiazka.md --embed --model nomic-embed-text
```

### 3. Migracja, Publikacja Kandydata i Rollback (Gate G4)
```bash
# Porównanie różnic pomiędzy kandydatem a produkcją:
uv run python -m src.pipeline candidate-diff --candidate output/candidates/cand_full_914_frozen/ksiazka.md

# Bezpieczna, atomowa publikacja kandydata do produkcji ze snapshotem bazowym:
uv run python -m src.pipeline candidate-publish --candidate output/candidates/cand_full_914_frozen/ksiazka.md

# Natychmiastowy rollback i przywrócenie poprzedniego stanu produkcyjnego z .pre_r18_bak:
uv run python -m src.pipeline candidate-rollback
```

### 4. Ekstrakcja i Konwersja Pełnej Książki (Gate G3/G4)
```bash
# Pełna konwersja 914 stron z automatycznymi checkpointami (partie po 50 stron):
uv run python -m src.pipeline full-candidate-convert

# Szybki test ekstrakcji na wybranych stronach w trybie RapidOCR z geometrią:
uv run python -m src.pipeline candidate-convert --pages 25-26 --mode rapidocr --geometry
```

### 5. Audyt i Kontrola Jakości Markdowna
```bash
# Audyt jakościowy (wykrywanie anomalii OCR, rozbitych ułamków i szumów):
uv run python -m src.pipeline audit --book ksiazka

# Czyszczenie i normalizacja tekstu Markdown:
uv run python -m src.pipeline clean --book ksiazka
```

---

## 🖥️ Web Studio UI (Astro 7 + Tailwind CSS)

Interfejs graficzny dedykowany do inspekcji skanów i nadzorowania poprawek:

```bash
cd web
npm install
npm run dev   # Uruchomienie na porcie http://localhost:4343
```

* **Podgląd skanów 300 DPI:** Wycinki stron PDF powiązane bezpośrednio z indeksami akapitów.
* **Księga decyzji (History Ledger):** Pełny rejestr wprowadzonych modyfikacji z uzasadnieniem i weryfikacją kotwic indeksów.
* **Autopilot:** Bezpieczny automat przeglądający wady z możliwością natychmiastowego zatrzymania (AbortController) bez uszkadzania pliku produkcyjnego.

---

## 🧪 Uruchamianie Testów

Gwarancja jakości (Definition of Done) wymaga 100% zaliczenia obu zestawów testów:

```bash
# Testy backendowe Python (163 testy):
uv run pytest

# Testy frontendowe Node.js (28 testów):
npm --prefix web test
```

---

## 📄 Licencja i Informacje Prawne

Projekt zrealizowany zgodnie z zasadami czystego kodu, test-driven development (TDD) oraz ścisłą dyscypliną Git/GitHub (Conventional Commits, Pull Requests, Milestones, Issues).
Wszystkie algorytmy i narzędzia działają lokalnie bez konieczności wysyłania wrażliwych danych do zewnętrznych płatnych chmur.
