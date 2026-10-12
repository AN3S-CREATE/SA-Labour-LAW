"""Knowledge compilation: case cards, scenarios and strategies.

Deterministic and rule-based -- no language model is involved, so nothing here
can paraphrase an award into something it did not say.

* A **case card** holds what the source states: its citation, the finding
  sentence quoted verbatim with its paragraph locator, the issues the tagger
  found with their paragraph evidence, and the legislation the text cites.
* A **scenario** exists only where two or more parsed cards share a dispute
  type and a material issue. No cluster is ever made from a single card.
* A **strategy** is a set of research notes, each tied to at least one
  ``native_id``. ``high`` confidence is used only for a verbatim quoted
  finding; everything else is ``medium`` or ``low``.

All text passes through ``minimise`` and must pass ``assert_minimised``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from ..ontology.dismissal import ALL_TAGS
from ..ontology.tagger import ParagraphMap
from .allowlist import RecordKind
from .catalogue import KnowledgeUnit, record_key
from .extract import Parsed
from .minimise import MinimisationError, assert_minimised, minimise

FOOTER = "Not legal advice. Verify against the full award and current CCMA Rules."

RELATED_AUTHORITY_NOTE = (
    "Labour Court / Labour Appeal Court judgment indexed as a related authority. "
    "It is not a CCMA award."
)

_TAG_INFO = {tag.name: tag for tag in ALL_TAGS}
_MATERIAL_CATEGORIES = frozenset({"misconduct", "incapacity", "operational", "process_defect", "fairness"})

# A finding sentence: the decision-maker's own conclusion on fairness or review.
_FINDING_RE = re.compile(
    r"(?:dismissal|termination)[^.]{0,120}?\b(?:was|is|to be|were)\s+"
    r"(?:both\s+)?(?:substantively|procedurally|automatically)?\s*(?:and\s+(?:substantively|procedurally)\s+)?"
    r"(?:un)?fair\b"
    r"|\b(?:substantively|procedurally)\s+(?:and\s+(?:substantively|procedurally)\s+)?(?:un)?fair\b"
    r"|\baward\s+is\s+(?:hereby\s+)?(?:reviewed\s+and\s+)?set\s+aside\b"
    r"|\breview\s+application\s+is\s+dismissed\b",
    re.IGNORECASE,
)
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")

_LEGISLATION_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Labour Relations Act 66 of 1995",
     re.compile(r"\bLabour Relations Act\b|\bthe LRA\b|\bLRA\b")),
    ("Basic Conditions of Employment Act 75 of 1997",
     re.compile(r"\bBasic Conditions of Employment Act\b|\bBCEA\b")),
    ("Employment Equity Act 55 of 1998",
     re.compile(r"\bEmployment Equity Act\b|\bEEA\b")),
    ("Code of Good Practice: Dismissal (Schedule 8 to the LRA)",
     re.compile(r"\bSchedule 8\b|Code of Good Practice\s*:?\s*Dismissal", re.I)),
    ("CCMA Rules",
     re.compile(r"\bCCMA Rules\b|Rules for the Conduct of Proceedings", re.I)),
    ("Occupational Health and Safety Act 85 of 1993",
     re.compile(r"\bOccupational Health and Safety Act\b")),
    ("Mine Health and Safety Act 29 of 1996",
     re.compile(r"\bMine Health and Safety Act\b|\bMHSA\b")),
)
_SECTION_RE = re.compile(
    r"\b(?:section|s)\s?(18[5-9]A?|19[0-9]|145|158|198[A-D]?)(?:\s?\(\d+\))?(?:\s?\([a-z]\))?",
    re.IGNORECASE,
)

_FAVOURS_EMPLOYEE = {
    "substantively_unfair", "procedurally_unfair", "substantively_and_procedurally_unfair",
    "automatically_unfair",
}
_FAVOURS_EMPLOYER = {"dismissal_fair"}


def _uid(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]


def _clean(text: str, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return minimise(text)


@dataclass(slots=True)
class Finding:
    quote: str
    paragraph: str | None


def find_holding(text: str) -> Finding | None:
    """The last finding sentence in the text, quoted verbatim.

    The last one is preferred: awards summarise the evidence first and state
    the conclusion at the end, usually under an 'Award' or 'Order' heading.
    """
    paragraphs = ParagraphMap.build(text)
    matches = list(_FINDING_RE.finditer(text))
    if not matches:
        return None
    match = matches[-1]
    start = text.rfind("\n", 0, match.start()) + 1
    for boundary in _SENTENCE_END_RE.finditer(text, start, match.start()):
        start = boundary.end()
    end_match = re.search(r"[.!?](?:\s|$)", text[match.end():])
    end = match.end() + (end_match.end() if end_match else 0)
    quote = text[start:end].strip()
    quote = re.sub(r"^\[\d{1,4}\]\s*", "", quote)   # the locator is carried separately
    if len(quote) > 500:
        quote = text[max(start, match.start() - 200): match.end() + 100].strip()
    return Finding(quote=quote, paragraph=paragraphs.label_at(match.start()))


def cited_legislation(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, regex in _LEGISLATION_RES:
        if regex.search(text):
            out.append({"instrument": name})
    sections = sorted({m.group(0).strip() for m in _SECTION_RE.finditer(text)})[:15]
    if sections and out and out[0]["instrument"].startswith("Labour Relations Act"):
        out[0]["sections_mentioned"] = sections
    return out


def _citation_entry(source: str, native_id: str, parsed_citation: str | None,
                    url: str | None, paragraph: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"source": source, "native_id": native_id, "citation": parsed_citation}
    if url:
        entry["source_url"] = url
    if paragraph:
        entry["paragraph"] = paragraph
    return entry


def _issues(parsed: Parsed) -> list[dict[str, Any]]:
    issues = []
    for ev in parsed.tagging.evidence:
        if ev.category in {"sector_decision", "outcome_decision"}:
            continue
        tag = _TAG_INFO.get(ev.tag)
        issues.append({
            "tag": ev.tag,
            "category": ev.category,
            "label": tag.description if tag and getattr(tag, "description", None) else ev.tag.replace("_", " "),
            "paragraphs": ev.paragraphs,
        })
    return issues


def build_record_units(
    source: str, native_id: str, record_kind: RecordKind, forum: str | None,
    source_url: str | None, parsed: Parsed,
) -> list[KnowledgeUnit]:
    """The case card and the strategy for one parsed record.

    Practice material, practice notes and statutes yield no units: they decide
    nothing.
    """
    if parsed.parse_status != "parsed" or not record_kind.is_decision:
        return []

    fk = record_key(source, native_id)
    citation = parsed.citation
    holding = find_holding(parsed.text)
    issues = _issues(parsed)
    legislation = cited_legislation(parsed.text)
    key_paragraphs = [
        {"paragraph": p, "issue": i["tag"]} for i in issues for p in i["paragraphs"]
    ][:12]

    card_body: dict[str, Any] = {
        "record_kind": record_kind.value,
        "citation": citation,
        "neutral_citation": parsed.meta.neutral_citation,
        "case_number": parsed.meta.case_no,
        "forum": forum,
        "year": parsed.meta.year,
        "date_delivered": parsed.meta.date_delivered,
        "dispute_type": parsed.dispute_type,
        "dispute_type_basis": parsed.dispute_reason,
        "issues": issues,
        "holding_as_stated": (
            {"quote": _clean(holding.quote), "paragraph": holding.paragraph}
            if holding else None
        ),
        "outcome_label": parsed.outcome_label,
        "outcome_basis": "rule-based reading of the text; confirm in the source",
        "key_paragraphs": key_paragraphs,
        "legislation_cited": legislation,
        "disclaimer": FOOTER,
    }
    if record_kind is RecordKind.RELATED_AUTHORITY:
        card_body["note"] = RELATED_AUTHORITY_NOTE

    title = _clean(parsed.title or citation or native_id, 200)
    card = KnowledgeUnit(
        id=_uid("case_card", fk, json.dumps(card_body, sort_keys=True)),
        kind="case_card",
        title=f"Case card: {title}",
        body=card_body,
        citations=[_citation_entry(source, native_id, citation, source_url,
                                   holding.paragraph if holding else None)],
        confidence="high" if holding and citation else "medium",
        record_fk=fk,
        dispute_type=parsed.dispute_type,
    )

    bullets: list[dict[str, Any]] = []
    if holding:
        bullets.append({
            "note": (
                f"The decision-maker found: “{_clean(holding.quote, 400)}”"
                + (f" (para {holding.paragraph})" if holding.paragraph else "")
                + ". Compare the facts that led there with your own before relying on it."
            ),
            "native_ids": [native_id],
            "confidence": "high",
            "basis": "verbatim quotation of the published finding",
        })
    defects = [i for i in issues if i["category"] == "process_defect"]
    for issue in defects[:6]:
        paras = ", ".join(issue["paragraphs"][:4])
        bullets.append({
            "note": (
                f"The record discusses {issue['label']}"
                + (f" (paras {paras})" if paras else "")
                + ". A representative might consider whether the same point arises "
                  "on their facts and how it was evidenced here."
            ),
            "native_ids": [native_id],
            "confidence": "medium",
            "basis": "rule-based issue tag with paragraph evidence",
        })
    substantive = [i for i in issues if i["category"] in {"misconduct", "incapacity", "operational"}]
    for issue in substantive[:4]:
        bullets.append({
            "note": (
                f"Issue raised: {issue['label']}. Read the cited paragraphs for how "
                "the evidence on this point was weighed."
            ),
            "native_ids": [native_id],
            "confidence": "low",
            "basis": "rule-based issue tag; no finding quoted",
        })
    units = [card]
    if bullets:
        strategy_body = {
            "applies_to": citation,
            "dispute_type": parsed.dispute_type,
            "notes": bullets,
            "footer": FOOTER,
        }
        if record_kind is RecordKind.RELATED_AUTHORITY:
            strategy_body["note"] = RELATED_AUTHORITY_NOTE
        units.append(KnowledgeUnit(
            id=_uid("strategy", fk, json.dumps(strategy_body, sort_keys=True)),
            kind="strategy",
            title=f"Research notes: {title}",
            body=strategy_body,
            citations=[_citation_entry(source, native_id, citation, source_url)],
            confidence="high" if any(b["confidence"] == "high" for b in bullets) else "medium",
            record_fk=fk,
            dispute_type=parsed.dispute_type,
        ))
    return [enforce_minimised(u) for u in units]


def enforce_minimised(unit: KnowledgeUnit) -> KnowledgeUnit:
    """Raise MinimisationError if any identifier survived. Never silently pass."""
    assert_minimised(unit.title)
    assert_minimised(json.dumps(unit.body, ensure_ascii=False))
    return unit


# -- scenarios -----------------------------------------------------------

@dataclass(slots=True)
class CardView:
    source: str
    native_id: str
    citation: str | None
    dispute_type: str
    outcome_label: str
    tags: list[str]
    record_kind: str


def cards_from_rows(rows: list[Any]) -> list[CardView]:
    views = []
    for row in rows:
        body = json.loads(row["body"])
        fk = row["record_fk"] or ":"
        source, native_id = fk.split(":", 1)
        views.append(CardView(
            source=source,
            native_id=native_id,
            citation=body.get("citation"),
            dispute_type=body.get("dispute_type") or "unclassified",
            outcome_label=body.get("outcome_label") or "not_determined",
            tags=[i["tag"] for i in body.get("issues", [])
                  if i.get("category") in _MATERIAL_CATEGORIES],
            record_kind=body.get("record_kind", ""),
        ))
    return views


def _polarity(label: str) -> str | None:
    if label in _FAVOURS_EMPLOYEE:
        return "employee"
    if label in _FAVOURS_EMPLOYER:
        return "employer"
    return None


def build_scenarios(cards: list[CardView], min_cards: int = 2) -> list[KnowledgeUnit]:
    """Group cards sharing a dispute type and a material issue.

    A group needs ``min_cards`` distinct records. Unclassified cards and
    generic fairness tags alone never form a scenario.
    """
    groups: dict[tuple[str, str], list[CardView]] = defaultdict(list)
    for card in cards:
        if card.dispute_type == "unclassified":
            continue
        for tag in set(card.tags):
            if tag in {"substantive_fairness", "procedural_fairness"}:
                continue
            groups[(card.dispute_type, tag)].append(card)

    units: list[KnowledgeUnit] = []
    for (dispute_type, issue), members in sorted(groups.items()):
        members = sorted({(c.source, c.native_id): c for c in members}.values(),
                         key=lambda c: (c.source, c.native_id))
        if len(members) < min_cards:
            continue
        outcomes = Counter(c.outcome_label for c in members)
        polarities = Counter(p for p in (_polarity(c.outcome_label) for c in members) if p)
        majority = polarities.most_common(1)[0][0] if polarities else None
        contra = [
            {"native_id": c.native_id, "citation": c.citation, "outcome_label": c.outcome_label}
            for c in members
            if majority and _polarity(c.outcome_label) not in (None, majority)
        ]
        shared = Counter(t for c in members for t in set(c.tags))
        elements = [
            {"tag": t, "records": n} for t, n in shared.most_common()
            if n >= 2 and t != issue
        ][:10]
        label = _TAG_INFO[issue].description if issue in _TAG_INFO and getattr(_TAG_INFO[issue], "description", None) else issue.replace("_", " ")
        body = {
            "dispute_type": dispute_type,
            "material_issue": issue,
            "fact_pattern": (
                f"{dispute_type.replace('_', ' ').capitalize()} matters in which "
                f"{label} was in issue, drawn from {len(members)} catalogued records."
            ),
            "elements_that_mattered": elements,
            "outcome_range_observed": dict(outcomes),
            "contra_records": contra,
            "records": [
                {"native_id": c.native_id, "citation": c.citation,
                 "outcome_label": c.outcome_label, "record_kind": c.record_kind}
                for c in members
            ],
            "observation_scope": (
                "Outcomes are as observed in these catalogued records only; they are "
                "not a statistic about CCMA outcomes generally."
            ),
            "footer": FOOTER,
        }
        unit = KnowledgeUnit(
            id=_uid("scenario", dispute_type, issue, json.dumps(body, sort_keys=True)),
            kind="scenario",
            title=_clean(f"Scenario: {dispute_type.replace('_', ' ')} / {label}", 200),
            body=body,
            citations=[
                _citation_entry(c.source, c.native_id, c.citation, None) for c in members
            ],
            confidence="medium" if len(members) >= 3 else "low",
            record_fk=None,
            dispute_type=dispute_type,
        )
        try:
            units.append(enforce_minimised(unit))
        except MinimisationError:
            continue
    return units
