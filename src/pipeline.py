import os
import shutil
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
from src.repair import get_anomaly_details, patch_paragraph, patch_batch_from_file

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
        shutil.copy2(epub_path, dest)
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
    input_md: Annotated[Optional[Path], typer.Option("--input-md", "-m")] = None,
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o")] = Path("output"),
):
    """Podmienia wskazany uszkodzony akapit na zrekonstruowany tekst (tworząc kopię .bak)."""
    input_md = ensure_markdown_exists(input_md=input_md, output_dir=output_dir)

    try:
        patch_paragraph(input_md, index, text)
        console.print(f"[bold green]✓ Pomyślnie podmieniono akapit #{index} w {input_md.name} (utworzono {input_md.name}.bak)[/bold green]")
    except Exception as e:
        console.print(f"[bold red]Błąd:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command(name="patch-batch")
def patch_batch_cmd(
    fixes_file: Annotated[Path, typer.Option("--file", "-f", help="Ścieżka do pliku JSON ze słownikiem poprawek.")],
    input_md: Annotated[Optional[Path], typer.Option("--input-md", "-m")] = None,
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o")] = Path("output"),
):
    """Aplikuje paczkę poprawek z pliku JSON na pliku Markdown (tworząc kopię .bak)."""
    input_md = ensure_markdown_exists(input_md=input_md, output_dir=output_dir)

    try:
        count = patch_batch_from_file(input_md, fixes_file)
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
