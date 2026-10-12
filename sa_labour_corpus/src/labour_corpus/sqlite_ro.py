"""Read-only SQLite connections for the layers that must never write.

Standard library only, so the stdlib MCP query layer can import it. The URI is
built with ``Path.as_uri()``, which percent-encodes the path: a hand-built
``file:{path}?mode=ro`` lets a ``#`` in a folder name end the path early and
drop ``mode=ro``, which opens (and creates) the wrong file read-write.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def readonly_uri(path: str | Path) -> str:
    """The ``file:`` URI that opens *path* read-only."""
    return Path(path).resolve().as_uri() + "?mode=ro"


def connect_readonly(path: str | Path, **kwargs: Any) -> sqlite3.Connection:
    """Open an existing database read-only; any write raises OperationalError."""
    return sqlite3.connect(readonly_uri(path), uri=True, **kwargs)
