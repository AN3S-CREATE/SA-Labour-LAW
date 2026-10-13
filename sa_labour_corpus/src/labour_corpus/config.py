"""Configuration objects and the crawler identity policy.

The User-Agent policy is deliberately not configurable. The crawler always
identifies itself as an automated client and always carries the operator
contact address. Presenting as an ordinary desktop browser in order to get
past a bot-management control is circumvention of an access control, and is
out of scope for this project by design.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROJECT_NAME = "SALabourCorpusBot"
PROJECT_VERSION = "0.1.0"
PROJECT_URL = "https://github.com/operator/sa-labour-corpus"

# Hostnames that are paid legal publishers. Adding one of these as a crawl
# target is a hard error, not a warning (spec section 14).
PAID_HOST_MARKERS: tuple[str, ...] = (
    "lexis",
    "juta",
    "sabinet",
    "sallr",
    "westlaw",
    "practicallaw",
    "labouredge",
    "lexisnexis",
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Default case-law window is the last 15 calendar years.
DEFAULT_YEAR_SPAN = 15

MAX_BODY_BYTES = 50 * 1024 * 1024          # skip bodies larger than 50 MB
DISK_WARN_BYTES = 2 * 1024 * 1024 * 1024   # warn the operator past 2 GB
CONSECUTIVE_FAILURE_LIMIT = 10             # stop a source after 10 hard failures


class ConfigError(ValueError):
    """Raised when operator-supplied configuration is unusable."""


class PaidSourceError(ConfigError):
    """Raised when a paid legal publisher is used as a crawl target."""


def assert_not_paid_host(host: str) -> None:
    """Reject paid legal publishers as crawl targets. Hard error by design."""
    lowered = host.lower()
    for marker in PAID_HOST_MARKERS:
        if marker in lowered:
            raise PaidSourceError(
                f"{host!r} matches paid-publisher marker {marker!r}. "
                "Paid legal databases are out of scope; acquire a licence and "
                "use the operator inbox/ drop-folder instead."
            )


def build_user_agent(contact_email: str) -> str:
    """Build the one and only User-Agent this crawler ever sends.

    The string declares that the client is a robot and carries the operator
    contact address so a site owner can identify and reach whoever is running
    it. There is no browser-impersonation mode.
    """
    if not contact_email or not _EMAIL_RE.match(contact_email.strip()):
        raise ConfigError(
            "--contact-email must be a valid address; it is the only identity "
            "this crawler sends."
        )
    email = contact_email.strip()
    return (
        f"Mozilla/5.0 (compatible; {PROJECT_NAME}/{PROJECT_VERSION}; "
        f"+{PROJECT_URL}; research crawler; contact {email})"
    )


def default_year_range(today: date | None = None) -> tuple[int, int]:
    """Default case-law year window: the last DEFAULT_YEAR_SPAN calendar years."""
    current = (today or date.today()).year
    return current - DEFAULT_YEAR_SPAN + 1, current


def parse_year_range(raw: str | None, today: date | None = None) -> tuple[int, int]:
    """Parse a ``YYYY-YYYY`` operator string into an inclusive year range."""
    if raw is None or not raw.strip():
        return default_year_range(today)
    text = raw.strip()
    match = re.fullmatch(r"(\d{4})\s*-\s*(\d{4})", text)
    if not match:
        raise ConfigError(f"--years must look like YYYY-YYYY, got {raw!r}")
    start, end = int(match.group(1)), int(match.group(2))
    if start > end:
        raise ConfigError(f"--years start {start} is after end {end}")
    return start, end


@dataclass(slots=True)
class CorpusPaths:
    """Every on-disk location the corpus writes to, derived from ``--out``."""

    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def text(self) -> Path:
        return self.root / "text"

    @property
    def parsed(self) -> Path:
        return self.root / "parsed"

    @property
    def db(self) -> Path:
        return self.root / "db"

    @property
    def corpus_db(self) -> Path:
        return self.db / "corpus.sqlite"

    @property
    def index(self) -> Path:
        return self.root / "index"

    @property
    def fts_db(self) -> Path:
        return self.index / "fts.sqlite"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def coverage_md(self) -> Path:
        return self.reports / "COVERAGE.md"

    @property
    def run_log(self) -> Path:
        return self.reports / "RUN.jsonl"

    @property
    def error_log(self) -> Path:
        return self.reports / "ERRORS.jsonl"

    @property
    def ontology(self) -> Path:
        return self.root / "ontology"

    @property
    def tls_cache(self) -> Path:
        """Completed certificate bundles for hosts with an incomplete chain."""
        return self.db / "tls"

    @property
    def inbox(self) -> Path:
        """Operator-owned documents (e.g. awards the operator already holds)."""
        return self.root / "inbox"

    @property
    def is_volatile(self) -> bool:
        """True when the corpus sits in a directory the OS may clean.

        A corpus is expensive to build -- crawling is rate limited and OCR runs
        for minutes per document -- and everything except ``raw/`` is derived
        from it. Putting it under the system temp directory means an OS cleanup
        silently destroys the lot, which is exactly what happened once here.
        """
        root = str(self.root).replace("\\", "/").lower()
        markers = (
            "/temp/", "/tmp/", "/appdata/local/temp", "/var/folders/",
            "/windows/temp",
        )
        return any(marker in root + "/" for marker in markers)

    def volatile_warning(self) -> str:
        return (
            f"{self.root} looks like a temporary directory. The operating "
            "system may delete it without warning, taking the whole corpus "
            "with it -- including OCR transcriptions that take minutes per "
            "document to produce. Use a durable path such as ./data/corpus."
        )

    def ensure(self) -> None:
        """Create every corpus directory. Safe to call repeatedly."""
        for path in (
            self.raw, self.text, self.parsed, self.db,
            self.index, self.reports, self.ontology, self.inbox, self.tls_cache,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(slots=True)
class CrawlSettings:
    """Operator-facing crawl knobs."""

    contact_email: str
    paths: CorpusPaths
    years: tuple[int, int]
    source_ids: list[str] = field(default_factory=list)
    max_docs_per_source: int | None = None
    min_delay: float = 1.0
    max_delay: float = 2.0
    timeout: float = 45.0
    acknowledged_public_sources: bool = False

    @property
    def user_agent(self) -> str:
        return build_user_agent(self.contact_email)
