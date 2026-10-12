"""Operator drop-folder ingestion.

This is the lawful way to grow CCMA and bargaining-council coverage. Awards the
operator already holds -- their own arbitration award, documents received in a
matter, or material obtained under licence -- are dropped into ``inbox/`` and
parsed with the same pipeline, the same ontology and the same redaction as
crawled material.

Provenance is recorded honestly: the canonical URL is a ``file://`` reference
and the source id marks the document as operator-supplied, so nothing in the
corpus ever claims a local file came from a public website.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import CorpusPaths
from ..http import FetchResult
from ..models import DocType
from ..observability import RunLog
from ..pipeline import build_parsed_doc, persist
from ..state import CorpusStore

INBOX_SOURCE_ID = "operator_inbox"
INBOX_FORUM = "OPERATOR_SUPPLIED"

SUPPORTED_SUFFIXES = (".pdf", ".rtf", ".docx", ".doc", ".html", ".htm", ".txt")

INBOX_LICENSE_NOTE = (
    "Operator-supplied document. Not harvested from a public website. "
    "Provenance, licensing and any redistribution rights are the operator's "
    "responsibility."
)

_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".rtf": "application/rtf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
}


@dataclass(slots=True)
class InboxOutcome:
    scanned: int = 0
    stored: int = 0
    skipped_duplicate: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=list)


def _doc_type_for(path: Path) -> DocType:
    """Guess the instrument type from the filename, defaulting to award.

    Operators most often drop arbitration awards here, which is the gap this
    folder exists to fill.
    """
    name = path.name.lower()
    if "judgment" in name or "judgement" in name:
        return DocType.JUDGMENT
    # An award says so first. And "act" must stand apart from other letters:
    # it is inside "practice" and "contract", so an unfair labour practice
    # award was typed a statute and lost its case number and outcome (D-027).
    # Letter boundaries rather than \b, because "_" is a word character and
    # filenames are often joined with underscores.
    if re.search(r"(?<![a-z])(?:award|arbitrat)", name):
        return DocType.AWARD
    if "agreement" in name or "collective" in name:
        return DocType.BARGAINING_COUNCIL_AGREEMENT
    if "rule" in name:
        return DocType.CCMA_RULE
    if "code" in name:
        return DocType.CODE_OF_GOOD_PRACTICE
    if re.search(r"(?<![a-z])act(?![a-z])", name) or "statute" in name:
        return DocType.STATUTE
    return DocType.AWARD


def ingest_inbox(
    paths: CorpusPaths,
    store: CorpusStore,
    log: RunLog,
    *,
    limit: int | None = None,
) -> InboxOutcome:
    """Parse every supported file in inbox/ into the corpus."""
    outcome = InboxOutcome()
    paths.inbox.mkdir(parents=True, exist_ok=True)

    files = sorted(
        p for p in paths.inbox.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not files:
        outcome.notes.append(
            f"no supported files in {paths.inbox}; "
            f"supported suffixes: {', '.join(SUPPORTED_SUFFIXES)}"
        )
        return outcome

    for path in files:
        if limit is not None and outcome.stored >= limit:
            break
        outcome.scanned += 1
        try:
            body = path.read_bytes()
        except OSError as exc:
            outcome.failed += 1
            log.error("parse", f"could not read {path.name}: {exc}", source_id=INBOX_SOURCE_ID)
            continue

        digest = hashlib.sha256(body).hexdigest()
        canonical = path.resolve().as_uri()

        if store.is_unchanged(canonical, digest):
            outcome.skipped_duplicate += 1
            log.bump("skipped_duplicate")
            continue

        result = FetchResult(
            url=canonical,
            final_url=canonical,
            status=200,
            headers={"content-type": _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")},
            body=body,
            content_sha256=digest,
            bytes=len(body),
            elapsed_ms=0,
        )

        try:
            doc, text = build_parsed_doc(
                source_id=INBOX_SOURCE_ID,
                forum=INBOX_FORUM,
                result=result,
                default_doc_type=_doc_type_for(path),
                license_note=INBOX_LICENSE_NOTE,
            )
            doc.parser_notes.append(f"operator-supplied file: {path.name}")
            persist(paths, store, doc, text)
        except Exception as exc:  # noqa: BLE001 -- third-party parsers fail broadly on corrupt input (D-008)
            outcome.failed += 1
            log.error("parse", f"{type(exc).__name__}: {exc}",
                      source_id=INBOX_SOURCE_ID, url=canonical)
            continue

        store.record_fetch(
            canonical,
            source_id=INBOX_SOURCE_ID,
            status=200,
            content_sha256=digest,
            bytes_=len(body),
            doc_id=doc.doc_id,
            raw_path=str(path),
        )
        outcome.stored += 1
        log.bump("parsed")
        log.event("parse", source_id=INBOX_SOURCE_ID, url=canonical,
                  doc_id=doc.doc_id, doc_type=doc.doc_type, status="ok")

    return outcome


README_TEXT = """# inbox/

Drop documents you already lawfully hold into this folder and run:

    labour-corpus ingest-inbox --out <corpus root> --contact-email you@example.com

Supported: .pdf .rtf .docx .doc .html .htm .txt

This is the lawful route to CCMA and bargaining-council award coverage. The
free public web does not publish the CCMA award archive, so the only honest
ways to grow that part of the corpus are:

  1. awards you or your client received as a party to the matter;
  2. awards obtained under a licence you hold with a commercial publisher,
     used within that licence;
  3. material a bargaining council or the CCMA publishes itself.

Files here are parsed with the same ontology and the same personal-information
redaction as crawled material. Their provenance is recorded as operator-supplied
and a file:// URL, so nothing in the corpus claims they came from a public site.

Do not place material here that you are not entitled to hold or process.
"""
