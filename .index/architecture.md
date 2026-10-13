# Architecture

## Purpose

Collect publicly available South African labour-law primary sources into a
local, provenance-tracked, queryable corpus that a later retrieval or agent
layer can use to help a dismissed employee understand unfair-dismissal law.

Not a legal adviser. Not a republication platform. Not a complete record of
CCMA jurisprudence, and it says so in every run.

## Components

```
sources.yaml ──► registry ──► crawler ──► http (robots, rate limit, retry)
                                 │                │
                                 │                └─► challenge detection ─► gap
                                 ▼
                          pipeline: store_raw ─► extract_text ─► redact
                                 │                                  │
                                 │                                  ▼
                                 │                          ontology tagger
                                 ▼                                  │
                          parse: judgment | statute | pdf ◄─────────┘
                                 │
                                 ▼
                          state (SQLite) ──► index/fts ──► search
                                 │
                                 └──────────► report/coverage ─► COVERAGE.md

inbox/ (operator-supplied) ─────► pipeline (same ontology, same redaction)
```

## Data flow

1. `registry.load_registry` parses `sources.yaml` and rejects paid publishers.
2. `crawler.preflight_gaps` records what is known to be uncollectable before a
   single request is made.
3. For each open source, `http.Fetcher.check_policy` resolves robots.txt,
   Content-Signal and host-trip state, then `fetch` applies rate limiting,
   conditional GET and retry.
4. `pipeline.store_raw` writes the body, headers and fetch metadata under
   `raw/{source_id}/{year}/{doc_id}/`.
5. `pipeline.build_parsed_doc` extracts text, redacts personal identifiers,
   applies the ontology, and runs the judgment or statute parser.
6. `state.CorpusStore.upsert_document` writes the row, its tags and citations.
7. `index/fts` builds FTS5 over `text/`; `report/coverage` writes COVERAGE.md.

Each stage is a separate CLI command and each is independently re-runnable.

## Key patterns

**Idempotency by content address.** `doc_id = sha256(canonical_url + content_sha256)[:32]`.
Identical bytes at the same URL produce the same id, the same paths and the same
row. Changed bytes produce a new id, i.e. a new version.

**Resume by validator.** `fetch_log` stores `ETag` and `Last-Modified`. A re-run
sends a conditional GET; unchanged documents return `304` and no body is
transferred. Verified live against `labour.gov.za`: 60 conditional GETs,
0 bytes. **Since 2026-10-09 validators are sent only for a URL that already
produced a stored document** (`crawler._holds_stored_document`): listings,
year indexes and bodies whose parse failed are always fetched in full, so a
capped or interrupted crawl resumes into the documents it had not reached.

**One request per URL per run (2026-10-09).** The single `Crawler` keeps a set
of document URLs handled this run and a cache of listing bodies, so a document
linked twice, or a listing shared by two sources, costs one request (the
2026-09-20 crawl repeated 33 URLs, 89.6 MB). An in-run repeat is counted
`skipped_duplicate`.

**Pace per host (2026-10-09).** `RateLimiter.set_host_delay` applies each
source's `min_delay`/`max_delay` to its host (never lowering one), so the
CCMA's 8-15 s (D-012) is enforced; the effective delay is the max of the
global, per-source and robots Crawl-delay. robots.txt is read most
restrictively and a delay or file the crawler cannot honour trips the host
(D-025). A persistent 5xx/429 after retries becomes an `http_error` gap, not a
crashed run. `SourceOutcome.failed` is a true total; the consecutive-failure
budget uses `consecutive_failures`.

**Known-universe accounting.** `known_citations` holds judgment *references*
read from published court indexes, never documents. `link_held_citations` binds
a reference to a `doc_id` when the corpus acquires the matching judgment, which
is what makes COVERAGE.md able to say "holds N of M". URLs in that table are
derived from the citation and flagged `url_is_derived = 1`.

