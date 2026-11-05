# Kompletna Dokumentacja Pipeline'u Konwersji PDF do Markdown i EPUB 3

Niniejszy dokument przedstawia kompleksową architekturę, narzędzia, biblioteki oraz standardowy proces (workflow) konwersji dokumentów i książek PDF (w tym wielokolumnowych, ze skanami, komiksami, tabelami i recepturami kulinarnymi) do czystego, walidującego się formatu Markdown (baza wiedzy pod RAG/LLM) oraz produkcyjnego standardu EPUB 3.

---

## 1. Architektura i Filozofia Systemu

Głównym celem systemu jest **bezwzględna wierność źródłu PDF**: eliminacja halucynacji OCR, zachowanie 100% receptur i składników, nienaruszona hierarchia nagłówków oraz pełna integralność wyekstrahowanych grafik.

```mermaid
flowchart TD
    subgraph Krok 1: Ingestia i Ekstrakcja
        PDF["Wejściowy plik PDF<br/>(input/*.pdf)"] --> Inspect["Analiza warstwy tekstowej<br/>(pypdfium2 / Document AI)"]
        Inspect --> Decision{"Jakość warstwy tekstu?"}
        Decision -->|"Czysty tekst cyfrowy"| ExtractDirect["Bezpośrednia ekstrakcja struktury<br/>(Docling Document AI)"]
        Decision -->|"Uszkodzona / Skany / Komiksy"| OCRPipeline["Wielomodalny OCR / VLM<br/>(RapidOCR + Ollama)"]
        ExtractDirect --> RawMD["Surowy Markdown + Assets<br/>(output/markdown/assets/*.png)"]
        OCRPipeline --> RawMD
    end

    subgraph Krok 2: Normalizacja i Czyszczenie (src/cleaner.py)
        RawMD --> DeHyphen["De-hyphenation (scalanie wyrazów)"]
        DeHyphen --> Fractions["Normalizacja ułamków (Unicode, typografia)"]
        Fractions --> Ingredients["Formatowanie składników (rozbijanie run-on)"]
        Ingredients --> Headings["Naprawa nagłówków i odklejanie grafik"]
        Headings --> DeNoise["Eliminacja szumu skanera i śmieciowych glifów"]
        DeNoise --> CleanMD["Oczyszczony Markdown (Źródło Prawdy)"]
    end

    subgraph Krok 3: Pętla Kontroli Jakości (Audit & Snapshot)
        CleanMD --> Audit["Audyt jakościowy (src/auditor.py)<br/>Heurystyki anomalii"]
        Audit -->|"Wykryto luki / błędy"| SurgRepair["Surgiczna rekonstrukcja z OCR"]
        SurgRepair --> CleanMD
        Audit -->|"Audyt zaliczony (PASS)"| Snapshot["Zamrożenie stanu (Gate G0)<br/>Manifest SHA-256 + Snapshot"]
    end

    subgraph Krok 4: Kompilacja i Walidacja Końcowa
        Snapshot --> PandocBuild["Kompilacja Pandoc 3.13+<br/>(epub.css + metadata.yaml)"]
        PandocBuild --> EPUB["Standard EPUB 3 (output/epub/*.epub)"]
        EPUB --> EPUBVal["Walidacja kontenera (Gate G5)<br/>Mimetype, OPF, Nav, XML"]
        EPUB --> WinSync["Automatyczny sync do Windows Downloads"]
        Snapshot --> RAGExport["Eksport Bazy Wiedzy RAG (JSONL/JSON)<br/>+ GPU Embeddings (RTX 4080)"]
    end
```

---

## 2. Narzędzia i Stos Technologiczny

