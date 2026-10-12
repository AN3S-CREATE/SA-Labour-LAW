"""The `parse` stage end to end, and the pipeline guarantees it relies on.

No network. Every corpus here is a tmp_path corpus built by the test; nothing
reads or writes data/.

Covered: a statute never carries a dispute outcome; an OCR transcription is
parsed like any other text instead of being pasted over an empty parse
(D-014, D-016); one bad document never ends a re-parse (D-008); a .docx that
would decompress to gigabytes is refused before python-docx reads it; the
rebuild script stops on a failing stage; the CLI's annotations are real types.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import orjson
import pytest
from typer.testing import CliRunner

from labour_corpus import cli as cli_module
from labour_corpus.cli import app
from labour_corpus.config import CorpusPaths
from labour_corpus.http import FetchResult
from labour_corpus.models import DocType, Outcome, Remedy, TextStatus, make_doc_id
from labour_corpus.observability import RunLog
from labour_corpus.parse.ocr import OCR_PROVENANCE_NOTE, OcrResult
from labour_corpus.parse.redaction import _plausible_sa_id
from labour_corpus.pipeline import (
    DOCX_MAX_COMPRESSION_RATIO,
    build_parsed_doc,
    extract_text,
    ocr_sidecar_path,
    ooxml_size_refusal,
    persist,
)
from labour_corpus.state import CorpusStore

PKG_ROOT = Path(__file__).resolve().parents[1]
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

runner = CliRunner()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _valid_sa_id() -> str:
    """An identity number with a correct Luhn digit, so redaction must fire."""
    for digit in range(10):
        candidate = "800101500108" + str(digit)
        if _plausible_sa_id(candidate):
            return candidate
    raise AssertionError("could not construct a valid test identity number")


def _blank_pdf() -> bytes:
    """A one-page PDF with no text layer: what a scan looks like to pypdf."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _draft_code_transcription(id_number: str) -> str:
    return (
        "DRAFT CODE OF GOOD PRACTICE ON DISMISSAL\n\n"
        "Published for public comment. Interested parties are invited to submit "
        "written comments on this draft to the Department of Employment and Labour.\n\n"
        "1. An employee who is dismissed for misconduct such as theft or dishonesty "
        "must be given a hearing, and the employer must show that the dismissal "
        "was for a fair reason and that the employee was heard.\n\n"
        f"2. The employee in this example holds identity number {id_number} and was "
        "dismissed after an enquiry into insubordination and the refusal to obey "
        "an instruction of the employer.\n"
    )


def _write_raw(
    root: Path,
    name: str,
    *,
    body: bytes,
    extension: str,
    url: str,
    doc_type: DocType,
    content_type: str,
) -> Path:
    """Lay out raw/<source>/<year>/<name>/ the way store_raw does.

    The directory name is chosen by the caller so tests control the order in
    which `parse` meets documents (it walks them sorted).
    """
    directory = root / "raw" / "s" / "2026" / name
    directory.mkdir(parents=True)
    sha = hashlib.sha256(body).hexdigest()
    (directory / f"source.{extension}").write_bytes(body)
    (directory / "headers.json").write_bytes(orjson.dumps({"content-type": content_type}))
    (directory / "fetch_meta.json").write_bytes(orjson.dumps({
        "doc_id": make_doc_id(url, sha),
        "source_id": "s",
        "url": url,
        "final_url": url,
        "http_status": 200,
        "content_sha256": sha,
        "bytes": len(body),
        "doc_type": doc_type.value,
        "forum": "DEL",
        "provenance": "crawled",
    }))
    return directory


def _parsed_json(root: Path, url: str, body: bytes) -> dict:
    doc_id = make_doc_id(url, hashlib.sha256(body).hexdigest())
    return orjson.loads((root / "parsed" / f"{doc_id}.json").read_bytes())


def _documents(root: Path) -> dict[str, dict]:
    store = CorpusStore(CorpusPaths(root=root).corpus_db)
    try:
        return {row["canonical_url"]: dict(row) for row in store.iter_documents()}
    finally:
        store.close()


def _error_lines(root: Path) -> list[dict]:
    path = CorpusPaths(root=root).error_log
    if not path.exists():
        return []
    return [orjson.loads(line) for line in path.read_bytes().splitlines() if line.strip()]


