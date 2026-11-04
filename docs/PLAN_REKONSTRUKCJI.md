# Plan wiarygodnej konwersji PDF → Markdown → EPUB

Stan rozpoznania: 2026-11-03. Status: **gotowy do realizacji**. Dokument jest planem, nie raportem potwierdzającym jakość nowej ekstrakcji. Podczas przygotowania planu nie wykonano nowej ekstrakcji produkcyjnej, nie zmieniono książki i nie pobrano nowych modeli.

Punktem wejścia do realizacji jest checklista w sekcji 13. Aktualizujemy ją po każdym zakończonym zadaniu, podając dowód wykonania. Następne zadanie: **R05 — wzorce ze skanów**.

## 1. Decyzja architektoniczna

Najpierw naprawiamy ekstrakcję i pomiar jakości. Model tekstowy nie może odtwarzać brakujących zdań na podstawie samego uszkodzonego OCR. Docling pozostaje pierwszym kandydatem do analizy struktury. Wynik musi przechowywać pochodzenie tekstu: stronę, obszar obrazu, kolejność i konfigurację odczytu. Wątpliwe obszary mogą trafić do modelu wizyjnego. Żaden silnik nie jest ogłoszony zwycięzcą przed porównaniem na skanie.

Akceptowany Markdown w output/markdown/ pozostaje źródłem prawdy zgodnie z AGENTS.md. JSON ekstrakcji i obrazy stron są materiałem dowodowym i formatem roboczym, a nie konkurencyjną wersją zaakceptowanej treści. Build czyta Markdown i nie odtwarza go z JSON-a.

## 2. Ustalenia z kodu, środowiska i dokumentacji

- PDF ma 914 stron i 38 385 114 bajtów. Docling 2.146.0, docling-core 2.105.0, EasyOCR 1.7.2, pypdfium2 5.14.0.
- has_text_layer() wybiera kilka stron i zwraca True, gdy jedna ma ponad 50 znaków. To sprawdzenie obecności tekstu, nie jego jakości. max_check_pages nie steruje faktycznie wyborem stron.
- get_converter() ustawia do_ocr, lecz nie ustawia trybu pełnego zastąpienia warstwy tekstowej. Dotychczasowe --ocr nie jest więc jednoznacznym poleceniem zastąpienia starego OCR.
- W lokalnie zainstalowanej wersji sprawdzono tworzenie EasyOcrOptions(mode=OcrMode.FULL_PAGE, lang=['pl','en']). Dokumentacja traktuje force_full_page_ocr jako starszy interfejs do tego trybu.
- Docling ma replace_broken_text_layer, ale jego dokumentacja jawnie opisuje niewykrywanie niektórych błędów liter i ich kolejności. Nie może to być jedyna kontrola tej książki.
- Obecny eksport zapisuje Markdown i grafiki, lecz nie utrwala dokumentu strukturalnego oraz mapowania wszystkich elementów na strony.
- Oddzielny source_ocr.py ponownie ustala kolejność słów z geometrii pól. Widzieliśmy czytelne skany, ale zły porządek tekstu w rekonstrukcji. Ten komponent wymaga osobnego testowania.
- PyTorch ma build CUDA 13.2, ale torch.cuda.is_available() zwróciło False. Nie ustalono przyczyny. Nie wolno z tego wyciągać wniosku o braku GPU w komputerze. Ollama na Windows używała GPU; te środowiska trzeba mierzyć oddzielnie.
- RapidOCR jest importowalny, onnxruntime nie; PaddleOCR/Paddle, tesseract, ocrmypdf oraz epubcheck nie były dostępne w sprawdzonym środowisku. Istnieje komenda Docker Desktop, ale nie potwierdzono dostępności jego silnika.

