"""SAFLII adapter: court databases, year indexes and consolidated legislation.

SAFLII organises each court database as a page of year links, each year as a
list of judgments. This adapter walks those published indexes. It never
enumerates case numbers.

Access note recorded on 2026-09-17: www.saflii.org answers every plain HTTP
request, including /robots.txt, with an HTTP 403 Cloudflare managed challenge.
The crawler therefore attempts the source honestly, records the refusal as a
coverage gap, and moves on. Nothing here tries to satisfy a challenge.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .links import extract_links, filter_links

# A SAFLII year index looks like /za/cases/ZALCJHB/2024/
_YEAR_INDEX_RE = re.compile(r"/(?:za|zw|na)/(?:cases|legis)/[A-Za-z_]+/(?P<year>(?:19|20)\d{2})/?$")

# A judgment lands at /za/cases/ZALCJHB/2024/212.html
_JUDGMENT_RE = re.compile(
    r"/(?:za|zw|na)/cases/(?P<court>[A-Z]+)/(?P<year>(?:19|20)\d{2})/(?P<num>\d{1,5})\.html?$",
    re.IGNORECASE,
)

# SAFLII offers the original file alongside the HTML rendering.
_ORIGINAL_FILE_RE = re.compile(r"\.(?:pdf|rtf|doc|docx)$", re.IGNORECASE)

# Labour and mining relevance filter, used for ZACC and ZASCA where the whole
# database must not be ingested.
LABOUR_FILTER_TERMS: tuple[str, ...] = (
    "dismiss", "unfair dismissal", "labour", "employee", "employer",
    "section 186", "section 187", "section 188", "section 189", "section 189a",
    "retrench", "operational requirements", "misconduct", "incapacity",
    "constructive dismissal", "automatically unfair", "discrimination",
    "whistle", "protected disclosure", "sidumo", "item 7",
    "code of good practice", "audi alteram partem", "precautionary suspension",
    "inconsistency", "progressive discipline", "tes", "labour broker",
    "section 198", "trade union", "collective agreement", "strike",
    "bargaining council", "ccma", "arbitration award",
    "mine", "mhsa", "mine health and safety", "overseer", "stoping",
    "platinum", "coal", "gold mine", "safety rule", "fatal",
    "section 54", "section 23", "colliery", "mineworker",
)

_FILTER_RE = re.compile(
    "|".join(re.escape(term) for term in LABOUR_FILTER_TERMS), re.IGNORECASE
)


def find_year_links(html: str | bytes, base_url: str, years: tuple[int, int]) -> list[str]:
    """Year index URLs for a court database, restricted to the year window."""
    start, end = years
    found: list[str] = []
    for url, _text in extract_links(html, base_url):
        match = _YEAR_INDEX_RE.search(urlparse(url).path)
        if not match:
            continue
        year = int(match.group("year"))
        if start <= year <= end and url not in found:
            found.append(url)
    return sorted(found)


def find_judgment_links(html: str | bytes, base_url: str) -> list[tuple[str, str]]:
    """Judgment URLs and their link text from a year index page."""
    out: list[tuple[str, str]] = []
    for url, text in extract_links(html, base_url):
        if _JUDGMENT_RE.search(urlparse(url).path):
            out.append((url, text))
    return out


def find_original_files(html: str | bytes, base_url: str) -> list[str]:
    """The 'Download original files' PDF/RTF links on a judgment page."""
    out: list[str] = []
    for url, text in extract_links(html, base_url):
        path = urlparse(url).path
        if _ORIGINAL_FILE_RE.search(path) or "original" in text.lower():
            if _ORIGINAL_FILE_RE.search(path) and url not in out:
                out.append(url)
    return out


def find_legislation_links(html: str | bytes, base_url: str, host: str) -> list[tuple[str, str]]:
    """Consolidated-act links from the SAFLII legislation index."""
    links = extract_links(html, base_url)
    return filter_links(links, host=host, patterns=[r"/legis/consol_act/"])


def is_labour_relevant(title: str, text: str = "") -> bool:
    """Whether a judgment is labour or mining related.

    Applied to ZACC and ZASCA so the whole constitutional corpus is not
    ingested. Title is checked first because a year index only gives titles.
    """
    if _FILTER_RE.search(title or ""):
        return True
    if text and _FILTER_RE.search(text[:40_000]):
        return True
    return False


def year_of(url: str) -> int | None:
    match = _JUDGMENT_RE.search(urlparse(url).path)
    if match:
        return int(match.group("year"))
    match = _YEAR_INDEX_RE.search(urlparse(url).path)
    if match:
        return int(match.group("year"))
    return None