def _parse(root: Path, *extra: str):
    return runner.invoke(app, ["parse", "--out", str(root), *extra])


HTML_BODY = (
    b"<html><head><title>Basic Conditions of Employment Act</title></head><body>"
    b"<p>" + b"The employer must pay the employee for overtime worked. " * 10 + b"</p>"
    b"</body></html>"
)


def _text_result(text: str, url: str = "https://example.org/acts/lra") -> FetchResult:
    body = text.encode("utf-8")
    return FetchResult(
        url=url, final_url=url, status=200,
        headers={"content-type": "text/plain"}, body=body,
        content_sha256=hashlib.sha256(body).hexdigest(), bytes=len(body), elapsed_ms=0,
    )


# ---------------------------------------------------------------------------
# a statute decides nothing
# ---------------------------------------------------------------------------
STATUTE_TEXT = (
    "LABOUR RELATIONS ACT 66 OF 1995\n\n"
    "187. Automatically unfair dismissals. A dismissal is automatically unfair "
    "if the reason for the dismissal is that the employee took part in a "
    "protected strike.\n\n"
    "193. Remedies. If the Labour Court finds that a dismissal is unfair, the "
    "court ordered reinstatement of the employee from any date not earlier "
    "than the date of dismissal.\n"
)


def test_a_statute_carries_no_outcome_remedy_or_outcome_audit_row(tmp_path):
    """An Act that defines s187 has not found any dismissal automatically unfair."""
    doc, text = build_parsed_doc(
        source_id="s", forum="DEL", result=_text_result(STATUTE_TEXT),
        default_doc_type=DocType.STATUTE, license_note="",
    )
    assert doc.outcome == Outcome.UNKNOWN
    assert doc.remedy == Remedy.UNKNOWN
    assert not any(e.category == "outcome_decision" for e in doc.tag_evidence)
    # What the Act is about is still recorded.
    assert doc.dismissal_tags

    paths = CorpusPaths(root=tmp_path / "corpus")
    paths.ensure()
    store = CorpusStore(paths.corpus_db)
    try:
        persist(paths, store, doc, text)
        row = store.conn.execute(
            "SELECT outcome, remedy FROM documents WHERE doc_id = ?", (doc.doc_id,)
        ).fetchone()
        audit = store.conn.execute(
            "SELECT COUNT(*) FROM tags WHERE doc_id = ? AND category = 'outcome_decision'",
            (doc.doc_id,),
        ).fetchone()[0]
    finally:
        store.close()
    assert (row["outcome"], row["remedy"]) == ("unknown", "unknown")
    assert audit == 0


def test_a_judgment_still_carries_its_inferred_outcome_and_remedy():
    doc, _ = build_parsed_doc(
        source_id="s", forum="ZALCJHB", result=_text_result(STATUTE_TEXT),
        default_doc_type=DocType.JUDGMENT, license_note="",
    )
    assert doc.outcome == Outcome.AUTOMATICALLY_UNFAIR
    assert doc.remedy == Remedy.REINSTATEMENT
    assert any(e.category == "outcome_decision" for e in doc.tag_evidence)


# ---------------------------------------------------------------------------
# an OCR transcription is parsed, not pasted
# ---------------------------------------------------------------------------
def test_text_override_derives_every_field_from_the_transcription():
    id_number = _valid_sa_id()
    body = _blank_pdf()
    url = "https://example.org/codes/draft-dismissal.pdf"
    result = FetchResult(
        url=url, final_url=url, status=200, headers={"content-type": "application/pdf"},
        body=body, content_sha256=hashlib.sha256(body).hexdigest(), bytes=len(body),
        elapsed_ms=0,
    )

    scan, scan_text = build_parsed_doc(
        source_id="s", forum="DEL", result=result,
        default_doc_type=DocType.CODE_OF_GOOD_PRACTICE, license_note="",
    )
    assert scan.text_status == TextStatus.OCR_NEEDED
    assert scan_text == ""

    doc, text = build_parsed_doc(
        source_id="s", forum="DEL", result=result,
        default_doc_type=DocType.CODE_OF_GOOD_PRACTICE, license_note="",
        text_override=_draft_code_transcription(id_number),
    )
    assert doc.doc_id == scan.doc_id
    assert doc.text_status == TextStatus.OK
    assert doc.parse_status == "ok"
    assert doc.is_draft is True
    assert doc.language == "en"
    assert {"theft", "dishonesty", "insubordination"} <= set(doc.dismissal_tags)
    assert doc.quotes
    # The transcription arrives unredacted; build_parsed_doc redacts it.
    assert doc.pi_redacted is True
    assert id_number not in text
    assert doc.text_chars == len(text)
    assert not any("no usable text layer" in n for n in doc.parser_notes)


