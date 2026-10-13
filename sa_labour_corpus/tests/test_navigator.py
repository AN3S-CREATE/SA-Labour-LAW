"""Dismissal Navigator: rules, deadlines, grounding, evidence, rendering, CLI.

Statute text here is built from the provisions' own quotes, re-wrapped and
given typographic apostrophes, so the tests exercise the same normalisation
the live corpus needs. One test grounds every provision against the real
corpus and is skipped when no corpus is present.
"""

from __future__ import annotations

import inspect
import io
import json
import logging
import re
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from labour_corpus.cli import app
from labour_corpus.config import CorpusPaths
from labour_corpus.index.fts import FtsIndex
from labour_corpus.navigator import cli as cli_mod
from labour_corpus.navigator import engine as engine_mod
from labour_corpus.navigator import log as navlog
from labour_corpus.navigator.deadlines import (
    WEEKEND_NOTE, add_months, make_deadline, pending_deadline,
)
from labour_corpus.navigator.engine import DISMISSAL_CODE_WARNING, Navigator
from labour_corpus.navigator.evidence import CorpusEvidence, LATE_QUERY, issues_for
from labour_corpus.navigator.grounding import (
    CorpusSource, InMemorySource, TextCache, corpus_source, ground, normalise,
)
from labour_corpus.navigator.models import (
    AutoUnfairGround, CaseFacts, CouncilStatus, DeadlineStatus, DisputeKind,
    EvidenceHit, Grounding, ProvisionRef, ReasonGiven,
)
from labour_corpus.navigator.provisions import EEA, LRA, PROVISIONS, get
from labour_corpus.navigator.render import to_markdown
from labour_corpus.navigator.routing import Track, checklist, classify, conciliation_forum
from labour_corpus.state import CorpusStore

TODAY = date(2026, 10, 7)
REAL_CORPUS = Path(__file__).resolve().parents[1] / "data" / "corpus"


# SAFLII's heading, which identifies a held copy as the principal Act (D-024).
SAFLII_HEADING = {LRA: "Labour Relations Act 1995\nSouth Africa: Consolidated Acts\n\n",
                  EEA: "Employment Equity Act 1998\nSouth Africa: Consolidated Acts\n\n"}


def statute_text(act: str, drop: str | tuple[str, ...] = (), heading: bool = True) -> str:
    """Wrap each quote across lines with curly apostrophes, as PDFs extract.

    The text opens with SAFLII's heading, so a corpus copy built from it
    identifies itself as the Act, as the held consolidations do.
    """
    parts = []
    for p in PROVISIONS.values():
        if p.act != act or p.key in ((drop,) if isinstance(drop, str) else drop):
            continue
        words = p.quote.replace("'", "’").split()
        parts.append("(1) " + "\n".join(" ".join(words[i:i + 6])
                                        for i in range(0, len(words), 6)))
    return (SAFLII_HEADING[act] if heading else "") + "\n\n".join(parts)


def full_source(drop: str | tuple[str, ...] = ()) -> InMemorySource:
    src = InMemorySource()
    src.add(LRA, "lra-current", statute_text(LRA, drop), "https://example.test/lra")
    src.add(EEA, "eea-current", statute_text(EEA, drop))
    return src


def facts(**kw) -> CaseFacts:
    kw.setdefault("event_date", date(2026, 9, 1))
    return CaseFacts(**kw)


class FakeEvidence:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def find(self, issue: str, query: str, limit: int) -> list[EvidenceHit]:
        self.calls.append(issue)
        return [EvidenceHit(issue=issue, doc_id="j1", doc_type="judgment",
                            title="[2011] ZALCCT 10", url="legacy:x", snippet="...")]


# -- models -------------------------------------------------------------------

@pytest.mark.parametrize("kw", [
    dict(reason=ReasonGiven.AUTOMATICALLY_UNFAIR),
    dict(reason=ReasonGiven.CONDUCT, auto_unfair_ground=AutoUnfairGround.PREGNANCY),
    dict(final_decision_date=date(2026, 8, 1)),
    dict(dispute=DisputeKind.UNFAIR_LABOUR_PRACTICE, reason=ReasonGiven.CONDUCT),
])
def test_facts_reject_inconsistent_input(kw):
    with pytest.raises(ValidationError):
        facts(**kw)


def test_facts_forbid_unknown_fields_such_as_personal_details():
    with pytest.raises(ValidationError):
        CaseFacts.model_validate({"event_date": "2026-09-01", "id_number": "x"})


# -- deadlines ----------------------------------------------------------------

