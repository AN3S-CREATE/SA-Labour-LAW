"""Format-sniffing, legacy Word and OCR tests.

The lesson encoded here: a file this project cannot read is not automatically a
scan. Word 97-2003 served as `application/msword` from a `.docx` URL needs a
Word reader, and calling it `ocr_needed` sends the operator to the wrong tool.
"""

from __future__ import annotations

import builtins
import io
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from labour_corpus.models import TextStatus
from labour_corpus.parse.ocr import (
    OCR_PROVENANCE_NOTE,
    ocr_pdf,
    ocr_toolchain_status,
    preferred_engine,
)
from labour_corpus.parse.office import (
    SniffedFormat,
    antiword_available,
    extract_doc_text,
    sniff_format,
)
from labour_corpus.pipeline import extract_text

OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
ZIP_MAGIC = b"PK\x03\x04" + b"\x00" * 64
PDF_MAGIC = b"%PDF-1.4\n" + b"\x00" * 64
RTF_MAGIC = b"{\\rtf1\\ansi test}"


# -- sniffing --------------------------------------------------------------
@pytest.mark.parametrize("body,expected", [
    (OLE2_MAGIC, SniffedFormat.OLE2_DOC),
    (ZIP_MAGIC, SniffedFormat.OOXML),
    (PDF_MAGIC, SniffedFormat.PDF),
    (RTF_MAGIC, SniffedFormat.RTF),
    (b"<!DOCTYPE html><html><body>hi</body></html>", SniffedFormat.HTML),
    (b"<html><body>hi</body></html>", SniffedFormat.HTML),
    (b"just some plain text", SniffedFormat.PLAIN),
    (b"", SniffedFormat.UNKNOWN),
])
def test_format_is_decided_by_magic_bytes(body, expected):
    assert sniff_format(body) is expected


def test_sniffing_beats_a_lying_content_type():
    """labour.gov.za serves Word 97-2003 as application/msword from .docx URLs."""
    assert sniff_format(OLE2_MAGIC) is SniffedFormat.OLE2_DOC
    # Neither the header nor the extension would have got this right.
    _text, _title, status, notes = extract_text(
        OLE2_MAGIC, "application/msword", "https://example.org/a.docx"
    )
    assert status is not TextStatus.OK  # the stub has no real content
    assert any("magic bytes" in n for n in notes)


def test_a_legacy_doc_is_never_reported_as_needing_ocr():
    """The core correction: a readable format with a missing reader is not a scan."""
    _text, _title, status, _notes = extract_text(
        OLE2_MAGIC, "application/msword", "https://example.org/a.docx"
    )
    assert status is not TextStatus.OCR_NEEDED
    assert status is TextStatus.UNSUPPORTED_FORMAT


def test_a_corrupt_ooxml_is_unsupported_not_a_scan():
    _text, _title, status, _notes = extract_text(
        ZIP_MAGIC, "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "https://example.org/a.docx",
    )
    assert status is TextStatus.UNSUPPORTED_FORMAT


def test_html_still_parses_normally():
    body = b"<html><head><title>An Act</title></head><body><p>Long body text here. " \
           + b"more words " * 60 + b"</p></body></html>"
    text, title, status, _notes = extract_text(body, "text/html", "https://example.org/a.html")
    assert status is TextStatus.OK
    assert title == "An Act"
    assert "Long body text here" in text


# -- legacy Word -----------------------------------------------------------
def test_missing_antiword_is_reported_not_hidden(monkeypatch):
    monkeypatch.setattr("labour_corpus.parse.office.antiword_path", lambda: None)
    result = extract_doc_text(OLE2_MAGIC)
    assert result.ok is False
    assert "antiword is not installed" in result.note
    assert result.text == ""


@pytest.mark.skipif(not antiword_available(), reason="antiword not installed")
def test_antiword_rejects_a_non_doc_payload():
    """A stub with OLE magic but no Word streams must fail honestly."""
    result = extract_doc_text(OLE2_MAGIC)
    assert result.ok is False
    assert result.note


