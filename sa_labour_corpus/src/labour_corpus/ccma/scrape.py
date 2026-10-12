"""Scrape: fetch listing and record pages through the policy-enforcing client.

``LiveGetter`` is the only object in the engine that touches the network. It
validates every URL against the source's allow-listed hosts (and paths, where
the source has a path allow-list) before the request and again after
redirects, and delegates to ``http.Fetcher`` for robots.txt, content signals,
challenge detection, rate limiting and retry.

``robots_preflight`` reads robots.txt for every allow-listed path of a source
before anything else is fetched from it. It requests robots.txt and nothing
else.

``inspect_record`` fetches and parses one record without saving it, which is
how an operator checks a parser against a live page before a bulk run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..challenge import ChallengeDetected
from ..http import ContentSignalOptOut, Fetcher, HostTripped, RobotsDisallowed
from .allowlist import CcmaSource, validate_url
from .extract import Parsed, parse_artifact
from .troubleshoot import EmptyBody, HttpStatusFailure


@dataclass(slots=True)
class Page:
    url: str
    final_url: str
    status: int
    body: bytes
    content_type: str | None
    sha256: str


@dataclass(slots=True)
class RobotsVerdict:
    """Whether robots.txt lets this crawler fetch one allow-listed URL."""

    url: str
    allowed: bool
    reason: str
    robots_status: int | None = None


class Getter(Protocol):
    def get(self, url: str) -> Page: ...

    def check_robots(self, url: str) -> RobotsVerdict: ...


class LiveGetter:
    """Network access for one source, confined to that source's hosts and paths."""

    def __init__(self, fetcher: Fetcher, source: CcmaSource) -> None:
        self.fetcher = fetcher
        self.hosts = frozenset(source.hosts)
        self.paths = tuple(source.allowed_paths)

    def check_robots(self, url: str) -> RobotsVerdict:
        """Read robots.txt for the URL's host and judge the URL. Fetches nothing else.

        A bot refusal or challenge on robots.txt itself is a refusal (D-004),
        as are an unreachable robots.txt and a content-signal opt-out.
        """
        validate_url(url, self.hosts, self.paths)
        try:
            rules = self.fetcher.check_policy(url)
        except (ChallengeDetected, RobotsDisallowed, ContentSignalOptOut, HostTripped) as exc:
            status = self.fetcher.robots_for(url).status   # cached; no second request
            return RobotsVerdict(url, False, f"{type(exc).__name__}: {exc}", status)
        if rules.fetched:
            reason = f"robots.txt (HTTP {rules.status}) permits this path"
        else:
            reason = f"no robots rules published (robots.txt HTTP {rules.status}); RFC 9309 allows"
        return RobotsVerdict(url, True, reason, rules.status)

    def get(self, url: str) -> Page:
        validate_url(url, self.hosts, self.paths)
        result = self.fetcher.fetch(url)
        validate_url(result.final_url, self.hosts, self.paths)   # open-redirect guard
        if result.status >= 400:
            raise HttpStatusFailure(result.status, url)
        if result.oversize:
            raise EmptyBody(f"body over the size cap was not stored: {url}")
        if not result.body:
            raise EmptyBody(f"HTTP {result.status} with an empty body: {url}")
        return Page(
            url=url,
            final_url=result.final_url,
            status=result.status,
            body=result.body,
            content_type=result.content_type,
            sha256=result.content_sha256,
        )


class FixtureGetter:
    """Serves saved pages from disk by URL. For tests and offline dry runs.

    ``robots_refusals`` maps a URL to the reason robots.txt refuses it; every
    other URL is allowed. Robots checks are logged apart from page requests.
    """

    def __init__(self, mapping: dict[str, Path],
                 robots_refusals: dict[str, str] | None = None) -> None:
        self.mapping = mapping
        self.robots_refusals = robots_refusals or {}
        self.requested: list[str] = []
        self.robots_checked: list[str] = []

    def check_robots(self, url: str) -> RobotsVerdict:
        self.robots_checked.append(url)
        if url in self.robots_refusals:
            return RobotsVerdict(url, False, self.robots_refusals[url], 403)
        return RobotsVerdict(url, True, "fixture: robots permit", 200)

    def get(self, url: str) -> Page:
        import hashlib

        self.requested.append(url)
        path = self.mapping.get(url)
        if path is None:
            raise HttpStatusFailure(404, url)
        body = Path(path).read_bytes()
        if not body:
            raise EmptyBody(url)
        content_type = "application/pdf" if str(path).lower().endswith(".pdf") else "text/html"
        return Page(url, url, 200, body, content_type, hashlib.sha256(body).hexdigest())


def robots_preflight(getter: Getter, source: CcmaSource) -> list[RobotsVerdict]:
    """Judge every allow-listed path of a source before anything is fetched from it.

    Operator rule 2: one refused path refuses the whole source. The caller
    decides what to do with the verdicts; this function only reads robots.txt.
    """
    return [getter.check_robots(url) for url in source.robots_probe_urls()]


def inspect_record(getter: Getter, source: CcmaSource, url: str) -> tuple[Page, Parsed]:
    """Fetch and parse one record page; persist nothing."""
    page = getter.get(url)
    parsed = parse_artifact(page.body, page.content_type, page.final_url, source.record_kind,
                            title_hint=source.title)
    return page, parsed
