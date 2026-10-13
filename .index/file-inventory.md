# File inventory

Root: `sa_labour_corpus/`. All paths relative to it. Status values: Active,
Deprecated, Dead, Experimental, Generated.

Last refreshed: 2026-10-13 (Git/CI baseline, legal deadline and outcome fixes,
dead adapter removal, install profiles and audit-log rotation).

## Project root (one level above `sa_labour_corpus/`)

| Path | Purpose | Status |
|---|---|---|
| `.gitignore` | Excludes corpus bodies, logs/reports, virtual environments, caches, build outputs and the superseded pre-Git snapshot | Active |
| `.github/workflows/ci.yml` | Python 3.11/3.13 gate: Ruff, mypy, pytest, golden review sheet and offline fixture smoke | Active |
| `REPO_ANALYSIS_MEMORY.md` | Working state of the multi-session investigation | Active |
| `analysis_logs/review-2026-10-09-findings.{md,json}` | Full evidence of the 2026-10-09 review: 72 findings with verifier reasoning and fix notes | Active (archive) |
| `analysis_logs/baseline-2026-10-09/` | Pre-change copy of `sa_labour_corpus` source/tests/docs/config, `.index/` and the memory file (128 files, 1 MB). The only rollback point until git exists | Active (archive) |
| `analysis_logs/session-report-2026-10-09.md` | The six-section session report: context, plan, execution and failure log, recommendations by category, resources, build status | Active (archive) |

## Package root

| Path | Purpose | Status |
|---|---|---|
| `README.md` | Setup and explicit core/contributor/OCR/full install profiles, commands, honesty guarantees, POPIA notes, RAG wiring | Active |
| `pyproject.toml` | Packaging; `labour-corpus` console script; extras `ocr`, `test`, `lint`; no browser fallback extra (D-030) | Active |
| `docs/legal-review-2026-10-13.md` | Source-pinned review pack and sign-off fields for the 30 navigator golden cases; practitioner approval still pending | Active |
| `docs/optimisation-roadmap.md` | Prioritised performance, security, bundle, architecture, quality and cost recommendations with implementation status and release gates | Active |
| `docs/navigator-architecture.md` | Navigator product vision, capabilities, architecture, auth model, verified behaviour, roadmap | Active |
| `examples/*.json` | Sample `CaseFacts` files for `navigate assess` | Active |
| `requirements.txt` | Full pinned dev/operator set (core, OCR, test, lint), no secrets; browser automation removed by D-030 | Active |
| `rebuild_corpus.sh` | crawl -> imports -> parse --ocr -> index -> report; `set -euo pipefail` with guarded greps, so a failing stage stops the rebuild (2026-10-09) | Active |
| `tools/acceptance_audit.py` | Checks the 11 acceptance criteria against a built corpus. C5 now fails on repeat 200 downloads of unchanged bodies (`resume_evidence`) | Active |
| `sources.yaml` | Source registry with live-verified access status per host. 26 sources, 14 collectable | Active |
| `ccma_sources.yaml` | CCMA engine allow-list: 9 sources, all https on the default port | Active |
| `docs/ccma-corpus-architecture.md` | O1: upgrade-vs-greenfield, capabilities, gap analysis, acceptance evidence; §10 Layer 1 | Active |
| `docs/ccma-source-map.md` | O2: verified CCMA sources with robots/ToS notes; Layer 1 notes, robots results, GG 53294 finding | Active |
| `mcp/navigator_server.py` | Launcher for the `dismissal-navigator` MCP server (`--root` corpus) | Active |
| `mcp/ccma_server.py` | O8: stdlib MCP stdio launcher (`--root`) | Active |

## Core (`src/labour_corpus/`)

