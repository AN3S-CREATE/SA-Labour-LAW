"""Parser tests against saved fixtures. No network access."""

from __future__ import annotations

from pathlib import Path

import pytest

from labour_corpus.models import DocType, Outcome, Remedy
from labour_corpus.parse.judgment import parse_judgment
from labour_corpus.parse.statute import classify_doc_type, parse_statute
from labour_corpus.parse.text import detect_language, html_title, html_to_text
from labour_corpus.ontology.tagger import process_defect_checklist, tag_document

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def judgment_text() -> tuple[str, str]:
    html = (FIXTURES / "zalcjhb_mining_dismissal.html").read_text(encoding="utf-8")
    return html_to_text(html), html_title(html)


@pytest.fixture(scope="module")
def statute_text() -> tuple[str, str]:
    html = (FIXTURES / "lra_consolidated_extract.html").read_text(encoding="utf-8")
    return html_to_text(html), html_title(html)


def test_extracts_neutral_citation_verbatim(judgment_text):
    text, title = judgment_text
    meta = parse_judgment(text, title=title)
    assert meta.neutral_citation == "[2024] ZALCJHB 212"
    assert meta.court_code == "ZALCJHB"


def test_extracts_case_number_and_date(judgment_text):
    text, title = judgment_text
    meta = parse_judgment(text, title=title)
    assert meta.case_no == "JR 1487/21"
    assert meta.date_delivered == "2024-06-14"
    assert meta.year == 2024


def test_extracts_parties_without_splitting_on_and(judgment_text):
    text, title = judgment_text
    meta = parse_judgment(text, title=title)
    assert "THEMBA MAHLANGU" in meta.parties
    # "Commission for Conciliation, Mediation and Arbitration" contains "and";
    # splitting on it would manufacture two fictional parties.
    assert any("CONCILIATION, MEDIATION AND ARBITRATION" in p for p in meta.parties)


def test_judge_extraction_rejects_headings(judgment_text):
    text, title = judgment_text
    meta = parse_judgment(text, title=title)
    assert "VAN NIEKERK J" in meta.judges
    assert not any("JUDGMENT" in j for j in meta.judges)
    assert not any("did not deal" in j for j in meta.judges)


def test_never_hallucinates_a_citation():
    """The core honesty guarantee: unknown must stay unknown."""
    meta = parse_judgment("A short note about employment. No citation appears here.")
    assert meta.neutral_citation is None
    assert meta.case_no is None
    assert meta.date_delivered is None
    assert meta.parties == []
    assert any("not found in source text" in note for note in meta.notes)


def test_citation_is_not_inferred_from_a_url():
    """A URL that looks like a citation must not become one."""
    meta = parse_judgment(
        "Body text with no citation.",
        title=None,
    )
    assert meta.neutral_citation is None


def test_tags_mining_dismissal_with_paragraph_evidence(judgment_text):
    text, _ = judgment_text
    result = tag_document(text)
    assert "mining" in result.sector_tags
    assert result.outcome is Outcome.UNFAIR_PROCEDURAL
    assert result.remedy is Remedy.REINSTATEMENT

    defects = process_defect_checklist(result.evidence)
    assert "inconsistency" in defects
    assert "representation_denied" in defects
    assert "zero_tolerance_without_proportionality" in defects

    # Every paragraph cited as evidence must be a marker present in the text.
    for evidence in result.evidence:
        for paragraph in evidence.paragraphs:
            assert f"[{paragraph}]" in text


def test_mining_decision_records_its_reason(judgment_text):
    text, _ = judgment_text
    result = tag_document(text)
    decisions = [e for e in result.evidence if e.category == "sector_decision"]
    assert decisions, "a mining call must record why it was made"
    assert "threshold" in decisions[0].reason


def test_statute_is_not_reclassified_by_a_citation(statute_text):
    """LRA s188(2) refers to a code of good practice; it is still an Act."""
    text, title = statute_text
    meta = parse_statute(text, title=title)
    assert meta.doc_type is DocType.STATUTE
    assert meta.short_title == "Labour Relations Act"
    assert meta.act_number == "66"
    assert meta.act_year == 1995
    assert "LRA 66 of 1995" in meta.instruments


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Code of Good Practice on Dismissal", DocType.CODE_OF_GOOD_PRACTICE),
        ("Rules for the Conduct of Proceedings before the CCMA", DocType.CCMA_RULE),
        ("Labour Court Practice Manual", DocType.PRACTICE_MANUAL),
        ("Labour Relations Amendment Act 6 of 2014", DocType.AMENDMENT),
    ],
)
def test_doc_type_classification(title, expected):
    assert classify_doc_type("", title) is expected


