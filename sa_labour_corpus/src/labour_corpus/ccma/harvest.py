"""The harvest run: find -> download -> seed per source, then compile and report.

Gates, in order, per source (see ``CcmaSource.live_refusal``): enabled,
verified_open, ``--allow-network``, operator confirmation, POPIA
acknowledgement. Local sources need only the POPIA acknowledgement to be
processed, since indexing full text of awards is itself bulk processing.

Official publications pass two more gates before their first request: their
robots/ToS notes must be on file in the source map (operator rule 1), and
robots.txt must permit every allow-listed path (rule 2). Either failing
refuses the whole source for the run.

One failing record never ends a source, and one failing source never ends the
run. Every failure is an ``errors`` row.
"""

from __future__ import annotations

import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable, Iterable

from ..config import build_user_agent
from ..http import Fetcher, RateLimiter
from ..observability import JsonlLogger
from .allowlist import LIVE_KINDS, AllowList, CcmaSource, SourceKind
from .catalogue import Catalogue
from .download import store_artifact
from .find import Found, find_live, find_local_corpus, find_local_directory, local_corpus_row
from .paths import CcmaPaths
from .scrape import Getter, LiveGetter, Page, robots_preflight
from .seed import compile_scenarios, export_knowledge, read_corpus_text, seed_one
from .troubleshoot import Classified, DiskFull, ErrorClass, classify_exception

DEFAULT_LIMIT = 50
DEFAULT_CONCURRENCY = 2
CHECKPOINT_EVERY = 100
PROGRESS_EVERY = 25
RETRY_WAITS = (1.0, 4.0, 16.0)
TIMEOUT_SECONDS = 20.0
ALL_STAGES = frozenset({"find", "download", "seed"})


@dataclass(slots=True)
class HarvestOptions:
    root: Path
    source_ids: list[str] = field(default_factory=list)
    limit: int | None = DEFAULT_LIMIT
    dry_run: bool = False
    allow_network: bool = False
    popia_ack: bool = False
    contact_email: str | None = None
    concurrency: int = DEFAULT_CONCURRENCY
    years: tuple[int, int] | None = None
    corpus_root: Path | None = None       # the main corpus, for local_corpus
    getter_factory: Callable[[CcmaSource], Getter] | None = None  # tests inject fixtures
    # Which stages run: find (walk listings), download (fetch + store), seed
    # (parse + catalogue + knowledge). `ccma harvest` runs all three.
    stages: frozenset[str] = ALL_STAGES


@dataclass(slots=True)
class SourceReport:
    source_id: str
    kind: str
    status: str
    attempted: bool
    refusal: str | None = None
    found: int = 0
    downloaded: int = 0
    seeded: int = 0
    skipped: int = 0
    failed: int = 0
    quarantined: int = 0
    would_fetch: list[str] = field(default_factory=list)
    failures: dict[str, int] = field(default_factory=dict)
    aborted: str | None = None
    robots: list[str] = field(default_factory=list)   # preflight verdicts, one line per path


@dataclass(slots=True)
class HarvestResult:
    run_id: str
    started_at: str
    finished_at: str = ""
    dry_run: bool = False
    allow_network: bool = False
    sources: list[SourceReport] = field(default_factory=list)
    scenarios: int = 0
    knowledge_exported: int = 0
    report_path: Path | None = None

    def total(self, name: str) -> int:
        return sum(getattr(s, name) for s in self.sources)

    @property
    def exit_code(self) -> int:
        live_run = self.allow_network and not self.dry_run
        expected_live = any(s.kind in {k.value for k in LIVE_KINDS} for s in self.sources)
        if live_run and expected_live and self.total("found") == 0:
            return 2
        return 0


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(2)}"


