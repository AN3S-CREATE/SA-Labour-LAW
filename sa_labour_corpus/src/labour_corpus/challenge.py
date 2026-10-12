"""Detection of bot-management challenge and interstitial pages.

This module only *recognises* challenges. Nothing in this project attempts to
solve, bypass or negotiate one. When a challenge is detected the host is
tripped for the remainder of the run and the block is recorded as a coverage
gap, which is the behaviour required by the specification.
"""

from __future__ import annotations

# Markers that identify an anti-bot interstitial rather than real content.
_CHALLENGE_MARKERS: tuple[str, ...] = (
    "just a moment...",
    "_cf_chl_opt",
    "cdn-cgi/challenge-platform",
    "challenges.cloudflare.com",
    "enable javascript and cookies to continue",
    "checking your browser before accessing",
    "ddos protection by cloudflare",
    "attention required! | cloudflare",
    "px-captcha",
    "captcha-delivery.com",
    "incapsula incident id",
    "/_incapsula_resource",
    "recaptcha/api.js",
    "hcaptcha.com/1/api.js",
    "request unsuccessful. incapsula",
)

# HTTP statuses that commonly carry a challenge or an outright bot refusal.
BLOCKING_STATUSES: frozenset[int] = frozenset({401, 403, 407, 451})


class ChallengeDetected(RuntimeError):
    """Raised when a host answers with a bot-management challenge.

    Carries the host so the caller can trip it for the run.
    """

    def __init__(self, host: str, url: str, status: int | None, marker: str) -> None:
        self.host = host
        self.url = url
        self.status = status
        self.marker = marker
        super().__init__(
            f"{host} served a bot-management challenge for {url} "
            f"(status={status}, marker={marker!r}). Host tripped for this run; "
            "this project does not attempt to bypass access controls."
        )


def find_challenge_marker(body: bytes | str | None) -> str | None:
    """Return the first challenge marker found in ``body``, else ``None``."""
    if not body:
        return None
    if isinstance(body, bytes):
        text = body[:200_000].decode("utf-8", errors="ignore")
    else:
        text = body[:200_000]
    lowered = text.lower()
    for marker in _CHALLENGE_MARKERS:
        if marker in lowered:
            return marker
    return None


def looks_like_challenge(status: int | None, body: bytes | str | None) -> str | None:
    """Return a marker string when the response is a challenge, else ``None``.

    A blocking status with an HTML-ish body but no recognisable marker is still
    reported, because a bare 403 to an identified crawler is a refusal of
    automated access in substance even when it carries no Cloudflare branding.
    """
    marker = find_challenge_marker(body)
    if marker:
        return marker
    if status in BLOCKING_STATUSES:
        return f"http_{status}_bot_refusal"
    return None