**Refusal becomes data.** Every robots disallow, challenge, content-signal
opt-out and HTTP error is written to the `gaps` table and surfaced in
COVERAGE.md. Blocked sources stay registered rather than being deleted, so the
corpus can state what it is missing.

**Evidence-bearing tags.** Every applied tag records the patterns that fired and
the paragraph markers present in the text. `mining` and `automatically_unfair`
additionally write an explicit audit reason.

**Never invent.** Metadata is read or left null. Titles derived from a filename
are labelled as derived. A PDF with no text layer is `ocr_needed`, not empty
text. A search with no hits returns no hits. Outcome and remedy labels are
kept only on judgments and awards (D-027). OCR text is re-derived through the
same pipeline as any other text (`build_parsed_doc(text_override=...)`), so a
transcription gets tags, language, draft status and quotes, with confidence
capped at 0.5 (D-014) — but its type and title change only on strong evidence
(title, an Act's self-identity, a code heading), never by a body-scan mention
in noisy OCR text (D-029).

**Read-only means read-only.** Every read-only SQLite connection goes through
`sqlite_ro.connect_readonly` (`Path.as_uri()` + `?mode=ro`); a hand-built URI
let a `#` in a folder name drop `mode=ro` and create a stray file.

**Transport trust.** An AIA intermediate is added to a per-host bundle only if
a certifi root's key verifies its signature (D-026).

## Constraints

- Python 3.11+; stdlib first; small custom orchestrator rather than a framework,
  so resume and state stay explicit.
- Local-first. No document body leaves the machine. Harvesting needs no LLM.
- One honest, non-configurable User-Agent carrying `--contact-email`.
- 1–2 s jittered delay per host, raised to a source's own `min_delay`/
  `max_delay` (CCMA 8-15 s) and to the published crawl-delay; one request at a
  time per host; a Crawl-delay or Retry-After above 900 s trips the host.
- There is no browser automation fallback. Robots refusals and challenge pages
  remain explicit coverage gaps (D-030).
- Paid legal publishers are a hard error at registry load.

## Access reality (verified 2026-09-17)

| Host | Result |
|---|---|
| labour.gov.za, gov.za, mhsc.org.za, gpwonline.co.za | Serve an identified crawler (200) |
| mibco.org.za, nbcrfli.org.za, bccei.co.za | Serve an identified crawler; robots permit (checked individually 2026-09-18) |
| www.saflii.org | 403 Cloudflare managed challenge on every request, including `/robots.txt` |
| www.ccma.org.za | **200 to the production UA** (corrected 2026-09-18, D-012); 403 only to a bare token UA; no robots.txt |
| lawlibrary.org.za | Reachable, but `Content-Signal: ai-input=no` and `/judgments/` disallowed |

This split is the single most important fact about the system's behaviour. See
`key-decisions.md` D-001 to D-003.

## Known structural limits

- Case law is absent from a default run, because the free case-law host refuses
  identified crawlers. The corpus is currently strong on legislation and codes
  and thin on judgments. The gap is *measured*: 12 of 6,410 known labour
  judgments (ZALCJHB, ZALC, ZALAC, ZALCCT, ZALCD, ZALCPE, ZALCC; 1997-2026),
  all from the legacy import (D-010).
- `labour.gov.za` is a SharePoint document centre: category page → folder page →
  PDFs. Both levels work over plain HTTP; no browser is needed.
- Bargaining councils are enumerated as candidates only. Each needs its own
  robots check before being enabled.

## CCMA Corpus Engine (added 2026-09-23)

Subpackage `labour_corpus.ccma`, full design in
`sa_labour_corpus/docs/ccma-corpus-architecture.md`. It sits beside the corpus
pipeline, reuses its fetcher, parsers, tagger and redaction, and adds:

```
ccma_sources.yaml -> gates -> find -> LiveGetter(validate_url) -> Fetcher
  -> download (sha256 sidecar | quarantine) -> extract -> seed (catalogue)
  -> knowledge (case card, strategy; scenarios >= 2 cards) -> report
query.py (stdlib, read-only) -> mcp_server.py -> mcp/ccma_server.py (stdio)
```

Its own store is `data/ccma/catalogue.sqlite`, keyed `(source, native_id)`.
It reads the main corpus read-only as the `local_corpus` source, bringing LC/LAC
judgments in as `related_authority`, never as awards (D-020).

Access reality for CCMA awards on 2026-09-23: SAFLII ZACCMA 403 challenge; CCMA
site 403 bot refusal on every path; lawlibrary `ai-input=no`. No live award
source is harvestable, so the live path is proven on fixtures only.

### Layer 1: official publications (D-021)

`kind: official_publication` sources carry official guidance and gazetted
instruments, seeded as `practice_note` / `statute` and never as awards. Order
of gates per source: enabled → verified_open → `--allow-network` →
confirmation (`pending_robots_check` = confirmed if robots allow) →
`--popia-ack` → **notes gate** (id in `docs/ccma-source-map.md`) → **robots
preflight** (one URL per allow-listed path; one refusal refuses the source) →
find → path-checked GETs. `labour-corpus ccma robots` runs the preflight alone.

```
ccma_sources.yaml (hosts + allowed_paths) -> notes gate -> robots_preflight
  -> find_official (urls | listing -> documents, detail pages, page/N/)
  -> LiveGetter (host+path check before and after redirect) -> download -> seed
     (non-decision: no case number/outcome/cards; scan detection; number,
      title-word and draft checks for statutes)
```

Live state, 2026-09-23: CCMA robots.txt 403, so `ccma_official_publications`
is disabled (rule 2). The gov.za GG 53294 PDF (Notice 3470 of 2025, Code of
Good Practice: Dismissal) is held as a `statute`, `ocr_needed`: its text layer
holds only running heads.

## Dismissal Navigator (added 2026-10-07, D-022, D-023)

The product layer the Purpose section anticipated. Package
`labour_corpus.navigator`; full design in
`sa_labour_corpus/docs/navigator-architecture.md`.

```
CaseFacts -> routing.classify -> engine (deadlines, route, remedy, onus, checklist)
   provision keys -> grounding.ground -> CorpusSource (read-only db + text/)
   issues -> evidence.CorpusEvidence (read-only FTS5) -> Assessment -> Markdown | JSON
```

Invariants: it reads the corpus read-only and never writes. Every legal
statement carries a `ProvisionRef` whose quote is re-found in a held, non-draft
copy of the Act at run time (`verified | not_found | not_checked`, plus copies
matched/checked). No LLM. Facts carry no PII and are never logged. Deadlines
are calendar days and are not extended. CLI exit codes: 0 grounded, 2 invalid
facts, 3 wording not found. The ontology's process-defect layer doubles as the
fairness checklist, so the checklist and the tagger share one vocabulary.

Entry points (2026-10-08): `navigate assess` (JSON facts), `navigate interview`
(plain-language questions), MCP `dismissal-navigator` (`navigator_assess`,
`navigator_provisions`; reuses the CCMA server's protocol loop via an
overridable `Server`). Quality gate: 30 golden fact patterns run in the test
suite and the GitHub Actions CI gate, with a `review-sheet` for a lawyer's sign-off
(pending).

Since 2026-10-09: a held copy counts only if it self-identifies as the Act
(D-028); the MCP server caches normalised texts per (mtime_ns, size) but runs
the copy selection on every call; `navigate assess` exits 3 when any
provision is `not_checked` (as `navigate provisions` already did); decision,
certificate and became-aware dates after the assessment date are invalid
facts (exit 2); `Track` is a `StrEnum` and the engine dispatches on it
exhaustively (`assert_never`), so a new Phase 3 track cannot fall through to
the EEA route.

Doc types (D-024): `classify_doc_type` reads title, then an Act's
self-identity in its opening, then the body. Type is now reliable for principal
Acts.