def test_parse_rebuilds_a_scan_from_its_stored_transcription(tmp_path):
    """D-014 reuse, with every text-derived field re-derived (not 0 tags)."""
    root = tmp_path / "corpus"
    id_number = _valid_sa_id()
    transcription = _draft_code_transcription(id_number)
    body = _blank_pdf()
    url = "https://example.org/codes/draft-dismissal.pdf"
    raw_dir = _write_raw(
        root, "scan", body=body, extension="pdf", url=url,
        doc_type=DocType.CODE_OF_GOOD_PRACTICE, content_type="application/pdf",
    )
    ocr_sidecar_path(raw_dir).write_text(transcription, encoding="utf-8")

    outcome = _parse(root)
    assert outcome.exit_code == 0, outcome.output

    parsed = _parsed_json(root, url, body)
    assert parsed["ocr_applied"] is True
    assert parsed["text_status"] == "ok"
    assert parsed["parse_status"] == "ok"
    assert parsed["language"] == "en"
    assert parsed["is_draft"] is True
    assert parsed["draft_reason"]
    assert parsed["dismissal_tags"]
    assert parsed["quotes"]
    assert parsed["parser_confidence"] <= 0.5
    assert parsed["pi_redacted"] is True
    assert parsed["provenance"] == "crawled"
    assert any("stored OCR transcription" in n for n in parsed["parser_notes"])
    assert not any("no usable text layer" in n for n in parsed["parser_notes"])

    text = (root / "text" / f"{parsed['doc_id']}.txt").read_text(encoding="utf-8")
    assert id_number not in text
    # The sidecar is the operator's OCR work: it is read, never rewritten.
    assert ocr_sidecar_path(raw_dir).read_text(encoding="utf-8") == transcription

    row = _documents(root)[url]
    assert row["is_draft"] == 1
    assert row["language"] == "en"
    assert row["parse_status"] == "ok"
    store = CorpusStore(CorpusPaths(root=root).corpus_db)
    try:
        tags = store.conn.execute(
            "SELECT COUNT(*) FROM tags WHERE doc_id = ?", (parsed["doc_id"],)
        ).fetchone()[0]
    finally:
        store.close()
    assert tags > 0


def test_a_stored_transcription_is_reused_when_the_reader_fails_on_every_page(
    tmp_path, monkeypatch
):
    """Measured on the real corpus: GG 54438's scan makes pypdf raise on every
    page, so it is now 'empty' (a reader failure), not 'ocr_needed'. Its OCR
    sidecar must still be reused, or 7,526 characters of read text are lost."""
    import pypdf

    def broken(self, *args, **kwargs):
        raise IndexError("corrupt content stream")

    monkeypatch.setattr(pypdf.PageObject, "extract_text", broken)
    root = tmp_path / "corpus"
    transcription = _draft_code_transcription(_valid_sa_id())
    body = _blank_pdf()
    url = "https://example.org/agreements/levies-scan.pdf"
    raw_dir = _write_raw(
        root, "scan", body=body, extension="pdf", url=url,
        doc_type=DocType.CODE_OF_GOOD_PRACTICE, content_type="application/pdf",
    )
    ocr_sidecar_path(raw_dir).write_text(transcription, encoding="utf-8")

    outcome = _parse(root)
    assert outcome.exit_code == 0, outcome.output
    parsed = _parsed_json(root, url, body)
    assert parsed["text_status"] == "ok"
    assert parsed["ocr_applied"] is True
    assert parsed["text_chars"] > 0
    assert any("stored OCR transcription" in n for n in parsed["parser_notes"])


