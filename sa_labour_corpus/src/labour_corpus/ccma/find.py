"""Find: discover record identities and URLs from allow-listed indexes.

Discovery walks what a publisher lists. It never enumerates case numbers or
guesses URLs. Listing parsers are pure functions over HTML so they can be
tested against saved fixtures before any live run.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Iterable, Iterator
from urllib.parse import unquote, urlparse

from ..sources.links import extract_links
from ..sources.saflii import find_judgment_links, find_year_links
from ..sqlite_ro import connect_readonly
from .allowlist import CcmaSource, RecordKind, SourceKind, path_allowed
from .paths import safe_segment
from .scrape import Getter, Page
from .troubleshoot import SchemaDrift

LOCAL_SUFFIXES = (".html", ".htm", ".pdf", ".txt", ".docx", ".doc", ".rtf")

# Bounds on an official-publication walk, so a listing never becomes a site
# crawl: pagination pages per listing, and detail pages per run.
MAX_LISTING_PAGES = 20
MAX_DETAIL_PAGES = 100
_PAGINATION_RE = re.compile(r"^page/(?P<n>\d{1,4})/?$")

_SAFLII_RECORD_RE = re.compile(
    r"/cases/(?P<court>[A-Z]+)/(?P<year>(?:19|20)\d{2})/(?P<num>\d{1,5})\.html?$", re.I
)

# Corpus rows the CCMA engine takes from the main corpus.
_LOCAL_CORPUS_SQL = """
SELECT doc_id, canonical_url, title, year, doc_type, forum, content_type, content_sha256,
       retrieved_at, provenance
FROM documents
WHERE doc_type IN ('judgment', 'award') OR forum IN ('ZACCMA', 'CCMA_PUBLIC')
ORDER BY year DESC, doc_id
"""


@dataclass(slots=True)
class Found:
    source: str
    native_id: str
    url: str
    title: str | None
    year: int | None
    record_kind: RecordKind


def _span(years: tuple[int, int] | None) -> tuple[int, int]:
    return years or (1990, 2100)


def upgrade_to_https(url: str, hosts: Iterable[str]) -> str:
    """A plain-http link to an allow-listed host, rewritten to https.

    The URL guard fetches https only, so a listing that writes its own links
    as ``http://`` would otherwise lose those records. Nothing is fetched in
    the clear. Any other URL is returned unchanged for the guard to judge.
    """
    parsed = urlparse(url)
    if parsed.scheme == "http" and (parsed.hostname or "").lower() in hosts:
        return parsed._replace(scheme="https").geturl()
    return url


# -- pure listing parsers --------------------------------------------------

def parse_saflii_database_index(html: bytes | str, base_url: str,
                                years: tuple[int, int] | None) -> list[str]:
    """Year-page URLs from a SAFLII court database index, newest first."""
    links = find_year_links(html, base_url, _span(years))
    if not links and not extract_links(html, base_url):
        raise SchemaDrift(f"no links at all on the database index {base_url}")
    return sorted(links, reverse=True)


def parse_saflii_year_listing(html: bytes | str, base_url: str, source_id: str,
                              record_kind: RecordKind) -> list[Found]:
    """Records listed on one SAFLII year page."""
    out: list[Found] = []
    for url, text in find_judgment_links(html, base_url):
        match = _SAFLII_RECORD_RE.search(urlparse(url).path)
        if not match:
            continue
        native_id = f"{match.group('court').upper()}-{match.group('year')}-{match.group('num')}"
        out.append(Found(source_id, native_id, url, text or None, int(match.group("year")), record_kind))
    return out


def parse_link_index(html: bytes | str, base_url: str, source: CcmaSource) -> list[Found]:
    """Documents on a single listing page, filtered by the source's patterns."""
    patterns = [re.compile(p, re.I) for p in source.link_patterns] or [re.compile(r"\.pdf$", re.I)]
    out: list[Found] = []
    seen: set[str] = set()
    urls: set[str] = set()
    for link, text in extract_links(html, base_url):
        url = upgrade_to_https(link, source.hosts)
        parsed = urlparse(url)
        if (parsed.hostname or "").lower() not in source.hosts:
            continue
        if url in urls or not any(p.search(parsed.path) for p in patterns):
            continue     # an http:// and an https:// link to one file are one record
        urls.add(url)
        slug = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1]) or "index"
        native_id = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", slug)
        if native_id in seen:
            native_id = f"{native_id}-{hashlib.sha1(url.encode()).hexdigest()[:8]}"
        seen.add(native_id)
        out.append(Found(source.id, native_id, url, text or None, None, source.record_kind))
    return out


