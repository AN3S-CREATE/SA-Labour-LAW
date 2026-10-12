"""Pydantic models describing the parsed-document contract.

Every ``parsed/{doc_id}.json`` file on disk is a serialised :class:`ParsedDoc`.
Fields that a parser could not establish are recorded as ``None`` or
``"unknown"`` -- never guessed. A citation string is either extracted from the
source text or absent.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class DocType(str, Enum):
    STATUTE = "statute"
    AMENDMENT = "amendment"
    REGULATION = "regulation"
    CODE_OF_GOOD_PRACTICE = "code_of_good_practice"
    CCMA_RULE = "ccma_rule"
    PRACTICE_MANUAL = "practice_manual"
    FORM = "form"
    GUIDELINE = "guideline"
    JUDGMENT = "judgment"
    AWARD = "award"
    BARGAINING_COUNCIL_AGREEMENT = "bargaining_council_agreement"
    GAZETTE = "gazette"
    OTHER = "other"


class Outcome(str, Enum):
    FAIR = "fair"
    UNFAIR_SUBSTANTIVE = "unfair_substantive"
    UNFAIR_PROCEDURAL = "unfair_procedural"
    BOTH = "both"
    AUTOMATICALLY_UNFAIR = "automatically_unfair"
    REVIEW_UPHELD = "review_upheld"
    REVIEW_SET_ASIDE = "review_set_aside"
    UNKNOWN = "unknown"


class Remedy(str, Enum):
    REINSTATEMENT = "reinstatement"
    REEMPLOYMENT = "reemployment"
    COMPENSATION = "compensation"
    NONE = "none"
    UNKNOWN = "unknown"


class Provenance(str, Enum):
    """How this copy of the document was obtained.

    Distinct from ``canonical_url``, which says where the document authoritatively
    lives. A legacy or operator-supplied copy may carry a correct canonical URL
    while never having been fetched by this crawler, and a reader must be able to
    tell the difference.
    """

    CRAWLED = "crawled"
    OPERATOR_SUPPLIED = "operator_supplied"
    LEGACY_LOCAL_HARVEST = "legacy_local_harvest"


class TextStatus(str, Enum):
    OK = "ok"
    OCR_NEEDED = "ocr_needed"            # a scan: no text layer, OCR would help
    UNSUPPORTED_FORMAT = "unsupported_format"  # readable format, reader missing
    EMPTY = "empty"
    SKIPPED_OVERSIZE = "skipped_oversize"


class ParseStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


class Quote(BaseModel):
    """A short grounded excerpt. ``paragraph`` is only set when the source
    text actually carried a paragraph marker -- it is never inferred."""

    model_config = ConfigDict(extra="forbid")

    text: str
    paragraph: str | None = None
    char_offset: int | None = None


class TagEvidence(BaseModel):
    """Why the rule-based tagger applied a tag."""

    model_config = ConfigDict(extra="forbid")

    tag: str
    category: str
    matched_patterns: list[str] = Field(default_factory=list)
    paragraphs: list[str] = Field(default_factory=list)
    reason: str = ""


class ParsedDoc(BaseModel):
    """The full on-disk contract for one harvested document."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: str = SCHEMA_VERSION

    # Provenance ---------------------------------------------------------
    doc_id: str
    source_id: str
    canonical_url: str
    retrieved_at: str
    http_status: int | None = None
    content_sha256: str
    content_type: str | None = None
    bytes: int | None = None

    # Classification -----------------------------------------------------
    doc_type: DocType = DocType.OTHER
    forum: str | None = None
    jurisdiction: str = "ZA"
    provenance: Provenance = Provenance.CRAWLED
    # True when the instrument is a draft or bill, i.e. not in force. A draft
    # has no legal effect and must never be read as the operative instrument.
    is_draft: bool = False
    draft_reason: str = ""

    # Bibliographic ------------------------------------------------------
    title: str | None = None
    citation: str | None = None
    case_no: str | None = None
    neutral_citation: str | None = None
    parties: list[str] = Field(default_factory=list)
    date_delivered: str | None = None
    judges_or_commissioner: list[str] = Field(default_factory=list)
    year: int | None = None

    # Subject tagging ----------------------------------------------------
    sector_tags: list[str] = Field(default_factory=list)
    dismissal_tags: list[str] = Field(default_factory=list)
    tag_evidence: list[TagEvidence] = Field(default_factory=list)
    outcome: Outcome = Outcome.UNKNOWN
    remedy: Remedy = Remedy.UNKNOWN

    # Text ---------------------------------------------------------------
    language: str = "unknown"
    text_status: TextStatus = TextStatus.OK
    text_chars: int = 0
    pi_redacted: bool = False
    # True when the text is a local OCR transcription, not a published text layer.
    ocr_applied: bool = False
    redaction_counts: dict[str, int] = Field(default_factory=dict)
    quotes: list[Quote] = Field(default_factory=list)

    # Quality ------------------------------------------------------------
    parse_status: ParseStatus = ParseStatus.OK
    parser_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    parser_notes: list[str] = Field(default_factory=list)
    license_note: str = ""


def make_doc_id(canonical_url: str, content_sha256: str) -> str:
    """Stable document id: hash of canonical URL plus the body checksum.

    Re-fetching identical bytes from the same URL yields the same id, which is
    what makes the pipeline idempotent. Changed bytes at the same URL yield a
    new id, i.e. a new version of the document.
    """
    digest = hashlib.sha256()
    digest.update(canonical_url.strip().encode("utf-8"))
    digest.update(b"\n")
    digest.update(content_sha256.encode("utf-8"))
    return digest.hexdigest()[:32]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


GapKind = Literal[
    "robots_disallow",
    "challenge_blocked",
    "http_error",
    "paid_source",
    "content_signal_opt_out",
    "not_published_free",
    "oversize",
    "parse_failed",
    "policy_excluded",
]


class Gap(BaseModel):
    """A machine-readable statement of something the corpus does NOT contain."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    # Typed so a misspelt kind fails loudly instead of producing a gap that
    # COVERAGE.md has no label for.
    kind: GapKind
    url: str | None = None
    detail: str = ""
    observed_at: str = Field(default_factory=utc_now_iso)
