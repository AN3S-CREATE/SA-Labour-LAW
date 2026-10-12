"""Navigator phase 2: golden cases, review sheet, interview, MCP server."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from labour_corpus.ccma.mcp_server import serve
from labour_corpus.cli import app
from labour_corpus.config import CorpusPaths
from labour_corpus.navigator import grounding
from labour_corpus.navigator.engine import Navigator
from labour_corpus.navigator.golden import (
    DEFAULT_CASES, check, load_cases, mismatches, review_sheet,
)
from labour_corpus.navigator.interview import (
    GROUNDS, REASONS, WHAT_HAPPENED, TerminalAsker, build_facts,
)
from labour_corpus.navigator.mcp_server import NavigatorServer
from labour_corpus.navigator.models import (
    AutoUnfairGround, CaseFacts, CouncilStatus, DisputeKind, ReasonGiven,
)
from test_navigator import build_corpus, full_source, statute_text
from labour_corpus.navigator.provisions import LRA, PROVISIONS
from labour_corpus.state import CorpusStore

runner = CliRunner()


# -- golden cases -------------------------------------------------------------

def test_every_golden_case_matches_the_engine():
    golden = load_cases()
    assert len(golden.cases) >= 30
    assert check(golden, Navigator(full_source())) == {}


def test_golden_cases_are_marked_pending_until_a_lawyer_signs():
    golden = load_cases()
    assert golden.status == "pending_lawyer_review"
    assert not any(c.reviewed for c in golden.cases)


def test_golden_cases_cover_every_track():
    tracks = {c.expect["track"] for c in load_cases().cases}
    assert tracks == {"arbitration", "labour_court", "election", "eea_labour_court"}


@pytest.mark.parametrize("key,wrong", [
    ("track", "labour_court"),
    ("due", ["2026-10-21"]),
    ("status", ["expired"]),
    ("route", ["lra_191_1_a"]),
    ("remedy", "lra_194_3"),
    ("onus", False),
    ("forum_has", "Labour Court"),
    ("classification_has", "retrench"),
    ("warning_has", "nowhere to be found"),
    ("no_such_key", 1),
])
def test_the_checker_detects_every_kind_of_mismatch(key, wrong):
    case = load_cases().cases[0]                       # conduct-open
    bad = replace(case, expect={**case.expect, key: wrong})
    problems = mismatches(bad, Navigator(full_source()).assess(bad.facts, bad.as_of))
    assert len(problems) == 1 and key in problems[0]


def test_loader_rejects_duplicates_and_missing_track(tmp_path: Path):
    base = {"as_of": "2026-10-07", "facts": {"event_date": "2026-09-01"},
            "expect": {"track": "arbitration"}}
    dup = tmp_path / "dup.yaml"
    dup.write_text(json.dumps({"cases": [{"id": "a", **base}, {"id": "a", **base}]}))
    with pytest.raises(ValueError, match="duplicate"):
        load_cases(dup)
    notrack = tmp_path / "nt.yaml"
    notrack.write_text(json.dumps({"cases": [{"id": "a", **base, "expect": {}}]}))
    with pytest.raises(ValueError, match="no expected track"):
        load_cases(notrack)


def test_review_sheet_shows_observed_outcome_and_signoff_state(tmp_path: Path):
    golden = load_cases()
    signed = replace(golden.cases[0], reviewed_by="A. Advocate", reviewed_on=date(2026, 10, 9))
    wrong = replace(golden.cases[1], expect={"track": "labour_court"})
    sheet = review_sheet(replace(golden, cases=[signed, wrong]), Navigator(full_source()))
    assert "1 of 2 cases signed off" in sheet
    assert "A. Advocate on 2026-10-09" in sheet
    assert "Matches expectation:** NO: track" in sheet
    assert sheet.count("[ ] AGREE") == 2


def test_cli_review_sheet_passes_and_fails(tmp_path: Path):
    ok = runner.invoke(app, ["navigate", "review-sheet"])
    assert ok.exit_code == 0 and "pending_lawyer_review" in ok.output
    bad = tmp_path / "bad.yaml"
    bad.write_text(json.dumps({"cases": [{"id": "x", "as_of": "2026-10-07",
                                          "facts": {"event_date": "2026-09-01"},
                                          "expect": {"track": "election"}}]}))
    failed = runner.invoke(app, ["navigate", "review-sheet", "--cases", str(bad)])
    assert failed.exit_code == 1


def test_default_cases_path_exists():
    assert DEFAULT_CASES.exists()


# -- interview ----------------------------------------------------------------

class Script:
    """An Asker that replays answers and records the questions asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked: list[str] = []

    def _next(self, question):
        self.asked.append(question)
        return self.answers.pop(0)

    def choose(self, question, options):
        answer = self._next(question)
        assert answer in [k for k, _ in options], answer
        return answer

    def when(self, question, required):
        answer = self._next(question)
        return None if answer is None else date.fromisoformat(answer)

    def yes_no(self, question, allow_unknown):
        return self._next(question)

    def text(self, question):
        return self._next(question)


