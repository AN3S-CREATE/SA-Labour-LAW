"""`labour-corpus ccma ...` -- the CCMA engine's command line.

Each capability is its own command: find, scrape, download, seed, troubleshoot.
`harvest` runs find -> download -> seed -> compile -> report in one go.

Nothing touches the network without --allow-network, nothing persists award
text without --popia-ack, and a live source is fetched only when the operator
has confirmed it in ccma_sources.yaml.

`robots` reads robots.txt for the official-publication sources and nothing
else, so the check can be made and recorded before any content is fetched.
"""

# No `from __future__ import annotations` here: Typer evaluates every command
# signature when it builds the app, and real annotation objects spare it
# parsing each string again.
import json
from pathlib import Path

import typer

from ..config import ConfigError, build_user_agent
from .allowlist import SourceKind, load_allowlist
from .catalogue import Catalogue
from .harvest import (ALL_STAGES, DEFAULT_CONCURRENCY, DEFAULT_LIMIT, RETRY_WAITS, TIMEOUT_SECONDS,
                      HarvestOptions, Harvester)
from .paths import CcmaPaths

CONTACT_ENV = "LABOUR_CORPUS_CONTACT_EMAIL"
_NEED_CONTACT = f"--contact-email (or {CONTACT_ENV}) is required with --allow-network"

ccma_app = typer.Typer(
    name="ccma",
    help=(
        "CCMA Corpus Engine: find, scrape, download, seed and troubleshoot published CCMA "
        "material, derive case cards / scenarios / strategies, and serve them over a local "
        "MCP server. Research tool; not legal advice."
    ),
    no_args_is_help=True,
    add_completion=False,
)

RootOpt = typer.Option(Path("."), "--root", help="Project root holding data/, reports/, logs/.")
AllowlistOpt = typer.Option(None, "--allowlist", help="Allow-list YAML (default: ccma_sources.yaml).")
SourceOpt = typer.Option(
    None, "--source", "-s",
    help="Source id, or a source type such as official_publication; repeat for several. Default: all.",
)
LimitOpt = typer.Option(DEFAULT_LIMIT, "--limit", "-n", min=1, help="Records per source (default 50).")
DryRunOpt = typer.Option(False, "--dry-run", help="Walk listings and report would-fetch URLs; save nothing.")
NetOpt = typer.Option(False, "--allow-network", help="Permit HTTP GETs to allow-listed hosts this run.")
PopiaOpt = typer.Option(
    False, "--popia-ack",
    help="Confirm the POPIA lawful basis is recorded and minimisation rules are accepted.",
)
EmailOpt = typer.Option(None, "--contact-email", envvar=CONTACT_ENV,
                        help=f"Operator contact carried in the User-Agent (or set {CONTACT_ENV}).")
YearsOpt = typer.Option(None, "--years", help="YYYY-YYYY window for year-indexed sources.")
ConcOpt = typer.Option(DEFAULT_CONCURRENCY, "--concurrency", min=1, max=8,
                       help="Concurrent fetches (default 2).")
CorpusOpt = typer.Option(None, "--corpus", help="Main corpus root for the local_corpus source.")


def _years(raw: str | None) -> tuple[int, int] | None:
    if not raw:
        return None
    from ..config import parse_year_range

    return parse_year_range(raw)


def _run(stages: frozenset[str], *, root: Path, allowlist: Path | None, source: list[str] | None,
         limit: int, dry_run: bool, allow_network: bool, popia_ack: bool,
         contact_email: str | None, years: str | None, concurrency: int,
         corpus: Path | None) -> None:
    try:
        if allow_network:
            if not contact_email:
                raise ConfigError(_NEED_CONTACT)
            build_user_agent(contact_email)
        options = HarvestOptions(
            root=root.resolve(), source_ids=list(source or []), limit=limit, dry_run=dry_run,
            allow_network=allow_network, popia_ack=popia_ack, contact_email=contact_email,
            concurrency=concurrency, years=_years(years), corpus_root=corpus, stages=stages,
        )
        result = Harvester(load_allowlist(allowlist), options, out=typer.echo).run()
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(64) from exc
    for s in result.sources:
        state = "attempted" if s.attempted else f"skipped ({s.refusal})"
        typer.echo(f"{s.source_id}: {state} found={s.found} downloaded={s.downloaded} "
                   f"seeded={s.seeded} skipped={s.skipped} failed={s.failed}")
    if result.scenarios:
        typer.echo(f"scenarios compiled: {result.scenarios}")
    typer.echo(f"report: {result.report_path}")
    if result.exit_code:
        typer.echo("live run found no records on any attempted source (exit 2)", err=True)
    raise typer.Exit(result.exit_code)


