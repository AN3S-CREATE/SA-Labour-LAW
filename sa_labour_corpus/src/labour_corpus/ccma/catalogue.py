"""The CCMA catalogue: sources, records, knowledge units, runs and errors.

Standard library only (sqlite3 + FTS5), so the read side can be imported by the
MCP server without pulling in the harvester's dependencies.

Identity is ``(source, native_id)``. Seeding the same artefact twice changes
nothing: an unchanged checksum with an unchanged parse result is a skip, and
knowledge units carry deterministic ids derived from their content.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections.abc import Iterator

from ..sqlite_ro import connect_readonly

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sources (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    base_url            TEXT,
    robots_ok           INTEGER,
    tos_notes           TEXT,
    enabled             INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL,
    operator_confirmed  INTEGER NOT NULL DEFAULT 0,
    record_kind         TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discovered (
    source          TEXT NOT NULL,
    native_id       TEXT NOT NULL,
    url             TEXT NOT NULL,
    title           TEXT,
    year            INTEGER,
    record_kind     TEXT NOT NULL,
    first_seen_run  TEXT NOT NULL,
    last_seen_run   TEXT NOT NULL,
    PRIMARY KEY (source, native_id)
);

CREATE TABLE IF NOT EXISTS records (
    source          TEXT NOT NULL,
    native_id       TEXT NOT NULL,
    record_kind     TEXT NOT NULL,
    case_number     TEXT,
    neutral_citation TEXT,
    title           TEXT,
    forum           TEXT,
    year            INTEGER,
    date_delivered  TEXT,
    dispute_type    TEXT,
    outcome_label   TEXT,
    source_url      TEXT,
    local_path      TEXT,
    sha256          TEXT,
    content_type    TEXT,
    retrieved_at    TEXT,
    provenance      TEXT NOT NULL,
    parse_status    TEXT NOT NULL,
    parse_notes     TEXT,
    tags_json       TEXT,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (source, native_id)
);
CREATE INDEX IF NOT EXISTS idx_records_case ON records(case_number);
CREATE INDEX IF NOT EXISTS idx_records_dispute ON records(dispute_type, year);

CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
    source UNINDEXED, native_id UNINDEXED, title, case_number, body,
    tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS knowledge_units (
    id              TEXT PRIMARY KEY,
    record_fk       TEXT,
    kind            TEXT NOT NULL CHECK (kind IN ('case_card', 'scenario', 'strategy')),
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    citations_json  TEXT NOT NULL,
    confidence      TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    dispute_type    TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ku_record ON knowledge_units(record_fk, kind);
CREATE INDEX IF NOT EXISTS idx_ku_dispute ON knowledge_units(kind, dispute_type);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    id UNINDEXED, kind UNINDEXED, title, body,
    tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS run_log (
    run_id              TEXT PRIMARY KEY,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    mode                TEXT NOT NULL,
    found               INTEGER NOT NULL DEFAULT 0,
    downloaded          INTEGER NOT NULL DEFAULT 0,
    seeded              INTEGER NOT NULL DEFAULT 0,
    failed              INTEGER NOT NULL DEFAULT 0,
    skipped             INTEGER NOT NULL DEFAULT 0,
    completeness_claim  INTEGER NOT NULL DEFAULT 0 CHECK (completeness_claim = 0),
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    source      TEXT NOT NULL,
    native_id   TEXT,
    class       TEXT NOT NULL,
    http_status INTEGER,
    message     TEXT,
    retryable   INTEGER NOT NULL,
    resolved    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_errors_run ON errors(run_id, class);

CREATE TABLE IF NOT EXISTS checkpoints (
    run_id      TEXT NOT NULL,
    source      TEXT NOT NULL,
    position    INTEGER NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (run_id, source)
);
"""

KNOWLEDGE_KINDS = ("case_card", "scenario", "strategy")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_key(source: str, native_id: str) -> str:
    return f"{source}:{native_id}"


