"""Loads sources.yaml into validated source definitions.

Loading is where the paid-publisher rule is enforced: a registry containing a
paid legal database raises rather than warns, so a mistake cannot quietly turn
into a crawl of a licensed product.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path

import yaml

from ..config import ConfigError, assert_not_paid_host
from ..models import DocType


class Access(str, Enum):
    """What the live web actually does when an identified crawler calls."""

    OPEN = "open"
    BOT_MANAGEMENT_BLOCKED = "bot_management_blocked"
    CONTENT_SIGNAL_OPT_OUT = "content_signal_opt_out"


class SourceKind(str, Enum):
    YEAR_INDEX = "year_index"
    LINK_INDEX = "link_index"
    INBOX = "inbox"


@dataclass(slots=True)
class SourceDef:
    id: str
    name: str
    host: str
    base_url: str
    forum: str
    doc_type: DocType
    kind: SourceKind
    access: Access
    enabled: bool = True
    filter_required: bool = False
    link_patterns: list[str] = field(default_factory=list)
    # Bounded pagination for listing pages. 0 disables it. A listing is walked
    # as ?<page_param>=N for N in 0..max_pages-1, which is index walking, not
    # enumeration: the pages are published and finite.
    max_pages: int = 0
    page_param: str = "page"
    # Only follow listing entries whose link text matches this regex. Keeps a
    # national Acts listing from pulling in every unrelated statute.
    title_filter: str = ""
    notes: str = ""
    license_note: str = ""
    min_delay: float = 1.0
    max_delay: float = 2.0

    @property
    def is_collectable(self) -> bool:
        """Whether an honest crawler is permitted and able to collect here."""
        return self.enabled and self.access is Access.OPEN


@dataclass(slots=True)
class Registry:
    sources: list[SourceDef]
    path: Path

    def by_id(self, source_id: str) -> SourceDef | None:
        for source in self.sources:
            if source.id == source_id:
                return source
        return None

    def select(self, ids: list[str] | None) -> list[SourceDef]:
        """Select sources by id, or every enabled source when ids is empty."""
        if not ids or ids == ["all_public"]:
            return [s for s in self.sources if s.enabled]
        chosen: list[SourceDef] = []
        unknown: list[str] = []
        for source_id in ids:
            found = self.by_id(source_id)
            if found is None:
                unknown.append(source_id)
            else:
                chosen.append(found)
        if unknown:
            raise ConfigError(
                f"unknown source id(s): {', '.join(unknown)}. "
                f"Known ids: {', '.join(s.id for s in self.sources)}"
            )
        return chosen

    @property
    def hosts(self) -> list[str]:
        return sorted({s.host for s in self.sources})


SUPPORTED_VERSION = 1

# What an entry may say. A key outside this set is a typo (``enable: false``,
# ``min-delay: 8``) that would otherwise be ignored and fall back to a default,
# turning a mistake into a policy change on the next crawl.
_ENTRY_KEYS = frozenset(f.name for f in fields(SourceDef))
_DEFAULT_KEYS = frozenset({"jurisdiction", "min_delay", "max_delay"})


def load_registry(path: Path) -> Registry:
    """Parse and validate sources.yaml.

    Strict about what it does not understand: an unknown key, a missing or
    non-boolean ``enabled`` (the default would be True), a ``min_delay`` above
    ``max_delay`` or an unsupported ``version`` is a ConfigError, never a
    silent default.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"source registry not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    version = data.get("version")
    if version is not None and version != SUPPORTED_VERSION:
        raise ConfigError(f"{path}: version {version!r} is not supported "
                          f"(expected {SUPPORTED_VERSION})")
    defaults = data.get("defaults") or {}
    unknown_defaults = set(defaults) - _DEFAULT_KEYS
    if unknown_defaults:
        raise ConfigError(f"{path}: unknown key(s) under defaults: "
                          f"{', '.join(sorted(map(str, unknown_defaults)))}")
    raw_sources = data.get("sources") or []
    if not raw_sources:
        raise ConfigError(f"{path} contains no sources")

    sources: list[SourceDef] = []
    seen: set[str] = set()

    for entry in raw_sources:
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: a source entry is not a mapping: {entry!r}")
        source_id = entry.get("id")
        if not source_id:
            raise ConfigError(f"{path}: a source entry has no id")
        if source_id in seen:
            raise ConfigError(f"{path}: duplicate source id {source_id!r}")
        seen.add(source_id)

        host = entry.get("host") or ""
        if not host:
            raise ConfigError(f"{path}: source {source_id!r} has no host")
        # Hard error, by design: a paid publisher must never be a crawl target.
        # Checked before the entry's other fields, so a paid host is always
        # reported as one.
        assert_not_paid_host(host)

        unknown = set(entry) - _ENTRY_KEYS
        if unknown:
            raise ConfigError(f"{path}: source {source_id!r} has unknown key(s): "
                              f"{', '.join(sorted(map(str, unknown)))}")
        # Required, because the dataclass default is the unsafe one (enabled).
        if not isinstance(entry.get("enabled"), bool):
            raise ConfigError(f"{path}: source {source_id!r} must set enabled: true or false")

        try:
            doc_type = DocType(entry.get("doc_type", "other"))
            kind = SourceKind(entry.get("kind", "link_index"))
            access = Access(entry.get("access", "open"))
            min_delay = float(entry.get("min_delay", defaults.get("min_delay", 1.0)))
            max_delay = float(entry.get("max_delay", defaults.get("max_delay", 2.0)))
        except (TypeError, ValueError) as exc:   # TypeError: an empty `min_delay:` is None
            raise ConfigError(f"{path}: source {source_id!r}: {exc}") from exc
        if min_delay > max_delay:
            raise ConfigError(f"{path}: source {source_id!r} has min_delay {min_delay} "
                              f"above max_delay {max_delay}")

        sources.append(
            SourceDef(
                id=source_id,
                name=str(entry.get("name") or source_id),
                host=host,
                base_url=entry.get("base_url", ""),
                forum=entry.get("forum", ""),
                doc_type=doc_type,
                kind=kind,
                access=access,
                enabled=entry["enabled"],
                filter_required=bool(entry.get("filter_required", False)),
                link_patterns=list(entry.get("link_patterns") or []),
                max_pages=int(entry.get("max_pages", 0)),
                page_param=str(entry.get("page_param", "page")),
                title_filter=(entry.get("title_filter") or "").strip(),
                notes=(entry.get("notes") or "").strip(),
                license_note=(entry.get("license_note") or "").strip(),
                min_delay=min_delay,
                max_delay=max_delay,
            )
        )

    return Registry(sources=sources, path=path)


def default_registry_path() -> Path:
    """sources.yaml at the repository root, next to pyproject.toml."""
    return Path(__file__).resolve().parents[3] / "sources.yaml"
