"""Structured logging configuration backed by structlog."""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog
from structlog.typing import FilteringBoundLogger, Processor

from meridian.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure structlog once per process.

    Console pretty-print in dev; JSON otherwise so log aggregators parse natively.
    """
    level = getattr(logging, settings.log_level.upper())
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Processor
    if settings.env == "dev":
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    logger = structlog.get_logger() if name is None else structlog.get_logger(name)
    return cast(FilteringBoundLogger, logger)