@dataclass(slots=True)
class RecordRow:
    source: str
    native_id: str
    record_kind: str
    provenance: str
    parse_status: str
    case_number: str | None = None
    neutral_citation: str | None = None
    title: str | None = None
    forum: str | None = None
    year: int | None = None
    date_delivered: str | None = None
    dispute_type: str | None = None
    outcome_label: str | None = None
    source_url: str | None = None
    local_path: str | None = None
    sha256: str | None = None
    content_type: str | None = None
    retrieved_at: str | None = None
    parse_notes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


# The stored record fields that come from the bytes and their parse, plus
# provenance (a claim served with the record). A difference in any of them
# means the stored row is stale.
_COMPARED_FIELDS = (
    "sha256", "parse_status", "record_kind", "case_number", "neutral_citation", "title",
    "forum", "year", "date_delivered", "dispute_type", "outcome_label", "provenance",
)


def _unchanged(existing: sqlite3.Row, row: RecordRow) -> bool:
    """Whether a stored record already says everything ``row`` would write."""
    return (
        all(existing[name] == getattr(row, name) for name in _COMPARED_FIELDS)
        and existing["parse_notes"] == json.dumps(row.parse_notes)
        and existing["tags_json"] == json.dumps(row.tags)
    )


@dataclass(slots=True)
class KnowledgeUnit:
    id: str
    kind: str
    title: str
    body: dict[str, Any]
    citations: list[dict[str, Any]]
    confidence: str
    record_fk: str | None = None
    dispute_type: str | None = None


