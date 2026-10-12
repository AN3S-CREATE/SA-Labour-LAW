"""Polite HTTP fetch layer: rate limiting, retry, checksums and host tripping.

Design rules that are not negotiable at runtime:

* One honest User-Agent, built from the operator contact address.
* robots.txt is consulted before any document fetch on a host.
* A bot-management challenge trips the host for the whole run. We never retry
  into a challenge and never attempt to satisfy one.
* Bodies above the size cap are not stored; headers and URL are kept instead.
"""

from __future__ import annotations

import hashlib
import math
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from collections.abc import Callable
from urllib.parse import urlparse

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_chain,
    wait_exponential,
    wait_fixed,
)
from tenacity.wait import wait_base

from .challenge import ChallengeDetected, looks_like_challenge
from .config import MAX_BODY_BYTES, assert_not_paid_host
from .robots import RobotsRules, parse_robots, robots_url_for

RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504, 408})
MAX_ATTEMPTS = 5
# A host asking us to wait longer than this is asking us to stop for the run.
# We trip it and record the refusal rather than sleep through it.
MAX_RETRY_AFTER = 900.0
# The same bound for a robots.txt Crawl-delay: one beyond it (or a non-finite
# one) is a refusal recorded as a gap, never a sleep that hangs or crashes.
MAX_CRAWL_DELAY = MAX_RETRY_AFTER
# RFC 9309 s2.5 lets a crawler stop reading robots.txt after at least 500 KiB.
# A longer file is not parsed in part: it is treated as disallowing everything.
ROBOTS_MAX_BYTES = 512 * 1024

# Far beyond MAX_RETRY_AFTER. An "inf" or a 5000-digit Retry-After is clamped
# to it so it stays finite (printable, int()-able) and still trips the host.
_RETRY_AFTER_CEILING = 1e12


class RetryableStatus(RuntimeError):
    """A transient HTTP status that tenacity should retry."""

    def __init__(self, status: int, url: str, retry_after: float | None = None) -> None:
        self.status = status
        self.url = url
        self.retry_after = retry_after
        super().__init__(f"retryable status {status} for {url}")


class HostTripped(RuntimeError):
    """Raised when a host has already been tripped for this run."""


class RobotsDisallowed(RuntimeError):
    """Raised when robots.txt forbids the path."""


class ContentSignalOptOut(RuntimeError):
    """Raised when a host expressly reserved rights against AI input."""


@dataclass(slots=True)
class FetchResult:
    url: str
    final_url: str
    status: int
    headers: dict[str, str]
    body: bytes | None
    content_sha256: str
    bytes: int
    elapsed_ms: int
    from_cache: bool = False
    oversize: bool = False

    @property
    def content_type(self) -> str | None:
        return self.headers.get("content-type")


@dataclass(slots=True)
class RateLimiter:
    """Per-host jittered delay.

    The delay for a host is the largest of the global pace, any per-host pace
    set with :meth:`set_host_delay` (a source's configured delay), and the
    robots crawl-delay.
    """

    min_delay: float = 1.0
    max_delay: float = 2.0
    _last: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _host_delays: dict[str, tuple[float, float]] = field(default_factory=dict)

    def set_host_delay(self, host: str, min_delay: float, max_delay: float) -> None:
        """Slow one host down without touching the pace of any other.

        Only ever raises the pace floor: when two sources share a host, the
        slower configuration wins whatever order they run in.
        """
        host = host.lower()
        with self._lock:
            old_min, old_max = self._host_delays.get(host, (0.0, 0.0))
            self._host_delays[host] = (max(old_min, min_delay), max(old_max, max_delay))

    def wait(self, host: str, crawl_delay: float | None = None) -> float:
        """Sleep as long as politeness requires; return seconds actually slept."""
        with self._lock:
            host_min, host_max = self._host_delays.get(host.lower(), (0.0, 0.0))
            floor = max(self.min_delay, host_min, crawl_delay or 0.0)
            ceiling = max(self.max_delay, host_max, floor)
            delay = random.uniform(floor, ceiling)
            previous = self._last.get(host)
            now = time.monotonic()
            if previous is None:
                self._last[host] = now
                return 0.0
            target = previous + delay
            sleep_for = max(0.0, target - now)
            self._last[host] = max(now, target)
        if sleep_for > 0:
            time.sleep(sleep_for)
        return sleep_for

    def penalise(self, host: str, seconds: float) -> None:
        """Honour a Retry-After by pushing this host's next slot out."""
        with self._lock:
            base = max(self._last.get(host, time.monotonic()), time.monotonic())
            self._last[host] = base + max(0.0, seconds)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def parse_retry_after(value: str | None) -> float | None:
    """Seconds a Retry-After header asks us to wait, or None when unusable.

    RFC 9110 s10.2.3 allows delay-seconds or an HTTP-date. Any other number
    float() reads ("2.5", "+30", "1e3") is honoured too: ignoring it would
    retry sooner than the server asked. An infinite or absurd wait is clamped
    to a finite value far above MAX_RETRY_AFTER, so the caller trips the host
    instead of sleeping. Only a value that names no wait (``nan``, a negative
    number, other text) gives None. A date in the past means "now".
    """
    if not value:
        return None
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        pass
    else:
        if math.isnan(seconds) or seconds < 0:
            return None
        return min(seconds, _RETRY_AFTER_CEILING)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if when.tzinfo is None:
        # RFC 9110 HTTP-dates are always GMT; a naive parse means UTC.
        when = when.replace(tzinfo=timezone.utc)
    seconds = (when - datetime.now(timezone.utc)).total_seconds()
    return min(max(0.0, seconds), _RETRY_AFTER_CEILING)