| Path | Purpose | Key symbols | Status |
|---|---|---|---|
| `cli.py` | Typer app: crawl, parse, index, search, report, ingest-inbox, sources, smoke; mounts `ccma` and `navigate`. No `from __future__ import annotations` (Typer evaluated string annotations on every invocation, ~1.2 s). `parse` has a D-008 document boundary and one OCR helper | `app`, `crawl`, `parse`, `_rebuild_from_transcription`, `smoke` | Active |
| `config.py` | Paths, settings, crawler identity, paid-host guard | `build_user_agent`, `assert_not_paid_host`, `CorpusPaths`, `CrawlSettings` | Active |
| `models.py` | Pydantic document contract and enums; `Gap.kind` typed as `GapKind` | `ParsedDoc`, `DocType`, `Provenance`, `Outcome`, `Remedy`, `make_doc_id`, `Gap`, `GapKind` | Active |
| `challenge.py` | Recognises bot-management interstitials. Never solves one | `looks_like_challenge`, `ChallengeDetected` | Active |
| `robots.py` | robots.txt (RFC 9309), Crawl-delay, Content-Signal; every matching group applies, most restrictively; oversize and non-finite delays refuse (D-025) | `parse_robots`, `RobotsRules._matching_groups`, `_most_restrictive`, `ContentSignal` | Active |
| `http.py` | Polite fetch: per-host rate limit, retry, checksum, conditional GET, host tripping, TLS chain repair, streamed robots.txt capped at 512 KiB, Retry-After digits/HTTP-date only, trips above 900 s; every retry attempt re-checks the trip (shared fetcher across CCMA workers) | `Fetcher`, `RateLimiter.set_host_delay`, `parse_retry_after`, `MAX_RETRY_AFTER`, `MAX_CRAWL_DELAY`, `ROBOTS_MAX_BYTES`, `Fetcher._read_robots` | Active |
| `tls.py` | Completes an incomplete chain via AIA; the gate verifies the intermediate's signature against a certifi root (D-026); AIA body streamed, 64 KiB cap | `complete_chain`, `_intermediate_is_trusted`, `_trusted_roots` (roots loaded one by one), `_download`, `looks_like_incomplete_chain` | Active |
| `sqlite_ro.py` | **New 2026-10-09.** Stdlib-only read-only SQLite connections via `Path.as_uri()`; used by grounding, evidence and the CCMA query/catalogue/find layers | `connect_readonly`, `readonly_uri` | Active |
| `state.py` | SQLite store: documents, citations, tags, fetch_log, gaps; `idx_documents_neutral` (link_held_citations 20x faster) | `CorpusStore`, `SCHEMA` | Active |
| `pipeline.py` | fetch -> raw -> parse -> tag -> persist. `text_override` re-derives everything from OCR text; outcome/remedy only for judgments and awards (D-027); OOXML decompression limits; malformed DOCX XML reported, not raised | `store_raw`, `build_parsed_doc`, `persist`, `extract_text`, `ooxml_size_refusal`, `DOCX_MAX_*` | Active |
| `observability.py` | Append-only RUN.jsonl / ERRORS.jsonl and counters | `RunLog`, `JsonlLogger` | Active |

## Sources (`src/labour_corpus/sources/`)

| Path | Purpose | Key symbols | Status |
|---|---|---|---|
| `registry.py` | Loads and validates `sources.yaml`; paid-host rule first; unknown keys, missing `enabled`, `min_delay > max_delay` and an unsupported `version` are ConfigErrors | `load_registry`, `SourceDef`, `Access`, `SUPPORTED_VERSION` | Active |
| `crawler.py` | Orchestrator; every refusal becomes a gap. Validators only for stored documents; per-run seen set and listing cache; per-host delays registered; RetryableStatus becomes a gap; `failed` total vs `consecutive_failures` | `Crawler`, `SourceOutcome`, `_holds_stored_document`, `preflight_gaps` | Active (tests: `test_crawler.py`) |
| `links.py` | Link discovery from index pages; no case-number enumeration | `extract_links`, `filter_links` | Active |
| `saflii.py` | SAFLII index walking and the labour relevance filter | `find_year_links`, `find_judgment_links`, `is_labour_relevant` | Active, source blocked |
| `del_gov.py` | DEL and gov.za patterns plus the cautiously stated 2025 dismissal-code record | `CURRENT_DISMISSAL_CODE`, `is_dismissal_code` | Active |
| `inbox.py` | Operator drop-folder ingestion with honest provenance; D-008 document boundary; filename guess: award/arbitration first, "act" only as a standalone word (2026-10-09) | `ingest_inbox`, `_doc_type_for`, `README_TEXT` | Active |
| `citation_map.py` | Reads judgment references from harvested court indexes; SA-only | `parse_listing_line`, `collect_citations`, `CitationRecord` | Active |
| `legacy_import.py` | Imports documents from an earlier local harvest | `classify_legacy_record`, `scan_legacy_corpus`, `LegacyDoc` | Active |

