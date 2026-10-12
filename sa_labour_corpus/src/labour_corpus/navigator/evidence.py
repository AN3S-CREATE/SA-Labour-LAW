"""Point the employee at held documents that discuss their issues.

Read-only FTS5 queries over the corpus index. Drafts are excluded, because a
draft is not the law (D-016). A query with no hits returns no hits: the
navigator never fills the gap with anything it did not find on disk.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Protocol

from ..config import CorpusPaths
from ..sqlite_ro import connect_readonly
from .models import CaseFacts, DisputeKind, EvidenceHit, ReasonGiven

log = logging.getLogger(__name__)

_REASON_QUERIES: dict[ReasonGiven, tuple[str, str]] = {
    ReasonGiven.CONDUCT: ("Misconduct dismissals", '"misconduct" AND "fair procedure"'),
    ReasonGiven.CAPACITY: ("Incapacity dismissals", '"poor performance" OR "ill health"'),
    ReasonGiven.OPERATIONAL_REQUIREMENTS:
        ("Retrenchment consultation", '"operational requirements" AND "consult"'),
    ReasonGiven.CONSTRUCTIVE: ("Constructive dismissal", '"continued employment intolerable"'),
    ReasonGiven.AUTOMATICALLY_UNFAIR: ("Automatically unfair dismissal", '"automatically unfair"'),
}
_DISPUTE_QUERIES: dict[DisputeKind, tuple[str, str]] = {
    DisputeKind.UNFAIR_LABOUR_PRACTICE: ("Unfair labour practices", '"unfair labour practice"'),
    DisputeKind.DISCRIMINATION_NOT_DISMISSAL:
        ("Unfair discrimination", '"unfair discrimination"'),
}
LATE_QUERY = ("Late referral and condonation", '"condonation" AND "good cause"')

# Document types that add something beyond the statutes the provisions quote.
# Statutes, amendments, regulations, forms and gazettes are excluded: the Acts
# are already cited verbatim, and the corpus's doc-type labels for Act copies
# are noisy, so held copies of the Acts are also excluded by title.
# Guidelines and council agreements are excluded too: a measured run matched
# council constitutions and sick-leave clauses on incidental wording.
EVIDENCE_TYPES: tuple[str, ...] = (
    "judgment", "award", "code_of_good_practice", "ccma_rule", "practice_manual",
)
_ACT_TITLE_PATTERNS: tuple[str, ...] = (
    "%labour relations act%", "%basic conditions of employment%",
    "%employment equity act%", "%lra1995%",
)
_ELIGIBLE_SQL = (
    "SELECT canonical_url, doc_type FROM documents WHERE doc_id = ? AND is_draft = 0 "
    f"AND doc_type IN ({','.join('?' * len(EVIDENCE_TYPES))}) AND "
    + " AND ".join("LOWER(IFNULL(title, '')) NOT LIKE ?" for _ in _ACT_TITLE_PATTERNS)
)


def issues_for(facts: CaseFacts, late: bool) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    if facts.dispute is DisputeKind.DISMISSAL:
        if facts.reason in _REASON_QUERIES:
            issues.append(_REASON_QUERIES[facts.reason])
    else:
        issues.append(_DISPUTE_QUERIES[facts.dispute])
    if late:
        issues.append(LATE_QUERY)
    return issues


class EvidenceFinder(Protocol):
    def find(self, issue: str, query: str, limit: int) -> list[EvidenceHit]: ...


class CorpusEvidence:
    def __init__(self, paths: CorpusPaths) -> None:
        self.paths = paths

    def find(self, issue: str, query: str, limit: int = 3) -> list[EvidenceHit]:
        if not (self.paths.fts_db.exists() and self.paths.corpus_db.exists()):
            return []
        fts = connect_readonly(self.paths.fts_db)
        docs = connect_readonly(self.paths.corpus_db)
        try:
            # Over-fetch: drafts, Act copies and excluded types are dropped after ranking.
            rows = fts.execute(
                """SELECT doc_id, title, snippet(docs, 6, '[', ']', ' ... ', 20)
                   FROM docs WHERE docs MATCH ? ORDER BY bm25(docs) LIMIT ?""",
                (query, limit * 10),
            ).fetchall()
            hits: list[EvidenceHit] = []
            for doc_id, title, snip in rows:
                meta = docs.execute(_ELIGIBLE_SQL,
                                    (doc_id, *EVIDENCE_TYPES, *_ACT_TITLE_PATTERNS)).fetchone()
                if meta is None:
                    continue
                hits.append(EvidenceHit(issue=issue, doc_id=doc_id, doc_type=meta[1],
                                        title=title or None, url=meta[0],
                                        snippet=" ".join((snip or "").split())))
                if len(hits) >= limit:
                    break
        except sqlite3.Error as exc:
            log.warning("evidence_query_failed", extra={"issue": issue, "error": str(exc)})
            return []
        finally:
            fts.close()
            docs.close()
        log.info("evidence_found", extra={"issue": issue, "hits": len(hits)})
        return hits
