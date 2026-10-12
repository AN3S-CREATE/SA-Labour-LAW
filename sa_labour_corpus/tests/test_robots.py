"""robots.txt, Content-Signal and rate-limiter tests. No network access."""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

import labour_corpus.http as http_module
from labour_corpus.challenge import looks_like_challenge
from labour_corpus.config import build_user_agent
from labour_corpus.http import (
    ROBOTS_MAX_BYTES,
    Fetcher,
    HostTripped,
    RateLimiter,
    parse_retry_after,
)
from labour_corpus.robots import parse_robots, robots_url_for

UA = build_user_agent("ops@example.test")


class _FakeClock:
    """Stands in for the time module inside labour_corpus.http: no real sleeps."""

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

LAWLIBRARY_SAMPLE = """
User-agent: *
Content-Signal: ai-train=no, search=yes, ai-input=no
Allow: /
Disallow: /api/
Disallow: /judgments/
Disallow: /akn/za/judgment/
Crawl-delay: 5
Sitemap: https://lawlibrary.org.za/sitemap.xml

User-agent: Claude-SearchBot
Content-Signal: ai-train=no, search=yes, ai-input=yes
Allow: /
"""


# -- parsing ---------------------------------------------------------------
def test_allow_and_disallow_paths():
    rules = parse_robots("lawlibrary.org.za", 200, LAWLIBRARY_SAMPLE)
    assert rules.can_fetch(UA, "https://lawlibrary.org.za/akn/za/act/1995/66")
    assert not rules.can_fetch(UA, "https://lawlibrary.org.za/akn/za/judgment/zalc/2024/1")
    assert not rules.can_fetch(UA, "https://lawlibrary.org.za/judgments/anything")
    assert not rules.can_fetch(UA, "https://lawlibrary.org.za/api/v1/works")


def test_crawl_delay_is_honoured():
    rules = parse_robots("lawlibrary.org.za", 200, LAWLIBRARY_SAMPLE)
    assert rules.crawl_delay(UA) == 5.0


def test_sitemap_is_captured():
    rules = parse_robots("lawlibrary.org.za", 200, LAWLIBRARY_SAMPLE)
    assert "https://lawlibrary.org.za/sitemap.xml" in rules.sitemaps


def test_content_signal_ai_input_no_blocks_rag_corpus():
    """ai-input=no covers RAG and grounding, which is this corpus's purpose."""
    rules = parse_robots("lawlibrary.org.za", 200, LAWLIBRARY_SAMPLE)
    signal = rules.content_signal(UA)
    assert signal.ai_input is False
    assert signal.search is True
    assert signal.ai_train is False
    assert signal.permits_rag_corpus() is False


def test_absent_content_signal_does_not_bar_collection():
    rules = parse_robots("example.org", 200, "User-agent: *\nAllow: /\n")
    assert rules.content_signal(UA).permits_rag_corpus() is True


def test_wildcard_and_anchor_rules():
    rules = parse_robots("example.org", 200, (
        "User-agent: *\n"
        "Disallow: /private/*/secret\n"
        "Disallow: /tmp$\n"
    ))
    assert not rules.can_fetch(UA, "https://example.org/private/a/secret")
    assert rules.can_fetch(UA, "https://example.org/private/a/public")
    assert not rules.can_fetch(UA, "https://example.org/tmp")
    assert rules.can_fetch(UA, "https://example.org/tmpfile")


def test_longest_rule_wins():
    rules = parse_robots("example.org", 200, (
        "User-agent: *\n"
        "Disallow: /docs/\n"
        "Allow: /docs/public/\n"
    ))
    assert rules.can_fetch(UA, "https://example.org/docs/public/a.pdf")
    assert not rules.can_fetch(UA, "https://example.org/docs/private/a.pdf")


