# CCMA Corpus Engine — architecture and gap report

**Kind of work: upgrade, not greenfield.** The workspace already held a mature
local-first labour-law harvester (`sa_labour_corpus/`, package `labour_corpus`).
The CCMA engine was added as a subpackage, `labour_corpus.ccma`, reusing its
stack (Python 3.11+, httpx, SQLite/FTS5, Typer) and its collection policy. No
existing behaviour was rewritten; one shared class gained an optional parameter
(below).

Written 2026-09-23. Research and retrieval tool; not legal advice.

---

## 1. Existing capabilities (before this work)

| Capability | Where | State |
|---|---|---|
| Polite HTTP: one honest, non-configurable User-Agent, robots.txt (RFC 9309), `Content-Signal`, rate limit, conditional GET, retry on 429/5xx, challenge detection and host tripping | `http.py`, `robots.py`, `challenge.py`, `config.py` | Working, tested |
| TLS chain completion for hosts that omit the intermediate (the CCMA does), with verification kept on | `tls.py` | Working (D-013) |
| Source registry with observed access status; paid publishers a hard error | `sources/registry.py`, `sources.yaml` | 26 sources, 14 collectable |
| Link discovery; SAFLII year-index and judgment-link walkers | `sources/links.py`, `sources/saflii.py` | Written and tested; SAFLII itself blocked |
| CCMA instruments | `ccma/find.py` + `ccma/extract.py` | Discovers and parses configured rules/forms; no bulk award API |
| Format sniffing, PDF/Word/RTF/HTML text, optional local OCR | `parse/*.py`, `pipeline.py` | Working |
| Judgment/award metadata read verbatim or left null, incl. CCMA case numbers (`GAJB1234-24`) | `parse/judgment.py` | Working |
| POPIA redaction (validated SA ID, labelled bank/tax, phone, e-mail) | `parse/redaction.py` | Working, narrow by design (D-007) |
| 73-tag LRA-structured dismissal ontology with paragraph evidence; outcome/remedy heuristics | `ontology/*` | Working |
| Provenance-tracked corpus store; FTS5 index; COVERAGE.md | `state.py`, `index/fts.py`, `report/coverage.py` | 397 documents, 12 LC/LAC judgments, 0 CCMA awards |
| Tests | `tests/` | 242 passing |

**What did not exist:** anything CCMA-award-specific: a catalogue keyed by
`(source, native_id)`, checksum quarantine, a failure taxonomy with
retryable/non-retryable classes, derived knowledge (case cards, scenarios,
strategies), per-run coverage reports with a denominator, a decision audit log,
HITL gates for POPIA and network, and an MCP query surface.

**What fails, and why (unchanged by this work):** no free source of CCMA awards
serves an identified crawler today. See `ccma-source-map.md`. That is policy
(D-001, D-002), not a defect.

## 2. Gap analysis against the spec

| Spec item | Before | After |
|---|---|---|
| O1 architecture + gap report | – | this file |
| O2 verified source map | partial, in `sources.yaml` comments | `docs/ccma-source-map.md`, `ccma_sources.yaml` |
| O3 find / scrape / download / seed / troubleshoot as separate operations | crawl + parse only | `labour-corpus ccma find|scrape|download|seed|troubleshoot` (+ `harvest`, `compile`) |
| O4 catalogue | corpus.sqlite (documents) | `data/ccma/catalogue.sqlite` with the spec's five tables plus `discovered`, `checkpoints`, FTS5 |
| O5 raw artefacts + checksum sidecar | `raw/{source}/{year}/{doc_id}/` | `data/ccma/raw/{source}/{native_id}/original.* + .sha256 + meta.json`, `quarantine/` |
| O6 knowledge objects | tags only | case cards, scenarios, strategies in the catalogue and `data/ccma/knowledge/{kind}/*.json` |
| O7 coverage + troubleshooting report | COVERAGE.md (whole corpus) | `reports/ccma-harvest-{run_id}.md` per run |
| O8 local MCP server | – | `mcp/ccma_server.py` (stdlib), also `labour-corpus ccma mcp` |
| O9 decision audit log | – | `logs/ccma-audit.jsonl` (+ `logs/ccma-events.jsonl`) |
| Retry 1s/4s/16s, 20 s timeout, concurrency 2 | exponential 2–60 s, 5 tries, 45 s | engine passes `retry_waits=(1,4,16)` and `timeout=20` to the shared `Fetcher` |
| Checkpoint every 100, progress every 25, disk floor 500 MB | – | `harvest.py` |

