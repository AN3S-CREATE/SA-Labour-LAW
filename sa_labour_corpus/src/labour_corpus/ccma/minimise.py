"""POPIA minimisation for the knowledge layer.

Stricter than ``parse.redaction``: that module keeps a record faithful and so
redacts only validated identifiers. Knowledge units are derived research notes
and need no identifiers at all, so here *any* 13-digit run, any e-mail, any
phone-shaped number and any street or postal address is removed, and
``assert_minimised`` refuses a unit that still carries one.

Party names are not removed: they are part of how a published award is cited.
"""

from __future__ import annotations

import re

PLACEHOLDER = "[removed]"

_PATTERNS: dict[str, re.Pattern[str]] = {
    "id_number": re.compile(r"(?<!\d)\d{6}\s?\d{4}\s?\d{3}(?!\d)"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+\s?(?:@|\[at\]|\(at\))\s?[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "phone": re.compile(
        r"(?<![\w/])(?:\+?27|0)[\s\-()]*\d{2}[\s\-()]*\d{3}[\s\-]*\d{4}(?![\w/])"
    ),
    "street_address": re.compile(
        r"\b\d{1,5}[A-Za-z]?\s+(?:[A-Z][A-Za-z'\-]+\s+){1,4}"
        r"(?:Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Crescent|Cres|Lane|Close|Way|"
        r"Boulevard|Place|Pl|Rylaan|Straat|Weg)\b\.?(?:[ ,]+[A-Z][A-Za-z\-]+){0,3}(?:[ ,]+\d{4})?"
    ),
    "po_box": re.compile(r"\b(?:P\.?\s?O\.?\s?Box|Posbus|Private\s+Bag)\s*\d+[^\n]{0,40}?(?:\d{4}|$)", re.I),
}


def minimise(text: str) -> str:
    if not text:
        return text
    out = text
    for pattern in _PATTERNS.values():
        out = pattern.sub(PLACEHOLDER, out)
    return out


def residual_identifiers(text: str) -> list[str]:
    """Names of identifier kinds still present in text."""
    return [name for name, pattern in _PATTERNS.items() if pattern.search(text or "")]


class MinimisationError(ValueError):
    """A knowledge unit still carries a personal identifier."""


def assert_minimised(text: str) -> None:
    found = residual_identifiers(text)
    if found:
        raise MinimisationError(f"personal identifiers remain: {', '.join(found)}")
