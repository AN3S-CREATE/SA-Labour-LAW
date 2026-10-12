"""The harvest pipeline: fetch, store raw, parse, tag, persist.

Every stage is separately re-runnable and every stage is idempotent. Storing a
document twice produces the same doc_id, the same paths and the same database
row, so a killed run can simply be started again.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

import orjson

from .config import CorpusPaths
from .http import FetchResult
from .models import (
    DocType,
    Gap,
    Outcome,
    ParsedDoc,
    ParseStatus,
    Quote,
    Remedy,
    TextStatus,
    make_doc_id,
    utc_now_iso,
)
from .ontology.tagger import tag_document
from .parse.judgment import parse_judgment
from .parse.pdf import extract_pdf_text
from .parse.redaction import redact
from .parse.statute import parse_statute
from .parse.office import SniffedFormat, extract_doc_text, sniff_format
from .parse.text import (
    classify_text,
    detect_language,
    html_title,
    html_to_text,
    normalise,
    rtf_to_text,
)
from .state import CorpusStore

MAX_QUOTES = 6
QUOTE_MAX_CHARS = 320

# A .docx is a zip, and python-docx decompresses every part it reaches fully
# into memory. Deflate expands roughly 1000:1, so the compressed body cap alone
# admits tens of gigabytes. The largest held package measured 1.1 MB in total
# and 0.6 MB in its largest part, so these limits cost no real document.
DOCX_MAX_TOTAL_BYTES = 200_000_000
DOCX_MAX_PART_BYTES = 100_000_000
# The ratio test applies only to large parts: small XML parts are repetitive
# and compress far better than 200:1 without being dangerous.
DOCX_RATIO_MIN_PART_BYTES = 10_000_000
DOCX_MAX_COMPRESSION_RATIO = 200


@dataclass(slots=True)
class StoredRaw:
    directory: Path
    body_path: Path
    doc_id: str


def _extension_for(content_type: str | None, url: str) -> str:
    lowered = (content_type or "").lower()
    if "pdf" in lowered:
        return "pdf"
    if "rtf" in lowered:
        return "rtf"
    if "word" in lowered or "officedocument" in lowered:
        return "docx"
    if "html" in lowered or "xml" in lowered:
        return "html"
    tail = url.lower().rsplit(".", 1)[-1]
    if tail in {"pdf", "rtf", "html", "htm", "docx", "doc", "txt"}:
        return "html" if tail == "htm" else tail
    return "html"


def store_raw(
    paths: CorpusPaths,
    *,
    source_id: str,
    result: FetchResult,
    year: int | None,
    doc_type: DocType | None = None,
    forum: str | None = None,
    provenance: str | None = None,
) -> StoredRaw:
    """Write the raw body, headers and fetch metadata under raw/.

    ``doc_type``, ``forum`` and ``provenance`` are recorded in fetch_meta.json so
    that a later ``parse`` run can reconstruct the document without consulting
    the source registry. That matters for documents whose source is not a
    crawl target, such as operator-supplied or legacy-imported material.
    """
    doc_id = make_doc_id(result.url, result.content_sha256)
    bucket = str(year) if year else "undated"
    directory = paths.raw / source_id / bucket / doc_id
    directory.mkdir(parents=True, exist_ok=True)

    extension = _extension_for(result.content_type, result.url)
    body_path = directory / f"source.{extension}"
    if result.body is not None:
        body_path.write_bytes(result.body)

    (directory / "headers.json").write_bytes(
        orjson.dumps(result.headers, option=orjson.OPT_INDENT_2)
    )
    (directory / "fetch_meta.json").write_bytes(
        orjson.dumps(
            {
                "doc_id": doc_id,
                "source_id": source_id,
                "url": result.url,
                "final_url": result.final_url,
                "http_status": result.status,
                "content_sha256": result.content_sha256,
                "bytes": result.bytes,
                "elapsed_ms": result.elapsed_ms,
                "oversize": result.oversize,
                "retrieved_at": utc_now_iso(),
                "doc_type": getattr(doc_type, "value", doc_type),
                "forum": forum,
                "provenance": provenance,
            },
            option=orjson.OPT_INDENT_2,
        )
    )
    return StoredRaw(directory=directory, body_path=body_path, doc_id=doc_id)


OCR_SIDECAR_NAME = "ocr.txt"


def ocr_sidecar_path(raw_dir: Path) -> Path:
    """Where a document's OCR transcription is kept, beside its raw bytes."""
    return Path(raw_dir) / OCR_SIDECAR_NAME


