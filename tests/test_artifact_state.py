import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.artifact_state import atomic_json, sha256
from src.pipeline import extract_pdf, compile_epub


def test_sample_keeps_canonical_text_and_records_scope(tmp_path, monkeypatch):
    pdf = tmp_path / 'book.pdf'
    pdf.write_bytes(b'pdf fixture')
    out = tmp_path / 'output'
    canonical = out / 'markdown' / 'book.md'
    canonical.parent.mkdir(parents=True)
    canonical.write_text('Corrected canonical text')

    class Document:
        def save_as_markdown(self, target, **kwargs):
            Path(target).write_text('# Sample\n')

    monkeypatch.setattr('src.pipeline.get_converter', lambda **kwargs: SimpleNamespace(convert=lambda *a, **kw: SimpleNamespace(document=Document())))
    sample = extract_pdf(pdf, output_dir=out, pages='1-2', ocr=False)
    assert sample != canonical
    assert canonical.read_text() == 'Corrected canonical text'
    manifest = json.loads((sample.parent.parent / 'extraction_book.json').read_text())
    assert manifest['scope'] == 'sample'
    assert manifest['pages'] == '1-2'
    assert manifest['markdown_sha256'] == sha256(sample)


def test_failed_validation_preserves_existing_epub(tmp_path, monkeypatch):
    md = tmp_path / 'markdown' / 'book.md'
    md.parent.mkdir()
    md.write_text('# Chapter\n\nText.')
    epub = tmp_path / 'epub' / 'book.epub'
    epub.parent.mkdir()
    epub.write_bytes(b'previous publication')

    def fake_pandoc(args, **kwargs):
        Path(args[args.index('-o') + 1]).write_bytes(b'invalid new epub')

    monkeypatch.setattr('src.pipeline.subprocess.run', fake_pandoc)
    with pytest.raises(RuntimeError, match='walidacji'):
        compile_epub(md, tmp_path, metadata_file=None, css_file=None, copy_to_windows=False)
    assert epub.read_bytes() == b'previous publication'
    assert not list(epub.parent.glob('.*.epub'))


def test_build_records_validated_revision(tmp_path):
    if not shutil.which('pandoc'):
        pytest.skip('pandoc unavailable')
    md = tmp_path / 'markdown' / 'book.md'
    md.parent.mkdir()
    md.write_text('# Chapter\n\nA complete fixture paragraph.')
    epub = compile_epub(md, tmp_path, metadata_file=None, css_file=None, copy_to_windows=False)
    manifest = json.loads((tmp_path / 'build_book.json').read_text())
    assert manifest['validated'] is True
    assert manifest['dependencies'][str(md.resolve())] == sha256(md)
    assert manifest['epub_sha256'] == sha256(epub)


def test_atomic_json_has_no_temporary_residue(tmp_path):
    dest = tmp_path / 'state.json'
    atomic_json(dest, {'complete': True})
    assert json.loads(dest.read_text()) == {'complete': True}
    assert not list(tmp_path.glob('*.tmp'))
