"""Citation-map import tests.

The map records what case law is known to EXIST. The guarantees under test are
that a reference never becomes a document, that non-South African material is
excluded, and that the held counter actually moves when the corpus acquires a
judgment.
"""

from __future__ import annotations

import pytest

from labour_corpus.config import CorpusPaths
from labour_corpus.models import DocType, ParsedDoc, make_doc_id, utc_now_iso
from labour_corpus.sources.citation_map import (
    SA_LABOUR_COLLECTIONS,
    is_excluded_collection,
    is_south_african,
    parse_listing_line,
    parse_listing_text,
)
from labour_corpus.state import CorpusStore

# Real lines from a SAFLII ZALCJHB year index.
LISTING = """2014 South Africa: Johannesburg Labour Court, Johannesburg Decisions
You are here:
SAFLII
>>
Databases
>>
January 2014
Makhanye v South African Airways (JS 265/13) [2014] ZALCJHB 6 (6 January 2014)
Tadyn Trading CC t/a Tadyn Consulting Services v Steiner and Others (J 2845/13) [2014] ZALCJHB 7; (2014) 35 ILJ 1672 (LC) (7 January 2014)
BIFAWU obo Members v Zurich Insurance Co SA (J175/12) [2014] ZALCJHB 18 (7 January 2014)
"""


@pytest.fixture()
def store(tmp_path):
    paths = CorpusPaths(root=tmp_path / "corpus")
    paths.ensure()
    s = CorpusStore(paths.corpus_db)
    yield s
    s.close()


# -- parsing ---------------------------------------------------------------
def test_parses_a_listing_line_verbatim():
    record = parse_listing_line(
        "Makhanye v South African Airways (JS 265/13) [2014] ZALCJHB 6 (6 January 2014)"
    )
    assert record is not None
    assert record.neutral_citation == "[2014] ZALCJHB 6"
    assert record.court == "ZALCJHB"
    assert record.year == 2014
    assert record.case_no == "JS 265/13"
    assert record.case_name == "Makhanye v South African Airways"
    assert record.date_delivered == "2014-01-06"


def test_captures_parallel_report_citations():
    record = parse_listing_line(
        "Tadyn Trading CC v Steiner and Others (J 2845/13) [2014] ZALCJHB 7; "
        "(2014) 35 ILJ 1672 (LC) (7 January 2014)"
    )
    assert record is not None
    assert record.parallel_citations == ["(2014) 35 ILJ 1672 (LC)"]


def test_handles_a_line_without_a_case_number():
    record = parse_listing_line("Some Party v Another [2015] ZALAC 31 (3 March 2015)")
    assert record is not None
    assert record.case_no is None
    assert record.neutral_citation == "[2015] ZALAC 31"


@pytest.mark.parametrize("noise", [
    "You are here:",
    "January 2014",
    "SAFLII",
    "Databases",
    "South Africa: Johannesburg Labour Court, Johannesburg",
    ">>",
    "",
])
def test_navigation_furniture_is_not_a_citation(noise):
    assert parse_listing_line(noise) is None


def test_parses_a_whole_listing_page():
    records = parse_listing_text(LISTING, source_note="fixture")
    assert len(records) == 3
    assert {r.neutral_citation for r in records} == {
        "[2014] ZALCJHB 6", "[2014] ZALCJHB 7", "[2014] ZALCJHB 18",
    }
    assert all(r.source_note == "fixture" for r in records)


# -- jurisdiction scope ----------------------------------------------------
@pytest.mark.parametrize("line", [
    "Botswana matter (X) [2014] BWCA 12 (1 May 2014)",
    "Namibian matter (Y) [2014] NAHC 3 (1 May 2014)",
    "Tribunal matter (Z) [2014] SADCT 1 (1 May 2014)",
])
def test_non_south_african_citations_are_dropped(line):
    assert parse_listing_line(line) is None


def test_court_rolls_are_dropped():
    """A roll lists hearings, not decisions, and carries no judgment text."""
    assert parse_listing_line("Matter (Y) [2020] ZAGPJHCROLLS 3 (1 May 2020)") is None
    assert is_south_african("ZAGPJHCROLLS") is False
    assert is_south_african("ZALCJHB") is True


@pytest.mark.parametrize("collection", ["BWCA", "NAHC", "MZTS", "EACA", "SADCT",
                                        "ZAGPJHCROLLS", "UKPC"])
def test_excluded_collections(collection):
    assert is_excluded_collection(collection) is True


@pytest.mark.parametrize("collection", sorted(SA_LABOUR_COLLECTIONS))
def test_sa_labour_collections_are_not_excluded(collection):
    assert is_excluded_collection(collection) is False


