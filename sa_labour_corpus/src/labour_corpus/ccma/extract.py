"""Bytes -> text -> metadata, shared by scrape (inspect) and seed (persist).

Reuses the corpus parsers, so an award is read exactly as a crawled judgment
is: format by magic bytes, metadata read verbatim or left null, POPIA
redaction before anything is indexed.

Award text is untrusted input. Control characters are stripped before any
further processing, and nothing in the text is ever treated as an instruction.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass, field

from ..models import TextStatus
from ..ontology.dismissal import ALL_TAGS
from ..ontology.tagger import TaggingResult, tag_document
from ..parse.judgment import JudgmentMeta, parse_judgment
from ..parse.office import SniffedFormat, sniff_format
from ..parse.redaction import redact
from ..parse.statute import detect_draft
from ..pipeline import extract_text
from .allowlist import RecordKind

# C0/C1 controls other than tab and newline, plus bidi overrides that can
# disguise text (a known prompt-injection carrier).
# Built from code points so the source file itself carries no invisible
# characters: U+202A..U+202E and U+2066..U+2069.
_BIDI_CONTROLS = "".join(map(chr, (*range(0x202A, 0x202F), *range(0x2066, 0x206A))))
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f" + _BIDI_CONTROLS + "]")

_TAG_CATEGORY = {tag.name: tag.category for tag in ALL_TAGS}

# Dispute type from tag evidence. A category decides only with evidence;
# on a tie the more specific type wins, in this order.
DISPUTE_TYPES: tuple[tuple[str, str], ...] = (
    ("automatically_unfair_dismissal", "automatically_unfair"),
    ("constructive_dismissal", "constructive_dismissal"),
    ("dismissal_operational_requirements", "operational"),
    ("dismissal_incapacity", "incapacity"),
    ("dismissal_misconduct", "misconduct"),
    ("unfair_labour_practice", "s186_2_unfair_labour_practice"),
    ("unfair_discrimination", "eea_discrimination"),
)
UNCLASSIFIED = "unclassified"

# Map Outcome enum values to the label stored and shown.
OUTCOME_LABELS = {
    "fair": "dismissal_fair",
    "unfair_substantive": "substantively_unfair",
    "unfair_procedural": "procedurally_unfair",
    "both": "substantively_and_procedurally_unfair",
    "automatically_unfair": "automatically_unfair",
    "review_upheld": "review_dismissed_award_stands",
    "review_set_aside": "award_reviewed_and_set_aside",
    "unknown": "not_determined",
}


# Used only when the shared tagger leaves the outcome unknown. The tagger's
# fairness pattern needs "dismissal was fair" adjacent; awards usually write
# "the dismissal of the applicant was substantively and procedurally fair".
# Unfair findings are not handled here: the tagger already matches those.
_FAIR_FINDING_RE = re.compile(
    r"\bdismissal(?:\s+of\s+[^.]{1,80}?)?\s+(?:was|is)\s+(?:both\s+)?"
    r"(?:(?:substantively|procedurally)(?:\s+and\s+(?:substantively|procedurally))?\s+)?fair\b",
    re.IGNORECASE,
)


# Gazette and notice numbers claimed by an operator-supplied title, e.g.
# "(GN 3470 / GG 53294)". They are checked against the text, never trusted.
_CLAIMED_NUMBER_RE = re.compile(
    r"\b(?P<label>GG|GN|Gazette|Notice)\s*(?:No\.?\s*)?(?P<number>\d{3,6})\b", re.IGNORECASE)
_NUMBER_CHECK_CHARS = 20_000
_TITLE_WORD_RE = re.compile(r"[A-Za-z]{4,}")

# A scanned gazette keeps a text layer for its running heads and footers only:
# thousands of characters, all of them page furniture (GG 53294: 94 characters
# of anything else over 16 pages). Below this many characters of other text per
# page on average, a multi-page PDF is treated as a scan. Deliberately low: a
# sparse genuine document must not be sent to OCR. A scan with a text-layer
# cover page can still pass; that limit is stated in the architecture notes.
MIN_SUBSTANTIVE_CHARS_PER_PAGE = 40


def strip_controls(text: str) -> str:
    return _CONTROL_RE.sub(" ", text or "")


def substantive_chars(text: str, pages: int) -> int:
    """Characters left once lines that recur across pages are set aside.

    Running heads and footers repeat page after page, differing at most in
    their numbers. What remains is the text a reader would call the document.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if pages < 2:
        return sum(len(line) for line in lines)
    shapes = [re.sub(r"\d+", "#", line).lower() for line in lines]
    counts = Counter(shapes)
    recurring = max(2, pages // 3)
    return sum(len(line) for line, shape in zip(lines, shapes) if counts[shape] < recurring)


def pdf_page_count(body: bytes) -> int:
    """Pages in a PDF body, or 0 when it is not a readable PDF."""
    if sniff_format(body) is not SniffedFormat.PDF:
        return 0
    # Lazy, as in parse.pdf: pypdf is slow to import and only PDFs need it.
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        return len(PdfReader(io.BytesIO(body), strict=False).pages)
    except (PyPdfError, ValueError, OSError, KeyError, TypeError):
        # PyPdfError, not PdfReadError: LimitReachedError and ParseError are
        # siblings of PdfReadError and would otherwise end the seed.
        return 0


def check_title_words(title: str | None, text: str) -> list[str]:
    """Whether the words of an operator-supplied title occur in the text.

    Parenthesised parts (gazette and notice numbers) are left to
    ``check_claimed_numbers``.
    """
    words = list(dict.fromkeys(
        w.lower() for w in _TITLE_WORD_RE.findall(re.sub(r"\([^)]*\)", " ", title or ""))))
    if not words:
        return []
    lowered = text.lower()
    missing = [w for w in words if w not in lowered]
    if missing:
        return [f"UNVERIFIED: title words not found in the document text: {', '.join(missing)}"]
    return [f"every title word occurs in the document text ({', '.join(words)})"]


def check_claimed_numbers(title: str | None, text: str) -> list[str]:
    """Notes stating whether each gazette/notice number in a title occurs in the text.

    A title can come from the allow-list or a listing; the numbers in it are a
    claim about the document. The text is the only evidence (D-006).
    """
    notes: list[str] = []
    head = text[:_NUMBER_CHECK_CHARS]
    for match in _CLAIMED_NUMBER_RE.finditer(title or ""):
        label, number = match.group("label").upper(), match.group("number")
        if re.search(rf"(?<!\d){number}(?!\d)", head):
            notes.append(f"{label} {number} from the title occurs in the document text")
        else:
            notes.append(f"UNVERIFIED: {label} {number} from the title does not occur in the "
                         f"first {_NUMBER_CHECK_CHARS:,} characters of the document text")
    return notes


@dataclass(slots=True)
class Parsed:
    text: str                      # control-stripped, NOT redacted: never persisted
    redacted_text: str             # what may be indexed
    title: str | None
    text_status: TextStatus
    meta: JudgmentMeta
    tagging: TaggingResult
    dispute_type: str
    dispute_reason: str
    outcome_label: str
    parse_status: str              # parsed | failed | ocr_needed | unsupported
    notes: list[str] = field(default_factory=list)

    @property
    def citation(self) -> str | None:
        return self.meta.case_no or self.meta.neutral_citation


def classify_dispute(tagging: TaggingResult) -> tuple[str, str]:
    """(dispute_type, reason). Reason names the evidence so it can be audited."""
    tags = set(tagging.dismissal_tags)
    by_category = Counter(_TAG_CATEGORY.get(t) for t in tags)
    candidates: list[tuple[int, int, str, str]] = []
    for priority, (dispute_type, key) in enumerate(DISPUTE_TYPES):
        if key in {"misconduct", "incapacity", "operational"}:
            weight = by_category.get(key, 0)
            evidence = sorted(t for t in tags if _TAG_CATEGORY.get(t) == key)
        else:
            weight = 2 if key in tags else 0   # a named statutory/fairness tag is strong
            evidence = [key] if key in tags else []
        if weight:
            candidates.append((weight, -priority, dispute_type, ", ".join(evidence)))
    if not candidates:
        return UNCLASSIFIED, "no dispute-type tag evidence in the text"
    _, _, dispute_type, tagged = max(candidates)
    return dispute_type, f"rule-based: tags [{tagged}]"


def parse_artifact(
    body: bytes, content_type: str | None, url: str, record_kind: RecordKind,
    *, stored_text: str | None = None, title_hint: str | None = None,
) -> Parsed:
    """Extract, redact, tag and classify one artefact.

    ``stored_text`` lets the local-corpus adapter reuse text the main corpus
    already extracted (including OCR) instead of re-reading the bytes.

    A record that decides nothing (``record_kind.is_decision`` is false) keeps
    its topic tags but gets no case number, citation, outcome or dispute type:
    a practice note that cites an award is not that award, and a code that
    describes a fair dismissal has not found one.
    """
    notes: list[str] = []
    if stored_text is not None:
        text, title, status = stored_text, None, TextStatus.OK if stored_text.strip() else TextStatus.EMPTY
        notes.append("text reused from the main corpus text/ store")
    else:
        text, title, status, extract_notes = extract_text(body, content_type, url)
        notes.extend(extract_notes)
    text = strip_controls(text)
    pages = pdf_page_count(body) if stored_text is None else 0
    if pages >= 2 and status is TextStatus.OK:
        kept = substantive_chars(text, pages)
        if kept < MIN_SUBSTANTIVE_CHARS_PER_PAGE * pages:
            status = TextStatus.OCR_NEEDED
            notes.append(
                f"OCR NEEDED: the PDF text layer holds only running heads and footers "
                f"({kept} characters of other text over {pages} pages). The body is a "
                "scanned image, so nothing in it has been read or checked.")
    report = redact(text)
    decision = record_kind.is_decision
    meta = parse_judgment(text, title=title) if decision and text.strip() else JudgmentMeta()
    tagging = tag_document(text)
    dispute_type, reason = classify_dispute(tagging)
    outcome_label = OUTCOME_LABELS.get(tagging.outcome.value, "not_determined")
    if decision and outcome_label == "not_determined" and _FAIR_FINDING_RE.search(text):
        outcome_label = "dismissal_fair"
        notes.append("outcome read from a 'dismissal ... was fair' finding sentence")

    if status is TextStatus.OCR_NEEDED:
        parse_status = "ocr_needed"
    elif status is TextStatus.UNSUPPORTED_FORMAT:
        parse_status = "unsupported"
    elif not text.strip():
        parse_status = "failed"
        notes.append("no text extracted")
    elif decision and not (meta.case_no or meta.neutral_citation):
        # Selector drift or not an award at all. Never guess the fields.
        parse_status = "failed"
        notes.append("no case number or neutral citation found in the text")
    else:
        parse_status = "parsed"

    if not decision:
        dispute_type = UNCLASSIFIED
        reason = f"{record_kind.value}; not a decided dispute"
        outcome_label = "not_determined"
        if record_kind in {RecordKind.STATUTE, RecordKind.PRACTICE_NOTE}:
            notes.extend(_official_notes(text, title or title_hint, record_kind,
                                         readable=parse_status == "parsed"))

    return Parsed(
        text=text,
        redacted_text=report.text,
        title=title or meta.neutral_citation,
        text_status=status,
        meta=meta,
        tagging=tagging,
        dispute_type=dispute_type,
        dispute_reason=reason,
        outcome_label=outcome_label,
        parse_status=parse_status,
        notes=notes + meta.notes,
    )


def _official_notes(text: str, title: str | None, record_kind: RecordKind, *,
                    readable: bool) -> list[str]:
    """Draft status, number and title checks for an official publication (D-016, D-006).

    Checks run on whatever text there is, and a check that had nothing to read
    says so: a draft check over running heads alone must never read as "passed".
    """
    notes = check_claimed_numbers(title, text)
    if record_kind is not RecordKind.STATUTE:
        return notes
    notes += check_title_words(title, text)
    if not readable:
        notes.append("draft check NOT possible: the body text is not available (see the "
                     "parse status), so whether this instrument is final or a draft is unverified")
        return notes
    is_draft, why = detect_draft(text, title)
    notes.append(f"DRAFT: not in force ({why})" if is_draft else
                 "draft check passed: the title has no draft wording, and the opening has "
                 "none or also carries commencement wording (parse.statute.detect_draft)")
    return notes
