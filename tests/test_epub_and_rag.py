"""Unit tests for EPUB 3 validation, conformance testing, and RAG knowledge base export (Gate G5 / R19)."""

import json
from pathlib import Path
import zipfile

import pytest

from src.epub_validator import (
    EpubValidationIssue,
    EpubValidationReport,
    IssueSeverity,
    save_epub_validation_report,
    validate_epub,
)
from src.rag_exporter import (
    RagChunk,
    chunk_markdown_document,
    export_rag_knowledge_base,
    extract_chunk_assets,
    generate_embedding_ollama,
    is_recipe_content,
)


def create_minimal_valid_epub(target_path: Path):
    """Helper creating a minimal valid EPUB 3 container."""
    container_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles>\n'
        '    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>\n'
        '  </rootfiles>\n'
        '</container>\n'
    )

    content_opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        '    <dc:identifier id="pub-id">urn:uuid:12345</dc:identifier>\n'
        '    <dc:title>Testowa Książka</dc:title>\n'
        '    <dc:language>pl</dc:language>\n'
        '    <dc:creator>Jan Kowalski</dc:creator>\n'
        '  </metadata>\n'
        '  <manifest>\n'
        '    <item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/>\n'
        '    <item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>\n'
        '    <item id="img1" href="media/img1.png" media-type="image/png"/>\n'
        '  </manifest>\n'
        '  <spine>\n'
        '    <itemref idref="c1"/>\n'
        '  </spine>\n'
        '</package>\n'
    )

    nav_xhtml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
        '<head><title>Nawigacja</title></head>\n'
        '<body><nav epub:type="toc"><ol><li><a href="text/ch1.xhtml">Rozdział 1</a></li></ol></nav></body>\n'
        '</html>\n'
    )

    ch1_xhtml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        '<head><title>Rozdział 1</title></head>\n'
        '<body><p>Tekst rozdziału.</p></body>\n'
        '</html>\n'
    )

    with zipfile.ZipFile(target_path, "w") as zf:
        # First entry must be mimetype uncompressed
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("EPUB/content.opf", content_opf)
        zf.writestr("EPUB/nav.xhtml", nav_xhtml)
        zf.writestr("EPUB/text/ch1.xhtml", ch1_xhtml)
        zf.writestr("EPUB/media/img1.png", b"\x89PNG\r\n\x1a\nmock")


def test_validate_epub_success(tmp_path: Path):
    """Verify that a standard-compliant EPUB 3 archive passes validation."""
    epub_path = tmp_path / "valid.epub"
    create_minimal_valid_epub(epub_path)

    report = validate_epub(epub_path)

    assert report.is_valid is True
    assert report.verdict == "PASS"
    assert report.mimetype_valid is True
    assert report.container_valid is True
    assert report.opf_valid is True
    assert report.nav_valid is True
    assert report.metadata.title == "Testowa Książka"
    assert "Jan Kowalski" in report.metadata.creator
    assert report.total_manifest_items == 3
    assert report.total_spine_items == 1
    assert report.total_image_assets == 1
    assert len(report.missing_manifest_files) == 0


def test_validate_epub_mimetype_and_container_errors(tmp_path: Path):
    """Verify detection of bad mimetype and missing container.xml."""
    # 1. Non-existent file
    rep_nonexistent = validate_epub(tmp_path / "absent.epub")
    assert rep_nonexistent.is_valid is False
    assert rep_nonexistent.verdict == "FAIL"

    # 2. Corrupt archive missing container.xml
    corrupt_epub = tmp_path / "corrupt.epub"
    with zipfile.ZipFile(corrupt_epub, "w") as zf:
        zf.writestr("mimetype", b"application/epub+zip")
        zf.writestr("some_file.txt", "hello")

    rep_corrupt = validate_epub(corrupt_epub)
    assert rep_corrupt.is_valid is False
    assert rep_corrupt.container_valid is False
    assert any(i.code == "MISSING_CONTAINER_XML" for i in rep_corrupt.issues)


