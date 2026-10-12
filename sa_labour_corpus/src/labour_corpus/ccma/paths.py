"""On-disk layout of the CCMA engine, derived from one project root."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Abort a source when free space falls below this (spec: 500 MB).
MIN_FREE_BYTES = 500 * 1024 * 1024

_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_segment(value: str) -> str:
    """A native id made safe to use as one directory name."""
    cleaned = _UNSAFE_RE.sub("_", value).strip("._")
    return cleaned[:120] or "_"


@dataclass(slots=True)
class CcmaPaths:
    """Every location the engine writes to, relative to the project root."""

    root: Path

    @property
    def data(self) -> Path:
        return self.root / "data" / "ccma"

    @property
    def catalogue(self) -> Path:
        return self.data / "catalogue.sqlite"

    @property
    def raw(self) -> Path:
        """Original bytes. Treat as containing personal information."""
        return self.data / "raw"

    @property
    def knowledge(self) -> Path:
        return self.data / "knowledge"

    @property
    def inbox(self) -> Path:
        """Awards the operator already lawfully holds."""
        return self.data / "inbox"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def audit_log(self) -> Path:
        return self.logs / "ccma-audit.jsonl"

    @property
    def event_log(self) -> Path:
        return self.logs / "ccma-events.jsonl"

    def record_dir(self, source: str, native_id: str) -> Path:
        return self.raw / safe_segment(source) / safe_segment(native_id)

    def ensure(self) -> None:
        for path in (self.data, self.raw, self.knowledge, self.reports, self.logs):
            path.mkdir(parents=True, exist_ok=True)