@ccma_app.command("sources")
def sources_cmd(allowlist: Path = AllowlistOpt) -> None:
    """List the allow-list with observed status and whether each can be harvested."""
    registry = load_allowlist(allowlist)
    for s in registry.sources:
        live = "live" if s.is_live else "local"
        typer.echo(f"{s.id:<28} {s.kind.value:<21} {live:<5} {s.status.value:<26} "
                   f"{s.record_kind.value:<18} {'enabled' if s.enabled else 'disabled':<8} "
                   f"{s.confirmation.value:<21} verified {s.verified_on or '-'}")


@ccma_app.command("robots")
def robots_cmd(allowlist: Path = AllowlistOpt, source: list[str] = SourceOpt,
               allow_network: bool = NetOpt, contact_email: str = EmailOpt,
               root: Path = RootOpt) -> None:
    """Read robots.txt for every allow-listed path of the enabled official publications.

    Fetches robots.txt and nothing else, and only for enabled
    official_publication sources. Exit 3 when any path is refused. The result
    goes to the audit log; recording it in ccma_sources.yaml and the source map
    is the operator's step.
    """
    from ..http import Fetcher, RateLimiter
    from ..observability import JsonlLogger
    from .scrape import LiveGetter, robots_preflight

    registry = load_allowlist(allowlist)
    if not allow_network:
        typer.echo("refused: network access not granted for this run (--allow-network)", err=True)
        raise typer.Exit(3)
    if not contact_email:
        typer.echo(f"error: {_NEED_CONTACT}", err=True)
        raise typer.Exit(64)
    paths = CcmaPaths(root.resolve())
    paths.ensure()
    audit = JsonlLogger(paths.audit_log)
    refused = 0
    with Fetcher(build_user_agent(contact_email), timeout=TIMEOUT_SECONDS,
                 limiter=RateLimiter(1.0, 2.0), tls_cache_dir=paths.data / "tls",
                 retry_waits=RETRY_WAITS) as fetcher:
        for src in registry.select(source or None):
            if src.kind is not SourceKind.OFFICIAL_PUBLICATION:
                typer.echo(f"{src.id}: not checked (robots covers official_publication sources only)")
                continue
            if not src.enabled:
                typer.echo(f"{src.id}: not checked (disabled in the allow-list)")
                continue
            note = registry.notes_refusal(src)
            if note:
                typer.echo(f"{src.id}: REFUSED before any request: {note}")
                refused += 1
                continue
            for verdict in robots_preflight(LiveGetter(fetcher, src), src):
                state = "allowed" if verdict.allowed else "REFUSED"
                typer.echo(f"{src.id}: {state} {verdict.url} "
                           f"(robots.txt HTTP {verdict.robots_status}) -- {verdict.reason}")
                audit.write(decision="robots_check", reason=verdict.reason, source=src.id,
                            url=verdict.url, allowed=verdict.allowed,
                            robots_status=verdict.robots_status)
                refused += 0 if verdict.allowed else 1
    raise typer.Exit(3 if refused else 0)


@ccma_app.command("find")
def find_cmd(root: Path = RootOpt, allowlist: Path = AllowlistOpt, source: list[str] = SourceOpt,
             limit: int = LimitOpt, dry_run: bool = DryRunOpt, allow_network: bool = NetOpt,
             popia_ack: bool = PopiaOpt, contact_email: str = EmailOpt, years: str = YearsOpt,
             corpus: Path = CorpusOpt) -> None:
    """Discover record URLs from allow-listed indexes (records them in the catalogue)."""
    _run(frozenset({"find"}), root=root, allowlist=allowlist, source=source, limit=limit,
         dry_run=dry_run, allow_network=allow_network, popia_ack=popia_ack,
         contact_email=contact_email, years=years, concurrency=1, corpus=corpus)


@ccma_app.command("download")
def download_cmd(root: Path = RootOpt, allowlist: Path = AllowlistOpt, source: list[str] = SourceOpt,
                 limit: int = LimitOpt, allow_network: bool = NetOpt, popia_ack: bool = PopiaOpt,
                 contact_email: str = EmailOpt, concurrency: int = ConcOpt,
                 corpus: Path = CorpusOpt) -> None:
    """Fetch and store records found by an earlier `ccma find` (checksummed; no seeding)."""
    _run(frozenset({"download"}), root=root, allowlist=allowlist, source=source, limit=limit,
         dry_run=False, allow_network=allow_network, popia_ack=popia_ack,
         contact_email=contact_email, years=None, concurrency=concurrency, corpus=corpus)


