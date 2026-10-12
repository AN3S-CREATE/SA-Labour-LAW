"""CCMA public-site adapter.

The CCMA publishes rules, guidelines, information sheets and referral forms.
It does not publish a bulk award API, and the individual awards that make up
the overwhelming majority of its work are not on its website at all.

Access note recorded on 2026-09-17: www.ccma.org.za answers HTTP 403 from its
Azure Application Gateway to any user-agent that identifies itself as an
automated client, while serving HTTP 200 to a desktop browser user-agent. The
site publishes no robots.txt (HTTP 404). Collecting it would require concealing
that the client is a crawler, so this adapter records the refusal instead.
"""

from __future__ import annotations

import re

CCMA_DOCUMENT_PATTERNS: tuple[str, ...] = (
    r"\.pdf$",
    r"/rules",
    r"/forms?",
    r"information[- ]sheet",
    r"/guidelines?",
    r"practice[- ]note",
    r"code[- ]of[- ]good[- ]practice",
)

# Instruments worth recognising when CCMA material does reach the corpus,
# whether crawled or dropped into inbox/ by the operator.
CCMA_INSTRUMENTS: dict[str, tuple[str, ...]] = {
    "CCMA Rules": (
        r"Rules for the Conduct of Proceedings before the CCMA",
        r"\bCCMA Rules\b",
    ),
    "CCMA Guidelines: Misconduct Arbitrations": (
        r"Guidelines?\s*[:\-]?\s*Misconduct Arbitration",
    ),
    "LRA Form 7.11 (referral of a dismissal dispute)": (
        r"\bLRA\s*7\.11\b",
        r"\bForm\s*7\.11\b",
    ),
    "LRA Form 7.13 (request for arbitration)": (
        r"\bLRA\s*7\.13\b",
        r"\bForm\s*7\.13\b",
    ),
    "CCMA Con-Arb process": (
        r"\bcon-?arb\b",
    ),
}

_INSTRUMENT_RES = {
    name: [re.compile(p, re.IGNORECASE) for p in patterns]
    for name, patterns in CCMA_INSTRUMENTS.items()
}

# Stated in COVERAGE.md and repeated wherever a reader might otherwise assume
# the corpus holds CCMA jurisprudence.
AWARD_ARCHIVE_NOTE = (
    "The CCMA has processed on the order of millions of referrals since 1996, "
    "and unfair dismissal is roughly half of recent referrals. Almost none of "
    "those individual awards are published on the free public web. This corpus "
    "cannot and does not claim completeness of CCMA jurisprudence."
)


def detect_instruments(text: str) -> list[str]:
    """Which recognised CCMA instruments a document appears to be."""
    found: list[str] = []
    sample = text[:100_000]
    for name, regexes in _INSTRUMENT_RES.items():
        if any(regex.search(sample) for regex in regexes):
            found.append(name)
    return found
