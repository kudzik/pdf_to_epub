# Audyt logiki projektu — 2026-11-04

Zakres: przegląd wyboru książki, stanu publikacji, ekstrakcji, czyszczenia, audytu, napraw, kompilacji, obsługi zadań oraz testów. To lista potwierdzonych usterek i ograniczeń w sprawdzonych ścieżkach, a nie gwarancja znalezienia wszystkich błędów repozytorium. Bez ponownego OCR, zapisu książki lub operacji resetu. W tej sesji zmieniono wyłącznie ten raport.

## Wyniki i plan naprawy

| ID / priorytet | Dowód i skutek | Naprawa |
|---|---|---|
| A01 / wysoki | `index.astro:150–163`: statyczny baner ogłasza znaleziony PDF i krok 1 przed odpowiedzią API. Przy błędzie odczytu zostaje fałszywa instrukcja. | Początkowy stan „Wybierz książkę / Ładowanie”, brak aktywnej akcji do zakończenia odczytu. Osobny stan błędu. |
| A02 / wysoki | `loadBooks`: przeglądarka automatycznie wybiera pierwszą opcję; brak jawnego stanu bez wyboru. | Pusta opcja wyboru lub wyraźne „Wznawiasz książkę…”, gdy przywracamy poprzedni wybór. |
| A03 / wysoki | Karty `stepCard1…5` to elementy div bez przycisków. Akcje są osobno w pasku i banerze. Nie ma jednoznacznego związku między kartą a operacją. | Przycisk na każdej karcie, widoczny stan i uzasadnienie niedostępności. Jedno źródło definicji akcji dla wszystkich miejsc UI. |
| A04 / wysoki | `updateWorkflowStepper`: `mdInfo.exists` oznacza „Wyekstrahowano”. Nawet pusty/importowany/częściowy MD wygląda jak udana ekstrakcja. Odtworzono dla MD z jedną linią. | Osobne pojęcia „MD istnieje” i „potwierdzony wynik ekstrakcji”. Wznowienie istniejącej pracy bez sugerowania nowego wykonania. |
| A05 / krytyczny dla kompletności | `extractRangeSelect`: domyślnie 1–10; `extract_pdf` zapisuje próbkę do tego samego produkcyjnego MD co całą książkę. Końcowy progress usuwa informację o zakresie i liczbie stron. | Próbki w izolowanym katalogu. Manifest pochodzenia z SHA PDF/MD, zakresem, pokryciem, silnikiem i wynikiem. Status „próbka” nie może oznaczać całej publikacji. |
| A06 / wysoki | UI ma stałe 914 stron i czas 20–30 minut dla każdej książki. | Liczba stron z PDF, czas tylko jako oszacowanie oparte na pomiarze; żadnego stałego opisu konkretnej książki w ogólnym panelu. |
| A07 / krytyczny dla treści | `triggerAction`: gałąź pełnej ekstrakcji wywołuje bezpośrednio `executeAction` i wraca przed ostrzeżeniem o nadpisaniu. Próg >10 linii dodatkowo pomija krótkie poprawione dokumenty. | Jedna kontrola nadpisania dla każdej istniejącej wersji, niezależna od zakresu i długości. Najpierw przygotowanie wyniku w izolacji, potem jawne zastąpienie. |
| A08 / wysoki | `fetchStatus`: brak anulowania/stempla żądania i porównania `data.book` z bieżącym wyborem. Wolna odpowiedź A może nadpisać widok B; historia jest pobierana już według aktualnego selektora. | Identyfikator żądania, AbortController, sprawdzenie książki przed każdym zapisem stanu i renderowaniem; kontekst książki przekazywany do historii. |
| A09 / wysoki | Zmiana książki nie czyści ani nie blokuje poprzedniego widoku do zakończenia fetch. Błędy są tylko logowane. | Natychmiastowy stan ładowania, blokada akcji, czytelny błąd i ponowienie. |
| A10 / wysoki | `status.ts` porównuje mtime MD z datą czyszczenia. Zapis naprawy oznacza „nieoczyszczony” i powrót do kroku 2. To regresja z poprzedniej mojej poprawki. | Historia etapów związana z generacją ekstrakcji. Naprawa po czyszczeniu unieważnia audyt/EPUB, ale nie automatycznie etap czyszczenia. |
| A11 / wysoki | `clean_markdown_file`: znacznik zapisany nieatomowo, wyjątki ignorowane. Udane czyszczenie może nie zostać zapisane w stanie. | Atomowy zapis manifestu i jawna obsługa niepowodzenia; wynik polecenia i stan UI muszą być spójne. |
| A12 / wysoki | `status.ts` odrzuca stary raport przez null, bez rozróżnienia braku/starości/uszkodzenia; jeśli MD nie istnieje, raport nie jest unieważniany. Odtworzono zielony audyt przy braku MD. | Jawne stany raportu: missing/stale/invalid/current; brak MD blokuje ważność raportu. |
| A13 / wysoki | `updateWorkflowStepper` ignoruje `missing_chapters`. Raport z brakującymi rozdziałami i 0 anomalii daje sukces. Odtworzono. | Osobna ocena kompletności i jakości OCR; pokazanie braków, ograniczeń i pokrycia. |
| A14 / wysoki | Audyt pomija akapity zaczynające się od `|`; brak rozpoznanego CONTENTS daje pustą listę rozdziałów. Nie obejmuje pełnej jakości tabel ani kompletności PDF. | Status zakresu kontroli, detektory tabel, manifest pokrycia PDF i jawne „nie oceniono”, jeśli brak podstaw. |
| A15 / wysoki | `startOllamaAutopilot` wykonuje build przed audytem; `runHeuristicBatch` także clean → build → audit. Faktyczne działanie przeczy kolejności pokazywanej w UI. | Naprawa → audyt → decyzja o dalszej kontroli/kompilacji. W UI jawnie pokazać, czy kompilacja jest automatyczna. |
| A16 / wysoki | `isEpubValid` oznacza obecność i mtime, a nie walidację. Nie uwzględnia zmian CSS, metadanych ani grafik. `compile_epub` nie wywołuje walidatora. | Manifest kompilacji z hashami zależności, atomowa publikacja EPUB i zapis wyniku walidacji. Rozdzielić aktualność od zgodności standardu. |
| A17 / średni | `files.ts` i selektor mówią „EPUB gotowy” po samym istnieniu pliku; panel pobierania także dopuszcza stary EPUB. Różne miejsca stosują różne reguły. | Jeden model stanu w API. Starszy EPUB dostępny wyłącznie z jednoznacznym opisem wersji. |
| A18 / krytyczny | `action.ts` składa polecenia shell z `book`, `pages`, `targetFolder`. Podwójne cudzysłowy i basename nie zapewniają bezpiecznych argumentów. | `spawn/execFile` z tablicą argumentów, walidacja nazw, zakresów i dozwolonych folderów. Bez odtwarzania ataku na danych użytkownika. |
| A19 / wysoki | `runningJobs` sprawdzane tylko w extract. Clean/build/purge mogą wejść równolegle i nadpisać wpis zadania; blokada UI działa tylko w jednej karcie. | Wspólna blokada serwerowa wszystkich mutacji książki, identyfikator zadania i kontrola własności wpisu przy sprzątaniu. |
| A20 / wysoki | Cancel wysyła sygnał do grupy, ale exec nie tworzy jawnie oddzielnej grupy. Fallback zabija shell, nie gwarantuje zatrzymania potomków. Wpis znika przed potwierdzeniem końca; komentarz zapowiada SIGKILL, którego brak. | Kontrolowany cykl życia drzewa procesów, stan cancelling do potwierdzonego exit, eskalacja sygnałów i dopiero wtedy zwolnienie blokady. |
| A21 / średni | Limit zadania 600 s jest krótszy niż deklarowany przez UI pełny przebieg 20–30 min. | Zadania niezależne od połączenia HTTP, checkpointy i limit dostosowany do operacji. |
| A22 / średni | CLI audit domyślnie zapisuje `audit_report.json` także dla nazwanej książki; status web szuka `audit_<book>.json` (fallback tylko dla ksiazka). | Jedna funkcja wyznaczania ścieżek artefaktów CLI/API. |
| A23 / średni | `metadata.yaml` wspólne dla wszystkich książek i zawiera konkretny tytuł/autorów. | Metadane per książka, widoczne przed kompilacją. |
| A24 / wysoki | `loadBooks` i komunikat braku raportu wstawiają nazwę książki przez innerHTML bez escape. | textContent/tworzenie elementów DOM lub konsekwentne kodowanie danych. |
| A25 / wysoki | Testy web w dużej części sprawdzają obecność fragmentów kodu. Mój test VM sprawdzał etykiety, nie realny wybór książki, przyciski, wyścigi, skutki plikowe. `playwright_audit.mjs` uruchamia nadpisanie i reset. | Bezpieczne E2E na izolowanych fixture, asercje zachowania i niezmienników. Żaden test nie może domyślnie dotykać książki produkcyjnej. |

