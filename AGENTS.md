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
- **Wybór książki:** Wszelkie polecenia (`extract`, `clean`, `audit`, `build`, `purge`) wspierają opcję `--book` / `-b <nazwa>` (np. `-b ksiazka`).
- **Auto-tworzenie pliku Markdown:** Jeśli wywołasz `clean`, `audit` lub `build` z opcją `--book <nazwa>`, a plik `output/markdown/<nazwa>.md` nie istnieje, pipeline automatycznie przeprowadzi bezstratną ekstrakcję z `input/<nazwa>.pdf`.
- **Pełny automatyczny przebieg (Ekstrakcja + Czyszczenie + EPUB + Kopiowanie do Windows):**
  ```bash
  uv run python -m src.pipeline all --input-pdf input/ksiazka.pdf
  ```
- **Tylko ekstrakcja (PDF -> surowy Markdown):**
  ```bash
  uv run python -m src.pipeline extract --book ksiazka
  ```
- **Szybki test ekstrakcji na wybranych stronach:**
  ```bash
  uv run python -m src.pipeline extract --input-pdf input/ksiazka.pdf --pages 1-5
  ```
- **Czyszczenie i normalizacja pliku Markdown:**
  ```bash
  uv run python -m src.pipeline clean --book ksiazka
  ```
- **Audyt jakościowy Markdowna (wykrywanie uszkodzeń OCR i braków):**
  ```bash
  uv run python -m src.pipeline audit --book ksiazka
  ```
- **Inspekcja konkretnej anomalii z kontekstem przed i po:**
  ```bash
  uv run python -m src.pipeline inspect-anomaly 51
  ```
- **Bezpieczna aplikacja poprawki akapitu (tworzy kopię .bak):**
  ```bash
  uv run python -m src.pipeline patch 51 --text "Zrekonstruowany tekst..."
  ```
- **Aplikacja paczki poprawek z pliku JSON:**
  ```bash
  uv run python -m src.pipeline patch-batch --file fixes.json
  ```
- **Kompilacja EPUB po korekcie Markdowna:**
  ```bash
  uv run python -m src.pipeline build --book ksiazka
  ```
- **Tworzenie i weryfikacja snapshotu stanu książki i środowiska (Gate G0 / R01):**
  ```bash
  # Tworzy nowy snapshot (zapisuje manifest SHA-256 i kopie plików bazowych):
  uv run python -m src.pipeline snapshot --book ksiazka
  # Weryfikuje integralność istniejącego snapshotu:
  uv run python -m src.pipeline snapshot --verify-dir output/snapshots/<snapshot_id>
  ```
- **Przeglądanie historii decyzji i weryfikacja kotwic indeksów (Gate G0 / R03):**
  ```bash
  uv run python -m src.pipeline history --book ksiazka
  # Szczegóły dla konkretnego akapitu:
  uv run python -m src.pipeline history --book ksiazka --index 51
  ```
- **Weryfikacja manifestu stron testowych (Gate G1 / R04):**
  ```bash
  uv run python -m src.pipeline test-manifest
  # Szczegółowa tabela stron dev i holdout:
  uv run python -m src.pipeline test-manifest --details
  ```
- **Zarządzanie wzorcami ze skanów i rendering stron (Gate G1 / R05):**
  ```bash
  # Renderowanie skanów stron PDF (300 DPI) do benchmarks/fixtures/images/:
  uv run python -m src.pipeline ground-truth-render --page 25
  # Walidacja poprawności wzorców i spójności z manifestem:
  uv run python -m src.pipeline ground-truth-validate
  # Podsumowanie liczby bloków, ilości/jednostek i tabel:
  uv run python -m src.pipeline ground-truth-summary
  ```
- **Adapter ekstrakcji i rejestracja pochodzenia bloków (Gate G1 / R06):**
  ```bash
  # Przetworzenie wybranej strony przez adapter, zapis sidecara JSON i wycinków bloków (crops):
  uv run python -m src.pipeline extract-page-adapter --page 25 --crop-blocks
  ```
- **Jawne tryby OCR i izolowany eksport kandydata (Gate G1 / R07, Gate G2 / R09, Gate G2 / R10):**
  ```bash
  # Wykonanie konwersji kandydata (tryby: native, full_page, layout_regions, rapidocr; opcja --geometry włącza separację 2 kolumn i prostowanie):
  uv run python -m src.pipeline candidate-convert --pages 25-26 --mode rapidocr --geometry
  # Lista zarejestrowanych przebiegów kandydackich:
  uv run python -m src.pipeline candidate-list
  ```
- **Inspekcja geometrii, prostowania i układu kolumn (Gate G2 / R10):**
  ```bash
  # Analiza kąta nachylenia (skew), guttera pionowego i re-sekwencjonowania bloków:
  uv run python -m src.pipeline geometry-inspect --page 58
  ```
- **Pomiar jakości OCR, CER/WER i runner porównań (Gate G1 / R08, Gate G2 / R09):**
  ```bash
  # Ewaluacja jakości kandydata względem wzorców (CER, WER, wierność miar i temperatur, pominięcia):
  uv run python -m src.pipeline benchmark-evaluate --candidate-dir output/candidates/<run_id>
  # Porównanie dwóch wariantów (np. native vs full_page):
  uv run python -m src.pipeline benchmark-compare --candidate-a output/candidates/<run1> --candidate-b output/candidates/<run2>
  # Zbiorczy benchmark porównawczy wielu silników (native, full_page, rapidocr) na stronach GT lub dev:
  uv run python -m src.pipeline benchmark-ocr --pages gt --modes native,full_page,rapidocr
  ```
- **Kontrolowany test VLM na wycinkach uszkodzonych bloków (Gate G2 / R12):**
  ```bash
  # Test wycinków trudnych bloków (ułamki, stopnie, miary kulinarne) modelem wizyjnym Ollama:
  uv run python -m src.pipeline vlm-test --pages 58,25 --model moondream:latest
  ```
- **Weryfikacja bramki Gate G2 i ślepy test holdout (Gate G2 / R14):**
  ```bash
  # Uruchomienie konwersji kandydata na 10 stronach holdout i weryfikacja kryteriów sekcji 8:
  uv run python -m src.pipeline gate-g2-evaluate
  ```
- **Pilotażowa ewaluacja 50 nowych stron i weryfikacja Bramki Gate G3 (Gate G3 / R16):**
  ```bash
  # Uruchomienie pilota 50 stron zamrożonym stosem produkcyjnym, kontrola przejść i ekstrapolacja kosztu:
  uv run python -m src.pipeline pilot-evaluate --pages 201-250
  ```
- **Otwieranie folderu z e-bookiem w Eksploratorze Windows (WSL):**
  ```bash
  uv run python -m src.pipeline open-windows --folder epub
  ```
- **Czyszczenie plików roboczych po skończonej pracy (oszczędność dysku):**
  ```bash
  # Usuwa pliki robocze (.md, .bak, raport), zachowując gotowy .epub:
  uv run python -m src.pipeline purge --book ksiazka --keep-epub
  # Usuwa wszystkie pliki robocze:
  uv run python -m src.pipeline purge --all
  ```
- **Uruchomienie testów jednostkowych:**
  ```bash
  uv run pytest
  ```