def _stub_antiword(monkeypatch, tmp_path, outcome):
    """Point tempfile at tmp_path and replace antiword with ``outcome``."""
    from labour_corpus.parse import office

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(office, "antiword_path", lambda: "antiword-stub")
    seen: list[Path] = []

    def fake_run(args, **_kwargs):
        seen.append(Path(args[-1]))
        assert seen[-1].read_bytes() == OLE2_MAGIC  # antiword gets the body
        if isinstance(outcome, Exception):
            raise outcome
        return subprocess.CompletedProcess(args, 0, outcome, b"")

    monkeypatch.setattr(office.subprocess, "run", fake_run)
    return seen


def test_legacy_doc_extraction_leaves_no_temporary_directory(monkeypatch, tmp_path):
    """Every .doc parsed used to leave an empty labour_doc_* directory behind."""
    seen = _stub_antiword(monkeypatch, tmp_path, b"Text of the legacy document")
    result = extract_doc_text(OLE2_MAGIC)
    assert result.ok is True
    assert result.text == "Text of the legacy document"
    assert seen and seen[0].parent.name.startswith("labour_doc_")
    assert list(tmp_path.glob("labour_doc_*")) == []


def test_a_timed_out_antiword_also_leaves_no_temporary_directory(monkeypatch, tmp_path):
    _stub_antiword(monkeypatch, tmp_path, subprocess.TimeoutExpired("antiword-stub", 60))
    result = extract_doc_text(OLE2_MAGIC)
    assert result.ok is False
    assert "TimeoutExpired" in result.note
    assert list(tmp_path.glob("labour_doc_*")) == []


def test_an_explicit_work_directory_is_kept_but_emptied(monkeypatch, tmp_path):
    _stub_antiword(monkeypatch, tmp_path, b"Text")
    work = tmp_path / "work"
    result = extract_doc_text(OLE2_MAGIC, tmp_dir=work)
    assert result.ok is True
    assert work.is_dir()
    assert list(work.iterdir()) == []


# -- PDF -------------------------------------------------------------------
def _blank_pdf(pages: int) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _error(name: str) -> type[Exception]:
    """A pypdf error class by name, else the builtin exception of that name."""
    from pypdf import errors

    return getattr(errors, name, None) or getattr(builtins, name)


