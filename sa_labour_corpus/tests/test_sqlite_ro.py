"""The shared read-only connection survives awkward folder names."""

from __future__ import annotations

import sqlite3

import pytest

from labour_corpus.sqlite_ro import connect_readonly


@pytest.mark.parametrize("folder", ["proj#1", "a%20b", "with space"])
def test_connect_readonly_opens_the_right_file_and_refuses_writes(tmp_path, folder):
    db = tmp_path / folder / "c.sqlite"
    db.parent.mkdir()
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")

    conn = connect_readonly(db)
    try:
        assert conn.execute("SELECT x FROM t").fetchall() == [(1,)]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO t VALUES (2)")
    finally:
        conn.close()
    # A hand-built URI would have created a stray file named after the prefix.
    assert sorted(p.name for p in tmp_path.iterdir()) == [folder]