class Harvester:
    def __init__(self, allowlist: AllowList, options: HarvestOptions,
                 out: Callable[[str], None] | None = None) -> None:
        self.allowlist = allowlist
        self.opts = options
        self.paths = CcmaPaths(options.root)
        self.paths.ensure()
        self.catalogue = Catalogue(self.paths.catalogue)
        self.events = JsonlLogger(self.paths.event_log)
        self.audit = JsonlLogger(self.paths.audit_log)
        self.out = out or (lambda line: print(line, file=sys.stderr))
        self.run_id = new_run_id()
        self._fetcher: Fetcher | None = None

    # -- helpers ----------------------------------------------------------
    def _event(self, event: str, **fields) -> None:
        self.events.write(event=event, run_id=self.run_id, **fields)

    def _audit(self, decision: str, reason: str, **fields) -> None:
        self.audit.write(decision=decision, reason=reason, run_id=self.run_id, **fields)

    def _fail(self, report: SourceReport, native_id: str | None, classified: Classified,
              event: str = "fetch_fail") -> None:
        report.failed += 1
        name = classified.error_class.value
        report.failures[name] = report.failures.get(name, 0) + 1
        if not self.opts.dry_run:
            self.catalogue.add_error(
                self.run_id, report.source_id, native_id, name,
                classified.http_status, classified.message, classified.retryable,
            )
        self._event(event, source=report.source_id, native_id=native_id,
                    error_class=name, http_status=classified.http_status,
                    retryable=classified.retryable)

    def _getter(self, source: CcmaSource) -> Getter:
        if self.opts.getter_factory is not None:
            return self.opts.getter_factory(source)
        if self._fetcher is None:
            if not self.opts.contact_email:
                raise ValueError("--contact-email is required for network access")
            self._fetcher = Fetcher(
                build_user_agent(self.opts.contact_email),
                timeout=TIMEOUT_SECONDS,
                limiter=RateLimiter(source.min_delay, source.max_delay),
                tls_cache_dir=self.paths.data / "tls",
                retry_waits=RETRY_WAITS,
                on_event=lambda e: self._event("http", **{k: v for k, v in e.items()
                                                          if k in {"stage", "url", "status", "error"}}),
            )
        else:
            self._fetcher.limiter.min_delay = max(self._fetcher.limiter.min_delay, source.min_delay)
            self._fetcher.limiter.max_delay = max(self._fetcher.limiter.max_delay, source.max_delay)
        return LiveGetter(self._fetcher, source)

    def _progress(self, report: SourceReport, position: int) -> None:
        if position % PROGRESS_EVERY == 0:
            self.out(f"{self.run_id} {report.source_id} found={report.found} "
                     f"downloaded={report.downloaded} seeded={report.seeded} failed={report.failed}")
        if position % CHECKPOINT_EVERY == 0 and not self.opts.dry_run:
            self.catalogue.checkpoint(self.run_id, report.source_id, position)

    # -- run --------------------------------------------------------------
    def run(self) -> HarvestResult:
        from .report import write_harvest_report   # late import: report imports this module

        opts = self.opts
        mode = "dry_run" if opts.dry_run else ("live" if opts.allow_network else "local")
        result = HarvestResult(self.run_id, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               dry_run=opts.dry_run, allow_network=opts.allow_network)
        sources = self.allowlist.select(opts.source_ids or None)
        self.catalogue.sync_sources(self.allowlist.sources)
        self.catalogue.start_run(self.run_id, mode)
        self._event("run_started", mode=mode, limit=opts.limit,
                    sources=[s.id for s in sources], popia_ack=opts.popia_ack)
        if opts.popia_ack and not opts.dry_run:
            self._audit("popia_acknowledged",
                        "operator passed --popia-ack: lawful basis stated and minimisation "
                        "rules accepted for this run")
        try:
            for source in sources:
                result.sources.append(self._run_source(source))
            if not opts.dry_run:
                result.scenarios = len(compile_scenarios(self.catalogue))
                result.knowledge_exported = export_knowledge(self.catalogue, self.paths)
                for row in self.catalogue.knowledge():
                    if row["confidence"] == "high" and row["kind"] != "scenario":
                        self._audit("knowledge_confidence_high",
                                    "unit quotes a published finding verbatim",
                                    unit_id=row["id"], record_fk=row["record_fk"])
        finally:
            if self._fetcher is not None:
                self._fetcher.close()
            result.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            counts = {name: result.total(name) for name in
                      ("found", "downloaded", "seeded", "failed", "skipped")}
            self.catalogue.finish_run(self.run_id, counts, notes=mode)
            result.report_path = write_harvest_report(result, self.catalogue, self.paths, self.allowlist)
            self._event("run_finished", **counts, exit_code=result.exit_code)
            self.catalogue.close()
        return result

    def _run_source(self, source: CcmaSource) -> SourceReport:
        opts = self.opts
        report = SourceReport(source.id, source.kind.value, source.status.value, attempted=False)
        if source.tos_notes:
            self._audit("tos_interpretation", source.tos_notes, source=source.id)

        stages = opts.stages
        needs_network = source.is_live and bool(stages & {"find", "download"})
        if needs_network:
            refusal = source.live_refusal(allow_network=opts.allow_network,
                                          popia_ack=opts.popia_ack, dry_run=opts.dry_run)
        elif not source.enabled:
            refusal = "source disabled in the allow-list"
        elif not opts.popia_ack and not opts.dry_run and "seed" in stages:
            refusal = "POPIA lawful basis and minimisation not acknowledged (--popia-ack)"
        else:
            refusal = None
        if refusal:
            return self._refuse(report, source, refusal, needs_network=needs_network,
                                policy=source.status.value != "verified_open")

        if needs_network and source.needs_robots_preflight:
            # Rule 1: the notes come before any request, the robots check included.
            refusal = self.allowlist.notes_refusal(source) or self._robots_preflight(source, report)
            if refusal:
                return self._refuse(report, source, refusal, needs_network=True, policy=True)

        report.attempted = True
        self._event("source_enabled", source=source.id, kind=source.kind.value)

        if stages == {"seed"}:
            self._seed_only(source, report)
            return report

        try:
            # Local sources are cheap to list, so they are always re-listed.
            if "find" in stages or not source.is_live:
                found = list(self._find(source, report))
            else:
                found = self._previously_found(source)
        except Exception as exc:  # noqa: BLE001 -- one failing source must not end the run
            self._fail(report, None, classify_exception(exc))
            report.aborted = f"discovery failed: {type(exc).__name__}"
            return report
        report.found = len(found)
        for f in found:
            self._event("record_found", source=source.id, native_id=f.native_id)
            if not opts.dry_run:
                self.catalogue.upsert_discovered(self.run_id, f.source, f.native_id, f.url,
                                                 f.title, f.year, f.record_kind.value)

        if opts.dry_run:
            report.would_fetch = [f.url for f in found]
            return report
        if "download" not in stages:
            return report

        if source.kind is SourceKind.LOCAL_CORPUS:
            self._seed_local_corpus(source, found, report)
        else:
            self._download_and_seed(source, found, report)
        return report

    def _refuse(self, report: SourceReport, source: CcmaSource, refusal: str, *,
                needs_network: bool, policy: bool) -> SourceReport:
        """Record why a source is not attempted. A live policy refusal is an error row."""
        report.refusal = refusal
        self._event("source_skipped", source=source.id, reason=refusal)
        self._audit("source_skipped", refusal, source=source.id, status=source.status.value)
        if needs_network and source.enabled and policy and not self.opts.dry_run:
            self.catalogue.add_error(self.run_id, source.id, None, ErrorClass.POLICY_BLOCK.value,
                                     None, refusal, False)
        return report

    def _robots_preflight(self, source: CcmaSource, report: SourceReport) -> str | None:
        """Rule 2: robots.txt must permit every allow-listed path, or nothing is fetched.

        Returns the refusal, or None when every path is allowed. A check that
        cannot complete is a refusal, never a pass.
        """
        try:
            verdicts = robots_preflight(self._getter(source), source)
        except Exception as exc:  # noqa: BLE001 -- an incomplete check must refuse
            return f"robots preflight could not complete: {classify_exception(exc).message}"
        for v in verdicts:
            report.robots.append(f"{'allowed' if v.allowed else 'REFUSED'}: {v.url} -- {v.reason}")
            self._audit("robots_check", v.reason, source=source.id, url=v.url,
                        allowed=v.allowed, robots_status=v.robots_status)
        refused = [v for v in verdicts if not v.allowed]
        if not verdicts:
            return "robots preflight had no allow-listed path to check"
        if refused:
            return (f"robots preflight refused {len(refused)} of {len(verdicts)} allow-listed "
                    f"path(s), first {refused[0].url}: {refused[0].reason}. Disable the source "
                    "in ccma_sources.yaml (operator rule 2); do not work around it")
        return None

    def _previously_found(self, source: CcmaSource) -> list[Found]:
        """Records an earlier `ccma find` discovered, for a download-only run."""
        from .allowlist import RecordKind

        rows = self.catalogue.discovered(source.id)
        if self.opts.limit is not None:
            rows = rows[: self.opts.limit]
        return [Found(r["source"], r["native_id"], r["url"], r["title"], r["year"],
                      RecordKind(r["record_kind"])) for r in rows]

    def _seed_only(self, source: CcmaSource, report: SourceReport) -> None:
        """Re-seed from what is already on disk. No network."""
        from .allowlist import RecordKind
        from .seed import iter_raw_records

        if source.kind is SourceKind.LOCAL_CORPUS:
            found = list(self._find(source))
            report.found = len(found)
            self._seed_local_corpus(source, found, report)
            return
        known = {r["native_id"]: r for r in self.catalogue.discovered(source.id)}
        for position, (src, native_id, original, meta) in enumerate(
                iter_raw_records(self.paths, source.id), start=1):
            if self.opts.limit is not None and position > self.opts.limit:
                break
            report.found += 1
            row = known.get(native_id)
            kind = RecordKind(row["record_kind"]) if row else source.record_kind
            item = Found(src, native_id, meta.get("source_url", ""), row["title"] if row else None,
                         row["year"] if row else None, kind)
            self._seed(report, item, original.read_bytes(), meta.get("content_type"),
                       meta.get("source_url", ""), meta.get("sha256", ""), meta.get("retrieved_at"),
                       str(original.relative_to(self.paths.root)),
                       "crawled" if source.is_live else "operator_supplied")
            self._progress(report, position)

    def _find(self, source: CcmaSource, report: SourceReport | None = None) -> Iterable[Found]:
        if source.is_live:
            def on_error(url: str, exc: Exception) -> None:
                if report is not None:
                    classified = classify_exception(exc)
                    classified.message = f"listing {url}: {classified.message}"
                    self._fail(report, None, classified)
            return find_live(source, self._getter(source), self.opts.limit, self.opts.years,
                             on_error=on_error)
        if source.kind is SourceKind.LOCAL_CORPUS:
            root = self.opts.corpus_root or (self.opts.root / (source.path or "data/corpus"))
            return find_local_corpus(source, root, self.opts.limit)
        directory = self.opts.root / (source.path or "data/ccma/inbox")
        return find_local_directory(source, directory, self.opts.limit)

    # -- acquisition ------------------------------------------------------
    def _acquire(self, source: CcmaSource, getter: Getter | None, found: Found) -> Page:
        if getter is not None:
            return getter.get(found.url)
        import hashlib
        from urllib.parse import urlparse
        from urllib.request import url2pathname

        path = Path(url2pathname(urlparse(found.url).path))
        body = path.read_bytes()
        content_type = "application/pdf" if path.suffix.lower() == ".pdf" else (
            "text/plain" if path.suffix.lower() == ".txt" else "text/html")
        return Page(found.url, found.url, 200, body, content_type, hashlib.sha256(body).hexdigest())

    def _download_and_seed(self, source: CcmaSource, found: list[Found], report: SourceReport) -> None:
        getter = self._getter(source) if source.is_live else None
        workers = max(1, min(self.opts.concurrency, 8)) if getter is not None else 1

        def fetch(item: Found):
            try:
                return item, self._acquire(source, getter, item), None
            except Exception as exc:  # noqa: BLE001 -- isolated per record, classified below
                return item, None, exc

        position = 0
        for start in range(0, len(found), CHECKPOINT_EVERY):
            batch = found[start:start + CHECKPOINT_EVERY]
            if workers > 1:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    outcomes = list(pool.map(fetch, batch))
            else:
                outcomes = [fetch(item) for item in batch]
            for item, page, exc in outcomes:
                position += 1
                if exc is not None:
                    self._fail(report, item.native_id, classify_exception(exc))
                    self._progress(report, position)
                    continue
                self._event("fetch_ok", source=source.id, native_id=item.native_id,
                            status=page.status, bytes=len(page.body))
                try:
                    stored = store_artifact(self.paths, source.id, item.native_id, page.body,
                                            page.content_type, page.final_url)
                except DiskFull as full:   # not `exc`: that would unbind the loop variable
                    self._fail(report, item.native_id, classify_exception(full))
                    report.aborted = "disk below 500 MB; source aborted"
                    return
                if stored.status == "quarantined":
                    report.quarantined += 1
                    held = stored.held_sha256 or "unknown"   # always set when quarantined
                    self._fail(report, item.native_id, Classified(
                        ErrorClass.CHECKSUM_CONFLICT, None,
                        f"held {held[:16]} != fetched {stored.sha256[:16]}; "
                        f"quarantined at {stored.path.relative_to(self.paths.root)}"),
                        event="quarantine")
                    self._progress(report, position)
                    continue
                if stored.status == "stored":
                    report.downloaded += 1
                if "seed" not in self.opts.stages:
                    if stored.status == "unchanged":
                        report.skipped += 1
                    self._progress(report, position)
                    continue
                self._seed(report, item, page.body, page.content_type, page.final_url,
                           stored.sha256, stored.retrieved_at,
                           str(stored.path.relative_to(self.paths.root)),
                           "crawled" if source.is_live else "operator_supplied")
                self._progress(report, position)

    def _seed_local_corpus(self, source: CcmaSource, found: list[Found], report: SourceReport) -> None:
        root = self.opts.corpus_root or (self.opts.root / (source.path or "data/corpus"))
        for position, item in enumerate(found, start=1):
            row = local_corpus_row(root, item.native_id)
            text = read_corpus_text(root, item.native_id)
            if row is None or text is None:
                self._fail(report, item.native_id, Classified(
                    ErrorClass.EMPTY_BODY, None, "main-corpus text/ file missing"), event="parse_fail")
                continue
            self._seed(report, item, b"", row["content_type"], row["canonical_url"],
                       row["content_sha256"], row["retrieved_at"],
                       str((root / "text" / f"{item.native_id}.txt")), row["provenance"],
                       stored_text=text, default_forum=row["forum"])
            self._progress(report, position)

    def _seed(self, report: SourceReport, item: Found, body: bytes, content_type: str | None,
              url: str, sha256: str, retrieved_at: str | None, local_path: str,
              provenance: str, *, stored_text: str | None = None,
              default_forum: str | None = None) -> None:
        try:
            outcome = seed_one(
                self.catalogue, source=item.source, native_id=item.native_id,
                record_kind=item.record_kind, body=body, content_type=content_type, url=url,
                sha256=sha256, retrieved_at=retrieved_at, local_path=local_path,
                provenance=provenance, stored_text=stored_text, default_forum=default_forum,
                title_hint=item.title,
            )
        except Exception as exc:  # noqa: BLE001 -- third-party parsers fail broadly (D-008)
            self._fail(report, item.native_id,
                       Classified(ErrorClass.PARSE_MISS, None, f"{type(exc).__name__}: {exc}"),
                       event="parse_fail")
            return
        if outcome.status == "skipped":
            report.skipped += 1
        else:
            report.seeded += 1
            self._event("seed_ok", source=item.source, native_id=item.native_id,
                        status=outcome.status, parse_status=outcome.parse_status,
                        knowledge_units=len(outcome.units))
        if outcome.parse_status in {"failed", "unsupported"}:
            cls = ErrorClass.PARSE_UNSUPPORTED if outcome.parse_status == "unsupported" \
                else ErrorClass.PARSE_MISS
            self._fail(report, item.native_id,
                       Classified(cls, None, "; ".join(outcome.parsed.notes[-3:]) if outcome.parsed else ""),
                       event="parse_fail")
        elif outcome.parse_status == "parsed":
            self.catalogue.resolve_errors(item.source, item.native_id)
        if outcome.minimisation_error:
            self._fail(report, item.native_id, Classified(
                ErrorClass.MINIMISATION_FAILED, None, outcome.minimisation_error), event="parse_fail")