class Catalogue:
    """Read/write access to the catalogue. One instance per thread."""

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        if read_only:
            self.conn = connect_readonly(self.path, timeout=30)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.path, timeout=30)
            self.conn.executescript(SCHEMA)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Catalogue":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    # -- sources ---------------------------------------------------------
    def sync_sources(self, sources: list[Any]) -> None:
        with self.tx() as c:
            for s in sources:
                c.execute(
                    """INSERT INTO sources(id, name, base_url, robots_ok, tos_notes, enabled,
                           status, operator_confirmed, record_kind, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                           base_url=excluded.base_url, robots_ok=excluded.robots_ok,
                           tos_notes=excluded.tos_notes, enabled=excluded.enabled,
                           status=excluded.status,
                           operator_confirmed=excluded.operator_confirmed,
                           record_kind=excluded.record_kind, updated_at=excluded.updated_at""",
                    (
                        s.id, s.name, s.base_url,
                        None if s.robots_ok is None else int(bool(s.robots_ok)),
                        s.tos_notes, int(s.enabled), s.status.value,
                        int(s.operator_confirmed), s.record_kind.value, now_iso(),
                    ),
                )

    # -- discovery -------------------------------------------------------
    def upsert_discovered(
        self, run_id: str, source: str, native_id: str, url: str,
        title: str | None, year: int | None, record_kind: str,
    ) -> bool:
        """Record a found URL. Returns True when it was not seen before."""
        with self.tx() as c:
            existing = c.execute(
                "SELECT 1 FROM discovered WHERE source=? AND native_id=?", (source, native_id)
            ).fetchone()
            c.execute(
                """INSERT INTO discovered(source, native_id, url, title, year, record_kind,
                       first_seen_run, last_seen_run) VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(source, native_id) DO UPDATE SET url=excluded.url,
                       title=COALESCE(excluded.title, discovered.title),
                       year=COALESCE(excluded.year, discovered.year),
                       last_seen_run=excluded.last_seen_run""",
                (source, native_id, url, title, year, record_kind, run_id, run_id),
            )
        return existing is None

    def discovered(self, source: str | None = None) -> list[sqlite3.Row]:
        if source:
            return self.conn.execute(
                "SELECT * FROM discovered WHERE source=? ORDER BY year DESC, native_id", (source,)
            ).fetchall()
        return self.conn.execute("SELECT * FROM discovered ORDER BY source, native_id").fetchall()

    # -- records ---------------------------------------------------------
    def get_record(self, source: str, native_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM records WHERE source=? AND native_id=?", (source, native_id)
        ).fetchone()

    def upsert_record(self, row: RecordRow, fts_body: str | None) -> str:
        """Insert or update one record. Returns 'inserted', 'updated' or 'skipped'.

        'skipped' means the same bytes were already seeded with the same parse
        result -- the re-run case, which must not touch the catalogue. Every
        stored field the parse derives is compared, so a corrected extractor
        reaches a record already seeded (and its knowledge units are rebuilt).
        Fetch bookkeeping (retrieved_at, local_path, source_url) is not
        compared: an unchanged re-fetch stays a skip.
        """
        existing = self.get_record(row.source, row.native_id)
        if existing is not None and _unchanged(existing, row):
            return "skipped"
        with self.tx() as c:
            c.execute(
                """INSERT INTO records(source, native_id, record_kind, case_number,
                       neutral_citation, title, forum, year, date_delivered, dispute_type,
                       outcome_label, source_url, local_path, sha256, content_type,
                       retrieved_at, provenance, parse_status, parse_notes, tags_json, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source, native_id) DO UPDATE SET
                       record_kind=excluded.record_kind, case_number=excluded.case_number,
                       neutral_citation=excluded.neutral_citation, title=excluded.title,
                       forum=excluded.forum, year=excluded.year,
                       date_delivered=excluded.date_delivered,
                       dispute_type=excluded.dispute_type, outcome_label=excluded.outcome_label,
                       source_url=excluded.source_url, local_path=excluded.local_path,
                       sha256=excluded.sha256, content_type=excluded.content_type,
                       retrieved_at=excluded.retrieved_at, provenance=excluded.provenance,
                       parse_status=excluded.parse_status, parse_notes=excluded.parse_notes,
                       tags_json=excluded.tags_json, updated_at=excluded.updated_at""",
                (
                    row.source, row.native_id, row.record_kind, row.case_number,
                    row.neutral_citation, row.title, row.forum, row.year, row.date_delivered,
                    row.dispute_type, row.outcome_label, row.source_url, row.local_path,
                    row.sha256, row.content_type, row.retrieved_at, row.provenance,
                    row.parse_status, json.dumps(row.parse_notes), json.dumps(row.tags), now_iso(),
                ),
            )
            c.execute(
                "DELETE FROM records_fts WHERE source=? AND native_id=?",
                (row.source, row.native_id),
            )
            if fts_body is not None:
                c.execute(
                    "INSERT INTO records_fts(source, native_id, title, case_number, body) "
                    "VALUES (?,?,?,?,?)",
                    (row.source, row.native_id, row.title or "", row.case_number or "", fts_body),
                )
        return "inserted" if existing is None else "updated"

    def count(self, table: str) -> int:
        if table not in {"records", "knowledge_units", "discovered", "errors", "run_log", "sources"}:
            raise ValueError(table)
        return int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def parsed_records(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM records WHERE parse_status='parsed' ORDER BY source, native_id"
        ).fetchall()

    # -- knowledge -------------------------------------------------------
    def replace_knowledge_for(self, record_fk: str, units: list[KnowledgeUnit]) -> int:
        """Swap one record's case card and strategies for a fresh set.

        Ids are content-derived, so an unchanged record produces identical ids
        and the swap is a no-op in effect.
        """
        with self.tx() as c:
            old = [r[0] for r in c.execute(
                "SELECT id FROM knowledge_units WHERE record_fk=?", (record_fk,)
            )]
            for uid in old:
                c.execute("DELETE FROM knowledge_fts WHERE id=?", (uid,))
            c.execute("DELETE FROM knowledge_units WHERE record_fk=?", (record_fk,))
            for unit in units:
                self._insert_unit(c, unit)
        return len(units)

    def replace_scenarios(self, units: list[KnowledgeUnit]) -> int:
        """Scenarios span records, so they are rebuilt as a set."""
        with self.tx() as c:
            old = [r[0] for r in c.execute(
                "SELECT id FROM knowledge_units WHERE record_fk IS NULL"
            )]
            for uid in old:
                c.execute("DELETE FROM knowledge_fts WHERE id=?", (uid,))
            c.execute("DELETE FROM knowledge_units WHERE record_fk IS NULL")
            for unit in units:
                self._insert_unit(c, unit)
        return len(units)

    def _insert_unit(self, c: sqlite3.Connection, unit: KnowledgeUnit) -> None:
        body_json = json.dumps(unit.body, ensure_ascii=False)
        c.execute(
            """INSERT OR REPLACE INTO knowledge_units(id, record_fk, kind, title, body,
                   citations_json, confidence, dispute_type, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                unit.id, unit.record_fk, unit.kind, unit.title, body_json,
                json.dumps(unit.citations, ensure_ascii=False), unit.confidence,
                unit.dispute_type, now_iso(),
            ),
        )
        c.execute(
            "INSERT INTO knowledge_fts(id, kind, title, body) VALUES (?,?,?,?)",
            (unit.id, unit.kind, unit.title, flatten_body(unit.body)),
        )

    def knowledge(self, kind: str | None = None) -> list[sqlite3.Row]:
        if kind:
            return self.conn.execute(
                "SELECT * FROM knowledge_units WHERE kind=? ORDER BY id", (kind,)
            ).fetchall()
        return self.conn.execute("SELECT * FROM knowledge_units ORDER BY kind, id").fetchall()

    # -- runs, errors, checkpoints ----------------------------------------
    def start_run(self, run_id: str, mode: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO run_log(run_id, started_at, mode) VALUES (?,?,?)",
                (run_id, now_iso(), mode),
            )

    def finish_run(self, run_id: str, counts: dict[str, int], notes: str = "") -> None:
        with self.tx() as c:
            c.execute(
                """UPDATE run_log SET finished_at=?, found=?, downloaded=?, seeded=?,
                       failed=?, skipped=?, notes=? WHERE run_id=?""",
                (
                    now_iso(), counts.get("found", 0), counts.get("downloaded", 0),
                    counts.get("seeded", 0), counts.get("failed", 0),
                    counts.get("skipped", 0), notes, run_id,
                ),
            )

    def runs(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM run_log ORDER BY rowid DESC LIMIT ?", (limit,)
        ).fetchall()

    def add_error(
        self, run_id: str, source: str, native_id: str | None, error_class: str,
        http_status: int | None, message: str, retryable: bool,
    ) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO errors(run_id, source, native_id, class, http_status, message,
                       retryable, created_at) VALUES (?,?,?,?,?,?,?,?)""",
                (run_id, source, native_id, error_class, http_status, message[:2000],
                 int(retryable), now_iso()),
            )

    def errors(self, run_id: str | None = None, unresolved_only: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM errors"
        clauses, params = [], []
        if run_id:
            clauses.append("run_id=?")
            params.append(run_id)
        if unresolved_only:
            clauses.append("resolved=0")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self.conn.execute(sql + " ORDER BY id", params).fetchall()

    def resolve_errors(self, source: str, native_id: str) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE errors SET resolved=1 WHERE source=? AND native_id=? AND resolved=0",
                (source, native_id),
            )

    def checkpoint(self, run_id: str, source: str, position: int) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO checkpoints(run_id, source, position, updated_at) VALUES (?,?,?,?)
                   ON CONFLICT(run_id, source) DO UPDATE SET position=excluded.position,
                       updated_at=excluded.updated_at""",
                (run_id, source, position, now_iso()),
            )


def flatten_body(body: Any) -> str:
    """All string leaves of a knowledge body, for full-text indexing."""
    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(body)
    return "\n".join(parts)