def test_parse_with_ocr_rebuilds_from_a_fresh_transcription(tmp_path, monkeypatch):
    from labour_corpus.parse import ocr as ocr_module

    root = tmp_path / "corpus"
    id_number = _valid_sa_id()
    transcription = _draft_code_transcription(id_number)
    body = _blank_pdf()
    url = "https://example.org/codes/draft-dismissal.pdf"
    raw_dir = _write_raw(
        root, "scan", body=body, extension="pdf", url=url,
        doc_type=DocType.CODE_OF_GOOD_PRACTICE, content_type="application/pdf",
    )

    monkeypatch.setattr(ocr_module, "ocr_toolchain_status", lambda: {
        "tesseract": "tesseract", "tesseract_available": True,
        "rapidocr_available": False, "engine": "tesseract",
        "page_renderer_available": True, "ready": True,
    })
    monkeypatch.setattr(ocr_module, "ocr_pdf", lambda data, **_: OcrResult(
        text=transcription, ok=True, pages_read=1, engine="tesseract",
        note=f"OCR read 1 page(s) at 220 dpi using tesseract. {OCR_PROVENANCE_NOTE}",
    ))

    outcome = _parse(root, "--ocr", "--ocr-dpi", "220")
    assert outcome.exit_code == 0, outcome.output

    # The unredacted transcription is kept beside the scan for the next run.
    assert ocr_sidecar_path(raw_dir).read_text(encoding="utf-8") == transcription

    parsed = _parsed_json(root, url, body)
    assert parsed["ocr_applied"] is True
    assert parsed["is_draft"] is True
    assert parsed["language"] == "en"
    assert parsed["parse_status"] == "ok"
    assert parsed["dismissal_tags"]
    assert parsed["parser_confidence"] <= 0.5
    assert any(OCR_PROVENANCE_NOTE in n for n in parsed["parser_notes"])
    text = (root / "text" / f"{parsed['doc_id']}.txt").read_text(encoding="utf-8")
    assert id_number not in text


def test_a_failed_ocr_leaves_the_scan_flagged_with_the_reason(tmp_path, monkeypatch):
    from labour_corpus.parse import ocr as ocr_module

    root = tmp_path / "corpus"
    body = _blank_pdf()
    url = "https://example.org/codes/scan.pdf"
    raw_dir = _write_raw(
        root, "scan", body=body, extension="pdf", url=url,
        doc_type=DocType.CODE_OF_GOOD_PRACTICE, content_type="application/pdf",
    )
    monkeypatch.setattr(ocr_module, "ocr_pdf", lambda data, **_: OcrResult(
        ok=False, note="OCR with tesseract produced no text",
    ))

    outcome = _parse(root, "--ocr")
    assert outcome.exit_code == 0, outcome.output
    parsed = _parsed_json(root, url, body)
    assert parsed["text_status"] == "ocr_needed"
    assert parsed["ocr_applied"] is False
    assert "OCR with tesseract produced no text" in parsed["parser_notes"]
    assert not ocr_sidecar_path(raw_dir).exists()


# ---------------------------------------------------------------------------
# D-008: one bad document never ends a re-parse
# ---------------------------------------------------------------------------
BAD_URL = "https://example.org/a_bad.pdf"
GOOD_URL = "https://example.org/b_good.html"


def _bad_then_good(root: Path) -> None:
    """A PDF that sorts first, then a healthy HTML document after it."""
    _write_raw(
        root, "a_bad", body=_blank_pdf(), extension="pdf", url=BAD_URL,
        doc_type=DocType.STATUTE, content_type="application/pdf",
    )
    _write_raw(
        root, "b_good", body=HTML_BODY, extension="html", url=GOOD_URL,
        doc_type=DocType.STATUTE, content_type="text/html",
    )


def test_a_parser_crash_on_one_pdf_does_not_stop_the_run(tmp_path, monkeypatch):
    """pypdf raises IndexError and friends, which are not ValueError/OSError."""
    from labour_corpus import pipeline

    def explode(data: bytes):
        raise IndexError("list index out of range")

    monkeypatch.setattr(pipeline, "extract_pdf_text", explode)
    root = tmp_path / "corpus"
    _bad_then_good(root)

    outcome = _parse(root)
    assert outcome.exit_code == 0, outcome.output
    assert "re-parsed 1, failed 1" in outcome.output

    documents = _documents(root)
    assert set(documents) == {GOOD_URL}
    assert documents[GOOD_URL]["text_status"] == "ok"

    errors = _error_lines(root)
    assert [e["url"] for e in errors] == [BAD_URL]
    assert errors[0]["error"].startswith("IndexError")


