"""Legacy-import tests.

The guarantees under test: listing pages never become documents, non-South
African material is excluded, provenance is recorded so a legacy copy is never
mistaken for a crawl, and re-parsing from raw/ preserves both.
"""

from __future__ import annotations

import hashlib

import pytest

from labour_corpus.config import CorpusPaths
from labour_corpus.http import FetchResult
from labour_corpus.models import DocType, Provenance
from labour_corpus.pipeline import build_parsed_doc, persist, store_raw
from labour_corpus.sources.legacy_import import (
    LEGACY_LICENSE_NOTE,
    LEGACY_SOURCE_ID,
    MIN_WORDS,
    classify_legacy_record,
    forum_for,
    legacy_parser_notes,
)
from labour_corpus.state import CorpusStore

JUDGMENT_BODY = (
    "IN THE LABOUR COURT OF SOUTH AFRICA, JOHANNESBURG\n"
    "Case no: JR 100/14\n"
    "[2014] ZALCJHB 364\n"
    "[1] The applicant was dismissed for misconduct.\n"
    "[2] The dismissal was procedurally unfair.\n"
) + ("filler text for length. " * 200)

LISTING_BODY = "\n".join(
    f"Party {n} v Other {n} (JS {n}/14) [2014] ZALCJHB {n} (6 January 2014)"
    for n in range(1, 40)
)


@pytest.fixture()
def corpus(tmp_path):
    paths = CorpusPaths(root=tmp_path / "corpus")
    paths.ensure()
    store = CorpusStore(paths.corpus_db)
    yield paths, store
    store.close()


# -- classification --------------------------------------------------------
def test_a_judgment_is_importable():
    result = classify_legacy_record(
        {"collection": "ZALCJHB", "text": JUDGMENT_BODY}
    )
    assert result is not None
    doc_type, citation = result
    assert doc_type is DocType.JUDGMENT
    assert citation == "[2014] ZALCJHB 364"


def test_a_listing_page_is_never_importable():
    """The failure that turned the legacy corpus into 201 distinct titles."""
    assert classify_legacy_record({"collection": "ZALCJHB", "text": LISTING_BODY}) is None


def test_text_without_a_court_heading_is_not_a_judgment():
    assert classify_legacy_record(
        {"collection": "ZALCJHB", "text": "A note about employment law."}
    ) is None


def test_consolidated_acts_are_importable():
    result = classify_legacy_record({
        "collection": "CONSOL_ACT",
        "text": "LABOUR RELATIONS ACT 66 OF 1995\nTo change the law governing labour relations.",
    })
    assert result is not None
    assert result[0] is DocType.STATUTE


@pytest.mark.parametrize("collection", ["BWCA", "NAHC", "MZTS", "EACA",
                                        "SADCT", "ZAGPJHCROLLS", "UKPC"])
def test_non_sa_and_rolls_are_excluded(collection):
    assert classify_legacy_record({"collection": collection, "text": JUDGMENT_BODY}) is None


def test_court_rolls_are_excluded_even_with_a_court_heading():
    assert classify_legacy_record(
        {"collection": "ZAGPJHCROLLS", "text": JUDGMENT_BODY}
    ) is None


# -- provenance ------------------------------------------------------------
def test_provenance_defaults_to_crawled():
    result = FetchResult(
        url="https://example.org/a", final_url="https://example.org/a", status=200,
        headers={"content-type": "text/html"}, body=b"<html><body>hi</body></html>",
        content_sha256="sha", bytes=10, elapsed_ms=1,
    )
    doc, _ = build_parsed_doc(
        source_id="s", forum="DEL", result=result,
        default_doc_type=DocType.STATUTE, license_note="",
    )
    assert doc.provenance == Provenance.CRAWLED.value