**Shared-code change:** `http.Fetcher` gained an optional `retry_waits` tuple.
Default `None` keeps the crawler's existing policy, so the main corpus is
unaffected (all 242 prior tests still pass).

## 3. Components

```
ccma_sources.yaml ─► allowlist.load_allowlist ─► per-source gates (live_refusal)
                                                     │
            ┌────────────────────────────────────────┤
            ▼                                        ▼
   find.py (listing parsers,               local adapters
   pure functions over HTML)               (main corpus / operator inbox)
            │                                        │
   scrape.LiveGetter ── validate_url (before + after redirect)
            │           └─► http.Fetcher (robots, content-signal, challenge,
            │                             rate limit, retry 1/4/16 s)
            ▼
   download.store_artifact ── sha256 sidecar ── same: skip │ different: quarantine
            ▼
   extract.parse_artifact ── strip controls ─ text ─ redact ─ parse_judgment ─ tag
            ▼
   seed.seed_one ── catalogue.upsert_record (idempotent on (source, native_id))
            │   └─ knowledge.build_record_units ── minimise ── assert_minimised
            ▼
   seed.compile_scenarios (≥ 2 cards sharing dispute type + material issue)
            ▼
   report.write_harvest_report ─► reports/ccma-harvest-*.md
   troubleshoot.diagnose        ─► remedies per error class, disk, secret scan
   query.py (stdlib, read-only) ─► mcp_server.py ─► mcp/ccma_server.py (stdio)
```

## 4. The five capabilities

| Capability | Entry point | Notes |
|---|---|---|
| **Find** | `ccma find` / `find.find_live`, `find_local_*` | Walks published indexes only; never enumerates case numbers. A failing year page is an error row, not the end of discovery. |
| **Scrape** | `ccma scrape URL -s SOURCE` / `scrape.inspect_record` | Fetch + parse one record, persist nothing: the pre-flight check against selector drift. |
| **Download** | `ccma download` / `download.store_artifact` | Original bytes, SHA-256 sidecar, `retrieved_at`, content type, source URL. Different checksum → quarantine + `checksum_conflict` row; the held file is never overwritten. |
| **Seed** | `ccma seed` / `seed.seed_one` | Upsert on `(source, native_id)`; unchanged bytes and parse → `skipped`. Parse failures stored with status and no knowledge. |
| **Troubleshoot** | `ccma troubleshoot` / `troubleshoot.py` | Classes: `http_403/404/429/5xx/other`, `timeout`, `network`, `empty_body`, `parse_miss`, `parse_unsupported`, `checksum_conflict`, `disk_full`, `schema_drift`, `policy_block`, `minimisation_failed`. Only 429/5xx/timeout/network are retryable. |

`ccma harvest` runs find → download → seed → compile → report.

## 5. Data model

Catalogue tables (`catalogue.py`): `sources`, `records`, `knowledge_units`,
`run_log`, `errors` as specified, plus `discovered` (find output),
`checkpoints`, and FTS5 tables `records_fts` / `knowledge_fts`.

* `records.record_kind` ∈ `award | related_authority | practice_material`.
  Labour Court and LAC judgments are **related authorities**, never awards.
* `records.provenance` ∈ `crawled | operator_supplied | legacy_local_harvest`.
* `run_log.completeness_claim` has a `CHECK (completeness_claim = 0)`: the
  database itself refuses a completeness claim.
