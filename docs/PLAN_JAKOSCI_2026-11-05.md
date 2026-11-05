# Plan pomiaru i poprawy jakości — 5 listopada 2026

Celem jest wiarygodna treść Markdown/EPUB, zachowanie receptur i grafik oraz przewidywalne działanie panelu. Najpierw trzeba naprawić pomiar: obecna liczba przechodzących testów i werdykt G2 nie potwierdzają jakości książki.

W tej sesji wykonano pomiary i przegląd, bez ponownej ekstrakcji, czyszczenia na dysku i kompilacji książki. Nowe pliki to ten plan, skrypty pomiarowe i wyniki JSON. Wcześniejsze niezacommitowane zmiany pozostawiono bez cofania.

## 1. Stan wyjściowy: fakty i ograniczenia

| Obszar | Wynik z tej sesji | Wniosek |
|---|---|---|
| Python | 178 passed, 7 skipped, 1 failed, 6,51 s | Nie ma obecnie zielonego zestawu; test negatywny zależy od brakującego input/ksiazka.pdf. Nie odtwarzamy tego pliku przez nadpisanie lub ponowną ekstrakcję. |
| Web | 12 plików testowych zaliczonych, około 0,79 s w runnerze | Dobra kontrola lokalnych zachowań, ale nie dowód jakości OCR ani odporności całej aplikacji na awarie. |
| Źródło PDF | Jedyny bieżący PDF ma 914 stron | Manifest benchmarku nadal wskazuje starą nazwę input/ksiazka.pdf. To wymaga poprawnej identyfikacji źródła, nie ręcznego obchodzenia testów. |
| Wzorce | 6 stron dev z 20, 0 holdout z 10; 6/6 hashy obrazów zgodnych | Brakuje 14 anotacji dev i wszystkich 10 anotacji holdout. Nie ma podstaw do deklaracji jakości całej książki. |
| Geometria wzorców | Wszystkie bbox bloków w sześciu plikach GT są puste | Nie mamy kompletnej podstawy do weryfikacji zgodności regionów i geometrii. |
| Cleaner na poprawnym tekście GT | 48 bloków bez zmiany po normalizacji metrycznej; 9 prób ochronnych bez zmiany wykrytych miar | Dobry wynik lokalny, ale zbyt mała próbka do gwarancji bezpieczeństwa reguł. |
| Auditor na GT | Jeden poprawny blok oznaczony za „to do so in” | Reguła szumu krótkich słów daje fałszywy alarm. Nie wolno traktować jej trafień jako automatycznej zgody na usuwanie tekstu. |
| Bieżący MD | 1 977 465 B, 19 632 linie; audyt w pamięci 0,627 s: 1016 podejrzanych akapitów, 6 braków rozdziałów | To wyniki heurystyk, nie 1016 potwierdzonych błędów ani dowód sześciu rzeczywistych braków. Każdy typ wymaga kalibracji. |
| Czyszczenie bieżącego MD w pamięci | 2,013 s; wynik różni się od wejścia | Potrzebny przegląd diffu i ocena wpływu na poprawną treść przed zastosowaniem. Nie zapisano wyniku. |
| Runtime | Python 3.13.18, Docling 2.146.0, ONNX Runtime 1.31.1, Node 26.13.0 | Konfiguracje i modele należy zapisywać w wynikach, nie tylko deklarować nazwą „zamrożony stos”. |
| Akceleracja OCR | Dostępne providery ONNX: AzureExecutionProvider, CPUExecutionProvider; brak CUDAExecutionProvider | W badanym środowisku nie wykazano OCR na GPU. Sam CUDA dla modeli Torch/Docling tego nie potwierdza. |
| EPUB | Brak końcowego EPUB w katalogu wyjściowym, brak epubcheck w PATH | Nie oceniono aktualnej publikacji. Lokalny walidator nie jest równoważny pełnemu EPUBCheck. |

Pomiary cleaner/auditor są pojedynczymi próbami. Nie są percentylami wydajności. Podczas tej sesji nie wykonano nowego benchmarku ekstrakcji OCR, więc nie podajemy CER/WER bieżącej książki ani czasu pełnej konwersji.

## 2. Wynik pomiaru kosztu stanu panelu