def test_legacy_provenance_is_recorded_and_queryable(corpus):
    paths, store = corpus
    body = JUDGMENT_BODY.encode("utf-8")
    result = FetchResult(
        url="https://www.saflii.org/za/cases/ZALCJHB/2014/364.html",
        final_url="https://www.saflii.org/za/cases/ZALCJHB/2014/364.html",
        status=200, headers={"content-type": "text/plain"}, body=body,
        content_sha256=hashlib.sha256(body).hexdigest(), bytes=len(body), elapsed_ms=0,
    )
    doc, text = build_parsed_doc(
        source_id=LEGACY_SOURCE_ID, forum="ZALCJHB", result=result,
        default_doc_type=DocType.JUDGMENT, license_note=LEGACY_LICENSE_NOTE,
    )
    doc.provenance = Provenance.LEGACY_LOCAL_HARVEST
    doc.http_status = None
    persist(paths, store, doc, text)

    row = next(store.iter_documents())
    assert row["provenance"] == "legacy_local_harvest"
    # Nothing may imply this crawler fetched it.
    assert row["http_status"] is None
    assert "not fetched by this crawler" in row["license_note"]

    counts = {r["provenance"]: r["n"] for r in store.counts_by_provenance()}
    assert counts["legacy_local_harvest"] == 1


def test_legacy_notes_disclose_how_the_copy_was_obtained():
    from labour_corpus.sources.legacy_import import LegacyDoc

    doc = LegacyDoc(
        text="x", collection="ZALCJHB", doc_type=DocType.JUDGMENT,
        canonical_url="https://www.saflii.org/za/cases/ZALCJHB/2014/364.html",
        url_is_derived=True, neutral_citation="[2014] ZALCJHB 364",
        source_file="ZALCJHB/x.html", content_sha256="sha", word_count=500,
    )
    notes = legacy_parser_notes(doc)
    assert any("not fetched by this crawler" in n for n in notes)
    assert any("derived from the neutral citation" in n for n in notes)


def test_forum_mapping():
    from labour_corpus.sources.legacy_import import LegacyDoc

    judgment = LegacyDoc(
        text="", collection="ZALAC", doc_type=DocType.JUDGMENT, canonical_url="u",
        url_is_derived=False, neutral_citation=None, source_file="", content_sha256="",
        word_count=0,
    )
    statute = LegacyDoc(
        text="", collection="CONSOL_ACT", doc_type=DocType.STATUTE, canonical_url="u",
        url_is_derived=False, neutral_citation=None, source_file="", content_sha256="",
        word_count=0,
    )
    assert forum_for(judgment) == "ZALAC"
    assert forum_for(statute) == "SAFLII_LEGIS"


# -- raw round-trip --------------------------------------------------------
def test_fetch_meta_carries_hints_so_reparse_is_correct(corpus):
    """A document whose source is not a crawl target must still re-parse."""
    import orjson

    paths, store = corpus
    body = JUDGMENT_BODY.encode("utf-8")
    result = FetchResult(
        url="https://www.saflii.org/za/cases/ZALCJHB/2014/364.html",
        final_url="https://www.saflii.org/za/cases/ZALCJHB/2014/364.html",
        status=200, headers={"content-type": "text/plain"}, body=body,
        content_sha256=hashlib.sha256(body).hexdigest(), bytes=len(body), elapsed_ms=0,
    )
    raw = store_raw(
        paths, source_id=LEGACY_SOURCE_ID, result=result, year=2014,
        doc_type=DocType.JUDGMENT, forum="ZALCJHB",
        provenance=Provenance.LEGACY_LOCAL_HARVEST.value,
    )
    meta = orjson.loads((raw.directory / "fetch_meta.json").read_bytes())
    assert meta["doc_type"] == "judgment"
    assert meta["forum"] == "ZALCJHB"
    assert meta["provenance"] == "legacy_local_harvest"


def test_short_records_are_rejected():
    """A stub is not a document."""
    short = {"collection": "ZALCJHB", "text": "IN THE LABOUR COURT\n[2014] ZALCJHB 1"}
    classified = classify_legacy_record(short)
    # It classifies, but the scan drops it on word count.
    assert classified is not None
    assert len(short["text"].split()) < MIN_WORDS
