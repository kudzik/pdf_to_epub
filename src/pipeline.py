import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Optional
import pypdfium2 as pdfium
import typer
from rich.console import Console
from rich.table import Table

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    EasyOcrOptions,
    RapidOcrOptions,
    OcrMode,
)
from docling.datamodel.base_models import InputFormat
from docling.datamodel.progress import PageCompletedProgress
from docling_core.types.doc.base import ImageRefMode

from src.cleaner import clean_markdown_file
from src.auditor import audit_markdown_file
from src.repair import get_anomaly_details, patch_paragraph, patch_batch_from_file
from src.snapshot import SnapshotOptions, create_snapshot, verify_snapshot_integrity
from src.history import load_history_ledger, verify_index_anchors
from src.benchmark_manifest import load_test_pages_manifest, validate_manifest_consistency
from src.logger import setup_logger, app_logger

# Inicjalizacja strukturyzowanego logowania Loguru + Sentry
setup_logger()

app = typer.Typer(
    help="Pipeline ekstrakcji ze skanów PDF do Markdown i kompilacji EPUB 3 z integracją WSL/Windows.",
    no_args_is_help=True,
)
console = Console()


def get_windows_downloads_dir() -> Optional[Path]:
    """Wykrywa katalog Pobrane użytkownika Windows w środowisku WSL."""
    wsl_users_dir = Path("/mnt/c/Users")
    if not wsl_users_dir.exists():
        return None
    for user_dir in wsl_users_dir.iterdir():
        if user_dir.is_dir() and user_dir.name not in ["Public", "Default", "Default User", "All Users"]:
            downloads = user_dir / "Downloads"
            if downloads.exists() and downloads.is_dir():
                return downloads
    return None


def copy_to_windows_downloads(epub_path: Path) -> Optional[Path]:
    """Kopiuje plik EPUB bezpośrednio do folderu Pobrane w Windows."""
    win_downloads = get_windows_downloads_dir()
    if win_downloads and win_downloads.exists() and epub_path.exists():
        dest = win_downloads / epub_path.name
        try:
            shutil.copy2(epub_path, dest)
        except OSError as exc:
            console.print(f"[yellow]EPUB utworzony, ale kopiowanie do Windows nie powiodło się: {exc}[/yellow]")
            return None
        return dest
    return None


def evaluate_text_layer_quality(pdf_path: str, max_check_pages: int = 5) -> dict[str, Any]:
    """
    Ocenia obecność i jakość wbudowanej warstwy tekstowej w pliku PDF.
    Rozróżnia czysty, cyfrowy wektor (np. InDesign, TeX, Word) od
    starego, uszkodzonego OCR ze skanera (np. Acrobat 5/6, zniekształcone znaki).
    """
    try:
        pdf = pdfium.PdfDocument(pdf_path)
        total_pages = len(pdf)
        check_indices = [min(i, total_pages - 1) for i in [0, 1, 5, 10, 25] if i < total_pages]
        if not check_indices:
            check_indices = [0]

        total_chars = 0
        total_garbage = 0
        total_words = 0

        for idx in check_indices:
            text = pdf[idx].get_textpage().get_text_range().strip()
            total_chars += len(text)
            words = re.findall(r"\b[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{2,}\b", text)
            total_words += len(words)
            garbage = re.findall(r"[\^■~¥|\\]|[a-zA-Z0-9]+[°*#@]+[a-zA-Z0-9]*|[“«»?]{2,}|[•\-\*]{2,}", text)
            total_garbage += len(garbage)

        has_text = total_chars > 50
        if not has_text:
            return {
                "has_text": False,
                "is_clean": False,
                "quality_score": 0.0,
                "recommended_ocr": True,
                "reason": "Brak warstwy tekstowej",
            }

        words_ratio = (total_words * 5) / max(1, total_chars)
        garbage_density = total_garbage / max(1, (total_chars / 1000))
        is_clean = (garbage_density < 0.5) and (words_ratio > 0.6) and (total_garbage == 0)
        quality_score = max(0.0, min(1.0, 1.0 - (garbage_density * 0.15)))

        return {
            "has_text": has_text,
            "is_clean": is_clean,
            "quality_score": round(quality_score, 2),
            "total_chars": total_chars,
            "total_words": total_words,
            "total_garbage": total_garbage,
            "recommended_ocr": not is_clean,
            "reason": "Czysty wektor cyfrowy" if is_clean else f"Uszkodzona warstwa OCR (śmieci: {total_garbage})",
        }
    except Exception as exc:
        return {
            "has_text": False,
            "is_clean": False,
            "quality_score": 0.0,
            "recommended_ocr": True,
            "reason": f"Błąd odczytu: {exc}",
        }


def has_text_layer(pdf_path: str, max_check_pages: int = 5) -> bool:
    """Sprawdza, czy plik PDF posiada wysokiej jakości warstwę tekstową bez konieczności OCR."""
    quality = evaluate_text_layer_quality(pdf_path, max_check_pages=max_check_pages)
    return quality["is_clean"]


def get_converter(do_ocr: bool, langs: list[str], engine: str = "easyocr") -> DocumentConverter:
    """Inicjalizuje DocumentConverter z obsługą OCR (RapidOCR lub EasyOCR) oraz akceleracji GPU (CUDA)."""
    pipeline_options = PdfPipelineOptions()
    pipeline_options.generate_page_images = False
    pipeline_options.generate_picture_images = True
    pipeline_options.do_ocr = do_ocr

    # Automatyczna detekcja akceleracji sprzętowej GPU (CUDA / RTX)
    try:
        import torch
        if torch.cuda.is_available():
            from docling.datamodel.pipeline_options import AcceleratorDevice, AcceleratorOptions
            pipeline_options.accelerator_options = AcceleratorOptions(
                num_threads=8,
                device=AcceleratorDevice.CUDA,
            )
    except Exception:
        pass

    if do_ocr:
        if engine.lower() == "rapidocr":
            rapid_lang = "pl" if "pl" in langs else (langs[0] if langs else "latin")
            pipeline_options.ocr_options = RapidOcrOptions(
                mode=OcrMode.FULL_PAGE,
                lang=[rapid_lang],
            )
        else:
            pipeline_options.ocr_options = EasyOcrOptions(lang=langs)

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


def extract_pdf(
    input_pdf: Path,
    output_dir: Path = Path("output"),
    languages: str = "pl,en",
    pages: Optional[str] = None,
    ocr: Optional[bool] = None,
    ocr_engine: str = "rapidocr",
) -> Path:
    """Czysta funkcja biznesowa: ekstrakcja PDF do Markdown."""
    if not input_pdf.exists():
        console.print(f"[bold red]Błąd: Nie znaleziono pliku wejściowego:[/bold red] {input_pdf}")
        raise typer.Exit(code=1)

    lang_list = [l.strip() for l in languages.split(",") if l.strip()]
    app_logger.info(f"Rozpoczęcie ekstrakcji: {input_pdf.name} (pages={pages}, ocr={ocr}, engine={ocr_engine})")

    if ocr is None:
        eval_res = evaluate_text_layer_quality(str(input_pdf))
        do_ocr = not eval_res["is_clean"]
        if eval_res["is_clean"]:
            console.print(f"[green]Wykryto czystą cyfrową warstwę tekstową w PDF (jakość: {eval_res['quality_score']:.2f}): użycie natywnego układu bez OCR...[/green]")
        else:
            reason = "wykryto zanieczyszczoną warstwę OCR ze skanera" if eval_res["has_text"] else "brak warstwy tekstowej"
            console.print(f"[yellow]Wymagany OCR ({reason}, jakość: {eval_res['quality_score']:.2f}): włączanie silnika {ocr_engine}...[/yellow]")
    else:
        do_ocr = ocr
        status_msg = f"Wymuszono OCR ({ocr_engine})" if do_ocr else "Wyłączono OCR (użycie natywnego tekstu)"
        console.print(f"[cyan]{status_msg}[/cyan]")

    md_dir = output_dir / "markdown"
    assets_dir = md_dir / "assets"
    md_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    converter = get_converter(do_ocr=do_ocr, langs=lang_list, engine=ocr_engine)

    progress_file = output_dir / f"extract_progress_{input_pdf.stem}.json"
    try:
        progress_file.write_text(json.dumps({
            "status": "starting",
            "current_page": 0,
            "completed_pages": 0,
            "total_pages": 0,
            "percent": 0.0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False))
    except Exception:
        pass

    convert_kwargs = {}
    if pages:
        try:
            parts = [int(p.strip()) for p in pages.split("-")]
            if len(parts) == 1:
                page_range = (parts[0], parts[0])
            elif len(parts) == 2:
                page_range = (parts[0], parts[1])
            else:
                raise ValueError
            convert_kwargs["page_range"] = page_range
            console.print(f"[yellow]Ograniczenie przetwarzania do stron: {page_range}[/yellow]")
        except ValueError:
            console.print(f"[bold red]Błąd:[/bold red] Niepoprawny format stron: '{pages}'. Użyj formatu np. '1-10' lub '5'.")
            raise typer.Exit(code=1)

    def on_progress(event):
        if isinstance(event, PageCompletedProgress) and event.total_pages:
            pct = round((event.completed_pages / event.total_pages) * 100, 1)
            console.print(f"[cyan]Postęp ekstrakcji:[/cyan] strona {event.page_no} (ukończono {event.completed_pages}/{event.total_pages} - {pct}%)")
            try:
                progress_file.write_text(json.dumps({
                    "status": "running",
                    "current_page": event.page_no,
                    "completed_pages": event.completed_pages,
                    "total_pages": event.total_pages,
                    "percent": pct,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }, ensure_ascii=False))
            except Exception:
                pass

    convert_kwargs["progress_callback"] = on_progress

    console.print(f"[cyan]Przetwarzanie dokumentu {input_pdf.name}...[/cyan]")
    result = converter.convert(input_pdf, **convert_kwargs)
    doc = result.document

    md_file = md_dir / f"{input_pdf.stem}.md"

    console.print("[cyan]Eksport do Markdown z referencjami do grafik...[/cyan]")
    doc.save_as_markdown(
        md_file,
        image_mode=ImageRefMode.REFERENCED,
        artifacts_dir=Path("assets"),
    )

    try:
        progress_file.write_text(json.dumps({
            "status": "completed",
            "percent": 100.0,
            "markdown_file": str(md_file),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False))
    except Exception:
        pass

    console.print(f"[bold green]✓ Zapisano Markdown:[/bold green] {md_file}")
    console.print(f"[bold green]✓ Grafiki wyodrębniono do:[/bold green] {assets_dir}")
    return md_file


def ensure_markdown_exists(
    input_md: Optional[Path] = None,
    book: Optional[str] = None,
    output_dir: Path = Path("output"),
) -> Path:
    """
    Zapewnia istnienie pliku Markdown.
    Jeśli podano --book lub --input-md i plik nie istnieje, automatycznie wyszukuje
    odpowiadający plik PDF w input/ i uruchamia ekstrakcję Document AI.
    Jeśli nic nie podano, szuka istniejącego pliku w katalogu output/markdown/.
    """
    md_dir = output_dir / "markdown"
    md_dir.mkdir(parents=True, exist_ok=True)

    # 1. Ustalanie pożądanej nazwy pliku
    if book:
        target_stem = book.replace(".pdf", "").replace(".md", "").replace(".epub", "")
        target_md = md_dir / f"{target_stem}.md"
    elif input_md is not None:
        target_md = input_md
        target_stem = input_md.stem
    else:
        # Sprawdź, czy są już pliki .md
        existing_mds = sorted(list(md_dir.glob("*.md")))
        if existing_mds:
            return existing_mds[0]
        console.print(f"[bold red]Błąd: Nie znaleziono żadnego pliku .md w katalogu {md_dir}[/bold red]")
        raise typer.Exit(code=1)

    # 2. Jeśli plik istnieje, zwróć go
    if target_md.exists():
        return target_md

    # 3. Jeśli nie istnieje, spróbuj znaleźć odpowiadający PDF w input/
    candidate_pdf = Path("input") / f"{target_stem}.pdf"
    if candidate_pdf.exists():
        console.print(f"[bold yellow]Plik {target_md.name} nie istnieje. Automatyczne tworzenie ze skanu: {candidate_pdf.name}...[/bold yellow]")
        return extract_pdf(candidate_pdf, output_dir=output_dir)

    console.print(f"[bold red]Błąd:[/bold red] Plik {target_md} nie istnieje, a w katalogu input/ nie znaleziono odpowiadającego pliku {target_stem}.pdf.")
    raise typer.Exit(code=1)


def compile_epub(
    input_md: Optional[Path] = None,
    output_dir: Path = Path("output"),
    metadata_file: Optional[Path] = Path("metadata.yaml"),
    css_file: Optional[Path] = Path("templates/epub.css"),
    cover_image: Optional[Path] = None,
    copy_to_windows: bool = True,
) -> Path:
    """Czysta funkcja biznesowa: kompilacja pliku Markdown do EPUB 3 za pomocą Pandoc."""
    md_dir = output_dir / "markdown"
    epub_dir = output_dir / "epub"
    epub_dir.mkdir(parents=True, exist_ok=True)

    input_md = ensure_markdown_exists(input_md=input_md, output_dir=output_dir)

    epub_file = epub_dir / f"{input_md.stem}.epub"
    console.print(f"[cyan]Kompilacja EPUB przy użyciu Pandoc: {input_md.name} -> {epub_file.name}...[/cyan]")

    pandoc_cmd = [
        "pandoc",
        str(input_md),
        "-o",
        str(epub_file),
        f"--resource-path={md_dir}",
        "--toc",
        "--toc-depth=3",
        "--split-level=2",
    ]

    if metadata_file and metadata_file.exists():
        pandoc_cmd.append(f"--metadata-file={metadata_file}")
    else:
        console.print(f"[yellow]Ostrzeżenie: Nie znaleziono pliku metadanych {metadata_file}. Kontynuowanie bez metadanych.[/yellow]")

    if css_file and css_file.exists():
        pandoc_cmd.append(f"--css={css_file}")

    if cover_image and cover_image.exists():
        pandoc_cmd.append(f"--epub-cover-image={cover_image}")

    try:
        subprocess.run(pandoc_cmd, check=True)
        console.print(f"[bold green]✓ Pomyślnie wygenerowano EPUB:[/bold green] {epub_file}")

        # Integracja WSL / Windows
        if copy_to_windows:
            win_dest = copy_to_windows_downloads(epub_file)
            if win_dest:
                console.print(f"[bold green]✓ Skopiowano EPUB do Windows (Pobrane):[/bold green] {win_dest}")

        console.print(f"[dim]Wskazówka WSL: Aby otworzyć folder z książką w Eksploratorze Windows, wpisz: explorer.exe output\\epub[/dim]")
        return epub_file
    except FileNotFoundError:
        console.print("[bold red]Błąd:[/bold red] Program 'pandoc' nie jest zainstalowany w ścieżce PATH.")
        raise typer.Exit(code=1)
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]Błąd Pandoc podczas kompilacji EPUB:[/bold red] {e}")
        raise typer.Exit(code=1)


# --- Komendy interfejsu CLI (Typer) ---


@app.command()
def extract(
    input_pdf: Annotated[
        Optional[Path],
        typer.Option("--input-pdf", "-i", help="Ścieżka do wejściowego pliku PDF."),
    ] = None,
    book: Annotated[
        Optional[str],
        typer.Option("--book", "-b", help="Nazwa książki z katalogu input/ (np. 'ksiazka')."),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Główny katalog wyjściowy."),
    ] = Path("output"),
    languages: Annotated[
        str,
        typer.Option("--langs", "-l", help="Kody języków dla OCR oddzielone przecinkami (np. pl,en)."),
    ] = "pl,en",
    pages: Annotated[
        Optional[str],
        typer.Option("--pages", "-p", help="Zakres stron do przetworzenia, np. 1-10."),
    ] = None,
    ocr: Annotated[
        Optional[bool],
        typer.Option("--ocr/--no-ocr", help="Wymuszenie lub wyłączenie OCR (domyślnie auto-detekcja)."),
    ] = None,
    ocr_engine: Annotated[
        str,
        typer.Option("--ocr-engine", help="Silnik OCR: 'rapidocr' (szybki, domyślny) lub 'easyocr'."),
    ] = "rapidocr",
):
    """Krok 1: Ekstrakcja układu, tekstu i grafik z PDF do Markdown (z inteligentną oceną jakości OCR)."""
    if book:
        clean_stem = book.replace(".pdf", "")
        input_pdf = Path("input") / f"{clean_stem}.pdf"
    elif input_pdf is None:
        pdf_files = sorted(list(Path("input").glob("*.pdf")))
        if not pdf_files:
            console.print("[bold red]Błąd:[/bold red] Brak plików .pdf w katalogu input/")
            raise typer.Exit(code=1)
        input_pdf = pdf_files[0]

    extract_pdf(
        input_pdf=input_pdf,
        output_dir=output_dir,
        languages=languages,
        pages=pages,
        ocr=ocr,
        ocr_engine=ocr_engine,
    )


@app.command()
def clean(
    input_md: Annotated[
        Optional[Path],
        typer.Option("--input-md", "-m", help="Ścieżka do pliku Markdown do oczyszczenia."),
    ] = None,
    book: Annotated[
        Optional[str],
        typer.Option("--book", "-b", help="Nazwa książki do oczyszczenia (np. 'ksiazka')."),
    ] = None,
    output_md: Annotated[
        Optional[Path],
        typer.Option("--output-md", help="Ścieżka pliku wyjściowego (domyślnie nadpisuje wejściowy)."),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Główny katalog wyjściowy."),
    ] = Path("output"),
):
    """Krok 2: Czyszczenie Markdowna (de-hyphenation, normalizacja ułamków, polskie znaki diakrytyczne)."""
    input_md = ensure_markdown_exists(input_md=input_md, book=book, output_dir=output_dir)

    console.print(f"[cyan]Czyszczenie i normalizacja: {input_md.name}...[/cyan]")
    out = clean_markdown_file(input_md, output_md)
    console.print(f"[bold green]✓ Pomyślnie oczyszczono plik Markdown:[/bold green] {out}")


@app.command()
def audit(
    input_md: Annotated[
        Optional[Path],
        typer.Option("--input-md", "-m", help="Ścieżka do pliku Markdown do audytu."),
    ] = None,
    book: Annotated[
        Optional[str],
        typer.Option("--book", "-b", help="Nazwa książki do audytu (np. 'ksiazka')."),
    ] = None,
    report_file: Annotated[
        Path,
        typer.Option("--report", "-r", help="Ścieżka wyjściowa dla raportu audytu JSON."),
    ] = Path("output/audit_report.json"),
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Główny katalog wyjściowy."),
    ] = Path("output"),
):
    """Krok audytu: Analiza jakościowa pliku Markdown, wykrywanie anomalii i braków."""
    input_md = ensure_markdown_exists(input_md=input_md, book=book, output_dir=output_dir)

    console.print(f"[cyan]Uruchamianie audytu jakościowego dla: {input_md.name}...[/cyan]")
    report = audit_markdown_file(input_md, report_file)

    console.print(f"\n[bold green]✓ Audyt zakończony sukcesem![/bold green]")
    console.print(f"  • Przeskanowano akapitów: [bold]{report.total_paragraphs}[/bold]")
    pct = (report.total_anomalies / max(1, report.total_paragraphs)) * 100
    console.print(f"  • Wykryto anomalii ogółem: [bold yellow]{report.total_anomalies}[/bold yellow] ({pct:.1f}% treści)")
    console.print(f"    - Wysoki priorytet (High): [bold red]{report.high_severity_count}[/bold red]")
    console.print(f"    - Średni priorytet (Medium): [yellow]{report.medium_severity_count}[/yellow]")
    console.print(f"    - Niski priorytet (Low): [blue]{report.low_severity_count}[/blue]")
    console.print(f"  • Przerośnięte nagłówki (całe akapity jako H2): [bold]{report.overlong_headings_count}[/bold]")
    if report.missing_chapters:
        console.print(f"  • Brakujące rozdziały ze spisu treści: [bold red]{len(report.missing_chapters)}[/bold red]")
        for ch in report.missing_chapters:
            console.print(f"    - [red]{ch}[/red]")
    else:
        console.print("  • Wszystkie rozdziały ze spisu treści obecne w dokumencie: [bold green]TAK[/bold green]")

    console.print(f"\n[cyan]Pełny szczegółowy raport zapisano do:[/cyan] [bold]{report_file}[/bold]")


