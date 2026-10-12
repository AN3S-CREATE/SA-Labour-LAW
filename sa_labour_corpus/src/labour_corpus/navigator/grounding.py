"""Check that every quoted provision appears in a statute the corpus holds.

This is the navigator's version of the corpus's never-invent rule. A provision
is ``verified`` only when its exact wording, after whitespace and typography
normalisation, is found in a held document that is not a draft and whose text
is readable. Anything else is reported, never papered over: ``not_found`` means
"do not rely on this wording", ``not_checked`` means "no corpus, or no held
copy of the Act, was available".

The corpus database is opened read-only. The navigator never writes to it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypedDict

from ..config import CorpusPaths
from ..models import DocType
from ..parse.statute import self_identified_act
from ..sqlite_ro import connect_readonly
from .models import Grounding, ProvisionRef
from .provisions import ACT_TITLES, Provision

log = logging.getLogger(__name__)

_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (
    ("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
    ("—", "-"), ("–", "-"), ("*", " "), ("\u00a0", " "),
)


def normalise(text: str) -> str:
    """Fold case, typography and line wrapping so a quote can be found verbatim."""
    # Chained replace() is several times faster than translate() with a map
    # holding non-Latin-1 keys, and split() breaks on exactly the code points
    # that re's \s matches, dropping leading and trailing whitespace too.
    for old, new in _QUOTE_PAIRS:
        text = text.replace(old, new)
    return " ".join(text.split()).lower()


@dataclass(frozen=True, slots=True)
class HeldText:
    doc_id: str
    url: str
    text: str           # already normalised


class StatuteSource(Protocol):
    def texts_for(self, act: str) -> list[HeldText]: ...


@dataclass
class InMemorySource:
    """A statute source built from given texts. Used by tests and fixtures."""

    by_act: dict[str, list[HeldText]] = field(default_factory=dict)

    def add(self, act: str, doc_id: str, text: str, url: str = "") -> None:
        self.by_act.setdefault(act, []).append(HeldText(doc_id, url, normalise(text)))

    def texts_for(self, act: str) -> list[HeldText]:
        return self.by_act.get(act, [])


# A held text file's identity and contents: doc_id -> (st_mtime_ns, st_size,
# normalised text, or None when the file does not identify itself as an Act).
TextCache = dict[str, tuple[int, int, str | None]]


class CorpusSource:
    """Held, readable, non-draft copies of each Act, read from the corpus.

    A copy is a document whose title names the Act *and* whose opening
    identifies it as a principal Act (D-024). The title alone also picks up
    fund rules, codes, summaries and forms that carry the Act's name, and
    counting those as copies made every quote look as if held consolidations
    disagreed (D-023). The title filter stays as the first stage, so an Act
    whose ``doc_type`` was mis-recorded is still found.

    Copies are tried largest first, because the consolidated text is the
    largest document carrying an Act's title; amendment Acts are smaller.

    ``text_cache`` lets a long-lived caller (the MCP server) share file
    contents between sources. The database is still queried on every
    ``texts_for`` call, so the set of held copies is always current; only a
    file whose modification time and size are unchanged is not read again.
    """

    def __init__(self, paths: CorpusPaths, text_cache: TextCache | None = None) -> None:
        self.paths = paths
        self._cache: dict[str, list[HeldText]] = {}
        self._texts: TextCache = {} if text_cache is None else text_cache

    def texts_for(self, act: str) -> list[HeldText]:
        if act in self._cache:
            return self._cache[act]
        fragments = ACT_TITLES.get(act, ())
        held: list[HeldText] = []
        excluded: list[str] = []
        if fragments and self.paths.corpus_db.exists():
            conn = connect_readonly(self.paths.corpus_db)
            try:
                where = " OR ".join("LOWER(title) LIKE ?" for _ in fragments)
                rows = conn.execute(
                    f"""SELECT doc_id, canonical_url FROM documents
                        WHERE ({where}) AND is_draft = 0 AND text_status = 'ok'
                        ORDER BY text_chars DESC""",
                    [f"%{f}%" for f in fragments],
                ).fetchall()
            finally:
                conn.close()
            for doc_id, url in rows:
                entry = self._read(doc_id)
                if entry is None:
                    continue
                text = entry[2]
                if text is None:
                    excluded.append(doc_id)
                    continue
                held.append(HeldText(doc_id, url or "", text))
        if excluded:
            log.info("statute_copies_excluded", extra={"act": act, "doc_ids": excluded})
        log.info("statute_texts_loaded", extra={"act": act, "documents": len(held)})
        self._cache[act] = held
        return held

    def _read(self, doc_id: str) -> tuple[int, int, str | None] | None:
        """The file's cache entry, re-read when its mtime or size changed; None if absent."""
        path = self.paths.text / f"{doc_id}.txt"
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        entry = self._texts.get(doc_id)
        if entry is None or entry[:2] != (stat.st_mtime_ns, stat.st_size):
            raw = path.read_text(encoding="utf-8", errors="ignore")
            is_act = self_identified_act(raw) is DocType.STATUTE
            entry = (stat.st_mtime_ns, stat.st_size, normalise(raw) if is_act else None)
            self._texts[doc_id] = entry
        return entry


class _Quoted(TypedDict):
    key: str
    cite: str
    quote: str


class _Copies(TypedDict):
    copies_checked: int
    copies_matching: int


def ground(provision: Provision, source: StatuteSource | None) -> ProvisionRef:
    """Return a reference to ``provision`` stating whether its wording is held."""
    base: _Quoted = {"key": provision.key, "cite": provision.cite, "quote": provision.quote}
    if source is None:
        return ProvisionRef(**base, grounding=Grounding.NOT_CHECKED)
    texts = source.texts_for(provision.act)
    if not texts:
        log.warning("grounding_no_statute", extra={"provision": provision.key,
                                                   "act": provision.act})
        return ProvisionRef(**base, grounding=Grounding.NOT_CHECKED)
    needle = normalise(provision.quote)
    matches = [held for held in texts if needle in held.text]
    counts: _Copies = {"copies_checked": len(texts), "copies_matching": len(matches)}
    if matches:
        if len(matches) < len(texts):
            log.info("grounding_partial", extra={"provision": provision.key, **counts})
        first = matches[0]
        return ProvisionRef(**base, grounding=Grounding.VERIFIED, doc_id=first.doc_id,
                            source_url=first.url or None, **counts)
    log.warning("grounding_not_found", extra={"provision": provision.key, **counts})
    return ProvisionRef(**base, grounding=Grounding.NOT_FOUND, **counts)


def corpus_source(root: Path, text_cache: TextCache | None = None) -> CorpusSource | None:
    """A corpus-backed source, or ``None`` when no corpus database exists."""
    paths = CorpusPaths(Path(root))
    return CorpusSource(paths, text_cache) if paths.corpus_db.exists() else None