## Dlaczego poprzednia poprawka nie wystarczyła

Skupiłem się na numeracji i lokalnej funkcji rekomendacji. Nie opisałem wcześniej stanów domenowych ani nie prześledziłem pełnego cyklu od wyboru książki do ponownego odczytu po naprawie. Przyjąłem błędne założenia istniejącego kodu: plik MD oznacza ukończoną ekstrakcję, a zmiana mtime po czyszczeniu oznacza konieczność ponownego czyszczenia. Testy potwierdziły te założenia zamiast je podważyć. Liczbę przechodzących testów przedstawiłem jako zbyt silny dowód poprawności. Za regresję A10 odpowiada moja poprzednia zmiana. Brak bezpiecznego E2E należało uzupełnić fixture; samo odstąpienie od ryzykownego skryptu nie zamykało weryfikacji.

## Docelowe zachowanie

Wybór książki jest przygotowaniem, nie ekstrakcją. Po wyborze UI pokazuje faktyczny stan zapisanej pracy. Gdy MD już istnieje, komunikat brzmi „Znaleziono istniejący Markdown”; potwierdzona ekstrakcja pokazuje zakres i kompletność. Każda karta ma własną akcję i wyraźnie rozdziela: zakończono, do wykonania, zalecany następny krok, trwa, błąd, wynik nieaktualny. Zielony stan nie oznacza aktywnego zaznaczenia.

