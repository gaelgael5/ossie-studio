from __future__ import annotations

import logging
import sys

import structlog


def setup_logging() -> None:
    """Configure structlog : JSON hors terminal, rendu lisible en console."""
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
    renderer: structlog.typing.Processor
    if sys.stderr.isatty():
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
