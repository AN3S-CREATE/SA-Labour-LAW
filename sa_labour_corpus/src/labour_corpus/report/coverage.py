"""COVERAGE.md generation.

The point of this report is to make the corpus honest about its own limits, so
that a retrieval layer reading it cannot mistake a thin corpus for a complete
one. It states what was collected, what was not, and why, in plain language.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from ..config import CorpusPaths
from ..sources.registry import Access, Registry
from ..state import CorpusStore

# Required by the specification. This paragraph must survive editing.
CCMA_INCOMPLETENESS_STATEMENT = (
    "Individual CCMA awards are largely unpublished on the free web. "
    "This corpus cannot truthfully claim completeness of CCMA jurisprudence."
)

_PROVENANCE_MEANING = {
    "crawled": "fetched from the public web by this crawler",
    "operator_supplied": "dropped into inbox/ by the operator",
    "legacy_local_harvest": "from an earlier local harvest; not fetched by this crawler",
}

_GAP_LABELS = {
    "robots_disallow": "Disallowed by robots.txt",
    "challenge_blocked": "Host refuses identified automated clients",
    "http_error": "HTTP error",
    "paid_source": "Paid publisher (out of scope)",
    "content_signal_opt_out": "Express reservation against AI input",
    "not_published_free": "Not published on the free web",
    "oversize": "Body exceeded the size cap",
    "parse_failed": "Parse failed",
    "policy_excluded": "Excluded by project policy",
}


def _table(headers: list[str], rows: Sequence[Sequence[object]]) -> str:
    if not rows:
        return "_None._\n"
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out) + "\n"


def build_coverage_markdown(
    paths: CorpusPaths,
    store: CorpusStore,
    registry: Registry,
    *,
    years: tuple[int, int],
    contact_email: str,
    run_counters: dict[str, int] | None = None,
) -> str:
    total = store.count_documents()
    counters = run_counters or {}

    lines: list[str] = []
    add = lines.append

    add("# Coverage and gap report")
    add("")
    add(f"Generated: {date.today().isoformat()}  ")
    add(f"Corpus root: `{paths.root}`  ")
    add(f"Case-law year window: {years[0]}-{years[1]}  ")
    add(f"Operator contact in crawler identity: `{contact_email}`")
    add("")

    # -- the headline honesty statement --------------------------------
    add("## What this corpus is not")
    add("")
    add(f"**{CCMA_INCOMPLETENESS_STATEMENT}**")
    add("")
    add(
        "The CCMA has processed on the order of 4.2 million referrals since 1996, "
        "and unfair dismissal is roughly half of recent referrals. Almost none of "
        "those individual awards appear on any free public website. SAFLII's "
        "ZACCMA database is a published subset on the order of tens of awards, not "
        "a national archive. Bulk historical CCMA and bargaining-council awards sit "
        "behind commercial publishers, and this project does not access paid "
        "databases."
    )
    add("")
    add(
        "Any system reading this corpus must therefore not state or imply that it "
        "has 'all CCMA cases', and must not treat the absence of a case as evidence "
        "that it does not exist."
    )
    add("")

    # -- drafts: a legal-accuracy warning, not a statistic ---------------
    drafts = store.count_drafts()
    if drafts:
        add("## Drafts in this corpus — not the law")
        add("")
        add(
            f"**{drafts} document(s) in this corpus are drafts or bills, not "
            "instruments in force.** A draft published for public comment has "
            "no legal effect. Anything reading this corpus must not present a "
            "draft as the operative instrument, and must not answer a question "
            "about current law from one."
        )
        add("")
        add(_table(
            ["Document", "Type", "Source", "Why it is marked a draft"],
            [
                [
                    (row["title"] or "-")[:60],
                    row["doc_type"],
                    row["source_id"],
                    (row["draft_reason"] or "")[:50],
                ]
                for row in store.draft_documents()[:25]
            ],
        ))
        add(
            "Every such document carries `is_draft = 1` in `documents` and a "
            "`parser_notes` entry saying it is not in force."
        )
        add("")

    # -- what was collected --------------------------------------------
    add("## What was collected")
    add("")
    add(f"Documents in the corpus: **{total}**")
    add("")

    if counters:
        add("Run counters:")
        add("")
        add(_table(
            ["Counter", "Value"],
            [[k, v] for k, v in sorted(counters.items())],
        ))

    by_type = store.counts_by_doc_type()
    add("### By document type")
    add("")
    add(_table(["Document type", "Count"], [[r["doc_type"], r["n"]] for r in by_type]))

    by_source = store.counts_by_source()
    add("### By source")
    add("")
    add(_table(["Source", "Count"], [[r["source_id"], r["n"]] for r in by_source]))

    # -- provenance ------------------------------------------------------
    provenance = store.counts_by_provenance()
    if provenance:
        add("### By provenance")
        add("")
        add(
            "How each copy was obtained, as distinct from where the document "
            "authoritatively lives. A legacy or operator-supplied copy may carry "
            "a correct canonical URL without this crawler ever having fetched it."
        )
        add("")
        add(_table(
            ["Provenance", "Count", "Meaning"],
            [
                [
                    row["provenance"], row["n"],
                    _PROVENANCE_MEANING.get(row["provenance"], ""),
                ]
                for row in provenance
            ],
        ))

    # -- text quality ----------------------------------------------------
    text_status = store.counts_by_text_status()
    needs_ocr = next(
        (r["n"] for r in text_status if r["text_status"] == "ocr_needed"), 0
    )
    if text_status:
        add("### Text quality")
        add("")
        add(_table(
            ["Text status", "Count"],
            [[r["text_status"], r["n"]] for r in text_status],
        ))
        if needs_ocr:
            share = needs_ocr / max(total, 1) * 100
            add(
                f"**{needs_ocr} document(s) ({share:.1f}%) carry no text layer** and "
                "are stored as `ocr_needed`. They are scanned images, so they are "
                "in the corpus but not in the search index and not tagged. No text "
                "was invented for them. Running a local OCR pass is the way to "
                "recover them."
            )
            add("")

    add("### By forum and year")
    add("")
    forum_year = store.counts_by_forum_year()
    if forum_year:
        grouped: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for row in forum_year:
            grouped[row["forum"]].append((row["year"], row["n"]))
        rows = []
        for forum in sorted(grouped):
            for year, n in sorted(grouped[forum]):
                rows.append([forum, year or "unknown", n])
        add(_table(["Forum", "Year", "Count"], rows))
    else:
        add("_No documents yet._\n")

    # -- measured coverage against the known universe --------------------
    known = store.count_known_citations()
    if known:
        held = store.count_held_citations()
        pct = (held / known * 100) if known else 0.0
        add("### Measured coverage of known labour judgments")
        add("")
        add(
            f"**The corpus holds {held} of {known} labour judgments known to "
            f"exist ({pct:.1f}%).**"
        )
        add("")
        add(
            "The denominator is read from published court index pages, which "
            "list every judgment in a court database. It is a floor, not a "
            "census: it counts only what those indexes covered. A reference in "
            "this table is not a document, and its URL is derived from the "
            "citation rather than observed."
        )
        add("")
        rows = []
        for row in store.known_citation_coverage():
            share = (row["held"] / row["known"] * 100) if row["known"] else 0.0
            rows.append([
                row["court"], f"{row['first_year']}-{row['last_year']}",
                row["known"], row["held"], f"{share:.1f}%",
            ])
        add(_table(["Forum", "Years", "Known", "Held", "Coverage"], rows))
        if held == 0:
            add(
                "A coverage of 0% is the honest consequence of the access "
                "position described below. The references are retained so the "
                "size of the gap is measurable rather than rhetorical."
            )
            add("")

    tags = store.top_tags(limit=30)
    add("### Most frequent ontology tags")
    add("")
    add(_table(
        ["Category", "Tag", "Documents"],
        [[r["category"], r["tag"], r["n"]] for r in tags],
    ))

    # -- source status --------------------------------------------------
    add("## Source status")
    add("")
    add(
        "Access status was established by live check and is recorded verbatim, "
        "not assumed."
    )
    add("")
    status_rows = []
    for source in registry.sources:
        if not source.enabled:
            status = "disabled by default"
        elif source.access is Access.OPEN:
            status = "collectable"
        elif source.access is Access.BOT_MANAGEMENT_BLOCKED:
            status = "refuses identified crawlers"
        else:
            status = "express reservation (ai-input=no)"
        status_rows.append([source.id, source.host, source.forum, status])
    add(_table(["Source", "Host", "Forum", "Status"], status_rows))

    # -- gaps -----------------------------------------------------------
    add("## Gaps observed during this run")
    add("")
    gaps = store.all_gaps()
    if gaps:
        by_kind: dict[str, list] = defaultdict(list)
        for gap in gaps:
            by_kind[gap["kind"]].append(gap)
        for kind in sorted(by_kind):
            label = _GAP_LABELS.get(kind, kind)
            add(f"### {label} (`{kind}`) - {len(by_kind[kind])}")
            add("")
            rows = []
            for gap in by_kind[kind][:40]:
                detail = (gap["detail"] or "").replace("|", "/")
                rows.append([
                    gap["source_id"],
                    (gap["url"] or "")[:90],
                    detail[:160],
                ])
            add(_table(["Source", "URL", "Detail"], rows))
            if len(by_kind[kind]) > 40:
                add(f"_...and {len(by_kind[kind]) - 40} more; see the `gaps` table._\n")
    else:
        add("_No gaps recorded._\n")

    # -- blocked sources, explained -------------------------------------
    add("## Why the largest gaps exist")
    add("")
    add(
        "### SAFLII (Labour Court, LAC, Constitutional Court, SCA, ZACCMA)\n\n"
        "SAFLII is the highest-value free case-law source in South Africa and is "
        "registered in `sources.yaml` for exactly that reason. On the access check "
        "recorded in that file, every plain HTTP request to `www.saflii.org` - "
        "including `/robots.txt` itself - returned HTTP 403 behind a Cloudflare "
        "managed challenge, for an identified crawler user-agent and for an "
        "ordinary desktop browser user-agent alike.\n\n"
        "Collecting it would require presenting as something other than an "
        "automated client in order to defeat a deliberately configured access "
        "control. This project does not do that, so Labour Court and Labour Appeal "
        "Court judgments are absent from a default run. That is a policy choice, "
        "not a bug, and it is the single largest gap in this corpus."
    )
    add("")
    add(
        "### CCMA website\n\n"
        "`www.ccma.org.za` returned HTTP 403 from its Azure Application Gateway to "
        "every user-agent identifying itself as automated, while serving HTTP 200 "
        "to a desktop browser user-agent. It publishes no robots.txt. The same "
        "reasoning applies: the CCMA Rules, guidelines and forms are not collected "
        "because collecting them would mean concealing the crawler."
    )
    add("")
    add(
        "### Laws.Africa / LawLibrary\n\n"
        "`lawlibrary.org.za` is reachable and well structured, but its robots.txt "
        "sets `Content-Signal: ai-train=no, search=yes, ai-input=no` for general "
        "crawlers and disallows `/judgments/` and `/akn/za/judgment/`. The "
        "`ai-input` signal covers retrieval-augmented generation and grounding, "
        "which is precisely this corpus's purpose, and the file frames it as an "
        "express reservation of rights. The source is therefore registered but "
        "disabled."
    )
    add("")

    # -- lawful upgrades -------------------------------------------------
    add("## Lawful ways to close these gaps")
    add("")
    add(
        "1. **Ask.** SAFLII, AfricanLII and Laws.Africa all respond to research "
        "access requests, and Laws.Africa offers data agreements. An explicit "
        "permission or an API key turns the three largest gaps above into "
        "collectable sources, and `sources.yaml` already has the entries."
    )
    add(
        "2. **Licence a commercial feed.** Sabinet's CCMA and bargaining-council "
        "award collection, Juta and LexisNexis all licence bulk labour content. "
        "Using licensed content within the licence is lawful; this project simply "
        "will not scrape it. Costs are quoted in ZAR and are out of scope for v1."
    )
    add(
        f"3. **Use the operator drop-folder.** Put awards and judgments you "
        f"already lawfully hold into `{paths.inbox}` and run `ingest-inbox`. They "
        "are parsed with the same ontology and the same redaction as crawled "
        "material, and are recorded as operator-supplied so provenance stays "
        "honest. This is the realistic route to CCMA award coverage."
    )
    add(
        "4. **Collect from the open government sources.** The consolidated statutes "
        "and the Codes of Good Practice are freely fetchable from the Department of "
        "Employment and Labour and gov.za, and those sources are enabled by default."
    )
    add("")

    # -- assumptions -----------------------------------------------------
    add("## Assumptions and their status")
    add("")
    add(_table(
        ["Assumption", "Status"],
        [
            ["Operator has a lawful research purpose",
             "Assumed; not verifiable by this tool"],
            ["SAFLII is the highest-value free case-law source",
             "Holds, but it is not reachable by an identified crawler"],
            ["CCMA publishes rules and educational material, not a bulk award API",
             "Confirmed; the site also refuses identified crawlers"],
            ["The 2025 Code of Good Practice on Dismissal is current",
             "Treated as current, but no automatic supersede check is implemented. "
             "Confirm currency against the Government Gazette before relying on it"],
            ["No budget for paid legal databases in v1",
             "Holds; paid hosts are a hard error in the registry"],
        ],
    ))

    add("## Disclaimer")
    add("")
    add(
        "This corpus and any model that reads it are an information aid. They are "
        "not a legal practitioner. Unfair-dismissal outcomes turn on facts, "
        "condonation, forum rules and representation. Confirm every citation "
        "against the original SAFLII or Gazette file before use. Obtain advice "
        "from a CCMA commissioner information officer, a union official, or an "
        "admitted attorney."
    )
    add("")

    return "\n".join(lines)


def write_coverage(
    paths: CorpusPaths,
    store: CorpusStore,
    registry: Registry,
    *,
    years: tuple[int, int],
    contact_email: str,
    run_counters: dict[str, int] | None = None,
) -> str:
    markdown = build_coverage_markdown(
        paths, store, registry,
        years=years, contact_email=contact_email, run_counters=run_counters,
    )
    paths.reports.mkdir(parents=True, exist_ok=True)
    paths.coverage_md.write_text(markdown, encoding="utf-8")
    return markdown