| Kategoria | Narzędzie / Biblioteka | Wersja | Rola w ekosystemie |
| :--- | :--- | :--- | :--- |
| **Środowisko i Pakiety** | **Python** | 3.13+ | Język bazowy całego potoku |
| | **uv** | 0.6+ | Błyskawiczny zarządca środowisk wirtualnych i zależności (deterministyczny `uv.lock`, PEP 723) |
| | **Linux / WSL 2** | Ubuntu 24.04 | Środowisko wykonawcze z bezpośrednim dostępem do GPU |
| **Analiza Dokumentów** | **Docling** | 2.146+ | IBM Document AI – analiza układu (Layout Analysis), struktury tabel i kolejności czytania |
| | **pypdfium2** | 4.30+ | Silnik Foxit PDFium – ultra-szybka rasteryzacja stron (300 DPI) i inspekcja warstwy tekstowej |
| | **Pillow (PIL)** | 11.0+ | Przetwarzanie obrazów rastrowych, kadrowanie bounding boxów, zapis do `assets/*.png` |
| **OCR i Wizja (VLM)** | **RapidOCR** | `rapidocr-onnxruntime` | Silnik ONNX (PP-OCRv4) – detekcja DBNet + CRNN/SVTR; stabilny, nie halucynuje, idealny do ilustracji |
| | **EasyOCR** | `easyocr` (PyTorch) | Opcjonalny silnik OCR z obsługą GPU |
| | **Ollama (VLM)** | Lokalny serwer | Lokalne modele wizyjne (`moondream:latest`, `qwen2.5:14b`) na RTX 4080 SUPER do trudnych bloków |
| **Kompilacja Publikacji**| **Pandoc** | 3.13+ | Uniwersalny konwerter dokumentów generujący standard EPUB 3 |
| | **Custom CSS / YAML** | Własne szablony | `templates/epub.css` (typografia mobilna) oraz `metadata/` (metadane dublin core) |
| **Baza Wiedzy (RAG)** | **RAG Exporter** | Własny moduł | Parsowanie semantyczne na chunki przepisów i narracji, ekstrakcja miar kulinarnych |
| | **Nomic Embed Text** | Via Ollama GPU | Wektoryzacja 768-dim na karcie graficznej NVIDIA |
| **Jakość i Testy** | **pytest** | 8.3+ | 210 testów jednostkowych, regresyjnych i testów niezmienników hipotez |
| | **Rich / Typer** | 13.0+ / 0.15+ | Czytelny interfejs CLI z paskami postępu i kolorowym formatowaniem |

---

## 3. Szczegółowy Workflow Krok po Kroku

### Krok 1: Weryfikacja wejścia i ocena jakości warstwy tekstowej
Przed uruchomieniem konwersji pipeline dokonuje ewaluacji jakości warstwy tekstowej pliku PDF (`evaluate_text_layer_quality`):
* Jeśli PDF posiada cyfrową warstwę tekstu, przeprowadzana jest bezpośrednia ekstrakcja układu (Layout Extraction) z zachowaniem idealnych znaków diakrytycznych.
* **Kluczowa lekcja (Problem ToUnicode):** W książkach z ilustracjami (np. komiksy, rysunki artystyczne) warstwa tekstowa bywa uszkodzona przez zniekształcone mapowanie czcionek (ToUnicode mapping). Powoduje to generowanie ciągów śmieciowych glifów (np. `A 4 =. = Y x ’ f= ’INS er ANT acs a Sete e tag`). W takich miejscach konieczne jest zastosowanie OCR (RapidOCR) bezpośrednio na zrenderowanym obrazie strony.

Komenda operacyjna:
```bash
uv run python -m src.pipeline extract --book "Nazwa_Ksiazki"
```

### Krok 2: Czyszczenie i normalizacja tekstu (`src/cleaner.py`)
Oczyszczanie odbywa się według rygorystycznych reguł deterministycznych:
1. **De-hyphenation:** Scalanie wyrazów podzielonych łącznikami na końcach linii:
   $$\text{"Mac-"} \setminus\text{n} \text{"anese"} \longrightarrow \text{"Macanese"}$$
2. **Normalizacja ułamków:**
   * Zamiana symboli Unicode: `½` $\to$ `1/2`, `¼` $\to$ `1/4`, `¾` $\to$ `3/4`.
   * Typograficzne cudzysłowy i zniekształcenia OCR: `1'/2` $\to$ `1 1/2`, `'/3` $\to$ `1/3`, `"2 cup` $\to$ `1/2 cup`, `V2 cup` $\to$ `1/2 cup`.
   * Naprawa brakujących kresek ułamkowych przed jednostkami: `plus 12 teaspoons` $\to$ `plus 1 1/2 teaspoons`, `1 2 tsp` $\to$ `1/2 tsp`.
3. **Formatowanie list składników (`format_recipe_ingredients`):**
   * Wykrywanie sklejonych w jednym wierszu pozycji (np. `2 quarts water 1 1/4 ounces salt 1 head cabbage`) i rozbijanie ich na przejrzystą listę Markdown (`- ...`).
