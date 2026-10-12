"""Citation-map import from previously harvested SAFLII index pages.

SAFLII publishes year and alphabetical index pages that list every judgment in
a court database. Those listings are structured enough to read verbatim:

    Makhanye v South African Airways (JS 265/13) [2014] ZALCJHB 6 (6 January 2014)

Importing them gives a measured map of what labour case law *exists*, which is
what turns the coverage report from "we have no judgments" into "we hold N of
M known judgments". It records references only -- no judgment text is created,
and a mapped citation is never presented as a held document.

Scope is the Republic of South Africa. Regional material (Botswana, Namibia,
Mozambique, East Africa) and court rolls are dropped, per the corpus's stated
jurisdiction.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterator

# South African labour forums whose listings we read.
SA_LABOUR_COLLECTIONS: frozenset[str] = frozenset({
    "ZALC", "ZALAC", "ZALCC", "ZALCCT", "ZALCD", "ZALCJHB", "ZALCPE", "ZACCMA",
})

# Non-South African databases in the legacy corpus. Explicitly excluded.
NON_SA_PREFIXES: tuple[str, ...] = (
    "BW",   # Botswana
    "NA",   # Namibia
    "MZ",   # Mozambique
    "EAC",  # East African Court of Justice
    "SADC", # SADC Tribunal
    "UK",   # United Kingdom
    "ZW",   # Zimbabwe
    "LS",   # Lesotho
    "SZ",   # Eswatini
)

# Court rolls list hearings, not decisions, and carry no judgment text.
_ROLLS_RE = re.compile(r"ROLLS?$", re.IGNORECASE)

# A listing line, with an optional case number and optional parallel citations:
#   <name> (<case no>) [<year>] <COURT> <n>[; <parallel>] (<date>)
_LISTING_RE = re.compile(
    r"^(?P<name>.{4,300}?)\s*"
    r"(?:\((?P<case_no>[^()\[\]]{1,60})\)\s*)?"
    r"\[(?P<year>(?:19|20)\d{2})\]\s*(?P<court>ZA[A-Z]{1,10})\s*(?P<number>\d{1,5})"
    r"(?P<rest>.*)$"
)

# A trailing "(6 January 2014)" delivery date.
_DATE_RE = re.compile(
    r"\((?P<day>\d{1,2})\s+(?P<month>[A-Za-z]+)\s+(?P<year>(?:19|20)\d{2})\)\s*$"
)

# Parallel report citations, e.g. "(2014) 35 ILJ 1672 (LC)".
_PARALLEL_RE = re.compile(
    r"\((?:19|20)\d{2}\)\s*\d{1,3}\s*(?:ILJ|BLLR|SA|BCLR|LLD)\s*\d{1,5}(?:\s*\([A-Z]{1,4}\))?"
)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

# Lines that are navigation furniture rather than judgments.
_NOISE_RE = re.compile(
    r"^(?:you are here|saflii|databases|>>|home|search|about|back to top|"
    r"\d{4}\s+south africa|south africa:|recent decisions?|"
    r"(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s*(?:\d{4})?$)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class CitationRecord:
    """One judgment reference read from a SAFLII listing line."""

    neutral_citation: str
    court: str
    year: int
    number: int
    case_name: str
    case_no: str | None = None
    date_delivered: str | None = None
    parallel_citations: list[str] = field(default_factory=list)
    source_note: str = ""

    @property
    def saflii_url(self) -> str:
        """The SAFLII path this citation maps to.

        Constructed from the citation using SAFLII's stable URL scheme. It is a
        pointer for a later lawful fetch, not a verified location, and is stored
        with url_is_derived = 1 so nothing mistakes it for an observed URL.
        """
        return f"https://www.saflii.org/za/cases/{self.court}/{self.year}/{self.number}.html"


def is_south_african(court: str) -> bool:
    """True for South African court codes only."""
    upper = court.upper()
    if not upper.startswith("ZA"):
        return False
    if _ROLLS_RE.search(upper):
        return False
    return True


def is_excluded_collection(collection: str) -> bool:
    """True for non-SA databases and court rolls."""
    upper = (collection or "").upper()
    if _ROLLS_RE.search(upper):
        return True
    return any(upper.startswith(prefix) for prefix in NON_SA_PREFIXES)


def _parse_date(rest: str) -> str | None:
    match = _DATE_RE.search(rest)
    if not match:
        return None
    month = _MONTHS.get(match.group("month").lower())
    if not month:
        return None
    try:
        from datetime import date

        return date(int(match.group("year")), month, int(match.group("day"))).isoformat()
    except ValueError:
        return None


def _clean_name(raw: str) -> str | None:
    name = re.sub(r"\s+", " ", raw).strip(" \t.,:;-|")
    if len(name) < 4 or len(name) > 300:
        return None
    if _NOISE_RE.match(name):
        return None
    # A real party line contains letters and almost always a "v".
    if not re.search(r"[A-Za-z]{3}", name):
        return None
    return name


def parse_listing_line(line: str) -> CitationRecord | None:
    """Parse one listing line into a CitationRecord, or None if it is not one."""
    text = line.strip()
    if len(text) < 12 or _NOISE_RE.match(text):
        return None

    match = _LISTING_RE.match(text)
    if not match:
        return None

    court = match.group("court").upper()
    if not is_south_african(court):
        return None

    name = _clean_name(match.group("name"))
    if name is None:
        return None

    rest = match.group("rest") or ""
    case_no = match.group("case_no")
    if case_no:
        case_no = re.sub(r"\s+", " ", case_no).strip()

    year = int(match.group("year"))
    number = int(match.group("number"))

    return CitationRecord(
        neutral_citation=f"[{year}] {court} {number}",
        court=court,
        year=year,
        number=number,
        case_name=name,
        case_no=case_no or None,
        date_delivered=_parse_date(rest),
        parallel_citations=_PARALLEL_RE.findall(rest),
    )


def parse_listing_text(text: str, source_note: str = "") -> list[CitationRecord]:
    """Parse every listing line in an index page's text."""
    out: list[CitationRecord] = []
    for line in (text or "").splitlines():
        record = parse_listing_line(line)
        if record is not None:
            record.source_note = source_note
            out.append(record)
    return out