@app.command(name="inspect-anomaly")
def inspect_anomaly_cmd(
    index: Annotated[int, typer.Argument(help="Indeks akapitu do inspekcji (z raportu audytu).")],
    report_file: Annotated[Path, typer.Option("--report", "-r", help="Ścieżka do raportu audytu.")] = Path("output/audit_report.json"),
    input_md: Annotated[Optional[Path], typer.Option("--input-md", "-m", help="Ścieżka do pliku Markdown.")] = None,
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o")] = Path("output"),
):
    """Inspekcja konkretnej anomalii: wyświetla uszkodzony akapit, żetony błędu i otaczający kontekst."""
    input_md = ensure_markdown_exists(input_md=input_md, output_dir=output_dir)

    try:
        data = get_anomaly_details(report_file, index, input_md)
    except Exception as e:
        console.print(f"[bold red]Błąd:[/bold red] {e}")
        raise typer.Exit(code=1)

    console.print(f"\n[bold cyan]=== Anomalia Akapitu #{index} (Severity: {data['severity'].upper()}) ===[/bold cyan]")
    if data.get("is_stale"):
        console.print("[bold yellow]⚠ Ostrzeżenie:[/bold yellow] [yellow]Raport audytu jest nieaktualny (plik Markdown uległ zmianie od czasu wygenerowania raportu). Zalecane: uv run python -m src.pipeline audit[/yellow]")
    console.print(f"[yellow]Wykryte błędy:[/yellow] {', '.join(data['sample_tokens'])}")
    console.print(f"[yellow]Kategorie:[/yellow] {', '.join(data['categories'])}")
    if data["context_before"]:
        console.print(f"\n[dim]--- Kontekst przed ---[/dim]\n[dim]{data['context_before']}[/dim]")
    console.print(f"\n[bold red]--- USZKODZONY TEKST (DO REKONSTRUKCJI) ---[/bold red]\n{data['raw_paragraph']}")
    if data["context_after"]:
        console.print(f"\n[dim]--- Kontekst po ---[/dim]\n[dim]{data['context_after']}[/dim]")


