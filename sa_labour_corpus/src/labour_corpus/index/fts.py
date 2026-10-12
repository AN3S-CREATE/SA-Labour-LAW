"""SQLite FTS5 full-text index over the corpus text.

Search returns only documents that are actually in the corpus. A query with no
hits returns no hits; it never falls back to anything generative and never
produces a case that is not on disk.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..config import CorpusPaths
from ..state import CorpusStore

log = logging.getLogger(__name__)

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
    doc_id UNINDEXED,
    source_id UNINDEXED,
    forum UNINDEXED,
    citation,
    title,
    tags,
    body,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


@dataclass(slots=True)
class SearchHit:
    doc_id: str
    source_id: str
    forum: str | None
    citation: str | None
    title: str | None
    url: str
    snippet: str
    rank: float


class FtsIndex:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(FTS_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def __enter__(self) -> "FtsIndex":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def rebuild(self, paths: CorpusPaths, store: CorpusStore) -> int:
        """Rebuild the index from parsed documents and their text files."""
        self.conn.execute("DELETE FROM docs")
        count = 0

        for row in store.iter_documents():
            doc_id = row["doc_id"]
            text_path = paths.text / f"{doc_id}.txt"
            body = ""
            if text_path.exists():
                body = text_path.read_text(encoding="utf-8", errors="ignore")

            tag_rows = store.conn.execute(
                "SELECT tag FROM tags WHERE doc_id = ?", (doc_id,)
            ).fetchall()
            tag_text = " ".join(r["tag"] for r in tag_rows)

            self.conn.execute(
                """INSERT INTO docs (doc_id, source_id, forum, citation, title, tags, body)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    doc_id,
                    row["source_id"],
                    row["forum"],
                    row["citation"] or row["neutral_citation"] or "",
                    row["title"] or "",
                    tag_text,
                    body,
                ),
            )
            count += 1

        self.conn.execute(
            "INSERT OR REPLACE INTO index_meta (key, value) VALUES ('documents', ?)",
            (str(count),),
        )
        self.conn.commit()

        # Emptying and refilling the table in place leaves FTS5 segments and
        # free pages behind, so the file grows with every rebuild.
        self.compact()
        return count

    def compact(self) -> bool:
        """Merge the FTS5 segments into one and reclaim the free pages.

        Best effort, run once the index is committed and complete: VACUUM
        needs the file to itself and gives way to a reader that holds it.
        Returns whether the index was compacted.
        """
        try:
            self.conn.execute("INSERT INTO docs(docs) VALUES('optimize')")
            self.conn.commit()
            self.conn.execute("VACUUM")
        except sqlite3.OperationalError as exc:
            # A failed optimize leaves its transaction open; the merge is lost,
            # never the committed index.
            self.conn.rollback()
            log.warning("full-text index not compacted: %s", exc)
            return False
        return True

    def search(self, query: str, store: CorpusStore, limit: int = 10) -> list[SearchHit]:
        """Rank documents against an FTS5 query."""
        if not query.strip():
            return []
        try:
            rows = self.conn.execute(
                """
                SELECT doc_id, source_id, forum, citation, title,
                       snippet(docs, 6, '[', ']', ' ... ', 24) AS snip,
                       bm25(docs) AS rank
                FROM docs
                WHERE docs MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # A bare phrase that FTS5 cannot parse is retried as a quoted phrase.
            safe = '"' + query.replace('"', " ") + '"'
            rows = self.conn.execute(
                """
                SELECT doc_id, source_id, forum, citation, title,
                       snippet(docs, 6, '[', ']', ' ... ', 24) AS snip,
                       bm25(docs) AS rank
                FROM docs
                WHERE docs MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (safe, limit),
            ).fetchall()

        hits: list[SearchHit] = []
        for row in rows:
            record = store.conn.execute(
                "SELECT canonical_url FROM documents WHERE doc_id = ?", (row["doc_id"],)
            ).fetchone()
            hits.append(
                SearchHit(
                    doc_id=row["doc_id"],
                    source_id=row["source_id"],
                    forum=row["forum"],
                    citation=row["citation"] or None,
                    title=row["title"] or None,
                    url=record["canonical_url"] if record else "",
                    snippet=(row["snip"] or "").strip(),
                    rank=float(row["rank"]),
                )
            )
        return hits

    def document_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM docs").fetchone()
        return int(row[0]) if row else 0