def test_interview_misconduct_with_appeal_and_council():
    ask = Script("dismissal", "conduct", "2026-09-01", True, "2026-09-20", False, False,
                 True, "MIBCO")
    facts = build_facts(ask)
    assert facts == CaseFacts(reason=ReasonGiven.CONDUCT, event_date=date(2026, 9, 1),
                              final_decision_date=date(2026, 9, 20),
                              council=CouncilStatus.YES, council_name="MIBCO")
    assert not ask.answers
    assert not any("name" in q.lower() and "council" not in q.lower() for q in ask.asked)


def test_interview_retrenchment_asks_s191_12_questions():
    facts = build_facts(Script("dismissal", "operational_requirements", "2026-09-01", False,
                               True, None, False, None))
    assert facts.employer_has_fewer_than_10_employees is True
    assert facts.only_this_employee_retrenched is None
    assert facts.council is CouncilStatus.UNKNOWN


def test_interview_automatically_unfair_and_certificate():
    facts = build_facts(Script("dismissal", "automatically_unfair", "e_pregnancy",
                               "2026-06-01", False, True, "2026-07-15", False))
    assert facts.auto_unfair_ground is AutoUnfairGround.PREGNANCY
    assert facts.certificate_date == date(2026, 7, 15) and facts.council is CouncilStatus.NO


def test_interview_constructive_with_and_without_protected_reason():
    plain = build_facts(Script("constructive", False, "2026-09-01", False, None))
    assert plain.reason is ReasonGiven.CONSTRUCTIVE and plain.auto_unfair_ground is None
    auto = build_facts(Script("constructive", True, "g_transfer_s197", "2026-09-01", False, None))
    assert auto.auto_unfair_ground is AutoUnfairGround.TRANSFER_S197


def test_interview_ulp_and_discrimination():
    ulp = build_facts(Script("ulp", "2026-05-01", "2026-08-01", None))
    assert ulp.dispute is DisputeKind.UNFAIR_LABOUR_PRACTICE
    assert ulp.became_aware_date == date(2026, 8, 1)
    disc = build_facts(Script("discrimination", "2026-05-01"))
    assert disc.dispute is DisputeKind.DISCRIMINATION_NOT_DISMISSAL


def test_menus_cover_every_reason_and_ground():
    assert {k for k, _ in REASONS} == {r.value for r in ReasonGiven} - {"constructive"}
    assert {k for k, _ in GROUNDS} == {g.value for g in AutoUnfairGround}
    assert len(WHAT_HAPPENED) == 4


def test_terminal_asker_reasks_until_the_answer_is_usable():
    replies = iter(["9", "x", "2", "31/12/2026", "2026-12-31", "", "maybe", "?", "n", "y",
                    "  Council " + "x" * 200])
    said: list[str] = []
    ask = TerminalAsker(prompt=lambda *a, **k: next(replies), echo=said.append)
    assert ask.choose("Q", [("a", "A"), ("b", "B")]) == "b"
    assert ask.when("D", required=True) == date(2026, 12, 31)
    assert ask.when("D", required=False) is None
    assert ask.yes_no("Y", allow_unknown=True) is None
    assert ask.yes_no("Y", allow_unknown=False) is False
    assert ask.yes_no("Y", allow_unknown=False) is True
    assert len(ask.text("T")) == 120
    assert any("1 to 2" in s for s in said) and any("YYYY-MM-DD" in s for s in said)


