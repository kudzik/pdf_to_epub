from pathlib import Path
from typer.testing import CliRunner
from src.pipeline import app

runner = CliRunner()


def test_cli_help():
    """Weryfikuje poprawność polecenia --help i obecność podkomend."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "extract" in result.stdout
    assert "build" in result.stdout
    assert "all" in result.stdout


def test_extract_missing_pdf(tmp_path: Path):
    """Weryfikuje obsługę błędu przy braku pliku PDF."""
    non_existent = tmp_path / "missing.pdf"
    result = runner.invoke(app, ["extract", "--input-pdf", str(non_existent)])
    assert result.exit_code == 1
    assert "Nie znaleziono pliku wejściowego" in result.stdout


def test_extract_invalid_pages_format(tmp_path: Path):
    """Weryfikuje walidację niepoprawnego formatu stron --pages."""
    dummy_pdf = tmp_path / "dummy.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 dummy content")
    result = runner.invoke(app, ["extract", "--input-pdf", str(dummy_pdf), "--pages", "invalid-range"])
    assert result.exit_code == 1
    assert "Niepoprawny format stron" in result.stdout


def test_build_missing_markdown(tmp_path: Path):
    """Weryfikuje błąd przy próbie budowy EPUB z pustego katalogu markdown."""
    empty_out = tmp_path / "empty_out"
    empty_out.mkdir()
    result = runner.invoke(app, ["build", "--output-dir", str(empty_out)])
    assert result.exit_code == 1
    assert "Nie znaleziono żadnego pliku .md" in result.stdout


def test_build_successful_with_sample_markdown(tmp_path: Path):
    """Weryfikuje pełne wykonanie podkomendy 'build' z poziomu CLI."""
    md_dir = tmp_path / "markdown"
    md_dir.mkdir(parents=True)
    sample_md = md_dir / "sample.md"
    sample_md.write_text(
        "# Rozdział Pierwszy\n\nPrzykładowa treść do weryfikacji CLI.\n",
        encoding="utf-8",
    )

    metadata = tmp_path / "metadata.yaml"
    metadata.write_text('title: "Test CLI"\nlang: "pl-PL"\n', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "build",
            "--input-md",
            str(sample_md),
            "--output-dir",
            str(tmp_path),
            "--metadata",
            str(metadata),
        ],
    )
    assert result.exit_code == 0, f"Błąd wykonania CLI: {result.stdout}"
    assert "Pomyślnie wygenerowano EPUB" in result.stdout

    epub_file = tmp_path / "epub" / "sample.epub"
    assert epub_file.exists()
    assert epub_file.stat().st_size > 0


def test_extract_regression_does_not_pass_none_page_range(monkeypatch, tmp_path: Path):
    """
    Test regresyjny: upewnia się, że brak flagi --pages nie przekazuje page_range=None
    do converter.convert(), co w Pydantic wywoływało ValidationError.
    """
    dummy_pdf = tmp_path / "dummy.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 dummy content")

    captured_kwargs = {}

    class MockDoc:
        def save_as_markdown(self, *args, **kwargs):
            pass

    class MockResult:
        document = MockDoc()

    class MockConverter:
        def convert(self, source, **kwargs):
            captured_kwargs.update(kwargs)
            return MockResult()

    monkeypatch.setattr("src.pipeline.get_converter", lambda *args, **kwargs: MockConverter())

    result = runner.invoke(
        app,
        [
            "extract",
            "--input-pdf",
            str(dummy_pdf),
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"extract rzucił błędem: {result.stdout}"
    assert "page_range" not in captured_kwargs, (
        f"page_range nie powinno znajdować się w kwargs, jeśli nie podano --pages! Otrzymano: {captured_kwargs}"
    )


def test_all_command_execution(monkeypatch, tmp_path: Path):
    """Weryfikuje, czy komenda 'all' bezproblemowo łączy extract i build bez błędu Typer OptionInfo."""
    dummy_pdf = tmp_path / "dummy.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 dummy content")

    class MockDoc:
        def save_as_markdown(self, target_path, *args, **kwargs):
            Path(target_path).write_text("# Rozdział 1\nTreść książki.", encoding="utf-8")

    class MockResult:
        document = MockDoc()

    class MockConverter:
        def convert(self, source, **kwargs):
            return MockResult()

    monkeypatch.setattr("src.pipeline.get_converter", lambda *args, **kwargs: MockConverter())

    metadata = tmp_path / "metadata.yaml"
    metadata.write_text('title: "Książka"\nlang: "pl-PL"\n', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "all",
            "--input-pdf",
            str(dummy_pdf),
            "--output-dir",
            str(tmp_path),
            "--metadata",
            str(metadata),
        ],
    )
    assert result.exit_code == 0, f"Błąd w 'all': {result.stdout}"
    assert "Pomyślnie wygenerowano EPUB" in result.stdout
    assert (tmp_path / "epub" / "dummy.epub").exists()


def test_clean_command(tmp_path: Path):
    """Weryfikuje wykonanie podkomendy 'clean' przez CLI."""
    md_dir = tmp_path / "markdown"
    md_dir.mkdir(parents=True)
    dirty_md = md_dir / "dirty.md"
    dirty_md.write_text("■ ^p-\nTake ½ tsp salt and ingre\xad dients.\n", encoding="utf-8")

    result = runner.invoke(app, ["clean", "--input-md", str(dirty_md)])
    assert result.exit_code == 0
    assert "Pomyślnie oczyszczono" in result.stdout

    cleaned_content = dirty_md.read_text(encoding="utf-8")
    assert "■ ^p-" not in cleaned_content
    assert "1/2 tsp" in cleaned_content
    assert "ingredients" in cleaned_content


def test_audit_command(tmp_path: Path):
    """Weryfikuje wykonanie podkomendy 'audit' przez CLI."""
    md_dir = tmp_path / "markdown"
    md_dir.mkdir(parents=True)
    sample_md = md_dir / "sample.md"
    sample_md.write_text("Normal text.\n\nCorrupted text with ha^been in die oven.\n", encoding="utf-8")
    report_file = tmp_path / "report.json"

    result = runner.invoke(app, ["audit", "--input-md", str(sample_md), "--report", str(report_file)])
    assert result.exit_code == 0
    assert "Audyt zakończony sukcesem" in result.stdout
    assert report_file.exists()


def test_inspect_and_patch_cli(tmp_path: Path):
    """Weryfikuje wykonanie podkomend inspect-anomaly oraz patch z poziomu CLI."""
    md_dir = tmp_path / "markdown"
    md_dir.mkdir(parents=True)
    sample_md = md_dir / "sample.md"
    sample_md.write_text("Wstęp.\n\nUszkodzony akapit z b°ynthe.\n\nZakończenie.", encoding="utf-8")
    report_file = tmp_path / "report.json"

    # 1. Tworzymy raport audytu
    res_audit = runner.invoke(app, ["audit", "--input-md", str(sample_md), "--report", str(report_file)])
    assert res_audit.exit_code == 0

    # 2. Inspekcja anomalii
    res_inspect = runner.invoke(app, ["inspect-anomaly", "1", "--report", str(report_file), "--input-md", str(sample_md)])
    assert res_inspect.exit_code == 0
    assert "Anomalia Akapitu #1" in res_inspect.stdout
    assert "b°ynthe" in res_inspect.stdout

    # 3. Podmiana akapitu (patch)
    res_patch = runner.invoke(app, ["patch", "1", "--text", "Naprawiony akapit tekstu.", "--input-md", str(sample_md)])
    assert res_patch.exit_code == 0
    assert "Pomyślnie podmieniono akapit #1" in res_patch.stdout

    # 4. Sprawdzenie zawartości
    new_text = sample_md.read_text(encoding="utf-8")
    assert "Naprawiony akapit tekstu." in new_text
    assert "b°ynthe" not in new_text







def test_windows_copy_failure_preserves_successful_build(monkeypatch, tmp_path):
    """A missing Windows write permission must not invalidate an existing EPUB."""
    from src import pipeline

    epub = tmp_path / "book.epub"
    epub.write_bytes(b"epub")
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    monkeypatch.setattr(pipeline, "get_windows_downloads_dir", lambda: downloads)

    def deny_copy(*args, **kwargs):
        raise PermissionError("Windows Downloads is read-only")

    monkeypatch.setattr(pipeline.shutil, "copy2", deny_copy)
    assert pipeline.copy_to_windows_downloads(epub) is None
    assert epub.read_bytes() == b"epub"