* `knowledge_units.confidence` ∈ `high | medium | low` (CHECK constraint).

Knowledge units (`knowledge.py`) are **rule-derived, never generated by a
language model**:

* **Case card**: citation (case number or neutral citation, read verbatim),
  forum, year, dispute type (with the tag evidence that decided it), issues with
  paragraph locators, the finding sentence quoted verbatim with its paragraph,
  outcome label, legislation the text itself cites.
* **Scenario**: only where ≥ 2 parsed cards share a dispute type and a material
  issue; records the elements that co-occurred, the outcome range *observed in
  those records*, and contra records.
* **Strategy**: research notes, each tied to at least one `native_id`. `high`
  only for a verbatim quoted finding (and every `high` is written to the audit
  log); issue notes are `medium`/`low`. Fixed footer: "Not legal advice. Verify
  against the full award and current CCMA Rules."

## 6. Gates, privacy and security

| Gate | Enforced in |
|---|---|
| Live fetch needs `status: verified_open`, `enabled`, `operator_confirmed: true`, `--allow-network`, `--popia-ack` | `CcmaSource.live_refusal` |
| Processing any full text (incl. local sources) needs `--popia-ack` | `Harvester._run_source` |
| Every URL checked against the source's hosts before the request and after redirects; `file:`, other schemes, credentials in URLs, IP literals refused | `allowlist.validate_url`, `scrape.LiveGetter` |
| robots.txt, `Content-Signal: ai-input=no`, bot-management challenges | shared `http.Fetcher` (D-001, D-002, D-004) |
| Knowledge units carry no ID numbers, e-mails, phones or addresses; the FTS index gets the same minimisation | `minimise.py`, `assert_minimised` (unit dropped + `minimisation_failed` row if anything survives) |
| Award text is untrusted: C0/C1 controls and bidi overrides stripped; nothing in it is executed or followed | `extract.strip_controls` |
| MCP live harvest off unless the call passes `allow_live_harvest=true` **and** the server has `CCMA_MCP_ALLOW_LIVE_HARVEST=1` | `mcp_server.Server._refresh` |
| Secrets only from environment; reports and logs scanned for secret env values and token shapes | `troubleshoot.scan_for_secrets` |

`data/` is gitignored; `data/ccma/raw/` must be treated as containing personal
information.

## 7. Bounds and observability

Defaults: `--limit 50` per source, concurrency 2 (max 8), 20 s timeout, retries
after 1/4/16 s, per-source delays from the allow-list (CCMA 8–15 s), checkpoint
every 100 records, progress line every 25, abort a source below 500 MB free.

Events (`logs/ccma-events.jsonl`): `run_started`, `source_enabled`,
`source_skipped`, `record_found`, `fetch_ok`, `fetch_fail`, `seed_ok`,
`quarantine`, `parse_fail`, `run_finished`, plus `http` stage events.

Audit (`logs/ccma-audit.jsonl`): `source_skipped`, `tos_interpretation`,
`popia_acknowledged`, `knowledge_confidence_high`, each with a `reason`.
Selector changes are code changes and must be recorded in `.index/key-decisions.md`
with the fixture that motivated them.

## 8. Acceptance criteria — evidence

