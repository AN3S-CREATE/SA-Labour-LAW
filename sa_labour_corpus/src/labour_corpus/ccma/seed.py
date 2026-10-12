"""Seed: upsert catalogue rows and derive knowledge units, idempotently.

Idempotent on ``(source, native_id)``: the same bytes with the same parse
result are a skip, and knowledge unit ids are derived from their content.
A record that fails to parse is stored with its parse status and gets no
knowledge units -- nothing is fabricated to fill the gap.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .allowlist import RecordKind
from .catalogue import Catalogue, KnowledgeUnit, RecordRow, record_key
from .download import find_original, read_meta
from .extract import Parsed, parse_artifact
from .knowledge import build_record_units, build_scenarios, cards_from_rows
from .minimise import MinimisationError, minimise
from .paths import CcmaPaths


@dataclass(slots=True)
class SeedResult:
    status: str                      # inserted | updated | skipped
    parse_status: str
    units: list[KnowledgeUnit]
    minimisation_error: str | None = None
    parsed: Parsed | None = None


def forum_for(parsed: Parsed, record_kind: RecordKind, default: str | None) -> str | None:
    if parsed.meta.court_code:
        return parsed.meta.court_code
    if record_kind is RecordKind.AWARD and parsed.meta.case_no and "-" in parsed.meta.case_no:
        return "CCMA"
    return default


def seed_one(
    catalogue: Catalogue, *, source: str, native_id: str, record_kind: RecordKind,
    body: bytes, content_type: str | None, url: str | None, sha256: str,
    retrieved_at: str | None, local_path: str | None, provenance: str,
    stored_text: str | None = None, default_forum: str | None = None,
    title_hint: str | None = None,
) -> SeedResult:
    parsed = parse_artifact(body, content_type, url or "", record_kind, stored_text=stored_text,
                            title_hint=title_hint)
    row = RecordRow(
        source=source,
        native_id=native_id,
        record_kind=record_kind.value,
        provenance=provenance,
        parse_status=parsed.parse_status,
        case_number=parsed.meta.case_no,
        neutral_citation=parsed.meta.neutral_citation,
        title=parsed.title or title_hint,
        forum=forum_for(parsed, record_kind, default_forum),
        year=parsed.meta.year,
        date_delivered=parsed.meta.date_delivered,
        dispute_type=parsed.dispute_type,
        outcome_label=parsed.outcome_label,
        source_url=url,
        local_path=local_path,
        sha256=sha256,
        content_type=content_type,
        retrieved_at=retrieved_at,
        parse_notes=parsed.notes,
        tags=parsed.tagging.dismissal_tags,
    )
    # The index gets the knowledge-layer minimisation too: identifiers and
    # addresses stay only in the raw files.
    fts_body = minimise(parsed.redacted_text) if parsed.parse_status == "parsed" else None
    status = catalogue.upsert_record(row, fts_body)
    if status == "skipped":
        return SeedResult(status, parsed.parse_status, [], parsed=parsed)

    fk = record_key(source, native_id)
    try:
        units = build_record_units(source, native_id, record_kind, row.forum, url, parsed)
    except MinimisationError as exc:
        catalogue.replace_knowledge_for(fk, [])
        return SeedResult(status, parsed.parse_status, [], str(exc), parsed)
    catalogue.replace_knowledge_for(fk, units)
    return SeedResult(status, parsed.parse_status, units, parsed=parsed)


def iter_raw_records(paths: CcmaPaths, source: str | None = None):
    """(source, native_id, original_path, meta) for every stored artefact."""
    if not paths.raw.exists():
        return
    for source_dir in sorted(p for p in paths.raw.iterdir() if p.is_dir()):
        if source and source_dir.name != source:
            continue
        for record_dir in sorted(p for p in source_dir.iterdir() if p.is_dir()):
            original = find_original(record_dir)
            if original is None:
                continue
            meta = read_meta(record_dir)
            yield (meta.get("source", source_dir.name), meta.get("native_id", record_dir.name),
                   original, meta)


def compile_scenarios(catalogue: Catalogue) -> list[KnowledgeUnit]:
    cards = cards_from_rows(catalogue.knowledge("case_card"))
    units = build_scenarios(cards)
    catalogue.replace_scenarios(units)
    return units


def export_knowledge(catalogue: Catalogue, paths: CcmaPaths) -> int:
    """Write every knowledge unit to data/ccma/knowledge/{kind}/{id}.json.

    The folder is generated output and is rebuilt from the catalogue each time.
    """
    target = paths.knowledge
    if target.exists():
        shutil.rmtree(target)
    written = 0
    for row in catalogue.knowledge():
        folder = target / row["kind"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{row['id']}.json").write_text(json.dumps({
            "id": row["id"],
            "kind": row["kind"],
            "record_fk": row["record_fk"],
            "title": row["title"],
            "confidence": row["confidence"],
            "dispute_type": row["dispute_type"],
            "citations": json.loads(row["citations_json"]),
            "body": json.loads(row["body"]),
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        written += 1
    return written


def read_corpus_text(corpus_root: Path, doc_id: str) -> str | None:
    path = corpus_root / "text" / f"{doc_id}.txt"
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else None