@pytest.mark.parametrize("error", [
    "IndexError", "AttributeError", "LimitReachedError", "ParseError", "PdfReadError",
])
def test_one_unreadable_page_costs_one_page_not_the_document(monkeypatch, error):
    """pypdf raises these from corrupt content streams; none may end a parse run."""
    import pypdf

    from labour_corpus.parse.pdf import extract_pdf_text

    outcomes = iter([_error(error)("corrupt content stream"), "Readable page text. " * 20])

    def fake_extract_text(_page, *_args, **_kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(pypdf.PageObject, "extract_text", fake_extract_text)
    result = extract_pdf_text(_blank_pdf(2))
    assert result.pages == 2
    assert f"page 1 unreadable: {error}" in result.notes
    assert "1 page(s) could not be read" in result.notes
    assert "Readable page text." in result.text
    assert result.status is TextStatus.OK


@pytest.mark.parametrize("error", ["LimitReachedError", "ParseError", "PdfReadError"])
def test_a_pdf_that_cannot_be_opened_is_reported_empty(monkeypatch, error):
    """LimitReachedError and ParseError are PyPdfError but not PdfReadError."""
    import pypdf

    from labour_corpus.parse.pdf import extract_pdf_text

    def unopenable(_self, *_args, **_kwargs):
        raise _error(error)("decompression limit reached")

    monkeypatch.setattr(pypdf.PdfReader, "__init__", unopenable)
    result = extract_pdf_text(_blank_pdf(1))
    assert result.status is TextStatus.EMPTY
    assert any(n.startswith("pdf could not be opened") for n in result.notes)


def test_a_pdf_whose_page_tree_cannot_be_read_is_reported_empty(monkeypatch):
    import pypdf
    from pypdf.errors import LimitReachedError

    from labour_corpus.parse.pdf import extract_pdf_text

    def no_page_tree(_self):
        raise LimitReachedError("page tree too deep")

    monkeypatch.setattr(pypdf.PdfReader, "pages", property(no_page_tree))
    result = extract_pdf_text(_blank_pdf(1))
    assert result.status is TextStatus.EMPTY
    assert any(n.startswith("pdf page count unavailable") for n in result.notes)


def test_a_real_blank_pdf_still_reads_as_a_scan():
    from labour_corpus.ccma.extract import pdf_page_count
    from labour_corpus.parse.pdf import extract_pdf_text

    body = _blank_pdf(3)
    result = extract_pdf_text(body)
    assert result.pages == 3
    assert result.status is TextStatus.OCR_NEEDED
    assert pdf_page_count(body) == 3


def test_importing_the_cli_does_not_import_pypdf():
    """pypdf costs about a second at import; commands that read no PDF skip it.

    Run in a fresh interpreter: other tests in this process import pypdf.
    """
    code = (
        "import sys, labour_corpus.cli, labour_corpus.ccma.extract; "
        "loaded = sorted(m for m in sys.modules if m.startswith('pypdf')); "
        "assert not loaded, loaded"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, check=False,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert completed.returncode == 0, completed.stderr[-2000:]


# -- OCR -------------------------------------------------------------------
def test_ocr_toolchain_status_reports_honestly():
    status = ocr_toolchain_status()
    assert set(status) == {
        "tesseract", "tesseract_available", "rapidocr_available",
        "engine", "page_renderer_available", "ready",
    }
    # Ready means: one local engine present, and something to render pages with.
    assert status["ready"] == (
        status["engine"] is not None and status["page_renderer_available"]
    )
    if status["engine"] == "tesseract":
        assert status["tesseract_available"]
    elif status["engine"] == "rapidocr":
        assert status["rapidocr_available"]


def test_tesseract_is_preferred_when_both_engines_exist(monkeypatch):
    from labour_corpus.parse import ocr as ocr_module

    monkeypatch.setattr(ocr_module, "tesseract_available", lambda: True)
    monkeypatch.setattr(ocr_module, "rapidocr_available", lambda: True)
    assert ocr_module.preferred_engine() == "tesseract"

    monkeypatch.setattr(ocr_module, "tesseract_available", lambda: False)
    assert ocr_module.preferred_engine() == "rapidocr"

    monkeypatch.setattr(ocr_module, "rapidocr_available", lambda: False)
    assert ocr_module.preferred_engine() is None


def test_ocr_without_any_engine_returns_a_reason_not_empty_text(monkeypatch):
    """No engine must yield an explanation, never silent empty text."""
    monkeypatch.setattr(
        "labour_corpus.parse.ocr.ocr_toolchain_status",
        lambda: {
            "tesseract": None, "tesseract_available": False,
            "rapidocr_available": False, "engine": None,
            "page_renderer_available": True, "ready": False,
        },
    )
    result = ocr_pdf(PDF_MAGIC)
    assert result.ok is False
    assert result.text == ""
    assert "OCR engine" in result.note
    assert "pip install" in result.note


def test_ocr_text_is_labelled_as_a_transcription():
    """OCR output must never be presented as published text."""
    assert "machine transcription" in OCR_PROVENANCE_NOTE
    assert "confirm against the original" in OCR_PROVENANCE_NOTE


@pytest.mark.skipif(preferred_engine() is None, reason="no local OCR engine")
def test_ocr_on_a_non_pdf_fails_cleanly():
    result = ocr_pdf(b"not a pdf at all")
    assert result.ok is False
    assert result.note


def test_ocr_applied_flag_exists_on_the_contract():
    from labour_corpus.models import ParsedDoc

    doc = ParsedDoc(
        doc_id="x", source_id="s", canonical_url="u",
        retrieved_at="t", content_sha256="c",
    )
    assert doc.ocr_applied is False


# -- OCR durability --------------------------------------------------------
def test_ocr_text_is_persisted_beside_the_raw_bytes(tmp_path):
    """OCR is expensive and unrecoverable from the scan, so it must survive."""
    from labour_corpus.pipeline import load_ocr_text, ocr_sidecar_path, save_ocr_text

    raw_dir = tmp_path / "raw" / "src" / "2024" / "abc"
    raw_dir.mkdir(parents=True)

    assert load_ocr_text(raw_dir) is None
    save_ocr_text(raw_dir, "RECOGNISED TEXT OF THE ACT")
    assert ocr_sidecar_path(raw_dir).name == "ocr.txt"
    assert load_ocr_text(raw_dir) == "RECOGNISED TEXT OF THE ACT"


def test_load_ocr_text_handles_a_missing_directory():
    from labour_corpus.pipeline import load_ocr_text

    assert load_ocr_text(None) is None


# -- act titles: a citation is not a title ---------------------------------
@pytest.mark.parametrize("text,expected", [
    ("LABOUR RELATIONS ACT 66 OF 1995\nTo change the law.", "LABOUR RELATIONS ACT"),
    ("Labour Relations Act 66 of 1995\nTo change the law.", "Labour Relations Act"),
    ("EMPLOYMENT EQUITY ACT, NO. 55 OF 1998", "EMPLOYMENT EQUITY ACT"),
    ("Labour Relations Amendment Act 6 of 2014", "Labour Relations Amendment Act"),
])
def test_a_document_titles_itself(text, expected):
    from labour_corpus.parse.statute import parse_statute

    assert parse_statute(text).short_title == expected


@pytest.mark.parametrize("text", [
    "This agreement is concluded in terms of the Labour Relations Act 66 of 1995.",
    "All the provisions of the Labour Relations Act 66 of 1995 apply here.",
    "Act as well as a learner in terms of Chapter IV of the Skills Development Act 97 of 1998.",
])
def test_a_cross_reference_is_not_a_title(text):
    """A council agreement citing the LRA is not the LRA."""
    from labour_corpus.parse.statute import parse_statute

    assert parse_statute(text).short_title is None


def test_the_documents_own_title_wins_over_a_later_citation():
    from labour_corpus.parse.statute import parse_statute

    meta = parse_statute(
        "SKILLS DEVELOPMENT ACT 97 OF 1998\nissued under the Labour Relations Act 66 of 1995"
    )
    assert meta.short_title == "SKILLS DEVELOPMENT ACT"
    assert meta.act_number == "97"


# -- drafts are not the law ------------------------------------------------
@pytest.mark.parametrize("title", [
    "Draft Code of Good Practice on Dismissal for public comments, 22 March 2024",
    "Draft Code of Good Practice on Dismissal",
    "Employment Services Amendment Bill",
    "Proposed Code on Harassment for comment",
])
def test_a_draft_instrument_is_flagged(title):
    """A draft published for comment has no legal effect."""
    from labour_corpus.parse.statute import detect_draft, parse_statute

    is_draft, reason = detect_draft("", title)
    assert is_draft is True
    assert reason
    meta = parse_statute("body text", title=title)
    assert meta.is_draft is True
    assert any("not in force" in n for n in meta.notes)


@pytest.mark.parametrize("title,body", [
    ("Code of Good Practice on Dismissal", "This Code comes into operation on 4 September 2025."),
    ("LABOUR RELATIONS ACT 66 OF 1995", "[DATE OF COMMENCEMENT: 11 NOVEMBER 1996]"),
    ("Basic Conditions of Employment Act", "assented to by the President"),
])
def test_an_instrument_in_force_is_not_flagged(title, body):
    from labour_corpus.parse.statute import parse_statute

    assert parse_statute(body, title=title).is_draft is False


@pytest.mark.parametrize("opening", [
    "Code of Good Practice on the Prevention and Elimination of Harassment in the "
    "Workplace\nThis Code gives effect to the rights in the Bill of Rights.",
    "The employer must respect the BILL OF RIGHTS in Chapter 2 of the Constitution.",
    "in terms of the Bill\nof Rights",
])
def test_the_bill_of_rights_is_not_a_draft_bill(opening):
    """Chapter 2 of the Constitution is cited by in-force codes; it is no bill."""
    from labour_corpus.parse.statute import detect_draft

    assert detect_draft(opening) == (False, "")
    assert detect_draft("", title=opening) == (False, "")


@pytest.mark.parametrize("opening", [
    "Employment Equity Amendment Bill\nTo amend the Employment Equity Act.",
    "LABOUR RELATIONS AMENDMENT BILL\nAs introduced in the National Assembly",
])
def test_a_bill_is_still_a_draft(opening):
    from labour_corpus.parse.statute import detect_draft

    is_draft, reason = detect_draft(opening)
    assert is_draft is True
    assert "bill" in reason.lower()


def test_the_draft_flag_reaches_the_document_contract():
    from labour_corpus.models import ParsedDoc

    doc = ParsedDoc(
        doc_id="x", source_id="s", canonical_url="u",
        retrieved_at="t", content_sha256="c",
    )
    assert doc.is_draft is False


def test_the_dismissal_code_currency_claim_is_marked_unverified():
    """The brief asserted a current code; the corpus must not repeat it as fact."""
    from labour_corpus.sources.del_gov import CURRENT_DISMISSAL_CODE

    assert CURRENT_DISMISSAL_CODE["verified"] is False
    note = str(CURRENT_DISMISSAL_CODE["verification_note"])
    assert "UNVERIFIED" in note
    assert "draft is not the law" in note.lower()


# -- docx tables ------------------------------------------------------------
def test_docx_table_content_is_read(tmp_path):
    """Forms put their content in tables; reading paragraphs alone loses it.

    The LRA referral forms 3.7, 3.8, 3.17 and 3.18 extracted as empty until
    table cells were included.
    """
    from docx import Document

    from labour_corpus.pipeline import extract_text

    document = Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Name of employee"
    table.cell(0, 1).text = "Themba Mahlangu"
    table.cell(1, 0).text = "Nature of dispute"
    table.cell(1, 1).text = "Unfair dismissal in terms of section 191"

    path = tmp_path / "form.docx"
    document.save(path)
    body = path.read_bytes()

    text, _title, status, notes = extract_text(
        body,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "https://example.org/LRA-Form-7.11.docx",
    )
    assert "Themba Mahlangu" in text
    assert "section 191" in text
    assert status is not TextStatus.EMPTY
    assert any("table" in n for n in notes)


def test_docx_paragraphs_still_read(tmp_path):
    from docx import Document

    from labour_corpus.pipeline import extract_text

    document = Document()
    document.add_paragraph("This is an ordinary paragraph of body text " * 12)
    path = tmp_path / "plain.docx"
    document.save(path)

    text, _t, status, _n = extract_text(
        path.read_bytes(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "https://example.org/a.docx",
    )
    assert "ordinary paragraph" in text
    assert status is TextStatus.OK


def test_antiword_failure_notes_do_not_carry_the_temporary_path(monkeypatch):
    """Identical bytes must give identical notes (the CCMA re-seed compares them)."""
    import subprocess as sp

    from labour_corpus.parse import office

    monkeypatch.setattr(office, "antiword_path", lambda: "antiword")

    def refuse(args, **kwargs):
        return sp.CompletedProcess(args, 1, b"", f"{args[-1]} is not a Word Document.".encode())

    monkeypatch.setattr(office.subprocess, "run", refuse)
    first = office.extract_doc_text(OLE2_MAGIC).note
    second = office.extract_doc_text(OLE2_MAGIC).note
    assert first == second
    assert "document.doc is not a Word Document" in first
    assert "labour_doc_" not in first

    def hang(args, **kwargs):
        raise sp.TimeoutExpired(args, 30)

    monkeypatch.setattr(office.subprocess, "run", hang)
    timed_out = office.extract_doc_text(OLE2_MAGIC).note
    assert "TimeoutExpired" in timed_out
    assert "labour_doc_" not in timed_out


def test_a_pdf_no_page_of_which_can_be_read_is_not_called_a_scan(monkeypatch):
    """D-011: a reader failure is not evidence of a scan, so OCR is not the remedy."""
    import pypdf

    from labour_corpus.parse.pdf import extract_pdf_text

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)

    def broken(self, *args, **kwargs):
        raise IndexError("corrupt content stream")

    monkeypatch.setattr(pypdf.PageObject, "extract_text", broken)
    result = extract_pdf_text(buffer.getvalue())
    assert result.status is TextStatus.EMPTY
    assert any("reader failure, not evidence of a scan" in n for n in result.notes)
    assert not any("flagged for OCR" in n for n in result.notes)