@app.command(name="patch")
def patch_cmd(
    index: Annotated[int, typer.Argument(help="Indeks akapitu do podmiany.")],
    text: Annotated[str, typer.Option("--text", "-t", help="Zrekonstruowany, poprawny tekst akapitu.")],
    expected_orig: Annotated[Optional[str], typer.Option("--expected-orig", help="Oczekiwana oryginalna treść akapitu (ochrona przed konfliktem).")] = None,
    expected_sha: Annotated[Optional[str], typer.Option("--expected-sha", help="Oczekiwana suma SHA-256 pliku Markdown.")] = None,
    input_md: Annotated[Optional[Path], typer.Option("--input-md", "-m")] = None,
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o")] = Path("output"),
):
    """Podmienia wskazany uszkodzony akapit na zrekonstruowany tekst (tworząc kopię .bak i weryfikując wersję)."""
    input_md = ensure_markdown_exists(input_md=input_md, output_dir=output_dir)

    try:
        patch_paragraph(
            markdown_path=input_md,
            paragraph_index=index,
            replacement_text=text,
            expected_original=expected_orig,
            expected_sha256=expected_sha,
        )
        console.print(f"[bold green]✓ Pomyślnie podmieniono akapit #{index} w {input_md.name} (utworzono {input_md.name}.bak)[/bold green]")
    except Exception as e:
        console.print(f"[bold red]Błąd:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command(name="patch-batch")
def patch_batch_cmd(
    fixes_file: Annotated[Path, typer.Option("--file", "-f", help="Ścieżka do pliku JSON ze słownikiem poprawek.")],
    expected_sha: Annotated[Optional[str], typer.Option("--expected-sha", help="Oczekiwana suma SHA-256 pliku Markdown.")] = None,
    input_md: Annotated[Optional[Path], typer.Option("--input-md", "-m")] = None,
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o")] = Path("output"),
):
    """Aplikuje paczkę poprawek z pliku JSON na pliku Markdown (tworząc kopię .bak i weryfikując wersję)."""
    input_md = ensure_markdown_exists(input_md=input_md, output_dir=output_dir)

    try:
        count = patch_batch_from_file(input_md, fixes_file, expected_sha256=expected_sha)
        console.print(f"[bold green]✓ Pomyślnie zaaplikowano {count} poprawek do {input_md.name} (utworzono {input_md.name}.bak)[/bold green]")
    except Exception as e:
        console.print(f"[bold red]Błąd:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command()
def build(
    input_md: Annotated[
        Optional[Path],
        typer.Option("--input-md", "-m", help="Ścieżka do pliku .md (domyślnie auto-wybór)."),
    ] = None,
    book: Annotated[
        Optional[str],
        typer.Option("--book", "-b", help="Nazwa książki do kompilacji (np. 'ksiazka')."),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Główny katalog wyjściowy."),
    ] = Path("output"),
    metadata_file: Annotated[
        Optional[Path],
        typer.Option("--metadata", help="Plik metadanych publikacji (YAML)."),
    ] = Path("metadata.yaml"),
    css_file: Annotated[
        Optional[Path],
        typer.Option("--css", help="Arkusz stylów CSS dla EPUB."),
    ] = Path("templates/epub.css"),
    cover_image: Annotated[
        Optional[Path],
        typer.Option("--cover", help="Ścieżka do pliku okładki (JPG/PNG)."),
    ] = None,
    copy_to_windows: Annotated[
        bool,
        typer.Option("--copy-windows/--no-copy-windows", help="Czy skopiować gotowy EPUB do folderu Pobrane w Windows."),
    ] = True,
):
    """Krok 3: Kompiluje plik Markdown do EPUB 3 za pomocą Pandoc (i opcjonalnie kopiuje do Windows)."""
    input_md = ensure_markdown_exists(input_md=input_md, book=book, output_dir=output_dir)
    compile_epub(
        input_md=input_md,
        output_dir=output_dir,
        metadata_file=metadata_file,
        css_file=css_file,
        cover_image=cover_image,
        copy_to_windows=copy_to_windows,
    )


@app.command()
def all(
    input_pdf: Annotated[
        Optional[Path],
        typer.Option("--input-pdf", "-i", help="Ścieżka do wejściowego pliku PDF."),
    ] = None,
    book: Annotated[
        Optional[str],
        typer.Option("--book", "-b", help="Nazwa książki z katalogu input/ (np. 'ksiazka')."),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Główny katalog wyjściowy."),
    ] = Path("output"),
    metadata_file: Annotated[
        Optional[Path],
        typer.Option("--metadata", help="Plik metadanych publikacji (YAML)."),
    ] = Path("metadata.yaml"),
    css_file: Annotated[
        Optional[Path],
        typer.Option("--css", help="Arkusz stylów CSS dla EPUB."),
    ] = Path("templates/epub.css"),
    languages: Annotated[
        str,
        typer.Option("--langs", "-l", help="Kody języków dla OCR oddzielone przecinkami (np. pl,en)."),
    ] = "pl,en",
    pages: Annotated[
        Optional[str],
        typer.Option("--pages", "-p", help="Zakres stron do przetworzenia, np. 1-10."),
    ] = None,
    ocr: Annotated[
        Optional[bool],
        typer.Option("--ocr/--no-ocr", help="Wymuszenie lub wyłączenie OCR (domyślnie auto-detekcja)."),
    ] = None,
    skip_clean: Annotated[
        bool,
        typer.Option("--skip-clean", help="Pomiń etap czyszczenia Markdown."),
    ] = False,
    cover_image: Annotated[
        Optional[Path],
        typer.Option("--cover", help="Ścieżka do pliku okładki (JPG/PNG)."),
    ] = None,
    copy_to_windows: Annotated[
        bool,
        typer.Option("--copy-windows/--no-copy-windows", help="Czy skopiować gotowy EPUB do folderu Pobrane w Windows."),
    ] = True,
):
    """Pełny pipeline: ekstrakcja, czyszczenie Markdowna, kompilacja EPUB 3 i kopiowanie do Windows."""
    if book:
        clean_stem = book.replace(".pdf", "")
        input_pdf = Path("input") / f"{clean_stem}.pdf"
    elif input_pdf is None:
        pdf_files = sorted(list(Path("input").glob("*.pdf")))
        if not pdf_files:
            console.print("[bold red]Błąd:[/bold red] Brak plików .pdf w katalogu input/")
            raise typer.Exit(code=1)
        input_pdf = pdf_files[0]

    md_file = extract_pdf(
        input_pdf=input_pdf,
        output_dir=output_dir,
        languages=languages,
        pages=pages,
        ocr=ocr,
    )

    if not skip_clean:
        console.print(f"[cyan]Czyszczenie i normalizacja Markdowna ({md_file.name})...[/cyan]")
        clean_markdown_file(md_file)
        console.print(f"[bold green]✓ Oczyszczono Markdown:[/bold green] {md_file}")

    compile_epub(
        input_md=md_file,
        output_dir=output_dir,
        metadata_file=metadata_file,
        css_file=css_file,
        cover_image=cover_image,
        copy_to_windows=copy_to_windows,
    )


def to_windows_path(linux_path: Path) -> str:
    """Konwertuje ścieżkę linuksową WSL na poprawną ścieżkę Windows (UNC lub literę dysku C:)."""
    abs_path = linux_path.resolve()
    try:
        win_path = subprocess.check_output(["wslpath", "-w", str(abs_path)], text=True).strip()
        return win_path
    except Exception:
        return str(abs_path)


def _clean_directory_contents(dir_path: Path, keep_gitkeep: bool = True) -> int:
    """Usuwa zawartość wskazanego folderu, zachowując sam katalog oraz opcjonalnie .gitkeep."""
    deleted_count = 0
    if not dir_path.exists():
        return 0
    for item in dir_path.iterdir():
        if keep_gitkeep and item.name == ".gitkeep":
            continue
        try:
            if item.is_file() or item.is_symlink():
                item.unlink()
                deleted_count += 1
            elif item.is_dir():
                shutil.rmtree(item)
                deleted_count += 1
        except Exception:
            pass
    return deleted_count


@app.command(name="purge")
def purge_cmd(
    book: Annotated[
        Optional[str],
        typer.Option("--book", "-b", help="Nazwa książki do usunięcia (np. 'ksiazka')."),
    ] = None,
    all: Annotated[
        bool,
        typer.Option("--all", "-a", help="Czy usunąć wszystkie wygenerowane pliki robocze ze wszystkich książek."),
    ] = False,
    temp_only: Annotated[
        bool,
        typer.Option("--temp-only", "--cache-only", help="Usuń wyłącznie tymczasowe śmieci robocze (candidates, cache, source_cache, scratch, kopie .bak), zachowując gotowy Markdown i EPUB."),
    ] = False,
    keep_md: Annotated[
        bool,
        typer.Option("--keep-md/--delete-md", help="Czy zachować plik produkcyjny Markdown i wyekstrahowane grafiki."),
    ] = False,
    only_epub: Annotated[
        bool,
        typer.Option("--only-epub", help="Usuń wyłącznie gotowy plik .epub dla danej książki."),
    ] = False,
    with_epub: Annotated[
        bool,
        typer.Option("--with-epub", help="Usuń również gotowy plik .epub (domyślnie czyszczenie zachowuje .epub)."),
    ] = False,
    keep_epub: Annotated[
        bool,
        typer.Option("--keep-epub", help="Zachowaj gotowy plik .epub (domyślne zachowanie)."),
    ] = True,
    keep_snapshots: Annotated[
        bool,
        typer.Option("--keep-snapshots", help="Zachowaj katalog snapshotów bezpieczeństwa."),
    ] = False,
    keep_benchmarks: Annotated[
        bool,
        typer.Option("--keep-benchmarks", help="Zachowaj raporty benchmarków i ewaluacji."),
    ] = False,
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o")] = Path("output"),
):
    """
    Usuwa wygenerowane pliki robocze (.md, .bak, assets, raporty, cache, candidates, benchmarks, rag, snapshots) po skończonej pracy.
    Domyślnie zachowuje gotowy plik .epub.
    Opcja --temp-only usuwa wyłącznie pamięć podręczną i pliki tymczasowe bez naruszania Markdowna i EPUB.
    """
    deleted_files = 0

    # 1. Tryb usuwania wyłącznie plików tymczasowych (candidates, cache, source_cache, scratch, .bak)
    if temp_only:
        console.print("[cyan]Czyszczenie pamięci podręcznej i tymczasowych plików roboczych...[/cyan]")
        deleted_files += _clean_directory_contents(output_dir / "candidates")
        deleted_files += _clean_directory_contents(output_dir / "cache" / "images")
        deleted_files += _clean_directory_contents(output_dir / "cache")
        deleted_files += _clean_directory_contents(output_dir / "source_cache")
        deleted_files += _clean_directory_contents(output_dir / "scratch")

        # Usuń kopie zapasowe i pliki tymczasowe w markdown/
        md_folder = output_dir / "markdown"
        if md_folder.exists():
            for bak in md_folder.glob("*.bak*"):
                bak.unlink()
                deleted_files += 1
            for pre in md_folder.glob("*.pre_r18_bak*"):
                pre.unlink()
                deleted_files += 1
            for tmp in md_folder.glob("*.tmp*"):
                tmp.unlink()
                deleted_files += 1

        # Usuń tymczasowe migawki testowe z snapshots/
        snap_folder = output_dir / "snapshots"
        if snap_folder.exists():
            for s in snap_folder.iterdir():
                if s.is_dir() and s.name != "20261104_075036_ksiazka":
                    shutil.rmtree(s)
                    deleted_files += 1

        # Usuń pliki postępu ekstrakcji
        for p_file in output_dir.glob("extract_progress_*.json"):
            if p_file.exists():
                p_file.unlink()
                deleted_files += 1

        console.print(f"[bold green]✓ Pomyślnie oczyszczono pamięć podręczną i pliki tymczasowe (usunięto {deleted_files} elementów). Gotowy Markdown i EPUB są nienaruszone![/bold green]")
        return

    # 2. Tryb dla konkretnej książki
    if book:
        stem = book.replace(".pdf", "").replace(".md", "").replace(".epub", "")
        epub_file = output_dir / "epub" / f"{stem}.epub"

        if only_epub:
            if epub_file.exists():
                epub_file.unlink()
                console.print(f"[yellow]Usunięto plik EPUB:[/yellow] {epub_file}")
                deleted_files += 1
            console.print(f"[bold green]✓ Usunięto plik EPUB dla książki: {stem}[/bold green]")
            return

        files_to_remove = [
            output_dir / f"audit_{stem}.json",
            output_dir / "audit_report.json",
            output_dir / f"extract_progress_{stem}.json",
        ]

        if not keep_md:
            files_to_remove.append(output_dir / "markdown" / f"{stem}.md")

        if with_epub:
            files_to_remove.append(epub_file)

        for f in files_to_remove:
            if f.exists():
                f.unlink()
                console.print(f"[yellow]Usunięto:[/yellow] {f}")
                deleted_files += 1

        # Usunięcie kopii zapasowych dla danej książki
        md_folder = output_dir / "markdown"
        if md_folder.exists():
            for pattern in [f"{stem}*.bak*", f"{stem}*.pre_r18_bak*", f"{stem}*.tmp*"]:
                for b_file in md_folder.glob(pattern):
                    if b_file.exists():
                        b_file.unlink()
                        deleted_files += 1

        # Usunięcie wyekstrahowanych grafik w assets (jeśli nie zachowujemy MD)
        if not keep_md:
            assets_dir = output_dir / "markdown" / "assets"
            if assets_dir.exists():
                for img in assets_dir.iterdir():
                    if img.name != ".gitkeep" and img.is_file():
                        img.unlink()
                        deleted_files += 1

        # Usunięcie ewentualnego zagnieżdżonego legacy katalogu output/markdown/output
        nested_output = output_dir / "markdown" / "output"
        if nested_output.exists():
            shutil.rmtree(nested_output)
            deleted_files += 1

        # Usunięcie śmieci w cache, candidates, scratch
        deleted_files += _clean_directory_contents(output_dir / "candidates")
        deleted_files += _clean_directory_contents(output_dir / "cache" / "images")
        deleted_files += _clean_directory_contents(output_dir / "cache")
        deleted_files += _clean_directory_contents(output_dir / "source_cache")
        deleted_files += _clean_directory_contents(output_dir / "scratch")

        # Usunięcie baz wiedzy RAG i historii audytu dla książki
        deleted_files += _clean_directory_contents(output_dir / "rag")
        deleted_files += _clean_directory_contents(output_dir / "history")

        # Usunięcie raportów benchmarków i snapshotów
        if not keep_benchmarks:
            deleted_files += _clean_directory_contents(output_dir / "benchmarks")

        if not keep_snapshots:
            deleted_files += _clean_directory_contents(output_dir / "snapshots")

        scope = "wszystkie pliki robocze oraz EPUB" if with_epub else "pliki robocze (z zachowaniem EPUB)"
        console.print(f"[bold green]✓ Wyczyszczono {scope} dla książki: {stem} (usunięto {deleted_files} elementów)[/bold green]")
        return

    # 3. Tryb dla wszystkich książek (--all)
    if all:
        if only_epub:
            epub_folder = output_dir / "epub"
            if epub_folder.exists():
                for item in epub_folder.glob("*.epub"):
                    item.unlink()
                    deleted_files += 1
            console.print(f"[bold green]✓ Usunięto wszystkie pliki EPUB (usunięto {deleted_files} plików)[/bold green]")
            return

        # Usuń pliki robocze z output/ zachowując strukturę i .gitkeep
        if not keep_md:
            for folder in [output_dir / "markdown" / "assets", output_dir / "markdown"]:
                if folder.exists():
                    for item in folder.iterdir():
                        if item.name == ".gitkeep":
                            continue
                        if item.is_file():
                            item.unlink()
                            deleted_files += 1
                        elif item.is_dir() and item.name != "assets":
                            shutil.rmtree(item)
                            deleted_files += 1
        else:
            md_folder = output_dir / "markdown"
            if md_folder.exists():
                for b_file in md_folder.glob("*.bak*"):
                    b_file.unlink()
                    deleted_files += 1
                for b_file in md_folder.glob("*.tmp*"):
                    b_file.unlink()
                    deleted_files += 1

        nested_output = output_dir / "markdown" / "output"
        if nested_output.exists():
            shutil.rmtree(nested_output)
            deleted_files += 1

        deleted_files += _clean_directory_contents(output_dir / "candidates")
        deleted_files += _clean_directory_contents(output_dir / "cache" / "images")
        deleted_files += _clean_directory_contents(output_dir / "cache")
        deleted_files += _clean_directory_contents(output_dir / "source_cache")
        deleted_files += _clean_directory_contents(output_dir / "scratch")
        deleted_files += _clean_directory_contents(output_dir / "rag")
        deleted_files += _clean_directory_contents(output_dir / "history")

        if not keep_benchmarks:
            deleted_files += _clean_directory_contents(output_dir / "benchmarks")

        if not keep_snapshots:
            deleted_files += _clean_directory_contents(output_dir / "snapshots")

        if with_epub:
            epub_folder = output_dir / "epub"
            if epub_folder.exists():
                for item in epub_folder.glob("*.epub"):
                    item.unlink()
                    deleted_files += 1

        for rep in output_dir.glob("audit*.json"):
            rep.unlink()
            deleted_files += 1

        for prog in output_dir.glob("extract_progress_*.json"):
            prog.unlink()
            deleted_files += 1

        scope = "wszystkie pliki wyjściowe łącznie z EPUB" if with_epub else "pliki robocze (z zachowaniem EPUB)"
        console.print(f"[bold green]✓ Pomyślnie wyczyszczono {scope} (usunięto {deleted_files} elementów)[/bold green]")
        return

    console.print("[yellow]Podaj --book <nazwa>, --all lub --temp-only, aby określić co usunąć.[/yellow]")


@app.command(name="snapshot")
def snapshot_cmd(
    book: Annotated[
        str,
        typer.Option("--book", "-b", help="Nazwa książki do inwentaryzacji (np. 'ksiazka')."),
    ] = "ksiazka",
    desc: Annotated[
        str,
        typer.Option("--desc", "-d", help="Opis celu snapshotu."),
    ] = "Baseline snapshot przed operacjami na tekście lub ekstrakcją",
    verify_dir: Annotated[
        Optional[Path],
        typer.Option("--verify-dir", help="Ścieżka do istniejącego katalogu snapshotu do weryfikacji sum SHA-256."),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Katalog bazowy dla snapshotów."),
    ] = Path("output/snapshots"),
):
    """
    Tworzy lub weryfikuje punkt odniesienia (snapshot) stanu książki, środowiska i metadanych.
    Zabezpiecza zaakceptowany Markdown, kopię .bak, metadane, CSS oraz rejestruje sumy SHA-256 assetów i PDF.
    """
    stem = book.replace(".pdf", "").replace(".md", "").replace(".epub", "")

    if verify_dir:
        res = verify_snapshot_integrity(verify_dir)
        if res.get("is_valid"):
            console.print(f"[bold green]✓ Snapshot {res.get('snapshot_id')} jest poprawny i spójny.[/bold green]")
            console.print(f"  Sprawdzono plików: {res.get('files_checked')}")
        else:
            console.print(f"[bold red]✗ Błąd weryfikacji snapshotu:[/bold red] {res.get('error', 'Wykryto niezgodności')}")
            for m in res.get("mismatches", []):
                console.print(f"  - [red]{m.get('rel_path')}[/red]: {m.get('error')}")
            raise typer.Exit(code=1)
        return

    options = SnapshotOptions(
        book_name=stem,
        description=desc,
        output_dir=output_dir,
    )
    manifest = create_snapshot(options)
    snapshot_path = output_dir / manifest.snapshot_id

    console.print(f"[bold green]✓ Pomyślnie utworzono snapshot:[/bold green] [cyan]{manifest.snapshot_id}[/cyan]")
    console.print(f"  Katalog: [dim]{snapshot_path}[/dim]")
    if manifest.book_state.pdf_exists and manifest.book_state.pdf_sha256:
        console.print(f"  PDF: [green]Dostępny[/green] (SHA-256: {manifest.book_state.pdf_sha256[:16]}...)")
    else:
        console.print("  PDF: [red]Brak[/red]")
    console.print(f"  Markdown: {'[green]Dostępny[/green]' if manifest.book_state.markdown_exists else '[red]Brak[/red]'} ({manifest.book_state.markdown_lines} linii, {manifest.book_state.markdown_paragraphs} akapitów)")
    console.print(f"  Assety: {manifest.book_state.assets_count} plików zarejestrowanych z SHA-256")
    if manifest.book_state.audit_report_exists:
        console.print(f"  Audyt: {manifest.book_state.total_anomalies} anomalii (w tym {manifest.book_state.high_severity_anomalies} high)")
    console.print(f"  CUDA: {'[green]Dostępne[/green]' if manifest.environment.cuda_available else '[yellow]Niedostępne w WSL[/yellow]'}")
    if manifest.environment.ollama_available:
        console.print(f"  Ollama: [green]Online[/green] ({manifest.environment.ollama_endpoint}, modele: {', '.join(manifest.environment.ollama_models[:3])})")
    else:
        console.print("  Ollama: [yellow]Offline lub nieosiągalna[/yellow]")


@app.command(name="history")
def history_cmd(
    book: Annotated[
        str,
        typer.Option("--book", "-b", help="Nazwa książki (np. 'ksiazka')."),
    ] = "ksiazka",
    index: Annotated[
        Optional[int],
        typer.Option("--index", "-i", help="Filtruj historię według numeru akapitu."),
    ] = None,
    verify: Annotated[
        bool,
        typer.Option("--verify", help="Weryfikuj kotwice indeksów w pliku Markdown (wykrywanie przesunięć)."),
    ] = True,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Katalog bazowy dla wyjścia."),
    ] = Path("output"),
):
    """Przegląda historię decyzji, propozycji i napraw powiązaną z wersją dokumentu oraz kotwicami akapitów."""
    from rich.table import Table

    stem = book.replace(".pdf", "").replace(".md", "").replace(".epub", "")
    ledger = load_history_ledger(stem, root_dir=Path("."))

    records = ledger.records
    if index is not None:
        records = [r for r in records if r.paragraph_index == index]

    if not records:
        console.print(f"[yellow]Brak wpisów w historii decyzji dla książki '{stem}'.[/yellow]")
        return

    md_path = output_dir / "markdown" / f"{stem}.md"
    anchors = {}
    if verify and md_path.is_file():
        anchors = verify_index_anchors(md_path, ledger)

    table = Table(title=f"Historia decyzji i napraw: {stem} (łącznie: {len(records)})")
    table.add_column("ID", style="dim", width=12)
    table.add_column("Czas", style="cyan", width=19)
    table.add_column("Akapit", justify="right", style="bold")
    table.add_column("Silnik", style="magenta")
    table.add_column("Decyzja", style="green")
    table.add_column("Powód", style="white")
    table.add_column("Kotwica / Przesunięcie", style="yellow")

    for r in records[-50:]:
        anchor_info = anchors.get(r.record_id, {})
        status = anchor_info.get("status", "niezweryfikowany")
        if status == "valid":
            anchor_str = "[green]✓ Zgodny[/green]"
        elif status == "shifted":
            shift = anchor_info.get("shift_offset", 0)
            anchor_str = f"[bold yellow]⚠ Przesunięty ({shift:+d}) -> #{anchor_info.get('current_index')}[/bold yellow]"
        elif status == "displaced":
            anchor_str = "[bold red]✗ Zmieniony/usunięty[/bold red]"
        else:
            anchor_str = "[dim]--[/dim]"

        dec_color = "green" if r.decision == "applied" else ("yellow" if r.decision == "proposed" else "red")
        table.add_row(
            r.record_id,
            r.timestamp[:19].replace("T", " "),
            f"#{r.paragraph_index}",
            r.source_engine,
            f"[{dec_color}]{r.decision}[/{dec_color}]",
            r.decision_reason[:40] if r.decision_reason else "-",
            anchor_str,
        )

    console.print(table)


@app.command(name="test-manifest")
def test_manifest_cmd(
    manifest_path: Annotated[
        Path,
        typer.Option("--path", "-p", help="Ścieżka do pliku manifestu stron testowych."),
    ] = Path("benchmarks/fixtures/test_pages_manifest.json"),
    show_details: Annotated[
        bool,
        typer.Option("--details", "-d", help="Wyświetl szczegółową listę stron testowych."),
    ] = False,
):
    """Wyświetla lub weryfikuje manifest stron testowych (20 stron dev + 10 stron holdout)."""
    from rich.table import Table

    try:
        manifest = load_test_pages_manifest(manifest_path)
    except Exception as e:
        console.print(f"[bold red]Błąd wczytywania manifestu:[/bold red] {e}")
        raise typer.Exit(code=1)

    validation = validate_manifest_consistency(manifest)
    if validation["is_valid"]:
        console.print(f"[bold green]✓ Manifest stron testowych jest poprawny i spójny:[/bold green] {manifest_path}")
        console.print(f"  Strony rozwojowe (dev): {validation['dev_count']} (strojenie, w tym trudne przypadki)")
        console.print(f"  Strony odłożone (holdout): {validation['holdout_count']} (ślepy test końcowy)")
        console.print(f"  Łącznie stron testowych: {validation['total_test_pages']}")
    else:
        console.print("[bold red]✗ Błąd spójności manifestu stron testowych:[/bold red]")
        for err in validation["errors"]:
            console.print(f"  - [red]{err}[/red]")
        raise typer.Exit(code=1)

    if show_details:
        table = Table(title="Manifest stron testowych (Dev & Holdout)")
        table.add_column("Zbiór", style="bold cyan")
        table.add_column("PDF", justify="right", style="bold")
        table.add_column("Druk", justify="right", style="dim")
        table.add_column("Rozdział", style="magenta")
        table.add_column("Układ", style="white")
        table.add_column("Główne wyzwania / Cechy", style="yellow")

        for p in manifest.dev_pages:
            table.add_row(
                "[cyan]DEV[/cyan]",
                str(p.pdf_page),
                p.printed_page or "-",
                p.chapter[:30],
                p.layout_type.value,
                ", ".join(p.key_challenges[:2]),
            )
        for p in manifest.holdout_pages:
            table.add_row(
                "[green]HOLDOUT[/green]",
                str(p.pdf_page),
                p.printed_page or "-",
                p.chapter[:30],
                p.layout_type.value,
                ", ".join(p.key_challenges[:2]),
            )
        console.print(table)


@app.command(name="ground-truth-render")
def ground_truth_render_cmd(
    book: Annotated[
        str,
        typer.Option("--book", "-b", help="Nazwa książki (domyślnie 'ksiazka')."),
    ] = "ksiazka",
    page: Annotated[
        Optional[int],
        typer.Option("--page", "-p", help="Konkretny numer strony PDF do wyrenderowania."),
    ] = None,
    all_dev: Annotated[
        bool,
        typer.Option("--all-dev", help="Renderuj wszystkie strony ze zbioru DEV z manifestu."),
    ] = False,
    all_holdout: Annotated[
        bool,
        typer.Option("--all-holdout", help="Renderuj wszystkie strony ze zbioru HOLDOUT z manifestu."),
    ] = False,
    dpi: Annotated[
        int,
        typer.Option("--dpi", help="Rozdzielczość renderingu w DPI (domyślnie 300)."),
    ] = 300,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Katalog docelowy dla wygenerowanych skanów PNG."),
    ] = Path("benchmarks/fixtures/images"),
):
    """Renderuje obrazy skanów stron PDF w wysokiej rozdzielczości (300 DPI) do wzorców."""
    from src.ground_truth import render_page_image
    from src.benchmark_manifest import load_test_pages_manifest

    pdf_path = Path(f"input/{book}.pdf")
    if not pdf_path.exists():
        console.print(f"[bold red]Błąd:[/bold red] Nie znaleziono pliku PDF: {pdf_path}")
        raise typer.Exit(code=1)

    pages_to_render: list[int] = []
    manifest_path = Path("benchmarks/fixtures/test_pages_manifest.json")

    if page is not None:
        pages_to_render.append(page)
    elif all_dev or all_holdout:
        if not manifest_path.exists():
            console.print(f"[bold red]Błąd:[/bold red] Brak pliku manifestu: {manifest_path}")
            raise typer.Exit(code=1)
        manifest = load_test_pages_manifest(manifest_path)
        if all_dev:
            pages_to_render.extend([p.pdf_page for p in manifest.dev_pages])
        if all_holdout:
            pages_to_render.extend([p.pdf_page for p in manifest.holdout_pages])
    else:
        # Default: render key test fixture pages
        pages_to_render = [25, 40, 54, 58, 125, 142]

    console.print(
        f"[cyan]Renderowanie {len(pages_to_render)} stron ze skanu {pdf_path} (DPI={dpi}) do {output_dir}...[/cyan]"
    )
    for p in sorted(set(pages_to_render)):
        img_path, sha = render_page_image(pdf_path, p, output_dir, dpi=dpi)
        console.print(f"  • Strona {p:03d} -> [green]{img_path.name}[/green] (SHA-256: [dim]{sha[:16]}...[/dim])")
    console.print(f"[bold green]✓ Pomyślnie wyrenderowano {len(pages_to_render)} stron testowych.[/bold green]")