# -- several groups for the same agent (RFC 9309 s2.2.1) -------------------
# The layout a CDN produces when it prepends managed content signals above the
# site's own robots.txt: two separate "User-agent: *" groups.
CDN_PREPENDED_SAMPLE = """
# Content signals managed by the CDN
User-Agent: *
Content-Signal: search=yes, ai-train=no
Allow: /

User-agent: Amazonbot
Disallow: /

# The site's own rules
User-agent: *
Content-Signal: ai-input=no
Disallow: /judgments/
Crawl-delay: 3
"""


def test_every_wildcard_group_is_honoured():
    rules = parse_robots("example.org", 200, CDN_PREPENDED_SAMPLE)
    assert not rules.can_fetch(UA, "https://example.org/judgments/x")
    assert rules.can_fetch(UA, "https://example.org/acts/x")


def test_a_later_group_content_signal_reservation_is_honoured():
    rules = parse_robots("example.org", 200, CDN_PREPENDED_SAMPLE)
    signal = rules.content_signal(UA)
    assert signal.ai_input is False
    assert signal.ai_train is False
    assert signal.search is True
    assert signal.permits_rag_corpus() is False


def test_the_longest_crawl_delay_across_groups_wins():
    rules = parse_robots("example.org", 200, (
        "User-agent: *\nCrawl-delay: 2\n\n"
        "User-agent: *\nCrawl-delay: 7\n"
    ))
    assert rules.crawl_delay(UA) == 7.0


@pytest.mark.parametrize("value", ["inf", "Infinity", "nan", "-inf"])
def test_a_non_finite_crawl_delay_reads_as_infinite(value):
    """Ignoring it would crawl at the default pace; it is a refusal instead."""
    rules = parse_robots("example.org", 200, (
        "User-agent: *\nCrawl-delay: 2\n\n"
        f"User-agent: *\nCrawl-delay: {value}\n"
    ))
    assert rules.crawl_delay(UA) == math.inf


def test_a_specific_group_still_beats_the_wildcard():
    rules = parse_robots("example.org", 200, (
        "User-agent: *\nDisallow: /\n\n"
        "User-agent: SALabourCorpusBot\nAllow: /\n"
    ))
    assert rules.can_fetch(UA, "https://example.org/anything")


def test_groups_naming_the_same_agent_are_combined():
    rules = parse_robots("example.org", 200, (
        "User-agent: SALabourCorpusBot\nDisallow: /a/\n\n"
        "User-agent: *\nDisallow: /\n\n"
        "User-agent: salabourcorpusbot\nDisallow: /b/\n"
    ))
    assert not rules.can_fetch(UA, "https://example.org/a/x")
    assert not rules.can_fetch(UA, "https://example.org/b/x")
    assert rules.can_fetch(UA, "https://example.org/c/x")


def test_an_allow_in_one_group_cannot_lift_another_groups_disallow():
    """Deliberately stricter than RFC 9309's merge: never looser than any group."""
    rules = parse_robots("example.org", 200, (
        "User-agent: *\nDisallow: /docs/\n\n"
        "User-agent: *\nAllow: /docs/public/\n"
    ))
    assert not rules.can_fetch(UA, "https://example.org/docs/public/a.pdf")


# -- status handling (RFC 9309, with the challenge deviation) --------------
def test_404_robots_means_no_restrictions():
    rules = parse_robots("example.org", 404, "")
    assert rules.can_fetch(UA, "https://example.org/anything")


def test_5xx_robots_is_treated_as_disallow():
    rules = parse_robots("example.org", 503, "")
    assert rules.unreachable
    assert not rules.can_fetch(UA, "https://example.org/anything")


def test_429_robots_is_treated_as_disallow():
    rules = parse_robots("example.org", 429, "")
    assert rules.unreachable
    assert not rules.can_fetch(UA, "https://example.org/anything")


def test_challenge_on_robots_blocks_the_host():
    """A challenge is not 'unavailable means allow all'."""
    body = '<html><head><title>Just a moment...</title></head><body></body></html>'
    rules = parse_robots("www.saflii.org", 403, body)
    assert rules.blocked_by_challenge
    assert rules.challenge_marker == "just a moment..."
    assert not rules.can_fetch(UA, "https://www.saflii.org/za/cases/ZALCJHB/")


