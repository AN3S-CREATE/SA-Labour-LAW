"""Command line interface.

Stages are separate commands so each one is re-runnable on its own:
crawl, parse, index, search, report, ingest-inbox, smoke, sources.

No ``from __future__ import annotations`` here: Typer evaluates every string
annotation of every command on each invocation, which made even ``--help``
cost about a second. Real annotations are read without evaluation.
"""

import hashlib
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from .config import (
    DISK_WARN_BYTES,
    ConfigError,
    CorpusPaths,
    CrawlSettings,
    PaidSourceError,
    build_user_agent,
    parse_year_range,
)
from .http import FetchResult, Fetcher, RateLimiter
from .index.fts import FtsIndex
from .models import DocType, Gap, ParsedDoc, Provenance, TextStatus
from .observability import RunLog
from .ontology.dismissal import OntologySchema
from .ontology.mining import overlay_to_dict
from .pipeline import build_parsed_doc, load_ocr_text, persist, save_ocr_text, store_raw
from .report.coverage import write_coverage
from .sources.crawler import Crawler, preflight_gaps
from .sources.inbox import README_TEXT, ingest_inbox
from .sources.registry import Access, SourceDef, default_registry_path, load_registry
from .state import CorpusStore
from .ccma.cli import ccma_app
from .navigator.cli import navigate_app

