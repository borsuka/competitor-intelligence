"""Structured logging.

One configuration for both the API and the workers.  Every log line is a JSON object in
production and a readable line in development, and every line inherits whatever context
has been bound for the current request or task (``request_id``, ``organization_id``,
``competitor_id``, ...).

Secrets never reach the log: :func:`redact_sensitive` scrubs known-dangerous keys before
rendering, so an accidental ``log.info("login", **payload)`` cannot leak a password.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from app.core.config import get_settings

# Substrings that mark a value as sensitive.  Matching is on the key, lowercased.
SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "pepper",
    "credential",
    "csrf",
)

REDACTED = "[redacted]"

# Keys that contain a sensitive substring but are not sensitive.  Token *counts* are the
# main cost signal in this system; redacting them would defeat the purpose of logging them.
SAFE_KEYS = frozenset(
    {"tokens_in", "tokens_out", "total_tokens", "ai_tokens_in", "ai_tokens_out", "token_count"}
)


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    if lowered in SAFE_KEYS:
        return False
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact_sensitive(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor that replaces sensitive values, recursively."""

    def scrub(value: Any, depth: int = 0) -> Any:
        if depth > 4:  # guard against cyclic / pathological structures
            return value
        if isinstance(value, dict):
            return {
                k: (REDACTED if _is_sensitive(str(k)) else scrub(v, depth + 1))
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [scrub(item, depth + 1) for item in value]
        return value

    for key in list(event_dict.keys()):
        if _is_sensitive(str(key)):
            event_dict[key] = REDACTED
        else:
            event_dict[key] = scrub(event_dict[key])
    return event_dict


def configure_logging() -> None:
    """Idempotently configure structlog + stdlib logging for this process."""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # structlog's own processors, not the stdlib ones: the logger factory below is
    # PrintLogger, which has no .name attribute for stdlib's add_logger_name to read.
    # The module name is bound explicitly in get_logger() instead.
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        redact_sensitive,
    ]

    if settings.log_format == "json":
        renderer: Any = structlog.processors.JSONRenderer()
        shared_processors.append(structlog.processors.format_exc_info)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)
        shared_processors.append(structlog.processors.format_exc_info)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route stdlib loggers (uvicorn, sqlalchemy, celery) through the same handler so
    # output stays uniform and parseable.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))


def get_logger(name: str | None = None):
    """Return a bound logger tagged with the calling module."""
    logger = structlog.get_logger()
    return logger.bind(logger=name) if name else logger


def bind_context(**kwargs: Any) -> None:
    """Bind values onto every subsequent log line in this task/request."""
    structlog.contextvars.bind_contextvars(**{k: v for k, v in kwargs.items() if v is not None})


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


__all__ = [
    "bind_context",
    "clear_context",
    "configure_logging",
    "get_logger",
    "redact_sensitive",
]