| # | Criterion | Evidence |
|---|---|---|
| 1 | O1 exists, states upgrade vs greenfield, has "Existing capabilities" | this file, §1 |
| 2 | O2 lists only verified or "unverified — do not harvest" sources; each harvest-enabled source has a robots/ToS note | `ccma-source-map.md`; `load_allowlist` rejects a `verified_open` source without `tos_notes`; `test_shipped_allowlist_loads_and_no_award_source_is_confirmed` |
| 3 | Five separately invokable operations | CLI commands in §4; `test_stage_commands_are_separately_invokable` |
| 4 | `--dry-run --limit 5` writes no raw files | `test_ac4_dry_run_limit_5_writes_no_raw_files`; also run on the real workspace 2026-09-23: 0 files under `data/ccma/raw` |
| 5 | Re-seed does not duplicate rows; `run_log` shows skips | `test_ac5_reseeding_the_same_fixture_does_not_duplicate_rows` |
| 6 | Forced checksum mismatch quarantines + `errors` row | `test_ac6_checksum_mismatch_is_quarantined_with_an_error_row` |
| 7 | Fixture award → case card citing its case number | `test_ac7_fixture_award_yields_case_card_citing_its_case_number` (`GAJB1234-24`) |
| 8 | No ID number / e-mail from the fixture in any knowledge unit | `test_ac8_...` checks units, exported JSON and the FTS index; a mutation check (minimiser disabled) makes it fail |
| 9 | MCP `ccma_search` / `ccma_get` answer from disk with network disabled | `test_ac9_...` (sockets patched to raise), `test_mcp_launcher_script_serves_over_stdio` |
| 10 | O7 has counts and failure classes, no completeness claim | `test_ac10_...`; phrase absent; DB constraint |
| 11 | No secrets in `reports/` or `logs/` | `test_ac11_...`; `ccma troubleshoot` runs the scan |

Definition of done is criteria 1–11. **Not done, and not blocking** (optional
extras): OCR of scanned awards (the main corpus's `parse --ocr` exists; the
engine records `ocr_needed` and waits for the operator), a Labour Court
related-authority graph, embeddings, a UI.

## 9. Known limits

* The live award sources are blocked today, so the engine's live path is proven
  on fixtures and against the real hosts' refusals, not on a live award sample.
* Dispute type and outcome are rule-based readings with their basis recorded;
  confirm against the source.
* Scenario "fact patterns" are structural (shared dispute type and issue), not
  narrative summaries; there is deliberately no language model in the loop.
* SQLite commits on the `D:` drive were observed to be slow (a 7-source dry run
  took about a minute, mostly I/O). Not a correctness issue.

## 10. Layer 1: official publications (added 2026-09-23, D-021)

A fifth source kind, `official_publication`, for official publishers' guidance
and gazetted instruments. It runs through the same find → download → seed
path with four additions:

| Addition | Where | Operator rule |
|---|---|---|
| Path allow-list (`allowed_paths`; a trailing `/` is a prefix, anything else is exact), enforced before each request and after redirects. Dot segments and encoded separators are refused. | `allowlist.path_allowed`, `validate_url` | 3, 4 |
| Notes gate: the source id must appear in `docs/ccma-source-map.md` and the entry must carry `tos_notes`. Checked before any request, the robots check included. | `AllowList.notes_refusal` | 1 |
| Robots preflight: robots.txt for one URL per allow-listed path, read with the production client. One refusal refuses the source for the run (`policy_block` row). Also available alone as `ccma robots`. | `scrape.robots_preflight`, `Harvester._robots_preflight` | 2, 7 |
| Records are `practice_note` or `statute` (the loader rejects anything else). Non-decision kinds get no case number, citation, outcome, dispute type or knowledge unit, whatever their text cites. | `RecordKind.is_decision`, `extract.parse_artifact`, `knowledge.build_record_units` | 6 |

Discovery (`find.find_official`): exact `urls` first, fetched without a
listing. Then, for each `listing_urls` page: its linked documents, one level of
`detail_paths` pages, and `page/N/` pagination, bounded at 20 listing pages and
100 detail pages. Native ids derive from the URL path alone, so a re-run maps
each URL to the same id.

**Scan detection.** A multi-page PDF whose text layer, once lines recurring
across pages are set aside, holds under 40 characters per page is
`ocr_needed`, not `parsed`. The first real Layer 1 file, GG 53294, is exactly
that: 16 pages of running heads over a scanned notice. The shared
`classify_text` rule (under 200 characters in total) did not catch it.
Statutes also get number and title-word checks against the text, and a draft
check that reports "not possible" rather than "passed" when there is no body
text. Limit: a scan with a text-layer cover page can still pass.

`--contact-email` may come from `LABOUR_CORPUS_CONTACT_EMAIL`.