def test_language_detection(judgment_text):
    text, _ = judgment_text
    assert detect_language(text) == "en"


def test_afrikaans_text_is_kept_and_labelled():
    afrikaans = (
        "Die werknemer is ontslaan nadat hy nie die veiligheidsreel nagekom het "
        "nie. Die hof bevind dat die ontslag substantief onbillik was en dat die "
        "werkgewer nie die ooreenkoms met die werknemer nagekom het nie. Die "
        "regter het ook bevind dat die werkgewer deur sy optrede die werknemer "
        "benadeel het en dat die aansoek van die werknemer dus slaag met koste."
    )
    assert detect_language(afrikaans) == "af"


def test_empty_document_yields_no_tags():
    result = tag_document("")
    assert result.dismissal_tags == []
    assert result.outcome is Outcome.UNKNOWN
    assert result.confidence == 0.0


# -- CCMA award metadata ---------------------------------------------------
AWARD_TEXT = """IN THE COMMISSION FOR CONCILIATION, MEDIATION AND ARBITRATION

Case No: GAJB 12345-24

Commissioner: T Ndlovu

[1] The applicant was dismissed for a safety rule breach at a platinum mine.

[2] I find that the dismissal was substantively unfair. The applicant is
reinstated with retrospective effect.
"""


def test_extracts_a_ccma_case_number():
    """CCMA numbering is office-code plus number plus year, e.g. GAJB12345-24."""
    meta = parse_judgment(AWARD_TEXT)
    assert meta.case_no == "GAJB 12345-24"


def test_extracts_a_commissioner_name():
    meta = parse_judgment(AWARD_TEXT)
    assert any("Ndlovu" in name for name in meta.judges)


@pytest.mark.parametrize("text,expected", [
    ("Commissioner: T Ndlovu", True),
    ("COMMISSIONER N P DLAMINI N.O.", True),
    ("The commissioner did not deal with the inconsistency.", False),
    ("THE COMMISSIONER FAILED TO APPLY HIS MIND", False),
])
def test_commissioner_extraction_rejects_prose(text, expected):
    """A sentence about a commissioner is not the naming of one."""
    from labour_corpus.parse.judgment import extract_judges

    assert bool(extract_judges(text)) is expected


def test_award_outcome_and_remedy(judgment_text):
    result = tag_document(AWARD_TEXT)
    assert result.outcome is Outcome.UNFAIR_SUBSTANTIVE
    assert result.remedy is Remedy.REINSTATEMENT


def test_mining_s54_requires_mining_context():
    """'section 54' appears in many statutes; only mining context counts."""
    unrelated = tag_document(
        "In terms of section 54 of the Companies Act the directors must file "
        "an annual return with the Commission before the anniversary date."
    )
    assert not any(e.tag == "mhsa_s54_stoppage" for e in unrelated.evidence)

    mining = tag_document(
        "The Principal Inspector of Mines issued a section 54 instruction "
        "stopping operations at the mine after the fall of ground."
    )
    assert any(e.tag == "mhsa_s54_stoppage" for e in mining.evidence)


# -- Acts that identify themselves in their opening (D-024) --------------------

from labour_corpus.parse.statute import self_identified_act  # noqa: E402

_SAFLII_CONSOL = ("Labour Relations Act 1995\nSouth Africa: Consolidated Acts\nYou are here:\n"
                  "... as amended by Labour Relations Amendment Act 6 of 2014 ...")


