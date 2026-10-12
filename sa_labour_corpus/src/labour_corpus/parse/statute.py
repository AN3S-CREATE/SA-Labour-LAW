"""Statute, code and instrument metadata extraction.

Recognises the labour statutes this corpus targets, distinguishes a code of
good practice from an act, and reads the short title and act number out of the
text rather than assuming them from a URL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import DocType

# Short title plus act number, e.g. "Labour Relations Act 66 of 1995".
#
# Every word of the name must begin with a capital, which matches both the
# title-case form and the ALL-CAPS form used on statute title pages, and stops
# the pattern swallowing a lower-case lead-in such as "all the provisions of
# the ...". "Act" itself is matched case-insensitively for the same reason.
# At least one capitalised word must precede "Act": a real short title names
# the statute. A bare "Act 66 of 1995" is a citation fragment, not a title, and
# admitting it produced documents titled simply "Act".
_ACT_TITLE_RE = re.compile(
    r"(?P<title>(?:[A-Z][A-Za-z'\-]*[,]?\s+){1,8}?(?i:Act))"
    r"[,\s]*(?:(?i:No|Nr)\.?\s*)?(?P<number>\d{1,3})\s+(?i:of)\s+"
    r"(?P<year>(?:19|20)\d{2})\b"
)

_AMENDMENT_RE = re.compile(r"\bAmendment Act\b", re.IGNORECASE)
_REGULATION_RE = re.compile(
    r"\bRegulations?\b|\bGovernment Notice R\s?\d+|\bGN R\s?\d+", re.IGNORECASE)
_CODE_RE = re.compile(r"\bCode of Good Practice\b", re.IGNORECASE)
_GAZETTE_RE = re.compile(
    r"\bGovernment Gazette\b|\bStaatskoerant\b|\bGG\s?\d{4,6}\b|"
    r"\bGeneral Notice\b|\bGN\s?\d{2,5}\b", re.IGNORECASE)
_CCMA_RULE_RE = re.compile(
    r"\bRules for the Conduct of Proceedings before the CCMA\b|"
    r"\bCCMA Rules?\b", re.IGNORECASE)
_PRACTICE_MANUAL_RE = re.compile(
    r"\bPractice Manual\b|\bPractice Directive\b", re.IGNORECASE)
_FORM_RE = re.compile(r"\bLRA Form\s*7\.\d+\b|\bReferral form\b", re.IGNORECASE)
_GUIDELINE_RE = re.compile(
    r"\bGuidelines?\b|\bInformation Sheet\b|\bFact Sheet\b", re.IGNORECASE)

# A document names itself in its opening; beyond that, Act names are citations.
TITLE_SEARCH_CHARS = 3_000

# Wording that marks the following Act name as a cross-reference rather than
# this document's own title.
_CROSS_REFERENCE_LEAD_RE = re.compile(
    r"(?:of|under|in terms of|provisions? of|pursuant to|defined in|"
    r"contemplated in|referred to in|pursuant of|amend(?:s|ment to)?|"
    r"pursuant|per)\s+the\s*$",
    re.IGNORECASE,
)


# Prose that refers to a code rather than being one: an Act "must take into
# account any relevant code of good practice" (LRA s188(2)); the Minister may
# "issue a code of good practice". Lower case only: a heading such as
# "THE CODE OF GOOD PRACTICE ON ..." names the document itself.
_CODE_REFERENCE_LEAD_RE = re.compile(r"\b(?:any|relevant|a|an|the|such)\s+$")


def _names_itself_a_code(head: str) -> bool:
    """Does the opening name the document a code, rather than cite one?"""
    for match in _CODE_RE.finditer(head):
        lead = head[max(0, match.start() - 20):match.start()]
        if not _CODE_REFERENCE_LEAD_RE.search(lead):
            return True
    return False


def _find_self_title(text: str) -> re.Match[str] | None:
    """Find the Act title the document gives itself, skipping cross-references."""
    for match in _ACT_TITLE_RE.finditer(text):
        lead = text[max(0, match.start() - 40):match.start()]
        if _CROSS_REFERENCE_LEAD_RE.search(lead):
            continue
        return match
    return None


# Wording that marks an instrument as not yet in force. A draft published for
# public comment has no legal effect, and a corpus that cannot tell one from
# the operative instrument will mislead someone about the law that binds them.
# "Bill of Rights" is Chapter 2 of the Constitution, cited by in-force codes
# (the 2022 Harassment Code among them), not a bill before Parliament.
_DRAFT_RE = re.compile(
    r"\bdraft\b|\bfor public comment(s)?\b|\bfor comment(s)?\b|"
    r"\binvites?\s+(?:written\s+)?comment|\bproposed\s+(?:code|amendment|"
    r"regulation|determination)\b|\bnot yet in force\b|\bBill\b(?!\s+of\s+Rights)",
    re.IGNORECASE,
)

# Wording that marks an instrument as in force.
_IN_FORCE_RE = re.compile(
    r"\bcomes? into (?:operation|force)\b|\btakes? effect\b|"
    r"\bwith effect from\b|\bhereby (?:issue|publish|promulgate)\b|"
    r"\bassented to\b|\bdate of commencement\b",
    re.IGNORECASE,
)

_GG_NUMBER_RE = re.compile(r"\bGG\s?(?P<n>\d{4,6})\b|\bGazette No\.?\s*(?P<n2>\d{4,6})\b",
                           re.IGNORECASE)
_GN_NUMBER_RE = re.compile(r"\bG(?:eneral )?N(?:otice)?\.?\s*(?:No\.?\s*)?(?P<n>\d{2,5})\b",
                           re.IGNORECASE)

# The labour instruments this corpus targets, keyed by a canonical short name.
KNOWN_INSTRUMENTS: dict[str, tuple[str, ...]] = {
    "LRA 66 of 1995": (r"\bLabour Relations Act\b", r"\bAct 66 of 1995\b", r"\bLRA\b"),
    "BCEA 75 of 1997": (r"\bBasic Conditions of Employment Act\b", r"\bAct 75 of 1997\b", r"\bBCEA\b"),
    "EEA 55 of 1998": (r"\bEmployment Equity Act\b", r"\bAct 55 of 1998\b"),
    "NMWA 9 of 2018": (r"\bNational Minimum Wage Act\b", r"\bAct 9 of 2018\b"),
    "COIDA 130 of 1993": (r"\bCompensation for Occupational Injuries\b", r"\bAct 130 of 1993\b", r"\bCOIDA\b"),
    "UIA 63 of 2001": (r"\bUnemployment Insurance Act\b", r"\bAct 63 of 2001\b"),
    "UICA 4 of 2002": (r"\bUnemployment Insurance Contributions Act\b", r"\bAct 4 of 2002\b"),
    "PEPUDA 4 of 2000": (r"\bPromotion of Equality and Prevention of Unfair Discrimination\b", r"\bPEPUDA\b"),
    "MHSA 29 of 1996": (r"\bMine Health and Safety Act\b", r"\bAct 29 of 1996\b", r"\bMHSA\b"),
    "MPRDA 28 of 2002": (r"\bMineral and Petroleum Resources Development Act\b", r"\bMPRDA\b"),
    "OHSA 85 of 1993": (r"\bOccupational Health and Safety Act\b", r"\bAct 85 of 1993\b"),
    "PDA 26 of 2000": (r"\bProtected Disclosures Act\b", r"\bAct 26 of 2000\b"),
    "SDA 97 of 1998": (r"\bSkills Development Act\b", r"\bAct 97 of 1998\b"),
    "ODMWA 78 of 1973": (r"\bOccupational Diseases in Mines and Works Act\b", r"\bODMWA\b"),
}


def detect_draft(text: str, title: str | None = None) -> tuple[bool, str]:
    """Is this instrument a draft rather than one in force?

    The title carries the strongest signal -- "Draft Code of Good Practice on
    Dismissal for public comments" says so plainly -- so it is weighted over
    the body, where "draft" may appear in ordinary discussion.
    """
    if title and (match := _DRAFT_RE.search(title)):
        return True, f"title says {match.group(0)!r}"

    head = text[:TITLE_SEARCH_CHARS]
    draft = _DRAFT_RE.search(head)
    if draft and not _IN_FORCE_RE.search(head):
        return True, f"opening says {draft.group(0)!r} with no commencement wording"
    return False, ""


@dataclass(slots=True)
class StatuteMeta:
    doc_type: DocType = DocType.OTHER
    is_draft: bool = False
    draft_reason: str = ""
    short_title: str | None = None
    act_number: str | None = None
    act_year: int | None = None
    instruments: list[str] = field(default_factory=list)
    gazette_number: str | None = None
    notice_number: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        score = 0.2
        if self.doc_type is not DocType.OTHER:
            score += 0.3
        if self.short_title:
            score += 0.25
        if self.instruments:
            score += 0.25
        return round(min(score, 1.0), 2)


# Most specific first. An act is checked before a code so that a statute which
# merely refers to a code of good practice -- as LRA s188(2) does -- is not
# reclassified as one.
_TYPE_ORDER: tuple[tuple[re.Pattern[str], DocType], ...] = (
    (_CCMA_RULE_RE, DocType.CCMA_RULE),
    (_PRACTICE_MANUAL_RE, DocType.PRACTICE_MANUAL),
    (_FORM_RE, DocType.FORM),
    (_AMENDMENT_RE, DocType.AMENDMENT),
    (_ACT_TITLE_RE, DocType.STATUTE),
    (_CODE_RE, DocType.CODE_OF_GOOD_PRACTICE),
    (_REGULATION_RE, DocType.REGULATION),
    (_GAZETTE_RE, DocType.GAZETTE),
    (_GUIDELINE_RE, DocType.GUIDELINE),
)


# How an Act announces itself in its opening. Each yields the Act's own name,
# and "Amendment" in that name is what separates an amending Act from a
# principal Act. Without these, a principal Act fell through to the body scan
# and took the type of whatever it cited first: SAFLII consolidations became
# "amendment" (their history lists amending Acts), the LRA became a "code of
# good practice" (Schedule 8), and gazetted Acts became "regulation" (D-024).
_SAFLII_ACT_RE = re.compile(
    r"(?P<name>[^\n]{3,200})\n\s*South Africa:\s*(?P<db>Consolidated|Numbered) Acts\b")
_ASSENT_RE = re.compile(r"has assented to the following Act", re.IGNORECASE)
_ASSENT_NAME_RE = re.compile(
    r"No\.?\s*\d{1,3}\s+of\s+(?:19|20)\d{2}\s*:\s*(?P<name>[^\n]{3,150}?\bAct\b)",
    re.IGNORECASE)
# The long title on an Act's title page: "ACT" on its own line, then "To ...".
_LONG_TITLE_RE = re.compile(r"(?m)^\s*ACT\s*\n+\s*To\s*(?P<amend>amend)?")
IDENTITY_CHARS = 4_000


def _act_type(name: str) -> DocType:
    return DocType.AMENDMENT if _AMENDMENT_RE.search(name) else DocType.STATUTE


def self_identified_act(text: str) -> DocType | None:
    """The type an Act gives itself in its opening, or None if it does not."""
    head = text[:IDENTITY_CHARS]
    saflii = _SAFLII_ACT_RE.search(head[:800])
    if saflii:
        if saflii.group("db") == "Consolidated":
            return DocType.STATUTE        # a consolidation is the principal Act
        return _act_type(saflii.group("name"))
    if _ASSENT_RE.search(head):
        named = _ASSENT_NAME_RE.search(head)
        if named:
            return _act_type(named.group("name"))
    long_title = _LONG_TITLE_RE.search(head)
    if long_title:
        return DocType.AMENDMENT if long_title.group("amend") else DocType.STATUTE
    return None


def classify_doc_type(text: str, title: str | None = None, *, body_scan: bool = True) -> DocType:
    """Classify an instrument.

    The title is the strongest evidence of what a document *is*, so it is
    tested on its own first. Then the opening, where an Act names itself.
    Body text is only consulted when both say nothing, because a body
    routinely cites other instruments and a citation is not an identity.

    ``body_scan=False`` stops before that last, weakest stage and returns
    OTHER, so the caller keeps the type its source declared. Used for machine
    transcriptions, where an incidental "Regulations" or a cited Act in noisy
    OCR text would otherwise retype a form or a collective agreement.
    """
    if title:
        for regex, doc_type in _TYPE_ORDER:
            if regex.search(title):
                return doc_type

    identity = self_identified_act(text)
    if identity is not None:
        return identity

    # A code names itself in its opening and then cites the Act it is issued
    # under, which the priority scan below would rank first (STATUTE precedes
    # CODE). An Act whose opening lists "Schedule 8: Code of Good Practice"
    # has already been decided by self_identified_act (D-024), and an Act
    # that merely cites "any relevant code of good practice" is not a code.
    if _names_itself_a_code(text[:TITLE_SEARCH_CHARS]):
        return DocType.CODE_OF_GOOD_PRACTICE

    if not body_scan:
        return DocType.OTHER

    # The opening of a document is where it names itself.
    head = text[:20_000]
    for regex, doc_type in _TYPE_ORDER:
        if regex.search(head):
            return doc_type
    return DocType.OTHER


def detect_instruments(text: str) -> list[str]:
    """Which known labour instruments this document references."""
    found: list[str] = []
    sample = text[:200_000]
    for name, patterns in KNOWN_INSTRUMENTS.items():
        for pattern in patterns:
            if re.search(pattern, sample, re.IGNORECASE):
                found.append(name)
                break
    return found


def parse_statute(
    text: str, title: str | None = None, *, machine_transcription: bool = False
) -> StatuteMeta:
    """Extract instrument metadata. Unknown fields stay None.

    For a machine transcription (OCR, D-014) only strong evidence types or
    titles the document: its title, an Act's self-identity (D-024) or a code
    heading in its opening. The body scan and a self-title read from a
    document that does not identify itself as an Act are skipped, because OCR
    noise plus a cross-reference ("in terms of the Labour Relations Act") is
    exactly the mention-for-identity failure of D-015 and D-024.
    """
    meta = StatuteMeta()
    if not text or not text.strip():
        meta.notes.append("empty text; no metadata extracted")
        return meta

    meta.doc_type = classify_doc_type(text, title, body_scan=not machine_transcription)
    meta.is_draft, meta.draft_reason = detect_draft(text, title)
    if meta.is_draft:
        meta.notes.append(
            f"DRAFT instrument, not in force ({meta.draft_reason}). It has no "
            "legal effect and must not be read as the operative instrument."
        )

    # A document names itself at the top. Searching deep into the body finds
    # cross-references instead -- "in terms of the Labour Relations Act" is a
    # citation, not this document's title.
    search_space = f"{title or ''}\n{text[:TITLE_SEARCH_CHARS]}"

    match = _find_self_title(search_space)
    if match and machine_transcription and self_identified_act(text) is None:
        match = None
        meta.notes.append(
            "short title not read from a machine transcription of a document "
            "that does not identify itself as an Act")
    if match:
        meta.short_title = re.sub(r"\s+", " ", match.group("title")).strip()
        meta.act_number = match.group("number")
        meta.act_year = int(match.group("year"))
    elif title:
        meta.short_title = title
    else:
        meta.notes.append("short title not found in source text; left unknown")

    meta.instruments = detect_instruments(text)

    gg = _GG_NUMBER_RE.search(search_space)
    if gg:
        meta.gazette_number = gg.group("n") or gg.group("n2")
    gn = _GN_NUMBER_RE.search(search_space)
    if gn:
        meta.notice_number = gn.group("n")

    return meta
