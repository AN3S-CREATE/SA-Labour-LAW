"""Rule-based tagger.

Applies the dismissal ontology and the mining overlay to document text and
records, for every tag, the patterns that fired and the paragraph numbers that
contained them. Paragraph numbers are read from markers actually present in the
text. When a document carries no paragraph markers the paragraph list is empty
rather than invented.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

from ..models import Outcome, Remedy, TagEvidence
from .dismissal import (
    ALL_TAGS,
    CAT_FAIRNESS,
    CAT_PROCESS_DEFECT,
    Tag,
)
from .mining import ALL_MINING_TAGS, MINING_TAG_THRESHOLD

# Paragraph markers used in South African judgments and awards:
#   [12]      bracketed, the Labour Court norm
#   12.       numbered clause at line start
#   Para 12   explicit
_PARA_MARKER_RE = re.compile(
    r"(?m)^[ \t]*(?:\[(?P<bracket>\d{1,4})\]|(?P<plain>\d{1,4})\.(?!\d)|"
    r"(?:para(?:graph)?\.?\s+)(?P<named>\d{1,4}))",
    re.IGNORECASE,
)

# A year in brackets followed by a court code or a law-report series is a
# citation on its own line ("[2014] ZALAC 49", "[1998] 19 ILJ 1234 (LAC)",
# "[2001] 2 BLLR 105", "[1999] 1 All SA 1"), not paragraph 2014.
# Case-sensitive: court codes and report series are written in capitals.
_CITATION_AFTER_YEAR_RE = re.compile(
    r"\s*(?:\d{1,4}\s+)?(?:[A-Z]{2,}|[A-Z]\.[A-Z]|All\s+(?:ER|SA)\b)"
)


def _is_year(label: str) -> bool:
    return len(label) == 4 and 1900 <= int(label) <= 2099


MAX_EVIDENCE_PARAGRAPHS = 8
MAX_MATCHED_PATTERNS = 6


@dataclass(slots=True)
class ParagraphMap:
    """Maps character offsets in the text to the paragraph marker in force."""

    offsets: list[int]
    labels: list[str]

    @classmethod
    def build(cls, text: str) -> "ParagraphMap":
        offsets: list[int] = []
        labels: list[str] = []
        for match in _PARA_MARKER_RE.finditer(text):
            bracket = match.group("bracket")
            plain = match.group("plain")
            label = bracket or plain or match.group("named")
            if label is None:
                continue
            # "Act 66 of\n1995. He said": hard-wrapped text puts a year at the
            # start of a line. No clause is numbered 1900-2099.
            if plain is not None and _is_year(plain):
                continue
            if (bracket is not None and _is_year(bracket)
                    and _CITATION_AFTER_YEAR_RE.match(text, match.end())):
                continue
            offsets.append(match.start())
            labels.append(label)
        return cls(offsets=offsets, labels=labels)

    def label_at(self, offset: int) -> str | None:
        if not self.offsets:
            return None
        position = bisect_right(self.offsets, offset) - 1
        if position < 0:
            return None
        return self.labels[position]

    def __bool__(self) -> bool:
        return bool(self.offsets)


@dataclass(slots=True)
class TaggingResult:
    evidence: list[TagEvidence]
    dismissal_tags: list[str]
    sector_tags: list[str]
    outcome: Outcome
    remedy: Remedy
    confidence: float


def _compile(tag: Tag) -> list[re.Pattern[str]]:
    return [re.compile(pattern, re.IGNORECASE) for pattern in tag.patterns]


_COMPILED: dict[str, list[re.Pattern[str]]] = {
    tag.name: _compile(tag) for tag in (ALL_TAGS + ALL_MINING_TAGS)
}


def _match_tag(tag: Tag, text: str, paragraphs: ParagraphMap) -> TagEvidence | None:
    """Return evidence when tag patterns fire, else None."""
    matched: list[str] = []
    paras: list[str] = []
    for pattern in _COMPILED[tag.name]:
        found = pattern.search(text)
        if not found:
            continue
        matched.append(pattern.pattern)
        for occurrence in pattern.finditer(text):
            label = paragraphs.label_at(occurrence.start())
            if label and label not in paras:
                paras.append(label)
            if len(paras) >= MAX_EVIDENCE_PARAGRAPHS:
                break
    if not matched:
        return None
    if tag.require_all and len(matched) < len(tag.patterns):
        return None
    return TagEvidence(
        tag=tag.name,
        category=tag.category,
        matched_patterns=matched[:MAX_MATCHED_PATTERNS],
        paragraphs=paras[:MAX_EVIDENCE_PARAGRAPHS],
        reason=tag.description or f"pattern match for {tag.name}",
    )


# -- outcome heuristics -----------------------------------------------------
# Deliberately conservative. Anything ambiguous stays "unknown"; the corpus is
# a research aid and a wrong outcome label is worse than an absent one.
_AUTO_UNFAIR_RE = re.compile(
    r"\bautomatic(ally)? unfair\b", re.IGNORECASE)
_SUBSTANTIVE_UNFAIR_RE = re.compile(
    r"\bsubstantively unfair\b|\bdismissal (was|is) substantively unfair\b",
    re.IGNORECASE)
_PROCEDURAL_UNFAIR_RE = re.compile(
    r"\bprocedurally unfair\b|\bdismissal (was|is) procedurally unfair\b",
    re.IGNORECASE)
_BOTH_UNFAIR_RE = re.compile(
    r"\bboth substantively and procedurally unfair\b|"
    r"\bsubstantively and procedurally unfair\b", re.IGNORECASE)
_FAIR_RE = re.compile(
    r"\bdismissal (was|is) (substantively and procedurally )?fair\b|"
    r"\bfound the dismissal to be fair\b", re.IGNORECASE)
_REVIEW_SET_ASIDE_RE = re.compile(
    r"\baward is (hereby )?(reviewed and )?set aside\b|"
    r"\bthe award .{0,40}is set aside\b|"
    r"\breview application is (granted|upheld)\b", re.IGNORECASE)
_REVIEW_DISMISSED_RE = re.compile(
    r"\breview application is dismissed\b|"
    r"\bapplication for review is dismissed\b", re.IGNORECASE)

_REINSTATE_ORDER_RE = re.compile(
    r"\breinstat(e|ed|ement)\b.{0,80}\b(order(ed)?|with retrospective effect)\b|"
    r"\b(order(ed)?|directed)\b.{0,80}\breinstat", re.IGNORECASE)
_REEMPLOY_ORDER_RE = re.compile(r"\bre-?employ(ed|ment)\b", re.IGNORECASE)
_COMPENSATION_ORDER_RE = re.compile(
    r"\bcompensation\b.{0,80}\bequivalent to\b|"
    r"\bpay(?:ment)? (of|to) .{0,40}\bcompensation\b|"
    r"\b\d{1,2}\s*months(?:'|’)?\s*(remuneration|salary|compensation)\b",
    re.IGNORECASE)


def _infer_outcome(text: str) -> Outcome:
    if _BOTH_UNFAIR_RE.search(text):
        return Outcome.BOTH
    if _AUTO_UNFAIR_RE.search(text):
        return Outcome.AUTOMATICALLY_UNFAIR
    substantive = bool(_SUBSTANTIVE_UNFAIR_RE.search(text))
    procedural = bool(_PROCEDURAL_UNFAIR_RE.search(text))
    if substantive and procedural:
        return Outcome.BOTH
    if substantive:
        return Outcome.UNFAIR_SUBSTANTIVE
    if procedural:
        return Outcome.UNFAIR_PROCEDURAL
    if _REVIEW_SET_ASIDE_RE.search(text):
        return Outcome.REVIEW_SET_ASIDE
    if _REVIEW_DISMISSED_RE.search(text):
        return Outcome.REVIEW_UPHELD
    if _FAIR_RE.search(text):
        return Outcome.FAIR
    return Outcome.UNKNOWN


def _infer_remedy(text: str) -> Remedy:
    if _REINSTATE_ORDER_RE.search(text):
        return Remedy.REINSTATEMENT
    if _REEMPLOY_ORDER_RE.search(text):
        return Remedy.REEMPLOYMENT
    if _COMPENSATION_ORDER_RE.search(text):
        return Remedy.COMPENSATION
    return Remedy.UNKNOWN


def _confidence(text: str, evidence: list[TagEvidence], has_paragraphs: bool) -> float:
    """Confidence in the tagging, not in any legal conclusion."""
    if not text.strip():
        return 0.0
    score = 0.2
    if len(text) > 2_000:
        score += 0.2
    if evidence:
        score += min(0.3, 0.03 * len(evidence))
    if has_paragraphs:
        score += 0.2
    if any(e.category == CAT_FAIRNESS for e in evidence):
        score += 0.1
    return round(min(score, 1.0), 2)


def tag_document(text: str) -> TaggingResult:
    """Apply the full ontology to text and return tags plus their evidence."""
    if not text or not text.strip():
        return TaggingResult([], [], [], Outcome.UNKNOWN, Remedy.UNKNOWN, 0.0)

    paragraphs = ParagraphMap.build(text)

    evidence: list[TagEvidence] = []
    for tag in ALL_TAGS:
        found = _match_tag(tag, text, paragraphs)
        if found:
            evidence.append(found)

    mining_evidence: list[TagEvidence] = []
    for tag in ALL_MINING_TAGS:
        found = _match_tag(tag, text, paragraphs)
        if found:
            mining_evidence.append(found)

    sector_tags: list[str] = []
    if len(mining_evidence) >= MINING_TAG_THRESHOLD:
        sector_tags.append("mining")
        names = ", ".join(e.tag for e in mining_evidence)
        evidence.append(
            TagEvidence(
                tag="mining",
                category="sector_decision",
                matched_patterns=[p for e in mining_evidence for p in e.matched_patterns][:MAX_MATCHED_PATTERNS],
                # Numeric order: as strings, "10" and "101" sort before "2"
                # and the slice drops the low paragraphs. Labels are digits.
                paragraphs=sorted(
                    {p for e in mining_evidence for p in e.paragraphs}, key=int
                )[:MAX_EVIDENCE_PARAGRAPHS],
                reason=(
                    f"sector=mining applied: {len(mining_evidence)} distinct mining "
                    f"tags matched (threshold {MINING_TAG_THRESHOLD}): {names}"
                ),
            )
        )
    evidence.extend(mining_evidence)

    outcome = _infer_outcome(text)
    remedy = _infer_remedy(text)

    if outcome is Outcome.AUTOMATICALLY_UNFAIR:
        hits = [e for e in evidence if e.tag in {"automatically_unfair", "s187_automatically_unfair"}]
        evidence.append(
            TagEvidence(
                tag="automatically_unfair",
                category="outcome_decision",
                matched_patterns=[p for e in hits for p in e.matched_patterns][:MAX_MATCHED_PATTERNS],
                paragraphs=sorted(
                    {p for e in hits for p in e.paragraphs}, key=int
                )[:MAX_EVIDENCE_PARAGRAPHS],
                reason=(
                    "outcome=automatically_unfair applied because the text uses the "
                    "phrase 'automatically unfair'; verify against s187 in the source."
                ),
            )
        )

    dismissal_tags = sorted({
        e.tag for e in evidence
        if e.category not in {"sector_decision", "outcome_decision"}
    })

    return TaggingResult(
        evidence=evidence,
        dismissal_tags=dismissal_tags,
        sector_tags=sector_tags,
        outcome=outcome,
        remedy=remedy,
        confidence=_confidence(text, evidence, bool(paragraphs)),
    )


def process_defect_checklist(evidence: list[TagEvidence]) -> list[str]:
    """Employee-side counter-checks that fired for this document."""
    return sorted({e.tag for e in evidence if e.category == CAT_PROCESS_DEFECT})
