"""Structured logging setup.

Call setup_logging() once at pipeline startup. All modules use standard
logging.getLogger(__name__) — no direct print() calls in library code.
"""
import logging
import sys

from config.settings import settings


def setup_logging() -> None:
    """Configure root logger with a structured format."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logging.basicConfig(level=level, handlers=[handler], force=True)
