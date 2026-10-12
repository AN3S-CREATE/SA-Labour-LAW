"""Idempotency and resume tests. No network access."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from labour_corpus.config import CorpusPaths
from labour_corpus.http import FetchResult
from labour_corpus.models import DocType, Gap, make_doc_id
from labour_corpus.pipeline import build_parsed_doc, persist, store_raw
from labour_corpus.state import CorpusStore

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.saflii.org/za/cases/ZALCJHB/2024/212.html"


def _result(body: bytes, url: str = URL) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status=200,
        headers={"content-type": "text/html", "etag": '"abc123"'},
        body=body,
        content_sha256=hashlib.sha256(body).hexdigest(),
        bytes=len(body),
        elapsed_ms=5,
    )


@pytest.fixture()
def corpus(tmp_path) -> tuple[CorpusPaths, CorpusStore]:
    paths = CorpusPaths(root=tmp_path / "corpus")
    paths.ensure()
    store = CorpusStore(paths.corpus_db)
    yield paths, store
    store.close()


@pytest.fixture(scope="module")
def body() -> bytes:
    return (FIXTURES / "zalcjhb_mining_dismissal.html").read_bytes()


def test_doc_id_is_stable_for_identical_content():
    first = make_doc_id(URL, "sha-value")
    second = make_doc_id(URL, "sha-value")
    assert first == second


def test_doc_id_changes_when_content_changes():
    assert make_doc_id(URL, "sha-a") != make_doc_id(URL, "sha-b")


def test_doc_id_changes_when_url_changes():
    assert make_doc_id("https://a/x", "sha") != make_doc_id("https://b/x", "sha")


def test_storing_twice_produces_one_row_and_one_file(corpus, body):
    paths, store = corpus
    result = _result(body)

    for _ in range(2):
        store_raw(paths, source_id="saflii_zalcjhb", result=result, year=2024)
        doc, text = build_parsed_doc(
            source_id="saflii_zalcjhb", forum="ZALCJHB", result=result,
            default_doc_type=DocType.JUDGMENT, license_note="test",
        )
        persist(paths, store, doc, text)

    assert store.count_documents() == 1
    assert len(list(paths.parsed.glob("*.json"))) == 1
    assert len(list(paths.text.glob("*.txt"))) == 1


def test_unchanged_body_is_detected_for_resume(corpus, body):
    paths, store = corpus
    result = _result(body)
    store.record_fetch(
        URL, source_id="saflii_zalcjhb", status=200,
        content_sha256=result.content_sha256, etag='"abc123"', bytes_=len(body),
    )
    assert store.is_unchanged(URL, result.content_sha256) is True
    assert store.is_unchanged(URL, "a-different-sha") is False


def test_fetch_record_carries_validators_for_conditional_get(corpus, body):
    paths, store = corpus
    store.record_fetch(
        URL, source_id="s", status=200, content_sha256="sha",
        etag='"abc123"', last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
    )
    record = store.get_fetch_record(URL)
    assert record["etag"] == '"abc123"'
    assert record["last_modified"] == "Wed, 01 Jan 2025 00:00:00 GMT"


def test_second_run_after_interruption_skips_unchanged_bodies(corpus, body):
    """Simulates kill-and-resume: the same bytes must not be reprocessed."""
    paths, store = corpus
    result = _result(body)

    raw = store_raw(paths, source_id="s", result=result, year=2024)
    doc, text = build_parsed_doc(
        source_id="s", forum="ZALCJHB", result=result,
        default_doc_type=DocType.JUDGMENT, license_note="",
    )
    persist(paths, store, doc, text)
    store.record_fetch(
        URL, source_id="s", status=200, content_sha256=result.content_sha256,
        doc_id=doc.doc_id, raw_path=str(raw.directory),
    )

    record = store.get_fetch_record(URL)
    would_skip = (
        record["content_sha256"] == result.content_sha256 and record["doc_id"] is not None
    )
    assert would_skip, "an unchanged body must be skipped on the next run"
    assert store.count_documents() == 1


def test_changed_body_creates_a_new_version(corpus, body):
    paths, store = corpus
    first = _result(body)
    store_raw(paths, source_id="s", result=first, year=2024)
    doc_a, text_a = build_parsed_doc(
        source_id="s", forum="ZALCJHB", result=first,
        default_doc_type=DocType.JUDGMENT, license_note="",
    )
    persist(paths, store, doc_a, text_a)

    amended = body.replace(b"procedurally unfair", b"substantively unfair")
    second = _result(amended)
    store_raw(paths, source_id="s", result=second, year=2024)
    doc_b, text_b = build_parsed_doc(
        source_id="s", forum="ZALCJHB", result=second,
        default_doc_type=DocType.JUDGMENT, license_note="",
    )
    persist(paths, store, doc_b, text_b)

    assert doc_a.doc_id != doc_b.doc_id
    assert store.count_documents() == 2


def test_gaps_are_recorded_once(corpus):
    _, store = corpus
    gap = Gap(source_id="saflii_zalcjhb", kind="challenge_blocked",
              url=URL, detail="challenge", observed_at="2026-09-17T00:00:00+00:00")
    store.record_gap(gap)
    store.record_gap(gap)
    assert len(store.all_gaps()) == 1


def test_oversize_body_is_recorded_without_text(corpus):
    paths, store = corpus
    result = FetchResult(
        url=URL, final_url=URL, status=200, headers={"content-type": "application/pdf"},
        body=None, content_sha256="", bytes=99_000_000, elapsed_ms=1, oversize=True,
    )
    doc, text = build_parsed_doc(
        source_id="s", forum="ZALCJHB", result=result,
        default_doc_type=DocType.JUDGMENT, license_note="",
    )
    # ParsedDoc uses use_enum_values=True, so enum fields hold their string value.
    assert doc.text_status == "skipped_oversize"
    assert text == ""
    persist(paths, store, doc, text)
    assert store.count_documents() == 1


def test_raw_layout_matches_the_output_contract(corpus, body):
    paths, store = corpus
    result = _result(body)
    raw = store_raw(paths, source_id="saflii_zalcjhb", result=result, year=2024)

    assert raw.directory.parent.name == "2024"
    assert raw.directory.parent.parent.name == "saflii_zalcjhb"
    assert (raw.directory / "headers.json").exists()
    assert (raw.directory / "fetch_meta.json").exists()
    assert raw.body_path.exists()
    assert raw.body_path.name == "source.html"


# -- durability of the corpus location -------------------------------------
@pytest.mark.parametrize("root,volatile", [
    ("C:/Users/x/AppData/Local/Temp/lc_live2", True),
    ("/tmp/corpus", True),
    ("/var/folders/ab/cd/T/corpus", True),
    ("C:/Windows/Temp/corpus", True),
    ("D:/Work/project/data/corpus", False),
    ("/home/user/corpora/labour", False),
    ("./data/corpus", False),
])
def test_a_temp_directory_corpus_is_flagged(root, volatile):
    """A corpus in %TEMP% is deleted by OS cleanup; warn before that happens.

    This is not hypothetical: an earlier corpus was lost exactly this way,
    including OCR transcriptions that take minutes per document to produce.
    """
    from pathlib import Path

    paths = CorpusPaths(root=Path(root))
    assert paths.is_volatile is volatile


def test_the_volatile_warning_names_the_risk_and_a_fix():
    from pathlib import Path

    paths = CorpusPaths(root=Path("/tmp/corpus"))
    warning = paths.volatile_warning()
    assert "delete" in warning
    assert "./data/corpus" in warning


# -- derived artefacts must not silently describe an older corpus ----------
def test_a_stale_index_is_detected(corpus):
    """A search against an index built before the last write misses documents."""
    import os
    import time

    from labour_corpus.cli import stale_artefact_warning

    paths, store = corpus
    body = (FIXTURES / "zalcjhb_mining_dismissal.html").read_bytes()
    result = _result(body)
    doc, text = build_parsed_doc(
        source_id="s", forum="ZALCJHB", result=result,
        default_doc_type=DocType.JUDGMENT, license_note="",
    )
    persist(paths, store, doc, text)

    # An index file whose mtime predates the document write.
    paths.fts_db.parent.mkdir(parents=True, exist_ok=True)
    paths.fts_db.write_bytes(b"")
    old = time.time() - 86_400
    os.utime(paths.fts_db, (old, old))

    message = stale_artefact_warning(paths.fts_db, store, "search index")
    assert message is not None and "older corpus" in message


def test_a_fresh_index_is_not_flagged(corpus):
    from labour_corpus.cli import stale_artefact_warning

    paths, store = corpus
    body = (FIXTURES / "zalcjhb_mining_dismissal.html").read_bytes()
    result = _result(body)
    doc, text = build_parsed_doc(
        source_id="s", forum="ZALCJHB", result=result,
        default_doc_type=DocType.JUDGMENT, license_note="",
    )
    persist(paths, store, doc, text)

    paths.fts_db.parent.mkdir(parents=True, exist_ok=True)
    paths.fts_db.write_bytes(b"")  # written now, i.e. after the document
    assert stale_artefact_warning(paths.fts_db, store, "search index") is None


def test_staleness_check_is_silent_on_an_empty_corpus(corpus):
    from labour_corpus.cli import stale_artefact_warning

    paths, store = corpus
    assert stale_artefact_warning(paths.fts_db, store, "search index") is None


# -- re-parsing a scan from its stored transcription -------------------------
def test_reparsing_an_ocrd_scan_twice_gives_the_same_document(tmp_path):
    """D-014: reusing ocr.txt keeps `parse` idempotent for scanned documents.

    The second run must rebuild the document from the transcription exactly as
    the first did -- same tags, draft flag and quotes -- not fall back to the
    empty parse of the scan.
    """
    import io

    import orjson
    from pypdf import PdfWriter
    from typer.testing import CliRunner

    from labour_corpus.cli import app
    from labour_corpus.pipeline import save_ocr_text

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    url = "https://example.org/codes/draft-code-for-comment.pdf"
    paths = CorpusPaths(root=tmp_path / "corpus")
    paths.ensure()
    pdf = FetchResult(
        url=url, final_url=url, status=200, headers={"content-type": "application/pdf"},
        body=buffer.getvalue(), content_sha256=hashlib.sha256(buffer.getvalue()).hexdigest(),
        bytes=len(buffer.getvalue()), elapsed_ms=0,
    )
    raw = store_raw(paths, source_id="s", result=pdf, year=2026,
                    doc_type=DocType.CODE_OF_GOOD_PRACTICE, forum="DEL", provenance="crawled")
    save_ocr_text(raw.directory, (
        "DRAFT CODE OF GOOD PRACTICE ON DISMISSAL\n\n"
        "Published for public comment. An employee who is dismissed for misconduct "
        "such as theft or dishonesty must be heard before the employer decides, and "
        "the dismissal must be for a fair reason.\n"
    ))

    def snapshot() -> tuple[dict, list]:
        parsed = orjson.loads((paths.parsed / f"{raw.doc_id}.json").read_bytes())
        parsed.pop("retrieved_at")
        store = CorpusStore(paths.corpus_db)
        try:
            tags = [tuple(r) for r in store.conn.execute(
                "SELECT category, tag FROM tags WHERE doc_id = ? ORDER BY category, tag",
                (raw.doc_id,),
            )]
            assert store.count_documents() == 1
        finally:
            store.close()
        return parsed, tags

    runner = CliRunner()
    assert runner.invoke(app, ["parse", "--out", str(paths.root)]).exit_code == 0
    first = snapshot()
    assert runner.invoke(app, ["parse", "--out", str(paths.root)]).exit_code == 0
    second = snapshot()

    assert first == second
    parsed, tags = first
    assert parsed["ocr_applied"] is True
    assert parsed["is_draft"] is True
    assert tags
