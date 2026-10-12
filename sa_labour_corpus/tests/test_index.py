"""Full-text index maintenance.

A rebuild empties and refills the FTS5 table in place. Without compaction the
file keeps every superseded segment and free page, so it grows with each run.
The guarantees under test are that a rebuild leaves one segment and no free
pages, that compaction never changes what a query returns compared with the
same index uncompacted, and that a refused VACUUM or a failed optimize never
fails a rebuild whose index is already committed.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from labour_corpus.config import CorpusPaths
from labour_corpus.http import FetchResult
from labour_corpus.index.fts import FtsIndex
from labour_corpus.models import DocType
from labour_corpus.pipeline import build_parsed_doc, persist
from labour_corpus.state import CorpusStore

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://example.org/j/1"
DOCUMENTS = {
    URL: ("zalcjhb_mining_dismissal.html", DocType.JUDGMENT),
    "https://example.org/act/lra": ("lra_consolidated_extract.html", DocType.STATUTE),
}
OPTIMIZE = "INSERT INTO docs(docs) VALUES('optimize')"


@pytest.fixture()
def corpus(tmp_path):
    paths = CorpusPaths(root=tmp_path / "corpus")
    paths.ensure()
    store = CorpusStore(paths.corpus_db)
    for url, (name, doc_type) in DOCUMENTS.items():
        body = (FIXTURES / name).read_bytes()
        result = FetchResult(
            url=url, final_url=url,
            status=200, headers={"content-type": "text/html"}, body=body,
            content_sha256=hashlib.sha256(body).hexdigest(), bytes=len(body), elapsed_ms=1,
        )
        doc, text = build_parsed_doc(
            source_id="s", forum="ZALCJHB", result=result,
            default_doc_type=doc_type, license_note="",
        )
        persist(paths, store, doc, text)
    yield paths, store
    store.close()


def _segments(fts: FtsIndex) -> int:
    return int(fts.conn.execute("SELECT count(DISTINCT segid) FROM docs_idx").fetchone()[0])


def _free_pages(fts: FtsIndex) -> int:
    return int(fts.conn.execute("PRAGMA freelist_count").fetchone()[0])


def _answers(fts: FtsIndex, store: CorpusStore) -> dict[str, list[tuple]]:
    queries = ('"zero tolerance policy"', "safety rule mine", "dismissal",
               "unfair OR operational", "zzzznonexistentterm")
    return {
        query: [(h.doc_id, h.url, h.snippet, h.rank) for h in fts.search(query, store)]
        for query in queries
    }


def test_repeated_rebuilds_leave_one_segment_and_no_free_pages(corpus):
    paths, store = corpus
    with FtsIndex(paths.fts_db) as fts:
        assert fts.rebuild(paths, store) == 2
        assert fts.rebuild(paths, store) == 2
        assert _segments(fts) == 1
        assert _free_pages(fts) == 0
        assert fts.search("safety rule mine", store)[0].url == URL


def test_compaction_never_changes_the_answers(corpus, monkeypatch):
    paths, store = corpus
    with FtsIndex(paths.fts_db) as fts:
        with monkeypatch.context() as patched:
            patched.setattr(fts, "compact", lambda: True)
            fts.rebuild(paths, store)
            fts.rebuild(paths, store)
        # The baseline really is uncompacted: it holds superseded segments.
        assert _segments(fts) > 1
        before = _answers(fts, store)
        assert before['"zero tolerance policy"'], "the fixture contains this phrase"
        assert len(before["unfair OR operational"]) == 2, "both documents rank"
        assert before["zzzznonexistentterm"] == []

        assert fts.compact() is True
        assert _segments(fts) == 1
        assert _free_pages(fts) == 0
        assert _answers(fts, store) == before


class _Failing:
    """A connection on which one statement fails, as it can under load.

    With ``partway`` set the statement runs before the error is raised, so the
    failure leaves behind the transaction the statement opened.
    """

    def __init__(self, conn: sqlite3.Connection, statement: str, error: str,
                 partway: bool = False) -> None:
        self._conn = conn
        self._statement = statement
        self._error = error
        self._partway = partway

    def execute(self, sql: str, *args: object) -> sqlite3.Cursor:
        if sql.strip() == self._statement:
            if self._partway:
                self._conn.execute(sql, *args)
            raise sqlite3.OperationalError(self._error)
        return self._conn.execute(sql, *args)

    def __getattr__(self, name: str) -> object:
        return getattr(self._conn, name)


def _assert_committed_and_searchable(fts: FtsIndex, store: CorpusStore) -> None:
    assert fts.search("safety rule mine", store)[0].url == URL
    stored = fts.conn.execute(
        "SELECT value FROM index_meta WHERE key = 'documents'"
    ).fetchone()[0]
    assert stored == "2"


def test_a_refused_vacuum_does_not_fail_the_rebuild(corpus, monkeypatch, caplog):
    paths, store = corpus
    with FtsIndex(paths.fts_db) as fts:
        monkeypatch.setattr(fts, "conn", _Failing(fts.conn, "VACUUM", "database is locked"))
        with caplog.at_level("WARNING", logger="labour_corpus.index.fts"):
            assert fts.rebuild(paths, store) == 2
        assert "not compacted: database is locked" in caplog.text
        _assert_committed_and_searchable(fts, store)


def test_a_failed_optimize_is_rolled_back_and_does_not_fail_the_rebuild(
    corpus, monkeypatch, caplog
):
    paths, store = corpus
    with FtsIndex(paths.fts_db) as fts:
        failing = _Failing(fts.conn, OPTIMIZE, "database or disk is full", partway=True)
        monkeypatch.setattr(fts, "conn", failing)
        with caplog.at_level("WARNING", logger="labour_corpus.index.fts"):
            assert fts.rebuild(paths, store) == 2
        assert "not compacted: database or disk is full" in caplog.text
        # The half-done merge is rolled back, not left for the next commit.
        assert not fts.conn.in_transaction
        _assert_committed_and_searchable(fts, store)