def test_any_exception_at_the_document_boundary_is_recorded(tmp_path, monkeypatch):
    original = cli_module.build_parsed_doc

    def flaky(**kwargs):
        if kwargs["result"].url == BAD_URL:
            raise RuntimeError("parser bug")
        return original(**kwargs)

    monkeypatch.setattr(cli_module, "build_parsed_doc", flaky)
    root = tmp_path / "corpus"
    _bad_then_good(root)

    outcome = _parse(root)
    assert outcome.exit_code == 0, outcome.output
    assert "re-parsed 1, failed 1" in outcome.output
    assert set(_documents(root)) == {GOOD_URL}
    assert [e["error"] for e in _error_lines(root)] == ["RuntimeError: parser bug"]


def test_a_malformed_fetch_meta_object_is_one_failure(tmp_path):
    root = tmp_path / "corpus"
    _bad_then_good(root)
    # Valid JSON, wrong shape: .get on a list raises AttributeError.
    (root / "raw" / "s" / "2026" / "a_bad" / "fetch_meta.json").write_bytes(b"[]")

    outcome = _parse(root)
    assert outcome.exit_code == 0, outcome.output
    assert "re-parsed 1, failed 1" in outcome.output
    assert set(_documents(root)) == {GOOD_URL}
    errors = _error_lines(root)
    assert len(errors) == 1 and errors[0]["error"].startswith("AttributeError")
    assert errors[0]["url"].endswith("fetch_meta.json")


def test_keyboard_interrupt_still_stops_the_run(tmp_path, monkeypatch):
    """The boundary catches Exception, not BaseException."""

    def interrupt(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "build_parsed_doc", interrupt)
    root = tmp_path / "corpus"
    _bad_then_good(root)

    outcome = _parse(root)
    assert outcome.exit_code != 0
    assert _documents(root) == {}
    assert _error_lines(root) == []


# ---------------------------------------------------------------------------
# .docx: decompression bombs and malformed XML
# ---------------------------------------------------------------------------
def _zip_with_large_part(size: int) -> bytes:
    """A zip whose word/document.xml is `size` bytes of 'a' (about 1000:1)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        chunk = b"a" * 1_000_000
        with package.open("word/document.xml", "w") as part:
            for _ in range(size // len(chunk)):
                part.write(chunk)
    return buffer.getvalue()


def test_a_docx_bomb_is_refused_before_python_docx_reads_it(monkeypatch):
    def must_not_open(*args, **kwargs):
        raise AssertionError("python-docx was asked to decompress a refused package")

    monkeypatch.setattr("docx.Document", must_not_open)
    body = _zip_with_large_part(12_000_000)
    assert len(body) < 1_000_000  # small on the wire, large once expanded

    text, _title, status, notes = extract_text(body, DOCX_TYPE, "https://example.org/a.docx")
    assert status is TextStatus.UNSUPPORTED_FORMAT
    assert text == ""
    assert any("docx refused" in n and "safety limits" in n for n in notes)


def _info(name: str, size: int, compressed: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.file_size = size
    info.compress_size = compressed
    return info


def test_ooxml_size_limits_read_only_the_declared_sizes():
    # One part above the per-part cap, even at a harmless ratio.
    assert ooxml_size_refusal([_info("word/document.xml", 150_000_000, 140_000_000)])
    # Parts each under the cap, but too much in total.
    parts = [_info(f"word/media/image{i}.png", 90_000_000, 89_000_000) for i in range(3)]
    assert ooxml_size_refusal(parts)
    # A large part with an implausible ratio.
    large = 20_000_000
    assert ooxml_size_refusal([
        _info("word/document.xml", large, large // (DOCX_MAX_COMPRESSION_RATIO + 50))
    ])
    # Small repetitive XML compresses far past 200:1 and is not refused.
    assert ooxml_size_refusal([_info("word/styles.xml", 5_000_000, 5_000)]) is None
    # A real-sized package passes.
    assert ooxml_size_refusal([
        _info("word/document.xml", 600_000, 90_000),
        _info("word/media/image1.png", 400_000, 395_000),
    ]) is None


def _docx_bytes(paragraph: str) -> bytes:
    from docx import Document

    document = Document()
    document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_an_ordinary_docx_still_parses():
    body = _docx_bytes("This is an ordinary paragraph of body text " * 12)
    text, _title, status, _notes = extract_text(body, DOCX_TYPE, "https://example.org/a.docx")
    assert status is TextStatus.OK
    assert "ordinary paragraph" in text


def _malformed_docx() -> bytes:
    """A valid package whose main document part is not well-formed XML."""
    source = zipfile.ZipFile(io.BytesIO(_docx_bytes("placeholder")))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = (
                b"<not closed" if item.filename == "word/document.xml"
                else source.read(item.filename)
            )
            target.writestr(item.filename, data)
    return buffer.getvalue()


def test_malformed_docx_xml_is_reported_not_raised():
    """lxml's XMLSyntaxError is a SyntaxError and used to end the whole run."""
    text, _title, status, notes = extract_text(
        _malformed_docx(), DOCX_TYPE, "https://example.org/bad.docx"
    )
    assert status is TextStatus.EMPTY
    assert text == ""
    assert any("XMLSyntaxError" in n for n in notes)


