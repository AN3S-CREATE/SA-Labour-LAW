"""The operator's source allow-list and the URL guard that enforces it.

A source is live-harvestable only when all of these hold:

* its observed ``status`` is ``verified_open`` (measured, never assumed);
* the operator has confirmed it: ``operator_confirmed: true``, or
  ``pending_robots_check``, which confirms it on condition that robots.txt
  permits every allow-listed path when the run starts;
* the run passes ``--allow-network`` and ``--popia-ack``.

Official publications (``kind: official_publication``) also need their
robots/ToS notes on file in ``docs/ccma-source-map.md`` before anything is
fetched, and are confined to an allow-list of paths as well as hosts.

Everything else is research-only. Blocked sources stay listed, so the coverage
report can say what is missing (the same rule as D-003 for the main corpus).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path
from collections.abc import Iterable
from urllib.parse import unquote, urlparse

import yaml

from ..config import ConfigError, assert_not_paid_host


class SourceKind(str, Enum):
    SAFLII_YEAR_INDEX = "saflii_year_index"   # database index -> year pages -> records
    LINK_INDEX = "link_index"                 # one page listing documents
    # Layer 1: allow-listed URLs and listings of an official publisher, confined
    # to allow-listed paths. Never awards.
    OFFICIAL_PUBLICATION = "official_publication"
    LOCAL_CORPUS = "local_corpus"             # the main corpus already on disk
    LOCAL_DIRECTORY = "local_directory"       # files the operator already holds


class SourceStatus(str, Enum):
    VERIFIED_OPEN = "verified_open"
    BLOCKED_BOT_MANAGEMENT = "blocked_bot_management"
    CONTENT_SIGNAL_OPT_OUT = "content_signal_opt_out"
    DROPPED_UNOFFICIAL = "dropped_unofficial"
    DROPPED_NOT_A_PUBLICATION = "dropped_not_a_publication"
    UNVERIFIED = "unverified"
    LOCAL = "local"


class RecordKind(str, Enum):
    AWARD = "award"
    RELATED_AUTHORITY = "related_authority"   # LC/LAC judgment; never an award
    PRACTICE_MATERIAL = "practice_material"   # rules, info sheets, forms
    PRACTICE_NOTE = "practice_note"           # official guidance: info sheets, Rules, forms
    STATUTE = "statute"                       # gazetted instrument: Act, code, regulation

    @property
    def is_decision(self) -> bool:
        """Awards and judgments decide a dispute. Nothing else does.

        A record that decides nothing gets no case number, outcome or case
        card, even when its text cites a case or describes a fair dismissal:
        a mention is not an identity.
        """
        return self in _DECISION_KINDS


_DECISION_KINDS = frozenset({RecordKind.AWARD, RecordKind.RELATED_AUTHORITY})

# Official publications are seeded as one of these and never as an award.
OFFICIAL_RECORD_KINDS = frozenset({RecordKind.PRACTICE_NOTE, RecordKind.STATUTE})


class HarvestMode(str, Enum):
    # Plain GET of allow-listed URLs through the policy-enforcing client. There
    # is deliberately no browser mode: Playwright may be considered only for a
    # listing shown to be JS-rendered on a path robots.txt allows.
    ALLOWLISTED_HTTP = "allowlisted_http"


class Confirmation(str, Enum):
    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"
    # Confirmed on condition that robots.txt, read at the start of each run with
    # the production client, permits every allow-listed path.
    PENDING_ROBOTS_CHECK = "pending_robots_check"


LIVE_KINDS = frozenset({SourceKind.SAFLII_YEAR_INDEX, SourceKind.LINK_INDEX,
                        SourceKind.OFFICIAL_PUBLICATION})
_KIND_SELECTORS = frozenset(k.value for k in SourceKind)


class UrlRejected(ValueError):
    """A URL outside the allow-listed host or path set, or otherwise unsafe to fetch."""


@dataclass(slots=True)
class CcmaSource:
    id: str
    name: str
    kind: SourceKind
    status: SourceStatus
    record_kind: RecordKind
    enabled: bool = False
    confirmation: Confirmation = Confirmation.UNCONFIRMED
    base_url: str | None = None
    hosts: tuple[str, ...] = ()
    path: str | None = None
    robots_ok: bool | None = None
    tos_notes: str = ""
    verified_on: str | None = None
    link_patterns: tuple[str, ...] = ()
    years: tuple[int, int] | None = None
    notes: str = ""
    # Politeness per request to this source's hosts, in seconds.
    min_delay: float = 2.0
    max_delay: float = 4.0
    # Official publications only.
    harvest_mode: HarvestMode = HarvestMode.ALLOWLISTED_HTTP
    # Path allow-list. An entry ending in "/" admits that directory and
    # everything under it; any other entry admits exactly that path. Empty
    # means hosts alone decide, as for the award sources.
    allowed_paths: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()            # exact record URLs, fetched without a listing
    listing_urls: tuple[str, ...] = ()    # pages whose linked documents are records
    detail_paths: tuple[str, ...] = ()    # listed pages followed one level for documents
    title: str | None = None              # operator label for `urls` records; never a citation

    @property
    def is_live(self) -> bool:
        return self.kind in LIVE_KINDS

    @property
    def operator_confirmed(self) -> bool:
        return self.confirmation is not Confirmation.UNCONFIRMED

    @operator_confirmed.setter
    def operator_confirmed(self, value: bool) -> None:
        self.confirmation = Confirmation.CONFIRMED if value else Confirmation.UNCONFIRMED

    @property
    def needs_robots_preflight(self) -> bool:
        """Whether robots.txt must permit every allow-listed path before any fetch.

        Always true for official publications (operator rule 2), and for any
        source whose confirmation is conditional on it.
        """
        return (self.kind is SourceKind.OFFICIAL_PUBLICATION
                or self.confirmation is Confirmation.PENDING_ROBOTS_CHECK)

    def robots_probe_urls(self) -> list[str]:
        """One URL per allow-listed path, exact URLs and listings first."""
        probes: list[str] = []
        for url in (*self.urls, *self.listing_urls):
            if url not in probes:
                probes.append(url)
        covered = {urlparse(u).path for u in probes}
        for prefix in self.allowed_paths:
            if not prefix.endswith("/"):
                continue          # exact paths are already probed through `urls`
            if any(path.startswith(prefix) for path in covered):
                continue
            for host in self.hosts:
                probes.append(f"https://{host}{prefix}")
        if not probes and self.base_url:
            probes.append(self.base_url)
        return probes

    def live_refusal(self, *, allow_network: bool, popia_ack: bool, dry_run: bool) -> str | None:
        """Why this source may not be fetched live now, or None if it may.

        A dry run only walks listings, so it needs network permission and an
        open source but not the POPIA acknowledgement: it saves nothing.
        """
        if not self.enabled:
            return "source disabled in the allow-list"
        if self.status is not SourceStatus.VERIFIED_OPEN:
            return f"source status is {self.status.value}; only verified_open sources are fetched"
        if not allow_network:
            return "network access not granted for this run (--allow-network)"
        if dry_run:
            return None
        if not self.operator_confirmed:
            return "operator has not confirmed this source (operator_confirmed: false)"
        if not popia_ack:
            return "POPIA lawful basis and minimisation not acknowledged (--popia-ack)"
        return None


@dataclass(slots=True)
class AllowList:
    sources: list[CcmaSource] = field(default_factory=list)
    path: Path | None = None

    def get(self, source_id: str) -> CcmaSource:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise ConfigError(f"unknown CCMA source {source_id!r}")

    def select(self, ids: list[str] | None) -> list[CcmaSource]:
        """Sources by id, or by type: ``official_publication`` selects every one of that kind.

        An exact source id wins over a type of the same name (``local_corpus``
        is both).
        """
        if not ids:
            return list(self.sources)
        chosen: list[CcmaSource] = []
        for key in ids:
            matches = [s for s in self.sources if s.id == key]
            if not matches and key in _KIND_SELECTORS:
                matches = [s for s in self.sources if s.kind.value == key]
            if not matches:
                matches = [self.get(key)]          # raises: unknown id
            chosen.extend(s for s in matches if all(c.id != s.id for c in chosen))
        return chosen

    def hosts_for(self, source: CcmaSource) -> frozenset[str]:
        return frozenset(h.lower() for h in source.hosts)

    @property
    def source_map_path(self) -> Path | None:
        """The human-readable source map kept beside the allow-list."""
        return self.path.parent / "docs" / "ccma-source-map.md" if self.path else None

    def notes_refusal(self, source: CcmaSource) -> str | None:
        """Operator rule 1: robots/ToS notes must be on file before any fetch.

        The allow-list entry needs its ``tos_notes``, and the source map needs a
        section naming the source id in backticks.
        """
        if not source.tos_notes:
            return "no robots/ToS note in the allow-list; write one before any fetch"
        doc = self.source_map_path
        if doc is None or not doc.exists():
            return f"source map {doc} not found; write robots/ToS notes before any fetch"
        if f"`{source.id}`" not in doc.read_text(encoding="utf-8"):
            return f"{doc.name} has no robots/ToS notes for `{source.id}`; write them before any fetch"
        return None


def default_allowlist_path() -> Path:
    return Path(__file__).resolve().parents[3] / "ccma_sources.yaml"


def _confirmation(value: object) -> Confirmation:
    """YAML ``true``/``false``, or a Confirmation value such as ``pending_robots_check``."""
    if value is None or isinstance(value, bool):
        return Confirmation.CONFIRMED if value else Confirmation.UNCONFIRMED
    return Confirmation(str(value).strip())


def _strings(raw: dict, key: str) -> tuple[str, ...]:
    value = raw.get(key) or []
    if isinstance(value, str):
        value = [value]
    return tuple(str(v).strip() for v in value)


def _check_official(source: CcmaSource, path: Path) -> None:
    """The extra rules an official-publication entry must satisfy."""
    where = f"{path}: {source.id!r}"
    if source.record_kind not in OFFICIAL_RECORD_KINDS:
        raise ConfigError(f"{where} is an official publication; record_kind must be "
                          "practice_note or statute, never an award")
    if not source.allowed_paths:
        raise ConfigError(f"{where} needs allowed_paths: hosts alone are too broad")
    for entry in source.allowed_paths:
        if not entry.startswith("/"):
            raise ConfigError(f"{where}: allowed path {entry!r} must start with '/'")
    if not (source.urls or source.listing_urls):
        raise ConfigError(f"{where} needs urls or listing_urls")
    hosts = frozenset(source.hosts)
    for url in (*source.urls, *source.listing_urls):
        try:
            validate_url(url, hosts, source.allowed_paths)
        except UrlRejected as exc:
            raise ConfigError(f"{where}: {exc}") from exc
    for detail in source.detail_paths:
        if not path_allowed(detail, source.allowed_paths):
            raise ConfigError(f"{where}: detail path {detail!r} is outside allowed_paths")
    if not source.tos_notes:
        raise ConfigError(f"{where} carries no robots/ToS note (rule: notes before any fetch)")


SUPPORTED_VERSION = 1

# What an allow-list entry may say: the CcmaSource fields, with the YAML's
# ``operator_confirmed`` in place of the ``confirmation`` derived from it. Any
# other key is a typo that would be ignored and fall back to a default.
_ENTRY_KEYS = ((frozenset(f.name for f in fields(CcmaSource)) - {"confirmation"})
               | {"operator_confirmed"})


def load_allowlist(path: Path | None = None) -> AllowList:
    """Parse and validate the allow-list.

    Strict about what it does not understand: an unknown key, a non-boolean
    ``enabled``, a ``min_delay`` above ``max_delay`` or an unsupported
    ``version`` is a ConfigError, never a silent default.
    """
    path = Path(path) if path else default_allowlist_path()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    version = data.get("version")
    if version is not None and version != SUPPORTED_VERSION:
        raise ConfigError(f"{path}: version {version!r} is not supported "
                          f"(expected {SUPPORTED_VERSION})")
    sources: list[CcmaSource] = []
    seen: set[str] = set()
    for raw in data.get("sources", []):
        if not isinstance(raw, dict):
            raise ConfigError(f"{path}: a CCMA source entry is not a mapping: {raw!r}")
        try:
            source = CcmaSource(
                id=str(raw["id"]),
                name=str(raw.get("name", raw["id"])),
                kind=SourceKind(raw["kind"]),
                status=SourceStatus(raw["status"]),
                record_kind=RecordKind(raw["record_kind"]),
                enabled=bool(raw.get("enabled", False)),
                base_url=raw.get("base_url"),
                hosts=tuple(str(h).lower() for h in raw.get("hosts", [])),
                path=raw.get("path"),
                robots_ok=raw.get("robots_ok"),
                tos_notes=str(raw.get("tos_notes", "")).strip(),
                verified_on=str(raw["verified_on"]) if raw.get("verified_on") else None,
                link_patterns=tuple(raw.get("link_patterns", [])),
                years=tuple(raw["years"]) if raw.get("years") else None,
                notes=str(raw.get("notes", "")).strip(),
                min_delay=float(raw.get("min_delay", 2.0)),
                max_delay=float(raw.get("max_delay", 4.0)),
                confirmation=_confirmation(raw.get("operator_confirmed", False)),
                harvest_mode=HarvestMode(raw.get("harvest_mode", HarvestMode.ALLOWLISTED_HTTP.value)),
                allowed_paths=_strings(raw, "allowed_paths"),
                urls=_strings(raw, "urls"),
                listing_urls=_strings(raw, "listing_urls"),
                detail_paths=_strings(raw, "detail_paths"),
                title=str(raw["title"]).strip() if raw.get("title") else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"{path}: bad CCMA source entry {raw!r}: {exc}") from exc
        if source.id in seen:
            raise ConfigError(f"{path}: duplicate CCMA source id {source.id!r}")
        seen.add(source.id)
        for host in source.hosts:
            assert_not_paid_host(host)
        unknown = set(raw) - _ENTRY_KEYS
        if unknown:
            raise ConfigError(f"{path}: CCMA source {source.id!r} has unknown key(s): "
                              f"{', '.join(sorted(map(str, unknown)))}")
        if "enabled" in raw and not isinstance(raw["enabled"], bool):
            raise ConfigError(f"{path}: CCMA source {source.id!r} must set enabled: true or false")
        if source.min_delay > source.max_delay:
            raise ConfigError(f"{path}: CCMA source {source.id!r} has min_delay {source.min_delay} "
                              f"above max_delay {source.max_delay}")
        if source.is_live:
            if not source.base_url or not source.hosts:
                raise ConfigError(f"{path}: live source {source.id!r} needs base_url and hosts")
            validate_url(source.base_url, frozenset(source.hosts), source.allowed_paths)
            if source.status is SourceStatus.VERIFIED_OPEN and not source.tos_notes:
                raise ConfigError(
                    f"{path}: {source.id!r} is verified_open but carries no robots/ToS note"
                )
        if source.kind is SourceKind.OFFICIAL_PUBLICATION:
            _check_official(source, path)
        sources.append(source)
    return AllowList(sources=sources, path=path)


# Percent-encoded "/", "\" and ".": the usual ways out of a path prefix.
_ENCODED_ESCAPE_RE = re.compile(r"%(?:2f|5c|2e)", re.IGNORECASE)


def path_allowed(path: str, allowed_paths: Iterable[str]) -> bool:
    """Whether a URL path lies inside the allow-list.

    An entry ending in "/" admits that directory and everything below it; any
    other entry admits exactly that path. Dot segments, backslashes and encoded
    separators are refused outright, because they are how a prefix check is
    walked out of (``/advices/../wp-login.php`` starts with ``/advices/``).
    """
    path = path or "/"
    decoded = unquote(path)
    if "\\" in decoded or _ENCODED_ESCAPE_RE.search(path):
        return False
    if any(segment in {".", ".."} for segment in decoded.split("/")):
        return False
    for entry in allowed_paths:
        if entry.endswith("/") and path.startswith(entry):
            return True
        if path == entry:
            return True
    return False


def validate_url(url: str, allowed_hosts: frozenset[str],
                 allowed_paths: tuple[str, ...] = ()) -> str:
    """Return the URL if it may be fetched, else raise UrlRejected.

    Rejects any scheme but https (plain ``http:`` and ``file:`` included),
    embedded credentials, IP literals, any host not on the allow-list, any
    port but 443 and, when the source has one, any path outside its path
    allow-list. Called before every request and again on the final URL after
    redirects, which is what stops an open redirect from walking the crawler
    off the allow-list or downgrading it to plain HTTP.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UrlRejected(f"scheme {parsed.scheme or '(none)'!r} is not fetchable "
                          f"(https only): {url}")
    if parsed.username or parsed.password:
        raise UrlRejected(f"credentials embedded in URL are refused: {parsed.hostname}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UrlRejected(f"URL has no host: {url}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise UrlRejected(f"IP-literal hosts are refused: {host}")
    if host not in allowed_hosts:
        raise UrlRejected(f"host {host!r} is not on the allow-list for this source")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UrlRejected(f"bad port in {url}") from exc
    if port not in (None, 443):
        raise UrlRejected(f"port {port} is refused; only the https default 443 is fetched: {url}")
    if allowed_paths and not path_allowed(parsed.path, allowed_paths):
        raise UrlRejected(f"path {parsed.path!r} is not on the path allow-list for this source")
    return url