def official_native_id(path: str, allowed_paths: tuple[str, ...]) -> str:
    """A stable id from the URL path alone, so a re-run maps a URL to the same id.

    The part below the longest matching allow-list prefix is kept, namespaced
    by that prefix's last segment: ``/wp-content/uploads/2021/03/Rules.pdf`` ->
    ``uploads_2021_03_Rules``, ``/advices/unfair-dismissal/`` ->
    ``advices_unfair-dismissal``. An exact entry keeps only its file name. The
    digits identify the file; they are never read as a date (D-006).
    """
    decoded = unquote(path)
    prefix = None
    for entry in allowed_paths:
        if entry in (path, decoded):
            prefix = None
            break
        if entry.endswith("/") and path.startswith(entry) and (prefix is None or len(entry) > len(prefix)):
            prefix = entry
    if prefix is None:
        rest = decoded.rstrip("/").rsplit("/", 1)[-1]
    else:
        rest = prefix.rstrip("/").rsplit("/", 1)[-1] + "/" + unquote(path[len(prefix):])
    rest = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", rest.strip("/"))
    return safe_segment(rest.replace("/", "_")) or "index"


def parse_official_listing(
    html: bytes | str, base_url: str, source: CcmaSource, listing_root: str,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
    """(documents, detail pages, pagination pages) linked from one page.

    Only links on the source's hosts and inside its path allow-list count, so
    login pages, search, other hosts and other sites are never even candidates.
    Documents match the source's ``link_patterns`` (default: PDFs), detail
    pages sit under ``detail_paths``, and pagination is ``page/N/`` directly
    under the listing's own path. A plain-http link to the source's hosts is
    upgraded to https; nothing is fetched in the clear.
    """
    patterns = [re.compile(p, re.I) for p in source.link_patterns] or [re.compile(r"\.pdf$", re.I)]
    documents: list[tuple[str, str]] = []
    details: list[tuple[str, str]] = []
    pages: list[str] = []
    for link, text in extract_links(html, base_url):
        url = upgrade_to_https(link, source.hosts)
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in source.hosts:
            continue
        if not path_allowed(parsed.path, source.allowed_paths):
            continue
        if any(p.search(parsed.path) for p in patterns):
            documents.append((url, text))
        elif any(parsed.path.startswith(d) for d in source.detail_paths):
            details.append((url, text))
        elif parsed.path.startswith(listing_root) and _PAGINATION_RE.match(parsed.path[len(listing_root):]):
            pages.append(url)
    return documents, details, pages


def _page_number(url: str) -> int:
    match = re.search(r"/page/(\d{1,4})/?$", urlparse(url).path)
    return int(match.group(1)) if match else 0


def _get_or_report(getter: Getter, url: str,
                   on_error: Callable[[str, Exception], None] | None) -> Page | None:
    try:
        return getter.get(url)
    except Exception as exc:  # noqa: BLE001 -- isolated per listing page
        if on_error is None:
            raise
        on_error(url, exc)
        return None


def find_official(source: CcmaSource, getter: Getter, limit: int | None,
                  on_error: Callable[[str, Exception], None] | None = None) -> Iterator[Found]:
    """Records of an official-publication source, up to ``limit``.

    Exact ``urls`` come first, in allow-list order and without any request.
    Then, per listing: the documents it links, the documents on the detail
    pages it links (one level), and its pagination, all bounded. A detail page
    that links no document is itself the record, because some guidance is
    published only as a web page.
    """
    seen: set[str] = set()
    produced = 0

    def make(url: str, title: str | None) -> Found:
        native_id = official_native_id(urlparse(url).path, source.allowed_paths)
        return Found(source.id, native_id, url, title or None, None, source.record_kind)

    def full() -> bool:
        return limit is not None and produced >= limit

    for url in source.urls:
        if url in seen:
            continue
        seen.add(url)
        yield make(url, source.title)
        produced += 1
        if full():
            return

    details_fetched = 0
    for listing in source.listing_urls:
        root = urlparse(listing).path
        queue: list[str] = [listing]
        visited: set[str] = set()
        while queue and len(visited) < MAX_LISTING_PAGES:
            page_url = queue.pop(0)
            if page_url in visited:
                continue
            visited.add(page_url)
            page = _get_or_report(getter, page_url, on_error)
            if page is None:
                continue
            documents, details, pages = parse_official_listing(page.body, page.final_url, source, root)
            for url, text in documents:
                if url in seen:
                    continue
                seen.add(url)
                yield make(url, text)
                produced += 1
                if full():
                    return
            for detail_url, text in details:
                if detail_url in seen or details_fetched >= MAX_DETAIL_PAGES:
                    continue
                seen.add(detail_url)
                details_fetched += 1
                detail = _get_or_report(getter, detail_url, on_error)
                if detail is None:
                    continue
                linked, _, _ = parse_official_listing(detail.body, detail.final_url, source, root)
                if not linked:
                    yield make(detail_url, text)
                    produced += 1
                    if full():
                        return
                    continue
                for url, doc_text in linked:
                    if url in seen:
                        continue
                    seen.add(url)
                    yield make(url, doc_text or text)
                    produced += 1
                    if full():
                        return
            queue.extend(sorted((p for p in pages if p not in visited), key=_page_number))


# -- per-source discovery --------------------------------------------------

def find_live(source: CcmaSource, getter: Getter, limit: int | None,
              years: tuple[int, int] | None = None,
              on_error: Callable[[str, Exception], None] | None = None) -> Iterator[Found]:
    """Walk a live source's listings, yielding records up to ``limit``.

    A failing year page is reported through ``on_error`` and skipped, so one
    bad listing does not end discovery for the whole source. A failing
    top-level index still raises: without it there is nothing to walk.
    """
    if source.kind is SourceKind.OFFICIAL_PUBLICATION:
        yield from find_official(source, getter, limit, on_error)
        return
    if source.kind not in (SourceKind.SAFLII_YEAR_INDEX, SourceKind.LINK_INDEX):
        raise ValueError(f"{source.id} is not a live source")
    base_url = source.base_url
    if base_url is None:   # load_allowlist refuses this; a source built in code may not
        raise ValueError(f"{source.id} has no base_url to walk")
    produced = 0
    if source.kind is SourceKind.SAFLII_YEAR_INDEX:
        index = getter.get(base_url)
        for listed in parse_saflii_database_index(index.body, index.final_url, years or source.years):
            year_url = upgrade_to_https(listed, source.hosts)
            try:
                page = getter.get(year_url)
            except Exception as exc:  # noqa: BLE001 -- isolated per listing page
                if on_error is None:
                    raise
                on_error(year_url, exc)
                continue
            for found in parse_saflii_year_listing(page.body, page.final_url, source.id, source.record_kind):
                found.url = upgrade_to_https(found.url, source.hosts)
                yield found
                produced += 1
                if limit is not None and produced >= limit:
                    return
    else:   # SourceKind.LINK_INDEX
        page = getter.get(base_url)
        for found in parse_link_index(page.body, page.final_url, source):
            yield found
            produced += 1
            if limit is not None and produced >= limit:
                return


def find_local_corpus(source: CcmaSource, corpus_root: Path, limit: int | None) -> Iterator[Found]:
    """Judgments, awards and CCMA material already held by the main corpus."""
    db = corpus_root / "db" / "corpus.sqlite"
    if not db.exists():
        return
    conn = connect_readonly(db)
    conn.row_factory = sqlite3.Row
    try:
        for n, row in enumerate(conn.execute(_LOCAL_CORPUS_SQL)):
            if limit is not None and n >= limit:
                return
            if row["doc_type"] == "award" or row["forum"] == "ZACCMA":
                kind = RecordKind.AWARD
            elif row["doc_type"] == "judgment":
                kind = RecordKind.RELATED_AUTHORITY
            else:
                kind = RecordKind.PRACTICE_MATERIAL
            yield Found(source.id, row["doc_id"], row["canonical_url"], row["title"], row["year"], kind)
    finally:
        conn.close()


def local_corpus_row(corpus_root: Path, doc_id: str) -> sqlite3.Row | None:
    db = corpus_root / "db" / "corpus.sqlite"
    conn = connect_readonly(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM documents WHERE doc_id=?", (doc_id,)
        ).fetchone()
    finally:
        conn.close()


def find_local_directory(source: CcmaSource, directory: Path, limit: int | None) -> Iterator[Found]:
    """Files the operator placed in a folder. The file stem is the native id."""
    if not directory.exists():
        return
    files = sorted(p for p in directory.iterdir()
                   if p.is_file() and p.suffix.lower() in LOCAL_SUFFIXES)
    for n, path in enumerate(files):
        if limit is not None and n >= limit:
            return
        yield Found(source.id, path.stem, path.resolve().as_uri(), None, None, source.record_kind)
