"""Bargaining-council adapter.

Bargaining councils have s191 dismissal jurisdiction in their sectors, so their
constitutions and main agreements matter to an unfair-dismissal analysis. Each
council runs its own website with its own terms, so councils are registered
individually and only after their robots policy has been checked.

No council is enabled by default. Adding one is a deliberate act: confirm that
the specific pages are public and robots-allowed, then add an entry to
sources.yaml.
"""

from __future__ import annotations

import re

# Councils whose sectors most often produce dismissal disputes. Listed as
# candidates for the operator to verify and enable, not as crawl targets.
CANDIDATE_COUNCILS: tuple[tuple[str, str], ...] = (
    ("MEIBC", "Metal and Engineering Industries Bargaining Council"),
    ("MIBCO", "Motor Industry Bargaining Council"),
    ("NBCRFLI", "National Bargaining Council for the Road Freight and Logistics Industry"),
    ("BCCEI", "Bargaining Council for the Civil Engineering Industry"),
    ("NBCCMI", "National Bargaining Council for the Clothing Manufacturing Industry"),
    ("NBCWPS", "National Bargaining Council for the Wood and Paper Sector"),
    ("SABC", "Statutory councils accredited by the CCMA for dismissal arbitration"),
)

BARGAINING_LINK_PATTERNS: tuple[str, ...] = (
    r"\.pdf$",
    r"constitution",
    r"main[- ]agreement",
    r"collective[- ]agreement",
    r"dispute[- ]resolution",
)

_COUNCIL_RE = re.compile(
    r"\b(MEIBC|MIBCO|NBCRFLI|BCCEI|NBCCMI|NBCWPS)\b|"
    r"\bbargaining council for the ([A-Za-z ,]{5,80})\b",
    re.IGNORECASE,
)


def detect_council(text: str) -> str | None:
    """Return a council identifier found in the text, else None."""
    match = _COUNCIL_RE.search(text[:40_000])
    if not match:
        return None
    return (match.group(1) or match.group(2) or "").strip() or None


def forum_for(council: str) -> str:
    """Forum code for a bargaining council, e.g. BC:MEIBC."""
    return f"BC:{council}"
