import subprocess
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions, EasyOcrOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.progress import PageCompletedProgress
from docling_core.types.doc.base import ImageRefMode

app = typer.Typer(
    help="Pipeline ekstrakcji ze skanów PDF do Markdown i kompilacji EPUB 3.",
    no_args_is_help=True,
)
console = Console()


def get_converter(langs: list[str]) -> DocumentConverter:
    """Inicjalizuje DocumentConverter z obsługą OCR dla wskazanych języków."""
    pipeline_options = PdfPipelineOptions()
    pipeline_options.generate_page_images = False
    pipeline_options.generate_picture_images = True
    pipeline_options.do_ocr = True
    pipeline_options.ocr_options = EasyOcrOptions(lang=langs)

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


@app.command()
def extract(
    input_pdf: Path = typer.Option(
        Path("input/ksiazka.pdf"),
        "--input-pdf",
        "-i",
        help="Ścieżka do wejściowego pliku PDF.",
    ),
    output_dir: Path = typer.Option(
        Path("output"),
        "--output-dir",
        "-o",
        help="Główny katalog wyjściowy.",
    ),
    languages: str = typer.Option(
        "pl,en",
        "--langs",
        "-l",
        help="Kody języków dla OCR oddzielone przecinkami (np. pl,en).",
    ),
    pages: Optional[str] = typer.Option(
        None,
        "--pages",
        "-p",
        help="Zakres stron do przetworzenia, np. 1-10.",
    ),
):
    """Krok 1: Wykonuje OCR, analizuje układ i zapisuje Markdown oraz wycięte grafiki."""
    if not input_pdf.exists():
        console.print(f"[bold red]Błąd: Nie znaleziono pliku wejściowego:[/bold red] {input_pdf}")
        raise typer.Exit(code=1)

    md_dir = output_dir / "markdown"
    assets_dir = md_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    lang_list = [lang.strip() for lang in languages.split(",") if lang.strip()]
    console.print(f"[cyan]Inicjalizacja silnika Docling (OCR języki: {lang_list})...[/cyan]")
    converter = get_converter(lang_list)

    page_range = None
    if pages:
        try:
            parts = [int(p.strip()) for p in pages.split("-")]
            if len(parts) == 1:
                page_range = (parts[0], parts[0])
            elif len(parts) == 2:
                page_range = (parts[0], parts[1])
            else:
                raise ValueError
            console.print(f"[yellow]Ograniczenie przetwarzania do stron: {page_range}[/yellow]")
        except ValueError:
            console.print(f"[bold red]Błąd:[/bold red] Niepoprawny format stron: '{pages}'. Użyj formatu np. '1-10' lub '5'.")
            raise typer.Exit(code=1)

    convert_kwargs = {}
    if page_range is not None:
        convert_kwargs["page_range"] = page_range

    def on_progress(event):
        if isinstance(event, PageCompletedProgress) and event.total_pages:
            console.print(f"[cyan]Postęp OCR:[/cyan] strona {event.page_no} (ukończono {event.completed_pages}/{event.total_pages})")

    convert_kwargs["progress_callback"] = on_progress

    console.print(f"[cyan]Przetwarzanie dokumentu {input_pdf.name}... To może chwilę potrwać.[/cyan]")
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


@app.command()
def build(
    input_md: Optional[Path] = typer.Option(
        None,
        "--input-md",
        "-m",
        help="Ścieżka do pliku .md (domyślnie szuka pierwszego .md w output/markdown/).",
    ),
    output_dir: Path = typer.Option(
        Path("output"),
        "--output-dir",
        "-o",
        help="Główny katalog wyjściowy.",
    ),
    metadata_file: Path = typer.Option(
        Path("metadata.yaml"),
        "--metadata",
        help="Plik metadanych publikacji (YAML).",
    ),
    css_file: Path = typer.Option(
        Path("templates/epub.css"),
        "--css",
        help="Arkusz stylów CSS dla EPUB.",
    ),
    cover_image: Optional[Path] = typer.Option(
        None,
        "--cover",
        help="Ścieżka do pliku okładki (JPG/PNG).",
    ),
):
    """Krok 2: Kompiluje plik Markdown do EPUB 3 za pomocą Pandoc."""
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

    if metadata_file.exists():
        pandoc_cmd.append(f"--metadata-file={metadata_file}")
    else:
        console.print(f"[yellow]Ostrzeżenie: Nie znaleziono pliku metadanych {metadata_file}. Kontynuowanie bez metadanych.[/yellow]")

    if css_file.exists():
        pandoc_cmd.append(f"--css={css_file}")

    if cover_image and cover_image.exists():
        pandoc_cmd.append(f"--epub-cover-image={cover_image}")

    try:
        subprocess.run(pandoc_cmd, check=True)
        console.print(f"[bold green]✓ Pomyślnie wygenerowano EPUB:[/bold green] {epub_file}")
    except FileNotFoundError:
        console.print("[bold red]Błąd:[/bold red] Program 'pandoc' nie jest zainstalowany w ścieżce PATH.")
        raise typer.Exit(code=1)
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]Błąd Pandoc podczas kompilacji EPUB:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command()
def all(
    input_pdf: Path = typer.Option(
        Path("input/ksiazka.pdf"),
        "--input-pdf",
        "-i",
        help="Ścieżka do wejściowego pliku PDF.",
    ),
    output_dir: Path = typer.Option(
        Path("output"),
        "--output-dir",
        "-o",
        help="Główny katalog wyjściowy.",
    ),
    metadata_file: Path = typer.Option(
        Path("metadata.yaml"),
        "--metadata",
        help="Plik metadanych publikacji (YAML).",
    ),
    languages: str = typer.Option(
        "pl,en",
        "--langs",
        "-l",
        help="Kody języków dla OCR oddzielone przecinkami (np. pl,en).",
    ),
    pages: Optional[str] = typer.Option(
        None,
        "--pages",
        "-p",
        help="Zakres stron do przetworzenia, np. 1-10.",
    ),
    cover_image: Optional[Path] = typer.Option(
        None,
        "--cover",
        help="Ścieżka do pliku okładki (JPG/PNG).",
    ),
):
    """Pełny pipeline: ekstrakcja do Markdown, a następnie automatyczna kompilacja do EPUB."""
    extract(input_pdf=input_pdf, output_dir=output_dir, languages=languages, pages=pages)
    md_file = output_dir / "markdown" / f"{input_pdf.stem}.md"
    build(input_md=md_file, output_dir=output_dir, metadata_file=metadata_file, cover_image=cover_image)


if __name__ == "__main__":
    app()