Źródła: [Docling OCR](https://docling-project.github.io/docling/concepts/OCR/), [opcje pipeline](https://docling-project.github.io/docling/reference/pipeline_options/), [model dokumentu](https://docling-project.github.io/docling/concepts/docling_document/).

## 3. Etap A — zabezpieczenie stanu i jednoznaczna wersja

Przed jakimkolwiek przebiegiem zapisujemy snapshot PDF, Markdowna, assets, metadanych, CSS i dziennika zmian. Manifest zawiera SHA-256, wersje narzędzi i konfigurację. Nie wykonujemy purge ani nadpisującego all/extract na istniejącym Markdownie.

Wyniki porównań trafiają do output/benchmarks/<run_id>/; pełna nowa ekstrakcja do output/candidates/<run_id>/. Dotychczasowy Markdown i EPUB nie są ruszane przez benchmark.

Dodajemy pojedyncze zadanie zapisujące daną książkę, kontrolę wersji przy zapisie i unieważnianie raportów niezgodnych z SHA-256 Markdowna. Wynik interfejsu przechowuje wersję wejściową i wynikową; stara karta ma status historyczny, a nie bieżące „zapisano”. Proces może być wznowiony bez ponownego OCR poprawnych bloków.

Kryterium ukończenia: test współbieżnej zmiany pliku odrzuca stary zapis, a odświeżenie strony nie przypisuje starych indeksów do nowych akapitów. Zachowano snapshoty i możliwy jest rollback.

## 4. Etap B — reprezentatywny zestaw odniesienia

Wybieramy 30 stron: 20 do strojenia i 10 odłożonych do końcowego testu. Znane przypadki 25–26, 33–34, 40, 54 i 58 trafiają do części rozwojowej. Pozostałe strony dobieramy z dalszych części książki, tak aby objąć:

- tekst ciągły i przejścia pomiędzy stronami;
- jedno- i dwukolumnowe przepisy;
- listy składników z ułamkami, skrótami, temperaturami i zakresami;
- dwujęzyczne tytuły oraz polskie znaki;
- tabele, podpisy i ilustracje;
- krzywe strony, wygięcie przy grzbiecie i drobny druk.

Tworzymy transkrypcje oraz mapę bloków/kolumn na podstawie obrazów. To osobne wzorce testowe; nie są podawane modelowi i nie są wpisywane do książki podczas testów. Niepewny znak we wzorcu wymaga sprawdzenia na powiększeniu, a nie zgadywania. Numer strony PDF i numer drukowany zapisujemy oddzielnie.

Kryterium ukończenia: każdy blok ma odniesienie do skanu, ustaloną kolejność i oznaczenie istotnych ilości/miar. Zestaw odłożony nie jest używany do poprawiania promptów.

## 5. Etap C — porównanie wariantów bez LLM

| Wariant | Cel | Koszt i ograniczenia |
| --- | --- | --- |
| Obecna ekstrakcja natywna | Punkt odniesienia pokazujący skalę problemu | Nie zakładamy, że istniejący tekst jest poprawny |
| Docling + pełny EasyOCR pl/en | Pierwszy wariant naprawczy w obecnym stosie | Sprawdzić kolejność, znaki i czas; modele są już lokalnie |
| Docling + pełny Tesseract CLI eng/pol | Niezależny odczyt tych samych obszarów | Wymaga instalacji silnika i danych językowych |
| Docling + RapidOCR Latin | Dodatkowy kandydat w tym samym formacie dokumentu | Wymaga właściwego backendu i modelu; nie wystarczy import pakietu |

Pierwsze uruchomienie obejmuje 6–8 trudnych stron, aby wyeliminować niedziałającą konfigurację. Warianty działające porównujemy na 20 stronach rozwojowych. Nie mnożymy kombinacji w nieskończoność: jeżeli co najmniej jeden wariant spełnia kryteria, przechodzi do testu odłożonego.

Docelowa konfiguracja musi jawnie określać sposób użycia OCR: native, full_page albo kontrolowany wybór per strona. Przedstawione nazwy odnoszą się do planowanego interfejsu projektu, a nie gotowych nowych komend. Zapisujemy rzeczywiste ustawienia Doclinga w manifeście.

Dla znanej uszkodzonej warstwy tekstowej startujemy od FULL_PAGE. Docling odpowiada za strukturę, kolumny i kolejność; nie składamy całej strony samodzielnym sortowaniem wszystkich słów po współrzędnej Y. Takie sortowanie miesza kolumny. Wszystkie silniki zwracają adapterowi tekst, geometrię i dostępne wskaźniki pewności. Wyniki pewności różnych silników nie są bezpośrednio porównywalne.

Opcjonalny kandydat po niepowodzeniu powyższych: PP-StructureV3. Ma mechanizmy układu, tabel i kolejności wielu kolumn. Trzeba wybrać model rozpoznający polskie znaki; domyślnego modelu chińsko-angielskiego nie traktujemy jako właściwej konfiguracji tej książki. W razie konfliktu zależności uruchamiamy go w odizolowanym środowisku, nie zmieniając Pythona całego projektu.

Źródła: [Docling OCR](https://docling-project.github.io/docling/concepts/OCR/), [PP-StructureV3](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PP-StructureV3.en.md), [języki PP-OCRv5](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.en.md).

## 6. Etap D — przygotowanie obrazów i struktura pośrednia

Testujemy rasteryzację 300 DPI i w razie potrzeby 400 DPI dla drobnego druku; to ustawienia próbne, nie gwarancja poprawy. Zapisujemy rzeczywistą skalę i transformację współrzędnych. Obrót, prostowanie, usuwanie marginesów i ewentualne dewarping muszą mieć obraz przed/po. Nie wolno obciąć przypisu, cyfr, ilustracji ani części drugiej kolumny.

Jeśli Docling nadal ma złą kolejność, pracujemy na wykrytych regionach: osobno kolumna/blok, a wewnątrz bloku wiersze. Dla liczb i ułamków używamy powiększonego wycinka. Rozbieżność dwóch odczytów jest sygnałem do kontroli, nie automatycznym głosowaniem większości.

OCRmyPDF pozostaje opcją do przygotowania kopii PDF: redo wymienia starą niewidoczną warstwę, force rasteruje treść. Nie wykonujemy force na oryginale i nie włączamy skip dla stron ze znaną złą warstwą. Nie jest to samodzielne rozwiązanie struktury Markdowna.

Po ekstrakcji utrwalamy DoclingDocument JSON, obrazy/wycinki oraz sidecar: run_id, source_pdf_sha256, page_no, printed_page, block_id, typ, bbox, reading_order, tekst surowy, silnik/model, wersja i transformacje. Identyfikator bloku jest stabilny w obrębie przebiegu. Zmiana segmentacji w nowym przebiegu wymaga jawnego mapowania, a nie obietnicy identycznych ID.

Źródła: [DoclingDocument](https://docling-project.github.io/docling/concepts/docling_document/), [OCRmyPDF — tryby przetwarzania](https://ocrmypdf.readthedocs.io/en/stable/advanced.html).

## 7. Etap E — rola modeli językowych i wizyjnych

Qwen2.5:14b przestaje pełnić rolę domyślnego autora rekonstrukcji całych stron. Opcjonalna korekta tekstowa działa na jednym ustalonym bloku, z sąsiadami tylko jako kontekstem. Model nie ma prawa tworzyć składników, dopisywać jednostek ani zastępować ubytków prawdopodobnym zdaniem. Limity generacji wynikają z rozmiaru bloku; brak kompletnej odpowiedzi nie jest sukcesem.

Dla trudnych obszarów porównujemy VLM czytający prawdziwy wycinek obrazu. Pierwszy konkretny kandydat: qwen3-vl:8b-instruct-q4_K_M w Ollama. Oficjalny tag ma 6,2 GB i przyjmuje obrazy. Rozmiar pliku nie jest zużyciem VRAM: pamięć, wielkość wycinka i kontekst trzeba zmierzyć na RTX 4080 SUPER 16 GB. Przy zbyt dużym koszcie sprawdzamy mniejszy wariant, nie obiecując równoważnej jakości.

Model wizyjny dostaje obraz, krótki zakres zadania, granice bloku i polecenie wiernej transkrypcji. Nie dostaje transkrypcji wzorcowej. Sprawdzamy możliwości przez /api/show oraz wersję serwera; oficjalna karta modelu wskazuje minimum Ollama 0.12.7. Początkowo modele uruchamiamy kolejno, aby nie zająć jednocześnie VRAM przez OCR i LLM.

VLM jest kandydatem do pomocy, nie dowodem prawdy. Jego zgodność z innym modelem nie dowodzi poprawności. Każda zmiana ilości lub jednostki nadal wymaga dowodu ze skanu albo zatwierdzenia. Przykład 375° → 3758 → 375°F pozostaje obowiązkowym testem.

Źródła: [Ollama vision](https://docs.ollama.com/capabilities/vision), [karta wybranego modelu](https://ollama.com/library/qwen3-vl:8b-instruct-q4_K_M), [przykład Docling OCR + VLM](https://github.com/docling-project/docling/blob/main/docs/examples/post_process_ocr_with_vlm.py).

## 8. Etap F — walidacja wierności i progi przejścia

Mierzymy osobno:

- CER i WER względem transkrypcji skanu, z udokumentowaną normalizacją;
- brakujące i dodatkowe słowa/bloki oraz długie powtórzenia;
- kolejność regionów i wierszy, w tym pary bloków przechodzących przez stronę;
- liczby, ułamki, zakresy, jednostki i skojarzenie ilości z właściwym składnikiem;
- komórki i nagłówki tabel, poziomy nagłówków, podpisy oraz grafikę;
- udział poprawnie automatycznie zaakceptowanych bloków, fałszywe akceptacje i liczbę bloków do kontroli;
- czas ścienny strony/regionu, p50/p95, RAM/VRAM, liczbę ponownych prób i timeoutów.

Proponowane początkowe bramki, a nie wyniki już osiągnięte:

| Miara na zbiorze odłożonym | Wymaganie |
| --- | --- |
| Średni CER / WER | ≤0,5% / ≤1%; każda kategoria raportowana osobno |
| Najgorsza testowana strona | WER ≤2%, bez błędów krytycznych; inaczej kontrola i brak promocji wariantu |
| Istotne liczby i przypisanie jednostek/składników | Zero wykrytych błędów na sprawdzonym zbiorze |
| Pominięcia całych zdań, bloków, kolumn i długie dopisane powtórzenia | Zero na sprawdzonym zbiorze |
| Sprawdzone tabele / figury / nagłówki | Kompletność i poprawne powiązania w całym zestawie odniesienia |
| Linki do assetów i duplikaty ID | Brak uszkodzonych linków i konfliktów |
| Fałszywe automatyczne akceptacje błędów krytycznych | Zero w testowanej próbce |

Powyższe progi przyjmujemy jako początkowe wymagania realizacji. Ich zmiana wymaga jawnego uzasadnienia w dzienniku decyzji i ponownej oceny zakresu, a nie ukrytej zmiany po obejrzeniu wyniku. Zero błędów w próbce nie dowodzi zera w całej książce. Wynik OCR confidence, podobieństwo bigramów ani poprawny JSON nie są miernikiem zgodności znaczeniowej. Jeśli nie ma wariantu spełniającego wymagania, pełny przebieg nie startuje: zawężamy problem do konkretnych typów stron, rozważamy inny OCR/VLM lub pracę ręczną na wybranych blokach.

Źródło pomocnicze: [rodzaje confidence w Docling](https://docling-project.github.io/docling/concepts/confidence_scores/).

## 9. Etap G — migracja do Markdowna i interfejs

Po wyborze konfiguracji przetwarzamy pilot 50 kolejnych, wcześniej niestrojonych stron. Sprawdzamy akapity na granicach stron, tabele, kompletność i wszystkie zgłoszone rozbieżności krytyczne. Dopiero potem pełne 914 stron w katalogu kandydata, z checkpointami i wznowieniem per strona/blok.

Budujemy kandydacki Markdown z zachowaniem kolejności, list składników i nagłówków. Proste tabele mogą być Markdown; scalone komórki wymagają formatu, który ich nie spłaszczy, np. kontrolowanego HTML w Markdownie. Ilustracje mają manifest z SHA-256; odróżniamy rzeczywiste ilustracje od fragmentów całej strony błędnie rozpoznanych jako grafiki. Zachowujemy docelowe linki assets/<nazwa>.png.

Obecnych poprawek nie przenosimy ślepo z .bak. Zachowujemy je jako historię i mapujemy kandydatów według strony, obszaru i treści. Zweryfikowane poprawki można przenieść; konflikty i wcześniejsze fantazje modelu trafiają do kontroli. Przy zatwierdzeniu sprawdzamy ponownie wersję produkcyjnego Markdowna. Publikacja kandydata jest pojedynczą operacją z możliwością odtworzenia starego zestawu MD + assets.

UI pokazuje skan obszaru, surowy OCR, propozycję i odrębny status: odczytano, zaproponowano, odrzucono, zapisano, zweryfikowano. „Zapisano” nie oznacza „tekst zgodny ze skanem”. Historia wyniku jest zapisywana na dysku, nie tylko w repairStates przeglądarki. Przeglądający nie musi kopiować fragmentów do czatu. Wskaźnik procentowy przedstawia zakończone bloki, a przerwanie zadania blokuje kolejne zapisy.

## 10. Etap H — EPUB i RAG

Build uruchamiamy dopiero dla zatwierdzonego Markdowna. Pandoc tworzy EPUB 3, a EPUBCheck osobno sprawdza zgodność formatu. Kontrolujemy spis treści, hierarchię, tabele, grafiki i próbkę treści w czytniku. Poprawny EPUBCheck nie dowodzi poprawności tekstu. Eksport do Windows następuje dopiero po tych kontrolach.

RAG otrzymuje tę samą zaakceptowaną treść z informacją o stronie/sekcji. Dzielenie na fragmenty nie może odłączyć ilości od składnika ani nagłówka od treści przepisu. Nie tworzymy niezależnie „naprawionej” wersji RAG rozbieżnej z Markdownem.

Źródło: [EPUBCheck](https://www.w3.org/publishing/epubcheck/).

## 11. Zakres zmian w kodzie

- src/pipeline.py: tryby odczytu, katalog kandydata, pełny OCR, checkpointy, eksport struktury, jawne zabezpieczenie przed nadpisaniem.
- Nowe moduły src/extraction_quality.py i src/benchmark.py: inwentaryzacja stron, metryki i porównanie silników; to proponowane pliki, obecnie nie istnieją.
- src/source_ocr.py: wykorzystanie pochodzenia bloku zamiast wyszukiwania go od nowa w uszkodzonym tekście, odczyt obszarów i kontrola geometrii.
- src/cleaner.py: reguły mają nie zmieniać ilości, nazw, grafiki i treści poza rozpoznanym wzorcem; regresje na realnych przypadkach i idempotencja.
- src/auditor.py: oprócz anomalii znakowych kontrola kompletności i struktur, wersja treści w raporcie.
- web/src/utils/metrics.ts i repair-policy.ts: miary dopasowane do skanu, powtórzenia/pominięcia, liczby powiązane z jednostkami; brak utożsamiania podobieństwa z prawdą.
- API i UI: zadania, historia wyników, odwołanie do wycinka, identyfikatory bloków i konflikty wersji.
- AGENTS.md i README.md: usunięcie gwarancji „perfekcyjnego podziału kolumn” i „bezstratnej ekstrakcji” wynikających wyłącznie z obecności tekstu; zachowanie zasady nienadpisywania zaakceptowanego Markdowna.

## 12. Kolejność decyzji i koszty

Najpierw snapshot i spójność wersji, następnie wzorce i benchmark Doclinga. Dopiero wyniki decydują o dodatkowym OCR/VLM. Nie instalujemy jednocześnie wszystkich alternatyw ani nie zmieniamy domyślnego modelu na podstawie pojedynczego tytułu.

Plan pozostaje lokalny. Koszt to czas GPU/CPU, pobranie modeli i miejsce na obrazy oraz wyniki. Rozmiary i limity cache mierzymy na próbce; brakujące obrazy można odtworzyć z PDF, zaakceptowanego Markdowna nie usuwamy automatycznie. WSL CUDA i dostępność Docker są osobnymi testami środowiska, nie warunkiem cichego przełączenia na wolny CPU.

Nie ma jeszcze uczciwej estymacji czasu całej książki. Powstanie po pomiarze czasu stron i częstości fallbacków, z uwzględnieniem wznowień i przeglądu rozbieżności. Czas wdrożenia zależy od wyniku benchmarku; obietnica jednej kolejnej poprawki lub konkretnej liczby godzin byłaby bezpodstawna.

Warunek zakończenia projektu: wybrany, powtarzalny proces przechodzi odłożony zestaw, pełna książka ma raport pokrycia i rozstrzygnięte błędy krytyczne, a EPUB przechodzi kontrolę formatu oraz przegląd reprezentatywnych stron. Nierozstrzygnięte obszary pozostają jawne i nie są oznaczane jako naprawione.

## 13. Checklista wykonawcza i zależności

Legenda: `[ ]` — do zrobienia, `[x]` — zakończone z dowodem. Sam działający kod ani zielone testy jednostkowe nie zamykają zadania wymagającego pomiaru na skanie. Zadania wykonuje agent implementujący; testy i przegląd obrazu są osobnymi czynnościami, nawet gdy wykonuje je ten sam agent. Nie zakładamy istnienia niezależnego zespołu testerów.

- [x] R00 — rozpoznanie kodu i dokumentacji. Dowód: sekcje 2–12, sprawdzone opcje zainstalowanego Doclinga i zapisane przypadki wcześniejszych niepowodzeń. Nie oznacza to walidacji nowej ekstrakcji.
- [x] R01 — snapshot i inwentaryzacja. Dowód: zaimplementowano moduł src/snapshot.py z walidacją Pydantic v2 oraz komendę CLI snapshot. Wygenerowano manifest baseline w output/snapshots/20261104_075036_ksiazka/manifest.json z sumami SHA-256 dla 119 plików (PDF, MD, BAK, metadata, css, audit, 113 assetów), zarejestrowano stan środowiska (Docling 2.146.0, Ollama qwen2.5:14b, CUDA info) oraz zweryfikowano spójność poleceniem weryfikacji i testami jednostkowymi w tests/test_snapshot.py.
- [x] R02 — wspólna kontrola zapisu. Dowód: zaimplementowano moduł src/write_control.py z blokadą pojedynczego zapisującego (.lock z fcntl.flock), kontrolą expected_sha256, weryfikacją oryginalnego tekstu akapitu, ochroną grafik (AssetIntegrityError) i atomowym zapisem przez plik tymczasowy. Zaktualizowano web/src/utils/repair-store.ts oraz web/src/pages/api/patch.ts pod kątem blokad i wersji. Powiązano raport audytu z SHA-256 Markdowna (src/auditor.py) oraz zaimplementowano sprawdzanie świeżości raportu. Dodano testy jednostkowe w tests/test_write_control.py (wszystkie 63 testy pytest i 24 testy node przechodzą).
- [x] R03 — raporty i historia związane z wersją. Dowód: zaimplementowano moduł src/history.py z modelami Pydantic v2 (HistoryRecord, HistoryLedger), trwały zapis na dysku w output/history/<book>_history.json, mechanizm sprawdzania kotwic i wykrywania przesunięć indeksów (verify_index_anchors), komendę CLI history w src/pipeline.py oraz endpoint web /api/history.ts. Zapewniono automatyczną rejestrację decyzji przy zapisach oraz pełne testy przesunięcia w tests/test_history.py (wszystkie 67 testów pytest i 24 testy node przechodzą). Bramka Gate G0 osiągnięta pomyślnie.
- [x] R04 — manifest stron testowych. Dowód: zaimplementowano moduł src/benchmark_manifest.py z modelami Pydantic v2 (TestPageEntry, BenchmarkPagesManifest), utworzono kanoniczny manifest 30 stron w benchmarks/fixtures/test_pages_manifest.json (dokładnie 20 stron dev i 10 stron holdout, zbiory w 100% rozłączne, obejmujące 1- i 2-kolumnowe układy, ułamki, temperatury, słowniki i tabele), dodano komendę CLI test-manifest w src/pipeline.py oraz testy jednostkowe w tests/test_benchmark_manifest.py (wszystkie 73 testy pytest i 24 testy node przechodzą).
- [ ] R05 — wzorce ze skanów. Przygotować transkrypcje, granice i kolejność bloków, tabele, figury oraz oznaczone ilości. Zapisać nierozstrzygnięte znaki. Wynik: sprawdzony zestaw odniesienia; żadna transkrypcja nie trafia do żądania modelu. Zależność: R04.
- [ ] R06 — adapter ekstrakcji i pochodzenie bloków. Zdefiniować wspólny zapis tekstu, geometrii, kolejności, silnika i przebiegu. Zachować DoclingDocument JSON. Wynik: round-trip struktury i mapa bloku do obrazu strony. Zależność: R01.
- [ ] R07 — jawne tryby OCR i eksport kandydata. Wprowadzić pełny OCR zastępujący starą warstwę, natywny punkt odniesienia i oddzielny katalog wyniku. Zablokować przypadkowe nadpisanie książki. Wynik: rzeczywista konwersja kilku stron z manifestem konfiguracji. Zależność: R06.
- [ ] R08 — pomiar jakości i runner porównań. Zaimplementować CER/WER, pominięcia, powtórzenia, kolejność, liczby z jednostkami, struktury, czas i pamięć. Wynik: raport per strona i per wariant; test celowo błędnego wyniku wykrywa pominięcia i przekręcone wartości. Zależności: R05, R06.
- [ ] R09 — benchmark podstawowych OCR. Porównać natywny odczyt, pełny EasyOCR i co najmniej jeden niezależny OCR na identycznych wejściach. RapidOCR jest kolejnym kandydatem, jeżeli daje uzasadnioną korzyść. Wynik: wyniki na 20 stronach i decyzja, co przechodzi dalej. Zależności: R07, R08.
- [ ] R10 — geometria i odczyt regionów. Usunąć wykryte mieszanie kolumn/wierszy, sprawdzić prostowanie i skalę obrazu. Wynik: poprawiona kolejność oraz brak ucięcia treści na stronach zawierających problem. Zależności: R06, R09.
- [ ] R11 — decyzja o dodatkowym silniku. Jeśli podstawowe OCR nie spełniają wymagań, ocenić PP-StructureV3 lub inny uzasadniony wariant. Wynik: pomiar albo wpis „niepotrzebne — podstawowy wariant spełnia kryteria” z dowodem. Zależności: R09, R10.
- [ ] R12 — kontrolowany test VLM. Sprawdzić obsługę obrazów i pamięć, następnie transkrypcję wycinków przez model wizyjny. Porównać z tym samym wzorcem, bez zapisu produkcyjnego. Wynik: decyzja o użyciu VLM i dokładny zakres jego zastosowania lub udokumentowane odrzucenie. Zależności: R05, R08, R10.
- [ ] R13 — walidacja i korekta bloków. Dodać kontrolę pominięć, powtórzeń, uciętej generacji, ilości, jednostek i assetów. Limity odpowiedzi powiązać z wielkością bloku. Wynik: wszystkie znane przypadki regresji oraz nowe przykłady błędnej akceptacji przechodzą kontrolę. Zależności: R09–R12.
- [ ] R14 — odłożony test i wybór konfiguracji. Zamrozić konfigurację przed użyciem 10 stron testowych. Wynik: raport spełnienia bramek z sekcji 8 i decyzja o pilocie. Zależności: R13.
- [ ] R15 — UI kontroli i zadania. Pokazać wycinek, OCR, propozycję, historię, powód decyzji i osobne statusy zapisu oraz weryfikacji. Wdrożyć przerwanie i wznowienie bez zapisu po anulowaniu. Wynik: test rzeczywistej przeglądarki na izolowanej książce. Zależności: R03, R06, R13.
- [ ] R16 — pilot 50 nowych stron. Ocenić kompletność i przejścia stron, udział automatycznej akceptacji, pozostałe błędy oraz koszt pełnej konwersji. Wynik: raport i decyzja o pełnym przebiegu. Zależności: R14, R15.
- [ ] R17 — pełne 914 stron w katalogu kandydata. Wykonać przebieg z checkpointami, raportem pokrycia stron i listą niepewnych obszarów. Wynik: kompletny kandydat, bez zamiany produkcyjnego Markdowna. Zależność: R16.
- [ ] R18 — migracja, przegląd i publikacja. Porównać stary i nowy dokument, rozstrzygnąć błędy krytyczne, przenieść tylko zweryfikowane poprawki i sprawdzić manifest grafik. Wynik: atomowe przełączenie logicznej wersji dokumentu oraz przetestowany rollback. Zależności: R02, R17.
- [ ] R19 — EPUB i RAG. Zbudować z zaakceptowanego Markdowna, uruchomić EPUBCheck i przegląd w czytniku; sprawdzić granice fragmentów RAG i pochodzenie. Wynik: EPUB i dane RAG zgodne z zaakceptowanym Markdownem. Zależność: R18.
- [ ] R20 — dokumentacja i zamknięcie. Uaktualnić AGENTS.md, README, opis trybów, komend, ograniczeń i procedur wznowienia. Wynik: końcowy raport jakości i odtwarzalny przebieg bez niejawnych kroków. Zależność: R19.

R13 wymaga rozstrzygnięcia R11 i R12, ale nie wymaga wdrożenia każdego kandydata. Poprawne rozstrzygnięcie może oznaczać odrzucenie wariantu na podstawie wyników. Nie instalujemy narzędzia tylko po to, by odhaczyć zadanie.

## 14. Bramki decyzyjne

| Bramka | Warunek wejścia | Decyzja i wymagany dowód |
| --- | --- | --- |
| G0 — bezpieczne eksperymenty | R01–R03 | Snapshot, kontrola wersji i brak możliwości cichego zapisu starego wyniku |
| G1 — wiarygodny pomiar | R04–R08 | Sprawdzone wzorce i metryki wykrywające znane błędy; poprawny eksport struktury |
| G2 — wybór ekstrakcji | R09–R14 | Konfiguracja zamrożona i wynik na odłożonych stronach spełniający kryteria jakości |
| G3 — pełna książka | R15–R16 | Pilot bez nierozstrzygniętych błędów krytycznych, działające wznowienie i oszacowany koszt |
| G4 — publikacja treści | R17–R18 | Pokrycie wszystkich stron, rozstrzygnięte błędy krytyczne, manifest assetów, brak konfliktu z aktualnym MD |
| G5 — gotowy wynik | R19–R20 | EPUBCheck, kontrola czytnika, zgodność treści RAG, raport końcowy i procedura odtworzenia |

Niepowodzenie G2 prowadzi do poprawy ekstrakcji i nowego eksperymentu. Jeżeli wyniki odłożonego zestawu zostały użyte do strojenia, zestaw staje się rozwojowy; kolejna ocena wymaga nowych, wcześniej nieużywanych stron. Niepowodzenie G3 nie prowadzi do uruchomienia pozostałych 864 stron. Niepowodzenie G4 pozostawia dotychczasową wersję produkcyjną aktywną.

## 15. Artefakty realizacji i zasady aktualizowania postępu

Planowane katalogi:

```text
output/snapshots/<snapshot_id>/        # stan początkowy i manifest
output/benchmarks/<run_id>/            # porównania; nigdy produkcyjny MD
output/candidates/<run_id>/            # nowa wersja książki przed publikacją
output/jobs/<job_id>/                  # checkpointy, decyzje i historia
benchmarks/fixtures/                   # manifesty oraz zweryfikowane wzorce testowe
```

Są to planowane lokalizacje; plan nie tworzy zaimplementowanych komend ani mechanizmów samym podaniem nazw. Duże obrazy i wyniki robocze nie trafiają automatycznie do Git. Referencje, konfiguracje, metryki i niewielkie przypadki regresji powinny być wersjonowane. Ustalenie licencji narzędzi/modeli i kompatybilności pakietów jest częścią wyboru konkretnego wariantu, przed jego użyciem poza eksperymentem.

Po zakończeniu zadania zapisujemy: ID, zakres zmiany, pliki, polecenie lub metodę sprawdzenia, lokalizację raportu, wynik, ograniczenia oraz następne zadanie. Po wykryciu regresji zadanie wraca do otwartego stanu; nie poprawiamy ręcznie wyniku modelu i nie zaliczamy tego jako udanej automatyzacji.

Dziennik decyzji:

| ID | Decyzja | Uzasadnienie | Status |
| --- | --- | --- | --- |
| D01 | Zachować Docling jako pierwszy wariant struktury | Jest w projekcie i obsługuje potrzebne tryby; jego jakość ma zostać zmierzona | Przyjęte do benchmarku |
| D02 | Nie ufać obecności warstwy tekstowej | W badanym PDF tekst jest obecny, lecz wadliwy | Przyjęte |
| D03 | Nie zmieniać domyślnego modelu przed porównaniem | Dwa udane przykłady nie dowodzą jakości na 914 stronach | Przyjęte |
| D04 | Zachować zaakceptowany Markdown jako źródło prawdy | Wymaganie AGENTS.md i ochrona dotychczasowej pracy | Przyjęte |
| D05 | Pełne przetwarzanie dopiero po pilocie | Ograniczenie kosztu i ryzyka powielania błędów | Przyjęte |

## 16. Ryzyka i sposób rozstrzygania

| Ryzyko | Działanie |
| --- | --- |
| Zbyt mała lub łatwa próbka daje pozornie dobry wynik | Dobór według typów stron, zestaw odłożony i pilot nowych stron; raport najgorszych przypadków |
| Prostowanie/wycinanie usuwa tekst lub miesza kolumny | Zachować obrazy przed/po i transformacje, mierzyć kompletność i kolejność |
| Wszystkie silniki mylą ten sam znak | Powiększony skan i jawna kontrola; zgodność silników nie zastępuje dowodu |
| LLM powtarza, ucina lub dopisuje treść | Krótsze bloki, dopasowany limit generacji i testy pominięć/powtórzeń; odrzucenie niepełnej odpowiedzi |
| Python, CUDA lub backend nowego OCR nie pasuje do projektu | Oddzielny eksperyment i przypięte wersje; nie zmieniać całego środowiska bez potrzeby |
| OCR i Ollama konkurują o pamięć GPU | Początkowo wykonywać kolejno, rejestrować pamięć i czas, dopiero potem mierzyć współbieżność |
| Zewnętrzna edycja zmienia książkę podczas zadania | Kontrola wersji i blokada zapisującego; kandydat nie nadpisuje nowszego MD |
| Wcześniejsze poprawki zawierają fantazje modelu | Nie przenosić ich bez potwierdzenia, zachować pochodzenie i konflikt do kontroli |
| Części skanu pozostaną nieczytelne | Jawna lista nierozstrzygniętych obszarów; brak automatycznej deklaracji ukończenia |

Pierwszy przegląd wyników realizacji następuje po R09: porównanie odczytów, błędów i kosztu. Nie czekamy z raportem do końca pełnej książki. Zakres przygotowania niniejszego planu kończy się na gotowej dokumentacji wykonawczej; implementacja rozpoczyna się od R01.
