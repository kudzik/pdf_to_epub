"""Tests for the pdf-to-markdown plugin and its conversion engine."""

import json
from pathlib import Path
import re
import pytest

PLUGIN_DIR = Path.home() / ".gemini" / "config" / "plugins" / "pdf-to-markdown"
SCRIPT_PATH = PLUGIN_DIR / "skills" / "pdf-to-markdown" / "scripts" / "convert_pdf.py"


def test_plugin_manifest_structure():
    """Verify plugin.json follows Antigravity best practices."""
    manifest_path = PLUGIN_DIR / "plugin.json"
    assert manifest_path.exists(), "plugin.json must exist"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.get("name") == "pdf-to-markdown"
    assert "description" in manifest and len(manifest["description"]) > 10
    assert "suggestedPrompts" in manifest
    assert len(manifest["suggestedPrompts"]) == 3, "Exactly 3 suggested prompts recommended"
    assert "version" in manifest


def test_plugin_skill_frontmatter():
    """Verify SKILL.md has valid YAML frontmatter for progressive disclosure."""
    skill_path = PLUGIN_DIR / "skills" / "pdf-to-markdown" / "SKILL.md"
    assert skill_path.exists(), "SKILL.md must exist"

    content = skill_path.read_text(encoding="utf-8")
    assert content.startswith("---"), "SKILL.md must start with YAML frontmatter"
    assert "name: pdf-to-markdown" in content
    assert "description:" in content


def test_plugin_rules_integrity():
    """Verify rules/AGENTS.md enforces relative assets and no base64."""
    rule_path = PLUGIN_DIR / "rules" / "AGENTS.md"
    assert rule_path.exists(), "rules/AGENTS.md must exist"

    content = rule_path.read_text(encoding="utf-8")
    assert "assets/" in content
    assert "base64" in content


def test_markdown_cleaner_functions():
    """Test text cleaning logic: de-hyphenation, fraction normalization, noise removal."""
    import sys
    sys.path.insert(0, str(SCRIPT_PATH.parent))
    from convert_pdf import clean_markdown_text

    raw_text = "Przepis: ½ łyżeczki soli oraz roz-\nwiązanie zagadki. ■ ^p- scanner artifact."
    cleaned = clean_markdown_text(raw_text)

    # Fractions normalized
    assert "1/2 łyżeczki" in cleaned
    assert "½" not in cleaned

    # De-hyphenated
    assert "rozwiązanie" in cleaned
    assert "roz-\nwiązanie" not in cleaned

    # Artifact cleaned
    assert "■ ^p-" not in cleaned


def test_end_to_end_conversion_invariants(tmp_path):
    """Test running the converter on real PDF pages and assert quality invariants."""
    import sys
    sys.path.insert(0, str(SCRIPT_PATH.parent))
    from convert_pdf import evaluate_pdf_text_layer, fallback_convert_pypdfium

    sample_pdf = Path("input/Polish Heritage Cookery Robert i Maria Strybel.pdf")
    if not sample_pdf.exists():
        pytest.skip("Sample PDF not found in input/")

    # 1. Text layer evaluation
    text_info = evaluate_pdf_text_layer(sample_pdf, max_pages=3)
    assert text_info["has_text"] is True
    assert text_info["total_pages"] > 0

    # 2. Conversion with image extraction
    out_md = tmp_path / "sample.md"
    images = fallback_convert_pypdfium(
        pdf_path=sample_pdf,
        output_md=out_md,
        assets_dir_name="assets",
        page_range=(1, 2),
    )

    assert out_md.exists()
    assert out_md.stat().st_size > 0
    assert len(images) > 0

    md_text = out_md.read_text(encoding="utf-8")

    # Invariant: No base64 images
    assert "data:image/" not in md_text, "Markdown must never contain base64 image blobs"

    # Invariant: All image links must be relative to assets/
    for img in images:
        assert img.exists()
        assert f"assets/{img.name}" in md_text

