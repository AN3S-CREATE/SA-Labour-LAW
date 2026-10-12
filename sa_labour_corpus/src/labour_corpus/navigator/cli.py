"""``labour-corpus navigate``: assess a dismissal from facts or an interview.

Facts come from a JSON file, stdin or a guided interview. They are written to
disk only when ``interview --save`` asks for it. The assessment goes to stdout;
logs go to stderr as JSON lines.
"""

import json
import sys
from datetime import date
from pathlib import Path

import typer
from pydantic import ValidationError

from . import log as navlog
from .engine import Navigator
from .evidence import CorpusEvidence
from .golden import DEFAULT_CASES, check, load_cases, review_sheet
from .grounding import InMemorySource, corpus_source, ground
from .interview import TerminalAsker, build_facts
from .models import CaseFacts, Grounding
from .provisions import PROVISIONS, Provision
from .render import to_markdown
from ..config import CorpusPaths

navigate_app = typer.Typer(help="Dismissal Navigator: deadlines, forum and grounded law "
                                "for a dismissed employee. Not legal advice.",
                           no_args_is_help=True)

DEFAULT_CORPUS = Path("data/corpus")


def _read_facts(path: str) -> CaseFacts:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return CaseFacts.model_validate(json.loads(raw))


def _parse_as_of(as_of: str | None) -> date:
    try:
        return date.fromisoformat(as_of) if as_of else date.today()
    except ValueError as exc:
        typer.echo(f"Invalid input: {exc}", err=True)
        raise typer.Exit(2)


def _run(case: CaseFacts, today: date, out: Path, as_json: bool, evidence: bool) -> None:
    """Assess, print, and exit 2 on impossible dates or 3 on ungrounded wording.

    Wording that could not be checked is ungrounded too: an assessment run
    against a missing corpus must not exit 0 (``navigate provisions`` agrees).
    """
    finder = CorpusEvidence(CorpusPaths(out)) if evidence else None
    try:
        result = Navigator(corpus_source(out), finder).assess(case, today)
    except ValueError as exc:
        typer.echo(f"Invalid input: {exc}", err=True)
        raise typer.Exit(2)
    typer.echo(result.model_dump_json(indent=2) if as_json else to_markdown(result))
    if result.grounding_summary.get(Grounding.NOT_CHECKED.value):
        db = CorpusPaths(out).corpus_db
        why = (f"no corpus database at {db.resolve()}" if not db.exists()
               else f"the corpus at {out.resolve()} holds no usable copy of an Act relied on")
        typer.echo(f"Not checked: {why}. Unchecked wording cannot be relied on.", err=True)
        raise typer.Exit(3)
    if result.grounding_summary.get(Grounding.NOT_FOUND.value):
        raise typer.Exit(3)


@navigate_app.command("assess")
def assess_cmd(
    facts: str = typer.Argument(..., help="Facts JSON file, or - for stdin"),
    out: Path = typer.Option(DEFAULT_CORPUS, "--out", help="Corpus root to ground against"),
    as_of: str = typer.Option(None, "--as-of", help="Assessment date, YYYY-MM-DD (default today)"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of Markdown"),
    evidence: bool = typer.Option(True, "--evidence/--no-evidence"),
    log_level: str = typer.Option("WARNING", "--log-level"),
) -> None:
    """Assess one case. Exit 2 on invalid facts, 3 if any provision is not verified."""
    navlog.configure(log_level)
    today = _parse_as_of(as_of)
    try:
        case = _read_facts(facts)
    except (ValidationError, ValueError, OSError) as exc:
        typer.echo(f"Invalid input: {exc}", err=True)
        raise typer.Exit(2)
    _run(case, today, out, as_json, evidence)


@navigate_app.command("interview")
def interview_cmd(
    out: Path = typer.Option(DEFAULT_CORPUS, "--out", help="Corpus root to ground against"),
    as_of: str = typer.Option(None, "--as-of", help="Assessment date, YYYY-MM-DD (default today)"),
    save: Path = typer.Option(None, "--save", help="Also write the facts to this JSON file"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of Markdown"),
    evidence: bool = typer.Option(True, "--evidence/--no-evidence"),
    log_level: str = typer.Option("WARNING", "--log-level"),
) -> None:
    """Ask plain-language questions, then assess. Same exit codes as assess."""
    navlog.configure(log_level)
    today = _parse_as_of(as_of)
    typer.echo("Answer a few questions. No names or ID numbers are needed.\n")
    try:
        case = build_facts(TerminalAsker())
    except (ValidationError, ValueError) as exc:
        typer.echo(f"Invalid input: {exc}", err=True)
        raise typer.Exit(2)
    if save:
        save.write_text(case.model_dump_json(indent=2, exclude_defaults=True), encoding="utf-8")
        typer.echo(f"Facts saved to {save}", err=True)
    typer.echo("")
    _run(case, today, out, as_json, evidence)


@navigate_app.command("review-sheet")
def review_sheet_cmd(
    cases: Path = typer.Option(DEFAULT_CASES, "--cases", help="Golden cases YAML"),
) -> None:
    """Print the golden-case sheet for a lawyer to sign off. Exit 1 if any case fails."""
    golden = load_cases(cases)
    navigator = Navigator(_quoting_source())
    typer.echo(review_sheet(golden, navigator))
    failures = check(golden, navigator)
    if failures:
        typer.echo(f"{len(failures)} golden case(s) do not match: {', '.join(failures)}",
                   err=True)
        raise typer.Exit(1)


def _quoting_source() -> InMemorySource:
    """A source holding exactly the quoted wording.

    The review sheet is about routing, so it must not depend on which corpus
    is present; `navigate provisions` checks grounding separately.
    """
    source = InMemorySource()
    by_act: dict[str, list[Provision]] = {}
    for provision in PROVISIONS.values():
        by_act.setdefault(provision.act, []).append(provision)
    for act, provisions in by_act.items():
        source.add(act, f"quotes-{act.lower()}", "\n".join(p.quote for p in provisions))
    return source


@navigate_app.command("provisions")
def provisions_cmd(
    out: Path = typer.Option(DEFAULT_CORPUS, "--out", help="Corpus root to ground against"),
) -> None:
    """Check every quoted provision against the held statutes. Exit 3 on any miss."""
    source = corpus_source(out)
    missing = 0
    for provision in PROVISIONS.values():
        ref = ground(provision, source)
        missing += ref.grounding is not Grounding.VERIFIED
        typer.echo(f"{ref.grounding.value:<12} {ref.cite:<22} {ref.doc_id or '-'}")
    if missing:
        typer.echo(f"{missing} of {len(PROVISIONS)} provisions are not verified.", err=True)
        raise typer.Exit(3)


@navigate_app.command("mcp")
def mcp_cmd(
    out: Path = typer.Option(DEFAULT_CORPUS, "--out", help="Corpus root to ground against"),
) -> None:
    """Serve navigator_assess and navigator_provisions over MCP stdio (read-only)."""
    from ..ccma.mcp_server import serve
    from .mcp_server import NavigatorServer

    root = out.resolve()
    serve(root / "db" / "corpus.sqlite", server=NavigatorServer(root))