def test_a_malformed_docx_in_the_inbox_does_not_end_the_ingest(tmp_path):
    from labour_corpus.sources.inbox import ingest_inbox

    paths = CorpusPaths(root=tmp_path / "corpus")
    paths.ensure()
    (paths.inbox / "a_bad.docx").write_bytes(_malformed_docx())
    (paths.inbox / "b_good.txt").write_text(
        "The commissioner found that the dismissal of the employee was unfair. " * 6,
        encoding="utf-8",
    )
    store = CorpusStore(paths.corpus_db)
    try:
        outcome = ingest_inbox(paths, store, RunLog(paths.run_log, paths.error_log))
        rows = {Path(r["canonical_url"]).name: dict(r) for r in store.iter_documents()}
    finally:
        store.close()

    assert outcome.scanned == 2
    assert rows["b_good.txt"]["text_status"] == "ok"
    assert rows.get("a_bad.docx", {}).get("text_status") != "ok"


def test_any_parser_failure_in_the_inbox_costs_one_file_not_the_run(tmp_path, monkeypatch):
    """D-008: the inbox's document boundary is broad, like crawl and parse."""
    from labour_corpus.sources import inbox

    real = inbox.build_parsed_doc

    def flaky(**kwargs):
        if kwargs["result"].url.endswith("a_bad.txt"):
            raise RuntimeError("a parser failure no specific handler names")
        return real(**kwargs)

    monkeypatch.setattr(inbox, "build_parsed_doc", flaky)
    paths = CorpusPaths(root=tmp_path / "corpus")
    paths.ensure()
    body = "The commissioner found that the dismissal of the employee was unfair. " * 6
    (paths.inbox / "a_bad.txt").write_text(body, encoding="utf-8")
    (paths.inbox / "b_good.txt").write_text(body + "Second file.", encoding="utf-8")
    store = CorpusStore(paths.corpus_db)
    try:
        outcome = inbox.ingest_inbox(paths, store, RunLog(paths.run_log, paths.error_log))
        names = {Path(r["canonical_url"]).name for r in store.iter_documents()}
    finally:
        store.close()

    assert outcome.failed == 1
    assert names == {"b_good.txt"}
    assert "RuntimeError" in paths.error_log.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# the CLI's annotations
# ---------------------------------------------------------------------------
def _all_callbacks(typer_app):
    """Every command callback on an app and, recursively, its sub-apps."""
    callbacks = [command.callback for command in typer_app.registered_commands]
    for group in typer_app.registered_groups:
        callbacks.extend(_all_callbacks(group.typer_instance))
    return callbacks


def test_cli_command_annotations_are_types_not_strings():
    """A string annotation makes Typer compile and eval it on every invocation.

    Walks the top-level app and its ccma and navigate sub-apps, so a later
    `from __future__ import annotations` in any of the three modules fails here.
    """
    callbacks = _all_callbacks(app)
    assert len(callbacks) > len(app.registered_commands)  # the sub-apps were reached
    stringly = [
        f"{callback.__name__}.{name}"
        for callback in callbacks
        for name, annotation in callback.__annotations__.items()
        if isinstance(annotation, str)
    ]
    assert stringly == []


@pytest.mark.parametrize("args", [["--help"], ["parse", "--help"]])
def test_cli_help_builds(args):
    outcome = runner.invoke(app, args)
    assert outcome.exit_code == 0, outcome.output


