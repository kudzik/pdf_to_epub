from pathlib import Path
from typer.testing import CliRunner
from src.pipeline import app, ensure_markdown_exists, get_windows_downloads_dir

runner = CliRunner()


def test_ensure_markdown_exists_finds_existing(tmp_path: Path):
    md_dir = tmp_path / "markdown"
    md_dir.mkdir(parents=True)
    sample_md = md_dir / "mojaksiazka.md"
    sample_md.write_text("# Test", encoding="utf-8")

    found = ensure_markdown_exists(book="mojaksiazka", output_dir=tmp_path)
    assert found == sample_md


def test_ensure_markdown_auto_extract_mock(monkeypatch, tmp_path: Path):
    """Gdy plik markdown nie istnieje, ale istnieje input/<stem>.pdf, uruchamia extract_pdf."""
    fake_input_dir = tmp_path / "input"
    fake_input_dir.mkdir(parents=True)
    fake_pdf = fake_input_dir / "nowaksiazka.pdf"
    fake_pdf.write_bytes(b"%PDF dummy")

    # Podmieniamy ścieżkę input na nasz tmp_path/input
    monkeypatch.setattr("src.pipeline.Path", lambda *args: fake_input_dir if args == ("input",) else Path(*args))

    extracted = False

    def mock_extract(pdf, output_dir=None):
        nonlocal extracted
        extracted = True
        target = (output_dir / "markdown" / f"{pdf.stem}.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# Auto Extracted", encoding="utf-8")
        return target

    monkeypatch.setattr("src.pipeline.extract_pdf", mock_extract)

    result_md = ensure_markdown_exists(book="nowaksiazka", output_dir=tmp_path)
    assert extracted is True
    assert result_md.exists()
    assert "# Auto Extracted" in result_md.read_text(encoding="utf-8")


def test_purge_command_book_keep_epub(tmp_path: Path):
    """Weryfikuje usuwanie plików roboczych książki z zachowaniem EPUB."""
    md_dir = tmp_path / "markdown"
    epub_dir = tmp_path / "epub"
    md_dir.mkdir(parents=True)
    epub_dir.mkdir(parents=True)

    md_file = md_dir / "ksiazka1.md"
    bak_file = md_dir / "ksiazka1.md.bak"
    epub_file = epub_dir / "ksiazka1.epub"
    report_file = tmp_path / "audit_ksiazka1.json"

    md_file.write_text("md", encoding="utf-8")
    bak_file.write_text("bak", encoding="utf-8")
    epub_file.write_text("epub", encoding="utf-8")
    report_file.write_text("{}", encoding="utf-8")

    result = runner.invoke(
        app,
        ["purge", "--book", "ksiazka1", "--keep-epub", "--output-dir", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "Wyczyszczono pliki robocze" in result.stdout
    assert "ksiazka1" in result.stdout

    # Pliki robocze usunięte
    assert not md_file.exists()
    assert not bak_file.exists()
    assert not report_file.exists()
    # EPUB zachowany
    assert epub_file.exists()


def test_purge_only_epub_command(tmp_path: Path):
    """Weryfikuje usuwanie wyłącznie pliku EPUB z zachowaniem plików roboczych."""
    md_dir = tmp_path / "markdown"
    epub_dir = tmp_path / "epub"
    md_dir.mkdir(parents=True)
    epub_dir.mkdir(parents=True)

    md_file = md_dir / "ksiazka1.md"
    epub_file = epub_dir / "ksiazka1.epub"
    md_file.write_text("markdown content", encoding="utf-8")
    epub_file.write_text("epub content", encoding="utf-8")

    result = runner.invoke(
        app,
        ["purge", "--book", "ksiazka1", "--only-epub", "--output-dir", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "Usunięto plik EPUB dla książki: ksiazka1" in result.stdout

    # Markdown zachowany, EPUB usunięty
    assert md_file.exists()
    assert not epub_file.exists()


def test_purge_all_command(tmp_path: Path):
    """Weryfikuje czyszczenie wszystkich plików roboczych."""
    md_dir = tmp_path / "markdown"
    epub_dir = tmp_path / "epub"
    assets_dir = md_dir / "assets"
    assets_dir.mkdir(parents=True)
    epub_dir.mkdir(parents=True)

    (md_dir / "book1.md").write_text("content", encoding="utf-8")
    (assets_dir / "img1.png").write_text("png", encoding="utf-8")
    (epub_dir / "book1.epub").write_text("epub", encoding="utf-8")
    (tmp_path / "audit_report.json").write_text("{}", encoding="utf-8")

    result = runner.invoke(
        app,
        ["purge", "--all", "--output-dir", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "Pomyślnie wyczyszczono" in result.stdout

    assert not (md_dir / "book1.md").exists()
    assert not (assets_dir / "img1.png").exists()
    assert not (epub_dir / "book1.epub").exists()
    assert not (tmp_path / "audit_report.json").exists()


def test_get_windows_downloads_dir_returns_path_or_none():
    """Weryfikuje, że detektor Windows Downloads zwraca poprawną ścieżkę lub None bez błędu."""
    dl = get_windows_downloads_dir()
    if dl is not None:
        assert isinstance(dl, Path)
        assert dl.name == "Downloads"