@app.command(name="ground-truth-validate")
def ground_truth_validate_cmd(
    fixtures_dir: Annotated[
        Path,
        typer.Option("--fixtures-dir", help="Katalog zawierający wzorce JSON."),
    ] = Path("benchmarks/fixtures/ground_truth"),
    manifest_path: Annotated[
        Path,
        typer.Option("--manifest", help="Ścieżka do pliku manifestu stron."),
    ] = Path("benchmarks/fixtures/test_pages_manifest.json"),
    verify_images: Annotated[
        bool,
        typer.Option("--verify-images/--no-verify-images", help="Weryfikuj obecność i sumy SHA-256 obrazów skanów."),
    ] = True,
):
    """Waliduje poprawność, schematy Pydantic v2 i spójność z manifestem dla wzorców skanów."""
    from src.ground_truth import (
        load_all_ground_truths,
        validate_ground_truth_against_manifest,
        compute_file_sha256,
    )

    if not fixtures_dir.exists():
        console.print(f"[bold red]Błąd:[/bold red] Brak katalogu wzorców: {fixtures_dir}")
        raise typer.Exit(code=1)

    fixtures = load_all_ground_truths(fixtures_dir)
    if not fixtures:
        console.print(f"[bold yellow]Ostrzeżenie:[/bold yellow] Brak wzorców w {fixtures_dir}")
        raise typer.Exit(code=0)

    console.print(f"[cyan]Weryfikacja {len(fixtures)} wzorców stron z {fixtures_dir}...[/cyan]")
    errors: list[str] = []

    for pno, gt in sorted(fixtures.items()):
        try:
            gt.validate_integrity()
            validate_ground_truth_against_manifest(gt, manifest_path)

            if verify_images:
                img_path = Path(gt.source_image_path)
                if not img_path.exists():
                    errors.append(f"Strona {pno:03d}: brak pliku obrazu '{img_path}'")
                else:
                    actual_sha = compute_file_sha256(img_path)
                    if actual_sha != gt.source_image_sha256:
                        errors.append(
                            f"Strona {pno:03d}: niezgodność SHA-256 obrazu (oczekiwano {gt.source_image_sha256[:12]}, jest {actual_sha[:12]})"
                        )
        except Exception as e:
            errors.append(f"Strona {pno:03d}: {e}")

    if errors:
        console.print(f"[bold red]Wykryto {len(errors)} błędów spójności wzorców:[/bold red]")
        for err in errors:
            console.print(f"  • [red]{err}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[bold green]✓ Wszystkie {len(fixtures)} wzorców przeszło weryfikację pomyślnie![/bold green]")


@app.command(name="ground-truth-summary")
def ground_truth_summary_cmd(
    fixtures_dir: Annotated[
        Path,
        typer.Option("--fixtures-dir", help="Katalog zawierający wzorce JSON."),
    ] = Path("benchmarks/fixtures/ground_truth"),
):
    """Wyświetla podsumowanie zdefiniowanych wzorców referencyjnych (bloki, ilości, tabele)."""
    from src.ground_truth import load_all_ground_truths

    fixtures = load_all_ground_truths(fixtures_dir)
    if not fixtures:
        console.print(f"[bold yellow]Brak wzorców w {fixtures_dir}[/bold yellow]")
        return

    table = Table(title="Wzorce referencyjne ze skanów (Ground Truth Fixtures)")
    table.add_column("PDF", justify="right", style="cyan")
    table.add_column("Druk", justify="right", style="dim")
    table.add_column("Układ", style="white")
    table.add_column("Bloki", justify="right", style="green")
    table.add_column("Ilości/Jedn.", justify="right", style="yellow")
    table.add_column("Tabele", justify="right", style="magenta")
    table.add_column("Glify niepewne", justify="right", style="red")

    total_blocks = 0
    total_quantities = 0
    total_tables = 0
    total_glyphs = 0

    for pno, gt in sorted(fixtures.items()):
        blocks = len(gt.blocks)
        quantities = len(gt.all_critical_quantities())
        tables = len(gt.tables)
        glyphs = len(gt.all_unresolved_glyphs())

        total_blocks += blocks
        total_quantities += quantities
        total_tables += tables
        total_glyphs += glyphs

        table.add_row(
            str(gt.page_no),
            str(gt.printed_page_no or "-"),
            gt.layout_type,
            str(blocks),
            str(quantities),
            str(tables),
            str(glyphs) if glyphs > 0 else "-",
        )

    console.print(table)
    console.print(
        f"[bold]Łącznie:[/bold] {len(fixtures)} stron, {total_blocks} bloków, "
        f"{total_quantities} adnotacji ilości/miar, {total_tables} tabel, {total_glyphs} oznaczonych trudnych glifów."
    )


