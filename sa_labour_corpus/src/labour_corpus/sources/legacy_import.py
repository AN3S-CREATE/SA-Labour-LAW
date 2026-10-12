"""Import documents from a previously harvested local corpus.

This is for material the operator already holds from an earlier local harvest.
The text is parsed with the same ontology and the same redaction as crawled
material, but its provenance is recorded as ``legacy_local_harvest`` so nothing
in the corpus implies this crawler fetched it.

Only the documents are imported. Listing pages, court rolls and non-South
African databases are excluded at classification time, because importing them
is what turned the source corpus into 4,891 records with 201 distinct titles.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterator

from ..models import DocType
from .citation_map import (
    SA_LABOUR_COLLECTIONS,
    is_excluded_collection,
    iter_legacy_records,
)

LEGACY_SOURCE_ID = "legacy_local_harvest"

LEGACY_LICENSE_NOTE = (
    "Text taken from an earlier local harvest held by the operator, not fetched "
    "by this crawler. The canonical URL is the document's authoritative location, "
    "not a record of how this copy was obtained. Confirm every citation against "
    "the original file before relying on it."
)

# Collections that carry consolidated legislation.
LEGISLATION_COLLECTIONS: frozenset[str] = frozenset({"CONSOL_ACT", "NUM_ACT", "HIST_ACT"})

_CITATION_RE = re.compile(r"\[(?P<year>\d{4})\]\s*(?P<court>ZA[A-Z]+)\s*(?P<num>\d+)")
_JUDGMENT_MARKER_RE = re.compile(
    r"IN THE LABOUR (?:APPEAL )?COURT|IN THE HIGH COURT|"
    r"IN THE COMMISSION FOR CONCILIATION",
    re.IGNORECASE,
)
_ACT_MARKER_RE = re.compile(
    r"\bAct\b[^\n]{0,40}\b(?:No\.?\s*)?\d{1,3}\s+of\s+(?:19|20)\d{2}", re.IGNORECASE
)

# A judgment discusses one matter. A listing page enumerates many.
MAX_CITATIONS_IN_A_JUDGMENT = 3

# Below this, a record is a stub or a navigation fragment rather than a document.
MIN_WORDS = 300


@dataclass(slots=True)
class LegacyDoc:
    """One importable document from a legacy corpus."""

    text: str
    collection: str
    doc_type: DocType
    canonical_url: str
    url_is_derived: bool
    neutral_citation: str | None
    source_file: str
    content_sha256: str
    word_count: int


@dataclass(slots=True)
class LegacyScan:
    scanned: int = 0
    excluded_non_sa: int = 0
    rejected_listing: int = 0
    rejected_too_short: int = 0
    judgments: int = 0
    legislation: int = 0
    duplicates: int = 0
    notes: list[str] = field(default_factory=list)


def _saflii_url_from_citation(citation_match: re.Match[str]) -> str:
    court = citation_match.group("court").upper()
    return (
        f"https://www.saflii.org/za/cases/{court}/"
        f"{citation_match.group('year')}/{citation_match.group('num')}.html"
    )


def classify_legacy_record(record: dict) -> tuple[DocType, str | None] | None:
    """Return (doc_type, neutral_citation) for an importable record, else None.

    A record is importable only when it is a single document. Listing pages are
    rejected by citation count, which is the signal that separates a judgment
    from a year index.
    """
    collection = (record.get("collection") or "").upper()
    if is_excluded_collection(collection):
        return None

    text = record.get("text") or ""
    distinct = {m.group(0) for m in _CITATION_RE.finditer(text)}

    if collection in SA_LABOUR_COLLECTIONS:
        if len(distinct) > MAX_CITATIONS_IN_A_JUDGMENT:
            return None
        if not _JUDGMENT_MARKER_RE.search(text):
            return None
        match = _CITATION_RE.search(text)
        return DocType.JUDGMENT, (match.group(0) if match else None)

    if collection in LEGISLATION_COLLECTIONS:
        if not _ACT_MARKER_RE.search(text[:20_000]):
            return None
        return DocType.STATUTE, None

    return None


def scan_legacy_corpus(
    jsonl_path: Path,
    *,
    include_judgments: bool = True,
    include_legislation: bool = True,
) -> tuple[list[LegacyDoc], LegacyScan]:
    """Read a legacy knowledge base and return deduplicated importable documents."""
    scan = LegacyScan()
    by_hash: dict[str, LegacyDoc] = {}

    for record in iter_legacy_records(jsonl_path):
        scan.scanned += 1
        collection = (record.get("collection") or "").upper()

        if is_excluded_collection(collection):
            scan.excluded_non_sa += 1
            continue

        classified = classify_legacy_record(record)
        if classified is None:
            if collection in SA_LABOUR_COLLECTIONS or collection in LEGISLATION_COLLECTIONS:
                scan.rejected_listing += 1
            continue

        doc_type, citation = classified
        if doc_type is DocType.JUDGMENT and not include_judgments:
            continue
        if doc_type is DocType.STATUTE and not include_legislation:
            continue

        text = record.get("text") or ""
        words = int(record.get("word_count") or len(text.split()))
        if words < MIN_WORDS:
            scan.rejected_too_short += 1
            continue

        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest in by_hash:
            scan.duplicates += 1
            continue

        match = _CITATION_RE.search(text) if citation else None
        if match is not None:
            url = _saflii_url_from_citation(match)
            derived = True
        else:
            # No citation to derive from: point at the legacy file itself.
            url = f"legacy:{record.get('source_file') or record.get('id') or digest[:16]}"
            derived = False

        by_hash[digest] = LegacyDoc(
            text=text,
            collection=collection,
            doc_type=doc_type,
            canonical_url=url,
            url_is_derived=derived,
            neutral_citation=citation,
            source_file=record.get("source_file") or "",
            content_sha256=digest,
            word_count=words,
        )
        if doc_type is DocType.JUDGMENT:
            scan.judgments += 1
        else:
            scan.legislation += 1

    return list(by_hash.values()), scan


def forum_for(doc: LegacyDoc) -> str:
    """Forum code for a legacy document."""
    if doc.doc_type is DocType.JUDGMENT:
        return doc.collection
    return "SAFLII_LEGIS"


def legacy_parser_notes(doc: LegacyDoc) -> list[str]:
    """Provenance notes written onto every imported document."""
    notes = [
        "imported from an earlier local harvest; not fetched by this crawler",
        f"legacy source file: {doc.source_file or 'unknown'}",
    ]
    if doc.url_is_derived:
        notes.append(
            "canonical URL derived from the neutral citation read in the text; "
            "it is the document's authoritative location, not an observed fetch"
        )
    return notes


def iter_importable(
    jsonl_path: Path,
    **kwargs: bool,
) -> Iterator[LegacyDoc]:
    docs, _ = scan_legacy_corpus(Path(jsonl_path), **kwargs)
    yield from docs