# ---------------------------------------------------------------------------
# rebuild_corpus.sh
# ---------------------------------------------------------------------------
REBUILD_SCRIPT = PKG_ROOT / "rebuild_corpus.sh"


def test_rebuild_script_fails_fast_and_guards_its_filters():
    script = REBUILD_SCRIPT.read_text(encoding="utf-8")
    assert "set -euo pipefail" in script
    for line in script.splitlines():
        if "grep" in line and not line.lstrip().startswith("#"):
            # grep exits 1 when nothing matches; unguarded, pipefail would
            # abort a stage that succeeded.
            assert "|| true" in line, line


def _posix_bash() -> str | None:
    """A bash that runs POSIX scripts here; never the WSL launcher on Windows."""
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    git = shutil.which("git")
    if git:
        candidate = Path(git).resolve().parents[1] / "bin" / "bash.exe"
        if candidate.exists():
            return str(candidate)
    return None


def _run_rebuild(tmp_path: Path, fail_stage: str) -> subprocess.CompletedProcess:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("no POSIX bash available")
    shutil.copy(REBUILD_SCRIPT, tmp_path / "rebuild_corpus.sh")
    # `python` is replaced by a function that prints a line no filter matches
    # and fails only for the named stage.
    stub = (
        'python() { echo "stage $3 ran"; [ "$3" != "$FAIL_STAGE" ]; }; '
        'export -f python; bash ./rebuild_corpus.sh "$@"'
    )
    return subprocess.run(
        [bash, "-c", stub, "_", "./out", "operator@example.org"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
        env={**os.environ, "FAIL_STAGE": fail_stage},
    )


def test_rebuild_script_completes_when_every_stage_succeeds(tmp_path):
    run = _run_rebuild(tmp_path, fail_stage="none")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "=== REBUILD COMPLETE ===" in run.stdout


def test_rebuild_script_stops_at_a_failing_stage(tmp_path):
    run = _run_rebuild(tmp_path, fail_stage="parse")
    assert run.returncode != 0
    assert "REBUILD COMPLETE" not in run.stdout
    assert "=== 5. index ===" not in run.stdout
    assert "REBUILD FAILED" in run.stderr


# ---------------------------------------------------------------------------
# a machine transcription does not retype or retitle by mention
# ---------------------------------------------------------------------------
FUND_RULES_OCR = (
    "MOTOR INDUSTRY SICK, ACCIDENT AND MATERNITY PAY FUND\n"
    "AGREEMENT 2025\n\n"
    "1. DEFINITIONS\n"
    '"Act" means the Labour Relations Act 66 of 1995, as amended;\n'
    "2. The Fund shall be administered in terms of these Regulations of the Council.\n"
) * 3


def test_a_transcription_keeps_its_declared_type_and_filename_title():
    """The body scan and self-title read a cited Act; on OCR text only
    self-identity or the title may retype or retitle a document (D-014, D-015)."""
    result = _text_result(FUND_RULES_OCR, url="https://www.mibco.org.za/Sick-Fund-Agreement-2025.pdf")
    doc, _ = build_parsed_doc(
        source_id="bc_mibco", forum=None, result=result,
        default_doc_type=DocType.BARGAINING_COUNCIL_AGREEMENT, license_note="",
        text_override=FUND_RULES_OCR,
    )
    assert doc.doc_type == DocType.BARGAINING_COUNCIL_AGREEMENT
    assert doc.title != "Labour Relations Act"
    assert "Sick-Fund-Agreement-2025" in (doc.title or "")


def test_a_transcription_of_an_act_that_names_itself_is_still_typed_and_titled():
    act = (
        "Unemployment Insurance Act 63 of 2001\nSouth Africa: Consolidated Acts\n\n"
        "ACT\nTo provide for the payment from the Unemployment Insurance Fund ...\n"
    ) * 3
    result = _text_result(act, url="https://www.labour.gov.za/uia-scan.pdf")
    doc, _ = build_parsed_doc(
        source_id="del_acts", forum=None, result=result,
        default_doc_type=DocType.FORM, license_note="", text_override=act,
    )
    assert doc.doc_type == DocType.STATUTE
    assert doc.title == "Unemployment Insurance Act"
