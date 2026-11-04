"""Strukturyzowany system logowania i raportowania błędów (Loguru + Sentry SDK).

Zapewnia:
- Rotację i retencję logów w output/logs/
- Śledzenie błędów z pełnym stacktrace i stanem zmiennych (backtrace=True)
- Opcjonalne raportowanie do Sentry (jeśli zdefiniowano zmienną SENTRY_DSN)
"""

import os
import sys
from pathlib import Path
from typing import Optional
from loguru import logger

# Flaga zapobiegająca wielokrotnej konfiguracji sinków
_INITIALIZED = False


def setup_logger(
    output_dir: Path = Path("output"),
    sentry_dsn: Optional[str] = None,
    force_reinit: bool = False,
) -> logger:
    """Konfiguruje ustrukturyzowane logowanie Loguru oraz opcjonalne SDK Sentry."""
    global _INITIALIZED
    if _INITIALIZED and not force_reinit:
        return logger

    # Usunięcie domyślnego handlera Loguru
    logger.remove()

    # 1. Standardowe wyjście konsoli (zwięzłe, czytelne dla użytkownika)
    log_format_console = (
        "<green>{time:HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )
    logger.add(
        sys.stderr,
        format=log_format_console,
        level="INFO",
        colorize=True,
        enqueue=True,
    )

    # 2. Katalog logów w output/
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # 3. Zapis wszystkich zdarzeń (DEBUG+) z rotacją 10 MB i retencją 7 dni
    logger.add(
        logs_dir / "pipeline.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message} | {extra}",
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
    )

    # 4. Dedykowany plik błędów (ERROR / CRITICAL) z pełnym zrzutem śladu stosu i diagnostyką zmiennych
    logger.add(
        logs_dir / "errors.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}\n{exception}",
        level="ERROR",
        backtrace=True,
        diagnose=True,
        rotation="10 MB",
        retention="14 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
    )

    # 5. Opcjonalna integracja z Sentry (jeśli podano DSN lub jest w zmiennych środowiska)
    dsn = sentry_dsn or os.environ.get("SENTRY_DSN")
    if dsn:
        try:
            import sentry_sdk
            sentry_sdk.init(
                dsn=dsn,
                traces_sample_rate=1.0,
                profiles_sample_rate=1.0,
                environment=os.environ.get("ENVIRONMENT", "development"),
                release=f"pdf-to-epub@{os.environ.get('APP_VERSION', '0.1.0')}",
            )
            logger.info("Zainicjalizowano Sentry SDK do raportowania zdarzeń i błędów.")
        except Exception as e:
            logger.warning(f"Nie udało się zainicjalizować Sentry SDK: {e}")

    _INITIALIZED = True
    return logger


# Domyślna instancja gotowa do importu
app_logger = logger
