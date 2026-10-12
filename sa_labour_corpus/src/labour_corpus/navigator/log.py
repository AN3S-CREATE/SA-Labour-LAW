"""Structured JSON logging for the navigator.

One JSON object per line on stderr, so stdout stays clean for the assessment
itself. Fields passed through ``extra=`` become top-level keys. Case facts are
never logged: only routing outcomes and counts.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO

import orjson

from ..models import utc_now_iso

# Attributes every LogRecord has; anything else came from ``extra=``.
_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = {
            "ts": utc_now_iso(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        event.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            event["exc"] = self.formatException(record.exc_info)
        return orjson.dumps(event, default=str).decode()


def configure(level: str = "WARNING", stream: TextIO | None = None) -> logging.Logger:
    """Attach one JSON handler to the navigator's logger; idempotent."""
    logger = logging.getLogger("labour_corpus.navigator")
    for handler in list(logger.handlers):
        if getattr(handler, "_navigator", False):
            logger.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler._navigator = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    return logger