Pomiar bez HTTP: 25 sekwencyjnych wywołań `readBookState` dla każdego syntetycznego profilu, przy rozgrzanym cache systemowym, CPU AMD Ryzen 9 9900X. Każdy profil zawiera również syntetyczny plik EPUB 1 MiB i manifest, który uruchamia sprawdzanie zależności.

| Markdown / grafiki | p50 | p95 | Maksimum | Opóźnienie timera podczas pojedynczego odczytu |
|---|---:|---:|---:|---:|
| 0,1 MiB / 0 MiB | 1,16 ms | 2,12 ms | 9,56 ms | 0,93 ms |
| 1 MiB / 16 MiB | 11,90 ms | 18,41 ms | 27,33 ms | 13,39 ms |
| 8 MiB / 64 MiB | 48,92 ms | 61,18 ms | 73,96 ms | 50,26 ms |

To nie jest zmierzona latencja API ani test wielu użytkowników. Pokazuje jednak, że koszt odczytu rośnie z rozmiarem treści i grafik. `readFileSync`, hashowanie i skanowanie katalogów odbywają się w wątku obsługującym żądania. Samo zastąpienie odczytu wersją async nie usuwa kosztu SHA ani wielokrotnego czytania tych samych zależności. Node zaleca odsuwanie kosztownych operacji od event loop: [dokumentacja Node](https://nodejs.org/learn/asynchronous-work/dont-block-the-event-loop).

## 3. Najpoważniejsze luki merytoryczne

1. `src/gate_g2_evaluation.py` wpisuje `invariants_intact=True` i kryterium zachowania miar/temperatur `True`. „Stabilność tabel i tekstu” to tylko `total_blocks > 0`. To potwierdzone pozorne pomiary. Brak wyniku musi dawać NOT_EVALUATED i blokować PASS.
2. `evaluate_text_layer_quality` ogląda kilka początkowych stron i decyduje o całym PDF. Dla mieszanych dokumentów nie jest to wiarygodna polityka wyboru OCR.
3. `get_converter` wybiera pojedynczy kod języka RapidOCR (`pl` ma pierwszeństwo), mimo domyślnego `pl,en`. Trzeba zweryfikować rzeczywiste działanie konfiguracji i modeli na dwujęzycznej treści. Nazwa języka nie jest dowodem obsługi obu języków.
4. Weryfikacja zależności obejmuje cały wspólny katalog assetów. Dodanie grafiki innej książki może unieważnić istniejącą kompilację. Źródła trzeba wiązać z konkretną książką i jej referencjami.
5. RAG wylicza `estimated_page` z proporcji numeru linii do domyślnych 914 stron. To szacunek, nie pochodzenie fragmentu. Nie może służyć jako wiarygodne cytowanie PDF.
6. Dziennik JSON z PID jest przydatnym mechanizmem obserwacji, ale nie pełną kolejką: brak transakcyjnego przydziału zadania, dzierżawy, heartbeat i kontrolowanego odzyskiwania pracy. Samego PID nie należy uznawać za trwałą tożsamość procesu.
7. Autopilot nadal zależy od otwartej przeglądarki. To ogranicza długie przebiegi i odzyskiwanie po awarii.
8. `web/src/pages/index.astro` ma około 3175 linii, `src/pipeline.py` około 2599. Logika stanu jest podzielona między frontend, API i CLI. Zwiększa to ryzyko kolejnych rozbieżności.
9. Entrypoint `pdf-to-epub` wskazuje funkcję wypisującą „Hello from pdf-to-epub!”, a nie właściwy pipeline. Opakowanie i test instalacji także wymagają korekty.

## 4. Kolejność prac i kryteria odbioru

Progi poniżej są proponowanymi celami projektu, nie osiągniętymi wynikami ani uniwersalnymi normami. Zatwierdzamy je po pomiarze na reprezentatywnych danych, przed otwarciem holdoutu.

### P0 — Wiarygodne pomiary i bezpieczny punkt odniesienia

- Powiązać źródło z SHA-256, profilem książki i jawną ścieżką. Usunąć zależność testów od konkretnej nazwy prywatnego PDF. Używać małych lokalnych PDF-fixture.
- Naprawić test negatywny i jawnie sklasyfikować pominięcia: brak opcjonalnego narzędzia, test integracyjny, rzeczywisty brak fixture.
- Zastąpić stałe PASS w G2 rzeczywistym pomiarem albo NOT_EVALUATED.
- Dodać testy negatywne całej bramki: 1 tsp → 1 tbsp, 180°C → 180°F, pominięty składnik, zamienione komórki tabeli, przeplecione kolumny, brak strony. Każdy musi uniemożliwić PASS.
- Zapisać snapshot źródeł i pełne środowisko: lockfile, wersje silników, rewizje modeli, konfigurację, providery i sprzęt.

**Odbiór:** wszystkie wymagane testy niezależne od danych użytkownika przechodzą; żadne niezmierzone kryterium nie daje PASS; każda kontrolowana korupcja jest wykryta. Bez tego nie wybieramy „najlepszego modelu”.

### P1 — Wzorce i realny benchmark jakości treści

- Uzupełnić 20 stron dev oraz 10 odłożonych holdout, z anotacją bloków, kolejności czytania, tabel, pomiarów, temperatur i pochodzenia. Przynajmniej trudne liczby i jednostki przechodzą drugą kontrolę ze skanem.
- Porównywać warianty na tych samych stronach: native, RapidOCR, EasyOCR, selektywny OCR regionów. Zapisywać wynik surowy i po czyszczeniu osobno.
- Liczyć CER/WER osobno dla zwykłego tekstu i elementów krytycznych, kompletność bloków/stron, kolejność czytania oraz zgodność komórek tabel. Raportować wyniki per typ układu, nie wyłącznie średnią książki.
- Kalibrować audyt: ręcznie oznaczyć co najmniej 100 trafień i 100 fragmentów bez trafienia, ze stratyfikacją po typie błędu. To próbka kalibracyjna; pełne precision/recall wyliczamy dopiero na odpowiednio oznaczonym zbiorze.
- Wprowadzić regułę dla cleanera: niepewna zmiana jest propozycją z diffem, a nie automatycznym usunięciem. Poszerzyć korpus poprawnych zdań, polskich nazw, miar i tabel.

**Odbiór:** 100% pokrycia stron testu; zero niewyjaśnionych zmian liczb, jednostek, temperatur i referencji do obrazów na oznaczonym zbiorze; cel startowy CER ≤1% na prozie, WER ≤3%, bez regresji w żadnej kategorii. Próg może wymagać korekty po pomiarze dev, nigdy po obejrzeniu holdoutu. 30 stron nie dowodzi bezbłędności całych 914 stron.

### P2 — Dobór OCR i wydajność konwersji

- Używać jednego adaptera konfiguracji dla panelu, CLI i benchmarku. Obecnie ścieżki mogą badać inny stos niż faktycznie używany.
- Decydować native/OCR na poziomie strony lub regionu. Docling udostępnia selektywne OCR i opcje struktury tabel; należy porównać warianty `ACCURATE`/`FAST` i cell matching na naszych danych, nie zakładać zwycięzcy: [opcje Docling](https://github.com/docling-project/docling/blob/main/docs/usage/advanced_options.md).
- Sprawdzić osobno GPU dla layoutu, tabel, OCR i Ollama. Rejestrować faktyczny provider każdej sesji. Instalację wariantu GPU poprzedzić kontrolą zgodności CUDA/cuDNN: [ONNX CUDA EP](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).
- Wydzielić koszt uruchomienia modeli od kolejnych stron. Porównać rozmiary partii 1/5/10/25 i ponowne użycie konwertera. Mierzyć p50/p95 czasu strony, RAM/VRAM, liczbę ponowień i sekundę na stronę bez błędu krytycznego.
- Zredukować koszt Levenshteina dopiero po profilowaniu; obecna implementacja Python ma czas O(m*n), więc jej przyspieszenie jest kandydatem do eksperymentu, nie jeszcze zmierzonym wąskim gardłem.

**Odbiór:** wybrany wariant zachowuje lub poprawia jakość P1; proponowany cel optymalizacji to ≥20% krótszy czas na reprezentatywnym dev. Minimum trzy porównywalne przebiegi, osobno cold/warm. Brak przewagi oznacza pozostawienie prostszego wariantu. Bez pełnego pomiaru nie obiecujemy czasu całej książki.

### P3 — Lekki i przewidywalny panel

- Oddzielić lekki status zadania od kosztownej weryfikacji treści. Hashować w workerze przy tworzeniu wersji; panel odczytuje mały snapshot stanu.
- Cache powiązać z generacją/revizją. Zmiany zewnętrzne unieważniają cache przez stat/watcher; przed publikacją wykonać pełną kontrolę hashy, więc optymalizacja nie osłabia integralności.
- Obejmować manifestem tylko rzeczywiste zależności książki. Nie unieważniać jej po zmianie obcej grafiki.
- Stosować adaptacyjne odpytywanie (szybko podczas pracy, wolniej w bezczynności, pauza dla ukrytej karty) lub SSE z ponownym połączeniem. SSE wdrażać, jeśli pomiar uzasadni większą złożoność.
- Osobno cache'ować dostępność Ollama; nie łączyć każdej operacji odczytu książki z sondowaniem sieci.
- Dodać prawdziwy test HTTP z 1 i 5 klientami, przy jednym działającym workerze. Test Playwright z atrapami API zachować jako test UI, nie nazywać go pełnym testem aplikacji.

**Odbiór:** proponowane cele: odczyt ciepłego snapshotu p95 <20 ms dla dużego profilu; lekki endpoint HTTP p95 <150 ms przy 5 klientach; opóźnienie event loop p95 <20 ms. Brak kolejnych pełnych hashy niezmienionych assetów podczas pollingu. Te cele wymagają nowych pomiarów HTTP.

### P4 — Trwałe zadania i Autopilot po stronie serwera

- Wprowadzić niewielki lokalny magazyn zadań SQLite i osobny worker. Rozdzielić transakcyjny przydział zadania od długiej pracy OCR. Nie ma obecnie uzasadnienia dla Redis/Kubernetes.
- Dodać stany queued/running/cancelling/succeeded/failed/interrupted, heartbeat, dzierżawę, identyfikator próby i token właściciela. Zachować idempotencję zapisu i weryfikację wersji Markdowna.
- Przenieść pętlę Autopilota do workera; każdy zaakceptowany blok ma checkpoint i historię. Nie ponawiać zatwierdzonej poprawki po restarcie.
- Ujednolicić checkpointy ze zbudowanym już runnerem kandydatów. Stopniowo zastąpić globalną blokadę blokadą książki; równoległość GPU początkowo 1.
- SQLite WAL pozwala współistnieć odczytom i zapisowi, ale nadal ma pojedynczego zapisującego. Transakcje mają być krótkie; WAL nie rozwiązuje sam harmonogramowania: [dokumentacja SQLite](https://www.sqlite.org/wal.html).

**Odbiór:** w testach refresh, zamknięcie karty, restart API, zabicie workera i utrata odpowiedzi nie gubią zapisanych bloków i nie powodują podwójnej aplikacji poprawki. Stara próba nie może zatwierdzić wyniku po utracie dzierżawy. Stan końcowy pochodzi z zapisanej weryfikacji artefaktów, nie samego istnienia PID.

### P5 — Publikacja EPUB, RAG i utrzymanie

- Do wydania dołączyć wynik oficjalnego EPUBCheck; sprawdzanie natywne zachować jako szybki etap. Brak narzędzia lub timeout oznacza „nie sprawdzono”, nie pełną zgodność: [EPUBCheck W3C](https://www.w3.org/publishing/epubcheck/).
- Sprawdzić wizualnie EPUB w co najmniej dwóch docelowych czytnikach: nagłówki, spis, grafiki, tabele i polskie znaki. Zgodność kontenera nie potwierdza poprawnej treści.
- RAG otrzymuje rzeczywiste page/block IDs z sidecara oraz hash wersji źródłowej. Dla starych plików oznaczać brak pochodzenia; nie przedstawiać oszacowania linii jako numeru strony źródłowej.
- Przepisy i tabele dzielić semantycznie; ocenić na niewielkim zestawie pytań wyszukiwanie przepisu, składnika, dawki i temperatury z poprawnym źródłem. Mierzyć Recall@k i poprawność cytowanego bloku, zanim dodamy kolejny model embeddingów.
- Rozdzielić duże pliki na moduły według funkcji po zbudowaniu testów kontraktowych. Naprawić entrypoint, deklarować bezpośrednie zależności i dodać smoke test czystej instalacji z lockfile.

**Odbiór:** 0 błędów EPUBCheck; ostrzeżenia opisane; brak utraconych assetów; każdy nowy chunk RAG ma sprawdzalne pochodzenie; test czystej instalacji przechodzi. Historyczne chunki bez pochodzenia pozostają jawnie oznaczone.

## 5. Zasady decyzji

Priorytet wdrożenia: **P0 → P1 → P2**, a P3 można realizować równolegle z anotacją wzorców; następnie P4 i P5. To opis niezależnych strumieni prac, nie polecenie uruchamiania dodatkowych agentów.

Nie zmieniamy silnika ani parametrów na podstawie pojedynczej ładnej strony. Każda zmiana dostaje bazę porównawczą, konfigurację, wynik jakości, koszt i decyzję accept/reject. Holdout otwieramy dopiero po zamrożeniu konfiguracji; po użyciu do strojenia przestaje być holdoutem. Publikacja pozostaje atomowa i odwracalna, a eksperymenty trafiają do katalogów kandydatów.

## 6. Artefakty i odtwarzanie pomiaru

- `docs/measurements/quality-baseline-2026-11-05.json`: GT, cleaner, auditor i bieżący MD.
- `docs/measurements/status-baseline-2026-11-05.json`: syntetyczne profile wydajności.
- `scripts/measure_quality_baseline.py`: uruchomienie z głównego katalogu przez `PYTHONPATH=. uv run python scripts/measure_quality_baseline.py`.
- `scripts/measure_status_baseline.mjs`: `node scripts/measure_status_baseline.mjs`.

Skrypty zapisują wyłącznie raporty pomiarowe oraz usuwalne fixture w katalogu tymczasowym. Nie uruchamiają ekstrakcji ani nie zapisują produkcyjnego Markdowna. Wyniki porównujemy tylko przy opisanych wersjach i warunkach; cache, obciążenie systemu i rozmiary danych wpływają na czas.

## Wdrożenie pierwszego etapu — P0

- Bramka G2 wylicza kryteria na podstawie wzorców i sidecarów stron. Brak danych daje `NOT_EVALUATED`; raport nie dopisuje automatycznie zgody na G3. CLI zwraca kod 2, jeżeli bramka nie uzyskała PASS.
- Preflight wymaga dziesięciu kompletnych, rozłącznych stron holdout, hashy obrazów i powiązania manifestu/wzorców z SHA-256 PDF. Braki zatrzymują pracę przed inferencją holdoutu. Obecnych anotacji nie uzupełniono domysłami.
- Miary są sprawdzane w dopasowanych blokach, z uwzględnieniem jawnych anotacji krytycznych. Tabele są sprawdzane według pozycji komórek prostych tabel Markdown. Nieobsługiwane lub niepełne struktury (np. scalenia) pozostają nieocenione zamiast uzyskać PASS. Dopasowanie niejednoznaczne też nie daje zaliczenia.
- Zapisuje się konfigurację kandydata, wersje pakietów, dostępne providery ONNX, hashe lockfile i kodu pomiarowego oraz identyfikatory źródeł i wzorców. Hashy faktycznie załadowanych wag modeli nie dodano jeszcze — pozostają częścią pomiaru rzeczywistego stosu w P2.
- Kontrola integralności obejmuje wszystkie książki i assety w `output/markdown`, a nie wyłącznie plik o starej nazwie. Sprawdzane są też zmiany źródła i wzorców podczas przebiegu.
- Usunięto zależność wykonywanych testów od prywatnego PDF. Generowane małe PDF-y nie wymagają ReportLab. Cztery testy inferencji Docling stanowią jawną grupę `document_integration`, uruchamianą flagą `--run-document-integration`; działają na generowanych dokumentach.
- Poprawiono definicję percentyli na nearest-rank. Dla dziesięciu próbek p95 obejmuje najwolniejszą, więc pojedynczej strony trwającej 60 s nie można ukryć pod limitem 30 s. Brak lub niepoprawny czas nie staje się zerem.
- Testy negatywne całego runnera obejmują zmianę jednostki/temperatury, usunięty składnik, zamienione komórki, zaburzoną kolejność bloków, brak strony, brak pomiaru czasu, zmianę źródeł i naruszenie produkcji. Oddzielny poprawny kandydat musi przejść wszystkie kryteria.

To wdrożenie mechanizmu kontroli, nie certyfikacja aktualnej książki. Holdout w rzeczywistym projekcie nadal wymaga anotacji i powiązania ze źródłem. Progi CER/WER są jawnie zapisane w raporcie; ich kalibracja i obsługa bardziej złożonych struktur należą do P1.
