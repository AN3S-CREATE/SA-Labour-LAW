"""Optional Playwright wrapper for genuinely JS-rendered public pages.

Scope, stated plainly: this exists for pages whose content is assembled by
JavaScript on an otherwise open host. It is not a way around bot management.

If a page answers with a challenge, the browser path stops exactly where the
plain HTTP path stops. It does not wait for a challenge to clear, does not
solve one, does not retry into one, and does not disguise the client. The
crawler identity is appended to the browser user-agent so the operator remains
identifiable, and the host is tripped and reported as a gap instead.

Playwright is imported lazily so that installing it is optional and the test
suite never requires a browser unless RUN_BROWSER=1.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .challenge import ChallengeDetected, looks_like_challenge

if TYPE_CHECKING:  # types only; the runtime import stays lazy and optional
    from playwright.sync_api import Browser, Playwright

DEFAULT_TIMEOUT_MS = 30_000


class BrowserUnavailable(RuntimeError):
    """Raised when Playwright or its browser binary is not installed."""


@dataclass(slots=True)
class BrowserResult:
    url: str
    final_url: str
    status: int
    html: str
    content_type: str | None = None


def browser_enabled() -> bool:
    """Browser use is opt-in, both in tests and at runtime."""
    return os.environ.get("RUN_BROWSER") == "1"


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on local install
        raise BrowserUnavailable(
            "playwright is not installed. Run: pip install playwright && "
            "python -m playwright install chromium"
        ) from exc
    return sync_playwright


class BrowserFetcher:
    """Headless Chromium on the operator machine. Never a remote grid."""

    def __init__(self, user_agent: str, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> None:
        self.user_agent = user_agent
        self.timeout_ms = timeout_ms
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> "BrowserFetcher":
        sync_playwright = _load_playwright()
        playwright = self._playwright = sync_playwright().start()
        try:
            self._browser = playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - depends on local install
            playwright.stop()
            raise BrowserUnavailable(
                f"chromium could not be launched: {exc}. "
                "Run: python -m playwright install chromium"
            ) from exc
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def fetch(self, url: str, host: str) -> BrowserResult:
        """Render one page. Raises ChallengeDetected rather than waiting one out."""
        if self._browser is None:
            raise BrowserUnavailable("browser not started; use as a context manager")

        context = self._browser.new_context(user_agent=self.user_agent)
        try:
            page = context.new_page()
            response = page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
            status = response.status if response else 0
            content_type = (
                response.headers.get("content-type") if response else None
            )
            html = page.content()

            marker = looks_like_challenge(status, html)
            if marker:
                # Stop here. Do not wait for the challenge to clear.
                raise ChallengeDetected(host, url, status, marker)

            return BrowserResult(
                url=url,
                final_url=page.url,
                status=status,
                html=html,
                content_type=content_type,
            )
        finally:
            context.close()
