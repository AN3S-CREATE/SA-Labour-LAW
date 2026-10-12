"""robots.txt handling with RFC 9309 semantics, Crawl-delay and Content-Signal.

Four policies beyond plain robots parsing matter for this corpus:

* A host that answers robots.txt with a bot-management challenge is treated as
  refusing automated access entirely. We do not fall back to the RFC's
  "4xx means allow all" rule in that case.
* Content-Signal (the ai-train / ai-input / search reservation used by
  Laws.Africa and others) is parsed and honoured. This corpus exists to be read
  by a retrieval-augmented system, so a host declaring ai-input=no is not a
  lawful source for it even when robots.txt would otherwise allow the path.
* When several groups apply to this crawler (a CDN often prepends its own
  ``User-agent: *`` block above the site's), all of them are honoured, most
  restrictively: a path must be allowed by each, the longest Crawl-delay wins
  and any Content-Signal "no" wins.
* Rules that cannot be read in full are not read in part. A robots.txt longer
  than the fetcher reads permits nothing, and a non-finite Crawl-delay reads as
  infinite, so the fetcher trips the host (D-004).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse

from .challenge import looks_like_challenge

DEFAULT_CRAWL_DELAY = 1.0


@dataclass(slots=True)
class ContentSignal:
    """Parsed Content-Signal directives for one user-agent group."""

    search: bool | None = None
    ai_input: bool | None = None
    ai_train: bool | None = None
    raw: str = ""

    def permits_rag_corpus(self) -> bool:
        """True unless the host expressly reserved rights against AI input.

        The ai-input signal covers retrieval-augmented generation and
        grounding, which is precisely what this corpus is built for. A value of
        None means the operator neither granted nor restricted permission, so
        collection is not barred by signal.
        """
        return self.ai_input is not False


@dataclass(slots=True)
class _Group:
    agents: list[str] = field(default_factory=list)
    allows: list[str] = field(default_factory=list)
    disallows: list[str] = field(default_factory=list)
    crawl_delay: float | None = None
    signal: ContentSignal | None = None


@dataclass(slots=True)
class RobotsRules:
    """Evaluated robots rules for a single host."""

    host: str
    fetched: bool = False
    status: int | None = None
    challenge_marker: str | None = None
    unreachable: bool = False
    # robots.txt was longer than the crawler reads. Any rule past the cut could
    # be a Disallow (or a whole group for this crawler), so like an unreachable
    # file it permits nothing (D-004).
    oversize: bool = False
    groups: list[_Group] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)

    @property
    def blocked_by_challenge(self) -> bool:
        return self.challenge_marker is not None

    def _matching_groups(self, user_agent: str) -> list[_Group]:
        """Every group that applies to this user-agent (RFC 9309 s2.2.1).

        The longest agent token that prefixes ours wins; failing that, the
        ``*`` groups apply. Every group naming the winner is returned, not just
        the first: a CDN that prepends its own ``User-agent: *`` block must not
        hide the site's ``User-agent: *`` rules further down.
        """
        token = _agent_token(user_agent)
        best_len = -1
        for group in self.groups:
            for agent in group.agents:
                low = agent.lower()
                if low != "*" and token.startswith(low) and len(low) > best_len:
                    best_len = len(low)

        def names_winner(agent: str) -> bool:
            low = agent.lower()
            if best_len < 0:
                return low == "*"
            return low != "*" and len(low) == best_len and token.startswith(low)

        return [g for g in self.groups if any(names_winner(a) for a in g.agents)]

    def can_fetch(self, user_agent: str, url: str) -> bool:
        """True only when every applicable group permits the path.

        RFC 9309 merges matching groups into one rule set. Requiring each group
        to allow the path is never looser than that merge, nor than reading any
        single group: an Allow in one group cannot override a Disallow the
        operator wrote in another. Like D-004, a deliberate conservative
        deviation; it can only cost a document, never take one we may not.
        """
        if self.blocked_by_challenge or self.unreachable or self.oversize:
            return False
        path = _rule_path(url)
        return all(_group_allows(group, path) for group in self._matching_groups(user_agent))

    def crawl_delay(self, user_agent: str) -> float:
        """The longest Crawl-delay any applicable group declares.

        Never below DEFAULT_CRAWL_DELAY. A non-finite value (``inf``, ``nan``)
        is read as infinite rather than ignored: a delay that cannot be
        honoured is a refusal, and the fetcher trips a host whose delay is
        beyond what it will wait (http.MAX_CRAWL_DELAY).
        """
        delay = DEFAULT_CRAWL_DELAY
        for group in self._matching_groups(user_agent):
            if group.crawl_delay is None:
                continue
            if not math.isfinite(group.crawl_delay):
                return math.inf
            delay = max(delay, group.crawl_delay)
        return delay

    def content_signal(self, user_agent: str) -> ContentSignal:
        """Content-Signal across applicable groups; any reservation wins."""
        signals = [
            g.signal for g in self._matching_groups(user_agent) if g.signal is not None
        ]
        if not signals:
            return ContentSignal()
        if len(signals) == 1:
            return signals[0]
        return ContentSignal(
            search=_most_restrictive([s.search for s in signals]),
            ai_input=_most_restrictive([s.ai_input for s in signals]),
            ai_train=_most_restrictive([s.ai_train for s in signals]),
            raw="; ".join(s.raw for s in signals),
        )


def _group_allows(group: _Group, path: str) -> bool:
    allow = _longest_match(group.allows, path)
    disallow = _longest_match(group.disallows, path)
    if disallow is None:
        return True
    if allow is None:
        return False
    # Longest rule wins; ties go to Allow (RFC 9309).
    return allow >= disallow


def _most_restrictive(values: list[bool | None]) -> bool | None:
    """Combine one signal across groups: an express "no" anywhere wins."""
    if any(value is False for value in values):
        return False
    if any(value is True for value in values):
        return True
    return None


def _agent_token(user_agent: str) -> str:
    """Reduce a full UA string to the product token robots.txt matches on."""
    lowered = user_agent.lower()
    match = re.search(r"([a-z0-9._-]*bot[a-z0-9._-]*)", lowered)
    if match:
        return match.group(1)
    return lowered.split("/")[0].strip()


def _rule_path(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path = path + "?" + parsed.query
    return path


def _longest_match(rules: list[str], path: str) -> int | None:
    """Return the length of the longest rule matching path, else None."""
    best: int | None = None
    for rule in rules:
        if _rule_matches(rule, path) and (best is None or len(rule) > best):
            best = len(rule)
    return best


def _rule_matches(rule: str, path: str) -> bool:
    """Match a robots rule supporting star wildcards and an end-anchor."""
    if rule == "":
        return False
    anchored = rule.endswith("$")
    body = rule[:-1] if anchored else rule
    pattern = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    regex = "^" + pattern + "$" if anchored else "^" + pattern
    return re.search(regex, path) is not None


def parse_robots(host: str, status: int | None, body: str | bytes | None) -> RobotsRules:
    """Parse a robots.txt payload into RobotsRules.

    RFC 9309 status handling, with one deliberate deviation: a challenge or a
    bot refusal is NOT treated as "unavailable means allow all".
    """
    rules = RobotsRules(host=host, status=status)

    marker = looks_like_challenge(status, body)
    if marker:
        rules.challenge_marker = marker
        return rules

    if status is None or status >= 500 or status == 429:
        rules.unreachable = True
        return rules

    if status >= 400:
        # "Unavailable" status: no restrictions were published.
        rules.fetched = False
        return rules

    if isinstance(body, bytes):
        text = body.decode("utf-8", errors="ignore")
    else:
        text = body or ""

    rules.fetched = True
    current: _Group | None = None
    expecting_agent = False

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()

        if key == "user-agent":
            if current is None or not expecting_agent:
                current = _Group()
                rules.groups.append(current)
                expecting_agent = True
            current.agents.append(value)
            continue

        if key == "sitemap":
            rules.sitemaps.append(value)
            continue

        if current is None:
            continue
        expecting_agent = False

        if key == "disallow":
            current.disallows.append(value)
        elif key == "allow":
            current.allows.append(value)
        elif key == "crawl-delay":
            try:
                current.crawl_delay = float(value)
            except ValueError:
                pass
        elif key == "content-signal":
            current.signal = _parse_content_signal(value)

    return rules


def _parse_content_signal(value: str) -> ContentSignal:
    signal = ContentSignal(raw=value)
    for part in value.split(","):
        name, _, setting = part.partition("=")
        name = name.strip().lower().replace("_", "-")
        flag = setting.strip().lower() == "yes"
        if name == "search":
            signal.search = flag
        elif name == "ai-input":
            signal.ai_input = flag
        elif name == "ai-train":
            signal.ai_train = flag
    return signal


def robots_url_for(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