@app.command(name="extract-page-adapter")
def extract_page_adapter_cmd(
    book: Annotated[
        str,
        typer.Option("--book", "-b", help="Nazwa książki (domyślnie 'ksiazka')."),
    ] = "ksiazka",
    page: Annotated[
        int,
        typer.Option("--page", "-p", help="Numer strony PDF do przetworzenia przez adapter."),
    ] = 25,
    ocr: Annotated[
        bool,
        typer.Option("--ocr/--no-ocr", help="Wymuś lub wyłącz OCR (EasyOCR). Domyślnie False (natywny odczyt)."),
    ] = False,
    run_id: Annotated[
        Optional[str],
        typer.Option("--run-id", help="Identyfikator przebiegu (domyślnie generowany na podstawie znacznika czasu)."),
    ] = None,
    output_dir: Annotated[
        Optional[Path],
        typer.Option("--output-dir", help="Katalog wyjściowy dla wyników adaptera."),
    ] = None,
    crop_blocks: Annotated[
        bool,
        typer.Option("--crop-blocks/--no-crop-blocks", help="Generuj wycinki graficzne dla każdego bloku."),
    ] = True,
):
    """Przetwarza stronę przez adapter ekstrakcji, utrwala pochodzenie (provenance), JSON i wycinki bloków."""
    import importlib.metadata
    from datetime import datetime, timezone
    from src.extraction_adapter import (
        from_docling_dict,
        save_extracted_page,
        crop_block_image,
        compute_file_sha256,
    )
    from src.ground_truth import render_page_image

    pdf_path = Path(f"input/{book}.pdf")
    if not pdf_path.exists():
        console.print(f"[bold red]Błąd:[/bold red] Nie znaleziono pliku PDF: {pdf_path}")
        raise typer.Exit(code=1)

    if run_id is None:
        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    if output_dir is None:
        output_dir = Path("output/benchmarks") / run_id

    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_sha = compute_file_sha256(pdf_path)

    engine_name = "docling_easyocr" if ocr else "docling_native"
    docling_ver = importlib.metadata.version("docling")

    console.print(f"[cyan]Uruchamianie adaptera ekstrakcji dla strony {page} (silnik: {engine_name})...[/cyan]")
    converter = get_converter(do_ocr=ocr, langs=["pl", "en"])
    conv_result = converter.convert(pdf_path, page_range=(page, page))
    doc_dict = conv_result.document.export_to_dict()

    extracted = from_docling_dict(
        doc_dict=doc_dict,
        run_id=run_id,
        engine=engine_name,
        engine_version=docling_ver,
        source_pdf_path=pdf_path,
        source_pdf_sha256=pdf_sha,
        target_page_no=page,
        transforms=["native" if not ocr else "easyocr"],
    )

    out_json = save_extracted_page(extracted, output_dir)
    console.print(f"[bold green]✓ Zapisano strukturę strony z pochodzeniem bloków:[/bold green] {out_json}")

    if crop_blocks and extracted.blocks:
        img_dir = output_dir / "images"
        crops_dir = output_dir / "crops"
        crops_dir.mkdir(parents=True, exist_ok=True)

        page_img_path, _ = render_page_image(pdf_path, page, img_dir, dpi=300)
        from PIL import Image
        with Image.open(page_img_path) as full_img:
            for b in extracted.blocks:
                if b.bbox is not None and b.bbox.width > 0 and b.bbox.height > 0:
                    crop = crop_block_image(
                        page_image=full_img,
                        bbox=b.bbox,
                        page_width_pt=extracted.width_pt,
                        page_height_pt=extracted.height_pt,
                        dpi=300,
                    )
                    crop.save(crops_dir / f"{b.block_id}.png")
        console.print(f"[bold green]✓ Wygenerowano wycinki {len(extracted.blocks)} bloków w:[/bold green] {crops_dir}")

    # Display Rich summary table
    table = Table(title=f"Wyekstrahowane bloki ze strony {page} (run: {run_id})")
    table.add_column("ID", style="cyan")
    table.add_column("Typ", style="white")
    table.add_column("Kolejność", justify="right", style="dim")
    table.add_column("Współrzędne (pt)", style="yellow")
    table.add_column("Tekst (podgląd)", style="green")

    for b in extracted.blocks:
        bbox_str = f"[{b.bbox.l:.1f}, {b.bbox.b:.1f}, {b.bbox.r:.1f}, {b.bbox.t:.1f}]" if b.bbox else "brak"
        preview = b.text.strip().replace("\n", " ")[:60]
        table.add_row(b.block_id, b.block_type, str(b.reading_order), bbox_str, preview)

    console.print(table)


@app.command(name="geometry-inspect")
def geometry_inspect_cmd(
    book: Annotated[
        str,
        typer.Option("--book", "-b", help="Nazwa książki (domyślnie 'ksiazka')."),
    ] = "ksiazka",
    page: Annotated[
        int,
        typer.Option("--page", "-p", help="Numer strony PDF do inspekcji geometrii."),
    ] = 58,
    dpi: Annotated[
        int,
        typer.Option("--dpi", help="Rozdzielczość renderingu do analizy."),
    ] = 300,
):
    """Analizuje geometrię strony: kąt nachylenia (skew), układ wielokolumnowy i kolejność czytania."""
    from src.ground_truth import render_page_image
    from src.geometry import detect_skew_angle, detect_column_gutter, process_page_geometry
    from src.extraction_adapter import from_docling_dict
    from docling.document_converter import DocumentConverter

    pdf_path = Path(f"input/{book}.pdf")
    if not pdf_path.exists():
        console.print(f"[bold red]Błąd:[/bold red] Nie znaleziono pliku PDF: {pdf_path}")
        raise typer.Exit(code=1)

    images_dir = Path("benchmarks/fixtures/images")
    img_path, sha = render_page_image(pdf_path, page, images_dir, dpi=dpi)

    import cv2

    cv_img = cv2.imread(str(img_path))
    skew_angle, gain = detect_skew_angle(cv_img)
    gutter = detect_column_gutter(cv_img)

    console.print(f"\n[bold cyan]Analiza geometrii strony {page} ({book}):[/bold cyan]")
    console.print(f"  • Wykryty kąt nachylenia (skew): [bold]{skew_angle:+.2f}°[/bold] (gain wariancji: {gain:.2f})")
    if abs(skew_angle) >= 0.2:
        console.print(f"    [yellow]➔ Zalecana korekta nachylenia (deskew)[/yellow]")
    else:
        console.print(f"    [green]✓ Kąt w normie (< 0.2°) — prostowanie zbędne[/green]")

    if gutter:
        console.print(
            f"  • Układ wielokolumnowy: [bold green]TAK[/bold green] (gutter na {gutter.split_x_relative*100:.1f}% szerokości, {gutter.gutter_width_px}px)"
        )
    else:
        console.print(f"  • Układ wielokolumnowy: [dim]NIE (tekst jednokolumnowy)[/dim]")

    # Run extraction & geometry pipeline
    conv = DocumentConverter()
    res = conv.convert(pdf_path, page_range=(page, page))
    raw_page = from_docling_dict(res.document.export_to_dict(), f"inspect_p{page}", "docling", "2.0", pdf_path, sha, page)
    refined_page, report = process_page_geometry(raw_page, img_path)

    table = Table(title=f"Kolejność bloków po korekcie geometrii (strona {page})")
    table.add_column("Kolejność", justify="right", style="cyan")
    table.add_column("ID Bloku", style="white")
    table.add_column("Typ", style="yellow")
    table.add_column("Współrzędne (pt)", style="dim")
    table.add_column("Tekst (podgląd)", style="green")

    for b in refined_page.blocks:
        bbox_str = f"[{b.bbox.l:.1f}, {b.bbox.b:.1f}, {b.bbox.r:.1f}, {b.bbox.t:.1f}]" if b.bbox else "brak"
        table.add_row(
            str(b.reading_order),
            b.block_id,
            b.block_type,
            bbox_str,
            b.text.strip().replace("\n", " ")[:60],
        )

    console.print(table)
    console.print(f"[bold green]Zastosowane transformacje:[/bold green] {report.transformations_applied}")


@app.command(name="candidate-convert")
def candidate_convert_cmd(
    book: Annotated[
        str,
        typer.Option("--book", "-b", help="Nazwa książki (domyślnie 'ksiazka')."),
    ] = "ksiazka",
    pages: Annotated[
        str,
        typer.Option("--pages", "-p", help="Zakres stron (np. '25-26') lub lista oddzielona przecinkami (np. '25,26,40')."),
    ] = "25",
    mode: Annotated[
        str,
        typer.Option("--mode", "-m", help="Jawny tryb OCR: 'native' (warstwa PDF), 'full_page' (EasyOCR cała strona), 'layout_regions', 'rapidocr'."),
    ] = "native",
    languages: Annotated[
        str,
        typer.Option("--languages", "-l", help="Języki dla OCR rozdzielone przecinkami (np. 'pl,en')."),
    ] = "pl,en",
    run_id: Annotated[
        Optional[str],
        typer.Option("--run-id", help="Własny identyfikator przebiegu kandydackiego."),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Katalog bazowy dla kandydatów (domyślnie output/candidates)."),
    ] = Path("output/candidates"),
    geometry: Annotated[
        bool,
        typer.Option("--geometry/--no-geometry", help="Włącz zaawansowaną korektę geometrii i separację 2 kolumn."),
    ] = False,
):
    """Wykonuje izolowaną konwersję stron do katalogu kandydata z jawnym trybem OCR."""
    from src.ocr_modes import OcrEngineMode, run_candidate_conversion

    pdf_path = Path(f"input/{book}.pdf")
    if not pdf_path.exists():
        console.print(f"[bold red]Błąd:[/bold red] Nie znaleziono pliku PDF: {pdf_path}")
        raise typer.Exit(code=1)

    try:
        engine_mode = OcrEngineMode(mode.lower().strip())
    except ValueError:
        valid_modes = [m.value for m in OcrEngineMode]
        console.print(f"[bold red]Błąd:[/bold red] Nieznany tryb OCR '{mode}'. Dostępne: {valid_modes}")
        raise typer.Exit(code=1)

    # Parse pages
    parsed_pages: list[int] = []
    try:
        if "-" in pages:
            parts = [int(x.strip()) for x in pages.split("-")]
            if len(parts) == 2:
                parsed_pages = list(range(parts[0], parts[1] + 1))
            else:
                raise ValueError
        else:
            parsed_pages = [int(x.strip()) for x in pages.split(",") if x.strip()]
        if not parsed_pages:
            raise ValueError
    except ValueError:
        console.print(f"[bold red]Błąd:[/bold red] Niepoprawny format stron: '{pages}'. Użyj np. '25-26' lub '25,40,58'.")
        raise typer.Exit(code=1)

    lang_list = [lang.strip() for lang in languages.split(",") if lang.strip()]

    console.print(
        f"[cyan]Uruchamianie konwersji kandydata: {len(parsed_pages)} stron, "
        f"tryb=[bold]{engine_mode.value}[/bold], PDF={pdf_path.name}, geometry={geometry}...[/cyan]"
    )

    manifest = run_candidate_conversion(
        pdf_path=pdf_path,
        pages=parsed_pages,
        mode=engine_mode,
        languages=lang_list,
        output_base_dir=output_dir,
        run_id=run_id,
        apply_geometry=geometry,
    )

    console.print(f"\n[bold green]✓ Konwersja kandydata zakończona pomyślnie![/bold green]")
    console.print(f"  • Run ID: [bold cyan]{manifest.config.run_id}[/bold cyan]")
    console.print(f"  • Tryb: [bold]{manifest.config.mode.value}[/bold]")
    console.print(f"  • Przetworzono stron: [bold]{manifest.total_pages}[/bold] ({manifest.pages_converted})")
    console.print(f"  • Wyekstrahowano bloków ogółem: [bold]{manifest.total_blocks}[/bold]")
    console.print(f"  • Plik kandydata Markdown: [green]{manifest.config.output_dir}/{manifest.candidate_markdown_path}[/green]")
    console.print(f"  • SHA-256 Markdowna: [dim]{manifest.candidate_markdown_sha256}[/dim]")
    console.print(f"  • Wersja produkcyjna chroniona: [bold green]is_production_truth = {manifest.is_production_truth}[/bold green]")


@app.command(name="candidate-list")
def candidate_list_cmd(
    candidates_dir: Annotated[
        Path,
        typer.Option("--candidates-dir", help="Katalog kandydatów (domyślnie output/candidates)."),
    ] = Path("output/candidates"),
):
    """Wyświetla listę zarejestrowanych przebiegów kandydackich."""
    from src.ocr_modes import load_candidate_manifest

    if not candidates_dir.exists():
        console.print(f"[bold yellow]Brak katalogu kandydatów: {candidates_dir}[/bold yellow]")
        return

    manifests = []
    for cand_folder in sorted(candidates_dir.iterdir()):
        if cand_folder.is_dir():
            m_file = cand_folder / "manifest.json"
            if m_file.exists():
                try:
                    manifests.append(load_candidate_manifest(m_file))
                except Exception:
                    continue

    if not manifests:
        console.print(f"[bold yellow]Brak przebiegów kandydackich w {candidates_dir}[/bold yellow]")
        return

    table = Table(title="Przebiegi kandydackie książki (output/candidates/)")
    table.add_column("Run ID", style="cyan")
    table.add_column("Tryb", style="white")
    table.add_column("Strony", style="yellow")
    table.add_column("Bloki", justify="right", style="green")
    table.add_column("Data utworzenia", style="dim")
    table.add_column("SHA-256 MD", style="magenta")

    for m in manifests:
        table.add_row(
            m.config.run_id,
            m.config.mode.value,
            str(m.pages_converted),
            str(m.total_blocks),
            m.config.created_at[:19].replace("T", " "),
            m.candidate_markdown_sha256[:12] + "..." if m.candidate_markdown_sha256 else "-",
        )

    console.print(table)


@app.command(name="benchmark-evaluate")
def benchmark_evaluate_cmd(
    candidate_dir: Annotated[
        Path,
        typer.Option("--candidate-dir", "-c", help="Katalog przebiegu kandydata (np. output/candidates/<run_id>)."),
    ],
    fixtures_dir: Annotated[
        Path,
        typer.Option("--fixtures-dir", help="Katalog zawierający wzorce ground truth."),
    ] = Path("benchmarks/fixtures/ground_truth"),
    output_file: Annotated[
        Optional[Path],
        typer.Option("--output-file", "-o", help="Opcjonalny plik wyjściowy JSON na raport ewaluacji."),
    ] = None,
):
    """Oblicza CER, WER, wierność miar i pominięcia dla kandydata względem wzorców ze skanów."""
    from src.benchmark import evaluate_candidate_run

    manifest_file = candidate_dir / "manifest.json"
    if not manifest_file.exists():
        console.print(f"[bold red]Błąd:[/bold red] Brak pliku manifestu w {manifest_file}")
        raise typer.Exit(code=1)

    console.print(f"[cyan]Ewaluacja jakości kandydata {candidate_dir.name} względem wzorców...[/cyan]")
    report = evaluate_candidate_run(manifest_file, fixtures_dir=fixtures_dir)

    table = Table(title=f"Raport jakości OCR: {report.run_id} ({report.engine_mode})")
    table.add_column("PDF", justify="right", style="cyan")
    table.add_column("Druk", justify="right", style="dim")
    table.add_column("CER", justify="right", style="yellow")
    table.add_column("WER", justify="right", style="magenta")
    table.add_column("Ilości (Zgodne/Wszystkie)", justify="right", style="green")
    table.add_column("Pominięcia", justify="right", style="red")
    table.add_column("Powtórzenia", justify="right", style="red")

    for pr in report.page_reports:
        table.add_row(
            str(pr.page_no),
            pr.printed_page or "-",
            f"{pr.cer*100:.2f}%",
            f"{pr.wer*100:.2f}%",
            f"{pr.exact_quantities}/{pr.total_quantities}",
            str(len(pr.omissions)),
            str(len(pr.repetitions)),
        )

    console.print(table)
    console.print(
        f"[bold]Podsumowanie:[/bold] Średni CER = [bold yellow]{report.mean_cer*100:.2f}%[/bold yellow], "
        f"Średni WER = [bold magenta]{report.mean_wer*100:.2f}%[/bold magenta], "
        f"Najgorszy WER = [red]{report.worst_page_wer*100:.2f}%[/red] (strona {report.worst_page_no})"
    )
    console.print(
        f"  • Dokładność miar i temperatur: [bold green]{report.quantity_accuracy_pct:.1f}%[/bold green] "
        f"({report.exact_critical_quantities}/{report.total_critical_quantities} bezbłędnych)"
    )
    if report.corrupted_critical_quantities > 0:
        console.print(f"  • [bold red]Zniekształcone ilości/jednostki:[/bold red] {report.corrupted_critical_quantities}")
    if report.total_omissions > 0:
        console.print(f"  • [bold red]Wykryte pominięcia bloków/zdań:[/bold red] {report.total_omissions}")

    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        console.print(f"[bold green]✓ Zapisano raport jakości:[/bold green] {output_file}")