class Fetcher:
    """HTTP client that enforces the project's collection policy."""

    def __init__(
        self,
        user_agent: str,
        *,
        timeout: float = 45.0,
        limiter: RateLimiter | None = None,
        max_body_bytes: int = MAX_BODY_BYTES,
        client: httpx.Client | None = None,
        on_event: Callable[[dict], None] | None = None,
        tls_cache_dir: Path | None = None,
        retry_waits: tuple[float, ...] | None = None,
    ) -> None:
        self.user_agent = user_agent
        # An explicit backoff schedule, e.g. (1, 4, 16): one retry per entry.
        # None keeps the crawler's default exponential policy.
        self.retry_waits = retry_waits
        self.timeout = timeout
        self.limiter = limiter or RateLimiter()
        self.max_body_bytes = max_body_bytes
        self.on_event = on_event
        self._robots: dict[str, RobotsRules] = {}
        self._tripped: dict[str, str] = {}
        self._tls_cache_dir = tls_cache_dir
        # Per-host clients built with a completed certificate chain. Only
        # created for hosts that serve an incomplete chain; see tls.py.
        self._chain_clients: dict[str, httpx.Client] = {}
        self._chain_attempted: set[str] = set()
        self._client = client or httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers=self._default_headers(),
        )

    def _default_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-ZA,en;q=0.9",
        }

    def _client_for(self, host: str) -> httpx.Client:
        """The client to use for a host: chain-completed one when we built it."""
        return self._chain_clients.get(host, self._client)

    def _try_complete_chain(self, host: str) -> bool:
        """Fetch a missing intermediate for a host, once per run.

        Returns True when a client with a completed, fully verified chain is
        now available. Verification is never disabled: the intermediate must
        itself chain to a root certifi already trusts.
        """
        if host in self._chain_clients:
            return True
        if host in self._chain_attempted or self._tls_cache_dir is None:
            return False
        self._chain_attempted.add(host)

        from .tls import complete_chain

        outcome = complete_chain(host, self._tls_cache_dir)
        self._emit(
            {
                "stage": "tls",
                "host": host,
                "completed": outcome.completed,
                "issuer_url": outcome.issuer_url,
                "detail": outcome.detail,
            }
        )
        if not outcome.completed or outcome.bundle_path is None:
            return False

        self._chain_clients[host] = httpx.Client(
            follow_redirects=True,
            timeout=self.timeout,
            headers=self._default_headers(),
            verify=str(outcome.bundle_path),
        )
        return True

    # -- lifecycle ------------------------------------------------------
    def close(self) -> None:
        self._client.close()
        for client in self._chain_clients.values():
            client.close()
        self._chain_clients.clear()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- policy ---------------------------------------------------------
    @property
    def tripped_hosts(self) -> dict[str, str]:
        return dict(self._tripped)

    def trip(self, host: str, reason: str) -> None:
        self._tripped.setdefault(host, reason)

    def is_tripped(self, host: str) -> bool:
        return host in self._tripped

    def robots_for(self, url: str) -> RobotsRules:
        """Fetch and cache robots.txt for the host behind url."""
        host = host_of(url)
        cached = self._robots.get(host)
        if cached is not None:
            return cached

        robots_url = robots_url_for(url)
        rules: RobotsRules | None = None
        try:
            rules = self._read_robots(host, robots_url)
        except httpx.HTTPError as exc:
            # A site that serves an incomplete certificate chain fails here even
            # though a browser opens it. Complete the chain and retry once,
            # keeping verification fully on.
            from .tls import looks_like_incomplete_chain

            if looks_like_incomplete_chain(exc) and self._try_complete_chain(host):
                try:
                    rules = self._read_robots(host, robots_url)
                except httpx.HTTPError as retry_exc:
                    exc = retry_exc
            if rules is None:
                rules = parse_robots(host, None, None)
                rules.unreachable = True
                self._emit({"stage": "robots", "url": robots_url, "error": str(exc)})

        self._robots[host] = rules
        if rules.blocked_by_challenge:
            self.trip(host, f"challenge_on_robots:{rules.challenge_marker}")
        self._emit(
            {
                "stage": "robots",
                "url": robots_url,
                "status": rules.status,
                "challenge": rules.challenge_marker,
                "unreachable": rules.unreachable,
                "oversize": rules.oversize,
            }
        )
        return rules

    def _read_robots(self, host: str, robots_url: str) -> RobotsRules:
        """GET robots.txt, reading at most ROBOTS_MAX_BYTES of it.

        The body is streamed so that an oversized robots.txt cannot fill
        memory. A file longer than the cap is not parsed in part: a Disallow,
        or a whole group for this crawler, past the cut would be lost, and the
        prefix could then allow what the full file forbids. Its rules are
        marked oversize, which permits nothing (D-004). Status handling is
        unchanged: the status, and the challenge scan of the first 200 KB,
        do not depend on the rest of the body.
        """
        buffer = bytearray()
        truncated = False
        with self._client_for(host).stream("GET", robots_url, timeout=self.timeout) as response:
            status = response.status_code
            for chunk in response.iter_bytes():
                buffer.extend(chunk)
                if len(buffer) > ROBOTS_MAX_BYTES:
                    truncated = True
                    break
        rules = parse_robots(host, status, bytes(buffer[:ROBOTS_MAX_BYTES]))
        if truncated and rules.fetched:
            rules.oversize = True
            self._emit({"stage": "robots", "url": robots_url, "truncated_at": ROBOTS_MAX_BYTES})
        return rules

    def check_policy(self, url: str) -> RobotsRules:
        """Raise if this URL may not be fetched. Returns the host's rules."""
        host = host_of(url)
        assert_not_paid_host(host)
        if self.is_tripped(host):
            raise HostTripped(f"{host} tripped earlier this run: {self._tripped[host]}")

        rules = self.robots_for(url)
        if rules.blocked_by_challenge:
            raise ChallengeDetected(host, url, rules.status, rules.challenge_marker or "")
        if rules.unreachable:
            self.trip(host, "robots_unreachable")
            raise HostTripped(f"{host} robots.txt unreachable; treating as disallow")
        if rules.oversize:
            self.trip(host, "robots_oversize")
            raise HostTripped(
                f"{host} robots.txt is longer than {ROBOTS_MAX_BYTES} bytes; rules past "
                "that point cannot be read, so treating as disallow"
            )
        if not rules.content_signal(self.user_agent).permits_rag_corpus():
            raise ContentSignalOptOut(
                f"{host} declares ai-input=no; it is not a lawful source for a "
                "retrieval corpus under this user-agent"
            )
        delay = rules.crawl_delay(self.user_agent)
        if delay > MAX_CRAWL_DELAY:
            # Honouring it would hang the run (or crash the sleep); ignoring it
            # would crawl faster than the host asked. Stop for the run instead.
            self.trip(host, f"crawl_delay:{delay:g}s")
            raise HostTripped(
                f"{host} robots.txt asks for a Crawl-delay of {delay:g}s, beyond the "
                f"{MAX_CRAWL_DELAY:g}s this crawler will wait; host tripped for this run"
            )
        if not rules.can_fetch(self.user_agent, url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        return rules

    # -- fetching -------------------------------------------------------
    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        """Fetch one URL under full policy, rate limiting and retry."""
        rules = self.check_policy(url)
        host = host_of(url)
        crawl_delay = rules.crawl_delay(self.user_agent)

        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        def _attempt() -> FetchResult:
            # Re-checked on every attempt, not just once before the retries:
            # another worker sharing this fetcher may have tripped the host
            # (a challenge, or a Retry-After beyond the cap) while this fetch
            # was backing off. HostTripped is not retried.
            if self.is_tripped(host):
                raise HostTripped(f"{host} tripped earlier this run: {self._tripped[host]}")
            self.limiter.wait(host, crawl_delay)
            started = time.perf_counter()
            with self._client_for(host).stream("GET", url, headers=headers) as response:
                status = response.status_code
                if status in RETRY_STATUSES:
                    retry_after = parse_retry_after(response.headers.get("retry-after"))
                    if retry_after is not None and retry_after > MAX_RETRY_AFTER:
                        # Back off entirely rather than sleep for hours (D-012).
                        self.trip(host, f"retry_after:{int(retry_after)}s")
                        raise HostTripped(
                            f"{host} answered HTTP {status} with Retry-After "
                            f"{int(retry_after)}s for {url}; host tripped for this run"
                        )
                    if retry_after:
                        self.limiter.penalise(host, retry_after)
                    raise RetryableStatus(status, url, retry_after)

                if status == 304:
                    elapsed = int((time.perf_counter() - started) * 1000)
                    return FetchResult(
                        url=url,
                        final_url=str(response.url),
                        status=304,
                        headers=dict(response.headers),
                        body=None,
                        content_sha256="",
                        bytes=0,
                        elapsed_ms=elapsed,
                        from_cache=True,
                    )

                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > self.max_body_bytes:
                    elapsed = int((time.perf_counter() - started) * 1000)
                    return FetchResult(
                        url=url,
                        final_url=str(response.url),
                        status=status,
                        headers=dict(response.headers),
                        body=None,
                        content_sha256="",
                        bytes=int(declared),
                        elapsed_ms=elapsed,
                        oversize=True,
                    )

                chunks: list[bytes] = []
                total = 0
                oversize = False
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self.max_body_bytes:
                        oversize = True
                        break
                    chunks.append(chunk)

                elapsed = int((time.perf_counter() - started) * 1000)
                body = b"".join(chunks) if not oversize else None

                marker = looks_like_challenge(status, body)
                if marker:
                    self.trip(host, f"challenge:{marker}")
                    raise ChallengeDetected(host, url, status, marker)

                return FetchResult(
                    url=url,
                    final_url=str(response.url),
                    status=status,
                    headers=dict(response.headers),
                    body=body,
                    content_sha256=sha256_bytes(body) if body is not None else "",
                    bytes=total,
                    elapsed_ms=elapsed,
                    oversize=oversize,
                )

        wait: wait_base
        if self.retry_waits is not None:
            stop = stop_after_attempt(len(self.retry_waits) + 1)
            wait = wait_chain(*(wait_fixed(s) for s in self.retry_waits))
        else:
            stop = stop_after_attempt(MAX_ATTEMPTS)
            wait = wait_exponential(multiplier=1.5, min=2, max=60)
        retryer = Retrying(
            retry=retry_if_exception_type((RetryableStatus, httpx.TransportError)),
            stop=stop,
            wait=wait,
            reraise=True,
            before_sleep=self._log_retry,
        )
        result = retryer(_attempt)
        self._emit(
            {
                "stage": "fetch",
                "url": url,
                "status": result.status,
                "bytes": result.bytes,
                "ms": result.elapsed_ms,
            }
        )
        return result

    def _log_retry(self, state: RetryCallState) -> None:
        exc = state.outcome.exception() if state.outcome else None
        self._emit(
            {
                "stage": "retry",
                "attempt": state.attempt_number,
                "error": str(exc) if exc else None,
            }
        )

    def _emit(self, event: dict) -> None:
        if self.on_event is not None:
            self.on_event(event)