def test_add_months_clips_to_month_end_and_rolls_year():
    assert add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert add_months(date(2026, 1, 15), 6) == date(2026, 7, 15)


def test_deadline_status_bands_and_weekend_note():
    ref = ProvisionRef(key="k", cite="c", quote="q")
    assert make_deadline("s", date(2026, 10, 20), TODAY, ref).status is DeadlineStatus.OPEN
    soon = make_deadline("s", date(2026, 10, 14), TODAY, ref)
    assert soon.status is DeadlineStatus.DUE_SOON and soon.days_left == 7
    assert make_deadline("s", date(2026, 10, 6), TODAY, ref).status is DeadlineStatus.EXPIRED
    weekend = make_deadline("s", date(2026, 10, 10), TODAY, ref, note="n")  # a Saturday
    assert weekend.note == f"n {WEEKEND_NOTE}"
    pending = pending_deadline("s", ref, "later")
    assert pending.status is DeadlineStatus.NOT_STARTED and pending.due is None


# -- routing ------------------------------------------------------------------

@pytest.mark.parametrize("kw,track,basis", [
    (dict(reason=ReasonGiven.CONDUCT), Track.ARBITRATION, ("lra_188_1",)),
    (dict(reason=ReasonGiven.CAPACITY), Track.ARBITRATION, ("lra_188_1",)),
    (dict(reason=ReasonGiven.UNKNOWN), Track.ARBITRATION, ("lra_191_5_a",)),
    (dict(reason=ReasonGiven.CONSTRUCTIVE), Track.ARBITRATION, ("lra_186_1_e",)),
    (dict(reason=ReasonGiven.CONSTRUCTIVE, auto_unfair_ground=AutoUnfairGround.PREGNANCY),
     Track.LABOUR_COURT, ("lra_186_1_e", "lra_187_1")),
    (dict(reason=ReasonGiven.AUTOMATICALLY_UNFAIR,
          auto_unfair_ground=AutoUnfairGround.PROTECTED_DISCLOSURE),
     Track.LABOUR_COURT, ("lra_187_1",)),
    (dict(reason=ReasonGiven.UNPROTECTED_STRIKE), Track.LABOUR_COURT, ("lra_191_5_b",)),
    (dict(reason=ReasonGiven.CLOSED_SHOP), Track.LABOUR_COURT, ("lra_191_5_b",)),
    (dict(dispute=DisputeKind.UNFAIR_LABOUR_PRACTICE), Track.ARBITRATION, ("lra_186_2",)),
    (dict(dispute=DisputeKind.DISCRIMINATION_NOT_DISMISSAL), Track.EEA_LABOUR_COURT,
     ("eea_10_2",)),
])
def test_classification_tracks_follow_s191(kw, track, basis):
    cls = classify(facts(**kw))
    assert (cls.track, cls.basis) == (track, basis)
    assert all(key in PROVISIONS for key in cls.basis)


def test_operational_requirements_election_and_its_prompt():
    ops = ReasonGiven.OPERATIONAL_REQUIREMENTS
    assert classify(facts(reason=ops, employer_has_fewer_than_10_employees=True)).track \
        == Track.ELECTION
    assert classify(facts(reason=ops, only_this_employee_retrenched=True)).track \
        == Track.ELECTION
    unknown = classify(facts(reason=ops))
    assert unknown.track == Track.LABOUR_COURT and "s191(12)" in unknown.notes[0]
    known = classify(facts(reason=ops, employer_has_fewer_than_10_employees=False,
                           only_this_employee_retrenched=False))
    assert known.track == Track.LABOUR_COURT and known.notes == ()


def test_conciliation_forum():
    assert conciliation_forum(facts(council=CouncilStatus.NO)) == "CCMA"
    assert conciliation_forum(facts(council=CouncilStatus.YES, council_name="MIBCO")) == "MIBCO"
    assert "bargaining council" in conciliation_forum(facts(council=CouncilStatus.YES))
    assert "otherwise the CCMA" in conciliation_forum(facts())
    assert conciliation_forum(facts(dispute=DisputeKind.DISCRIMINATION_NOT_DISMISSAL,
                                    council=CouncilStatus.YES)) == "CCMA"


def test_checklist_comes_from_the_ontology():
    conduct = checklist(facts(reason=ReasonGiven.CONDUCT), late=False)
    assert "No hearing was held at all." in conduct
    assert checklist(facts(dispute=DisputeKind.UNFAIR_LABOUR_PRACTICE), late=False) == []
    assert checklist(facts(dispute=DisputeKind.UNFAIR_LABOUR_PRACTICE), late=True) == \
        ["Condonation or late-referral issue."]


