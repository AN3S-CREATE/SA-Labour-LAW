"""Append-only JSONL run and error logs.

One event per fetch / parse / index action, written as it happens so that a
killed run still leaves an accurate record of what it did.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import orjson

from .models import utc_now_iso


class JsonlLogger:
    """Thread-safe append-only JSONL writer."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, **event: Any) -> None:
        event.setdefault("ts", utc_now_iso())
        line = orjson.dumps(event, option=orjson.OPT_APPEND_NEWLINE)
        with self._lock:
            with self.path.open("ab") as handle:
                handle.write(line)


class RunLog:
    """Bundles the RUN and ERRORS logs plus in-memory run counters."""

    def __init__(self, run_path: Path, error_path: Path) -> None:
        self.run = JsonlLogger(run_path)
        self.errors = JsonlLogger(error_path)
        self.counters: dict[str, int] = {
            "fetched": 0,
            "skipped_duplicate": 0,
            "failed": 0,
            "parsed": 0,
            "parse_failed": 0,
            "indexed": 0,
            "gaps": 0,
            "robots_skipped": 0,
        }

    def bump(self, key: str, amount: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + amount

    def event(self, stage: str, **fields: Any) -> None:
        self.run.write(stage=stage, **fields)

    def error(self, stage: str, error: str, **fields: Any) -> None:
        self.bump("failed" if stage == "fetch" else "parse_failed")
        self.errors.write(stage=stage, error=error, **fields)
        self.run.write(stage=stage, status="error", error=error, **fields)

    def summary(self) -> dict[str, int]:
        return dict(self.counters)