4. **Czyszczenie struktury nagłówków (`clean_markdown_headings`):**
   * Odklejanie obrazów wklejonych w tej samej linii co tytuł (`## INTRO\n\n![Image](...)`).
   * Demotowanie pojedynczych składników i zdań wykonawczych błędnie oznaczonych jako `##`.
   * Usuwanie znaczników przejścia stron (`## >> continued`).
5. **Usuwanie szumu skanera (`remove_scan_noise`):**
   * Eliminacja izolowanych kresek (`|`, `/`, `\`), pustych punktorów oraz klastrów losowych liter z marginesów.

Komenda operacyjna:
```bash
uv run python -m src.pipeline clean --book "Nazwa_Ksiazki"
```

### Krok 3: Pętla Audytu Jakościowego i Weryfikacja OCR (`src/auditor.py`)
Audytor skanuje plik Markdown akapit po akapicie, klasyfikując ewentualne anomalie wg wag (High, Medium, Low):
* **Wykrywanie utraconych przepisów:** Porównanie spisu treści z rzeczywistymi nagłówkami przepisów w treści dokumentu.
* **Weryfikacja składników:** Kontrola czy przepisy posiadają listę składników (ilość + jednostka miary).
* **Weryfikacja OCR:** W przypadku wykrycia zniekształceń, pipeline pobiera obraz ze strony PDFium (skala 2.0 / 300 DPI), przetwarza go silnikiem RapidOCR i podmienia zniekształcony fragment na autentyczny tekst źródłowy.

Komenda operacyjna:
```bash
uv run python -m src.pipeline audit --book "Nazwa_Ksiazki"
```

### Krok 4: Zamrożenie stanu i rejestracja snapshotu (Bramka Gate G0)
Przed finalną publikacją tworzony jest niezmienny snapshot w `output/snapshots/<snapshot_id>/`:
* Obliczany jest manifest skrótów SHA-256 dla pliku Markdown oraz wszystkich 100% wyekstrahowanych obrazów w katalogu `assets/`.
* Rejestrowane są metadane środowiska (wersje Pythona, Pandoc, bibliotek, stan CUDA i modeli Ollama).

Komenda operacyjna:
```bash
uv run python -m src.pipeline snapshot --book "Nazwa_Ksiazki"
```

### Krok 5: Kompilacja do walidującego się standardu EPUB 3
Pandoc kompiluje oczyszczony Markdown do formatu EPUB 3 z zastosowaniem stylów CSS i metadanych Dublin Core:
* Zachowanie hierarchii nagłówków (`#`, `##`, `###`).
* Zachowanie relatywnych linków do grafik (`assets/*.png`).
* Wdrożenie responsywnych stylów typograficznych dla czytników e-booków (Kindle, Apple Books, Kobo, Calibre).

Komenda operacyjna:
```bash
uv run python -m src.pipeline build --book "Nazwa_Ksiazki"
```

### Krok 6: Rygorystyczna Walidacja Kontenera EPUB 3 (Gate G5)
Moduł `src/epub_validator.py` weryfikuje strukturę wygenerowanego archiwum EPUB:
1. Pierwsze 4 bajty: `mimetype` nieskompresowany (`application/epub+zip`).
2. Poprawność kontenera XML `META-INF/container.xml`.
3. Poprawność pakietu OPF (`content.opf`), manifestu oraz spine.
4. Obecność pliku nawigacji XHTML (`nav.xhtml`).
5. Kompletność assetów: 100% plików zadeklarowanych w manifeście musi fizycznie istnieć w kontenerze.

Komenda operacyjna:
```bash
uv run python -m src.pipeline epub-validate --epub output/epub/Nazwa_Ksiazki.epub
```

### Krok 7: Eksport Bazy Wiedzy pod RAG i LLM
Moduł `src/rag_exporter.py` przetwarza oczyszczony plik Markdown na ustrukturyzowaną bazę wiedzy:
* Podział na logiczne chunki z metadanymi (typ fragmentu: `recipe` vs `narrative`).
* Ekstrakcja liczby porcji, temperatur pieczenia oraz znormalizowanych miar kulinarnych.
* Powiązanie z konkretnymi plikami ilustracji.
* Opcjonalna wektoryzacja na GPU za pośrednictwem lokalnego serwera Ollama.

Komenda operacyjna:
```bash
uv run python -m src.pipeline rag-export --input-md output/markdown/Nazwa_Ksiazki.md
```

---

## 4. Studium Przypadku: Rekonstrukcja "The Adventures of Fat Rice"

Podczas realizacji konwersji 328-stronicowej książki kulinarno-komiksowej *The Adventures of Fat Rice* napotkano szereg złożonych wyzwań, które doprowadziły do udoskonalenia i uogólnienia pipeline'u:

### Wyzwania i Zastosowane Rozwiązania

1. **Problem zniekształceń ToUnicode w komiksach (strona tytułowa i panele narracyjne):**
   * *Problem:* Zamiast tytułu książki i autorów, odczyt cyfrowy generował ciąg: `A 4 =. = Y x ’ f= ’INS er ANT acs a Sete e tag`.
   * *Rozwiązanie:* Zastosowano celowany skan RapidOCR na zrenderowanej stronie tytułowej (strona 7 PDF) i przywrócono autentyczne napisy: Abraham Conlon, Adrienne Lo, Hugh Amano, zdjęcia Dan Goldberg, ilustracje Sarah Becan, wydawnictwo Ten Speed Press Berkeley.

2. **Utracone przepisy osadzone w grafikach:**
   * *Galinha a Africana Marinade:* Cały przepis na marynatę (strona 187 PDF) był narysowany w dymkach komiksowych. Został zrekonstruowany w 100% za pomocą RapidOCR.
   * *Portuguese-Style Tomato Sauce:* Przepis z 271 strony książki został z powodu specyfiki kolumn Document AI pominięty. Został zidentyfikowany podczas porównania OCR vs PDF i w całości wdrożony do rozdziału *Building Blocks*.
   * *Minchi & Potato Croutons:* Przywrócono uciętą procedurę Minchi oraz brakujący przepis na *Potato Croutons*.

3. **Naprawa sklejonych składników i błędnych nagłówków:**
   * Rozwinięto moduł `src/cleaner.py` o inteligentny parser `format_recipe_ingredients`, który automatycznie dzieli ciągi produktów w nawiasach i jednostkach miar na czytelne listy Markdown.
   * Oczyszczono zniekształcone nagłówki komiksowe (np. `## ae ee) Gicr` $\to$ `## PO KOK GAI ("PORTUGUESE" CHICKEN CURRY)`).

### Metryki Końcowe Książki
* **Format Markdown:** 5070 linii, 2159 akapitów, 0 uszkodzonych nagłówków.
* **Grafiki:** 285 wyekstrahowanych ilustracji, 100% linków poprawnych relatywnie (`assets/*.png`).
* **Walidacja EPUB 3 (Gate G5):** **PASS** (540 elementów w pakiecie, 0 braków).
* **Baza wiedzy RAG:** 337 chunków (203 przepisy, 3658 zindeksowanych jednostek kulinarnych).
* **Testy regresyjne:** 210/210 zaliczonych (`uv run pytest`).

---

## 5. Przewodnik Operacyjny (Ściągawka Poleceń)

```bash
# 1. Pełny zautomatyzowany przebieg od PDF do gotowego EPUB:
uv run python -m src.pipeline all --input-pdf input/ksiazka.pdf

# 2. Tylko ekstrakcja cyfrowa / layout Document AI:
uv run python -m src.pipeline extract --book ksiazka

# 3. Uruchomienie czyszczenia i normalizacji Markdown:
uv run python -m src.pipeline clean --book ksiazka

# 4. Audyt jakościowy (wyszukiwanie anomalii i braków):
uv run python -m src.pipeline audit --book ksiazka

# 5. Tworzenie i weryfikacja snapshotu integralności:
uv run python -m src.pipeline snapshot --book ksiazka

# 6. Kompilacja do walidującego się standardu EPUB 3:
uv run python -m src.pipeline build --book ksiazka

# 7. Rygorystyczna walidacja kontenera EPUB 3:
uv run python -m src.pipeline epub-validate --epub output/epub/ksiazka.epub

# 8. Eksport bazy wiedzy pod RAG/LLM:
uv run python -m src.pipeline rag-export --input-md output/markdown/ksiazka.md

# 9. Otwarcie folderu z gotowym e-bookiem w Windows:
uv run python -m src.pipeline open-windows --folder epub

# 10. Uruchomienie pełnego zestawu testów jednostkowych:
uv run pytest
```