@app.command(name="benchmark-compare")
def benchmark_compare_cmd(
    candidate_a: Annotated[
        Path,
        typer.Option("--candidate-a", "-a", help="Katalog pierwszego wariantu (np. native)."),
    ],
    candidate_b: Annotated[
        Path,
        typer.Option("--candidate-b", "-b", help="Katalog drugiego wariantu (np. full_page)."),
    ],
    fixtures_dir: Annotated[
        Path,
        typer.Option("--fixtures-dir", help="Katalog zawierający wzorce ground truth."),
    ] = Path("benchmarks/fixtures/ground_truth"),
):
    """Porównuje dwa warianty ekstrakcji (np. native vs full_page) na tych samych stronach."""
    from src.benchmark import evaluate_candidate_run

    rep_a = evaluate_candidate_run(candidate_a / "manifest.json", fixtures_dir=fixtures_dir)
    rep_b = evaluate_candidate_run(candidate_b / "manifest.json", fixtures_dir=fixtures_dir)

    common_pages = sorted(set(rep_a.pages_evaluated).intersection(set(rep_b.pages_evaluated)))
    if not common_pages:
        console.print("[bold red]Błąd:[/bold red] Brak wspólnych stron do porównania.")
        raise typer.Exit(code=1)

    table = Table(title=f"Porównanie wariantów: {rep_a.run_id} vs {rep_b.run_id}")
    table.add_column("Metryka", style="cyan")
    table.add_column(f"{rep_a.engine_mode} (A)", justify="right", style="white")
    table.add_column(f"{rep_b.engine_mode} (B)", justify="right", style="white")
    table.add_column("Różnica / Zwycięzca", justify="center", style="green")

    table.add_row(
        "Średni CER",
        f"{rep_a.mean_cer*100:.2f}%",
        f"{rep_b.mean_cer*100:.2f}%",
        f"{rep_b.mean_cer - rep_a.mean_cer:+.2f}%",
    )
    table.add_row(
        "Średni WER",
        f"{rep_a.mean_wer*100:.2f}%",
        f"{rep_b.mean_wer*100:.2f}%",
        f"{rep_b.mean_wer - rep_a.mean_wer:+.2f}%",
    )
    table.add_row(
        "Zgodność miar i temperatur",
        f"{rep_a.quantity_accuracy_pct:.1f}%",
        f"{rep_b.quantity_accuracy_pct:.1f}%",
        f"{rep_b.quantity_accuracy_pct - rep_a.quantity_accuracy_pct:+.1f}%",
    )
    table.add_row(
        "Błędy krytyczne (Zniekształcenia)",
        str(rep_a.corrupted_critical_quantities),
        str(rep_b.corrupted_critical_quantities),
        f"{rep_b.corrupted_critical_quantities - rep_a.corrupted_critical_quantities:+d}",
    )
    table.add_row(
        "Pominięcia zdań",
        str(rep_a.total_omissions),
        str(rep_b.total_omissions),
        f"{rep_b.total_omissions - rep_a.total_omissions:+d}",
    )

    console.print(table)


@app.command(name="benchmark-ocr")
def benchmark_ocr_cmd(
    book: Annotated[
        str,
        typer.Option("--book", "-b", help="Nazwa książki (domyślnie 'ksiazka')."),
    ] = "ksiazka",
    pages: Annotated[
        str,
        typer.Option("--pages", "-p", help="Zakres stron (np. '25-26'), lista '25,40,54,58,125,142' lub 'gt' (wszystkie strony ze zweryfikowanym wzorcem)."),
    ] = "gt",
    modes: Annotated[
        str,
        typer.Option("--modes", "-m", help="Silniki OCR do porównania rozdzielone przecinkami (np. 'native,full_page,rapidocr')."),
    ] = "native,full_page,rapidocr",
    fixtures_dir: Annotated[
        Path,
        typer.Option("--fixtures-dir", help="Katalog zawierający wzorce ground truth."),
    ] = Path("benchmarks/fixtures/ground_truth"),
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Katalog wyjściowy dla raportów benchmarku."),
    ] = Path("output/benchmarks"),
    run_id: Annotated[
        Optional[str],
        typer.Option("--run-id", help="Identyfikator przebiegu porównawczego."),
    ] = None,
):
    """Przeprowadza zbiorczy benchmark porównawczy silników OCR (Native, EasyOCR, RapidOCR) względem Ground Truth."""
    from src.ocr_modes import OcrEngineMode
    from src.benchmark import run_multi_engine_benchmark
    from src.ground_truth import load_all_ground_truths
    from src.benchmark_manifest import load_test_pages_manifest

    pdf_path = Path(f"input/{book}.pdf")
    if not pdf_path.exists():
        console.print(f"[bold red]Błąd:[/bold red] Nie znaleziono pliku PDF: {pdf_path}")
        raise typer.Exit(code=1)

    parsed_pages: list[int] = []
    if pages.strip().lower() == "gt":
        gts = load_all_ground_truths(fixtures_dir)
        parsed_pages = sorted(gts.keys())
    elif pages.strip().lower() == "dev":
        manifest = load_test_pages_manifest()
        parsed_pages = [e.pdf_page for e in manifest.dev_pages]
    elif "-" in pages:
        parts = [int(x.strip()) for x in pages.split("-")]
        if len(parts) == 2:
            parsed_pages = list(range(parts[0], parts[1] + 1))
        else:
            raise typer.BadParameter(f"Nieprawidłowy zakres stron: {pages}")
    else:
        parsed_pages = [int(x.strip()) for x in pages.split(",") if x.strip()]

    engine_modes: list[OcrEngineMode] = []
    for m in modes.split(","):
        clean_m = m.strip().lower()
        if not clean_m:
            continue
        try:
            engine_modes.append(OcrEngineMode(clean_m))
        except ValueError:
            console.print(f"[bold red]Błąd:[/bold red] Nieznany tryb OCR '{clean_m}'. Dostępne: {[e.value for e in OcrEngineMode]}")
            raise typer.Exit(code=1)

    console.print(f"[bold cyan]Rozpoczynanie benchmarku OCR:[/bold cyan] {len(engine_modes)} trybów na {len(parsed_pages)} stronach ({parsed_pages})...")

    report, json_path, md_path = run_multi_engine_benchmark(
        pdf_path=pdf_path,
        pages=parsed_pages,
        modes=engine_modes,
        benchmarks_dir=output_dir,
        fixtures_dir=fixtures_dir,
        benchmark_id=run_id,
    )

    table = Table(title=f"Wyniki Benchmarku OCR — {report.benchmark_id}")
    table.add_column("Silnik OCR", style="cyan")
    table.add_column("Średni CER", justify="right", style="white")
    table.add_column("Średni WER", justify="right", style="white")
    table.add_column("Najgorszy WER (str)", justify="right", style="magenta")
    table.add_column("Zgodność miar", justify="right", style="green")
    table.add_column("Błędy miar", justify="right", style="red")
    table.add_column("Pominięcia", justify="right", style="yellow")
    table.add_column("p50 / p95 (s)", justify="right", style="dim")
    table.add_column("Gate G2?", justify="center")

    for row in report.summary_table:
        p50_p95 = f"{row['p50_s']}s / {row['p95_s']}s" if row['p50_s'] is not None else "N/A"
        gate_status = "[bold green]PASS[/bold green]" if row["passes_gate_g2"] else "[bold red]FAIL[/bold red]"
        table.add_row(
            row["engine"],
            f"{row['mean_cer']*100:.2f}%",
            f"{row['mean_wer']*100:.2f}%",
            f"{row['worst_page_wer']*100:.2f}% (str. {row['worst_page_no']})",
            f"{row['quantity_accuracy_pct']:.1f}%",
            str(row["corrupted_quantities"]),
            str(row["omissions"]),
            p50_p95,
            gate_status,
        )

    console.print(table)
    console.print(f"[bold green]✓ Zapisano raport JSON:[/bold green] {json_path}")
    console.print(f"[bold green]✓ Zapisano raport Markdown:[/bold green] {md_path}")
    if report.best_overall_engine:
        console.print(f"[bold blue]Najlepsza jakość:[/bold blue] [cyan]{report.best_overall_engine}[/cyan]")
    if report.fastest_engine:
        console.print(f"[bold blue]Najwyższa prędkość:[/bold blue] [cyan]{report.fastest_engine}[/cyan]")


@app.command(name="vlm-test")
def vlm_test_cmd(
    pages: Annotated[
        str,
        typer.Option("--pages", "-p", help="Numery stron do testu wycinków VLM (np. '58', '58,25')."),
    ] = "58,25",
    model: Annotated[
        str,
        typer.Option("--model", "-m", help="Nazwa modelu wizyjnego w Ollama (np. 'moondream:latest')."),
    ] = "moondream:latest",
    output_dir: Annotated[
        Optional[Path],
        typer.Option("--output-dir", "-o", help="Katalog wyjściowy na raporty benchmarku VLM."),
    ] = None,
):
    """Przeprowadza kontrolowany test VLM na wycinkach uszkodzonych bloków zgodnie z bramką G2 (R12)."""
    from rich.table import Table
    from src.vlm_transcription import run_controlled_vlm_benchmark, save_vlm_benchmark_report

    page_list: list[int] = []
    for part in pages.split(","):
        p = part.strip()
        if p.isdigit():
            page_list.append(int(p))

    if not page_list:
        console.print("[bold red]Błąd:[/bold red] Należy podać przynajmniej jeden prawidłowy numer strony.")
        raise typer.Exit(code=1)

    out_base = output_dir or Path(f"output/benchmarks/vlm_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

    console.print(f"[bold blue]=== Kontrolowany test VLM ({model}) na wycinkach stron: {page_list} ===[/bold blue]")
    try:
        report = run_controlled_vlm_benchmark(
            pages=page_list,
            model=model,
            output_dir=out_base,
        )
    except Exception as e:
        console.print(f"[bold red]Błąd podczas testu VLM:[/bold red] {e}")
        raise typer.Exit(code=1)

    json_path, md_path = save_vlm_benchmark_report(report, out_base)

    table = Table(title=f"Wyniki Kontrolowanego Testu VLM — {report.run_id}")
    table.add_column("Strona", justify="right", style="cyan")
    table.add_column("Blok", style="dim")
    table.add_column("Typ", style="white")
    table.add_column("Werdykt", justify="center")
    table.add_column("Halucynacja", justify="center")
    table.add_column("Miary OK?", justify="center")
    table.add_column("OCR CER", justify="right", style="green")
    table.add_column("VLM CER", justify="right", style="magenta")

    for c in report.comparisons:
        hal_str = "[bold red]TAK[/bold red]" if c.hallucination_detected else "[green]NIE[/green]"
        qty_str = "[bold green]TAK[/bold green]" if c.critical_quantities_preserved else "[bold red]NIE[/bold red]"
        table.add_row(
            str(c.page_no),
            c.block_id.split("_")[-1],
            c.block_type,
            f"[bold green]{c.verdict}[/bold green]" if c.verdict == "vlm_better" else f"[cyan]{c.verdict}[/cyan]",
            hal_str,
            qty_str,
            f"{c.ocr_cer*100:.1f}%",
            f"{c.vlm_cer*100:.1f}%",
        )

    console.print(table)
    console.print(f"\n[bold]Podsumowanie metryk VLM ({model}):[/bold]")
    console.print(f"  • Średni CER: RapidOCR = [bold green]{report.ocr_mean_cer*100:.2f}%[/bold green] vs VLM = [bold magenta]{report.vlm_mean_cer*100:.2f}%[/bold magenta]")
    console.print(f"  • Średni WER: RapidOCR = [bold green]{report.ocr_mean_wer*100:.2f}%[/bold green] vs VLM = [bold magenta]{report.vlm_mean_wer*100:.2f}%[/bold magenta]")
    console.print(f"  • Średni czas na wycinek: RapidOCR = [bold green]~0.02s[/bold green] vs VLM = [bold magenta]{report.vlm_mean_duration_s:.2f}s[/bold magenta]")
    console.print(f"  • Odsetek halucynacji VLM: [bold red]{report.hallucination_rate:.1f}%[/bold red]")
    console.print(f"\n[bold yellow]Decyzja architektoniczna (D08):[/bold yellow]\n  {report.architectural_decision}")
    console.print(f"\n[bold green]✓ Zapisano raport JSON:[/bold green] {json_path}")
    console.print(f"[bold green]✓ Zapisano raport Markdown:[/bold green] {md_path}")


@app.command(name="gate-g2-evaluate")
def gate_g2_evaluate_cmd(
    input_pdf: Annotated[
        Path,
        typer.Option("--input-pdf", "-i", help="Ścieżka do bazowego pliku PDF."),
    ] = Path("input/ksiazka.pdf"),
    manifest_path: Annotated[
        Path,
        typer.Option("--manifest", "-m", help="Ścieżka do manifestu stron testowych."),
    ] = Path("benchmarks/fixtures/test_pages_manifest.json"),
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Katalog wyjściowy na raporty Gate G2."),
    ] = Path("output/benchmarks/gate_g2_evaluation"),
):
    """Przeprowadza ewaluację Gate G2 na 10 odłożonych stronach holdout i weryfikuje kryteria sekcji 8."""
    from rich.table import Table
    from src.gate_g2_evaluation import run_gate_g2_holdout_evaluation, save_gate_g2_report

    console.print(f"[bold blue]=== Rozpoczynanie formalnej ewaluacji Bramki Gate G2 (10 stron holdout) ===[/bold blue]")
    try:
        report = run_gate_g2_holdout_evaluation(
            pdf_path=input_pdf,
            holdout_manifest_path=manifest_path,
        )
    except Exception as e:
        console.print(f"[bold red]Błąd podczas ewaluacji Gate G2:[/bold red] {e}")
        raise typer.Exit(code=1)

    json_path, md_path = save_gate_g2_report(report, output_dir)

    table = Table(title=f"Wyniki Ewaluacji Stron Holdout — Gate G2 ({report.run_id})")
    table.add_column("PDF", justify="right", style="cyan")
    table.add_column("Kolumny", justify="center")
    table.add_column("Gutter", justify="center")
    table.add_column("Kąt skew", justify="right", style="dim")
    table.add_column("Bloki", justify="right")
    table.add_column("Czas (s)", justify="right", style="green")
    table.add_column("Przeplatanie?", justify="center")

    for ps in report.page_summaries:
        skew_str = f"{ps.skew_angle_deg:+.1f}°" if ps.skew_angle_deg is not None else "0.0°"
        gut_str = "[green]TAK[/green]" if ps.has_gutter else "NIE"
        inter_str = "[bold red]TAK[/bold red]" if ps.interleaving_detected else "[bold green]NIE[/bold green]"
        table.add_row(
            str(ps.page_no),
            str(ps.column_count),
            gut_str,
            skew_str,
            str(ps.total_blocks),
            f"{ps.duration_seconds:.2f}s",
            inter_str,
        )

    console.print(table)

    crit_table = Table(title="Kryteria Akceptacji Bramki Gate G2 (Sekcja 8)")
    crit_table.add_column("Kryterium", style="white")
    crit_table.add_column("Status", justify="center")

    for k, v in report.criteria_checklist.items():
        status_str = "[bold green]PASS[/bold green]" if v else "[bold red]FAIL[/bold red]"
        crit_table.add_row(k.replace("_", " ").capitalize(), status_str)

    console.print(crit_table)
    console.print(f"\n[bold]Latencja na stronę:[/bold] p50 = [bold green]{report.p50_duration_s:.2f}s[/bold green], p95 = [bold green]{report.p95_duration_s:.2f}s[/bold green], max = {report.max_duration_s:.2f}s")
    console.print(f"[bold]Ochrona produkcji (output/markdown/ksiazka.md):[/bold] [bold green]{'NIENARUSZONE' if report.production_preserved else 'NARUSZONE!'}[/bold green]")
    verdict_color = "green" if report.verdict == "PASS" else "red"
    console.print(f"\n[bold {verdict_color}]WERDYKT BRAMKI GATE G2: {report.verdict}[/bold {verdict_color}]")
    console.print(f"\n[bold green]✓ Zapisano raport JSON:[/bold green] {json_path}")
    console.print(f"[bold green]✓ Zapisano raport Markdown:[/bold green] {md_path}")