def save_ocr_text(raw_dir: Path, text: str) -> Path:
    """Persist an OCR transcription next to the raw file.

    OCR is expensive and its output is not recoverable from the scan, so it is
    written beside the bytes it came from. A later ``parse`` reuses it instead
    of re-extracting nothing from an image and silently losing the text.
    """
    path = ocr_sidecar_path(raw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def load_ocr_text(raw_dir: Path | None) -> str | None:
    if raw_dir is None:
        return None
    path = ocr_sidecar_path(raw_dir)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text or None


def ooxml_size_refusal(infos: list[zipfile.ZipInfo]) -> str | None:
    """Return why a zip package is too large to decompress, else None.

    Reads only the sizes the central directory declares, so nothing is
    decompressed to decide.
    """
    total = sum(info.file_size for info in infos)
    if total > DOCX_MAX_TOTAL_BYTES:
        return f"{total} bytes in total, limit {DOCX_MAX_TOTAL_BYTES}"
    for info in infos:
        if info.file_size > DOCX_MAX_PART_BYTES:
            return f"part {info.filename} is {info.file_size} bytes, limit {DOCX_MAX_PART_BYTES}"
        if (
            info.file_size > DOCX_RATIO_MIN_PART_BYTES
            and info.compress_size
            and info.file_size / info.compress_size > DOCX_MAX_COMPRESSION_RATIO
        ):
            ratio = info.file_size // info.compress_size
            return (
                f"part {info.filename} expands {ratio}:1, "
                f"limit {DOCX_MAX_COMPRESSION_RATIO}:1"
            )
    return None


def extract_text(body: bytes, content_type: str | None, url: str) -> tuple[str, str | None, TextStatus, list[str]]:
    """Return (text, title, status, notes) for a raw body.

    Format is decided by magic number, not by the Content-Type header or the
    URL extension. Government document libraries serve Word 97-2003 files as
    ``application/msword`` from ``.docx`` URLs, and trusting either label sends
    the bytes to the wrong reader.
    """
    notes: list[str] = []
    sniffed = sniff_format(body)
    declared = _extension_for(content_type, url)

    if sniffed is not SniffedFormat.UNKNOWN and sniffed.value not in declared:
        notes.append(
            f"format detected from magic bytes as {sniffed.value}; the server "
            f"labelled it {content_type or 'unlabelled'}"
        )

    if sniffed is SniffedFormat.PDF or (sniffed is SniffedFormat.UNKNOWN and declared == "pdf"):
        extracted = extract_pdf_text(body)
        notes.extend(extracted.notes)
        return extracted.text, None, extracted.status, notes

    if sniffed is SniffedFormat.OLE2_DOC:
        # Word 97-2003. This is a readable format with a missing reader, not a
        # scan, so it must never be reported as needing OCR.
        word = extract_doc_text(body)
        notes.append(word.note)
        if word.ok:
            text = normalise(word.text)
            return text, None, classify_text(text), notes
        return "", None, TextStatus.UNSUPPORTED_FORMAT, notes

    if sniffed is SniffedFormat.RTF or declared == "rtf":
        text = rtf_to_text(body)
        return text, None, classify_text(text), notes

    if sniffed is SniffedFormat.OOXML or declared == "docx":
        try:
            from docx import Document
            from docx.opc.exceptions import PackageNotFoundError

            with zipfile.ZipFile(io.BytesIO(body)) as package:
                refusal = ooxml_size_refusal(package.infolist())
            if refusal:
                notes.append(
                    "docx refused: declared uncompressed size exceeds safety "
                    f"limits ({refusal})"
                )
                return "", None, TextStatus.UNSUPPORTED_FORMAT, notes

            document = Document(io.BytesIO(body))
            # python-docx's `.paragraphs` covers body paragraphs only. Forms --
            # LRA referral forms among them -- put nearly all their content in
            # tables, so reading paragraphs alone returns an empty document.
            parts = [p.text for p in document.paragraphs]
            for table in document.tables:
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    line = " | ".join(c for c in cells if c)
                    if line:
                        parts.append(line)
            text = "\n".join(part for part in parts if part.strip())
            if document.tables:
                notes.append(f"read {len(document.tables)} table(s) as well as paragraphs")
            return text, None, classify_text(text), notes
        except (zipfile.BadZipFile, PackageNotFoundError) as exc:
            notes.append(
                f"not a readable Office Open XML package: {type(exc).__name__}"
            )
            return "", None, TextStatus.UNSUPPORTED_FORMAT, notes
        # SyntaxError covers lxml's XMLSyntaxError: a part that is not
        # well-formed XML must cost this document, not end the run.
        except (ValueError, KeyError, OSError, SyntaxError) as exc:
            notes.append(f"docx could not be read: {type(exc).__name__}: {exc}")
            return "", None, TextStatus.EMPTY, notes

    decoded = body.decode("utf-8", errors="ignore")
    title = html_title(decoded)
    text = html_to_text(decoded)
    return text, title, classify_text(text), notes


def title_from_url(url: str) -> str | None:
    """Derive a display title from the published filename.

    Used only when a document carries no title of its own, which is the norm
    for PDFs. It is a convenience label, not a citation, and callers record a
    parser note saying it was derived rather than read from the document.
    """
    from urllib.parse import unquote, urlparse

    name = unquote((urlparse(url).path or "").rsplit("/", 1)[-1])
    if not name:
        return None
    stem = name.rsplit(".", 1)[0]
    stem = stem.replace("_", " ").replace("%20", " ")
    stem = " ".join(stem.split())
    return stem or None


def _pick_quotes(text: str, evidence_paragraphs: list[str]) -> list[Quote]:
    """Take short grounded excerpts, preferring paragraphs the tagger cited."""
    if not text:
        return []
    quotes: list[Quote] = []
    blocks = [b.strip() for b in text.split("\n\n") if len(b.strip()) > 80]
    wanted = set(evidence_paragraphs)

    for block in blocks:
        label = None
        if block.startswith("["):
            end = block.find("]")
            if 0 < end <= 6:
                label = block[1:end]
        if wanted and label not in wanted:
            continue
        quotes.append(
            Quote(text=block[:QUOTE_MAX_CHARS].strip(), paragraph=label)
        )
        if len(quotes) >= MAX_QUOTES:
            break

    if not quotes:
        for block in blocks[:MAX_QUOTES]:
            quotes.append(Quote(text=block[:QUOTE_MAX_CHARS].strip()))
    return quotes


def build_parsed_doc(
    *,
    source_id: str,
    forum: str | None,
    result: FetchResult,
    default_doc_type: DocType,
    license_note: str,
    text_override: str | None = None,
) -> tuple[ParsedDoc, str]:
    """Parse a fetched body into a ParsedDoc plus its redacted plain text.

    ``text_override`` replaces text extraction with a text the caller already
    holds -- an OCR transcription of a scan (D-014). It must be unredacted:
    redaction, tagging, draft detection, language and quotes all run on it
    here exactly as on an extracted text layer.
    """
    doc_id = make_doc_id(result.url, result.content_sha256)
    notes: list[str] = []

    if result.oversize or result.body is None:
        doc = ParsedDoc(
            doc_id=doc_id,
            source_id=source_id,
            canonical_url=result.url,
            retrieved_at=utc_now_iso(),
            http_status=result.status,
            content_sha256=result.content_sha256,
            content_type=result.content_type,
            bytes=result.bytes,
            doc_type=default_doc_type,
            forum=forum,
            text_status=TextStatus.SKIPPED_OVERSIZE,
            parse_status=ParseStatus.PARTIAL,
            parser_confidence=0.0,
            parser_notes=["body exceeded the size cap; headers and URL stored only"],
            license_note=license_note,
        )
        return doc, ""

    if text_override is not None:
        # The body is a scan; the caller supplies its transcription. The
        # extraction notes ("no usable text layer") would describe the scan,
        # not this text, so none are carried.
        raw_text, title = text_override, None
        text_status = classify_text(text_override)
    else:
        raw_text, title, text_status, extract_notes = extract_text(
            result.body, result.content_type, result.url
        )
        notes.extend(extract_notes)

    report = redact(raw_text)
    text = report.text
    if report.sensitive_context:
        notes.append(
            "sensitive context detected (minor or sexual-offence matter); "
            "review before any republication"
        )

    tagging = tag_document(text)

    # An arbitration award carries the same adjudicative metadata as a judgment
    # -- case number, commissioner, date, parties, outcome -- so both go through
    # the judgment parser. Everything else is treated as an instrument.
    adjudicative = default_doc_type in (DocType.JUDGMENT, DocType.AWARD)
    judgment = parse_judgment(text, title=title) if adjudicative else None
    statute = (
        parse_statute(text, title=title, machine_transcription=text_override is not None)
        if not adjudicative else None
    )

    # Only a decision has an outcome or orders a remedy. An Act that defines an
    # automatically unfair dismissal (LRA s187) or lists reinstatement among
    # the remedies decided nothing, and a wrong outcome label is worse than an
    # absent one. Topic and dismissal tags stay: they describe what the text is
    # about, not what it decided.
    outcome = tagging.outcome if adjudicative else Outcome.UNKNOWN
    remedy = tagging.remedy if adjudicative else Remedy.UNKNOWN
    tag_evidence = [
        e for e in tagging.evidence
        if adjudicative or e.category != "outcome_decision"
    ]

    doc_type = default_doc_type
    if statute is not None and statute.doc_type is not DocType.OTHER:
        doc_type = statute.doc_type

    if text_status is TextStatus.OK and not text.strip():
        text_status = TextStatus.EMPTY

    parse_status = ParseStatus.OK
    if text_status in (TextStatus.OCR_NEEDED, TextStatus.EMPTY):
        parse_status = ParseStatus.PARTIAL

    confidences: list[float] = [tagging.confidence]
    if judgment is not None:
        confidences.append(judgment.confidence)
        notes.extend(judgment.notes)
    if statute is not None:
        confidences.append(statute.confidence)
        notes.extend(statute.notes)
    confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.0

    evidence_paragraphs = [p for e in tag_evidence for p in e.paragraphs]

    # Title precedence: the document's own title, then a statute short title
    # read from its text, then the published filename as a labelled fallback.
    display_title = title or (statute.short_title if statute else None)
    if not display_title and judgment is not None:
        display_title = judgment.neutral_citation
    if not display_title:
        display_title = title_from_url(result.url)
        if display_title:
            notes.append(
                "title derived from the published filename, not read from the "
                "document; it is a label, not a citation"
            )

    doc = ParsedDoc(
        doc_id=doc_id,
        source_id=source_id,
        canonical_url=result.url,
        retrieved_at=utc_now_iso(),
        http_status=result.status,
        content_sha256=result.content_sha256,
        content_type=result.content_type,
        bytes=result.bytes,
        doc_type=doc_type,
        forum=forum,
        is_draft=bool(statute and statute.is_draft),
        draft_reason=(statute.draft_reason if statute else ""),
        title=display_title,
        citation=judgment.citation if judgment else None,
        case_no=judgment.case_no if judgment else None,
        neutral_citation=judgment.neutral_citation if judgment else None,
        parties=judgment.parties if judgment else [],
        date_delivered=judgment.date_delivered if judgment else None,
        judges_or_commissioner=judgment.judges if judgment else [],
        year=judgment.year if judgment else (statute.act_year if statute else None),
        sector_tags=tagging.sector_tags,
        dismissal_tags=tagging.dismissal_tags,
        tag_evidence=tag_evidence,
        outcome=outcome,
        remedy=remedy,
        language=detect_language(text),
        text_status=text_status,
        text_chars=len(text),
        pi_redacted=report.redacted,
        redaction_counts=report.counts,
        quotes=_pick_quotes(text, evidence_paragraphs),
        parse_status=parse_status,
        parser_confidence=confidence,
        parser_notes=notes,
        license_note=license_note,
    )
    return doc, text


def persist(
    paths: CorpusPaths,
    store: CorpusStore,
    doc: ParsedDoc,
    text: str,
) -> bool:
    """Write text and parsed JSON, then upsert the database row."""
    if text:
        (paths.text / f"{doc.doc_id}.txt").write_text(text, encoding="utf-8")
    (paths.parsed / f"{doc.doc_id}.json").write_bytes(
        orjson.dumps(
            json.loads(doc.model_dump_json()),
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
        )
    )
    return store.upsert_document(doc)


def record_gap(store: CorpusStore, gap: Gap) -> None:
    store.record_gap(gap)
