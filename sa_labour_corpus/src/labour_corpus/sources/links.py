"""Link discovery from index pages.

Indexes are walked, never guessed. Sequential case-number enumeration is
deliberately not implemented: it hammers a host for documents that may not
exist and it is exactly the behaviour that gets a research crawler blocked.
"""

from __future__ import annotations

import re
from urllib.parse import urldefrag, urljoin, urlparse

from selectolax.parser import HTMLParser

# Document extensions worth downloading.
DOCUMENT_SUFFIXES = (".pdf", ".rtf", ".doc", ".docx", ".html", ".htm")

_SKIP_SCHEMES = ("mailto:", "javascript:", "tel:", "data:", "#")
_SKIP_PATTERNS = re.compile(
    r"/(?:login|signin|register|account|cart|search\?|print/|share|"
    r"feed|rss|atom|wp-admin|wp-login|cdn-cgi)/?"
    # SharePoint infrastructure endpoints. These are machinery, not documents,
    # and labour.gov.za's robots.txt disallows them at the site root.
    r"|/_(?:layouts|vti_bin|catalogs|api)/",
    re.IGNORECASE,
)


def extract_links(html: str | bytes, base_url: str) -> list[tuple[str, str]]:
    """Return (absolute_url, link_text) pairs from a page, de-duplicated."""
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="ignore")
    if not html.strip():
        return []

    tree = HTMLParser(html)
    seen: set[str] = set()
    out: list[tuple[str, str]] = []

    for node in tree.css("a"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.lower().startswith(_SKIP_SCHEMES):
            continue
        absolute, _ = urldefrag(urljoin(base_url, href))
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append((absolute, (node.text(strip=True) or "").strip()))
    return out


def same_host(url: str, host: str) -> bool:
    return (urlparse(url).hostname or "").lower() == host.lower()


def looks_like_document(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    return path.endswith(DOCUMENT_SUFFIXES)


def filter_links(
    links: list[tuple[str, str]],
    *,
    host: str,
    patterns: list[str] | None = None,
    documents_only: bool = False,
) -> list[tuple[str, str]]:
    """Keep same-host links that match any pattern and are not boilerplate."""
    compiled = [re.compile(p, re.IGNORECASE) for p in (patterns or [])]
    kept: list[tuple[str, str]] = []

    for url, text in links:
        if not same_host(url, host):
            continue
        if _SKIP_PATTERNS.search(url):
            continue
        if documents_only and not looks_like_document(url):
            continue
        if compiled and not any(
            regex.search(url) or regex.search(text) for regex in compiled
        ):
            continue
        kept.append((url, text))
    return kept