# -- grounding ----------------------------------------------------------------

def test_normalise_folds_typography_and_wrapping():
    assert normalise("12 months’\n  Remuneration*") == "12 months' remuneration"


def test_normalise_matches_the_translate_and_regex_formula_it_replaced():
    old_map = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"',
                             "—": "-", "–": "-", "*": " ", " ": " "})
    every_space = "".join(chr(c) for c in range(0x110000) if chr(c).isspace())
    text = (f"{every_space} ’‘“”—–*  Section 191(1)(b)\t30 DAYS{every_space}"
            f"ΔΗΜΟΣ ΙΣΙΖΟΥΛΟΥ ünïcödé*{every_space}end of {every_space}")
    assert normalise(text) == re.sub(r"\s+", " ", text.translate(old_map)).strip().lower()


def test_ground_verified_partial_missing_and_unchecked():
    src = full_source()
    src.add(LRA, "lra-old", "nothing relevant")
    ref = ground(get("lra_191_1_b_i"), src)
    assert ref.grounding is Grounding.VERIFIED and ref.doc_id == "lra-current"
    assert (ref.copies_checked, ref.copies_matching) == (2, 1)
    assert ref.source_url == "https://example.test/lra"
    assert ground(get("lra_191_2"), full_source(drop="lra_191_2")).grounding \
        is Grounding.NOT_FOUND
    assert ground(get("lra_191_2"), None).grounding is Grounding.NOT_CHECKED
    assert ground(get("eea_10_2"), InMemorySource()).grounding is Grounding.NOT_CHECKED