app = typer.Typer(
    name="labour-corpus",
    help=(
        "Local-first harvester for South African labour-law primary sources, "
        "with an unfair-dismissal ontology and a mining overlay.\n\n"
        "This tool builds a research corpus. It is not a legal practitioner and "
        "it does not give legal advice."
    ),
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(ccma_app, name="ccma")
app.add_typer(navigate_app, name="navigate")

def _make_console() -> Console:
    """Build a console that survives a legacy Windows code page.

    A default Windows terminal is cp1252, which cannot encode the spinner and
    box-drawing glyphs rich emits. Reconfiguring the streams to UTF-8 keeps the
    output readable; if that is not possible, fall back to ASCII-safe output
    rather than crashing the run.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    safe = encoding.replace("-", "") not in {"utf8", "utf8mb4"}
    return Console(legacy_windows=safe or None, safe_box=True)


console = _make_console()

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------
def _settings(
    out: Path,
    contact_email: str,
    years: str | None,
    sources: str | None,
    max_docs: int | None,
    acknowledged: bool = False,
) -> CrawlSettings:
    paths = CorpusPaths(root=Path(out).expanduser().resolve())
    paths.ensure()
    source_ids = [s.strip() for s in (sources or "").split(",") if s.strip()]
    return CrawlSettings(
        contact_email=contact_email,
        paths=paths,
        years=parse_year_range(years),
        source_ids=source_ids,
        max_docs_per_source=max_docs,
        acknowledged_public_sources=acknowledged,
    )


def _write_ontology(paths: CorpusPaths) -> None:
    import orjson

    paths.ontology.mkdir(parents=True, exist_ok=True)
    (paths.ontology / "dismissal_schema.json").write_bytes(
        orjson.dumps(OntologySchema().to_dict(), option=orjson.OPT_INDENT_2)
    )
    (paths.ontology / "mining_overlay.json").write_bytes(
        orjson.dumps(overlay_to_dict(), option=orjson.OPT_INDENT_2)
    )


def _write_inbox_readme(paths: CorpusPaths) -> None:
    paths.inbox.mkdir(parents=True, exist_ok=True)
    readme = paths.inbox / "README.md"
    if not readme.exists():
        readme.write_text(README_TEXT, encoding="utf-8")


def _corpus_bytes(paths: CorpusPaths) -> int:
    return sum(p.stat().st_size for p in paths.root.rglob("*") if p.is_file())


def stale_artefact_warning(artefact: Path, store: CorpusStore, what: str) -> str | None:
    """Return a warning when a derived artefact predates the corpus, else None.

    The index and the coverage report are built from the database. If a parse
    or crawl wrote after they were built, they silently describe an older
    corpus -- a search can miss documents that are present, and nothing says so.

    Returning the message rather than printing it keeps the decision testable
    apart from the console.
    """
    import datetime

    last_write = store.last_document_write()
    if not last_write or not artefact.exists():
        return None
    built = datetime.datetime.fromtimestamp(
        artefact.stat().st_mtime, datetime.timezone.utc
    ).isoformat(timespec="seconds")
    if built >= last_write:
        return None
    return (
        f"the {what} was built at {built}, before the most recent document "
        f"write at {last_write}. It describes an older corpus. Re-run "
        f"`labour-corpus index` and `labour-corpus report`."
    )


def _warn_if_stale(artefact: Path, store: CorpusStore, what: str) -> None:
    message = stale_artefact_warning(artefact, store, what)
    if message:
        console.print(f"[yellow]warning:[/yellow] {message}")


def _warn_if_volatile(paths: CorpusPaths) -> None:
    """Tell the operator when the corpus sits somewhere the OS may clean."""
    if paths.is_volatile:
        console.print(f"[yellow]warning:[/yellow] {paths.volatile_warning()}")


def _fail(message: str, code: int = EXIT_USAGE) -> None:
    console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code)


# ---------------------------------------------------------------------------
# crawl
# ---------------------------------------------------------------------------
@app.command()
def crawl(
    out: Path = typer.Option(Path("./data/corpus"), "--out", help="Corpus root."),
    contact_email: str = typer.Option(..., "--contact-email", help="Operator contact address, sent in the User-Agent."),
    years: str = typer.Option(None, "--years", help="Case-law window as YYYY-YYYY. Default: last 15 years."),
    sources: str = typer.Option(None, "--sources", help="Comma-separated source ids, or 'all_public'."),
    max_docs_per_source: int = typer.Option(None, "--max-docs-per-source", help="Cap documents per source."),
    registry_path: Path = typer.Option(None, "--registry", help="Path to sources.yaml."),
    i_understand_public_sources: bool = typer.Option(
        False, "--i-understand-public-sources",
        help="Required before the first live crawl. Confirms you are collecting public sources for a lawful purpose.",
    ),
) -> None:
    """Fetch, parse and store documents from the configured public sources."""
    if sources is not None and not sources.strip():
        _fail("--sources was supplied but empty. Give source ids or omit the flag.", EXIT_USAGE)

    try:
        settings = _settings(out, contact_email, years, sources, max_docs_per_source,
                             acknowledged=i_understand_public_sources)
        registry = load_registry(registry_path or default_registry_path())
        selected = registry.select(settings.source_ids)
    except (ConfigError, PaidSourceError) as exc:
        _fail(str(exc), EXIT_USAGE)
        return

    if not selected:
        _fail("no sources selected", EXIT_USAGE)

    if not i_understand_public_sources:
        _fail(
            "first live crawl requires --i-understand-public-sources. "
            "It confirms you are collecting public sources for a lawful purpose "
            "and that the crawler identifies itself with your contact address.",
            EXIT_USAGE,
        )

    _warn_if_volatile(settings.paths)
    _write_ontology(settings.paths)
    _write_inbox_readme(settings.paths)

    log = RunLog(settings.paths.run_log, settings.paths.error_log)
    log.event("crawl_start", sources=[s.id for s in selected],
              years=list(settings.years), user_agent=settings.user_agent)

    console.print(f"[bold]User-Agent:[/bold] {settings.user_agent}")
    console.print(f"[bold]Corpus:[/bold] {settings.paths.root}")
    console.print(f"[bold]Years:[/bold] {settings.years[0]}-{settings.years[1]}\n")

    store = CorpusStore(settings.paths.corpus_db)
    fetcher = Fetcher(
        settings.user_agent,
        timeout=settings.timeout,
        limiter=RateLimiter(settings.min_delay, settings.max_delay),
        on_event=lambda e: log.run.write(**e),
        tls_cache_dir=settings.paths.tls_cache,
    )
    crawler = Crawler(settings, fetcher, store, log)

    interrupted = False
    try:
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("{task.completed}/{task.total} sources"),
            TimeElapsedColumn(), console=console,
        ) as progress:
            task = progress.add_task("crawling", total=len(selected))
            for source in selected:
                progress.update(task, description=f"{source.id} ({source.host})")

                for gap in preflight_gaps(source):
                    store.record_gap(gap)

                if source.access is not Access.OPEN:
                    console.print(
                        f"  [yellow]skip[/yellow] {source.id}: {source.access.value} "
                        "(recorded as a gap)"
                    )
                    progress.advance(task)
                    continue

                outcome = crawler.crawl_source(source)
                console.print(
                    f"  [green]{source.id}[/green]: stored={outcome.stored} "
                    f"dup={outcome.skipped_duplicate} failed={outcome.failed} "
                    f"gaps={len(outcome.gaps)}"
                    + (f" [dim]({outcome.stopped_reason})[/dim]" if outcome.stopped_reason else "")
                )
                progress.advance(task)
    except KeyboardInterrupt:
        interrupted = True
        console.print("\n[yellow]interrupted; state flushed. Re-run to resume.[/yellow]")
    finally:
        log.event("crawl_end", **log.summary())
        fetcher.close()
        size = _corpus_bytes(settings.paths)
        store.close()

    if size > DISK_WARN_BYTES:
        console.print(f"[yellow]corpus is {size / 1e9:.2f} GB[/yellow]")

    console.print(f"\n[bold]summary:[/bold] {log.summary()}")
    if interrupted:
        raise typer.Exit(EXIT_INTERRUPTED)


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------
OCR_REUSED_NOTE = (
    "text reused from a stored OCR transcription (raw/.../ocr.txt); machine "
    "transcription, not published text"
)


def _apply_recorded_provenance(doc: ParsedDoc, meta: dict) -> None:
    """Carry the provenance fetch_meta.json recorded onto a re-parsed doc."""
    recorded = meta.get("provenance")
    if recorded:
        try:
            doc.provenance = Provenance(recorded)
        except ValueError:
            pass


def _rebuild_from_transcription(
    meta: dict,
    result: FetchResult,
    source: SourceDef | None,
    doc_type: DocType,
    forum: str | None,
    transcription: str,
    extra_notes: list[str],
) -> tuple[ParsedDoc, str]:
    """Re-derive a scanned document from its OCR transcription.

    The first pass over a scan computes tags, outcome, draft status, language,
    quotes and parse status from an empty text layer. Swapping in only the
    text would leave every one of them describing nothing, so the whole
    document is rebuilt from the transcription. It is passed unredacted:
    build_parsed_doc redacts it.
    """
    doc, text = build_parsed_doc(
        source_id=meta.get("source_id", "unknown"),
        forum=forum,
        result=result,
        default_doc_type=doc_type,
        license_note=source.license_note if source else "",
        text_override=transcription,
    )
    _apply_recorded_provenance(doc, meta)
    doc.ocr_applied = True
    # OCR text is a transcription, so confidence is capped.
    doc.parser_confidence = min(doc.parser_confidence, 0.5)
    doc.parser_notes.extend(extra_notes)
    return doc, text


@app.command()
def parse(
    out: Path = typer.Option(Path("./data/corpus"), "--out", help="Corpus root."),
    registry_path: Path = typer.Option(None, "--registry", help="Path to sources.yaml."),
    ocr: bool = typer.Option(
        False, "--ocr",
        help="Also OCR scanned PDFs with a local tesseract. Opt-in; nothing leaves the machine.",
    ),
    ocr_language: str = typer.Option("eng", "--ocr-language", help="tesseract language code."),
    ocr_dpi: int = typer.Option(
        300, "--ocr-dpi",
        help="Render resolution for OCR. Lower is faster; 200-220 is usually enough for clean printed text.",
    ),
) -> None:
    """Re-parse everything already stored under raw/ without re-fetching.

    With --ocr, documents flagged ocr_needed are additionally rendered and read
    by a local tesseract. OCR text is marked as a machine transcription in the
    document's parser notes.
    """
    import orjson

    paths = CorpusPaths(root=Path(out).expanduser().resolve())
    paths.ensure()
    _warn_if_volatile(paths)
    registry = load_registry(registry_path or default_registry_path())
    log = RunLog(paths.run_log, paths.error_log)
    store = CorpusStore(paths.corpus_db)

    metas = sorted(paths.raw.rglob("fetch_meta.json"))
    if not metas:
        console.print("[yellow]nothing under raw/ to parse[/yellow]")
        store.close()
        return

    reparsed = 0
    failed = 0
    ocr_done = 0
    ocr_failed = 0
    ocr_reused = 0

    if ocr:
        from .parse.ocr import ocr_toolchain_status

        status = ocr_toolchain_status()
        if not status["ready"]:
            console.print(
                "[yellow]--ocr requested but the toolchain is incomplete:[/yellow] "
                f"tesseract={'yes' if status['tesseract_available'] else 'NO'}, "
                f"pymupdf={'yes' if status['page_renderer_available'] else 'NO'}"
            )
            console.print(
                "[dim]Scanned documents will keep their ocr_needed status.[/dim]"
            )
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
                  TextColumn("{task.completed}/{task.total}"), console=console) as progress:
        task = progress.add_task("parsing", total=len(metas))
        for meta_path in metas:
            progress.advance(task)
            try:
                meta = orjson.loads(meta_path.read_bytes())
            except (ValueError, OSError) as exc:
                failed += 1
                log.error("parse", f"unreadable fetch_meta: {exc}", url=str(meta_path))
                continue

            url = str(meta_path)
            try:
                url = str(meta.get("url") or url)
                bodies = [p for p in meta_path.parent.glob("source.*")]
                if not bodies:
                    continue
                body_path = bodies[0]

                source = registry.by_id(meta.get("source_id", ""))
                try:
                    headers = orjson.loads((meta_path.parent / "headers.json").read_bytes())
                except (ValueError, OSError):
                    headers = {}

                result = FetchResult(
                    url=meta.get("url", ""),
                    final_url=meta.get("final_url", meta.get("url", "")),
                    status=int(meta.get("http_status") or 200),
                    headers=headers,
                    body=body_path.read_bytes(),
                    content_sha256=meta.get("content_sha256", ""),
                    bytes=int(meta.get("bytes") or 0),
                    elapsed_ms=0,
                )
                # fetch_meta.json is authoritative when it carries the hints, so
                # a document whose source is not a crawl target still re-parses
                # correctly. The registry is the fallback.
                try:
                    recorded_type = meta.get("doc_type")
                    doc_type = DocType(recorded_type) if recorded_type else (
                        source.doc_type if source else DocType.OTHER
                    )
                except ValueError:
                    doc_type = source.doc_type if source else DocType.OTHER

                forum = meta.get("forum") or (source.forum if source else None)

                doc, text = build_parsed_doc(
                    source_id=meta.get("source_id", "unknown"),
                    forum=forum,
                    result=result,
                    default_doc_type=doc_type,
                    license_note=source.license_note if source else "",
                )
                _apply_recorded_provenance(doc, meta)

                # A scan yields no text layer on re-extraction. If a previous
                # run already OCR'd it, reuse that transcription rather than
                # dropping back to ocr_needed and losing the text (D-014).
                # Also when the PDF reader now fails on every page (empty): a
                # sidecar beside these exact bytes proves OCR already read
                # them, whatever the reader's trouble is.
                ocr_event = None
                if doc.text_status in (TextStatus.OCR_NEEDED.value, TextStatus.EMPTY.value):
                    saved = load_ocr_text(meta_path.parent)
                    if saved and saved.strip():
                        doc, text = _rebuild_from_transcription(
                            meta, result, source, doc_type, forum, saved,
                            [OCR_REUSED_NOTE],
                        )
                        ocr_event = "reused"

                # Still a scan and --ocr was asked for: read it locally.
                if ocr and doc.text_status == TextStatus.OCR_NEEDED.value:
                    from .parse.ocr import ocr_pdf

                    outcome = ocr_pdf(result.body or b"", language=ocr_language, dpi=ocr_dpi)
                    ocr_notes = [outcome.note, *outcome.notes]
                    if outcome.ok:
                        # Persist the unredacted transcription beside the scan
                        # first: it is the expensive part and cannot be redone
                        # from anything else.
                        save_ocr_text(meta_path.parent, outcome.text)
                        doc, text = _rebuild_from_transcription(
                            meta, result, source, doc_type, forum, outcome.text,
                            ocr_notes,
                        )
                        ocr_event = "done"
                    else:
                        doc.parser_notes.extend(ocr_notes)
                        ocr_event = "failed"

                persist(paths, store, doc, text)
                reparsed += 1
                if ocr_event == "reused":
                    ocr_reused += 1
                elif ocr_event == "done":
                    ocr_done += 1
                elif ocr_event == "failed":
                    ocr_failed += 1
            except Exception as exc:  # noqa: BLE001 -- third-party parsers fail broadly on corrupt input (D-008)
                # One malformed document must never end a re-parse: documents
                # are processed in sorted order with no checkpoint, so a single
                # bad file would stop every later run at the same place.
                # KeyboardInterrupt is not an Exception and still stops the run.
                failed += 1
                log.error("parse", f"{type(exc).__name__}: {exc}", url=url)

    store.close()
    console.print(f"re-parsed {reparsed}, failed {failed}")
    if ocr_reused:
        console.print(f"reused {ocr_reused} stored OCR transcription(s)")
    if ocr:
        console.print(f"OCR: {ocr_done} recovered, {ocr_failed} still unreadable")


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------
@app.command()
def index(
    out: Path = typer.Option(Path("./data/corpus"), "--out", help="Corpus root."),
) -> None:
    """Rebuild the FTS5 full-text index over the corpus."""
    paths = CorpusPaths(root=Path(out).expanduser().resolve())
    paths.ensure()
    store = CorpusStore(paths.corpus_db)
    with FtsIndex(paths.fts_db) as fts:
        count = fts.rebuild(paths, store)
    store.close()
    console.print(f"indexed {count} documents into {paths.fts_db}")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
@app.command()
def search(
    query: str = typer.Argument(..., help="FTS5 query, e.g. 'section 189 meaningful consultation mine'."),
    out: Path = typer.Option(Path("./data/corpus"), "--out", help="Corpus root."),
    limit: int = typer.Option(10, "--limit", help="Maximum hits."),
) -> None:
    """Search the local corpus. Returns only documents that are on disk."""
    paths = CorpusPaths(root=Path(out).expanduser().resolve())
    if not paths.fts_db.exists():
        console.print("[yellow]no index yet; run `labour-corpus index` first[/yellow]")
        raise typer.Exit(EXIT_OK)

    store = CorpusStore(paths.corpus_db)
    _warn_if_stale(paths.fts_db, store, "search index")
    with FtsIndex(paths.fts_db) as fts:
        hits = fts.search(query, store, limit=limit)
    store.close()

    if not hits:
        console.print("0 hits")
        console.print(
            "[dim]The corpus contains no matching document. This is not evidence "
            "that no such case exists.[/dim]"
        )
        raise typer.Exit(EXIT_OK)

    table = Table(title=f"{len(hits)} hit(s) for {query!r}", show_lines=True)
    table.add_column("Citation / title", style="bold", max_width=42)
    table.add_column("Forum", max_width=12)
    table.add_column("Snippet", max_width=60)
    table.add_column("URL", style="dim", max_width=40)
    for hit in hits:
        table.add_row(
            hit.citation or hit.title or hit.doc_id,
            hit.forum or "-",
            hit.snippet or "-",
            hit.url,
        )
    console.print(table)
    console.print(
        "[dim]Confirm every citation against the original source file before use.[/dim]"
    )


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
@app.command()
def report(
    out: Path = typer.Option(Path("./data/corpus"), "--out", help="Corpus root."),
    contact_email: str = typer.Option("not-recorded@example.invalid", "--contact-email"),
    years: str = typer.Option(None, "--years", help="Case-law window as YYYY-YYYY."),
    registry_path: Path = typer.Option(None, "--registry", help="Path to sources.yaml."),
) -> None:
    """Write reports/COVERAGE.md describing what is and is not in the corpus."""
    paths = CorpusPaths(root=Path(out).expanduser().resolve())
    paths.ensure()
    registry = load_registry(registry_path or default_registry_path())
    store = CorpusStore(paths.corpus_db)
    _write_ontology(paths)
    write_coverage(
        paths, store, registry,
        years=parse_year_range(years), contact_email=contact_email,
    )
    store.close()
    console.print(f"wrote {paths.coverage_md}")


# ---------------------------------------------------------------------------
# ingest-inbox
# ---------------------------------------------------------------------------
@app.command(name="ingest-inbox")
def ingest_inbox_cmd(
    out: Path = typer.Option(Path("./data/corpus"), "--out", help="Corpus root."),
    limit: int = typer.Option(None, "--limit", help="Maximum files to ingest."),
) -> None:
    """Parse operator-supplied documents from inbox/ into the corpus."""
    paths = CorpusPaths(root=Path(out).expanduser().resolve())
    paths.ensure()
    _write_inbox_readme(paths)
    log = RunLog(paths.run_log, paths.error_log)
    store = CorpusStore(paths.corpus_db)
    outcome = ingest_inbox(paths, store, log, limit=limit)
    store.close()

    console.print(
        f"scanned {outcome.scanned}, stored {outcome.stored}, "
        f"duplicate {outcome.skipped_duplicate}, failed {outcome.failed}"
    )
    for note in outcome.notes:
        console.print(f"[dim]{note}[/dim]")


# ---------------------------------------------------------------------------
# import-citation-map
# ---------------------------------------------------------------------------
@app.command(name="import-citation-map")
def import_citation_map_cmd(
    source: Path = typer.Option(..., "--from", help="Legacy knowledge-base JSONL to read."),
    out: Path = typer.Option(Path("./data/corpus"), "--out", help="Corpus root."),
) -> None:
    """Import judgment references from previously harvested court indexes.

    This records what labour case law is known to EXIST. A row is a reference,
    never a held document, so the coverage report can state how much of the
    known universe the corpus actually contains.
    """
    from .sources.citation_map import collect_citations

    jsonl = Path(source).expanduser()
    if not jsonl.exists():
        _fail(f"source not found: {jsonl}", EXIT_USAGE)

    paths = CorpusPaths(root=Path(out).expanduser().resolve())
    paths.ensure()
    store = CorpusStore(paths.corpus_db)
    log = RunLog(paths.run_log, paths.error_log)

    console.print(f"reading {jsonl}")
    with console.status("parsing court index listings..."):
        citations, summary = collect_citations(jsonl)

    added = 0
    for citation in citations.values():
        if store.upsert_known_citation(
            neutral_citation=citation.neutral_citation,
            court=citation.court,
            year=citation.year,
            number=citation.number,
            case_name=citation.case_name,
            case_no=citation.case_no,
            date_delivered=citation.date_delivered,
            parallel_citations=citation.parallel_citations,
            saflii_url=citation.saflii_url,
            source_note=citation.source_note,
        ):
            added += 1
    store.conn.commit()
    held = store.link_held_citations()
    known = store.count_known_citations()

    log.event(
        "import_citation_map",
        source=str(jsonl), known=known, added=added, held=held,
        scanned=summary.scanned_records, excluded_non_sa=summary.excluded_non_sa,
    )

    table = Table(title="Known labour judgments by forum")
    table.add_column("Forum", style="bold")
    table.add_column("Years")
    table.add_column("Known", justify="right")
    table.add_column("Held", justify="right")
    table.add_column("Coverage", justify="right")
    for row in store.known_citation_coverage():
        pct = (row["held"] / row["known"] * 100) if row["known"] else 0.0
        table.add_row(
            row["court"], f"{row['first_year']}-{row['last_year']}",
            str(row["known"]), str(row["held"]), f"{pct:.1f}%",
        )
    store.close()

    console.print(table)
    console.print(
        f"scanned {summary.scanned_records} legacy records, "
        f"{summary.labour_records} labour index pages, "
        f"{summary.excluded_non_sa} non-SA records excluded"
    )
    console.print(f"[bold]{known}[/bold] known judgments ({added} new), [bold]{held}[/bold] held")
    console.print(
        "[dim]These are references read from published indexes. A reference is "
        "not a document, and the URL is derived from the citation.[/dim]"
    )


# ---------------------------------------------------------------------------
# import-legacy-documents
# ---------------------------------------------------------------------------
@app.command(name="import-legacy-documents")
def import_legacy_documents_cmd(
    source: Path = typer.Option(..., "--from", help="Legacy knowledge-base JSONL to read."),
    out: Path = typer.Option(Path("./data/corpus"), "--out", help="Corpus root."),
    judgments: bool = typer.Option(True, "--judgments/--no-judgments", help="Import judgments."),
    legislation: bool = typer.Option(True, "--legislation/--no-legislation", help="Import consolidated Acts."),
    limit: int = typer.Option(None, "--limit", help="Maximum documents to import."),
) -> None:
    """Import documents from an earlier local harvest the operator already holds.

    Text is parsed with the same ontology and redaction as crawled material, but
    provenance is recorded as legacy_local_harvest so nothing implies this
    crawler fetched it. Listing pages, court rolls and non-SA databases are
    excluded.
    """
    import hashlib as _hashlib

    from .models import Provenance
    from .sources.legacy_import import (
        LEGACY_LICENSE_NOTE,
        LEGACY_SOURCE_ID,
        forum_for,
        legacy_parser_notes,
        scan_legacy_corpus,
    )

    jsonl = Path(source).expanduser()
    if not jsonl.exists():
        _fail(f"source not found: {jsonl}", EXIT_USAGE)

    paths = CorpusPaths(root=Path(out).expanduser().resolve())
    paths.ensure()
    store = CorpusStore(paths.corpus_db)
    log = RunLog(paths.run_log, paths.error_log)

    console.print(f"reading {jsonl}")
    with console.status("classifying legacy records..."):
        docs, scan = scan_legacy_corpus(
            jsonl, include_judgments=judgments, include_legislation=legislation
        )

    stored = 0
    skipped = 0
    failed = 0

    with Progress(
        SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
        TextColumn("{task.completed}/{task.total}"), console=console,
    ) as progress:
        task = progress.add_task("importing", total=min(len(docs), limit or len(docs)))
        for doc in docs:
            if limit is not None and stored >= limit:
                break
            progress.advance(task)

            body = doc.text.encode("utf-8")
            if store.is_unchanged(doc.canonical_url, doc.content_sha256):
                skipped += 1
                log.bump("skipped_duplicate")
                continue

            result = FetchResult(
                url=doc.canonical_url,
                final_url=doc.canonical_url,
                status=200,
                headers={"content-type": "text/plain; charset=utf-8"},
                body=body,
                content_sha256=_hashlib.sha256(body).hexdigest(),
                bytes=len(body),
                elapsed_ms=0,
            )
            try:
                raw = store_raw(
                    paths, source_id=LEGACY_SOURCE_ID, result=result, year=None,
                    doc_type=doc.doc_type, forum=forum_for(doc),
                    provenance=Provenance.LEGACY_LOCAL_HARVEST.value,
                )
                parsed, text = build_parsed_doc(
                    source_id=LEGACY_SOURCE_ID,
                    forum=forum_for(doc),
                    result=result,
                    default_doc_type=doc.doc_type,
                    license_note=LEGACY_LICENSE_NOTE,
                )
                parsed.provenance = Provenance.LEGACY_LOCAL_HARVEST
                parsed.http_status = None  # nothing was fetched
                parsed.parser_notes.extend(legacy_parser_notes(doc))
                persist(paths, store, parsed, text)
            except Exception as exc:  # noqa: BLE001 - one bad record must not stop the import
                failed += 1
                log.error("parse", f"{type(exc).__name__}: {exc}",
                          source_id=LEGACY_SOURCE_ID, url=doc.canonical_url)
                continue

            store.record_fetch(
                doc.canonical_url,
                source_id=LEGACY_SOURCE_ID,
                status=None,
                content_sha256=result.content_sha256,
                bytes_=len(body),
                doc_id=parsed.doc_id,
                raw_path=str(raw.directory),
            )
            stored += 1
            log.bump("parsed")

    held = store.link_held_citations()
    known = store.count_known_citations()
    total = store.count_documents()
    store.close()

    console.print(
        f"scanned {scan.scanned} legacy records: "
        f"{scan.judgments} judgments, {scan.legislation} legislation, "
        f"{scan.duplicates} duplicate, {scan.rejected_listing} listing pages rejected, "
        f"{scan.excluded_non_sa} non-SA excluded"
    )
    console.print(
        f"[bold]imported {stored}[/bold] ({skipped} already held, {failed} failed); "
        f"corpus now {total} documents"
    )
    if known:
        pct = held / known * 100
        console.print(
            f"[bold]coverage:[/bold] {held} of {known} known labour judgments held ({pct:.1f}%)"
        )
    console.print(
        "[dim]Provenance recorded as legacy_local_harvest. Confirm every citation "
        "against the original file before relying on it.[/dim]"
    )


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------
@app.command(name="sources")
def sources_cmd(
    registry_path: Path = typer.Option(None, "--registry", help="Path to sources.yaml."),
) -> None:
    """List the registered sources and their live access status."""
    registry = load_registry(registry_path or default_registry_path())
    table = Table(title=f"{len(registry.sources)} sources")
    table.add_column("id", style="bold")
    table.add_column("host")
    table.add_column("forum")
    table.add_column("type")
    table.add_column("access")
    table.add_column("collect")
    for source in registry.sources:
        table.add_row(
            source.id, source.host, source.forum, source.doc_type,
            source.access.value,
            "[green]yes[/green]" if source.is_collectable else "[yellow]no[/yellow]",
        )
    console.print(table)
    collectable = sum(1 for s in registry.sources if s.is_collectable)
    console.print(
        f"{collectable} collectable, {len(registry.sources) - collectable} "
        "registered but not collectable (recorded as gaps in COVERAGE.md)"
    )


# ---------------------------------------------------------------------------
# smoke
# ---------------------------------------------------------------------------
@app.command()
def smoke(
    out: Path = typer.Option(Path("./data/smoke"), "--out", help="Corpus root for the smoke run."),
    contact_email: str = typer.Option(..., "--contact-email", help="Operator contact address."),
    live: bool = typer.Option(False, "--live", help="Also attempt one live fetch from an open source."),
    registry_path: Path = typer.Option(None, "--registry", help="Path to sources.yaml."),
) -> None:
    """End-to-end check: one judgment and one statute through the full contract.

    Runs on bundled fixtures so it is deterministic and needs no network. With
    --live it additionally attempts a single real fetch from an open source and
    reports honestly whether that host served an identified crawler.
    """
    try:
        user_agent = build_user_agent(contact_email)
    except ConfigError as exc:
        _fail(str(exc), EXIT_USAGE)
        return

    paths = CorpusPaths(root=Path(out).expanduser().resolve())
    paths.ensure()
    _write_ontology(paths)
    _write_inbox_readme(paths)

    registry = load_registry(registry_path or default_registry_path())
    log = RunLog(paths.run_log, paths.error_log)
    store = CorpusStore(paths.corpus_db)

    console.print(f"[bold]User-Agent:[/bold] {user_agent}")
    console.print(f"[bold]Corpus:[/bold] {paths.root}\n")

    cases = [
        ("zalcjhb_mining_dismissal.html", "saflii_zalcjhb", DocType.JUDGMENT,
         "https://www.saflii.org/za/cases/ZALCJHB/2024/212.html", "ZALCJHB", 2024),
        ("lra_consolidated_extract.html", "del_legislation", DocType.STATUTE,
         "https://www.labour.gov.za/Legislation/Acts/labour-relations-act", "DEL", 1995),
    ]

    stored = 0
    for filename, source_id, doc_type, url, forum, year in cases:
        fixture = FIXTURES / filename
        if not fixture.exists():
            _fail(f"missing fixture: {fixture}", EXIT_USAGE)
        body = fixture.read_bytes()
        source = registry.by_id(source_id)

        result = FetchResult(
            url=url,
            final_url=url,
            status=200,
            headers={"content-type": "text/html; charset=utf-8"},
            body=body,
            content_sha256=hashlib.sha256(body).hexdigest(),
            bytes=len(body),
            elapsed_ms=0,
        )
        raw = store_raw(
            paths, source_id=source_id, result=result, year=year,
            doc_type=doc_type, forum=forum, provenance="crawled",
        )
        doc, text = build_parsed_doc(
            source_id=source_id,
            forum=forum,
            result=result,
            default_doc_type=doc_type,
            license_note=source.license_note if source else "",
        )
        doc.parser_notes.append(f"smoke fixture: {filename}")
        persist(paths, store, doc, text)
        store.record_fetch(
            url, source_id=source_id, status=200,
            content_sha256=result.content_sha256, bytes_=len(body),
            doc_id=doc.doc_id, raw_path=str(raw.directory),
        )
        stored += 1
        console.print(
            f"  [green]ok[/green] {doc.doc_type:<10} {doc.doc_id[:12]}  "
            f"citation={doc.neutral_citation or '-'}  "
            f"tags={len(doc.dismissal_tags)}  sector={doc.sector_tags or '-'}  "
            f"conf={doc.parser_confidence}"
        )

    if live:
        console.print("\n[bold]live check[/bold]")
        open_sources = [s for s in registry.sources if s.is_collectable]
        fetcher = Fetcher(user_agent, limiter=RateLimiter(1.0, 2.0),
                          on_event=lambda e: log.run.write(**e),
                          tls_cache_dir=paths.tls_cache)
        try:
            for source in open_sources[:1]:
                try:
                    live_result = fetcher.fetch(source.base_url)
                    console.print(
                        f"  [green]{source.host}[/green] served an identified crawler: "
                        f"HTTP {live_result.status}, {live_result.bytes} bytes"
                    )
                except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                    console.print(f"  [yellow]{source.host}[/yellow] refused: {exc}")
                    store.record_gap(Gap(
                        source_id=source.id, kind="http_error",
                        url=source.base_url, detail=str(exc),
                    ))
        finally:
            fetcher.close()

    with FtsIndex(paths.fts_db) as fts:
        indexed = fts.rebuild(paths, store)

    write_coverage(paths, store, registry, years=parse_year_range(None),
                   contact_email=contact_email, run_counters=log.summary())

    rows = store.count_documents()
    store.close()

    console.print(
        f"\n[bold]smoke:[/bold] stored={stored} db_rows={rows} indexed={indexed}"
    )
    console.print(f"  raw      {paths.raw}")
    console.print(f"  parsed   {paths.parsed}")
    console.print(f"  sqlite   {paths.corpus_db}")
    console.print(f"  coverage {paths.coverage_md}")

    if rows < 2:
        _fail(f"expected at least 2 database rows, got {rows}", 1)
    console.print("\n[green]smoke passed[/green]")


def main() -> int:
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/yellow]")
        return EXIT_INTERRUPTED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
