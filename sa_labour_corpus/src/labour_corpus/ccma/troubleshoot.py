"""Failure classification, diagnosis and the secret scan.

Every failure becomes one machine-readable ``errors`` row with a class and a
retryable flag, the record is isolated, and the run continues. Only the
transient classes are ever retried; a refusal is never retried into.
"""

from __future__ import annotations

import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import httpx

from ..challenge import ChallengeDetected
from ..http import ContentSignalOptOut, HostTripped, RetryableStatus, RobotsDisallowed
from .allowlist import UrlRejected
from .catalogue import Catalogue
from .paths import MIN_FREE_BYTES, CcmaPaths


class ErrorClass(str, Enum):
    HTTP_403 = "http_403"
    HTTP_404 = "http_404"
    HTTP_429 = "http_429"
    HTTP_5XX = "http_5xx"
    HTTP_OTHER = "http_other"
    TIMEOUT = "timeout"
    NETWORK = "network"
    EMPTY_BODY = "empty_body"
    PARSE_MISS = "parse_miss"
    PARSE_UNSUPPORTED = "parse_unsupported"
    CHECKSUM_CONFLICT = "checksum_conflict"
    DISK_FULL = "disk_full"
    SCHEMA_DRIFT = "schema_drift"
    POLICY_BLOCK = "policy_block"
    MINIMISATION_FAILED = "minimisation_failed"


RETRYABLE: frozenset[ErrorClass] = frozenset({
    ErrorClass.HTTP_429, ErrorClass.HTTP_5XX, ErrorClass.TIMEOUT, ErrorClass.NETWORK,
})

# What the operator should do about each class. Shown by `ccma troubleshoot`.
REMEDIES: dict[ErrorClass, str] = {
    ErrorClass.HTTP_403: "The host refused the request. Do not retry or change identity; "
                         "ask the publisher for research access (see D-001).",
    ErrorClass.HTTP_404: "The record moved or was withdrawn. Re-run `ccma find` to refresh URLs.",
    ErrorClass.HTTP_429: "Rate limited. Retry later with `ccma download --retry-failed`; "
                         "the engine already backed off 1s/4s/16s.",
    ErrorClass.HTTP_5XX: "Server error on the source. Retry later; it is not a local fault.",
    ErrorClass.HTTP_OTHER: "Unexpected HTTP status; inspect the message.",
    ErrorClass.TIMEOUT: "Timed out after 20s. Retry later or check connectivity.",
    ErrorClass.NETWORK: "Connection failed. Check connectivity and DNS, then retry.",
    ErrorClass.EMPTY_BODY: "The server returned no content. Retry once; if persistent, "
                           "the record is not really published.",
    ErrorClass.PARSE_MISS: "No case number or citation found. Selectors may have drifted: "
                           "compare the raw file against tests/fixtures before changing "
                           "a parser, and record the change in the audit log.",
    ErrorClass.PARSE_UNSUPPORTED: "Readable format without an installed reader (e.g. legacy "
                                  "Word needs antiword).",
    ErrorClass.CHECKSUM_CONFLICT: "The source now serves different bytes for a record already "
                                  "held. The new bytes are quarantined; compare them by hand "
                                  "before accepting either version.",
    ErrorClass.DISK_FULL: "Less than 500 MB free. Free space before the next run.",
    ErrorClass.SCHEMA_DRIFT: "The listing no longer has the expected structure. Update the "
                             "fixture from a fresh page, then the parser.",
    ErrorClass.POLICY_BLOCK: "Refused by policy (robots.txt, bot management, content signal, "
                             "or allow-list). Not retried. The lawful route is a data "
                             "agreement with the publisher.",
    ErrorClass.MINIMISATION_FAILED: "A knowledge unit still contained a personal identifier "
                                    "after minimisation and was dropped. Report it as a bug.",
}


class DiskFull(RuntimeError):
    """Free space fell below the per-source floor."""


class EmptyBody(RuntimeError):
    """A 2xx response with no content."""


class SchemaDrift(RuntimeError):
    """A listing page lacked the structure its parser was tested against."""


class HttpStatusFailure(RuntimeError):
    """A final, non-retryable HTTP status such as 404."""

    def __init__(self, status: int, url: str) -> None:
        self.status = status
        self.url = url
        super().__init__(f"HTTP {status} for {url}")


@dataclass(slots=True)
class Classified:
    error_class: ErrorClass
    http_status: int | None
    message: str

    @property
    def retryable(self) -> bool:
        return self.error_class in RETRYABLE


def classify_status(status: int) -> ErrorClass:
    if status == 403:
        return ErrorClass.HTTP_403
    if status == 404 or status == 410:
        return ErrorClass.HTTP_404
    if status == 429:
        return ErrorClass.HTTP_429
    if 500 <= status < 600:
        return ErrorClass.HTTP_5XX
    return ErrorClass.HTTP_OTHER