def build_corpus(root: Path, docs: list[dict]) -> CorpusPaths:
    """A minimal real corpus: database rows, text files and an FTS index."""
    paths = CorpusPaths(root)
    paths.text.mkdir(parents=True)
    with CorpusStore(paths.corpus_db) as store:
        for d in docs:
            store.conn.execute(
                """INSERT INTO documents (doc_id, source_id, canonical_url, retrieved_at,
                   content_sha256, doc_type, is_draft, title, text_status, text_chars,
                   updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (d["doc_id"], "s", d.get("url", f"https://x.test/{d['doc_id']}"), "t",
                 d["doc_id"], d["doc_type"], d.get("draft", 0), d["title"],
                 d.get("status", "ok"), len(d["text"]), "t"))
            if d.get("write_text", True):
                (paths.text / f"{d['doc_id']}.txt").write_text(d["text"], encoding="utf-8")
        store.conn.commit()
        with FtsIndex(paths.fts_db) as fts:
            fts.rebuild(paths, store)
    return paths


@pytest.fixture
def corpus(tmp_path: Path) -> CorpusPaths:
    lra = statute_text(LRA)
    return build_corpus(tmp_path / "corpus", [
        dict(doc_id="lra", doc_type="amendment", title="LABOUR RELATIONS ACT", text=lra),
        dict(doc_id="lra-draft", doc_type="statute", title="Draft Labour Relations Act",
             text=lra + " padding" * 50, draft=1),
        dict(doc_id="lra-scan", doc_type="statute", title="Labour Relations Act",
             text=lra, status="ocr_needed"),
        dict(doc_id="lra-notext", doc_type="statute", title="Labour Relations Act",
             text="x", write_text=False),
        dict(doc_id="eea", doc_type="statute", title="Employment Equity Act",
             text=statute_text(EEA)),
        dict(doc_id="j1", doc_type="judgment", title="[2011] ZALCCT 10",
             text="the employer made continued employment intolerable for her"),
        dict(doc_id="j2", doc_type="judgment", title="[2012] ZALC 1", draft=1,
             text="continued employment intolerable, a draft"),
        dict(doc_id="g1", doc_type="guideline", title="Council constitution",
             text="continued employment intolerable in a guideline"),
        dict(doc_id="lra-cop", doc_type="code_of_good_practice",
             title="Labour Relations Act", text="continued employment intolerable"),
        dict(doc_id="eea-form", doc_type="statute", title="Employment Equity Act",
             text="DECLARATION BY EMPLOYEE\n(Confidential)\nPURPOSE OF THIS FORM\n"),
    ])


def test_corpus_source_reads_only_held_readable_non_draft_copies(corpus):
    source = CorpusSource(corpus)
    texts = source.texts_for(LRA)
    # 'lra' is typed amendment on purpose (the pre-D-024 mistyping): its opening
    # still names it as the Act. 'lra-cop' only carries the Act's name.
    assert [t.doc_id for t in texts] == ["lra"]
    assert source.texts_for(LRA) is texts                         # cached
    assert ground(get("lra_194_3"), source).doc_id == "lra"
    assert [t.doc_id for t in source.texts_for(EEA)] == ["eea"]   # not the form
    assert CorpusSource(corpus).texts_for("UNKNOWN") == []
    assert corpus_source(corpus.root) is not None
    assert corpus_source(corpus.root / "missing") is None


def test_only_documents_that_identify_as_the_act_count_as_copies(tmp_path):
    """Title matches that are not the Act do not dilute the copy count (D-023, D-024)."""
    paths = build_corpus(tmp_path / "c", [
        dict(doc_id="lra", doc_type="statute", title="LABOUR RELATIONS ACT",
             text=statute_text(LRA)),
        dict(doc_id="lra-gazette", doc_type="statute", title="Labour Relations Act",
             text="It is hereby notified that the President has assented to the "
                  "following Act\nNo. 66 of 1995: Labour Relations Act, 1995.\n\n"
                  + statute_text(LRA, drop="lra_191_2", heading=False)),
        dict(doc_id="lra-fund", doc_type="statute", title="Labour Relations Act",
             text="MOTOR INDUSTRY SICK, ACCIDENT AND MATERNITY PAY FUND\nRULES\n"
                  "the employer must prove that the dismissal is fair"),
        dict(doc_id="lra-amend", doc_type="statute", title="Labour Relations Act",
             text="ACT\nTo amend the Labour Relations Act, 1995\n\n"
                  "the employer must prove that the dismissal is fair"),
        dict(doc_id="eea", doc_type="statute", title="Employment Equity Act",
             text="ACT\nTo provide for employment equity\n\n" + statute_text(EEA, heading=False)),
        dict(doc_id="eea-summary", doc_type="statute",
             title="SUMMARY OF THE EMPLOYMENT EQUITY ACT",
             text="SUMMARY OF THE EMPLOYMENT EQUITY ACT, 55 OF 1998, AS AMENDED\n"
                  "within six months after the act or omission that allegedly "
                  "constitutes unfair discrimination"),
    ])
    source = CorpusSource(paths)
    assert {t.doc_id for t in source.texts_for(LRA)} == {"lra", "lra-gazette"}
    assert [t.doc_id for t in source.texts_for(EEA)] == ["eea"]
    onus = ground(get("lra_192_2"), source)             # also quoted by the fund rules
    assert (onus.copies_checked, onus.copies_matching) == (2, 2)
    condonation = ground(get("lra_191_2"), source)      # a genuine disagreement
    assert (condonation.copies_checked, condonation.copies_matching) == (2, 1)
    eea = ground(get("eea_10_2"), source)
    assert eea.grounding is Grounding.VERIFIED
    assert (eea.copies_checked, eea.copies_matching) == (1, 1)


def test_corpus_source_shares_texts_by_file_identity(tmp_path, monkeypatch):
    paths = build_corpus(tmp_path / "c", [
        dict(doc_id="lra", doc_type="statute", title="Labour Relations Act",
             text=statute_text(LRA)),
        dict(doc_id="lra-fund", doc_type="statute", title="Labour Relations Act",
             text="FUND RULES"),
    ])
    reads: list[str] = []
    real_read = Path.read_text

    def counting_read(self: Path, *args, **kwargs) -> str:
        reads.append(self.stem)
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read)
    shared: TextCache = {}
    first = CorpusSource(paths, text_cache=shared).texts_for(LRA)
    again = CorpusSource(paths, text_cache=shared).texts_for(LRA)
    assert reads == ["lra", "lra-fund"] and again == first   # neither file read twice
    (paths.text / "lra.txt").write_text(statute_text(LRA, drop="lra_192_2"), encoding="utf-8")
    changed = ground(get("lra_192_2"), CorpusSource(paths, text_cache=shared))
    assert changed.grounding is Grounding.NOT_FOUND
    assert reads[2:] == ["lra"]                              # only the changed file


def test_corpus_evidence_filters_drafts_types_and_act_copies(corpus):
    finder = CorpusEvidence(corpus)
    hits = finder.find("Constructive", '"continued employment intolerable"', limit=5)
    assert [h.doc_id for h in hits] == ["j1"]
    assert hits[0].doc_type == "judgment" and "[" in hits[0].snippet
    assert finder.find("x", '"never appears anywhere"', limit=3) == []
    assert CorpusEvidence(CorpusPaths(corpus.root / "missing")).find("x", "y", 3) == []


def test_corpus_evidence_survives_a_malformed_query(corpus):
    assert CorpusEvidence(corpus).find("x", '"unbalanced', limit=3) == []


def test_a_hash_in_the_corpus_path_still_opens_the_corpus_read_only(tmp_path):
    # A hand-built file: URI ends the path at '#', drops mode=ro and opens (and
    # creates) a stray database named 'proj' instead.
    paths = build_corpus(tmp_path / "proj#1" / "c", [
        dict(doc_id="lra", doc_type="statute", title="Labour Relations Act",
             text=statute_text(LRA)),
        dict(doc_id="j1", doc_type="judgment", title="[2011] ZALCCT 10",
             text="the employer made continued employment intolerable for her")])
    assert ground(get("lra_192_2"), corpus_source(paths.root)).grounding is Grounding.VERIFIED
    hits = CorpusEvidence(paths).find("x", '"continued employment intolerable"', limit=3)
    assert [h.doc_id for h in hits] == ["j1"]
    assert [p.name for p in tmp_path.iterdir()] == ["proj#1"]


def test_issues_for():
    assert issues_for(facts(reason=ReasonGiven.UNKNOWN), late=False) == []
    assert issues_for(facts(reason=ReasonGiven.UNKNOWN), late=True) == [LATE_QUERY]
    ulp = issues_for(facts(dispute=DisputeKind.UNFAIR_LABOUR_PRACTICE), late=False)
    assert ulp[0][0] == "Unfair labour practices"


# -- engine -------------------------------------------------------------------

def nav(**kw) -> Navigator:
    kw.setdefault("statutes", full_source())
    return Navigator(**kw)


def test_misconduct_after_internal_appeal_counts_from_the_final_decision():
    a = nav().assess(facts(reason=ReasonGiven.CONDUCT, final_decision_date=date(2026, 9, 15)),
                     TODAY)
    referral = a.deadlines[0]
    assert referral.due == date(2026, 10, 15) and referral.status is DeadlineStatus.OPEN
    assert [s.basis.key for s in a.route] == ["lra_191_1_a", "lra_191_5_a"]
    assert a.remedy_basis.key == "lra_194_1" and a.onus.key == "lra_192_2"
    assert a.fully_grounded and a.grounding_summary == {"verified": 6}
    assert DISMISSAL_CODE_WARNING in a.warnings


def test_late_referral_adds_condonation_everywhere():
    finder = FakeEvidence()
    a = nav(evidence=finder).assess(facts(reason=ReasonGiven.CONSTRUCTIVE,
                                          event_date=date(2026, 8, 3)), TODAY)
    assert a.deadlines[0].status is DeadlineStatus.EXPIRED
    assert "lra_191_2" in [s.basis.key for s in a.route]
    assert a.checklist[-1] == "Condonation or late-referral issue."
    assert finder.calls == ["Constructive dismissal", LATE_QUERY[0]]
    assert len(a.evidence) == 2


def test_labour_court_track_deadline_waits_for_certificate_then_runs_90_days():
    auto = dict(reason=ReasonGiven.AUTOMATICALLY_UNFAIR,
                auto_unfair_ground=AutoUnfairGround.PREGNANCY)
    pending = nav().assess(facts(**auto), TODAY).deadlines[1]
    assert pending.status is DeadlineStatus.NOT_STARTED and pending.basis.key == "lra_191_11"
    a = nav().assess(facts(**auto, certificate_date=date(2026, 9, 20)), TODAY)
    assert a.deadlines[1].due == date(2026, 12, 19)
    assert a.route[-1].basis.key == "lra_191_5_b"
    assert a.remedy_basis.key == "lra_194_3"


def test_certificate_advances_arbitration_route_and_starts_s136_deadline():
    a = nav().assess(facts(reason=ReasonGiven.CONDUCT,
                           event_date=date(2026, 6, 1),
                           certificate_date=date(2026, 8, 1)), TODAY)
    assert [d.due for d in a.deadlines] == [date(2026, 7, 1), date(2026, 10, 30)]
    assert [step.basis.key for step in a.route if step.basis] == ["lra_191_5_a"]
    assert a.route[0].action == "Request arbitration"


def test_late_s136_request_gets_arbitration_condonation_not_referral_condonation():
    a = nav().assess(facts(reason=ReasonGiven.CONDUCT,
                           event_date=date(2026, 1, 1),
                           certificate_date=date(2026, 4, 1)), TODAY)
    assert a.deadlines[-1].status is DeadlineStatus.EXPIRED
    assert [step.basis.key for step in a.route if step.basis] == [
        "lra_136_1_b", "lra_191_5_a"
    ]
    assert "arbitration request" in a.route[0].action


def test_election_and_probation_routes():
    a = nav().assess(facts(reason=ReasonGiven.OPERATIONAL_REQUIREMENTS,
                           employer_has_fewer_than_10_employees=True), TODAY)
    assert a.route[-1].basis.key == "lra_191_12" and len(a.deadlines) == 2
    p = nav().assess(facts(reason=ReasonGiven.CAPACITY, probation=True), TODAY)
    assert [s.basis.key for s in p.route][-2:] == ["lra_191_5_a", "lra_191_5A"]
    assert "immediately" in p.route[-2].note


def test_unfair_labour_practice_counts_from_awareness():
    a = nav().assess(facts(dispute=DisputeKind.UNFAIR_LABOUR_PRACTICE,
                           event_date=date(2026, 5, 1), became_aware_date=date(2026, 8, 1)),
                     TODAY)
    assert a.deadlines[0].due == date(2026, 10, 30)
    assert a.route[-1].basis.key == "lra_191_13"
    assert a.remedy_basis.key == "lra_194_4" and a.onus is None
    assert DISMISSAL_CODE_WARNING not in a.warnings


def test_discrimination_six_months_and_eea_route():
    a = nav().assess(facts(dispute=DisputeKind.DISCRIMINATION_NOT_DISMISSAL,
                           event_date=date(2026, 1, 31)), TODAY)
    assert a.deadlines[0].due == date(2026, 7, 31)
    assert [s.basis.key for s in a.route] == ["eea_10_2", "eea_10_3", "eea_10_6_a"]
    assert a.remedy_ceiling is None and a.remedy_basis is None


def test_ungrounded_assessments_say_so():
    missing = nav(statutes=full_source(drop="lra_188_1")).assess(
        facts(reason=ReasonGiven.CONDUCT), TODAY)
    assert missing.grounding_summary["not_found"] >= 1 and not missing.fully_grounded
    assert any("not found" in w for w in missing.warnings)
    unchecked = Navigator().assess(facts(reason=ReasonGiven.CONDUCT), TODAY)
    assert set(unchecked.grounding_summary) == {"not_checked"}
    assert any("unverified" in w for w in unchecked.warnings)


def test_event_after_assessment_date_is_rejected():
    with pytest.raises(ValueError):
        nav().assess(facts(event_date=date(2026, 12, 1)), TODAY)


@pytest.mark.parametrize("kw", [
    # An appeal outcome typed a year late would otherwise move the 30-day
    # referral deadline from 2026-10-25 to 2027-10-25 and report it as open.
    dict(reason=ReasonGiven.CONDUCT, event_date=date(2026, 8, 1),
         final_decision_date=date(2027, 9, 25)),
    dict(reason=ReasonGiven.AUTOMATICALLY_UNFAIR, auto_unfair_ground=AutoUnfairGround.PREGNANCY,
         event_date=date(2026, 8, 1), certificate_date=date(2027, 1, 5)),
    dict(dispute=DisputeKind.UNFAIR_LABOUR_PRACTICE, event_date=date(2026, 8, 1),
         became_aware_date=date(2026, 10, 8)),
])
def test_dates_after_the_assessment_date_are_rejected(kw):
    name = next(k for k in kw if k.endswith("_date") and k != "event_date")
    with pytest.raises(ValueError, match=f"{name} is after the assessment date"):
        nav().assess(facts(**kw), TODAY)


def test_dates_on_the_assessment_date_are_accepted():
    a = nav().assess(facts(reason=ReasonGiven.CONDUCT, event_date=date(2026, 8, 1),
                           final_decision_date=TODAY), TODAY)
    assert a.deadlines[0].due == date(2026, 11, 6)


ORDER_2_BASIS = {Track.ARBITRATION: "lra_191_5_a", Track.LABOUR_COURT: "lra_191_5_b",
                 Track.ELECTION: "lra_191_12", Track.EEA_LABOUR_COURT: "eea_10_6_a"}


def test_track_values_are_the_strings_golden_cases_and_logs_use():
    assert [t.value for t in Track] == list(ORDER_2_BASIS) == \
        ["arbitration", "labour_court", "election", "eea_labour_court"]
    assert json.dumps(Track.ELECTION) == '"election"' and f"{Track.ELECTION}" == "election"


@pytest.mark.parametrize("track", list(Track))
def test_every_track_has_its_own_route_and_deadlines(track):
    engine = nav()
    route = engine._route(facts(), track, late=False)
    order_2 = [s.basis.key for s in route if s.order == 2 and s.basis]
    assert order_2[0] == ORDER_2_BASIS[track]
    if track != Track.EEA_LABOUR_COURT:
        assert not any(s.basis.key.startswith("eea_") for s in route if s.basis)
    labour_court = ["lra_191_11"] if track in (Track.LABOUR_COURT, Track.ELECTION) else []
    assert [d.basis.key for d in engine._deadlines(facts(), track, TODAY)] == \
        ["lra_191_1_b_i", *labour_court]
    # A plain string still dispatches like its member.
    assert engine._route(facts(), track.value, late=False) == route


def test_an_unknown_track_fails_instead_of_borrowing_the_eea_route():
    with pytest.raises(AssertionError):
        nav()._route(facts(), "bogus", late=False)
    with pytest.raises(AssertionError):
        nav()._deadlines(facts(), "bogus", TODAY)


def test_code_warning_follows_the_verified_flag(monkeypatch):
    monkeypatch.setitem(engine_mod.CURRENT_DISMISSAL_CODE, "verified", True)
    assert DISMISSAL_CODE_WARNING not in nav().assess(facts(), TODAY).warnings


def test_assessment_is_deterministic():
    f = facts(reason=ReasonGiven.CONDUCT)
    assert nav().assess(f, TODAY) == nav().assess(f, TODAY)


# -- rendering ----------------------------------------------------------------

def test_markdown_states_grounding_copies_and_honest_links():
    src = full_source(drop="lra_194_1")
    src.add(LRA, "lra-old", statute_text(LRA, drop=("lra_188_1", "lra_194_1")))
    md = to_markdown(Navigator(src, FakeEvidence()).assess(
        facts(reason=ReasonGiven.CONDUCT, event_date=date(2026, 8, 1)), TODAY))
    for section in ("## Deadlines", "## Where to go", "## Remedies",
                    "## Questions to test", "## Held documents", "## Provisions quoted",
                    "## Warnings"):
        assert section in md
    assert "PASSED - condonation needed" in md
    assert "NOT FOUND in held text" in md
    assert "In 1 of 2 held copies; the others may be older consolidations." in md
    assert "no public URL recorded" in md
    assert "not legal advice" in md


def test_markdown_omits_empty_sections():
    md = to_markdown(nav().assess(facts(dispute=DisputeKind.DISCRIMINATION_NOT_DISMISSAL),
                                  TODAY))
    assert "## Remedies" not in md and "## Held documents" not in md
    assert "## Questions to test" not in md and "| - | - |" not in md


def test_markdown_shows_pending_deadline():
    md = to_markdown(nav().assess(facts(reason=ReasonGiven.CLOSED_SHOP), TODAY))
    assert "| - | - | not started |" in md


# -- logging ------------------------------------------------------------------

def test_json_logs_carry_outcomes_but_no_facts():
    stream = io.StringIO()
    navlog.configure("INFO", stream)
    navlog.configure("INFO", stream)          # idempotent: one handler, one line per event
    nav().assess(facts(reason=ReasonGiven.CONDUCT, council_name="Secret Council",
                       council=CouncilStatus.YES, event_date=date(2026, 9, 20)), TODAY)
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    done = [e for e in lines if e["event"] == "assessment_completed"]
    assert len(done) == 1 and done[0]["track"] == "arbitration"
    assert done[0]["grounding"] == {"verified": 6} and done[0]["level"] == "info"
    assert "Secret Council" not in stream.getvalue()
    logging.getLogger("labour_corpus.navigator").handlers.clear()


def test_json_formatter_includes_exceptions():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.getLogger("t").makeRecord(
            "t", logging.ERROR, __file__, 1, "failed", (), __import__("sys").exc_info())
    out = json.loads(navlog.JsonFormatter().format(record))
    assert out["event"] == "failed" and "RuntimeError: boom" in out["exc"]


# -- CLI ----------------------------------------------------------------------

runner = CliRunner()


def write_facts(tmp_path: Path, **data) -> str:
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_cli_assess_markdown_json_and_stdin(corpus, tmp_path):
    f = write_facts(tmp_path, reason="constructive", event_date="2026-09-01")
    args = ["navigate", "assess", f, "--out", str(corpus.root), "--as-of", "2026-10-07"]
    md = runner.invoke(app, args)
    assert md.exit_code == 0, md.output
    assert "Constructive dismissal" in md.output and "[2011] ZALCCT 10" in md.output
    js = runner.invoke(app, [*args, "--json", "--no-evidence"])
    assert js.exit_code == 0 and json.loads(js.output)["evidence"] == []
    piped = runner.invoke(app, ["navigate", "assess", "-", "--out", str(corpus.root),
                                "--as-of", "2026-10-07"],
                          input='{"event_date": "2026-09-01"}')
    assert piped.exit_code == 0


@pytest.mark.parametrize("data,as_of", [
    (dict(event_date="not a date"), "2026-10-07"),
    (dict(event_date="2026-09-01"), "07/10/2026"),
    (dict(event_date="2026-11-01"), "2026-10-07"),
    (dict(reason="conduct", event_date="2026-08-01", final_decision_date="2027-09-25"),
     "2026-10-07"),
])
def test_cli_invalid_input_exits_2(tmp_path, data, as_of):
    result = runner.invoke(app, ["navigate", "assess", write_facts(tmp_path, **data),
                                 "--out", str(tmp_path / "none"), "--as-of", as_of])
    assert result.exit_code == 2


def test_cli_exits_3_when_nothing_could_be_checked(tmp_path):
    f = write_facts(tmp_path, reason="conduct", event_date="2026-09-01")
    missing = runner.invoke(app, ["navigate", "assess", f, "--out", str(tmp_path / "none"),
                                  "--as-of", "2026-10-07", "--no-evidence"])
    assert missing.exit_code == 3
    assert "no corpus database at" in missing.stderr and "none" in missing.stderr
    assert "unverified - no statute text available" in missing.stdout
    # A corpus that holds the LRA but no copy of the EEA cannot check EEA wording.
    paths = build_corpus(tmp_path / "c", [dict(
        doc_id="lra", doc_type="statute", title="Labour Relations Act", text=statute_text(LRA))])
    eea = write_facts(tmp_path, dispute="discrimination_not_dismissal", event_date="2026-09-01")
    no_act = runner.invoke(app, ["navigate", "assess", eea, "--out", str(paths.root),
                                 "--as-of", "2026-10-07", "--no-evidence"])
    assert no_act.exit_code == 3 and "holds no usable copy" in no_act.stderr


def test_cli_annotations_are_evaluated_at_import():
    # Without postponed evaluation, Typer reads real types instead of strings.
    assert not hasattr(cli_mod, "annotations")
    assert inspect.signature(cli_mod._run).parameters["today"].annotation is date


def test_cli_exits_3_when_relied_on_wording_is_not_held(tmp_path):
    paths = build_corpus(tmp_path / "c", [dict(
        doc_id="lra", doc_type="statute", title="Labour Relations Act",
        text=statute_text(LRA, drop="lra_192_2"))])
    f = write_facts(tmp_path, reason="conduct", event_date="2026-09-01")
    result = runner.invoke(app, ["navigate", "assess", f, "--out", str(paths.root),
                                 "--as-of", "2026-10-07"])
    assert result.exit_code == 3 and "NOT FOUND" in result.output


def test_cli_provisions_check(corpus, tmp_path):
    ok = runner.invoke(app, ["navigate", "provisions", "--out", str(corpus.root)])
    assert ok.exit_code == 0 and ok.output.count("verified") == len(PROVISIONS)
    bad = runner.invoke(app, ["navigate", "provisions", "--out", str(tmp_path / "none")])
    assert bad.exit_code == 3 and "not_checked" in bad.output


# -- the real corpus ----------------------------------------------------------

@pytest.mark.skipif(not (REAL_CORPUS / "db" / "corpus.sqlite").exists(),
                    reason="no built corpus")
def test_every_provision_is_verified_in_the_real_corpus():
    source = corpus_source(REAL_CORPUS)
    refs = {k: ground(p, source) for k, p in PROVISIONS.items()}
    unverified = [k for k, r in refs.items() if r.grounding is not Grounding.VERIFIED]
    assert unverified == []
    # Measured 2026-10-09: two held copies of each Act identify themselves as
    # the Act; title matches that are fund rules, a code, a summary or a form
    # do not count (D-024). Only the wording amended since the older copy
    # disagrees (D-023). Update these when a copy of an Act is added.
    assert {r.copies_checked for r in refs.values()} == {2}
    assert {k for k, r in refs.items() if r.copies_matching < r.copies_checked} == {
        "lra_186_1_e", "lra_186_2", "lra_191_2", "lra_194_4", "eea_10_2"}


def test_unknown_provision_key_is_a_programming_error():
    with pytest.raises(KeyError, match="unknown provision"):
        get("lra_999")
