# PDF to EPUB Converter (Modern Document AI + Pandoc)

Nowoczesny, powtarzalny pipeline do ekstrakcji treści ze skanów PDF (książek, artykułów, publikacji kulinarnych z wielokolumnowym tekstem, tabelami i grafikami) do edytowalnego formatu **Markdown** (jako Source of Truth / RAG) oraz kompilacji do standardu **EPUB 3** gotowego do odczytu na czytnikach e-booków (Kindle, Kobo, PocketBook, Apple Books).

## 🚀 Stos technologiczny
* **Język i środowisko:** Python 3.13+ zarządzany przez [`uv`](https://github.com/astral-sh/uv).
* **Document AI & Ekstrakcja:** [`docling`](https://github.com/DS4SD/docling) (analiza układu RT-DETR, detekcja kolumn, tabel i grafik) + [`pypdfium2`](https://github.com/pypdfium2-team/pypdfium2) (inteligentna detekcja warstwy tekstu).
* **Sanitizer & Normalizer:** Dedykowany moduł `src/cleaner.py` (de-hyphenation, normalizacja ułamków kulinarnych i miar, filtr szumu skanera, deduplikacja nagłówków).
* **Kompilator EPUB:** [`pandoc`](https://pandoc.org/) 3.13+.
* **CLI & Terminal:** [`typer`](https://typer.tiangolo.com/) oraz [`rich`](https://github.com/Textualize/rich).
* **Testy:** [`pytest`](https://docs.pytest.org/) (43 testy jednostkowe i integracyjne z pełną obsługą znaków diakrytycznych i WSL).
* **Interfejs Wizualny & Studio:** [Astro 7](https://astro.build/) (SSR Node) + [Tailwind CSS v4](https://tailwindcss.com/) na porcie `4343` z integracją lokalnego LLM (Ollama / RTX 4080 Super), selektorem książek, Autopilotem naprawczym oraz 1-klikowym eksportem do Windows na WSL.

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

* **Krok 3: Audyt jakościowy (wykrywanie anomalii OCR i braków):**
  ```bash
  uv run python -m src.pipeline audit
  ```

* **Krok 4: Inspekcja i naprawa uszkodzonych akapitów przez Agenta AI:**
  ```bash
  # Podgląd uszkodzonego akapitu z kontekstem
  uv run python -m src.pipeline inspect-anomaly 51

  # Zastosowanie poprawki (z automatyczną kopią .bak)
  uv run python -m src.pipeline patch 51 --text "Zrekonstruowany tekst..."
  ```
  *(Zobacz dedykowany plik `PROMPT_FOR_AGENT.md` zawierający gotowy prompt do wklejenia w VS Code)*.

* **Krok 5: Kompilacja do EPUB i eksport do Windows:**
  ```bash
  # Standardowa kompilacja (z automatycznym kopiowaniem do Pobranych w Windows)
  uv run python -m src.pipeline build --book ksiazka

  # Opcjonalnie z własną okładką:
  uv run python -m src.pipeline build --book ksiazka --cover sciezka/do/okladki.jpg

  # Otwórz folder wyjściowy bezpośrednio w Eksploratorze Windows (z WSL):
  uv run python -m src.pipeline open-windows --folder epub
  ```

* **Krok 6: Czyszczenie plików roboczych po skończonej pracy (oszczędność dysku):**
  ```bash
  # Usuwa pliki robocze (.md, .bak, raporty audytu) dla danej książki, zachowując gotowy .epub:
  uv run python -m src.pipeline purge --book ksiazka --keep-epub

  # Pełne wyczyszczenie wszystkich plików roboczych:
  uv run python -m src.pipeline purge --all
  ```

### 3. Nowoczesny Interfejs Wizualny (Web Studio UI)

Projekt posiada dedykowany interfejs webowy zbudowany w **Astro 7 + Tailwind CSS v4**, działający na porcie **`4343`** (wygodnym i wolnym od konfliktów z innymi narzędziami dev):

```bash
cd web
npm install
npm run dev   # Serwer deweloperski na http://localhost:4343
# lub
npm run start # Serwer produkcyjny SSR

# Zatrzymanie serwera działającego w tle na porcie 4343:
fuser -k 4343/tcp
# lub:
pkill -f "astro"
```

#### Funkcje Web Studio:
* **Wybór pliku:** Przełączanie między różnymi książkami w `output/markdown/` i `input/`.
* **Katalog Wad OCR:** Wyszukiwanie, filtrowanie po wadze (High, Medium, Low), podgląd diff uszkodzonych słów (np. `ha^been`, `1 2 tsp`, `in die oven`) oraz edytor bezpośredni.
* **Autopilot Naprawczy:** Rozwiązuje problem ręcznego klikania 113 razy — automatycznie iteruje przez uszkodzone akapity i nanosi poprawki.
* **Integracja z lokalnym Ollama (RTX 4080 Super):** Jeśli Ollama działa w tle, web UI łączy się z modelem (np. `qwen2.5:14b`), rekonstruując usterki bez płatnych tokenów API.
* **Bezpośrednie pobieranie EPUB:** Jedno kliknięcie, aby pobrać gotowy `.epub`.

---

### 4. Wykorzystanie lokalnego GPU (NVIDIA RTX 4080 SUPER 16GB + Ollama na Windows/WSL)

Posiadając kartę **NVIDIA GeForce RTX 4080 SUPER (16 GB VRAM)**, masz do dyspozycji potężną platformę do lokalnego wnioskowania AI:
* **Zero opłat:** Brak konieczności płacenia za API (OpenAI/Anthropic).
* **Rekomendowany model:** `qwen2.5:14b-instruct` (lub `llama3.1:8b`). Plik modelu 14B w kwantyzacji Q4 zajmuje około 9 GB; zużycie VRAM zależy również od kontekstu. Szybkość i jakość trzeba zmierzyć na rzeczywistych danych. Ten model przyjmuje tekst, a nie obrazy stron.

#### Konfiguracja Ollama na Windows dla środowiska WSL:
Gdy Ollama jest zainstalowana bezpośrednio w Windows, domyślnie nasłuchuje tylko na `127.0.0.1` wewnątrz Windows, odrzucając pakiety z wirtualnej sieci WSL. Aby umożliwić połączenie:

1. **Udostępnij port dla WSL:** Otwórz **PowerShell** w Windows i wklej:
   ```powershell
   [Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0", "User")
   ```
2. **Zrestartuj Ollama:** Zamknij Ollama z zasobnika systemowego Windows (koło zegarka ➔ *Quit Ollama*) i uruchom ją ponownie.
3. **Uruchom model:**
   ```powershell
   ollama run qwen2.5:14b
   ```
4. **Weryfikacja w Web Studio:** Otwórz `http://localhost:4343`. Wskaźnik w prawym górnym rogu zmieni się na zielony **[Ollama Online]**.
   Silnik regułowy poprawia tylko znane wzorce. Nie rekonstruuje dowolnego uszkodzonego tekstu i nie gwarantuje kompletnej korekty.

---

### 5. Uruchamianie testów
```bash
uv run pytest
```


## Naprawa OCR z kontrolą źródła

Przed naprawą istniejącego Markdowna nie uruchamiaj `extract` ani `all`.

1. „AI Fix” oraz Autopilot wysyłają **pełny akapit**, nie skrót z raportu.
2. Model zwraca JSON z tekstem i informacją o niepewności. Drobna korekta tekstowa przechodzi kontrolę liczb, jednostek i podobieństwa. Oceny są heurystykami, nie dowodem braku halucynacji.
3. Gdy pierwsza propozycja jest niepewna, nie zmienia tekstu lub nie przechodzi kontroli, `src/source_ocr.py` odnajduje fragment w warstwie tekstowej PDF i odczytuje ponownie tylko dopasowaną stronę lub dwie kolejne strony. Obraz jest prostowany, odczytywany przez EasyOCR, a pola tekstowe porządkowane. Nie jest to ponowna ekstrakcja książki: proces **nie zapisuje Markdowna**.
4. Model otrzymuje niezależny odczyt skanu. Usunięcie fałszywych cyfr starego OCR jest dopuszczalne tylko po kontroli względem tego odczytu. Samo oznaczenie przez model „to błąd OCR” nie wystarcza.
5. Odnośniki `![...](assets/...)` pozostają poza modelem, w tej samej kolejności i między tymi samymi fragmentami tekstu. Zapis AI, ręczny i wsadowy odrzuca usunięcie lub zmianę grafik.
6. Zapis sprawdza aktualność akapitu, zachowuje jego indeks i tworzy `.bak`. Błędy i brak zmiany nie są liczone jako poprawione rekordy. Odrzucona propozycja pozostaje widoczna do kontroli.

Wymagania: `input/<nazwa>.pdf`, istniejące modele EasyOCR (`pl`, `en`) oraz działająca Ollama. Odczyt źródła używa CPU i nie pobiera modeli automatycznie. Cache jest w `output/source_cache/`; zmiana pliku PDF unieważnia go. Nieczytelne granice, powtarzające się fragmenty i niska pewność odczytu powodują odrzucenie propozycji. Dokument bez użytecznej warstwy tekstowej nie może być automatycznie dopasowany przez ten mechanizm. Złożone tabele i kolumny nadal wymagają sprawdzenia.

### Uruchamianie i wersja serwera

`npm run build` nie przeładowuje już działającego `astro preview`. Po zmianach zatrzymaj serwer preview tego projektu i uruchom go ponownie (Astro 7 może działać w tle, więc samo zamknięcie terminala nie wystarcza):

```bash
cd web
npm run stop
npm run build
npm run start
npm run status
```

Do pracy nad kodem używaj `npm run dev`; sprawdź port wypisany przez Astro. Gdy 4343 jest zajęty, serwer deweloperski może przejść na kolejny port. API `/api/status?book=ksiazka` zwraca `repairVersion: "source-ocr-v1"` w wersji z naprawą opartą o źródło.

### Testy bez ukrywania błędów

```bash
UV_CACHE_DIR=/tmp/pdf-epub-uv-cache uv run pytest
cd web
npm test
# Wymaga działającej aplikacji, Ollama qwen2.5:14b i oryginalnego PDF:
npm run test:live
# Rzeczywisty zapis do tymczasowej książki i test konfliktu (oryginał pozostaje bez zmian):
npm run test:live:write
# Inny port testowanego serwera:
STUDIO_TEST_URL=http://127.0.0.1:4397 npm run test:live
```

`web/tests/fixtures/foreword.json` zawiera oryginalny uszkodzony tekst oraz osobną transkrypcję wzorcową ze skanu. **Wzorzec nie jest wysyłany modelowi.** Test rzeczywisty wysyła `apply:false`, porównuje wynik słowo po słowie, kontroluje grafiki i sumę SHA-256 książki. Próg 3,5% błędów słownych dotyczy wyłącznie tego przypadku regresji; nie oznacza certyfikacji jakości całej książki. Raport z pełnym wynikiem trafia do `web/tests/artifacts/live-repair.json`. Testy jednostkowe z atrapą modelu sprawdzają zachowanie programu, nie skuteczność rzeczywistej rekonstrukcji.

Dokumentacja implementacji: [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs), [Ollama vision](https://docs.ollama.com/capabilities/vision), [EasyOCR API](https://www.jaided.ai/easyocr/documentation/).

Krótki dwujęzyczny nagłówek jest osobnym przypadkiem regresji: `cd web && npm run test:live:heading`. Test tworzy tymczasową książkę z sąsiednimi akapitami; nie wysyła modelowi poprawionego nagłówka. API pobiera kontekst z Markdowna po indeksie, a wyszukiwanie źródła używa sąsiadów, gdy sam tytuł jest zbyt krótki. Nagłówki zawsze wymagają sprawdzenia źródła; dopasowanie obejmuje do trzech wierszy, aby zachować angielską i polską nazwę.