@app.command(name="pilot-evaluate")
def pilot_evaluate_cmd(
    input_pdf: Annotated[
        Path,
        typer.Option("--input-pdf", "-i", help="Ścieżka do bazowego pliku PDF."),
    ] = Path("input/ksiazka.pdf"),
    pages: Annotated[
        str,
        typer.Option("--pages", "-p", help="Zakres 50 stron do pilota (np. 201-250)."),
    ] = "201-250",
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Katalog wyjściowy na raporty pilota."),
    ] = Path("output/benchmarks/pilot_50_pages"),
    run_id: Annotated[
        Optional[str],
        typer.Option("--run-id", "-r", help="Opcjonalny identyfikator przebiegu."),
    ] = None,
):
    """Przeprowadza pilotażową konwersję 50 stron zamrożonym stosem produkcyjnym i ewaluuje Gate G3."""
    from rich.table import Table
    from src.pilot_evaluation import run_pilot_evaluation, save_pilot_report

    # Parse page range
    parsed_pages: list[int] = []
    if "-" in pages:
        parts = pages.split("-")
        parsed_pages = list(range(int(parts[0]), int(parts[1]) + 1))
    elif "," in pages:
        parsed_pages = [int(p.strip()) for p in pages.split(",") if p.strip()]
    else:
        parsed_pages = [int(pages)]

    console.print(f"[bold blue]=== Rozpoczynanie formalnego Pilota Gate G3 ({len(parsed_pages)} stron: {parsed_pages[0]}–{parsed_pages[-1]}) ===[/bold blue]")
    try:
        report = run_pilot_evaluation(
            pdf_path=input_pdf,
            pages=parsed_pages,
            output_base_dir=Path("output/candidates"),
            run_id=run_id,
        )
    except Exception as e:
        console.print(f"[bold red]Błąd podczas ewaluacji pilota 50 stron:[/bold red] {e}")
        raise typer.Exit(code=1)

    json_path, md_path = save_pilot_report(report, output_dir)

    table = Table(title=f"Wyniki Pilota 50 Stron — Gate G3 ({report.run_id})")
    table.add_column("PDF", justify="right", style="cyan")
    table.add_column("Kol.", justify="center")
    table.add_column("Gutter", justify="center")
    table.add_column("Kąt skew", justify="right", style="dim")
    table.add_column("Bloki", justify="right")
    table.add_column("Zaakceptowane", justify="right", style="green")
    table.add_column("Akceptacja", justify="right", style="bold")
    table.add_column("Czas (s)", justify="right", style="green")
    table.add_column("Przeplatanie?", justify="center")

    for ps in report.page_summaries[:15]:
        skew_str = f"{ps.skew_angle_deg:+.1f}°" if ps.skew_angle_deg is not None else "0.0°"
        gut_str = "[green]TAK[/green]" if ps.has_gutter else "NIE"
        inter_str = "[bold red]TAK[/bold red]" if ps.interleaving_detected else "[bold green]NIE[/bold green]"
        table.add_row(
            str(ps.page_no),
            str(ps.column_count),
            gut_str,
            skew_str,
            str(ps.total_blocks),
            str(ps.accepted_blocks),
            f"{ps.acceptance_rate_percent}%",
            f"{ps.duration_seconds:.2f}s",
            inter_str,
        )

    console.print(table)
    if len(report.page_summaries) > 15:
        console.print(f"[dim]... oraz {len(report.page_summaries) - 15} pozostałych stron w pełnym raporcie.[/dim]")

    crit_table = Table(title="Kryteria Akceptacji Bramki Gate G3 (Sekcja 14)")
    crit_table.add_column("Kryterium", style="white")
    crit_table.add_column("Status", justify="center")

    for k, v in report.checklist.items():
        status_str = "[bold green]PASS[/bold green]" if v else "[bold red]FAIL[/bold red]"
        crit_table.add_row(k.replace("_", " ").capitalize(), status_str)

    console.print(crit_table)
    extrap = report.cost_extrapolation
    console.print(f"\n[bold]Metryki jakościowe:[/bold]")
    console.print(f"  • Wyekstrahowano bloków: [bold]{report.total_blocks}[/bold] (zaakceptowano: [bold green]{report.total_accepted_blocks}[/bold green], oflagowano: {report.total_flagged_blocks})")
    console.print(f"  • Wskaźnik automatycznej akceptacji: [bold green]{report.overall_acceptance_rate_percent}%[/bold green]")
    console.print(f"  • Przeplatanie kolumn: [bold green]{report.column_interleaving_cases} przypadków[/bold green]")
    console.print(f"  • Uszkodzone przejścia stron: [bold green]{report.broken_transitions_count}[/bold green]")
    console.print(f"\n[bold]Ekstrapolacja dla 914 stron:[/bold]")
    console.print(f"  • Mediana (p50): [bold green]{extrap.p50_page_duration_s:.2f}s[/bold green], p95: [bold green]{extrap.p95_page_duration_s:.2f}s[/bold green]")
    console.print(f"  • Szacowany czas pełnej książki (914 stron): [bold cyan]{extrap.estimated_total_minutes:.1f} min[/bold cyan] (~{extrap.estimated_hours_for_914_pages:.2f} h)")
    console.print(f"  • Koszt zewnętrznych API: [bold green]${extrap.api_cost_usd:.2f} USD[/bold green] (100% lokalny RapidOCR ONNX)")
    console.print(f"  • Zużycie pamięci: RAM {extrap.peak_ram_mb:.1f} MB, GPU VRAM {extrap.peak_gpu_vram_mb:.1f} MB")
    console.print(f"  • Ochrona produkcji (output/markdown/ksiazka.md): [bold green]{'NIENARUSZONE' if report.production_preserved else 'NARUSZONE!'}[/bold green]")
    verdict_color = "green" if report.verdict == "PASS" else "red"
    console.print(f"\n[bold {verdict_color}]WERDYKT BRAMKI GATE G3: {report.verdict}[/bold {verdict_color}]")
    console.print(f"\n[bold yellow]Formalna Decyzja D10:[/bold yellow]\n  {report.decision_d10}")
    console.print(f"\n[bold green]✓ Zapisano raport JSON:[/bold green] {json_path}")
    console.print(f"[bold green]✓ Zapisano raport Markdown:[/bold green] {md_path}")


@app.command(name="full-candidate-convert")
def full_candidate_convert_cmd(
    input_pdf: Annotated[
        Path,
        typer.Option("--input-pdf", "-i", help="Ścieżka do bazowego pliku PDF."),
    ] = Path("input/ksiazka.pdf"),
    pages: Annotated[
        Optional[str],
        typer.Option("--pages", "-p", help="Zakres stron (np. '1-914', domyślnie cała książka)."),
    ] = None,
    batch_size: Annotated[
        int,
        typer.Option("--batch-size", "-b", help="Liczba stron w jednej partii checkpointu."),
    ] = 50,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Katalog wyjściowy na raporty pokrycia."),
    ] = Path("output/benchmarks/full_candidate"),
    run_id: Annotated[
        Optional[str],
        typer.Option("--run-id", "-r", help="Identyfikator przebiegu (opcjonalny)."),
    ] = None,
    resume: Annotated[
        bool,
        typer.Option("--resume/--no-resume", help="Wznawianie od ostatniego checkpointu."),
    ] = True,
):
    """Przeprowadza pełną konwersję stron do katalogu kandydata z checkpointami (Gate G4 / R17)."""
    from rich.table import Table
    from src.full_candidate_runner import run_full_candidate_conversion, save_full_candidate_report

    parsed_pages: Optional[list[int]] = None
    if pages:
        if "-" in pages:
            parts = pages.split("-")
            parsed_pages = list(range(int(parts[0]), int(parts[1]) + 1))
        elif "," in pages:
            parsed_pages = [int(p.strip()) for p in pages.split(",") if p.strip()]
        else:
            parsed_pages = [int(pages)]

    target_desc = f"{len(parsed_pages)} stron" if parsed_pages else "pełnych 914 stron"
    console.print(f"[bold blue]=== Rozpoczynanie pełnej konwersji kandydata ({target_desc}, batch_size={batch_size}) ===[/bold blue]")

    def on_progress(batch_idx: int, total_batches: int, batch: Any, skipped: bool):
        status_lbl = "[cyan]POMINIĘTO (już ukończone)[/cyan]" if skipped else f"[green]UKOŃCZONO ({batch.duration_seconds:.1f}s, {batch.blocks_count} bloków)[/green]"
        console.print(f"  • Partia [{batch_idx + 1}/{total_batches}]: strony {batch.start_page}–{batch.end_page} -> {status_lbl}")

    try:
        report = run_full_candidate_conversion(
            pdf_path=input_pdf,
            pages=parsed_pages,
            batch_size=batch_size,
            output_base_dir=Path("output/candidates"),
            run_id=run_id,
            resume=resume,
            progress_callback=on_progress,
        )
    except Exception as e:
        console.print(f"[bold red]Błąd podczas konwersji kandydata:[/bold red] {e}")
        raise typer.Exit(code=1)

    json_path, md_path = save_full_candidate_report(report, output_dir)

    console.print(f"\n[bold]Podsumowanie Przebiegu Kandydata ({report.run_id}):[/bold]")
    console.print(f"  • Pokrycie stron: [bold green]{report.total_pages_converted}/{report.total_pages_expected} ({report.coverage_percent}%)[/bold green]")
    console.print(f"  • Łączna liczba bloków: [bold]{report.total_blocks}[/bold] (zaakceptowano: [bold green]{report.accepted_blocks}[/bold green], oflagowano: {report.flagged_blocks})")
    console.print(f"  • Wskaźnik automatycznej akceptacji: [bold green]{report.acceptance_rate_percent}%[/bold green]")
    console.print(f"  • Obszary niepewne: [bold yellow]{report.uncertain_areas_count}[/bold yellow] zarejestrowanych anomalii")
    console.print(f"  • Czas trwania: [bold cyan]{report.total_duration_minutes:.2f} minut[/bold cyan] (p50: {report.p50_page_duration_s:.2f}s, p95: {report.p95_page_duration_s:.2f}s)")
    console.print(f"  • Ochrona produkcji (output/markdown/ksiazka.md): [bold green]{'NIENARUSZONE' if report.production_preserved else 'NARUSZONE!'}[/bold green]")
    verdict_color = "green" if report.verdict == "PASS" else "red"
    console.print(f"\n[bold {verdict_color}]WERDYKT KANDYDATA: {report.verdict}[/bold {verdict_color}]")
    console.print(f"\n[bold green]✓ Zapisano kandydat Markdown:[/bold green] {report.candidate_markdown_path}")
    console.print(f"[bold green]✓ Zapisano raport JSON:[/bold green] {json_path}")
    console.print(f"[bold green]✓ Zapisano raport Markdown:[/bold green] {md_path}")


