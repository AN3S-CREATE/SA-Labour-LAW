"""SQLite corpus store: documents, citations, tags, fetch_log, gaps.

The fetch_log is what makes a run resumable. It records, per URL, the last
status, the body checksum and any validators the server offered. A re-run
issues a conditional GET and skips the write entirely when the bytes are
unchanged, so killing a crawl mid-way and starting again does not re-download
or rewrite unchanged documents.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator

from .models import Gap, ParsedDoc, utc_now_iso

# How long a writer waits for another writer before giving up.
BUSY_TIMEOUT_SECONDS = 30.0

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS documents (
    doc_id             TEXT PRIMARY KEY,
    source_id          TEXT NOT NULL,
    canonical_url      TEXT NOT NULL,
    retrieved_at       TEXT NOT NULL,
    http_status        INTEGER,
    content_sha256     TEXT NOT NULL,
    content_type       TEXT,
    bytes              INTEGER,
    doc_type           TEXT NOT NULL,
    provenance         TEXT NOT NULL DEFAULT 'crawled',
    is_draft           INTEGER NOT NULL DEFAULT 0,
    draft_reason       TEXT,
    forum              TEXT,
    jurisdiction       TEXT,
    title              TEXT,
    citation           TEXT,
    case_no            TEXT,
    neutral_citation   TEXT,
    date_delivered     TEXT,
    year               INTEGER,
    outcome            TEXT,
    remedy             TEXT,
    language           TEXT,
    text_status        TEXT,
    text_chars         INTEGER,
    pi_redacted        INTEGER,
    parse_status       TEXT,
    parser_confidence  REAL,
    license_note       TEXT,
    parties_json       TEXT,
    judges_json        TEXT,
    updated_at         TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_url_sha
    ON documents(canonical_url, content_sha256);
CREATE INDEX IF NOT EXISTS idx_documents_forum_year ON documents(forum, year);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_id);
CREATE INDEX IF NOT EXISTS idx_documents_doctype ON documents(doc_type);
-- link_held_citations matches every known citation against this column; without
-- an index each match is a full scan of documents.
CREATE INDEX IF NOT EXISTS idx_documents_neutral ON documents(neutral_citation);

CREATE TABLE IF NOT EXISTS tags (
    doc_id    TEXT NOT NULL,
    category  TEXT NOT NULL,
    tag       TEXT NOT NULL,
    patterns  TEXT,
    paragraphs TEXT,
    reason    TEXT,
    PRIMARY KEY (doc_id, category, tag),
    FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);

CREATE TABLE IF NOT EXISTS citations (
    doc_id    TEXT NOT NULL,
    kind      TEXT NOT NULL,
    value     TEXT NOT NULL,
    PRIMARY KEY (doc_id, kind, value),
    FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_citations_value ON citations(value);

CREATE TABLE IF NOT EXISTS fetch_log (
    url             TEXT PRIMARY KEY,
    source_id       TEXT,
    last_status     INTEGER,
    content_sha256  TEXT,
    etag            TEXT,
    last_modified   TEXT,
    bytes           INTEGER,
    doc_id          TEXT,
    raw_path        TEXT,
    fetched_at      TEXT,
    attempts        INTEGER DEFAULT 0,
    last_error      TEXT
);
CREATE INDEX IF NOT EXISTS idx_fetch_source ON fetch_log(source_id);

CREATE TABLE IF NOT EXISTS gaps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    url         TEXT,
    detail      TEXT,
    observed_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_gaps_unique
    ON gaps(source_id, kind, IFNULL(url, ''), IFNULL(detail, ''));

-- Judgments known to exist, read from published court indexes. A row here is
-- a REFERENCE, not a held document. `held_doc_id` is set only when the corpus
-- actually contains the judgment, which is what makes coverage measurable.
-- `url_is_derived` marks a URL constructed from the citation rather than
-- observed, so it is never mistaken for a verified location.
CREATE TABLE IF NOT EXISTS known_citations (
    neutral_citation   TEXT PRIMARY KEY,
    court              TEXT NOT NULL,
    year               INTEGER NOT NULL,
    number             INTEGER NOT NULL,
    case_name          TEXT NOT NULL,
    case_no            TEXT,
    date_delivered     TEXT,
    parallel_citations TEXT,
    saflii_url         TEXT,
    url_is_derived     INTEGER NOT NULL DEFAULT 1,
    held_doc_id        TEXT,
    source_note        TEXT,
    imported_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_known_court_year ON known_citations(court, year);
CREATE INDEX IF NOT EXISTS idx_known_held ON known_citations(held_doc_id);
"""