def test_cli_interview_assesses_and_saves(tmp_path: Path):
    paths = build_corpus(tmp_path / "c", [dict(doc_id="lra", doc_type="statute",
                                               title="Labour Relations Act",
                                               text=statute_text(LRA))])
    save = tmp_path / "facts.json"
    answers = "1\n1\n2026-09-20\nn\nn\nn\nn\n"
    result = runner.invoke(app, ["navigate", "interview", "--out", str(paths.root),
                                 "--as-of", "2026-10-07", "--save", str(save),
                                 "--no-evidence"], input=answers)
    assert result.exit_code == 0, result.output
    assert "conduct" in result.output and "2026-10-20" in result.output
    assert json.loads(save.read_text()) == {"reason": "conduct", "event_date": "2026-09-20",
                                            "council": "no"}


def test_cli_interview_without_a_corpus_still_answers_but_exits_3(tmp_path: Path):
    answers = "1\n1\n2026-09-20\nn\nn\nn\nn\n"
    result = runner.invoke(app, ["navigate", "interview", "--out", str(tmp_path / "none"),
                                 "--as-of", "2026-10-07", "--no-evidence"], input=answers)
    assert result.exit_code == 3
    assert "2026-10-20" in result.stdout and "no corpus database at" in result.stderr


def test_cli_interview_rejects_a_future_appeal_date(tmp_path: Path):
    answers = "1\n1\n2026-08-01\ny\n2027-09-25\nn\nn\nn\n"
    result = runner.invoke(app, ["navigate", "interview", "--as-of", "2026-10-07",
                                 "--no-evidence"], input=answers)
    assert result.exit_code == 2 and "final_decision_date" in result.stderr


def test_cli_interview_rejects_impossible_dates(tmp_path: Path):
    answers = "1\n1\n2026-09-20\ny\n2026-09-01\n"
    result = runner.invoke(app, ["navigate", "interview", "--as-of", "2026-10-07"],
                           input=answers)
    assert result.exit_code == 2
    bad_as_of = runner.invoke(app, ["navigate", "interview", "--as-of", "soon"])
    assert bad_as_of.exit_code == 2


# -- MCP ----------------------------------------------------------------------

@pytest.fixture
def server(tmp_path: Path) -> NavigatorServer:
    paths = build_corpus(tmp_path / "c", [dict(doc_id="lra", doc_type="statute",
                                               title="Labour Relations Act",
                                               text=statute_text(LRA))])
    return NavigatorServer(paths.root, today=date(2026, 10, 7))


def call(server, name, args):
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": args}})["result"]


def test_mcp_lists_navigator_tools_with_a_strict_schema(server):
    init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "dismissal-navigator"
    assert "not legal advice" in init["result"]["instructions"]
    tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assess = next(t for t in tools if t["name"] == "navigator_assess")
    assert assess["inputSchema"]["additionalProperties"] is False
    assert "event_date" in assess["inputSchema"]["required"]


def test_mcp_assess_returns_grounded_assessment(server):
    result = call(server, "navigator_assess", {"reason": "conduct", "event_date": "2026-09-20",
                                               "include_evidence": True})
    body = result["structuredContent"]
    assert result["isError"] is False
    assert body["deadlines"][0]["due"] == "2026-10-20"
    assert body["fully_grounded"] is True and body["markdown"].startswith("# Dismissal")
    explicit = call(server, "navigator_assess", {"event_date": "2026-09-20",
                                                 "as_of": "2026-11-30"})
    assert explicit["structuredContent"]["deadlines"][0]["status"] == "expired"


@pytest.mark.parametrize("args,message", [
    ({"event_date": "2026-09-20", "name": "Jane"}, "Extra inputs"),
    ({"reason": "conduct"}, "event_date"),
    ({"event_date": "2026-12-01"}, "after the assessment date"),
    ({"event_date": "2026-09-01", "certificate_date": "2027-01-05"},
     "certificate_date is after the assessment date"),
])
def test_mcp_assess_refuses_bad_facts(server, args, message):
    result = call(server, "navigator_assess", args)
    assert result["isError"] is True and message in result["content"][0]["text"]