@pytest.mark.parametrize("opening,expected", [
    (_SAFLII_CONSOL, DocType.STATUTE),
    ("Law of Evidence Amendment Act 1988\nSouth Africa: Consolidated Acts\n", DocType.STATUTE),
    ("Telecommunications Act, 1996 [No. 103 of 1996] - G 17581\nSouth Africa: Numbered Acts\n",
     DocType.STATUTE),
    ("Labour Relations Amendment Act, 2014 [No. 6 of 2014]\nSouth Africa: Numbered Acts\n",
     DocType.AMENDMENT),
    ("NO. 66 OF 1995: LABOUR RELATIONS ACT, 1995.\nIt is hereby notified that the President "
     "has assented to the following Act", DocType.STATUTE),
    ("It is hereby notified that the President has assented to the following Act\n"
     "No. 8 of 2018: Labour Relations Amendment Act, 2018", DocType.AMENDMENT),
    ("BASIC CONDITIONS OF\nEMPLOYMENTACT\nNo , 1997\nACT\nTogiveeffecttotherighttofair",
     DocType.STATUTE),
    ("REPUBLIC OF SOUTH AFRICA\nACT\nTo amend the Labour Relations Act, 1995", DocType.AMENDMENT),
])
def test_acts_identify_themselves_in_their_opening(opening, expected):
    assert self_identified_act(opening) is expected
    assert classify_doc_type(opening + "\nSchedule 8 Code of Good Practice") is expected


@pytest.mark.parametrize("opening", [
    "Code of Good Practice on Dismissal\nissued in terms of the Labour Relations Act 66 of 1995",
    "LRA Form 7.11\nReferral of a dispute to the CCMA for conciliation",
    "the President has assented to nothing here; this is a guideline",
])
def test_documents_that_merely_cite_an_act_are_not_acts(opening):
    assert self_identified_act(opening) is None


def test_a_title_still_outranks_the_opening():
    assert classify_doc_type(_SAFLII_CONSOL, title="Code of Good Practice on Dismissal") \
        is DocType.CODE_OF_GOOD_PRACTICE


# -- a code names itself in its opening; the Act it cites is a citation ----------
@pytest.mark.parametrize("opening", [
    "Code of Good Practice on Dismissal\nissued in terms of the Labour Relations Act 66 of 1995",
    # The HIV/AIDS code's opening (871f826a): the Act's name comes first.
    "Department of Labour\nEmployment Equity Act,\nAct No. 55 of 1998\n\n"
    "CODE OF GOOD PRACTICE ON KEY ASPECTS OF HIV/AIDS AND EMPLOYMENT",
    "EMPLOYMENT EQUITY ACT NO 55, OF 1998\n\nCODE OF GOOD PRACTICE ON KEY ASPECTS OF "
    "DISABILITY\n" + "x" * 2_000 + " The Employment Equity Act, No. 55 of 1998",
    "BASIC CONDITIONS OF EMPLOYMENT ACT 75 OF 1997\nCode of Good Practice on the "
    "Protection of Employees during Pregnancy and after the Birth of a Child",
    # A gazette's notice list names it in sentence case (6b2a8370).
    "Government Gazette\n1202 Employment Equity Act (55/1998): Draft code of good "
    "practice on the preparation and implementation of employment equity plans",
])
def test_a_code_that_cites_its_act_is_a_code(opening):
    """Title-less PDFs reach the classifier with only their text (pipeline title=None)."""
    assert classify_doc_type(opening) is DocType.CODE_OF_GOOD_PRACTICE


def test_a_code_mentioned_only_after_the_opening_does_not_make_a_code():
    """Beyond the opening, a code is something the document refers to."""
    text = "Labour Relations Act 66 of 1995\n" + "x" * 3_200 + "\nCode of Good Practice"
    assert classify_doc_type(text) is DocType.STATUTE


@pytest.mark.parametrize("reference", [
    "must take into account any relevant code of good practice issued in terms of this Act.",
    "The Minister may, after consulting NEDLAC, issue a code of good practice.",
    "regard must be had to the code of good practice on dismissal.",
])
def test_an_act_that_cites_a_code_in_its_opening_is_not_a_code(reference):
    """LRA s188(2) refers to 'any relevant code of good practice': a citation."""
    text = f"Labour Relations Act 66 of 1995\n188. Other dismissals\n(2) Any person {reference}"
    assert classify_doc_type(text) is DocType.STATUTE


def test_the_lra_fixture_without_its_title_is_not_a_code(statute_text):
    """s188(2) sits inside the extract's first 3,000 characters."""
    text, _title = statute_text
    assert classify_doc_type(text) is not DocType.CODE_OF_GOOD_PRACTICE