def test_validate_epub_missing_manifest_item(tmp_path: Path):
    """Verify detection when manifest item does not exist in zip."""
    epub_path = tmp_path / "missing_file.epub"
    create_minimal_valid_epub(epub_path)

    # Re-pack without img1.png
    bad_epub = tmp_path / "bad.epub"
    with zipfile.ZipFile(epub_path, "r") as zf_in:
        with zipfile.ZipFile(bad_epub, "w") as zf_out:
            for item in zf_in.infolist():
                if item.filename != "EPUB/media/img1.png":
                    zf_out.writestr(item, zf_in.read(item.filename))

    rep = validate_epub(bad_epub)
    assert rep.is_valid is False
    assert rep.verdict == "FAIL"
    assert "EPUB/media/img1.png" in rep.missing_manifest_files


def test_chunk_markdown_document_recipes_and_sections(tmp_path: Path):
    """Verify hierarchical chunking, heading tracking, and recipe recognition."""
    md_file = tmp_path / "cookbook.md"
    content = (
        "# Traditional Polish Soups\n\n"
        "Soups are the cornerstone of traditional Polish dining.\n\n"
        "## Rosol Broth\n\n"
        "A delicate clear Sunday chicken soup.![Image](assets/rosol.png)\n\n"
        "Składniki:\n"
        "- 1 whole chicken\n"
        "- 2 tsp salt\n"
        "- 1/2 tsp black pepper\n"
        "- 1 cup carrots, sliced\n\n"
        "Przygotowanie:\n"
        "Cook and simmer for 2 hours at 190°F until clear.\n\n"
        "## Barszcz Red Beet Soup\n\n"
        "Fermented beet soup served with uszka dumplings.\n"
    )
    md_file.write_text(content, encoding="utf-8")

    chunks = chunk_markdown_document(md_file, max_words_per_chunk=30)

    assert len(chunks) >= 3

    # Check first section
    ch1 = chunks[0]
    assert ch1.title == "Traditional Polish Soups"
    assert "Traditional Polish Soups" in ch1.parent_sections

    # Check rosol recipe
    rosol_chunk = next((c for c in chunks if "Rosol" in c.title or "chicken" in c.content), None)
    assert rosol_chunk is not None
    assert rosol_chunk.chunk_type == "recipe"
    assert len(rosol_chunk.culinary_measurements) >= 2
    assert "assets/rosol.png" in rosol_chunk.assets_referenced


def test_export_rag_knowledge_base_files(tmp_path: Path):
    """Verify generation of jsonl and json summary files."""
    md_file = tmp_path / "test_doc.md"
    md_file.write_text(
        "# Chapter 1\n\n"
        "First paragraph.\n\n"
        "# Chapter 2\n\n"
        "Add 1 tbsp sugar and 1/2 cup milk.\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "rag_out"
    jsonl_path, json_path, summary = export_rag_knowledge_base(
        md_path=md_file,
        output_dir=out_dir,
        embed=False,
    )

    assert jsonl_path.exists()
    assert json_path.exists()
    assert summary.total_chunks == 2
    assert summary.total_words > 0

    lines = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0]["chunk_id"] == "chunk_00001"
    assert lines[1]["chunk_id"] == "chunk_00002"


def test_save_epub_validation_report(tmp_path: Path):
    """Verify persisting epub validation report to JSON and Markdown."""
    report = EpubValidationReport(
        run_id="test_val_001",
        epub_path="output/epub/ksiazka.epub",
        file_size_bytes=6543210,
        epub_version="3.0",
        is_valid=True,
        verdict="PASS",
        mimetype_valid=True,
        container_valid=True,
        opf_valid=True,
        nav_valid=True,
        total_manifest_items=118,
        total_spine_items=3,
        total_image_assets=113,
    )

    out_dir = tmp_path / "reports"
    json_p, md_p = save_epub_validation_report(report, out_dir)

    assert json_p.exists()
    assert md_p.exists()
    md_text = md_p.read_text(encoding="utf-8")
    assert "Raport Walidacji EPUB 3 (Gate G5 / R19) — PASS" in md_text
    assert "113" in md_text
    assert "PASS" in md_text
