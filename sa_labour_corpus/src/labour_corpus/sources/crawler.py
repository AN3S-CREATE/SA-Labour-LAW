"""Crawl orchestrator.

Explicit, resumable and boring by design. One host at a time, one request at a
time per host, state written as we go, and every refusal recorded as a gap
rather than retried into the ground.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

import httpx

from ..challenge import ChallengeDetected
from ..config import CONSECUTIVE_FAILURE_LIMIT, CrawlSettings
from ..http import (
    ContentSignalOptOut,
    Fetcher,
    FetchResult,
    HostTripped,
    RetryableStatus,
    RobotsDisallowed,
    host_of,
)
from ..models import Gap, GapKind, make_doc_id
from ..observability import RunLog
from ..pipeline import build_parsed_doc, persist, store_raw
from ..state import CorpusStore
from . import saflii
from .links import extract_links, filter_links, looks_like_document
from .registry import Access, SourceDef, SourceKind

MAX_INDEX_PAGES = 40
# Listing pages kept in memory for re-use within a run. Only a small text page
# is kept: a "sub-page" that is really a large download is not worth holding
# for the whole run, and is simply fetched again if another source links it.
# The total bounds the cache however many listings a run walks.
LISTING_CACHE_MAX_BYTES = 2 * 1024 * 1024
LISTING_CACHE_TOTAL_BYTES = 64 * 1024 * 1024


@dataclass(slots=True)
class SourceOutcome:
    source_id: str
    fetched: int = 0
    stored: int = 0
    skipped_duplicate: int = 0
    # Every hard failure this source had. Reported to the operator.
    failed: int = 0
    # The current run of failures without a successful fetch in between. This
    # is what CONSECUTIVE_FAILURE_LIMIT stops a source on.
    consecutive_failures: int = 0
    gaps: list[Gap] = field(default_factory=list)
    stopped_reason: str | None = None


class Crawler:
    """Walks configured sources and persists what it is allowed to collect."""

    def __init__(
        self,
        settings: CrawlSettings,
        fetcher: Fetcher,
        store: CorpusStore,
        log: RunLog,
    ) -> None:
        self.settings = settings
        self.fetcher = fetcher
        self.store = store
        self.log = log
        # One request per URL per run. A document linked from several pages
        # or sources is fetched once; a listing shared by several sources is
        # fetched once and its body re-read by each source's own filters.
        self._seen_docs: set[str] = set()
        self._listings: dict[str, FetchResult] = {}
        self._listing_bytes = 0

    # -- public ---------------------------------------------------------
    def crawl_source(self, source: SourceDef) -> SourceOutcome:
        outcome = SourceOutcome(source_id=source.id)

        if source.access is Access.CONTENT_SIGNAL_OPT_OUT:
            self._gap(
                outcome, source, "content_signal_opt_out", source.base_url,
                "Host expressly reserves rights against AI input (ai-input=no). "
                "Excluded pending permission or a data agreement.",
            )
            outcome.stopped_reason = "content_signal_opt_out"
            return outcome

        # A source's configured delay (e.g. 8-15 s for the CCMA, D-012) slows
        # its host only. It can raise the pace floor, never lower it.
        limiter = self.fetcher.limiter
        limiter.set_host_delay(source.host, source.min_delay, source.max_delay)
        base_host = host_of(source.base_url)
        if base_host and base_host != source.host.lower():
            limiter.set_host_delay(base_host, source.min_delay, source.max_delay)

        if source.kind is SourceKind.YEAR_INDEX:
            self._crawl_year_index(source, outcome)
        else:
            self._crawl_link_index(source, outcome)
        return outcome

    # -- strategies -----------------------------------------------------
    def _crawl_year_index(self, source: SourceDef, outcome: SourceOutcome) -> None:
        """SAFLII-style: database index -> year indexes -> judgments."""
        index = self._fetch_listing(source, source.base_url, outcome)
        if index is None or index.body is None:
            return

        year_urls = saflii.find_year_links(index.body, source.base_url, self.settings.years)
        if not year_urls:
            self._gap(
                outcome, source, "http_error", source.base_url,
                "no year index links found on the database page",
            )
            return

        for year_url in year_urls:
            if self._budget_reached(source, outcome):
                return
            year_page = self._fetch_listing(source, year_url, outcome)
            if year_page is None or year_page.body is None:
                continue

            for url, title in saflii.find_judgment_links(year_page.body, year_url):
                if self._budget_reached(source, outcome):
                    return
                if source.filter_required and not saflii.is_labour_relevant(title):
                    continue
                self._collect(source, url, outcome, year=saflii.year_of(url))

    def _paged_index_urls(self, source: SourceDef) -> list[str]:
        """The listing pages to walk for this source.

        Pagination is bounded by ``max_pages`` and uses the site's own published
        page parameter. This walks a finite published index; it is not
        enumeration of unknown resources.
        """
        if source.max_pages <= 0:
            return [source.base_url]
        separator = "&" if "?" in source.base_url else "?"
        return [
            f"{source.base_url}{separator}{source.page_param}={page}"
            for page in range(source.max_pages)
        ]

    def _crawl_link_index(self, source: SourceDef, outcome: SourceOutcome) -> None:
        """Government-site style: an index page linking to documents."""
        for index_url in self._paged_index_urls(source):
            if self._budget_reached(source, outcome):
                return
            self._crawl_one_index_page(source, index_url, outcome)

    def _crawl_one_index_page(
        self, source: SourceDef, index_url: str, outcome: SourceOutcome
    ) -> None:
        index = self._fetch_listing(source, index_url, outcome)
        if index is None or index.body is None:
            return
        source_base_url = index_url

        links = extract_links(index.body, source_base_url)
        documents = filter_links(
            links, host=source.host, patterns=source.link_patterns, documents_only=True
        )
        pages = [
            (url, text) for url, text in filter_links(
                links, host=source.host, patterns=source.link_patterns
            )
            if not looks_like_document(url)
        ]

        # A title filter keeps a national listing from pulling every statute.
        if source.title_filter:
            regex = re.compile(source.title_filter, re.IGNORECASE)
            documents = [(u, t) for u, t in documents if regex.search(t or "")]
            pages = [(u, t) for u, t in pages if regex.search(t or "")]

        pages = pages[:MAX_INDEX_PAGES]

        for url, _text in documents:
            if self._budget_reached(source, outcome):
                return
            self._collect(source, url, outcome, year=None)

        # One level deeper: sub-pages that themselves link to documents.
        for page_url, _text in pages:
            if self._budget_reached(source, outcome):
                return
            page = self._fetch_listing(source, page_url, outcome)
            if page is None or page.body is None:
                continue
            sub = filter_links(
                extract_links(page.body, page_url),
                host=source.host,
                patterns=source.link_patterns,
                documents_only=True,
            )
            for url, _label in sub:
                if self._budget_reached(source, outcome):
                    return
                self._collect(source, url, outcome, year=None)

    # -- helpers --------------------------------------------------------
    def _budget_reached(self, source: SourceDef, outcome: SourceOutcome) -> bool:
        cap = self.settings.max_docs_per_source
        if cap is not None and outcome.stored >= cap:
            outcome.stopped_reason = f"max_docs_per_source={cap} reached"
            return True
        if outcome.consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
            outcome.stopped_reason = (
                f"{CONSECUTIVE_FAILURE_LIMIT} consecutive hard failures"
            )
            return True
        if self.fetcher.is_tripped(source.host):
            outcome.stopped_reason = f"host tripped: {self.fetcher.tripped_hosts[source.host]}"
            return True
        return False

    def _fetch_listing(
        self, source: SourceDef, url: str, outcome: SourceOutcome
    ) -> FetchResult | None:
        """Fetch an index or listing page, at most once per run.

        A listing another source already fetched this run is re-read from
        memory, so this source still applies its own link patterns and title
        filter without a second request. Only a small text page is reused (see
        LISTING_CACHE_MAX_BYTES): a failure is retried by the next source,
        which records its own gap.
        """
        cached = self._listings.get(url)
        if cached is not None:
            self.log.event(
                "fetch", source_id=source.id, url=url, status=cached.status,
                reused_in_run=True,
            )
            return cached
        result = self._fetch(source, url, outcome)
        if result is not None and self._worth_caching(result):
            self._listings[url] = result
            self._listing_bytes += len(result.body or b"")
        return result

    def _worth_caching(self, result: FetchResult) -> bool:
        body = result.body
        if body is None or len(body) > LISTING_CACHE_MAX_BYTES:
            return False
        if self._listing_bytes + len(body) > LISTING_CACHE_TOTAL_BYTES:
            return False
        content_type = (result.content_type or "").lower()
        return (
            not content_type
            or content_type.startswith("text/")
            or "html" in content_type
            or "xml" in content_type
        )

    def _fetch(
        self, source: SourceDef, url: str, outcome: SourceOutcome
    ) -> FetchResult | None:
        """Fetch one URL, translating every refusal into a recorded gap."""
        record = self.store.get_fetch_record(url)
        # Validators only for a URL whose last body is a stored document. A
        # listing must be walked every run (a capped or interrupted run left
        # links behind it), and a body whose parse failed must be fetched again
        # rather than answered with a 304.
        persisted = _holds_stored_document(url, record)
        etag = record["etag"] if record and persisted else None
        modified = record["last_modified"] if record and persisted else None
        tripped_before = self.fetcher.is_tripped(host_of(url))

        try:
            result = self.fetcher.fetch(url, etag=etag, last_modified=modified)
        except ChallengeDetected as exc:
            self._gap(
                outcome, source, "challenge_blocked", url,
                f"host served a bot-management challenge ({exc.marker}). "
                "Not bypassed by design.",
            )
            outcome.stopped_reason = "challenge_blocked"
            self.log.error("fetch", str(exc), source_id=source.id, url=url)
            return None
        except RobotsDisallowed as exc:
            self._gap(outcome, source, "robots_disallow", url, str(exc))
            self.log.bump("robots_skipped")
            return None
        except ContentSignalOptOut as exc:
            self._gap(outcome, source, "content_signal_opt_out", url, str(exc))
            outcome.stopped_reason = "content_signal_opt_out"
            return None
        except HostTripped as exc:
            if not tripped_before:
                # This very request tripped the host (robots.txt unreachable or
                # oversize, a Crawl-delay beyond MAX_CRAWL_DELAY, or a
                # Retry-After beyond MAX_RETRY_AFTER). Without a gap the refusal
                # would leave no trace in the corpus record.
                self._gap(outcome, source, "http_error", url, str(exc))
                self.log.error("fetch", str(exc), source_id=source.id, url=url)
            outcome.stopped_reason = str(exc)
            return None
        except RetryableStatus as exc:
            # Still transient after every retry. One URL becomes a gap; the run
            # goes on (D-008). The host is not tripped for it:
            # CONSECUTIVE_FAILURE_LIMIT already stops a source that keeps failing.
            self._failed(outcome)
            self._gap(outcome, source, "http_error", url, f"HTTP {exc.status} after retries")
            self.log.error("fetch", str(exc), source_id=source.id, url=url)
            return None
        except (httpx.HTTPError, OSError) as exc:
            self._failed(outcome)
            self._gap(outcome, source, "http_error", url, f"{type(exc).__name__}: {exc}")
            self.log.error("fetch", str(exc), source_id=source.id, url=url)
            return None

        if result.status == 304:
            outcome.skipped_duplicate += 1
            self.log.bump("skipped_duplicate")
            self.log.event("fetch", source_id=source.id, url=url, status=304, cached=True)
            return None

        if result.status >= 400:
            self._failed(outcome)
            self._gap(outcome, source, "http_error", url, f"HTTP {result.status}")
            self.log.error("fetch", f"HTTP {result.status}", source_id=source.id, url=url)
            return None

        # A successful fetch ends the failure streak, even if the parse then
        # fails; the total in outcome.failed is kept.
        outcome.consecutive_failures = 0
        outcome.fetched += 1
        self.log.bump("fetched")
        self.store.record_fetch(
            url,
            source_id=source.id,
            status=result.status,
            content_sha256=result.content_sha256,
            etag=result.headers.get("etag"),
            last_modified=result.headers.get("last-modified"),
            bytes_=result.bytes,
        )
        return result

    def _collect(self, source: SourceDef, url: str, outcome: SourceOutcome, year: int | None) -> None:
        """Fetch, parse and persist one document, idempotently."""
        if url in self._seen_docs:
            # Already handled this run, from another page or source. Politeness
            # wins over an in-run retry; tenacity has retried transient errors.
            outcome.skipped_duplicate += 1
            self.log.bump("skipped_duplicate")
            self.log.event(
                "parse", source_id=source.id, url=url, status="skipped_duplicate_in_run"
            )
            return
        self._seen_docs.add(url)

        record = self.store.get_fetch_record(url)
        result = self._fetch(source, url, outcome)
        if result is None:
            return

        if (
            record
            and record["content_sha256"] == result.content_sha256
            and _holds_stored_document(url, record)
        ):
            outcome.skipped_duplicate += 1
            self.log.bump("skipped_duplicate")
            self.log.event(
                "parse", source_id=source.id, url=url, status="skipped_duplicate"
            )
            return

        if result.oversize:
            self._gap(
                outcome, source, "oversize", url,
                f"body exceeded the size cap ({result.bytes} bytes); headers stored only",
            )

        try:
            raw = store_raw(
                self.settings.paths, source_id=source.id, result=result, year=year,
                doc_type=source.doc_type, forum=source.forum, provenance="crawled",
            )
            doc, text = build_parsed_doc(
                source_id=source.id,
                forum=source.forum,
                result=result,
                default_doc_type=source.doc_type,
                license_note=source.license_note,
            )
            if doc.year is None and year is not None:
                doc.year = year
            persist(self.settings.paths, self.store, doc, text)
        except Exception as exc:  # noqa: BLE001
            # One malformed document must never end a run. Every parse failure
            # is recorded with its type and moved past, per the failure policy.
            # Deliberately broad: third-party parsers raise a wide and poorly
            # documented range of exceptions on corrupt input.
            self._failed(outcome)
            self._gap(outcome, source, "parse_failed", url, f"{type(exc).__name__}: {exc}")
            self.log.error("parse", f"{type(exc).__name__}: {exc}",
                           source_id=source.id, url=url)
            return

        self.store.record_fetch(
            url,
            source_id=source.id,
            status=result.status,
            content_sha256=result.content_sha256,
            etag=result.headers.get("etag"),
            last_modified=result.headers.get("last-modified"),
            bytes_=result.bytes,
            doc_id=doc.doc_id,
            raw_path=str(raw.directory),
        )
        outcome.stored += 1
        self.log.bump("parsed")
        self.log.event(
            "parse",
            source_id=source.id,
            url=url,
            doc_id=doc.doc_id,
            doc_type=doc.doc_type,
            status="ok",
            bytes=result.bytes,
        )

    @staticmethod
    def _failed(outcome: SourceOutcome) -> None:
        """Count one hard failure in the total and in the current streak."""
        outcome.failed += 1
        outcome.consecutive_failures += 1

    def _gap(
        self,
        outcome: SourceOutcome,
        source: SourceDef,
        kind: GapKind,
        url: str | None,
        detail: str,
    ) -> None:
        gap = Gap(source_id=source.id, kind=kind, url=url, detail=detail)
        outcome.gaps.append(gap)
        self.store.record_gap(gap)
        self.log.bump("gaps")
        self.log.event("gap", source_id=source.id, kind=kind, url=url, detail=detail)


def _holds_stored_document(url: str, record: sqlite3.Row | None) -> bool:
    """True when the fetch_log row's last body is the one stored as a document.

    The row's checksum and validators are written on every fetch, its doc_id
    only once a parse succeeds (and COALESCE keeps an older one). A changed
    body whose parse failed therefore sits beside the previous version's
    doc_id. It was never stored, so it must be fetched and parsed again, not
    answered with a 304 or skipped as a duplicate.
    """
    if not record or not record["doc_id"]:
        return False
    return bool(record["doc_id"] == make_doc_id(url, record["content_sha256"] or ""))


def preflight_gaps(source: SourceDef) -> list[Gap]:
    """Gaps that are known before a single request is made."""
    if source.access is Access.BOT_MANAGEMENT_BLOCKED:
        return [
            Gap(
                source_id=source.id,
                kind="challenge_blocked",
                url=source.base_url,
                detail=(
                    "Host refuses self-identifying automated clients. Collecting it "
                    "would require concealing that the client is a crawler, which "
                    "this project does not do."
                ),
            )
        ]
    if source.access is Access.CONTENT_SIGNAL_OPT_OUT:
        return [
            Gap(
                source_id=source.id,
                kind="content_signal_opt_out",
                url=source.base_url,
                detail="Host declares ai-input=no for general crawlers.",
            )
        ]
    return []