def test_bare_403_is_treated_as_a_refusal_of_automation():
    rules = parse_robots("www.ccma.org.za", 403, "<html>403 Forbidden</html>")
    assert rules.blocked_by_challenge
    assert not rules.can_fetch(UA, "https://www.ccma.org.za/rules")


def test_robots_url_for():
    assert robots_url_for("https://example.org/a/b?c=d") == "https://example.org/robots.txt"


# -- challenge detection ---------------------------------------------------
@pytest.mark.parametrize("marker", [
    "Just a moment...",
    "cdn-cgi/challenge-platform",
    "Enable JavaScript and cookies to continue",
    "Checking your browser before accessing",
])
def test_challenge_markers_are_detected(marker):
    assert looks_like_challenge(403, f"<html>{marker}</html>") is not None


def test_ordinary_page_is_not_a_challenge():
    assert looks_like_challenge(200, "<html><body>Real content</body></html>") is None


# -- rate limiting ---------------------------------------------------------
def test_rate_limiter_spaces_requests_for_a_host():
    limiter = RateLimiter(min_delay=0.05, max_delay=0.06)
    limiter.wait("example.org")          # first call returns immediately
    started = time.monotonic()
    limiter.wait("example.org")
    assert time.monotonic() - started >= 0.04


def test_rate_limiter_is_per_host():
    limiter = RateLimiter(min_delay=0.2, max_delay=0.25)
    limiter.wait("a.example")
    started = time.monotonic()
    limiter.wait("b.example")            # a different host must not be delayed
    assert time.monotonic() - started < 0.1


def test_rate_limiter_respects_a_larger_crawl_delay():
    limiter = RateLimiter(min_delay=0.01, max_delay=0.02)
    limiter.wait("example.org")
    started = time.monotonic()
    limiter.wait("example.org", crawl_delay=0.15)
    assert time.monotonic() - started >= 0.14


def test_a_host_delay_slows_only_that_host(clock):
    limiter = RateLimiter(min_delay=0.01, max_delay=0.02)
    limiter.set_host_delay("slow.example", 0.15, 0.2)
    limiter.wait("slow.example")
    assert 0.15 <= limiter.wait("slow.example") <= 0.2
    limiter.wait("fast.example")
    assert 0.01 <= limiter.wait("fast.example") <= 0.02


def test_a_host_delay_is_never_lowered(clock):
    limiter = RateLimiter(min_delay=0.0, max_delay=0.0)
    limiter.set_host_delay("A.Example", 0.2, 0.25)
    limiter.set_host_delay("a.example", 0.05, 0.06)     # a faster second source
    assert limiter._host_delays["a.example"] == (0.2, 0.25)
    limiter.wait("a.example")
    assert limiter.wait("a.example") >= 0.2


def test_a_host_delay_and_crawl_delay_take_the_larger(clock):
    limiter = RateLimiter(min_delay=0.0, max_delay=0.0)
    limiter.set_host_delay("example.org", 0.3, 0.3)
    limiter.wait("example.org")
    assert limiter.wait("example.org", crawl_delay=1.0) == pytest.approx(1.0)
    assert limiter.wait("example.org", crawl_delay=0.1) == pytest.approx(0.3)


def test_retry_after_parsing():
    assert parse_retry_after("30") == 30.0
    assert parse_retry_after(" 120 ") == 120.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("not-a-number") is None


@pytest.mark.parametrize(("value", "seconds"), [
    ("2.5", 2.5), ("+30", 30.0), ("1e2", 100.0), ("1_000", 1000.0),
    ("30.", 30.0), (".5", 0.5),
])
def test_retry_after_honours_any_plain_number(value, seconds):
    """Not RFC delay-seconds, but ignoring them would retry sooner than asked."""
    assert parse_retry_after(value) == seconds


@pytest.mark.parametrize("value", ["nan", "NaN", "-5", "0x10", "soon"])
def test_retry_after_ignores_a_value_that_names_no_wait(value):
    assert parse_retry_after(value) is None


