"""Government Gazette adapter.

The Gazette is the authoritative publication for codes, sectoral determinations
and commencement notices. Government Printing Works serves an identified
crawler and publishes no robots.txt, but its archive is paginated by
publication date and a full sweep is a very large crawl, so the source is
disabled by default and enabled deliberately with --sources.
"""

from __future__ import annotations

import re

GAZETTE_LINK_PATTERNS: tuple[str, ...] = (
    r"\.pdf$",
    r"/Gazettes/",
)

# Labour-relevant gazette subject matter.
LABOUR_GAZETTE_TERMS: tuple[str, ...] = (
    "labour relations",
    "basic conditions of employment",
    "employment equity",
    "code of good practice",
    "dismissal",
    "sectoral determination",
    "national minimum wage",
    "bargaining council",
    "collective agreement",
    "unemployment insurance",
    "compensation for occupational injuries",
    "mine health and safety",
    "occupational health and safety",
    "extension of collective agreement",
    "essential services",
)

_LABOUR_RE = re.compile(
    "|".join(re.escape(term) for term in LABOUR_GAZETTE_TERMS), re.IGNORECASE
)
_GG_RE = re.compile(r"\b(?:GG|Gazette(?:\s+No\.?)?)\s*(\d{4,6})\b", re.IGNORECASE)
_GN_RE = re.compile(
    r"\bG(?:eneral\s+)?N(?:otice)?\.?\s*(?:No\.?\s*)?(\d{2,5})\b", re.IGNORECASE
)


def is_labour_gazette(title: str, text: str = "") -> bool:
    """Whether a gazette item is labour-related. Keeps the crawl proportionate."""
    if _LABOUR_RE.search(title or ""):
        return True
    return bool(text and _LABOUR_RE.search(text[:40_000]))


def gazette_numbers(text: str) -> dict[str, str | None]:
    """Read the gazette and notice numbers out of the text."""
    gg = _GG_RE.search(text[:20_000])
    gn = _GN_RE.search(text[:20_000])
    return {
        "gazette_number": gg.group(1) if gg else None,
        "notice_number": gn.group(1) if gn else None,
    }