## CCMA engine (`src/labour_corpus/ccma/`)

| Path | Purpose | Key symbols | Status |
|---|---|---|---|
| `allowlist.py` | Loads `ccma_sources.yaml`; live gates; URL guard now https-only on the default port (plus hosts, paths, credentials, IP literals); unknown keys rejected | `load_allowlist`, `CcmaSource.live_refusal`, `validate_url`, `path_allowed`, `AllowList.notes_refusal` | Active |
| `paths.py` | `data/ccma/`, `reports/`, `logs/` layout; 500 MB floor | `CcmaPaths`, `MIN_FREE_BYTES` | Active |
| `catalogue.py` | SQLite catalogue with FTS5. `upsert_record` skips only when every content/parse-derived field matches (not just four) | `Catalogue`, `RecordRow`, `KnowledgeUnit` | Active |
| `find.py` | Listing parsers and per-source discovery; http links on allow-listed hosts upgraded to https | `find_live`, `find_official`, `parse_official_listing` | Active |
| `scrape.py` | The only network object; URL check before and after redirect; robots preflight | `LiveGetter`, `robots_preflight`, `FixtureGetter` | Active |
| `download.py` | Original bytes + `.sha256` + meta; checksum conflict -> quarantine | `store_artifact` | Active |
| `extract.py` | Text, redaction, metadata, dispute type, scan detection, statute checks; pypdf imported lazily; `pdf_page_count` catches `PyPdfError` | `parse_artifact`, `pdf_page_count`, `substantive_chars` | Active |
| `minimise.py` | Knowledge-layer PII removal + assertion | `minimise`, `assert_minimised` | Active |
| `knowledge.py` | Case cards, strategies, scenarios; final-decision-section holding extraction; no LLM | `build_record_units`, `build_scenarios`, `find_holding` | Active |
| `seed.py` | Idempotent upsert + knowledge; scenario compile; JSON export | `seed_one`, `compile_scenarios` | Active |
| `harvest.py` | Orchestrator with gates, checkpoints, audit | `Harvester`, `HarvestOptions`, `RETRY_WAITS` | Active |
| `troubleshoot.py` | Error classes, remedies, disk check, secret scan | `classify_exception`, `diagnose`, `scan_for_secrets` | Active |
| `report.py` | Per-run report with denominator and unobserved slices | `write_harvest_report` | Active |
| `query.py` | Stdlib read-only queries for MCP (via `sqlite_ro`) | `open_catalogue`, `search`, `get`, `coverage` | Active |
| `mcp_server.py` | Stdlib JSON-RPC MCP server; `ccma_refresh` double-gated; overridable `Server` | `Server`, `serve`, `TOOLS` | Active |
| `cli.py` | `labour-corpus ccma ...`; no future-annotations import (Typer speed) | `ccma_app`, `robots_cmd` | Active |

## Dismissal Navigator (`src/labour_corpus/navigator/`)

Read-only product layer over the corpus (D-022, D-023, D-028). Rules as code plus quotes re-found in held statutes; no LLM, no writes.