class CorpusStore:
    """Thin, explicit SQLite wrapper. No ORM, no hidden migrations."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=BUSY_TIMEOUT_SECONDS)
        self.conn.row_factory = sqlite3.Row
        # WAL allows concurrent readers alongside one writer, but a second
        # writer still has to wait. Without a busy timeout it fails instantly
        # with "database is locked", which turns a normal overlap -- an index
        # rebuild while a crawl finishes -- into a crash.
        self.conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_SECONDS * 1000)}")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a corpus was first created.

        CREATE TABLE IF NOT EXISTS leaves an existing table untouched, so a
        corpus built by an earlier version needs its new columns added here.
        Additive only: nothing is dropped or rewritten.
        """
        additions: dict[str, list[tuple[str, str]]] = {
            "documents": [
                ("provenance", "TEXT NOT NULL DEFAULT 'crawled'"),
                ("is_draft", "INTEGER NOT NULL DEFAULT 0"),
                ("draft_reason", "TEXT"),
            ],
        }
        for table, columns in additions.items():
            existing = {
                row["name"]
                for row in self.conn.execute(f"PRAGMA table_info({table})")
            }
            if not existing:
                continue
            for name, definition in columns:
                if name not in existing:
                    self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                    )

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def __enter__(self) -> "CorpusStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # -- resume ---------------------------------------------------------
    def get_fetch_record(self, url: str) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM fetch_log WHERE url = ?", (url,))
        return cur.fetchone()

    def is_unchanged(self, url: str, content_sha256: str) -> bool:
        """True when this exact body was already stored for this URL."""
        row = self.get_fetch_record(url)
        return bool(row and row["content_sha256"] == content_sha256)

    def record_fetch(
        self,
        url: str,
        *,
        source_id: str,
        status: int | None,
        content_sha256: str | None,
        etag: str | None = None,
        last_modified: str | None = None,
        bytes_: int | None = None,
        doc_id: str | None = None,
        raw_path: str | None = None,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO fetch_log (
                url, source_id, last_status, content_sha256, etag, last_modified,
                bytes, doc_id, raw_path, fetched_at, attempts, last_error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)
            ON CONFLICT(url) DO UPDATE SET
                source_id      = excluded.source_id,
                last_status    = excluded.last_status,
                content_sha256 = COALESCE(excluded.content_sha256, fetch_log.content_sha256),
                etag           = COALESCE(excluded.etag, fetch_log.etag),
                last_modified  = COALESCE(excluded.last_modified, fetch_log.last_modified),
                bytes          = COALESCE(excluded.bytes, fetch_log.bytes),
                doc_id         = COALESCE(excluded.doc_id, fetch_log.doc_id),
                raw_path       = COALESCE(excluded.raw_path, fetch_log.raw_path),
                fetched_at     = excluded.fetched_at,
                attempts       = fetch_log.attempts + 1,
                last_error     = excluded.last_error
            """,
            (
                url, source_id, status, content_sha256, etag, last_modified,
                bytes_, doc_id, raw_path, utc_now_iso(), error,
            ),
        )
        self.conn.commit()

    # -- documents ------------------------------------------------------
    def upsert_document(self, doc: ParsedDoc) -> bool:
        """Insert or update one parsed document. Returns True when it is new."""
        existing = self.conn.execute(
            "SELECT doc_id FROM documents WHERE doc_id = ?", (doc.doc_id,)
        ).fetchone()

        self.conn.execute(
            """
            INSERT INTO documents (
                doc_id, source_id, canonical_url, retrieved_at, http_status,
                content_sha256, content_type, bytes, doc_type, provenance,
                is_draft, draft_reason, forum,
                jurisdiction, title, citation, case_no, neutral_citation,
                date_delivered, year, outcome, remedy, language, text_status,
                text_chars, pi_redacted, parse_status, parser_confidence,
                license_note, parties_json, judges_json, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(doc_id) DO UPDATE SET
                retrieved_at      = excluded.retrieved_at,
                http_status       = excluded.http_status,
                doc_type          = excluded.doc_type,
                provenance        = excluded.provenance,
                is_draft          = excluded.is_draft,
                draft_reason      = excluded.draft_reason,
                forum             = excluded.forum,
                title             = excluded.title,
                citation          = excluded.citation,
                case_no           = excluded.case_no,
                neutral_citation  = excluded.neutral_citation,
                date_delivered    = excluded.date_delivered,
                year              = excluded.year,
                outcome           = excluded.outcome,
                remedy            = excluded.remedy,
                language          = excluded.language,
                text_status       = excluded.text_status,
                text_chars        = excluded.text_chars,
                pi_redacted       = excluded.pi_redacted,
                parse_status      = excluded.parse_status,
                parser_confidence = excluded.parser_confidence,
                parties_json      = excluded.parties_json,
                judges_json       = excluded.judges_json,
                updated_at        = excluded.updated_at
            """,
            (
                doc.doc_id, doc.source_id, doc.canonical_url, doc.retrieved_at,
                doc.http_status, doc.content_sha256, doc.content_type, doc.bytes,
                _v(doc.doc_type), _v(doc.provenance), int(doc.is_draft),
                doc.draft_reason, doc.forum, doc.jurisdiction, doc.title,
                doc.citation, doc.case_no, doc.neutral_citation, doc.date_delivered,
                doc.year, _v(doc.outcome), _v(doc.remedy), doc.language,
                _v(doc.text_status), doc.text_chars, int(doc.pi_redacted),
                _v(doc.parse_status), doc.parser_confidence, doc.license_note,
                json.dumps(doc.parties), json.dumps(doc.judges_or_commissioner),
                utc_now_iso(),
            ),
        )

        self.conn.execute("DELETE FROM tags WHERE doc_id = ?", (doc.doc_id,))
        for evidence in doc.tag_evidence:
            self.conn.execute(
                """INSERT OR REPLACE INTO tags
                   (doc_id, category, tag, patterns, paragraphs, reason)
                   VALUES (?,?,?,?,?,?)""",
                (
                    doc.doc_id, evidence.category, evidence.tag,
                    json.dumps(evidence.matched_patterns),
                    json.dumps(evidence.paragraphs),
                    evidence.reason,
                ),
            )
        for tag in doc.sector_tags:
            self.conn.execute(
                """INSERT OR IGNORE INTO tags (doc_id, category, tag)
                   VALUES (?,?,?)""",
                (doc.doc_id, "sector", tag),
            )

        self.conn.execute("DELETE FROM citations WHERE doc_id = ?", (doc.doc_id,))
        for kind, value in (
            ("neutral", doc.neutral_citation),
            ("citation", doc.citation),
            ("case_no", doc.case_no),
        ):
            if value:
                self.conn.execute(
                    "INSERT OR IGNORE INTO citations (doc_id, kind, value) VALUES (?,?,?)",
                    (doc.doc_id, kind, value),
                )

        self.conn.commit()
        return existing is None

    def record_gap(self, gap: Gap) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO gaps (source_id, kind, url, detail, observed_at)
               VALUES (?,?,?,?,?)""",
            (gap.source_id, gap.kind, gap.url, gap.detail, gap.observed_at),
        )
        self.conn.commit()

    # -- known citations (the coverage map) ------------------------------
    def upsert_known_citation(
        self,
        *,
        neutral_citation: str,
        court: str,
        year: int,
        number: int,
        case_name: str,
        case_no: str | None,
        date_delivered: str | None,
        parallel_citations: list[str],
        saflii_url: str,
        source_note: str,
    ) -> bool:
        """Record a judgment known to exist. Returns True when it is new."""
        existing = self.conn.execute(
            "SELECT 1 FROM known_citations WHERE neutral_citation = ?",
            (neutral_citation,),
        ).fetchone()
        self.conn.execute(
            """
            INSERT INTO known_citations (
                neutral_citation, court, year, number, case_name, case_no,
                date_delivered, parallel_citations, saflii_url, url_is_derived,
                held_doc_id, source_note, imported_at
            ) VALUES (?,?,?,?,?,?,?,?,?,1,NULL,?,?)
            ON CONFLICT(neutral_citation) DO UPDATE SET
                case_name          = excluded.case_name,
                case_no            = COALESCE(excluded.case_no, known_citations.case_no),
                date_delivered     = COALESCE(excluded.date_delivered, known_citations.date_delivered),
                parallel_citations = excluded.parallel_citations,
                source_note        = excluded.source_note
            """,
            (
                neutral_citation, court, year, number, case_name, case_no,
                date_delivered, json.dumps(parallel_citations), saflii_url,
                source_note, utc_now_iso(),
            ),
        )
        return existing is None

    def link_held_citations(self) -> int:
        """Mark known citations the corpus actually holds. Returns the count."""
        self.conn.execute("UPDATE known_citations SET held_doc_id = NULL")
        self.conn.execute(
            """
            UPDATE known_citations
            SET held_doc_id = (
                SELECT d.doc_id FROM documents d
                WHERE d.neutral_citation = known_citations.neutral_citation
                LIMIT 1
            )
            WHERE EXISTS (
                SELECT 1 FROM documents d
                WHERE d.neutral_citation = known_citations.neutral_citation
            )
            """
        )
        self.conn.commit()
        return self.count_held_citations()

    def count_known_citations(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM known_citations").fetchone()
        return int(row[0]) if row else 0

    def count_held_citations(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM known_citations WHERE held_doc_id IS NOT NULL"
        ).fetchone()
        return int(row[0]) if row else 0

    def known_citation_coverage(self) -> list[sqlite3.Row]:
        """Known vs held, by court and year."""
        return self.conn.execute(
            """
            SELECT court,
                   MIN(year) AS first_year,
                   MAX(year) AS last_year,
                   COUNT(*) AS known,
                   SUM(CASE WHEN held_doc_id IS NOT NULL THEN 1 ELSE 0 END) AS held
            FROM known_citations
            GROUP BY court ORDER BY known DESC
            """
        ).fetchall()

    # -- reporting ------------------------------------------------------
    def count_documents(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])

    def counts_by_forum_year(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT IFNULL(forum, 'unknown') AS forum,
                      IFNULL(year, 0) AS year,
                      COUNT(*) AS n
               FROM documents GROUP BY forum, year ORDER BY forum, year"""
        ).fetchall()

    def counts_by_doc_type(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT doc_type, COUNT(*) AS n FROM documents
               GROUP BY doc_type ORDER BY n DESC"""
        ).fetchall()

    def last_document_write(self) -> str | None:
        """Timestamp of the most recent document upsert, or None when empty."""
        row = self.conn.execute("SELECT MAX(updated_at) FROM documents").fetchone()
        return row[0] if row else None

    def count_drafts(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM documents WHERE is_draft = 1"
        ).fetchone()
        return int(row[0]) if row else 0

    def draft_documents(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT title, doc_type, source_id, draft_reason FROM documents
               WHERE is_draft = 1 ORDER BY doc_type, title"""
        ).fetchall()

    def counts_by_provenance(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT IFNULL(provenance, 'crawled') AS provenance, COUNT(*) AS n
               FROM documents GROUP BY provenance ORDER BY n DESC"""
        ).fetchall()

    def counts_by_text_status(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT IFNULL(text_status, 'unknown') AS text_status, COUNT(*) AS n
               FROM documents GROUP BY text_status ORDER BY n DESC"""
        ).fetchall()

    def counts_by_source(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT source_id, COUNT(*) AS n FROM documents
               GROUP BY source_id ORDER BY n DESC"""
        ).fetchall()

    def all_gaps(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM gaps ORDER BY source_id, kind"
        ).fetchall()

    def top_tags(self, limit: int = 40) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT category, tag, COUNT(*) AS n FROM tags
               GROUP BY category, tag ORDER BY n DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    def iter_documents(self) -> Iterator[sqlite3.Row]:
        yield from self.conn.execute("SELECT * FROM documents ORDER BY doc_id")


def _v(value: object) -> str | None:
    """Enum-or-string to its stored string value."""
    if value is None:
        return None
    stored = getattr(value, "value", value)
    return stored if isinstance(stored, str) else str(stored)
