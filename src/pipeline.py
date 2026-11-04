import os
import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Optional
import pypdfium2 as pdfium
import typer
from rich.console import Console
from rich.table import Table

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions, EasyOcrOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.progress import PageCompletedProgress
from docling_core.types.doc.base import ImageRefMode

from src.cleaner import clean_markdown_file
from src.auditor import audit_markdown_file
from src.repair import get_anomaly_details, patch_paragraph, patch_batch_from_file
from src.snapshot import SnapshotOptions, create_snapshot, verify_snapshot_integrity
from src.history import load_history_ledger, verify_index_anchors
from src.benchmark_manifest import load_test_pages_manifest, validate_manifest_consistency

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


def has_text_layer(pdf_path: str, max_check_pages: int = 5) -> bool:
    """Sprawdza, czy plik PDF posiada wbudowaną warstwę tekstową."""
    try:
        pdf = pdfium.PdfDocument(pdf_path)
        total_pages = len(pdf)
        check_indices = [min(i, total_pages - 1) for i in [1, 5, 10, 15] if i < total_pages]
        if not check_indices:
            check_indices = [0]
        for idx in check_indices:
            text = pdf[idx].get_textpage().get_text_range().strip()
            if len(text) > 50:
                return True
        return False
    except Exception:
        return False


def get_converter(do_ocr: bool, langs: list[str]) -> DocumentConverter:
    """Inicjalizuje DocumentConverter z opcjonalną obsługą OCR."""
    pipeline_options = PdfPipelineOptions()
    pipeline_options.generate_page_images = False
    pipeline_options.generate_picture_images = True
    pipeline_options.do_ocr = do_ocr
    if do_ocr:
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
) -> Path:
    """Czysta funkcja biznesowa: ekstrakcja PDF do Markdown."""
    if not input_pdf.exists():
        console.print(f"[bold red]Błąd: Nie znaleziono pliku wejściowego:[/bold red] {input_pdf}")
        raise typer.Exit(code=1)

    lang_list = [l.strip() for l in languages.split(",") if l.strip()]

    if ocr is None:
        has_text = has_text_layer(str(input_pdf))
        do_ocr = not has_text
        if has_text:
            console.print("[green]Wykryto warstwę tekstową w PDF: użycie natywnego układu bez OCR (zachowanie kolumn, błyskawiczna konwersja)...[/green]")
        else:
            console.print("[yellow]Brak warstwy tekstowej: włączanie OCR (EasyOCR)...[/yellow]")
    else:
        do_ocr = ocr
        status_msg = "Wymuszono OCR" if do_ocr else "Wyłączono OCR"
        console.print(f"[cyan]{status_msg}[/cyan]")

    md_dir = output_dir / "markdown"
    assets_dir = md_dir / "assets"
    md_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    converter = get_converter(do_ocr=do_ocr, langs=lang_list)

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
            console.print(f"[cyan]Postęp ekstrakcji:[/cyan] strona {event.page_no} (ukończono {event.completed_pages}/{event.total_pages})")

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
):
    """Krok 1: Ekstrakcja układu, tekstu i grafik z PDF do Markdown (z auto-detekcją OCR)."""
    if book:
        clean_stem = book.replace(".pdf", "")
        input_pdf = Path("input") / f"{clean_stem}.pdf"
    elif input_pdf is None:
        pdf_files = sorted(list(Path("input").glob("*.pdf")))
        if not pdf_files:
            console.print("[bold red]Błąd:[/bold red] Brak plików .pdf w katalogu input/")
            raise typer.Exit(code=1)
        input_pdf = pdf_files[0]

    extract_pdf(input_pdf=input_pdf, output_dir=output_dir, languages=languages, pages=pages, ocr=ocr)


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
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o")] = Path("output"),
):
    """
    Usuwa wygenerowane pliki robocze (.md, .bak, assets, raporty) po skończonej pracy.
    Domyślnie zachowuje gotowy plik .epub (do usuwania EPUB służy flaga --only-epub lub dedykowany przycisk).
    """
    deleted_files = 0

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
            output_dir / "markdown" / f"{stem}.md",
            output_dir / "markdown" / f"{stem}.md.bak",
            output_dir / f"audit_{stem}.json",
            output_dir / "audit_report.json",
        ]

        if with_epub:
            files_to_remove.append(epub_file)

        for f in files_to_remove:
            if f.exists():
                f.unlink()
                console.print(f"[yellow]Usunięto:[/yellow] {f}")
                deleted_files += 1

        # Usunięcie wyekstrahowanych grafik w assets
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

        scope = "wszystkie pliki robocze oraz EPUB" if with_epub else "pliki robocze (z zachowaniem EPUB)"
        console.print(f"[bold green]✓ Wyczyszczono {scope} dla książki: {stem} (usunięto {deleted_files} plików)[/bold green]")
        return

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

        nested_output = output_dir / "markdown" / "output"
        if nested_output.exists():
            shutil.rmtree(nested_output)
            deleted_files += 1

        if with_epub:
            epub_folder = output_dir / "epub"
            if epub_folder.exists():
                for item in epub_folder.glob("*.epub"):
                    item.unlink()
                    deleted_files += 1

        for rep in output_dir.glob("audit*.json"):
            rep.unlink()
            deleted_files += 1

        scope = "wszystkie pliki wyjściowe łącznie z EPUB" if with_epub else "pliki robocze (z zachowaniem EPUB)"
        console.print(f"[bold green]✓ Pomyślnie wyczyszczono {scope} (usunięto {deleted_files} plików)[/bold green]")
        return

    console.print("[yellow]Podaj --book <nazwa> lub --all, aby określić co usunąć.[/yellow]")


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
        typer.Option("--mode", "-m", help="Jawny tryb OCR: 'native' (warstwa PDF), 'full_page' (EasyOCR cała strona), 'layout_regions'."),
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
        f"tryb=[bold]{engine_mode.value}[/bold], PDF={pdf_path.name}...[/cyan]"
    )

    manifest = run_candidate_conversion(
        pdf_path=pdf_path,
        pages=parsed_pages,
        mode=engine_mode,
        languages=lang_list,
        output_base_dir=output_dir,
        run_id=run_id,
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
