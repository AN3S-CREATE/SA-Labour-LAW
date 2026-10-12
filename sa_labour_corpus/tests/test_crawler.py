"""Crawler orchestration tests over a scripted host. No network access.

Every test drives the real Crawler through a real Fetcher whose transport is an
httpx.MockTransport, with a corpus under tmp_path and a fake clock in place of
the fetch layer's time module, so no test ever really sleeps (the robots
default Crawl-delay of 1 s applies even with RateLimiter(0, 0)).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import get_args

import httpx
import pydantic
import pytest

import labour_corpus.http as http_module
from labour_corpus.config import CorpusPaths, CrawlSettings, build_user_agent
from labour_corpus.http import ROBOTS_MAX_BYTES, Fetcher, RateLimiter
from labour_corpus.models import DocType, Gap, GapKind
from labour_corpus.observability import RunLog
from labour_corpus.report import coverage
from labour_corpus.sources import crawler as crawler_module
from labour_corpus.sources.crawler import Crawler, SourceOutcome
from labour_corpus.sources.registry import Access, SourceDef, SourceKind
from labour_corpus.state import CorpusStore

HOST = "example.org"
BASE = f"https://{HOST}"
UA = build_user_agent("ops@example.test")

Route = Callable[[httpx.Request], httpx.Response]


# -- a scripted host -------------------------------------------------------
class Site:
    """A MockTransport handler that routes by path+query and records requests."""

    def __init__(self, routes: dict[str, Route]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(request.url.raw_path.decode("ascii"))
        return route(request) if route else httpx.Response(404)

    def requests_for(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.raw_path.decode("ascii") == path]

    def hits(self, path: str) -> int:
        return len(self.requests_for(path))


def _html(title: str) -> bytes:
    return (
        f"<html><head><title>{title}</title></head><body><h1>{title}</h1>"
        "<p>The employer dismissed the employee after a disciplinary hearing.</p>"
        "</body></html>"
    ).encode()


def _listing(*hrefs: str) -> bytes:
    links = "".join(f'<a href="{href}">{href}</a> ' for href in hrefs)
    return f"<html><body>{links}</body></html>".encode()


def page(body: bytes, *, etag: str | None = None, status: int = 200) -> Route:
    """A page that answers 304 to its own ETag, like a validator-honouring server."""

    def route(request: httpx.Request) -> httpx.Response:
        if etag is not None and request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers={"etag": etag})
        headers = {"content-type": "text/html"}
        if etag is not None:
            headers["etag"] = etag
        return httpx.Response(status, content=body, headers=headers)

    return route


def status(code: int, **headers: str) -> Route:
    return lambda request: httpx.Response(code, headers=headers)


ROBOTS_ALLOW_ALL: dict[str, Route] = {
    "/robots.txt": lambda request: httpx.Response(200, text="User-agent: *\nAllow: /\n"),
}


def _source(**overrides: object) -> SourceDef:
    fields: dict[str, object] = dict(
        id="test_source",
        name="Test source",
        host=HOST,
        base_url=f"{BASE}/list/",
        forum="TEST",
        doc_type=DocType.OTHER,
        kind=SourceKind.LINK_INDEX,
        access=Access.OPEN,
        link_patterns=[r"\.html$", "list"],
        # Zero so the tests never sleep; pacing has its own tests.
        min_delay=0.0,
        max_delay=0.0,
    )
    fields.update(overrides)
    return SourceDef(**fields)  # type: ignore[arg-type]


@pytest.fixture()
def make_crawler(tmp_path, clock):
    """Build a Crawler over a Site. Each call is one run on the same corpus."""
    paths = CorpusPaths(root=tmp_path / "corpus")
    paths.ensure()
    opened: list[Crawler] = []

    def build(
        site: Site, *, max_docs: int | None = None, limiter: RateLimiter | None = None
    ) -> Crawler:
        for previous in opened:          # one run at a time, as the CLI does
            previous.fetcher.close()
            previous.store.close()
        opened.clear()
        settings = CrawlSettings(
            contact_email="ops@example.test", paths=paths, years=(2020, 2026),
            max_docs_per_source=max_docs,
        )
        fetcher = Fetcher(
            settings.user_agent,
            limiter=limiter or RateLimiter(0, 0),
            client=httpx.Client(transport=httpx.MockTransport(site)),
            retry_waits=(0,),
        )
        crawler = Crawler(
            settings, fetcher, CorpusStore(paths.corpus_db),
            RunLog(paths.run_log, paths.error_log),
        )
        opened.append(crawler)
        return crawler

    yield build
    for crawler in opened:
        crawler.fetcher.close()
        crawler.store.close()


class _FakeClock:
    """Stands in for the time module inside labour_corpus.http."""

    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def perf_counter(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture()
def clock(monkeypatch) -> _FakeClock:
    fake = _FakeClock()
    monkeypatch.setattr(http_module, "time", fake)
    return fake


# -- a persistent 503 is a gap, not the end of the run ---------------------
def test_persistent_retryable_status_becomes_a_gap(make_crawler):
    site = Site({**ROBOTS_ALLOW_ALL, "/a.pdf": status(503)})
    crawler = make_crawler(site)
    source = _source()
    outcome = SourceOutcome(source.id)

    assert crawler._fetch(source, f"{BASE}/a.pdf", outcome) is None
    assert outcome.failed == 1
    assert [g.kind for g in outcome.gaps] == ["http_error"]
    assert "503" in outcome.gaps[0].detail
    assert site.hits("/a.pdf") == 2                  # one try, one retry
    assert not crawler.fetcher.is_tripped(HOST)      # a 503 does not trip the host
    assert len(crawler.store.all_gaps()) == 1


def test_crawl_continues_past_a_persistent_503(make_crawler):
    site = Site({
        **ROBOTS_ALLOW_ALL,
        "/list/": page(_listing("/a.html", "/b.html")),
        "/a.html": status(503),
        "/b.html": page(_html("Document B")),
    })
    crawler = make_crawler(site)

    outcome = crawler.crawl_source(_source())

    assert outcome.stored == 1
    assert [(g.kind, g.url) for g in outcome.gaps] == [("http_error", f"{BASE}/a.html")]
    assert crawler.store.count_documents() == 1


# -- failure counting -------------------------------------------------------
def test_failed_is_a_total_not_the_trailing_streak(make_crawler):
    site = Site({
        **ROBOTS_ALLOW_ALL,
        "/list/": page(_listing("/x1.html", "/x2.html", "/ok.html")),
        "/ok.html": page(_html("OK")),
    })
    outcome = make_crawler(site).crawl_source(_source())

    assert outcome.failed == 2
    assert outcome.consecutive_failures == 0
    assert outcome.stored == 1
    assert [g.kind for g in outcome.gaps] == ["http_error", "http_error"]


def test_consecutive_failures_stop_the_source(make_crawler):
    missing = [f"/missing{i}.html" for i in range(10)]
    site = Site({
        **ROBOTS_ALLOW_ALL,
        "/list/": page(_listing(*missing, "/late.html")),
        "/late.html": page(_html("Late")),
    })
    outcome = make_crawler(site).crawl_source(_source())

    assert "consecutive hard failures" in (outcome.stopped_reason or "")
    assert outcome.failed == 10
    assert site.hits("/late.html") == 0


# -- resume: validators only for persisted documents -----------------------
def _resumable_site() -> Site:
    return Site({
        "/robots.txt": status(404),
        "/list/": page(_listing("/a.html", "/b.html"), etag='"L1"'),
        "/a.html": page(_html("Document A"), etag='"A1"'),
        "/b.html": page(_html("Document B"), etag='"B1"'),
    })


def test_capped_run_resumes_and_finds_the_rest(make_crawler):
    site = _resumable_site()
    first = make_crawler(site, max_docs=1).crawl_source(_source())
    assert first.stored == 1
    assert site.hits("/b.html") == 0

    second_crawler = make_crawler(site)
    second = second_crawler.crawl_source(_source())

    listing_requests = site.requests_for("/list/")
    assert len(listing_requests) == 2
    assert "if-none-match" not in listing_requests[1].headers   # listings are always walked
    assert second.stored == 1                                     # b.html
    assert second_crawler.store.count_documents() == 2
    # The persisted document still resumes by validator: one 304, no re-download.
    assert site.requests_for("/a.html")[1].headers["if-none-match"] == '"A1"'
    assert second.skipped_duplicate == 1


def test_a_document_whose_parse_failed_is_fetched_again(make_crawler, monkeypatch):
    site = _resumable_site()
    real = crawler_module.build_parsed_doc
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("corrupt body")
        return real(**kwargs)

    monkeypatch.setattr(crawler_module, "build_parsed_doc", flaky)
    first = make_crawler(site).crawl_source(_source(link_patterns=[r"a\.html$"]))
    assert first.stored == 0
    assert [g.kind for g in first.gaps] == ["parse_failed"]

    second_crawler = make_crawler(site)
    second = second_crawler.crawl_source(_source(link_patterns=[r"a\.html$"]))

    assert "if-none-match" not in site.requests_for("/a.html")[1].headers
    assert second.stored == 1
    assert second_crawler.store.count_documents() == 1


def test_a_changed_document_whose_parse_failed_is_fetched_again(make_crawler, monkeypatch):
    """The new body's checksum and ETag are logged before the parse fails.

    The row keeps the old version's doc_id, so it must not read as stored: the
    next run sends no validator and parses the new version.
    """
    current = {"body": _html("Version one"), "etag": '"V1"'}

    def document(request: httpx.Request) -> httpx.Response:
        if request.headers.get("if-none-match") == current["etag"]:
            return httpx.Response(304, headers={"etag": current["etag"]})
        return httpx.Response(200, content=current["body"], headers={
            "content-type": "text/html", "etag": current["etag"],
        })

    site = Site({"/robots.txt": status(404), "/list/": page(_listing("/a.html")),
                 "/a.html": document})
    source = _source(link_patterns=[r"a\.html$"])
    assert make_crawler(site).crawl_source(source).stored == 1

    current.update(body=_html("Version two"), etag='"V2"')
    real = crawler_module.build_parsed_doc

    def corrupt(**kwargs):
        raise ValueError("corrupt body")

    monkeypatch.setattr(crawler_module, "build_parsed_doc", corrupt)
    second = make_crawler(site).crawl_source(source)
    assert [g.kind for g in second.gaps] == ["parse_failed"]

    monkeypatch.setattr(crawler_module, "build_parsed_doc", real)
    third_crawler = make_crawler(site)
    third = third_crawler.crawl_source(source)

    requests = site.requests_for("/a.html")
    assert requests[1].headers["if-none-match"] == '"V1"'    # stored: resume by validator
    assert "if-none-match" not in requests[2].headers        # failed: fetched in full
    assert third.stored == 1
    assert third_crawler.store.count_documents() == 2        # both versions kept


# -- one request per URL per run -------------------------------------------
def test_a_document_linked_twice_is_fetched_once(make_crawler):
    site = Site({
        **ROBOTS_ALLOW_ALL,
        "/list/": page(_listing("/doc.html", "/list/", "/list/?s=")),
        "/list/?s=": page(_listing("/doc.html")),
        "/doc.html": page(_html("The document")),
    })
    outcome = make_crawler(site).crawl_source(_source())

    assert site.hits("/doc.html") == 1
    assert site.hits("/list/") == 1          # the self-link is not re-requested
    assert site.hits("/list/?s=") == 1
    assert outcome.stored == 1
    assert outcome.skipped_duplicate == 2    # re-linked from /list/ and /list/?s=


def test_sources_sharing_a_listing_reuse_it_with_their_own_patterns(make_crawler):
    site = Site({
        **ROBOTS_ALLOW_ALL,
        "/list/": page(_listing("/doc.html", "/extra.html")),
        "/doc.html": page(_html("The document")),
        "/extra.html": page(_html("Another document")),
    })
    crawler = make_crawler(site)

    narrow = crawler.crawl_source(_source(id="narrow", link_patterns=[r"doc\.html$"]))
    wide = crawler.crawl_source(_source(id="wide", link_patterns=[r"\.html$"]))

    assert narrow.stored == 1
    assert wide.stored == 1                   # extra.html, found by its own pattern
    assert wide.skipped_duplicate == 1        # doc.html, already handled this run
    assert site.hits("/list/") == 1
    assert site.hits("/doc.html") == 1
    assert site.hits("/extra.html") == 1


def test_only_small_text_listings_are_kept_in_memory(make_crawler):
    """A 'sub-page' that is really a large page or a download is not cached."""
    listing = _listing("/list/big", "/list/download.php?id=1")
    big = b"<html>" + b" " * crawler_module.LISTING_CACHE_MAX_BYTES + b"</html>"
    site = Site({
        **ROBOTS_ALLOW_ALL,
        "/list/": page(listing),
        "/list/big": page(big),
        "/list/download.php?id=1": lambda request: httpx.Response(
            200, content=b"%PDF-1.4 not a listing", headers={"content-type": "application/pdf"}
        ),
    })
    crawler = make_crawler(site)
    crawler.crawl_source(_source())

    assert set(crawler._listings) == {f"{BASE}/list/"}
    assert crawler._listing_bytes == len(listing)
    assert site.hits("/list/big") == 1
    assert site.hits("/list/download.php?id=1") == 1


def test_the_listing_cache_has_a_total_bound(make_crawler, monkeypatch):
    first, second = _listing("/list/a"), _listing("/list/b", "/list/a")
    monkeypatch.setattr(crawler_module, "LISTING_CACHE_TOTAL_BYTES", len(first) + 1)
    site = Site({**ROBOTS_ALLOW_ALL, "/list/": page(first), "/list/a": page(second)})
    crawler = make_crawler(site)
    crawler.crawl_source(_source())

    assert set(crawler._listings) == {f"{BASE}/list/"}
    assert crawler._listing_bytes == len(first)


# -- per-source pacing -------------------------------------------------------
def test_a_source_delay_is_applied_to_its_host(make_crawler, clock):
    site = Site({"/robots.txt": status(404), "/list/": page(_listing())})
    crawler = make_crawler(site)

    crawler.crawl_source(_source(min_delay=8.0, max_delay=15.0))

    limiter = crawler.fetcher.limiter
    assert limiter._host_delays[HOST] == (8.0, 15.0)
    assert (limiter.min_delay, limiter.max_delay) == (0, 0)   # other hosts untouched


def test_a_source_delay_also_covers_a_differing_base_url_host(make_crawler, clock):
    crawler = make_crawler(Site({}))
    crawler.crawl_source(_source(base_url="https://docs.example.org/list/",
                                 min_delay=8.0, max_delay=15.0))

    delays = crawler.fetcher.limiter._host_delays
    assert delays[HOST] == (8.0, 15.0)
    assert delays["docs.example.org"] == (8.0, 15.0)


def test_requests_to_a_slow_source_are_spaced_by_its_delay(make_crawler, clock):
    site = Site({
        **ROBOTS_ALLOW_ALL,
        "/list/": page(_listing("/a.html", "/b.html")),
        "/a.html": page(_html("A")),
        "/b.html": page(_html("B")),
    })
    outcome = make_crawler(site).crawl_source(_source(min_delay=8.0, max_delay=15.0))

    assert outcome.stored == 2
    # /list/ is the host's first request; a.html and b.html each wait 8-15 s.
    assert len(clock.slept) == 2
    assert all(8.0 <= s <= 15.0 for s in clock.slept)


# -- refusals that trip the host are recorded --------------------------------
def test_a_long_retry_after_trips_the_host_and_is_recorded(make_crawler, clock):
    site = Site({
        **ROBOTS_ALLOW_ALL,
        "/list/": page(_listing("/a.html", "/b.html")),
        "/a.html": status(503, **{"retry-after": "86400"}),
        "/b.html": page(_html("B")),
    })
    crawler = make_crawler(site)
    outcome = crawler.crawl_source(_source())

    assert crawler.fetcher.is_tripped(HOST)
    assert sum(clock.slept) < 60                 # backed off, did not sleep a day
    assert site.hits("/a.html") == 1             # and did not retry
    assert site.hits("/b.html") == 0
    assert [g.kind for g in outcome.gaps] == ["http_error"]
    assert "Retry-After 86400" in outcome.gaps[0].detail
    assert "host tripped" in (outcome.stopped_reason or "")


def test_an_unreachable_robots_txt_is_recorded_as_a_gap(make_crawler):
    site = Site({"/robots.txt": status(503), "/list/": page(_listing("/a.html"))})
    crawler = make_crawler(site)
    outcome = crawler.crawl_source(_source())

    assert site.hits("/list/") == 0
    assert [g.kind for g in outcome.gaps] == ["http_error"]
    assert "unreachable" in outcome.gaps[0].detail


def test_an_oversize_robots_txt_is_recorded_as_a_gap(make_crawler):
    robots = b"User-agent: *\nAllow: /\n" + b"#" * ROBOTS_MAX_BYTES + b"\n"
    site = Site({
        "/robots.txt": lambda request: httpx.Response(200, content=robots),
        "/list/": page(_listing("/a.html")),
    })
    crawler = make_crawler(site)
    outcome = crawler.crawl_source(_source())

    assert site.hits("/list/") == 0
    assert crawler.fetcher.tripped_hosts[HOST] == "robots_oversize"
    assert [g.kind for g in outcome.gaps] == ["http_error"]
    assert "longer than" in outcome.gaps[0].detail


def test_an_impossible_crawl_delay_is_recorded_as_a_gap(make_crawler, clock):
    site = Site({
        "/robots.txt": lambda request: httpx.Response(
            200, text="User-agent: *\nCrawl-delay: 1e12\n"
        ),
        "/list/": page(_listing("/a.html")),
        "/a.html": page(_html("A")),
    })
    crawler = make_crawler(site)
    outcome = crawler.crawl_source(_source())

    assert site.hits("/list/") == 0
    assert clock.slept == []
    assert crawler.fetcher.tripped_hosts[HOST].startswith("crawl_delay:")
    assert [g.kind for g in outcome.gaps] == ["http_error"]
    assert "Crawl-delay" in outcome.gaps[0].detail


# -- the gap vocabulary ----------------------------------------------------
def test_gap_kinds_and_coverage_labels_cannot_drift():
    assert set(get_args(GapKind)) == set(coverage._GAP_LABELS)


def test_a_misspelt_gap_kind_is_rejected():
    with pytest.raises(pydantic.ValidationError):
        Gap(source_id="s", kind="typo_kind")  # type: ignore[arg-type]
