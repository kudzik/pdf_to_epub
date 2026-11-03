import subprocess
from pathlib import Path
from typing import Annotated, Optional
import pypdfium2 as pdfium
import typer
from rich.console import Console

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions, EasyOcrOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.progress import PageCompletedProgress
from docling_core.types.doc.base import ImageRefMode

from src.cleaner import clean_markdown_file
from src.auditor import audit_markdown_file

app = typer.Typer(
    help="Pipeline ekstrakcji ze skanów PDF do Markdown i kompilacji EPUB 3.",
    no_args_is_help=True,
)
console = Console()


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
    """Czysta funkcja biznesowa: ekstrakcja układu, tekstu i grafik z PDF do Markdown."""
    if not input_pdf.exists():
        console.print(f"[bold red]Błąd: Nie znaleziono pliku wejściowego:[/bold red] {input_pdf}")
        raise typer.Exit(code=1)

    md_dir = output_dir / "markdown"
    assets_dir = md_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Inteligentna detekcja OCR
    if ocr is None:
        has_text = has_text_layer(str(input_pdf))
        do_ocr = not has_text
        if has_text:
            console.print("[cyan]Wykryto warstwę tekstową w PDF: użycie natywnego układu bez OCR (zachowanie kolumn, błyskawiczna konwersja)...[/cyan]")
        else:
            console.print("[yellow]Brak warstwy tekstowej w PDF: uruchamianie silnika OCR...[/yellow]")
    else:
        do_ocr = ocr
        if do_ocr:
            console.print("[yellow]Wymuszono silnik OCR zgodnie z opcją CLI...[/yellow]")
        else:
            console.print("[cyan]Wyłączono OCR zgodnie z opcją CLI (tryb czystego tekstu PDF)...[/cyan]")

    lang_list = [lang.strip() for lang in languages.split(",") if lang.strip()]
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
        artifacts_dir=assets_dir,
    )

    console.print(f"[bold green]✓ Zapisano Markdown:[/bold green] {md_file}")
    console.print(f"[bold green]✓ Grafiki wyodrębniono do:[/bold green] {assets_dir}")
    return md_file


def compile_epub(
    input_md: Optional[Path] = None,
    output_dir: Path = Path("output"),
    metadata_file: Optional[Path] = Path("metadata.yaml"),
    css_file: Optional[Path] = Path("templates/epub.css"),
    cover_image: Optional[Path] = None,
) -> Path:
    """Czysta funkcja biznesowa: kompilacja pliku Markdown do EPUB 3 za pomocą Pandoc."""
    md_dir = output_dir / "markdown"
    epub_dir = output_dir / "epub"
    epub_dir.mkdir(parents=True, exist_ok=True)

    if input_md is None:
        md_files = sorted(list(md_dir.glob("*.md")))
        if not md_files:
            console.print(f"[bold red]Błąd:[/bold red] Nie znaleziono żadnego pliku .md w {md_dir}")
            raise typer.Exit(code=1)
        input_md = md_files[0]

    if not input_md.exists():
        console.print(f"[bold red]Błąd:[/bold red] Plik {input_md} nie istnieje.")
        raise typer.Exit(code=1)

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
        Path,
        typer.Option("--input-pdf", "-i", help="Ścieżka do wejściowego pliku PDF."),
    ] = Path("input/ksiazka.pdf"),
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
    extract_pdf(input_pdf=input_pdf, output_dir=output_dir, languages=languages, pages=pages, ocr=ocr)


@app.command()
def clean(
    input_md: Annotated[
        Optional[Path],
        typer.Option("--input-md", "-m", help="Ścieżka do pliku Markdown do oczyszczenia."),
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
    """Krok 2: Czyszczenie Markdowna (de-hyphenation, normalizacja ułamków, usuwanie szumów)."""
    if input_md is None:
        md_dir = output_dir / "markdown"
        md_files = sorted(list(md_dir.glob("*.md")))
        if not md_files:
            console.print(f"[bold red]Błąd:[/bold red] Nie znaleziono żadnego pliku .md w {md_dir}")
            raise typer.Exit(code=1)
        input_md = md_files[0]

    console.print(f"[cyan]Czyszczenie i normalizacja: {input_md.name}...[/cyan]")
    out = clean_markdown_file(input_md, output_md)
    console.print(f"[bold green]✓ Pomyślnie oczyszczono plik Markdown:[/bold green] {out}")


@app.command()
def audit(
    input_md: Annotated[
        Optional[Path],
        typer.Option("--input-md", "-m", help="Ścieżka do pliku Markdown do audytu."),
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
    if input_md is None:
        md_dir = output_dir / "markdown"
        md_files = sorted(list(md_dir.glob("*.md")))
        if not md_files:
            console.print(f"[bold red]Błąd:[/bold red] Nie znaleziono żadnego pliku .md w {md_dir}")
            raise typer.Exit(code=1)
        input_md = md_files[0]

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


@app.command()
def build(
    input_md: Annotated[
        Optional[Path],
        typer.Option("--input-md", "-m", help="Ścieżka do pliku .md (domyślnie szuka pierwszego .md w output/markdown/)."),
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
):
    """Krok 3: Kompiluje plik Markdown do EPUB 3 za pomocą Pandoc."""
    compile_epub(
        input_md=input_md,
        output_dir=output_dir,
        metadata_file=metadata_file,
        css_file=css_file,
        cover_image=cover_image,
    )


@app.command()
def all(
    input_pdf: Annotated[
        Path,
        typer.Option("--input-pdf", "-i", help="Ścieżka do wejściowego pliku PDF."),
    ] = Path("input/ksiazka.pdf"),
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
):
    """Pełny pipeline: ekstrakcja (z auto-detekcją OCR), czyszczenie Markdowna i kompilacja do EPUB."""
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
    )


if __name__ == "__main__":
    app()
