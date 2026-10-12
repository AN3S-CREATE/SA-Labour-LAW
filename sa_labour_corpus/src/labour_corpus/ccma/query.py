"""Read-only queries over the catalogue, for the MCP server and the CLI.

Standard library only. Opens the catalogue read-only; nothing here can write,
fetch, or reach the network.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..sqlite_ro import connect_readonly

MAX_LIMIT = 50
COVERAGE_STATEMENT = (
    "This catalogue holds what allow-listed sources exposed to past runs, plus "
    "records the operator supplied. It is not every CCMA award: the great majority "
    "of awards are never published. completeness_claim is false."
)
FOOTER = "Not legal advice. Verify against the full award and current CCMA Rules."


class QueryError(ValueError):
    """A bad argument from the caller."""


def open_catalogue(path: Path) -> sqlite3.Connection:
    if not Path(path).exists():
        raise QueryError(f"catalogue not found at {path}; run `labour-corpus ccma harvest` first")
    conn = connect_readonly(path)
    conn.row_factory = sqlite3.Row
    return conn


def fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 query: every word, quoted, ANDed."""
    words = re.findall(r"[\w'-]+", text or "", flags=re.UNICODE)
    words = [w.replace('"', "") for w in words if w.strip("'-")]
    if not words:
        raise QueryError("query must contain at least one word")
    return " ".join(f'"{w}"' for w in words[:20])


def _record_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "source": row["source"],
        "native_id": row["native_id"],
        "record_kind": row["record_kind"],
        "case_number": row["case_number"],
        "neutral_citation": row["neutral_citation"],
        "title": row["title"],
        "forum": row["forum"],
        "year": row["year"],
        "date_delivered": row["date_delivered"],
        "dispute_type": row["dispute_type"],
        "outcome_label": row["outcome_label"],
        "source_url": row["source_url"],
        "parse_status": row["parse_status"],
        "provenance": row["provenance"],
        "retrieved_at": row["retrieved_at"],
    }


def _unit_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "confidence": row["confidence"],
        "dispute_type": row["dispute_type"],
        "citations": json.loads(row["citations_json"]),
        "body": json.loads(row["body"]),
    }


def search(conn: sqlite3.Connection, query: str, year_from: int | None = None,
           year_to: int | None = None, dispute_type: str | None = None,
           limit: int | None = 10) -> dict[str, Any]:
    limit = max(1, min(int(limit or 10), MAX_LIMIT))
    match = fts_query(query)
    sql = """
        SELECT r.*, snippet(records_fts, 4, '[', ']', ' … ', 24) AS snip,
               bm25(records_fts) AS score
        FROM records_fts JOIN records r
          ON r.source = records_fts.source AND r.native_id = records_fts.native_id
        WHERE records_fts MATCH ?
    """
    params: list[Any] = [match]
    if year_from is not None:
        sql += " AND r.year >= ?"
        params.append(int(year_from))
    if year_to is not None:
        sql += " AND r.year <= ?"
        params.append(int(year_to))
    if dispute_type:
        sql += " AND r.dispute_type = ?"
        params.append(dispute_type)
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)
    hits = []
    for row in conn.execute(sql, params):
        item = _record_dict(row)
        item["snippet"] = row["snip"]
        hits.append(item)
    return {"query": query, "count": len(hits), "results": hits, "note": FOOTER}


def _find_record(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    key = (key or "").strip()
    if not key:
        raise QueryError("native_id_or_case_number is required")
    row = conn.execute("SELECT * FROM records WHERE native_id = ? LIMIT 1", (key,)).fetchone()
    if row is not None:
        return row
    if ":" in key:
        source, native = key.split(":", 1)
        row = conn.execute("SELECT * FROM records WHERE source=? AND native_id=?",
                           (source, native)).fetchone()
        if row is not None:
            return row
    squashed = re.sub(r"\s+", "", key).upper()
    return conn.execute(
        """SELECT * FROM records
           WHERE UPPER(REPLACE(case_number, ' ', '')) = ?
              OR UPPER(REPLACE(neutral_citation, ' ', '')) = ?
           LIMIT 1""",
        (squashed, squashed),
    ).fetchone()


def get(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
    row = _find_record(conn, key)
    if row is None:
        return {"found": False, "key": key}
    fk = f"{row['source']}:{row['native_id']}"
    units = [_unit_dict(u) for u in conn.execute(
        "SELECT * FROM knowledge_units WHERE record_fk=? ORDER BY kind", (fk,))]
    scenarios = [
        _unit_dict(u) for u in conn.execute(
            "SELECT * FROM knowledge_units WHERE kind='scenario' AND citations_json LIKE ?",
            (f'%"native_id": "{row["native_id"]}"%',))
    ]
    return {
        "found": True,
        "record": _record_dict(row),
        "parse_notes": json.loads(row["parse_notes"] or "[]"),
        "knowledge": units,
        "scenarios": scenarios,
        "note": FOOTER,
    }


def strategies(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
    result = get(conn, key)
    if not result.get("found"):
        return result
    return {
        "found": True,
        "record": result["record"],
        "strategies": [u for u in result["knowledge"] if u["kind"] == "strategy"],
        "note": FOOTER,
    }


def scenarios(conn: sqlite3.Connection, dispute_type: str | None = None) -> dict[str, Any]:
    if dispute_type:
        rows = conn.execute(
            "SELECT * FROM knowledge_units WHERE kind='scenario' AND dispute_type=? ORDER BY title",
            (dispute_type,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM knowledge_units WHERE kind='scenario' ORDER BY dispute_type, title"
        ).fetchall()
    types = [r[0] for r in conn.execute(
        "SELECT DISTINCT dispute_type FROM records WHERE dispute_type IS NOT NULL ORDER BY 1")]
    return {"dispute_type": dispute_type, "count": len(rows),
            "scenarios": [_unit_dict(r) for r in rows],
            "known_dispute_types": types, "note": FOOTER}


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    def rows(sql: str) -> list[dict[str, Any]]:
        return [dict(r) for r in conn.execute(sql)]

    last_run = conn.execute(
        "SELECT * FROM run_log ORDER BY rowid DESC LIMIT 1").fetchone()
    return {
        "completeness_claim": False,
        "statement": COVERAGE_STATEMENT,
        "sources": rows("SELECT id, status, enabled, operator_confirmed, record_kind FROM sources ORDER BY id"),
        "records_by_source": rows(
            "SELECT source, record_kind, parse_status, COUNT(*) AS records FROM records "
            "GROUP BY source, record_kind, parse_status ORDER BY source"),
        "knowledge_units": rows(
            "SELECT kind, confidence, COUNT(*) AS units FROM knowledge_units GROUP BY kind, confidence"),
        "unresolved_errors": rows(
            "SELECT class, COUNT(*) AS n FROM errors WHERE resolved=0 GROUP BY class ORDER BY n DESC"),
        "last_run": dict(last_run) if last_run else None,
    }