@pytest.mark.parametrize("value", [
    "inf", "Infinity", "1e12", "1e300", pytest.param("9" * 5000, id="5000-digits"),
])
def test_retry_after_clamps_an_absurd_wait_to_a_finite_trip(value):
    """These used to reach time.sleep: inf crashed the run, 1e9 hung it."""
    seconds = parse_retry_after(value)
    assert seconds is not None
    assert math.isfinite(seconds)
    assert seconds > http_module.MAX_RETRY_AFTER


def test_retry_after_accepts_an_http_date(monkeypatch):
    fixed = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(http_module, "datetime", _Frozen)
    ahead = format_datetime(fixed + timedelta(seconds=60), usegmt=True)
    behind = format_datetime(fixed - timedelta(hours=1), usegmt=True)
    assert parse_retry_after(ahead) == 60.0
    assert parse_retry_after(behind) == 0.0


# -- the fetcher's back-off and robots.txt reading -------------------------
def _fetcher(handler) -> Fetcher:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Fetcher(UA, limiter=RateLimiter(0, 0), client=client, retry_waits=(0, 0))


def test_a_retry_after_beyond_the_cap_trips_the_host_without_sleeping(clock):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(request.url.path)
        return httpx.Response(503, headers={"retry-after": "86400"})

    fetcher = _fetcher(handler)
    with pytest.raises(HostTripped):
        fetcher.fetch("https://example.org/a.pdf")
    assert fetcher.is_tripped("example.org")
    assert fetcher.tripped_hosts["example.org"] == "retry_after:86400s"
    assert calls == ["/a.pdf"]          # not retried
    assert clock.slept == []            # and not slept on
    fetcher.close()


def test_an_infinite_retry_after_trips_the_host_rather_than_crashing(clock):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(request.url.path)
        return httpx.Response(503, headers={"retry-after": "inf"})

    fetcher = _fetcher(handler)
    with pytest.raises(HostTripped):
        fetcher.fetch("https://example.org/a.pdf")
    assert fetcher.tripped_hosts["example.org"].startswith("retry_after:")
    assert calls == ["/a.pdf"]
    assert clock.slept == []
    fetcher.close()


def test_a_short_retry_after_is_still_honoured(clock):
    answers = iter([httpx.Response(503, headers={"retry-after": "5"}), httpx.Response(200)])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return next(answers)

    fetcher = _fetcher(handler)
    assert fetcher.fetch("https://example.org/a.pdf").status == 200
    # The retry waited out the Retry-After (plus the default crawl-delay slot).
    assert len(clock.slept) == 1 and clock.slept[0] >= 5.0
    assert not fetcher.is_tripped("example.org")
    fetcher.close()


def test_robots_txt_is_read_only_up_to_the_cap():
    consumed = {"bytes": 0}

    def huge_robots():
        head = b"User-agent: *\nDisallow: /x\n"
        consumed["bytes"] += len(head)
        yield head
        line = b"#" + b"a" * 1022 + b"\n"
        for _ in range(10 * 1024):           # 10 MiB of comment lines
            consumed["bytes"] += len(line)
            yield line

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=huge_robots())

    fetcher = _fetcher(handler)
    rules = fetcher.robots_for("https://example.org/")
    assert consumed["bytes"] < 1024 * 1024
    # Rules past the cap are unknown, so the file permits nothing (D-004).
    assert rules.oversize
    assert not rules.can_fetch(UA, "https://example.org/y")
    with pytest.raises(HostTripped):
        fetcher.check_policy("https://example.org/y")
    assert fetcher.tripped_hosts["example.org"] == "robots_oversize"
    fetcher.close()


def test_a_group_cut_off_by_the_robots_cap_does_not_allow_anything():
    """The prefix holds an empty group for this crawler, whose Disallow is cut.

    Parsed alone, that group would beat '*' and allow everything; the full file
    disallows everything.
    """
    head = b"User-agent: *\nDisallow: /\n"
    ours = b"User-agent: SALabourCorpusBot\n"
    filler = b"#" * (ROBOTS_MAX_BYTES - len(head) - len(ours) - 1) + b"\n"
    assert len(head + filler + ours) == ROBOTS_MAX_BYTES
    body = head + filler + ours + b"Disallow: /\n"

    fetcher = _fetcher(lambda request: httpx.Response(200, content=body))
    rules = fetcher.robots_for("https://example.org/")
    assert not rules.can_fetch(UA, "https://example.org/anything")
    assert not rules.can_fetch(UA, "https://example.org/")
    fetcher.close()


