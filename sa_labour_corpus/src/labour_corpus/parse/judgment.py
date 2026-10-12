"""Judgment metadata extraction.

Every field here is either read out of the document text or left unset. There
is no inference of a citation from a URL, no reconstruction of a case number
from a filename, and no guessed date. A corpus that invents a citation is worse
than one that admits it does not know, because a downstream reader cannot tell
the difference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

# Neutral citation, e.g. [2024] ZALCJHB 123 or (2023) ZACC 12.
_NEUTRAL_RE = re.compile(
    r"[\[\(](?P<year>(?:19|20)\d{2})[\]\)]\s*"
    r"(?P<court>ZA[A-Z]{2,8})\s*(?P<number>\d{1,4})\b"
)

# Labour Court and LAC case numbers, e.g. JR 1487/21.
_CASE_NO_RE = re.compile(
    r"\b(?P<prefix>JR|JS|JA|CA|PA|DA|RA|CCT|SCA|C|D|J|P|EL|PR)\s?"
    r"(?P<number>\d{1,5})\s?/\s?(?P<year>\d{2,4})\b"
)

# CCMA and bargaining-council case numbers, e.g. GAJB12345-24. The first two
# letters are the province, the next two the office.
_CCMA_CASE_NO_RE = re.compile(
    r"\b(?P<office>(?:GA|WE|KN|EC|FS|LP|MP|NC|NW)[A-Z]{2})\s?"
    r"(?P<number>\d{1,6})\s?[-/]\s?(?P<year>\d{2,4})\b"
)

_CASE_NO_LABEL_RE = re.compile(
    r"\b(?:case\s*(?:no|number|nr)\.?|saak\s*(?:no|nommer)\.?)\s*[:\-]?\s*"
    r"(?P<value>[A-Z]{1,6}\s?\d{1,6}\s?[-/]\s?\d{2,4})",
    re.IGNORECASE,
)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    # Afrikaans month names appear in Afrikaans judgments.
    "januarie": 1, "februarie": 2, "maart": 3, "mei": 5, "junie": 6,
    "julie": 7, "augustus": 8, "oktober": 10, "desember": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

_DATE_LABEL_RE = re.compile(
    r"\b(?:date\s+of\s+judgment|judgment\s+delivered|delivered\s+on|delivered|"
    r"handed\s+down|date\s+of\s+award|datum\s+van\s+uitspraak|gelewer)\b"
    r"\s*[:\-]?\s*(?P<value>[^\n]{0,60})",
    re.IGNORECASE,
)
_DMY_RE = re.compile(
    rf"\b(?P<day>\d{{1,2}})\s+(?P<month>{_MONTH_ALT})\s+(?P<year>(?:19|20)\d{{2}})\b",
    re.IGNORECASE,
)
_MDY_RE = re.compile(
    rf"\b(?P<month>{_MONTH_ALT})\s+(?P<day>\d{{1,2}}),?\s+(?P<year>(?:19|20)\d{{2}})\b",
    re.IGNORECASE,
)
_ISO_RE = re.compile(r"\b(?P<year>(?:19|20)\d{2})-(?P<month>\d{2})-(?P<day>\d{2})\b")
_NUMERIC_DMY_RE = re.compile(
    r"\b(?P<day>\d{1,2})[/.](?P<month>\d{1,2})[/.](?P<year>(?:19|20)\d{2})\b"
)

# "v" / "vs" only. "and" is not treated as a separator because it appears
# inside party names such as "Commission for Conciliation, Mediation and
# Arbitration", and splitting on it manufactures fictional parties.
_VERSUS_RE = re.compile(
    r"^(?P<first>.{3,160}?)\s+(?:v|vs|versus)\.?\s+(?P<second>.{3,160})$",
    re.IGNORECASE,
)

# Structural headings that must never be read as a person's name.
_NAME_STOPWORDS = frozenset({
    "judgment", "judgement", "order", "appearances", "ruling", "award", "reasons",
    "introduction", "background", "analysis", "conclusion", "coram",
    "before", "heard", "delivered", "applicant", "respondent", "appellant",
    "court", "republic", "commission", "arbitration", "mediation",
    "conciliation", "uitspraak", "bevel", "the", "this", "that", "his",
    "her", "did", "failed", "found", "held", "was", "is", "and", "for",
    # Law report series and court suffixes that appear in parallel citations.
    "ilj", "bllr", "sallr", "bclr", "sa", "lac", "lc", "cc", "sca", "all",
    "reportable", "noteup", "lawcite", "saflii", "databases",
})

# The title must be a separate word. Without the required whitespace, the law
# report abbreviation "ILJ" in a parallel citation such as
# "(2000) 21 ILJ 571 (LAC)" parses as a judge named "IL" with the title "J".
_JUDGE_RE = re.compile(
    r"(?:^|[\n;,]|\s)(?P<name>[A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,3}),?\s+"
    r"(?P<title>AJA|ADJP|DJP|ACJ|AJP|JA|AJ|JP|CJ|J)\b(?![A-Za-z])"
)
# A commissioner's name is written as initials plus a surname, or a surname,
# optionally followed by N.O. A lowercase word after "Commissioner" means the
# sentence is talking about the commissioner, not naming one.
# The keyword is matched case-insensitively by spelling both forms, so that the
# name pattern itself stays case-sensitive and does not swallow lowercase prose.
_COMMISSIONER_RE = re.compile(
    r"\b(?i:commissioner)\s*[:\-]?\s*"
    r"(?P<name>(?:[A-Z]\.?\s*){0,3}[A-Z][A-Za-z'\-]{2,}"
    r"(?:\s+[A-Z][A-Za-z'\-]{2,}){0,2})"
    r"(?=\s*(?:N\.?\s*O\.?|\n|,|$))",
)

# Party blocks label the role at the end of the line. Whitespace runs collapse
# during text extraction, so the role label is the anchor, not the spacing.
_PARTY_LABEL_RE = re.compile(
    r"^(?P<name>.{3,160}?)[\s:\-]+"
    r"(?P<role>(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH)?\s*"
    r"(?:APPLICANT|RESPONDENT|APPELLANT|INTERVENING PARTY|AMICUS CURIAE)S?)"
    r"\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_NOISE_PREFIX_RE = re.compile(
    r"^(?:in the matter between|in die saak tussen|between)\s*[:\-]?\s*",
    re.IGNORECASE,
)


@dataclass(slots=True)
class JudgmentMeta:
    """What could actually be read out of a judgment. None means unknown."""

    neutral_citation: str | None = None
    citation: str | None = None
    case_no: str | None = None
    date_delivered: str | None = None
    year: int | None = None
    parties: list[str] = field(default_factory=list)
    judges: list[str] = field(default_factory=list)
    court_code: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        """Share of the four key fields that were actually established."""
        found = sum(
            1 for value in (
                self.neutral_citation, self.case_no,
                self.date_delivered, self.parties or None,
            ) if value
        )
        return round(found / 4.0, 2)


def extract_neutral_citation(text: str) -> tuple[str | None, str | None, int | None]:
    """Return (neutral_citation, court_code, year) read verbatim from the text."""
    match = _NEUTRAL_RE.search(text)
    if not match:
        return None, None, None
    year = int(match.group("year"))
    court = match.group("court").upper()
    number = match.group("number")
    return f"[{year}] {court} {number}", court, year


def extract_case_no(text: str) -> str | None:
    """Read a court or CCMA case number out of the text, else None."""
    labelled = _CASE_NO_LABEL_RE.search(text)
    if labelled:
        return re.sub(r"\s+", " ", labelled.group("value")).strip()
    match = _CASE_NO_RE.search(text)
    if match:
        return f"{match.group('prefix')}{match.group('number')}/{match.group('year')}"
    ccma = _CCMA_CASE_NO_RE.search(text)
    if ccma:
        return f"{ccma.group('office')}{ccma.group('number')}-{ccma.group('year')}"
    return None


def _to_iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _parse_date_string(value: str) -> str | None:
    for regex in (_DMY_RE, _MDY_RE):
        match = regex.search(value)
        if match:
            month = _MONTHS.get(match.group("month").lower())
            if month:
                iso = _to_iso(int(match.group("year")), month, int(match.group("day")))
                if iso:
                    return iso
    match = _ISO_RE.search(value)
    if match:
        return _to_iso(int(match.group("year")), int(match.group("month")), int(match.group("day")))
    match = _NUMERIC_DMY_RE.search(value)
    if match:
        return _to_iso(int(match.group("year")), int(match.group("month")), int(match.group("day")))
    return None


def extract_date_delivered(text: str) -> str | None:
    """Prefer an explicitly labelled delivery date over a loose date."""
    head = text[:8_000]
    for label in _DATE_LABEL_RE.finditer(head):
        parsed = _parse_date_string(label.group("value"))
        if parsed:
            return parsed
    return _parse_date_string(head)


def extract_parties(text: str) -> list[str]:
    """Read party names from labelled blocks or a 'X v Y' heading."""
    head = text[:12_000]
    parties: list[str] = []

    for match in _PARTY_LABEL_RE.finditer(head):
        name = _clean_party(match.group("name"))
        if name and name not in parties:
            parties.append(name)
        if len(parties) >= 8:
            break
    if parties:
        return parties

    for line in head.split("\n")[:80]:
        candidate = _NOISE_PREFIX_RE.sub("", line.strip())
        if not 8 <= len(candidate) <= 320:
            continue
        if candidate.lower().startswith(("judgment", "uitspraak", "coram", "before")):
            continue
        versus = _VERSUS_RE.match(candidate)
        if versus:
            first = _clean_party(versus.group("first"))
            second = _clean_party(versus.group("second"))
            if first and second:
                return [first, second]
    return []


def _clean_party(raw: str) -> str | None:
    name = re.sub(r"\s+", " ", raw).strip(" \t.,:;-_|")
    name = _NOISE_PREFIX_RE.sub("", name).strip()
    name = re.sub(
        r"\s*\((?:FIRST|SECOND|THIRD|FOURTH|FIFTH)?\s*"
        r"(?:APPLICANT|RESPONDENT|APPELLANT)S?\)$",
        "", name, flags=re.IGNORECASE,
    ).strip()
    if len(name) < 3 or len(name) > 200:
        return None
    if not re.search(r"[A-Za-z]{3}", name):
        return None
    return name


def _is_name_like(candidate: str) -> bool:
    """Reject structural headings and sentence fragments as names."""
    words = [w for w in re.split(r"\s+", candidate.strip()) if w]
    if not words or len(words) > 5:
        return False
    surnames = 0
    for word in words:
        bare = word.strip(".,'-").lower()
        if bare in _NAME_STOPWORDS:
            return False
        if not bare:
            return False
        # A single letter is an initial, which is normal in "N P Dlamini".
        if len(bare) > 1:
            surnames += 1
    # At least one word must be a real name rather than a run of initials.
    return surnames >= 1


def _add_unique(found: list[str], entry: str) -> None:
    """Append unless an equivalent entry is already present.

    Comparison ignores case so that "BARTHUS AJ" and "Barthus AJ" — the same
    judge written twice in one judgment — are not recorded as two people.
    """
    if entry.lower() not in {existing.lower() for existing in found}:
        found.append(entry)


def extract_judges(text: str) -> list[str]:
    head = text[:12_000]
    found: list[str] = []
    for match in _JUDGE_RE.finditer(head):
        # A match may reach back across a line break into a heading such as
        # "JUDGEMENT\nSHAI AJ"; only the final line is the name.
        name = match.group("name").strip().splitlines()[-1].strip()
        if not _is_name_like(name):
            continue
        _add_unique(found, f"{name} {match.group('title')}")
        if len(found) >= 5:
            break
    for match in _COMMISSIONER_RE.finditer(head):
        name = re.sub(r"\s+", " ", match.group("name")).strip()
        if not _is_name_like(name):
            continue
        _add_unique(found, f"Commissioner {name}")
        if len(found) >= 6:
            break
    return found


def parse_judgment(text: str, title: str | None = None) -> JudgmentMeta:
    """Extract judgment metadata. Unknown fields stay None.

    ``title`` is the HTML document title when one exists. SAFLII puts the full
    citation there, so it is searched for the neutral citation before the body.
    It is still a verbatim read of published text, never a construction.
    """
    meta = JudgmentMeta()
    if not text or not text.strip():
        meta.notes.append("empty text; no metadata extracted")
        return meta

    search_space = f"{title}\n{text}" if title else text
    neutral, court, year = extract_neutral_citation(search_space)
    meta.neutral_citation = neutral
    meta.court_code = court
    meta.citation = neutral  # SAFLII judgments cite by neutral citation
    meta.case_no = extract_case_no(search_space)
    meta.date_delivered = extract_date_delivered(text)
    meta.parties = extract_parties(text)
    meta.judges = extract_judges(text)

    if meta.date_delivered:
        meta.year = int(meta.date_delivered[:4])
    elif year:
        meta.year = year

    for label, value in (
        ("neutral_citation", meta.neutral_citation),
        ("case_no", meta.case_no),
        ("date_delivered", meta.date_delivered),
    ):
        if not value:
            meta.notes.append(f"{label} not found in source text; left unknown")
    if not meta.parties:
        meta.notes.append("parties not found in source text; left unknown")
    return meta