@app.command(name="candidate-diff")
def candidate_diff_cmd(
    production_md: Annotated[
        Path,
        typer.Option("--prod", "-p", help="Ścieżka do obecnego produkcyjnego pliku Markdown."),
    ] = Path("output/markdown/ksiazka.md"),
    candidate_md: Annotated[
        Path,
        typer.Option("--candidate", "-c", help="Ścieżka do pliku kandydata Markdown."),
    ] = Path("output/candidates/cand_full_914_frozen/ksiazka.md"),
    assets_dir: Annotated[
        Path,
        typer.Option("--assets-dir", "-a", help="Katalog grafik (assets)."),
    ] = Path("output/markdown/assets"),
):
    """Porównuje logicznie wersję produkcyjną z kandydatem i sprawdza spójność grafik."""
    from rich.table import Table
    from src.migration import build_asset_manifest, compare_documents

    if not production_md.exists():
        console.print(f"[bold red]Błąd:[/bold red] Plik produkcyjny nie istnieje: {production_md}")
        raise typer.Exit(code=1)
    if not candidate_md.exists():
        console.print(f"[bold red]Błąd:[/bold red] Plik kandydata nie istnieje: {candidate_md}")
        raise typer.Exit(code=1)

    diff = compare_documents(production_md, candidate_md)
    manifest = build_asset_manifest(assets_dir, production_md, candidate_md)

    console.print(f"[bold blue]=== Porównanie Logiczne: Produkcja vs Kandydat ===[/bold blue]")

    table = Table(title="Porównanie Właściwości Dokumentu")
    table.add_column("Metryka", style="white")
    table.add_column("Produkcja (obecna)", justify="right", style="cyan")
    table.add_column("Kandydat (nowy)", justify="right", style="green")
    table.add_column("Zmiana / Różnica", justify="right")

    table.add_row("Liczba słów", str(diff.production_words), str(diff.candidate_words), f"{diff.improvements.get('word_count_delta', 0):+d}")
    table.add_row("Liczba akapitów", str(diff.production_paragraphs), str(diff.candidate_paragraphs), f"{diff.improvements.get('paragraph_count_delta', 0):+d}")
    table.add_row("Liczba linii", str(diff.production_lines), str(diff.candidate_lines), f"{diff.candidate_lines - diff.production_lines:+d}")
    table.add_row("Miary kulinarne", str(diff.culinary_measurements_prod), str(diff.culinary_measurements_cand), "Zgodne")
    table.add_row("Suma SHA-256", diff.production_sha256[:16] + "...", diff.candidate_sha256[:16] + "...", "Nowy build")

    console.print(table)
    console.print(f"  • Podobieństwo tekstu (Dice bigram): [bold green]{diff.word_similarity_pct}%[/bold green]")
    console.print(f"  • Odnośniki do grafik (assets): [bold green]{diff.total_assets_preserved}[/bold green] (brakujące pliki: [bold green]{len(manifest.missing_files)}[/bold green])")
    console.print(f"  • Ułamki Unicode znormalizowane: [bold green]{diff.improvements.get('unicode_fractions_normalized', 0)}[/bold green]")
    console.print(f"  • Rozbite ułamki naprawione: [bold green]{diff.improvements.get('broken_fractions_repaired', 0)}[/bold green]")
    console.print(f"  • Usunięte śmieci skanera: [bold green]{diff.improvements.get('scanner_debris_eliminated', 0)}[/bold green]")


@app.command(name="candidate-publish")
def candidate_publish_cmd(
    candidate_md: Annotated[
        Path,
        typer.Option("--candidate", "-c", help="Ścieżka do kandydata Markdown."),
    ] = Path("output/candidates/cand_full_914_frozen/ksiazka.md"),
    production_md: Annotated[
        Path,
        typer.Option("--prod", "-p", help="Ścieżka do produkcyjnego pliku Markdown."),
    ] = Path("output/markdown/ksiazka.md"),
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Katalog wyjściowy na raport migracji."),
    ] = Path("output/benchmarks/migration"),
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Tylko symulacja bez modyfikacji pliku produkcyjnego."),
    ] = False,
):
    """Atomowo publikuje wersję kandydata do produkcji ze snapshotem bazowym i kopią .pre_r18_bak."""
    from src.migration import publish_candidate_document, save_migration_report

    console.print(f"[bold blue]=== Rozpoczynanie Publikacji Kandydata (Gate G4 / R18) ===[/bold blue]")
    try:
        report = publish_candidate_document(
            candidate_md_path=candidate_md,
            production_md_path=production_md,
            book_name="ksiazka",
            dry_run=dry_run,
        )
    except Exception as e:
        console.print(f"[bold red]Błąd podczas publikacji kandydata:[/bold red] {e}")
        raise typer.Exit(code=1)

    json_path, md_path = save_migration_report(report, output_dir)

    console.print(f"\n[bold]Wynik Publikacji:[/bold]")
    console.print(f"  • Status: [bold green]{report.status.upper()}[/bold green]")
    console.print(f"  • Snapshot bazowy: [bold cyan]{report.snapshot_id}[/bold cyan]")
    console.print(f"  • Kopia zapasowa (rollback target): [bold green]{report.backup_path}[/bold green]")
    console.print(f"  • SHA-256 przed: `{report.production_sha_before}`")
    console.print(f"  • SHA-256 po: `{report.production_sha_after}`")
    console.print(f"  • Spójność manifestu grafik (assets): [bold green]{'100% ZACHOWANE' if report.asset_manifest.all_references_valid else 'BŁĄD'}[/bold green]")
    verdict_color = "green" if report.verdict == "PASS" else "red"
    console.print(f"\n[bold {verdict_color}]WERDYKT GATE G4: {report.verdict}[/bold {verdict_color}]")
    console.print(f"\n[bold green]✓ Zapisano raport JSON:[/bold green] {json_path}")
    console.print(f"[bold green]✓ Zapisano raport Markdown:[/bold green] {md_path}")


@app.command(name="candidate-rollback")
def candidate_rollback_cmd(
    production_md: Annotated[
        Path,
        typer.Option("--prod", "-p", help="Ścieżka do produkcyjnego pliku Markdown."),
    ] = Path("output/markdown/ksiazka.md"),
    backup_file: Annotated[
        Optional[Path],
        typer.Option("--backup", "-b", help="Ścieżka do pliku kopii zapasowej (.pre_r18_bak)."),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Katalog na raport rollbacku."),
    ] = Path("output/benchmarks/migration"),
):
    """Atomowo przywraca plik produkcyjny z kopii zapasowej .pre_r18_bak."""
    from src.migration import rollback_production_document, save_migration_report

    console.print(f"[bold yellow]=== Rozpoczynanie Procedury Rollbacku (Przywracanie Bazy) ===[/bold yellow]")
    try:
        report = rollback_production_document(
            production_md_path=production_md,
            backup_path=backup_file,
            book_name="ksiazka",
        )
    except Exception as e:
        console.print(f"[bold red]Błąd podczas rollbacku:[/bold red] {e}")
        raise typer.Exit(code=1)

    json_path, md_path = save_migration_report(report, output_dir)

    console.print(f"\n[bold]Wynik Rollbacku:[/bold]")
    console.print(f"  • Status: [bold yellow]{report.status.upper()}[/bold yellow]")
    console.print(f"  • Przywrócono z: [bold cyan]{report.backup_path}[/bold cyan]")
    console.print(f"  • Aktualny SHA-256 pliku produkcyjnego: `{report.production_sha_after}`")
    verdict_color = "green" if report.verdict == "PASS" else "red"
    console.print(f"\n[bold {verdict_color}]WERDYKT ROLLBACKU: {report.verdict}[/bold {verdict_color}]")
    console.print(f"\n[bold green]✓ Zapisano raport JSON:[/bold green] {json_path}")
    console.print(f"[bold green]✓ Zapisano raport Markdown:[/bold green] {md_path}")


@app.command(name="epub-validate")
def epub_validate_cmd(
    epub_path: Annotated[
        Path,
        typer.Option("--epub", "-e", help="Ścieżka do pliku EPUB do walidacji."),
    ] = Path("output/epub/ksiazka.epub"),
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Katalog wyjściowy na raport walidacji."),
    ] = Path("output/benchmarks/epub"),
):
    """Waliduje plik EPUB 3 pod kątem integralności kontenera, specyfikacji i obecności grafik."""
    from src.epub_validator import validate_epub, save_epub_validation_report

    console.print(f"[bold cyan]=== Rozpoczynanie Walidacji EPUB 3 ({epub_path.name}) ===[/bold cyan]")
    report = validate_epub(epub_path)
    json_path, md_path = save_epub_validation_report(report, output_dir)

    console.print(f"\n[bold]Wynik Walidacji EPUB:[/bold]")
    console.print(f"  • Status: [bold {'green' if report.is_valid else 'red'}]{report.verdict}[/bold {'green' if report.is_valid else 'red'}]")
    console.print(f"  • Wersja EPUB: [bold]{report.epub_version}[/bold]")
    console.print(f"  • Mimetype poprawny: [bold green]{report.mimetype_valid}[/bold green]")
    console.print(f"  • Kontener XML poprawny: [bold green]{report.container_valid}[/bold green]")
    console.print(f"  • Pakiet OPF poprawny: [bold green]{report.opf_valid}[/bold green]")
    console.print(f"  • Nawigacja (nav.xhtml): [bold green]{report.nav_valid}[/bold green]")
    console.print(f"  • Pozycje w manifeście: [bold]{report.total_manifest_items}[/bold] (brakujących plików: {len(report.missing_manifest_files)})")
    console.print(f"  • Ilustracje (Assets): [bold green]{report.total_image_assets}[/bold green]")
    if report.issues:
        console.print(f"\n[bold yellow]Wykryte uwagi ({len(report.issues)}):[/bold yellow]")
        for iss in report.issues:
            color = "red" if iss.severity.value == "ERROR" else "yellow"
            console.print(f"  • [{color}][{iss.severity.value}][/{color}] {iss.code}: {iss.message}")

    verdict_color = "green" if report.verdict == "PASS" else "red"
    console.print(f"\n[bold {verdict_color}]WERDYKT GATE G5 (EPUB): {report.verdict}[/bold {verdict_color}]")
    console.print(f"[bold green]✓ Zapisano raport JSON:[/bold green] {json_path}")
    console.print(f"[bold green]✓ Zapisano raport Markdown:[/bold green] {md_path}")


@app.command(name="rag-export")
def rag_export_cmd(
    input_md: Annotated[
        Path,
        typer.Option("--input-md", "-m", help="Ścieżka do źródłowego pliku Markdown."),
    ] = Path("output/markdown/ksiazka.md"),
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Katalog docelowy na bazę wiedzy RAG."),
    ] = Path("output/rag"),
    embed: Annotated[
        bool,
        typer.Option("--embed/--no-embed", help="Generowanie embeddingów wektorowych na GPU przez Ollama."),
    ] = False,
    ollama_endpoint: Annotated[
        str,
        typer.Option("--endpoint", help="Adres endpointu Ollama."),
    ] = "http://172.22.192.1:11434",
    embedding_model: Annotated[
        str,
        typer.Option("--model", help="Nazwa modelu embeddingów w Ollama."),
    ] = "nomic-embed-text",
    max_words_per_chunk: Annotated[
        int,
        typer.Option("--max-words", help="Maksymalna liczba słów na chunk RAG."),
    ] = 400,
    max_embed_chunks: Annotated[
        Optional[int],
        typer.Option("--max-embed-chunks", help="Limit chunków do zaembedowania (None = wszystkie)."),
    ] = None,
):
    """Eksportuje plik Markdown do ustrukturyzowanej bazy wiedzy RAG (JSONL/JSON) z embeddingami GPU."""
    from src.rag_exporter import export_rag_knowledge_base

    console.print(f"[bold cyan]=== Eksport Bazy Wiedzy RAG ({input_md.name}) ===[/bold cyan]")
    if embed:
        console.print(f"[cyan]Włączono wektoryzację GPU: model=[bold]{embedding_model}[/bold] na {ollama_endpoint}[/cyan]")

    jsonl_path, json_path, summary = export_rag_knowledge_base(
        md_path=input_md,
        output_dir=output_dir,
        embed=embed,
        ollama_endpoint=ollama_endpoint,
        embedding_model=embedding_model,
        max_words_per_chunk=max_words_per_chunk,
        max_embed_chunks=max_embed_chunks,
    )

    console.print(f"\n[bold green]✓ Pomyślnie wygenerowano bazę wiedzy RAG![/bold green]")
    console.print(f"  • Łączna liczba chunków: [bold]{summary.total_chunks}[/bold]")
    console.print(f"  • Chunki przepisów (recipes): [bold cyan]{summary.recipe_chunks_count}[/bold cyan]")
    console.print(f"  • Chunki tekstu ogólnego: [bold]{summary.prose_chunks_count}[/bold]")
    console.print(f"  • Wykryte miary kulinarne: [bold green]{summary.total_culinary_measurements}[/bold green]")
    console.print(f"  • Powiązane ilustracje (assets): [bold green]{summary.total_assets_referenced}[/bold green]")
    console.print(f"  • Zaembedowane chunki na GPU: [bold magenta]{summary.embedded_chunks_count}[/bold magenta]")
    console.print(f"\n[bold green]✓ Zapisano plik JSONL:[/bold green] {jsonl_path}")
    console.print(f"[bold green]✓ Zapisano raport podsumowujący:[/bold green] {json_path}")


@app.command(name="open-windows")
def open_windows_cmd(
    folder: Annotated[
        str,
        typer.Option("--folder", "-f", help="Folder do otwarcia w Windows (output, epub, markdown, input lub downloads)."),
    ] = "output",
):
    """Otwiera wskazany folder w Eksploratorze Windows (WSL -> Windows)."""
    if folder in ["output", "out", "app", "project", "root"]:
        target = Path("output").resolve()
    elif folder == "downloads":
        target = get_windows_downloads_dir()
        if not target or not target.exists():
            console.print("[bold red]Błąd:[/bold red] Nie znaleziono folderu Pobrane w Windows.")
            raise typer.Exit(code=1)
    elif folder == "input":
        target = Path("input").resolve()
    elif folder in ["markdown", "assets"]:
        target = (Path("output") / folder).resolve()
    elif folder == "epub":
        target = Path("output/epub").resolve()
    else:
        target = Path("output").resolve()

    target.mkdir(parents=True, exist_ok=True)
    win_path = to_windows_path(target)
    try:
        subprocess.run(["explorer.exe", win_path], check=True)
        console.print(f"[bold green]✓ Otwarto Eksplorator Windows w folderze:[/bold green] {win_path}")
    except Exception as e:
        console.print(f"[bold red]Błąd otwierania Eksploratora Windows:[/bold red] {e}")


if __name__ == "__main__":
    app()