| Path | Purpose | Key symbols | Status |
|---|---|---|---|
| `models.py` | Typed contracts; `CaseFacts` forbids extra fields; `Assessment` frozen | `CaseFacts`, `Assessment`, `ProvisionRef`, `Grounding` | Active |
| `provisions.py` | 22 verbatim provisions (including LRA s136(1)(b), s186-s194 and EEA s10) | `PROVISIONS`, `ACT_TITLES`, `get` | Active |
| `grounding.py` | Re-finds each quote in held, readable, non-draft copies that **self-identify as the Act** (D-028); normalised text cached per doc_id on (mtime_ns, size); `normalise` uses chained replace (8x faster, identical output) | `ground`, `normalise`, `CorpusSource`, `TextCache`, `HeldText`, `corpus_source` | Active |
| `routing.py` | Classification and track; `Track` is a `StrEnum` | `classify`, `Track`, `conciliation_forum`, `checklist` | Active |
| `deadlines.py` | Calendar-day / calendar-month arithmetic; never extended | `add_days`, `add_months`, `make_deadline` | Active |
| `evidence.py` | Read-only FTS5 per issue (via `sqlite_ro`); type allow-list; drafts and Act copies excluded by title | `CorpusEvidence`, `issues_for`, `EVIDENCE_TYPES` | Active |
| `engine.py` | Composes an assessment; rejects decision/certificate/became-aware dates after the assessment date; exhaustive `Track` dispatch (`assert_never`) | `Navigator.assess`, `DISMISSAL_CODE_WARNING` | Active |
| `render.py` | Markdown for a non-lawyer; grounding marks, copy counts | `to_markdown` | Active |
| `log.py` | JSON-lines logging to stderr; facts never logged | `JsonFormatter`, `configure` | Active |
| `cli.py` | `navigate assess|interview|review-sheet|provisions|mcp`; exit 0/2/3, and 3 also when any provision is not_checked (no corpus at `--out`) | `navigate_app`, `_run` | Active |
| `golden.py` | Golden cases, checker, lawyer review sheet; observed track is the plain string | `load_cases`, `check`, `mismatches`, `review_sheet` | Active |
| `interview.py` | Plain-language question flow; no PII asked | `build_facts`, `TerminalAsker` | Active |
| `mcp_server.py` | `dismissal-navigator` MCP server; one `TextCache` per server instance | `NavigatorServer`, `NavigatorServer._statutes`, `NAVIGATOR_TOOLS` | Active |

## Parsing (`src/labour_corpus/parse/`)

| Path | Purpose | Key symbols | Status |
|---|---|---|---|
| `text.py` | HTML/RTF to text, title, language, usability. RTF: linear-time, `\ansicpgN` decoding, `\ucN` fallback skip, surrogate pairs combined | `html_to_text`, `rtf_to_text`, `_rtf_codepage`, `detect_language`, `classify_text` | Active |
| `judgment.py` | Judgment and award metadata. Never invents a citation | `parse_judgment`, `extract_neutral_citation`, `extract_case_no` | Active |
| `statute.py` | Instrument classification (title, D-024 self-identity, then "Code of Good Practice" in the opening, then the body scan unless `body_scan=False`), draft detection ("Bill of Rights" is not a bill); `parse_statute(machine_transcription=True)` for OCR text (D-029) | `classify_doc_type`, `self_identified_act`, `_names_itself_a_code`, `parse_statute`, `detect_draft` | Active |
| `pdf.py` | PDF text; pypdf imported lazily; per-page and document errors catch `PyPdfError`; a PDF whose every page fails is `empty` (reader failure), not `ocr_needed` | `extract_pdf_text` | Active |
| `office.py` | Magic-byte sniffing; legacy Word via antiword in a `TemporaryDirectory` (no leaked temp dirs); notes never carry the temporary path | `sniff_format`, `extract_doc_text`, `_without_path`, `SniffedFormat` | Active |
| `ocr.py` | Optional local-only OCR; tesseract or rapidocr | `ocr_pdf`, `preferred_engine` | Active, running on rapidocr |
| `redaction.py` | POPIA redaction, validated and narrow; SA IDs also in the spaced `YYMMDD SSSS CAZ` form | `redact`, `_plausible_sa_id`, `_SA_ID_RE` | Active |

## Ontology (`src/labour_corpus/ontology/`)

| Path | Purpose | Key symbols | Status |
|---|---|---|---|
| `dismissal.py` | LRA-structured tag vocabulary incl. the process-defect layer | `ALL_TAGS`, `Tag`, `OntologySchema` | Active |
| `mining.py` | MHSA/MPRDA overlay and the sector decision rule | `ALL_MINING_TAGS`, `MINING_TAG_THRESHOLD` | Active |
| `tagger.py` | Tags with paragraph evidence; final labelled decision section controls outcome/remedy inference; conflicts return unknown | `decisive_text`, `tag_document`, `ParagraphMap`, `TaggingResult` | Active |

## Index and report

| Path | Purpose | Key symbols | Status |
|---|---|---|---|
| `index/fts.py` | SQLite FTS5 build and search; rebuild ends with `optimize` + best-effort `VACUUM` (39.4 -> 27.3 MB measured on a copy) | `FtsIndex`, `SearchHit` | Active |
| `report/coverage.py` | COVERAGE.md generation; explicitly states that dismissal-code supersede checking is manual | `build_coverage_markdown`, `_GAP_LABELS` | Active |