def test_a_robots_txt_exactly_at_the_cap_is_read_in_full():
    head = b"User-agent: *\n"
    tail = b"Disallow: /private/\n"
    filler = b"#" * (ROBOTS_MAX_BYTES - len(head) - len(tail) - 1) + b"\n"
    body = head + filler + tail
    assert len(body) == ROBOTS_MAX_BYTES

    fetcher = _fetcher(lambda request: httpx.Response(200, content=body))
    rules = fetcher.robots_for("https://example.org/")
    assert not rules.oversize
    assert not rules.can_fetch(UA, "https://example.org/private/x")
    assert rules.can_fetch(UA, "https://example.org/public/x")
    fetcher.close()


def test_a_long_404_robots_page_still_means_no_restrictions():
    """Only the rules depend on the body past the cap; RFC status handling does not."""
    body = b"<html><body>" + b"Not found. " * (ROBOTS_MAX_BYTES // 5) + b"</body></html>"
    fetcher = _fetcher(lambda request: httpx.Response(404, content=body))
    rules = fetcher.robots_for("https://example.org/")
    assert not rules.oversize
    assert rules.can_fetch(UA, "https://example.org/anything")
    fetcher.close()


# -- a Crawl-delay the crawler cannot honour --------------------------------
@pytest.mark.parametrize("value", ["1e12", "86400", "inf", "nan"])
def test_an_impossible_crawl_delay_trips_the_host(clock, value):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=f"User-agent: *\nCrawl-delay: {value}\n")
        calls.append(request.url.path)
        return httpx.Response(200)

    fetcher = _fetcher(handler)
    with pytest.raises(HostTripped):
        fetcher.fetch("https://example.org/a.pdf")
    with pytest.raises(HostTripped):
        fetcher.fetch("https://example.org/b.pdf")
    assert fetcher.tripped_hosts["example.org"].startswith("crawl_delay:")
    assert calls == []                   # nothing fetched, nothing slept
    assert clock.slept == []
    fetcher.close()


def test_a_long_but_bounded_crawl_delay_is_honoured(clock):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 60\n")
        return httpx.Response(200)

    fetcher = _fetcher(handler)
    fetcher.fetch("https://example.org/a.pdf")
    fetcher.fetch("https://example.org/b.pdf")
    assert not fetcher.is_tripped("example.org")
    assert clock.slept == [60.0]
    fetcher.close()


# -- crawler identity ------------------------------------------------------
def test_user_agent_always_identifies_the_crawler_and_operator():
    agent = build_user_agent("ops@example.test")
    assert "compatible" in agent
    assert "SALabourCorpusBot" in agent
    assert "ops@example.test" in agent
    assert "research crawler" in agent


def test_user_agent_requires_a_valid_contact_address():
    from labour_corpus.config import ConfigError

    for bad in ["", "   ", "not-an-email", "a@b"]:
        with pytest.raises(ConfigError):
            build_user_agent(bad)


def test_a_trip_by_another_worker_ends_this_fetchs_retries(clock):
    """The CCMA engine shares one Fetcher across worker threads.

    A host tripped while this fetch backs off (here: during its first 503)
    must not receive the retry; HostTripped is not retried.
    """
    calls: list[str] = []
    fetcher: Fetcher

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(request.url.path)
        fetcher.trip("example.org", "retry_after:86400s")  # another worker's verdict
        return httpx.Response(503)

    fetcher = _fetcher(handler)
    with pytest.raises(HostTripped):
        fetcher.fetch("https://example.org/b.pdf")
    assert calls == ["/b.pdf"]          # the retry was never sent
    fetcher.close()