def test_mcp_provisions_and_unknown_tool(server, tmp_path):
    body = call(server, "navigator_provisions", {})["structuredContent"]
    assert body["all_verified"] is False                      # EEA not held in this corpus
    assert {p["grounding"] for p in body["provisions"]} == {"verified", "not_checked"}
    assert call(server, "nope", {})["isError"] is True
    missing = NavigatorServer(tmp_path / "none")
    assert {p["grounding"] for p in call(missing, "navigator_provisions", {})
            ["structuredContent"]["provisions"]} == {"not_checked"}


# -- MCP: held texts are reused across calls, never served stale ----------------

def add_documents(root: Path, docs: list[dict]) -> None:
    """Add rows and text files to a built corpus, as a later harvest would."""
    paths = CorpusPaths(root)
    with CorpusStore(paths.corpus_db) as store:
        for d in docs:
            store.conn.execute(
                """INSERT INTO documents (doc_id, source_id, canonical_url, retrieved_at,
                   content_sha256, doc_type, is_draft, title, text_status, text_chars,
                   updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (d["doc_id"], "s", f"https://x.test/{d['doc_id']}", "t", d["doc_id"],
                 "statute", d.get("draft", 0), d["title"], "ok", len(d["text"]), "t2"))
            (paths.text / f"{d['doc_id']}.txt").write_text(d["text"], encoding="utf-8")
        store.conn.commit()


def lra_refs(server) -> dict[str, dict]:
    body = call(server, "navigator_provisions", {})["structuredContent"]
    return {p["key"]: p for p in body["provisions"] if p["key"].startswith("lra_")}


def test_mcp_reuses_held_texts_across_calls(server, monkeypatch):
    normalised: list[str] = []
    real = grounding.normalise

    def counting(text: str) -> str:
        normalised.append(text)
        return real(text)

    monkeypatch.setattr(grounding, "normalise", counting)
    first = call(server, "navigator_provisions", {})
    quotes = {p.quote for p in PROVISIONS.values()}
    assert any(text not in quotes for text in normalised)      # the held LRA text
    normalised.clear()
    again = call(server, "navigator_provisions", {})
    assert again == first
    assert normalised and set(normalised) <= quotes             # only the 18 LRA needles
    normalised.clear()
    assessed = call(server, "navigator_assess", {"reason": "conduct",
                                                 "event_date": "2026-09-20"})
    assert assessed["structuredContent"]["fully_grounded"] is True
    assert set(normalised) <= quotes                            # assess shares the cache


def test_mcp_rereads_a_changed_text_without_a_restart(server):
    assert lra_refs(server)["lra_192_2"]["grounding"] == "verified"
    text = CorpusPaths(server.corpus_root).text / "lra.txt"
    text.write_text(statute_text(LRA, drop="lra_192_2"), encoding="utf-8")
    result = call(server, "navigator_assess", {"reason": "conduct", "event_date": "2026-09-20"})
    assert result["structuredContent"]["onus"]["grounding"] == "not_found"


def test_mcp_picks_up_new_copies_but_never_drafts(server):
    assert lra_refs(server)["lra_191_2"]["copies_checked"] == 1
    add_documents(server.corpus_root, [
        dict(doc_id="lra-draft", title="Draft Labour Relations Act", draft=1,
             text=statute_text(LRA) + " padding" * 50)])
    assert lra_refs(server)["lra_191_2"]["copies_checked"] == 1
    add_documents(server.corpus_root, [
        dict(doc_id="lra-old", title="Labour Relations Act",
             text=statute_text(LRA, drop="lra_191_2"))])
    refs = lra_refs(server)
    assert (refs["lra_191_2"]["copies_checked"], refs["lra_191_2"]["copies_matching"]) == (2, 1)
    assert refs["lra_191_2"]["doc_id"] == "lra"


def test_mcp_serves_over_stdio(server):
    stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n")
    stdout = io.StringIO()
    serve(Path("unused"), stdin=stdin, stdout=stdout, server=server)
    assert json.loads(stdout.getvalue())["id"] == 7


def test_cli_mcp_command_serves(server, monkeypatch):
    seen = {}
    monkeypatch.setattr("labour_corpus.ccma.mcp_server.serve",
                        lambda path, server: seen.update(path=path, server=server))
    result = runner.invoke(app, ["navigate", "mcp", "--out", str(server.corpus_root)])
    assert result.exit_code == 0 and isinstance(seen["server"], NavigatorServer)
