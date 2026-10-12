"""The per-run coverage and troubleshooting report (reports/ccma-harvest-*.md).

The report states a denominator -- what each source exposed *this run* -- and
the slices that were never observed. It never claims completeness, and
``completeness_claim`` is recorded as false in the catalogue by constraint.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .allowlist import AllowList
from .catalogue import Catalogue
from .harvest import HarvestResult
from .paths import CcmaPaths
from .troubleshoot import REMEDIES, RETRYABLE, ErrorClass

AWARD_UNIVERSE_NOTE = (
    "The CCMA and bargaining councils issue a very large number of awards and rulings "
    "every year. Only a small, selected fraction is ever published on the free public "
    "web, and unpublished referrals, settlements and rulings are not observable at all. "
    "This catalogue is a sample of what allow-listed sources exposed; it is not, and "
    "cannot be, a census of CCMA decisions."
)

UNOBSERVED_SLICES = (
    "Awards and rulings the CCMA or councils never published (the overwhelming majority).",
    "Conciliation outcomes, settlement agreements and certificates of outcome (not published).",
    "Any source whose status is not verified_open, listed below with the reason.",
    "Years outside the requested window, and records beyond --limit.",
    "Scanned records awaiting OCR (parse_status = ocr_needed): catalogued, not analysed.",
    "Paid databases (Juta, LexisNexis and similar): out of scope without a licence.",
)


def _table(header: list[str], rows: list[list[object]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out.extend("| " + " | ".join(str(c) for c in row) + " |" for row in rows)
    return out


def write_harvest_report(result: HarvestResult, catalogue: Catalogue, paths: CcmaPaths,
                         allowlist: AllowList) -> Path:
    paths.reports.mkdir(parents=True, exist_ok=True)
    target = paths.reports / f"ccma-harvest-{result.run_id}.md"
    lines: list[str] = [
        f"# CCMA harvest report — run {result.run_id}",
        "",
        f"- Started: {result.started_at}",
        f"- Finished: {result.finished_at}",
        f"- Mode: {'dry run (nothing saved)' if result.dry_run else ('live' if result.allow_network else 'local only (no network)')}",
        "- completeness_claim: false",
        "",
        "> Research and retrieval tool. Not legal advice.",
        "",
        "## Counts this run",
        "",
    ]
    lines += _table(
        ["source", "kind", "status", "attempted", "found", "downloaded", "seeded",
         "skipped", "failed", "quarantined"],
        [[s.source_id, s.kind, s.status, "yes" if s.attempted else "no", s.found,
          s.downloaded, s.seeded, s.skipped, s.failed, s.quarantined] for s in result.sources],
    )
    lines += [
        "",
        f"Totals: found={result.total('found')} downloaded={result.total('downloaded')} "
        f"seeded={result.total('seeded')} skipped={result.total('skipped')} "
        f"failed={result.total('failed')}.",
        "",
        "`found` is the denominator: the number of records the attempted sources "
        "exposed to this run within its window and limit. It is not the number of "
        "CCMA decisions that exist.",
        "",
        "## Sources not attempted",
        "",
    ]
    skipped = [s for s in result.sources if not s.attempted]
    if skipped:
        for s in skipped:
            source = allowlist.get(s.source_id)
            lines.append(f"- **{s.source_id}** ({s.status}): {s.refusal}.")
            if source.tos_notes:
                lines.append(f"  - Robots/ToS note: {source.tos_notes}")
    else:
        lines.append("- None.")

    checked = [s for s in result.sources if s.robots]
    if checked:
        lines += ["", "## Robots preflight", "",
                  "robots.txt was read with the production client before anything else was "
                  "requested from these sources. One refused path refuses the whole source.", ""]
        for s in checked:
            lines.append(f"- **{s.source_id}**")
            lines += [f"  - {line}" for line in s.robots]

    aborted = [s for s in result.sources if s.aborted]
    if aborted:
        lines += ["", "## Partial failures", ""]
        lines += [f"- **{s.source_id}**: {s.aborted}. Other sources continued." for s in aborted]

    failures: Counter[str] = Counter()
    for s in result.sources:
        failures.update(s.failures)
    lines += ["", "## Failures by class", ""]
    if failures:
        lines += _table(["class", "count", "retryable", "remedy"], [
            [name, count, "yes" if ErrorClass(name) in RETRYABLE else "no", REMEDIES[ErrorClass(name)]]
            for name, count in failures.most_common()
        ])
    else:
        lines.append("- None.")

    if result.dry_run:
        lines += ["", "## Would fetch or seed (dry run)", ""]
        any_urls = False
        for s in result.sources:
            local = s.kind in {"local_corpus", "local_directory"}
            for url in s.would_fetch:
                suffix = " (local copy; nothing fetched)" if local else ""
                lines.append(f"- `{s.source_id}` {url}{suffix}")
                any_urls = True
        if not any_urls:
            lines.append("- Nothing: no attempted source listed any record.")

    status_rows = catalogue.conn.execute(
        "SELECT record_kind, parse_status, COUNT(*) AS n FROM records "
        "GROUP BY record_kind, parse_status ORDER BY record_kind, parse_status"
    ).fetchall()
    ku_rows = catalogue.conn.execute(
        "SELECT kind, confidence, COUNT(*) AS n FROM knowledge_units GROUP BY kind, confidence "
        "ORDER BY kind, confidence"
    ).fetchall()
    lines += ["", "## Catalogue state after this run", ""]
    if status_rows:
        lines += _table(["record kind", "parse status", "records"],
                        [[r["record_kind"], r["parse_status"], r["n"]] for r in status_rows])
    else:
        lines.append("- The catalogue holds no records.")
    if ku_rows:
        lines += [""] + _table(["knowledge kind", "confidence", "units"],
                               [[r["kind"], r["confidence"], r["n"]] for r in ku_rows])
    lines += [
        "",
        "Records of kind `related_authority` are Labour Court or Labour Appeal Court "
        "judgments. They are indexed because they apply or review CCMA awards; they "
        "are not CCMA awards and are never counted as such.",
        "",
        "Records of kind `practice_note` and `statute` are official publications "
        "(CCMA information sheets, Rules and forms; gazetted instruments). They decide "
        "nothing, so they carry no case number or outcome and yield no case card.",
        "",
    ]
    lines += _statute_checks(catalogue)
    lines += [
        "## Gaps against \"all published\"",
        "",
        AWARD_UNIVERSE_NOTE,
        "",
        "Known unobserved slices:",
        "",
    ]
    lines += [f"- {item}" for item in UNOBSERVED_SLICES]
    lines += ["", "## Next actions", ""]
    lines += _next_actions(result, allowlist)
    lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _statute_checks(catalogue: Catalogue) -> list[str]:
    """Draft status and number checks for every statute held (D-016, D-006)."""
    rows = catalogue.conn.execute(
        "SELECT source, native_id, title, parse_status, parse_notes FROM records "
        "WHERE record_kind = 'statute' ORDER BY source, native_id"
    ).fetchall()
    if not rows:
        return []
    out = ["### Statutes held: draft status and number checks", ""]
    for row in rows:
        notes = [n for n in json.loads(row["parse_notes"] or "[]")
                 if n.startswith(("OCR NEEDED", "DRAFT", "draft check", "UNVERIFIED"))
                 or "occurs in the document text" in n]
        out.append(f"- `{row['source']}:{row['native_id']}` {row['title'] or '(untitled)'} "
                   f"-- parse {row['parse_status']}")
        out += [f"  - {n}" for n in notes] or ["  - no checks recorded (text not parsed)"]
    return out + [""]


def _next_actions(result: HarvestResult, allowlist: AllowList) -> list[str]:
    actions: list[str] = []
    for s in result.sources:
        if s.robots and any(line.startswith("REFUSED") for line in s.robots):
            actions.append(
                f"- `{s.source_id}`: robots.txt refused an allow-listed path. Set `enabled: false` "
                "in ccma_sources.yaml and record the evidence in docs/ccma-source-map.md "
                "(operator rule 2). No browser and no other route.")
    blocked = [s for s in allowlist.sources
               if s.is_live and s.status.value in {"blocked_bot_management", "content_signal_opt_out"}]
    if blocked:
        names = ", ".join(s.id for s in blocked)
        actions.append(
            f"- Request research access or a data agreement for: {names}. Do not work "
            "around the refusal (D-001, D-002)."
        )
    unconfirmed = [s for s in allowlist.sources
                   if s.is_live and s.status.value == "verified_open" and not s.operator_confirmed]
    if unconfirmed:
        actions.append("- Operator to confirm: " + ", ".join(s.id for s in unconfirmed)
                       + " (set `operator_confirmed: true` in ccma_sources.yaml).")
    if result.total("failed"):
        actions.append("- Run `labour-corpus ccma troubleshoot` for per-class remedies.")
    if any(s.quarantined for s in result.sources):
        actions.append("- Review quarantined files by hand before accepting either version.")
    actions.append("- Add awards you already lawfully hold to `data/ccma/inbox/` and re-run "
                   "with `--source operator_inbox --popia-ack`.")
    return actions
