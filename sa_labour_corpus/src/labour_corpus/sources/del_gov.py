"""Department of Employment and Labour and gov.za adapter.

This is where the current consolidated labour statutes and the Codes of Good
Practice actually live in freely fetchable form. Both hosts serve an honestly
identified crawler, so this is the part of the corpus that fills up on a first
run.
"""

from __future__ import annotations

import re

DEL_DOCUMENT_PATTERNS: tuple[str, ...] = (
    r"\.pdf$",
    r"/Legislation/",
    r"/Documents/",
    r"Code[- ]of[- ]Good[- ]Practice",
)

GOVZA_DOCUMENT_PATTERNS: tuple[str, ...] = (
    r"/documents/",
    r"\.pdf$",
)

# The 2025 consolidated dismissal code replaced the former Schedule 8 and the
# separate operational-requirements code. Treat it as current unless a later
# gazette is found during a crawl.
CURRENT_DISMISSAL_CODE: dict[str, object] = {
    "name": "Code of Good Practice on Dismissal",
    "effective": "2025-09-04",
    "gazette": "GG 53294",
    "notice": "GN 3470",
    "replaces": [
        "Schedule 8 to the LRA (Code of Good Practice: Dismissal)",
        "Code of Good Practice on Dismissal Based on Operational Requirements",
    ],
    # The status of this record, stated honestly rather than assumed.
    "verified": False,
    "verification_note": (
        "UNVERIFIED. These details come from the project brief, not from any "
        "document this corpus holds. A live check on 2026-09-20 found that the "
        "Department of Employment and Labour publishes only a DRAFT Code of "
        "Good Practice on Dismissal for public comment in its Codes of Good "
        "Practice library -- its Labour Relations folder contains that draft "
        "and nothing else -- and no gazetted final code was located on gov.za. "
        "Do not rely on the effective date or the gazette and notice numbers "
        "above until the Government Gazette itself has been checked. "
        "A draft is not the law."
    ),
}

_CODE_RE = re.compile(
    r"Code of Good Practice[^.\n]{0,60}Dismissal|\bGG\s?53294\b|\bGN\s?3470\b",
    re.IGNORECASE,
)
_SUPERSEDING_RE = re.compile(
    r"\b(repeals?|replaces?|supersedes?|withdrawn|withdraws?)\b[^.\n]{0,80}"
    r"Code of Good Practice[^.\n]{0,60}Dismissal",
    re.IGNORECASE,
)

# Statutes this source is expected to yield, used to report what is missing.
EXPECTED_STATUTES: tuple[str, ...] = (
    "LRA 66 of 1995",
    "BCEA 75 of 1997",
    "EEA 55 of 1998",
    "NMWA 9 of 2018",
    "COIDA 130 of 1993",
    "UIA 63 of 2001",
    "OHSA 85 of 1993",
    "SDA 97 of 1998",
)


def is_dismissal_code(text: str, title: str | None = None) -> bool:
    """Whether a document is a Code of Good Practice on Dismissal."""
    return bool(_CODE_RE.search(f"{title or ''}\n{text[:40_000]}"))


def mentions_superseding_instrument(text: str) -> bool:
    """Flag text that appears to repeal or replace a dismissal code.

    Used to notice a later instrument rather than assuming the 2025 code is
    permanently current.
    """
    return bool(_SUPERSEDING_RE.search(text[:80_000]))