Cztery etapy użytkowe: ekstrakcja, czyszczenie, audyt/naprawa, kompilacja/walidacja. Autopilot jest akcją naprawczą w etapie audytu. Stan pochodzi z jednego modelu backendu, nie z kilku niezależnych heurystyk w przeglądarce. Importowany plik o nieznanym pochodzeniu pozostaje użyteczny bez przymusowej ekstrakcji, ale ma jawny status nieznanego pokrycia.

## Kolejność wdrożenia i odbiór

1. Zabezpieczenie argumentów procesów, ochrony przed nadpisaniem, blokad i anulowania.
2. Manifest pochodzenia, odróżnienie próbek, wersje artefaktów i jeden model stanu.
3. UI wyboru/ładowania i akcje na kartach; usunięcie rozproszonych reguł gotowości.
4. Ujednolicenie naprawa → audyt → build → walidacja, rozdzielenie kompletności i OCR.
5. Bezpieczne testy integracyjne/E2E: nowa książka, istniejący/importowany/pusty MD, próbka, pełny wynik, A→B z odwróconą kolejnością odpowiedzi, błąd API, korekta po clean, nieaktualny audyt, braki rozdziałów, stare zależności EPUB, dwa równoległe klienty, anulowanie i ochrona produkcji.

Nie należy ogłaszać „wszystko naprawione” przed wdrożeniem i przejściem tych scenariuszy. Ten dokument opisuje audyt i plan; implementacja nie została w tej turze zmieniona.

## Wdrożenie poprawek

Wdrożono stan bez wybranej książki, blokowanie akcji podczas odczytu, obsługę błędu i odrzucanie spóźnionych odpowiedzi. Karty mają przyciski. Istniejący MD jest opisywany jako istniejący plik, bez deklaracji wykonania ekstrakcji. Puste pliki nie są użytecznym wynikiem. Domyślna ekstrakcja obejmuje całość, próbki trafiają do `output/samples/`. Zapis wyniku jest przygotowywany w pliku tymczasowym, sprawdza konflikt wersji i zachowuje kopię istniejącego Markdowna.