def classify_exception(exc: BaseException) -> Classified:
    """Map any failure raised while fetching or storing onto an error class."""
    message = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, (ChallengeDetected, RobotsDisallowed, ContentSignalOptOut,
                        HostTripped, UrlRejected)):
        status = getattr(exc, "status", None)
        return Classified(ErrorClass.POLICY_BLOCK, status, message)
    if isinstance(exc, (RetryableStatus, HttpStatusFailure)):
        return Classified(classify_status(exc.status), exc.status, message)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return Classified(classify_status(status), status, message)
    if isinstance(exc, httpx.TimeoutException):
        return Classified(ErrorClass.TIMEOUT, None, message)
    if isinstance(exc, httpx.TransportError):
        return Classified(ErrorClass.NETWORK, None, message)
    if isinstance(exc, DiskFull):
        return Classified(ErrorClass.DISK_FULL, None, message)
    if isinstance(exc, EmptyBody):
        return Classified(ErrorClass.EMPTY_BODY, None, message)
    if isinstance(exc, SchemaDrift):
        return Classified(ErrorClass.SCHEMA_DRIFT, None, message)
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:  # ENOSPC
        return Classified(ErrorClass.DISK_FULL, None, message)
    return Classified(ErrorClass.HTTP_OTHER, None, message)


def check_disk(path: Path, floor: int = MIN_FREE_BYTES) -> int:
    """Raise DiskFull when free space under path is below the floor."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < floor:
        raise DiskFull(f"{free // (1024 * 1024)} MB free under {probe}; need {floor // (1024 * 1024)} MB")
    return free


# -- secrets -------------------------------------------------------------

_SECRET_ENV_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|COOKIE|CREDENTIAL)", re.I)
_TOKEN_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*", re.I),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def secret_values_from_env(environ: dict[str, str] | None = None) -> list[str]:
    env = os.environ if environ is None else environ
    return [v for k, v in env.items() if _SECRET_ENV_RE.search(k) and v and len(v) >= 8]


def scan_for_secrets(dirs: list[Path], environ: dict[str, str] | None = None) -> list[str]:
    """Files under dirs that contain a secret env value or a token-shaped string.

    Returns 'path: reason' lines and never the secret itself.
    """
    values = secret_values_from_env(environ)
    findings: list[str] = []
    for root in dirs:
        if not root.exists():
            continue
        for file in sorted(p for p in root.rglob("*") if p.is_file()):
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(value in text for value in values):
                findings.append(f"{file}: contains the value of a secret environment variable")
            for pattern in _TOKEN_PATTERNS:
                if pattern.search(text):
                    findings.append(f"{file}: token-shaped string ({pattern.pattern[:24]}...)")
                    break
    return findings


# -- diagnosis -----------------------------------------------------------

def diagnose(catalogue: Catalogue, paths: CcmaPaths) -> str:
    """A plain-text troubleshooting report over the whole catalogue."""
    lines: list[str] = ["CCMA engine troubleshooting report", ""]

    errors = catalogue.errors(unresolved_only=True)
    by_class = Counter(row["class"] for row in errors)
    lines.append(f"Unresolved errors: {len(errors)}")
    for name, count in by_class.most_common():
        cls = ErrorClass(name)
        flag = "retryable" if cls in RETRYABLE else "not retried"
        lines.append(f"  {name:<22} {count:>5}  ({flag})")
        lines.append(f"      -> {REMEDIES[cls]}")
    by_source = Counter((row["source"], row["class"]) for row in errors)
    if by_source:
        lines.append("")
        lines.append("By source:")
        for (source, name), count in sorted(by_source.items()):
            lines.append(f"  {source:<28} {name:<22} {count}")

    status_rows = catalogue.conn.execute(
        "SELECT parse_status, COUNT(*) AS n FROM records GROUP BY parse_status ORDER BY n DESC"
    ).fetchall()
    lines.append("")
    lines.append("Records by parse status:")
    for row in status_rows:
        lines.append(f"  {row['parse_status']:<22} {row['n']}")
    if not status_rows:
        lines.append("  (no records)")

    quarantined = sorted(paths.raw.glob("*/*/quarantine/*")) if paths.raw.exists() else []
    lines.append("")
    lines.append(f"Quarantined files: {len([q for q in quarantined if not q.name.endswith('.json')])}")
    for q in quarantined[:20]:
        lines.append(f"  {q.relative_to(paths.root)}")

    try:
        free = check_disk(paths.data, floor=0)
        state = "OK" if free >= MIN_FREE_BYTES else "BELOW 500 MB FLOOR"
        lines.append("")
        lines.append(f"Free disk: {free // (1024 * 1024)} MB ({state})")
    except OSError as exc:
        lines.append(f"Free disk: unknown ({exc})")

    blocked = catalogue.conn.execute(
        "SELECT id, status FROM sources WHERE status NOT IN ('verified_open', 'local') ORDER BY id"
    ).fetchall()
    if blocked:
        lines.append("")
        lines.append("Sources not collectable (policy, not a fault):")
        for row in blocked:
            lines.append(f"  {row['id']:<28} {row['status']}")

    secrets = scan_for_secrets([paths.reports, paths.logs])
    lines.append("")
    lines.append(f"Secret scan of reports/ and logs/: {'CLEAN' if not secrets else 'FINDINGS'}")
    lines.extend(f"  {s}" for s in secrets)
    return "\n".join(lines) + "\n"
