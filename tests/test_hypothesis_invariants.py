"""Property-based testing (Hypothesis) oraz weryfikacja logowania (Loguru)."""

from pathlib import Path
from hypothesis import given, strategies as st, settings, HealthCheck
from src.cleaner import clean_markdown_text
from src.logger import setup_logger, app_logger


@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(st.text())
def test_clean_markdown_never_crashes(text: str):
    """Niezmiennik odporności: czyszczenie nigdy nie rzuca nieobsłużonego wyjątku na dowolnym tekście."""
    result = clean_markdown_text(text)
    assert isinstance(result, str)


@settings(max_examples=50)
@given(st.text(alphabet=st.characters(blacklist_categories=["Cs"])))
def test_clean_markdown_is_idempotent(text: str):
    """Niezmiennik idempotencji: podwójne czyszczenie daje dokładnie ten sam wynik."""
    cleaned_once = clean_markdown_text(text)
    cleaned_twice = clean_markdown_text(cleaned_once)
    assert cleaned_once == cleaned_twice


@settings(max_examples=50)
@given(
    num=st.integers(min_value=1, max_value=9999).filter(lambda n: n not in {12, 14, 34, 114}),
    unit=st.sampled_from(["g", "kg", "ml", "tsp", "tbsp", "cups", "min", "°C", "°F"]),
)
def test_clean_markdown_preserves_valid_quantities(num: int, unit: str):
    """Niezmiennik miar: czyszczenie nie usuwa poprawnych liczb i jednostek kulinarnych (poza znanymi artefaktami OCR ułamków)."""
    sample = f"Add {num} {unit} of sugar."
    cleaned = clean_markdown_text(sample)
    assert str(num) in cleaned
    assert unit in cleaned


def test_logger_file_sinks(tmp_path: Path):
    """Weryfikacja konfiguracji Loguru: tworzenie plików i rejestracja wpisów."""
    log = setup_logger(output_dir=tmp_path, force_reinit=True)
    log.info("Test loguru info message")
    log.error("Test loguru error message")
    log.complete()

    logs_dir = tmp_path / "logs"
    assert logs_dir.exists(), "Katalog logs powinien zostać utworzony"

    pipeline_log = logs_dir / "pipeline.log"
    assert pipeline_log.exists(), "Plik pipeline.log powinien istnieć"
    pipeline_content = pipeline_log.read_text(encoding="utf-8")
    assert "Test loguru info message" in pipeline_content
    assert "Test loguru error message" in pipeline_content

    error_log = logs_dir / "errors.log"
    assert error_log.exists(), "Plik errors.log powinien istnieć"
    error_content = error_log.read_text(encoding="utf-8")
    assert "Test loguru error message" in error_content
    assert "Test loguru info message" not in error_content
