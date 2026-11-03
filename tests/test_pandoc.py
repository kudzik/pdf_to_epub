import re
import shutil
import subprocess
import zipfile
from pathlib import Path


def test_pandoc_binary_exists():
    """Weryfikuje, czy kompilator pandoc jest dostępny w PATH."""
    pandoc_path = shutil.which("pandoc")
    assert pandoc_path is not None, "Program 'pandoc' nie został znaleziony w PATH."


def test_pandoc_version_is_3_or_higher():
    """Weryfikuje, czy wersja Pandoc to co najmniej 3.0."""
    result = subprocess.run(
        ["pandoc", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    first_line = result.stdout.splitlines()[0]
    match = re.search(r"pandoc\s+([0-9]+)\.([0-9]+)", first_line)
    assert match is not None, f"Nie udało się odczytać wersji Pandoc z: '{first_line}'"
    major = int(match.group(1))
    assert major >= 3, f"Wymagany Pandoc >= 3.0, wykryto wersję: {first_line}"


def test_epub_generation_and_archive_validity(tmp_path: Path):
    """Kompiluje testowy plik Markdown do EPUB i weryfikuje poprawność kontenera EPUB."""
    md_file = tmp_path / "test_book.md"
    md_file.write_text(
        "# Rozdział 1: Testowy\n\n"
        "Zażółć gęślą jaźń.\n\n"
        "| Kolumna A | Kolumna B |\n"
        "|---|---|\n"
        "| Wartość 1 | Wartość 2 |\n",
        encoding="utf-8",
    )

    metadata_file = tmp_path / "metadata.yaml"
    metadata_file.write_text(
        'title: "Testowa Książka"\n'
        'author:\n  - "Tester AI"\n'
        'lang: "pl-PL"\n',
        encoding="utf-8",
    )

    css_file = Path("templates/epub.css")
    epub_out = tmp_path / "test_book.epub"

    cmd = [
        "pandoc",
        str(md_file),
        "-o",
        str(epub_out),
        f"--resource-path={tmp_path}",
        "--metadata-file",
        str(metadata_file),
        "--toc",
        "--toc-depth=3",
        "--split-level=2",
    ]
    if css_file.exists():
        cmd.append(f"--css={css_file}")

    subprocess.run(cmd, check=True)

    assert epub_out.exists(), "Plik EPUB nie został wygenerowany."
    assert epub_out.stat().st_size > 0, "Wygenerowany plik EPUB jest pusty."

    # Weryfikacja struktury standardu EPUB (ZIP archive)
    with zipfile.ZipFile(epub_out, "r") as zf:
        namelist = zf.namelist()
        assert "mimetype" in namelist, "Brak wymaganego pliku 'mimetype' w archiwum EPUB."
        assert "META-INF/container.xml" in namelist, "Brak wymaganego pliku container.xml."

        # Sprawdzenie zawartości pliku mimetype (musi być ściśle 'application/epub+zip')
        mimetype_content = zf.read("mimetype").decode("utf-8").strip()
        assert mimetype_content == "application/epub+zip", (
            f"Nieprawidłowy mimetype w EPUB: '{mimetype_content}'"
        )

