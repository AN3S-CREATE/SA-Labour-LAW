"""Ontology coverage, coverage-report content and inbox ingestion tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from labour_corpus.config import CorpusPaths
from labour_corpus.http import FetchResult
from labour_corpus.models import DocType, Outcome
from labour_corpus.observability import RunLog
from labour_corpus.ontology.dismissal import ALL_TAGS, OntologySchema
from labour_corpus.ontology.mining import ALL_MINING_TAGS, overlay_to_dict
from labour_corpus.ontology.tagger import tag_document
from labour_corpus.pipeline import build_parsed_doc, persist
from labour_corpus.report.coverage import (
    CCMA_INCOMPLETENESS_STATEMENT,
    build_coverage_markdown,
)
from labour_corpus.sources.inbox import ingest_inbox
from labour_corpus.sources.registry import load_registry
from labour_corpus.state import CorpusStore

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "sources.yaml"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def corpus(tmp_path):
    paths = CorpusPaths(root=tmp_path / "corpus")
    paths.ensure()
    store = CorpusStore(paths.corpus_db)
    yield paths, store
    store.close()


# -- ontology completeness -------------------------------------------------
@pytest.mark.parametrize("required", [
    "s186_dismissal_definition",
    "s186_1_e_constructive",
    "s186_2_unfair_labour_practice",
    "s187_automatically_unfair",
    "s188_fair_reason_and_procedure",
    "s189_operational_requirements",
    "s189A_large_scale_retrenchment",
    "s191_referral_and_condonation",
    "s145_review",
    "s198_tes_labour_broker",
])
def test_required_lra_sections_are_in_the_ontology(required):
    """Acceptance criterion 8: s186/s187/s188/s189/s145 must be present."""
    assert required in {tag.name for tag in ALL_TAGS}


def test_fairness_is_stored_as_a_separate_split():
    names = {tag.name for tag in ALL_TAGS}
    for required in ("substantive_fairness", "procedural_fairness", "automatically_unfair"):
        assert required in names


def test_process_defect_layer_is_employee_facing():
    defects = {t.name for t in ALL_TAGS if t.category == "process_defect"}
    for required in (
        "defective_charge_sheet", "chairperson_not_impartial",
        "representation_denied", "predetermined_outcome", "inconsistency",
        "no_progressive_discipline", "paper_consultation_s189",
        "selection_criteria_unfair", "alternatives_not_considered",
        "forced_resignation", "condonation_late_referral",
        "review_test_confusion",
    ):
        assert required in defects


def test_mining_overlay_covers_mhsa_sections():
    names = {tag.name for tag in ALL_MINING_TAGS}
    assert "mhsa" in names
    assert "mhsa_s54_stoppage" in names
    assert "mhsa_s23_right_to_refuse" in names


def test_ontology_serialises_with_a_disclaimer():
    data = OntologySchema().to_dict()
    assert data["tags"]
    assert "not legal advice" in data["disclaimer"]
    assert data["jurisdiction"] == "ZA"


def test_mining_overlay_serialises_with_its_decision_rule():
    data = overlay_to_dict()
    assert data["sector_decision_rule"]["sector_tag"] == "mining"
    assert data["sector_decision_rule"]["min_distinct_tags"] >= 2


def test_single_mining_mention_does_not_flip_the_sector():
    """One passing use of the word 'mine' must not tag a document as mining."""
    result = tag_document(
        "The applicant was dismissed for misconduct. Counsel referred to a mine "
        "in passing during argument, but this is a retail matter throughout and "
        "concerns a till shortage at a supermarket branch."
    )
    assert "mining" not in result.sector_tags


def test_automatically_unfair_records_an_audit_reason():
    result = tag_document(
        "[1] The dismissal was automatically unfair in terms of section 187(1)(d) "
        "because the employee participated in a protected strike."
    )
    decisions = [e for e in result.evidence if e.category == "outcome_decision"]
    assert decisions
    assert "verify against s187" in decisions[0].reason


def test_outcome_uses_final_order_not_argument_summary():
    result = tag_document(
        "[4] The employee submitted that the dismissal was substantively unfair.\n"
        "[18] The employer said it was fair.\n\nORDER\n"
        "[31] The dismissal was substantively and procedurally fair."
    )
    assert result.outcome is Outcome.FAIR


# -- paragraph evidence: numeric order, real markers only (D-005) -------------
def _decision(result, category: str):
    rows = [e for e in result.evidence if e.category == category]
    assert len(rows) == 1
    return rows[0]


def test_sector_decision_cites_paragraphs_in_numeric_order():
    """As strings, '10' and '101' sorted before '2'."""
    result = tag_document(
        "[2] The employee worked underground at the shaft.\n"
        "[10] He was a rock drill operator on a platinum operation.\n"
        "[101] The Mine Health and Safety Act applied to the workings.\n"
    )
    assert "mining" in result.sector_tags
    assert _decision(result, "sector_decision").paragraphs == ["2", "10", "101"]


def test_sector_decision_keeps_the_lowest_paragraphs_when_capped():
    lines = [f"[{n}] Work continued underground near the shaft." for n in range(2, 10)]
    lines += ["[10] The employee was a rock drill operator.",
              "[101] The Mine Health and Safety Act applied."]
    result = tag_document("\n".join(lines) + "\n")
    cited = _decision(result, "sector_decision").paragraphs
    assert cited == ["2", "3", "4", "5", "6", "7", "8", "9"]
    assert "101" not in cited


def test_automatically_unfair_decision_cites_paragraphs_in_numeric_order():
    result = tag_document(
        "[2] The dismissal was automatically unfair.\n"
        "[10] Section 187 lists the prohibited grounds.\n"
        "[101] The dismissal is automatically unfair and compensation follows.\n"
    )
    assert _decision(result, "outcome_decision").paragraphs == ["2", "10", "101"]


def test_citation_lines_and_wrapped_years_are_not_paragraph_markers():
    """'[2014] ZALAC 49' is a citation and '1995.' a wrapped year, not para 2014/1995."""
    from labour_corpus.ontology.tagger import ParagraphMap

    text = "[2014] ZALAC 49\n[1] The applicant was dismissed under Act 66 of\n1995. He said"
    assert ParagraphMap.build(text).labels == ["1"]


@pytest.mark.parametrize("citation", [
    "[2014] ZALAC 49",
    "[2016] ZACC 14",
    "[2008] ZASCA 7",
    "[2019] ZALCJHB 212",
    "[1998] 19 ILJ 1234 (LAC)",
    "[2001] 2 BLLR 105 (LAC)",
    "[1999] 1 All SA 1 (A)",
    "[1986] 1 S.C.R. 103",
    "[2004] UKHL 22",
    "[2009] EWCA Civ 1046",
    "[2012] JOL 28856 (LC)",
    "[2010] ZACC99.the Constitution",
    "[2005]\n26 ILJ 1519 (LAC)",
])
def test_a_bracketed_year_before_a_court_code_or_report_series_is_a_citation(citation):
    from labour_corpus.ontology.tagger import ParagraphMap

    text = f"[1] The applicant relied on\n{citation}\n[2] The court agreed.\n"
    assert ParagraphMap.build(text).labels == ["1", "2"]


def test_real_paragraph_markers_are_kept():
    from labour_corpus.ontology.tagger import ParagraphMap

    text = (
        "[1] Introduction.\n[12] ZALAC is not cited here; [12] is a paragraph.\n"
        "13. A numbered clause.\nPara 14 explicit.\n[999] A long judgment.\n"
    )
    assert ParagraphMap.build(text).labels == ["1", "12", "13", "14", "999"]


def test_cited_paragraphs_never_include_a_citation_year():
    result = tag_document(
        "[2014] ZALAC 49\n"
        "[1] The employee worked underground at the shaft of a platinum operation.\n"
        "[1998] 19 ILJ 1234 (LAC)\n"
        "The rock drill operator was dismissed under the Mine Health and Safety Act.\n"
    )
    for evidence in result.evidence:
        assert set(evidence.paragraphs) <= {"1"}


# -- coverage report -------------------------------------------------------
def test_coverage_contains_the_ccma_incompleteness_statement(corpus):
    """Acceptance criterion 7."""
    paths, store = corpus
    registry = load_registry(REGISTRY_PATH)
    markdown = build_coverage_markdown(
        paths, store, registry, years=(2011, 2026), contact_email="ops@example.test"
    )
    assert CCMA_INCOMPLETENESS_STATEMENT in markdown


def test_coverage_explains_blocked_sources_and_lawful_upgrades(corpus):
    paths, store = corpus
    registry = load_registry(REGISTRY_PATH)
    markdown = build_coverage_markdown(
        paths, store, registry, years=(2011, 2026), contact_email="ops@example.test"
    )
    assert "SAFLII" in markdown
    assert "Cloudflare managed challenge" in markdown
    assert "ai-input" in markdown
    assert "Lawful ways to close these gaps" in markdown
    assert "inbox" in markdown
    assert "not a legal practitioner" in markdown


def test_coverage_never_claims_completeness(corpus):
    """The report may discuss the phrase 'all CCMA cases' only to deny it."""
    import re

    paths, store = corpus
    registry = load_registry(REGISTRY_PATH)
    markdown = build_coverage_markdown(
        paths, store, registry, years=(2011, 2026), contact_email="ops@example.test"
    ).lower()

    # Affirmative completeness claims, as opposed to denials of them.
    affirmative_claims = [
        r"(?<!not )contains all ccma",
        r"(?<!not )includes every ccma",
        r"complete (?:record|archive) of ccma",
        r"all ccma awards are included",
        r"this corpus (?:has|holds) all",
    ]
    for pattern in affirmative_claims:
        assert not re.search(pattern, markdown), f"affirmative claim matched: {pattern}"

    # And the denial itself must be present.
    assert "cannot truthfully claim completeness" in markdown
    assert re.search(r"must (?:therefore )?not state or imply", markdown)


# -- inbox -----------------------------------------------------------------
def test_inbox_ingests_operator_documents_with_honest_provenance(corpus, tmp_path):
    paths, store = corpus
    log = RunLog(paths.run_log, paths.error_log)

    award = paths.inbox / "my_ccma_award.html"
    award.write_text(
        "<html><title>Arbitration award</title><body>"
        "<p>[1] The applicant was dismissed for misconduct on a mine.</p>"
        "<p>[2] I find the dismissal was substantively unfair.</p>"
        "</body></html>",
        encoding="utf-8",
    )

    outcome = ingest_inbox(paths, store, log)
    assert outcome.stored == 1
    assert store.count_documents() == 1

    row = next(store.iter_documents())
    assert row["source_id"] == "operator_inbox"
    assert row["canonical_url"].startswith("file://")
    assert "Operator-supplied" in row["license_note"]
    assert row["doc_type"] == DocType.AWARD.value


def test_inbox_is_idempotent(corpus):
    paths, store = corpus
    log = RunLog(paths.run_log, paths.error_log)
    (paths.inbox / "award.txt").write_text("An arbitration award.", encoding="utf-8")

    first = ingest_inbox(paths, store, log)
    second = ingest_inbox(paths, store, log)
    assert first.stored == 1
    assert second.stored == 0
    assert second.skipped_duplicate == 1
    assert store.count_documents() == 1


def test_inbox_reports_when_empty(corpus):
    paths, store = corpus
    log = RunLog(paths.run_log, paths.error_log)
    outcome = ingest_inbox(paths, store, log)
    assert outcome.stored == 0
    assert any("no supported files" in note for note in outcome.notes)


# -- full-text search ------------------------------------------------------
def test_search_returns_only_documents_on_disk(corpus):
    from labour_corpus.index.fts import FtsIndex

    paths, store = corpus
    body = (FIXTURES / "zalcjhb_mining_dismissal.html").read_bytes()
    result = FetchResult(
        url="https://example.org/j/1", final_url="https://example.org/j/1",
        status=200, headers={"content-type": "text/html"}, body=body,
        content_sha256=hashlib.sha256(body).hexdigest(), bytes=len(body), elapsed_ms=1,
    )
    doc, text = build_parsed_doc(
        source_id="s", forum="ZALCJHB", result=result,
        default_doc_type=DocType.JUDGMENT, license_note="",
    )
    persist(paths, store, doc, text)

    with FtsIndex(paths.fts_db) as fts:
        fts.rebuild(paths, store)
        hits = fts.search("safety rule mine", store)
        assert hits
        assert hits[0].url == "https://example.org/j/1"

        assert fts.search("zzzznonexistentterm", store) == []


@pytest.mark.parametrize("filename, expected", [
    ("GAJB1234-24 unfair labour practice award.pdf", "award"),
    ("unfair_labour_practice_award.pdf", "award"),
    ("award - breach of contract.txt", "award"),
    ("fixed-term contract worker.txt", "award"),        # default, not a statute
    ("arbitration GAJB5678-24 (collective agreement dispute).pdf", "award"),
    ("labour_relations_act_66_1995.pdf", "statute"),
    ("LRA Act 66 of 1995.pdf", "statute"),
    ("main collective agreement 2025.pdf", "bargaining_council_agreement"),
    ("ccma rules 2024.pdf", "ccma_rule"),
    ("code of good practice dismissal.pdf", "code_of_good_practice"),
    ("judgment JR1487-21.pdf", "judgment"),
])
def test_inbox_filename_guess_does_not_read_act_inside_other_words(filename, expected):
    """'act' is inside 'practice' and 'contract': those awards were typed statutes."""
    from pathlib import Path

    from labour_corpus.sources.inbox import _doc_type_for

    assert _doc_type_for(Path(filename)).value == expected