def iter_legacy_records(jsonl_path: Path) -> Iterator[dict]:
    """Stream a legacy knowledge-base JSONL without loading it into memory."""
    with Path(jsonl_path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


@dataclass(slots=True)
class ImportSummary:
    scanned_records: int = 0
    labour_records: int = 0
    excluded_non_sa: int = 0
    citations_found: int = 0
    unique_citations: int = 0
    by_court: dict[str, int] = field(default_factory=dict)
    year_range: tuple[int, int] | None = None


def collect_citations(
    jsonl_path: Path,
    *,
    collections: frozenset[str] = SA_LABOUR_COLLECTIONS,
) -> tuple[dict[str, CitationRecord], ImportSummary]:
    """Read a legacy knowledge base and return the deduplicated citation map."""
    found: dict[str, CitationRecord] = {}
    summary = ImportSummary()

    for record in iter_legacy_records(jsonl_path):
        summary.scanned_records += 1
        collection = (record.get("collection") or "").upper()

        if is_excluded_collection(collection):
            summary.excluded_non_sa += 1
            continue
        if collection not in collections:
            continue

        summary.labour_records += 1
        source = record.get("source_file") or record.get("id") or "legacy"
        for citation in parse_listing_text(record.get("text") or "", source_note=source):
            summary.citations_found += 1
            existing = found.get(citation.neutral_citation)
            # Keep the richest version of a repeated citation.
            if existing is None or _richness(citation) > _richness(existing):
                found[citation.neutral_citation] = citation

    summary.unique_citations = len(found)
    for citation in found.values():
        summary.by_court[citation.court] = summary.by_court.get(citation.court, 0) + 1
    if found:
        years = [c.year for c in found.values()]
        summary.year_range = (min(years), max(years))
    return found, summary


def _richness(record: CitationRecord) -> int:
    """How complete a record is, used to pick between duplicates."""
    score = len(record.case_name)
    if record.case_no:
        score += 50
    if record.date_delivered:
        score += 50
    score += 10 * len(record.parallel_citations)
    return score