@ccma_app.command("seed")
def seed_cmd(root: Path = RootOpt, allowlist: Path = AllowlistOpt, source: list[str] = SourceOpt,
             limit: int = LimitOpt, popia_ack: bool = PopiaOpt, corpus: Path = CorpusOpt) -> None:
    """Parse stored artefacts into the catalogue and knowledge units. No network."""
    _run(frozenset({"seed"}), root=root, allowlist=allowlist, source=source, limit=limit,
         dry_run=False, allow_network=False, popia_ack=popia_ack, contact_email=None,
         years=None, concurrency=1, corpus=corpus)


@ccma_app.command("harvest")
def harvest_cmd(root: Path = RootOpt, allowlist: Path = AllowlistOpt, source: list[str] = SourceOpt,
                limit: int = LimitOpt, dry_run: bool = DryRunOpt, allow_network: bool = NetOpt,
                popia_ack: bool = PopiaOpt, contact_email: str = EmailOpt, years: str = YearsOpt,
                concurrency: int = ConcOpt, corpus: Path = CorpusOpt) -> None:
    """find -> download -> seed -> compile scenarios -> write the run report."""
    _run(ALL_STAGES, root=root, allowlist=allowlist, source=source, limit=limit, dry_run=dry_run,
         allow_network=allow_network, popia_ack=popia_ack, contact_email=contact_email,
         years=years, concurrency=concurrency, corpus=corpus)


@ccma_app.command("scrape")
def scrape_cmd(url: str = typer.Argument(..., help="One record URL on an allow-listed host."),
               source: str = typer.Option(..., "--source", "-s"),
               allowlist: Path = AllowlistOpt, allow_network: bool = NetOpt,
               contact_email: str = EmailOpt, root: Path = RootOpt) -> None:
    """Fetch and parse one record page and print what was read. Saves nothing."""
    from ..http import Fetcher, RateLimiter
    from .scrape import LiveGetter, inspect_record

    registry = load_allowlist(allowlist)
    src = registry.get(source)
    refusal = src.live_refusal(allow_network=allow_network, popia_ack=True, dry_run=True)
    if not refusal and src.needs_robots_preflight:
        refusal = registry.notes_refusal(src)
    if refusal:
        typer.echo(f"refused: {refusal}", err=True)
        raise typer.Exit(3)
    if not contact_email:
        typer.echo(f"error: {_NEED_CONTACT}", err=True)
        raise typer.Exit(64)
    paths = CcmaPaths(root.resolve())
    with Fetcher(build_user_agent(contact_email), timeout=TIMEOUT_SECONDS,
                 limiter=RateLimiter(src.min_delay, src.max_delay),
                 tls_cache_dir=paths.data / "tls", retry_waits=RETRY_WAITS) as fetcher:
        page, parsed = inspect_record(LiveGetter(fetcher, src), src, url)
    typer.echo(json.dumps({
        "url": page.final_url, "status": page.status, "sha256": page.sha256,
        "parse_status": parsed.parse_status, "case_number": parsed.meta.case_no,
        "neutral_citation": parsed.meta.neutral_citation, "date": parsed.meta.date_delivered,
        "dispute_type": parsed.dispute_type, "outcome_label": parsed.outcome_label,
        "notes": parsed.notes,
    }, indent=2))


@ccma_app.command("compile")
def compile_cmd(root: Path = RootOpt) -> None:
    """Rebuild scenarios from case cards and export knowledge JSON files."""
    from .seed import compile_scenarios, export_knowledge

    paths = CcmaPaths(root.resolve())
    with Catalogue(paths.catalogue) as catalogue:
        scenarios = compile_scenarios(catalogue)
        written = export_knowledge(catalogue, paths)
    typer.echo(f"scenarios: {len(scenarios)}; knowledge files written: {written}")


@ccma_app.command("troubleshoot")
def troubleshoot_cmd(root: Path = RootOpt) -> None:
    """Classify outstanding failures, show remedies, check disk, scan for secrets."""
    from .troubleshoot import diagnose

    paths = CcmaPaths(root.resolve())
    if not paths.catalogue.exists():
        typer.echo("no catalogue yet; run `labour-corpus ccma harvest --dry-run` first")
        raise typer.Exit(0)
    with Catalogue(paths.catalogue) as catalogue:
        typer.echo(diagnose(catalogue, paths))


@ccma_app.command("mcp")
def mcp_cmd(root: Path = RootOpt) -> None:
    """Serve the catalogue over MCP on stdio (read-only)."""
    from .mcp_server import serve

    serve(CcmaPaths(root.resolve()).catalogue)