# -- the derived URL is labelled as derived --------------------------------
def test_url_is_constructed_from_the_citation_and_flagged(store):
    record = parse_listing_line(
        "Makhanye v South African Airways (JS 265/13) [2014] ZALCJHB 6 (6 January 2014)"
    )
    assert record.saflii_url == "https://www.saflii.org/za/cases/ZALCJHB/2014/6.html"

    store.upsert_known_citation(
        neutral_citation=record.neutral_citation, court=record.court,
        year=record.year, number=record.number, case_name=record.case_name,
        case_no=record.case_no, date_delivered=record.date_delivered,
        parallel_citations=record.parallel_citations,
        saflii_url=record.saflii_url, source_note="test",
    )
    store.conn.commit()
    row = store.conn.execute(
        "SELECT url_is_derived FROM known_citations WHERE neutral_citation = ?",
        (record.neutral_citation,),
    ).fetchone()
    assert row["url_is_derived"] == 1


# -- a reference is not a document -----------------------------------------
def test_importing_references_creates_no_documents(store):
    for record in parse_listing_text(LISTING):
        store.upsert_known_citation(
            neutral_citation=record.neutral_citation, court=record.court,
            year=record.year, number=record.number, case_name=record.case_name,
            case_no=record.case_no, date_delivered=record.date_delivered,
            parallel_citations=record.parallel_citations,
            saflii_url=record.saflii_url, source_note="test",
        )
    store.conn.commit()
    assert store.count_known_citations() == 3
    assert store.count_documents() == 0, "a reference must never become a document"
    assert store.count_held_citations() == 0


def test_import_is_idempotent(store):
    for _ in range(2):
        for record in parse_listing_text(LISTING):
            store.upsert_known_citation(
                neutral_citation=record.neutral_citation, court=record.court,
                year=record.year, number=record.number, case_name=record.case_name,
                case_no=record.case_no, date_delivered=record.date_delivered,
                parallel_citations=record.parallel_citations,
                saflii_url=record.saflii_url, source_note="test",
            )
    store.conn.commit()
    assert store.count_known_citations() == 3


# -- the held counter must actually move -----------------------------------
def test_held_counter_moves_when_a_judgment_is_acquired(store):
    for record in parse_listing_text(LISTING):
        store.upsert_known_citation(
            neutral_citation=record.neutral_citation, court=record.court,
            year=record.year, number=record.number, case_name=record.case_name,
            case_no=record.case_no, date_delivered=record.date_delivered,
            parallel_citations=record.parallel_citations,
            saflii_url=record.saflii_url, source_note="test",
        )
    store.conn.commit()
    assert store.link_held_citations() == 0

    doc = ParsedDoc(
        doc_id=make_doc_id("https://example.org/j/6", "sha"),
        source_id="operator_inbox", canonical_url="https://example.org/j/6",
        retrieved_at=utc_now_iso(), content_sha256="sha",
        doc_type=DocType.JUDGMENT, forum="ZALCJHB",
        neutral_citation="[2014] ZALCJHB 6",
    )
    store.upsert_document(doc)

    assert store.link_held_citations() == 1
    row = store.conn.execute(
        "SELECT held_doc_id FROM known_citations WHERE neutral_citation = ?",
        ("[2014] ZALCJHB 6",),
    ).fetchone()
    assert row["held_doc_id"] == doc.doc_id

    coverage = {r["court"]: (r["known"], r["held"]) for r in store.known_citation_coverage()}
    assert coverage["ZALCJHB"] == (3, 1)


def _neutral_lookup_plan(store: CorpusStore) -> str:
    return " ".join(
        str(row["detail"])
        for row in store.conn.execute(
            "EXPLAIN QUERY PLAN SELECT 1 FROM documents WHERE neutral_citation = ?",
            ("[2014] ZALCJHB 6",),
        )
    )


def test_held_citation_lookup_uses_the_neutral_citation_index(store):
    """link_held_citations matches on documents.neutral_citation once per known
    citation; without an index each match scans every held document."""
    assert "idx_documents_neutral" in _neutral_lookup_plan(store)


def test_neutral_citation_index_is_added_to_an_existing_corpus(store):
    """A corpus built before the index existed gains it on the next open."""
    store.conn.execute("DROP INDEX idx_documents_neutral")
    store.conn.commit()
    assert "idx_documents_neutral" not in _neutral_lookup_plan(store)

    reopened = CorpusStore(store.db_path)
    try:
        assert "idx_documents_neutral" in _neutral_lookup_plan(reopened)
    finally:
        reopened.close()


def test_coverage_report_states_measured_coverage(store, tmp_path):
    from labour_corpus.report.coverage import build_coverage_markdown
    from labour_corpus.sources.registry import load_registry
    from pathlib import Path

    for record in parse_listing_text(LISTING):
        store.upsert_known_citation(
            neutral_citation=record.neutral_citation, court=record.court,
            year=record.year, number=record.number, case_name=record.case_name,
            case_no=record.case_no, date_delivered=record.date_delivered,
            parallel_citations=record.parallel_citations,
            saflii_url=record.saflii_url, source_note="test",
        )
    store.conn.commit()
    store.link_held_citations()

    registry = load_registry(Path(__file__).resolve().parents[1] / "sources.yaml")
    paths = CorpusPaths(root=tmp_path / "corpus")
    markdown = build_coverage_markdown(
        paths, store, registry, years=(2011, 2026), contact_email="ops@example.test"
    )
    assert "holds 0 of 3 labour judgments known to exist" in markdown
    assert "a floor, not a census" in markdown