## Tests (`tests/`) — 707 passing, 1 skipped on 2026-10-13 (421 before the review)

| Path | Covers | Status |
|---|---|---|
| `test_parser_saflii_fixture.py` | Citation/party/date/judge extraction, never-hallucinate, tagging, code precedence, Bill of Rights | Active |
| `test_robots.py` | robots semantics incl. multi-group, Content-Signal merge, oversize, non-finite delays; rate limiter host delays (fake clock); Retry-After parsing and trips | Active |
| `test_crawler.py` | **New.** The real Crawler over MockTransport: 5xx gaps, failure totals, resume into unreached documents, one request per URL per run, per-host pace, GapKind = coverage labels | Active |
| `test_idempotency.py` | doc_id stability, duplicate skip, resume, oversize, raw layout | Active |
| `test_redaction.py` | PI removal (incl. spaced SA IDs) and preservation of legal identifiers | Active |
| `test_sources_yaml.py` | Paid-host hard error, registry integrity, strict keys/version/enabled/delays | Active |
| `test_ontology_and_report.py` | LRA coverage, coverage report, inbox, FTS search, tagger paragraph markers | Active |
| `test_citation_map.py` | Listing parsing, held counter, neutral-citation index plan | Active |
| `test_legacy_import.py` | Listing rejection, non-SA exclusion, provenance | Active |
| `test_office_and_ocr.py` | Sniffing, legacy Word, OCR honesty, no temp-dir leak, pypdf not imported by the CLI | Active |
| `test_rtf.py` | **New.** RTF equivalence with the old algorithm, code page, `\uc`, surrogate pairs | Active |
| `test_parse_cli.py` | **New.** `parse` D-008 boundary and OCR re-derivation, rebuild script fail-fast, outcome only on decisions, DOCX limits/malformed XML, inbox boundary, CLI annotations across all sub-apps | Active |
| `test_index.py` | **New.** FTS compaction (1 segment, 0 free pages) and unchanged results | Active |
| `test_packaging.py` | **New.** Every core dependency is imported or a documented transitive pin | Active |
| `test_acceptance_audit.py` | **New.** C5 can fail; deliberate re-fetches are not repeats | Active |
| `test_sqlite_ro.py` | **New.** Read-only connections with `#`, `%20`, spaces in paths | Active |
| `test_tls.py` | Chain completion, signature-verified gate (forged CA refused, real Certum intermediate accepted), AIA cap; no real httpx client construction | Active |
| `test_ccma_engine.py` | CCMA engine AC4-AC11, gates, URL guard (https, port), backoff without real sleeps, stale-reseed, read-only catalogue on a `#` path, MCP | Active |
| `test_ccma_official.py` | Layer 1 rules 1-7, path guard, robots verdicts, scan detection, page count survives any pypdf error | Active |
| `test_navigator_phase2.py` | Golden cases, review sheet, interview, MCP incl. text-cache invalidation | Active |
| `test_navigator.py` | Navigator: models, deadlines, routing (exhaustive Track), grounding (self-identity, copy counts), evidence, engine (future dates), Markdown, CLI exit codes; real-corpus grounding test | Active |
| `golden/navigator_cases.yaml` | 30 golden fact patterns; `pending_lawyer_review` | Active |
| `fixtures/**` | Fictional SAFLII/CCMA pages, mining review, LRA extract | Active |

## Generated at runtime (never edited by hand)

`data/**/raw/`, `text/`, `parsed/`, `db/corpus.sqlite`, `index/fts.sqlite`,
`reports/COVERAGE.md`, `reports/RUN.jsonl`, `reports/ERRORS.jsonl`,
`ontology/*.json` — all **Generated**. CCMA engine: `data/ccma/**`,
`reports/ccma-harvest-*.md`, `logs/ccma-*.jsonl` — **Generated**. Backups:
`data/corpus/db/corpus.backup-2026-10-08.sqlite`, `data/backup-2026-10-09/`
(db, text, parsed, index, reports taken before the 2026-10-09 re-parse).
