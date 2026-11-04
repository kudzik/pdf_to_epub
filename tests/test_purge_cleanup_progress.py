from pathlib import Path
from typer.testing import CliRunner
from src.pipeline import app, purge_cmd

runner = CliRunner()


def test_purge_removes_extract_progress_file(tmp_path: Path):
    """Weryfikuje, czy komenda purge usuwa plik extract_progress_<book>.json dla danej książki."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "markdown").mkdir(parents=True, exist_ok=True)

    prog_file = out_dir / "extract_progress_ksiazka.json"
    prog_file.write_text('{"status": "completed", "percent": 100}')

    md_file = out_dir / "markdown" / "ksiazka.md"
    md_file.write_text("# Test")

    assert prog_file.exists()
    assert md_file.exists()

    purge_cmd(book="ksiazka", keep_epub=True, output_dir=out_dir)

    assert not prog_file.exists(), "Plik extract_progress_ksiazka.json powinien zostać usunięty przez purge!"
    assert not md_file.exists(), "Plik ksiazka.md powinien zostać usunięty przez purge!"


def test_purge_all_removes_all_progress_files(tmp_path: Path):
    """Weryfikuje, czy purge --all usuwa wszystkie pliki postępu ekstrakcji."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    prog1 = out_dir / "extract_progress_book1.json"
    prog2 = out_dir / "extract_progress_book2.json"
    prog1.write_text('{"status": "completed"}')
    prog2.write_text('{"status": "running"}')

    assert prog1.exists()
    assert prog2.exists()

    purge_cmd(all=True, keep_epub=True, output_dir=out_dir)

    assert not prog1.exists(), "prog1 powinien zostać usunięty przez purge --all"
    assert not prog2.exists(), "prog2 powinien zostać usunięty przez purge --all"


def test_purge_temp_only_removes_progress_files(tmp_path: Path):
    """Weryfikuje, czy purge --temp-only usuwa pliki postępu ekstrakcji."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    prog = out_dir / "extract_progress_temp.json"
    prog.write_text('{"status": "running"}')

    purge_cmd(temp_only=True, output_dir=out_dir)

    assert not prog.exists(), "Plik postępu powinien zostać usunięty w trybie temp-only!"