Dodano manifest ekstrakcji i walidowanej kompilacji oraz wspólny odczyt stanu dla listy książek i panelu. Czyszczenie jest zachowanym etapem po naprawie, nowa ekstrakcja go unieważnia. Raporty mają stany missing/stale/invalid/current. Braki rozdziałów zatrzymują komunikat sukcesu; brak rozpoznanego spisu treści jest zgłaszany jako nieoceniona kompletność. Audyt nie pomija już całych tabel. Naprawa kończy się ponownym audytem; kompilacja jest osobną decyzją.

EPUB powstaje w pliku tymczasowym, przechodzi lokalny walidator, a dopiero potem zastępuje poprzedni. Manifest sprawdza Markdown, metadane, CSS, grafikę okładki i katalog assetów. Starsze publikacje bez manifestu nie są określane jako zweryfikowane. Metadane nazwanych książek są szukane w `metadata/<book>.yaml`; pozostałe tytuły nie dziedziczą autorów tej książki kucharskiej.

Polecenia API używają `execFile` z tablicą argumentów. Dodano walidację zakresów, potwierdzenie nadpisania po stronie serwera, wspólną blokadę mutacji API na dysku i zgodny protokół blokady książki CLI/API. Anulowanie czeka na zakończenie procesu i eskaluje do SIGKILL po 3 sekundach. Limit operacji zwiększono do 2 godzin. To nadal lokalny proces, **nie trwała kolejka zadań**: panel nie zapewnia wznowienia procesu po restarcie serwera. Istniejący runner pełnej konwersji z checkpointami pozostaje oddzielną komendą CLI.

Dodatkowo naprawiono szersze niż deklarowane usuwanie danych: reset jednej książki nie usuwa innych książek, współdzielonych assetów ani ich historii; sprzątanie cache nie kasuje znacznika ukończonego czyszczenia. CLI i panel zapisują raporty nazwanych książek pod tą samą nazwą.

Test Playwright zastąpiono bezpiecznym scenariuszem z przechwyceniem wszystkich API. Sprawdza wybór bez uruchamiania operacji, akcje na kartach, zmianę książki, spóźnioną odpowiedź i błąd odczytu. Dodano testy na tymczasowych plikach dla stanu, blokad, parametrów API, izolacji próbek, zachowania starego EPUB-a przy błędzie walidacji oraz zakresu resetu. Nie wykonywano ponownej ekstrakcji ani korekty produkcyjnego Markdowna.

## Synchronizacja zadań i odświeżanie panelu

Dodano dziennik zadań w `output/jobs/`, odczyt przez `/api/jobs` i okresową synchronizację panelu. Odświeżenie strony odtwarza wybraną książkę, działające zadanie, czas rozpoczęcia, końcówkę logu oraz procent ekstrakcji, jeżeli pipeline go udostępnia. Stan zakończonego/przerwanego zadania jest również zapisany. Zamknięcie strumienia HTTP nie zatrzymuje procesu; zachowanie jest sprawdzane testem uruchamiającym bezpieczny proces zastępczy w katalogu tymczasowym. Martwy proces bez potwierdzonego wyniku jest oznaczany jako interrupted, a nie succeeded. Blokada uwzględnia zapisany działający proces również po utracie pamięci serwera.

Kafelki i dolny pasek są blokowane razem. Panel pokazuje nazwę innej książki, której zadanie zajmuje globalną blokadę zapisu. Nadal jest to jedna globalna blokada mutacji — nie wprowadzono równoległego zapisu różnych książek.

Ustawienia ekstrakcji są wspólne, widoczne na kafelku i zapamiętywane w przeglądarce. Auto pozostawia ocenę jakości warstwy tekstowej, jawny RapidOCR/EasyOCR wymusza OCR, tryb natywny go wyłącza. Naprawiono respektowanie jawnie wskazanej książki podczas odczytu historii.

Autopilot pozostaje pętlą w przeglądarce. Przerwana sesja jest sygnalizowana po powrocie i wymaga ponownego audytu przed kolejnym uruchomieniem; zapisane poprawki są zachowane. Nie wdrożono automatycznego ponawiania napraw AI ani automatycznego wznowienia martwego procesu po restarcie serwera.
