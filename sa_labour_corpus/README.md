# sa_labour_corpus

A local-first, polite, resumable harvester for South African labour-law primary
sources. It discovers, downloads, parses, deduplicates, tags and full-text
indexes publicly available instruments and case law into an on-disk corpus with
full provenance, ready for a later retrieval or agent layer.

It carries an unfair-dismissal ontology built on the Labour Relations Act and a
mining-labour overlay built on the Mine Health and Safety Act.

---

## Read this first

> This corpus and any model that reads it are an information aid. They are not a
> legal practitioner. Unfair-dismissal outcomes turn on facts, condonation,
> forum rules and representation. Confirm every citation against the original
> SAFLII or Gazette file before use. Obtain advice from a CCMA commissioner
> information officer, a union official, or an admitted attorney.

**This corpus does not contain "all CCMA cases", and no honest system built on
it may say that it does.** The CCMA has processed on the order of 4.2 million
referrals since 1996, roughly half of them unfair-dismissal matters. Almost none
of those individual awards are published on the free public web. Every run emits
`reports/COVERAGE.md` stating exactly what was and was not collected, and why.

---

## What it actually collects, and what it does not

Access status below was established by live check, not assumed.

| Source | Status | Why |
|---|---|---|
| labour.gov.za (Acts, Codes of Good Practice, Regulations, Sectoral, Forms) | **collected** | Serves an identified crawler |
| gov.za documents | **collected** | Serves an identified crawler |
| mhsc.org.za (Mine Health and Safety Council) | **collected** | Serves an identified crawler |
| gpwonline.co.za (Gazettes) | registered, disabled | Reachable, but a full sweep is a very large crawl. Enable deliberately |
| **saflii.org** (Labour Court, LAC, ZACC, ZASCA, ZACCMA, legislation) | **not collected** | Answers every plain HTTP request, including `/robots.txt`, with an HTTP 403 Cloudflare managed challenge |
| ccma.org.za (referral forms, information sheets, resources) | **collected** | Serves the production user-agent HTTP 200; no robots.txt. Corrected 2026-09-18 — see below |
| mibco.org.za, nbcrfli.org.za, bccei.co.za (bargaining councils) | **collected** | robots permit; each checked individually |
| **lawlibrary.org.za** (Laws.Africa) | **not collected** | `Content-Signal: ai-input=no` — an express reservation against exactly this use |

### Why the blocked sources stay blocked

SAFLII would only yield content if the crawler stopped identifying itself as a
robot and presented as a desktop browser. That is defeating a deliberately
configured access control, and this project does not do it. Laws.Africa has
published a machine-readable reservation of rights against AI input, which is
precisely what a retrieval corpus is; that reservation is honoured.

**A correction about the CCMA.** An earlier version of this file listed
`ccma.org.za` as blocked. That was measured with a short token user-agent such
as `SALabourCorpusBot/0.1 (+mailto:...)`, which its gateway does reject. The
user-agent this project actually sends is served HTTP 200 with real content, and
the site publishes no robots.txt. The CCMA is therefore collected. What has not
changed is that it publishes rules, guidance and forms — **not** its award
archive.

These sources remain **registered** in `sources.yaml` rather than deleted, so
that every run records the gap explicitly instead of quietly pretending the
material does not exist. The lawful ways to close the gaps are listed in
`COVERAGE.md` and summarised under [Growing coverage lawfully](#growing-coverage-lawfully).

---

## Install

Requires Python 3.11 or newer.

### Ubuntu / Debian

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### Windows (PowerShell)

```powershell
py -3.11 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
```

Playwright is optional. It is only used for genuinely JavaScript-rendered pages
on open hosts, never to get past bot management. Install it only if you need it:

```bash
python -m playwright install chromium
```

---

## Quick start

Check everything works, offline, against bundled fixtures:

```bash
labour-corpus smoke --out ./data/smoke --contact-email you@example.com
```

See which sources are registered and what each one's live access status is:

```bash
labour-corpus sources
```

Run a first, deliberately small crawl of 50 documents per source:

```bash
labour-corpus crawl --out ./data/corpus --contact-email you@example.com --max-docs-per-source 50 --i-understand-public-sources
```

Build the search index, then search:

```bash
labour-corpus index --out ./data/corpus
```

```bash
labour-corpus search "section 189 meaningful consultation" --out ./data/corpus
```

Write the coverage and gap report:

```bash
labour-corpus report --out ./data/corpus --contact-email you@example.com
```

### Commands

| Command | What it does |
|---|---|
| `smoke` | End-to-end check on bundled fixtures; `--live` also tries one real fetch |
| `crawl` | Fetch, parse and store from the configured sources |
| `parse` | Re-parse everything under `raw/` without re-fetching |
| `index` | Rebuild the SQLite FTS5 index |
| `search` | Query the local corpus |
| `report` | Write `reports/COVERAGE.md` |
| `ingest-inbox` | Parse operator-supplied documents from `inbox/` |
| `import-citation-map` | Import judgment references from previously harvested court indexes |
| `import-legacy-documents` | Import documents from an earlier local harvest you hold |
| `sources` | List registered sources and their access status |

Every stage is separately re-runnable, and all of them are idempotent.

---

## How it behaves on the network

- **One honest identity.** The User-Agent always declares that the client is a
  robot and always carries `--contact-email`. There is no browser-impersonation
  mode, and the User-Agent is not configurable.
- **robots.txt is obeyed**, including `Crawl-delay`, with RFC 9309 matching
  semantics. One deliberate deviation: a host that answers robots.txt with a
  challenge is treated as refusing automated access, not as "unavailable means
  allow all".
- **`Content-Signal` is obeyed.** A host declaring `ai-input=no` is not a lawful
  source for a retrieval corpus under this user-agent, and is skipped.
- **Politeness**: 1–2 s jittered delay per host, at least the published
  crawl-delay, one request at a time per host, `Retry-After` honoured.
- **Retries** only on 429/5xx/timeouts, exponential backoff, capped at 5
  attempts. Parse failures are never retried into a loop.
- **A challenge trips the host for the run.** It is recorded as a gap and the
  crawl moves on. Nothing attempts to solve, wait out or bypass one.
- **Paid legal publishers are a hard error.** A hostname containing `lexis`,
  `juta`, `sabinet`, `sallr` and similar cannot be loaded as a crawl target;
  `tests/test_sources_yaml.py` fails the build if one ever appears.
- **TLS verification is never disabled.** Some public bodies serve an
  incomplete certificate chain — only the leaf, no intermediate — which
  browsers hide and Python does not. When that happens the crawler fetches the
  missing intermediate from the certificate's own AIA extension, **verifies
  that it chains to a root certifi already trusts**, and uses a completed
  bundle. An intermediate that does not verify is discarded. There is no
  `verify=False` anywhere, and a test asserts there never will be.

---

## Output layout

```
data/corpus/
  raw/{source_id}/{yyyy}/{doc_id}/
    source.html | source.pdf | source.rtf
    headers.json
    fetch_meta.json
  text/{doc_id}.txt          # redacted plain text
  parsed/{doc_id}.json       # the full document contract
  db/corpus.sqlite           # documents, citations, tags, fetch_log, gaps
  db/tls/                    # completed cert bundles for hosts with a broken chain
  index/fts.sqlite           # FTS5 over the text
  reports/COVERAGE.md        # what is and is not here, and why
  reports/RUN.jsonl          # one event per fetch/parse/index action
  reports/ERRORS.jsonl
  ontology/dismissal_schema.json
  ontology/mining_overlay.json
  inbox/                     # operator-supplied documents
```

`doc_id` is a stable hash of the canonical URL plus the body checksum. The same
bytes at the same URL always produce the same id, the same paths and the same
database row, which is what makes re-running safe. Changed bytes produce a new
id, i.e. a new version.

### Resume

`fetch_log` stores the last checksum and any `ETag` / `Last-Modified` the server
offered. A re-run issues a conditional GET, so unchanged documents come back
`304 Not Modified` and are never re-downloaded. Interrupting a crawl with
Ctrl-C flushes state and exits 130; running it again continues from where it
stopped.

---

## The unfair-dismissal ontology

Tags follow the LRA, not a blog taxonomy. Substantive and procedural fairness
are always stored separately.

- **Statutory anchors**: s186 (including s186(1)(e) constructive dismissal and
  s186(2) residual unfair labour practice), s187 automatically unfair, s188 fair
  reason and fair procedure, s189 and s189A operational requirements, s191
  referral and condonation, s193/s194 remedies, s145 review, s198–198D TES, plus
  the EEA and Protected Disclosures Act overlaps.
- **Misconduct, incapacity and operational** pattern tags.
- **Process-defect tags** — the employee-side counter-checks: defective or
  shifting charges, chairperson not impartial, representation or interpretation
  denied, evidence not disclosed, predetermined outcome, inconsistency, no
  progressive discipline, service and record ignored, zero tolerance without a
  proportionality analysis, s189 paper consultation, unfair selection criteria,
  alternatives not considered, forced resignation, after-acquired evidence,
  condonation, and review-versus-appeal confusion.

These are framed as failure modes an employee or their representative can test a
dismissal against. They are not employer guidance.

Every applied tag records the patterns that fired and the **paragraph numbers**
that contained them, read from markers actually present in the text. A document
with no paragraph markers gets an empty paragraph list rather than invented
numbers. Applying `mining` or `automatically_unfair` always writes an explicit
audit reason into the parsed JSON.

### Mining overlay

Mining dismissals are ordinary LRA analysis with the MHSA layered on top. The
overlay recognises MHSA duties, s23 refusal of dangerous work, s54 stoppage
instructions, MPRDA and social-and-labour-plan context, underground and opencast
operations, mining roles, commodities and hazards. A document is only tagged
`mining` when at least two distinct mining signals fire, so one passing use of
the word "mine" does not flip the sector.

---

## Honesty guarantees

These are enforced by tests, not just by intent:

- **A citation is never invented.** Metadata is read out of the document or left
  `null`, with a note saying it was not found. A citation is never reconstructed
  from a URL or a filename.
- **A search with no hits returns no hits**, plus a reminder that absence from
  the corpus is not evidence that no such case exists.
- **`parser_confidence`** describes confidence in the *parsing*, never in any
  legal conclusion.
- **Outcome inference is conservative.** Anything ambiguous stays `unknown`.
- **Titles derived from a filename are labelled as derived** in
  `parser_notes`, because a filename is a label, not a citation.
- **Format is decided by magic bytes**, not by the Content-Type header or the
  URL extension. Government libraries serve Word 97-2003 as
  `application/msword` from `.docx` URLs, and trusting either label sends the
  bytes to the wrong reader.
- **An unreadable file is not automatically a scan.** `unsupported_format`
  means a readable format whose reader is missing; `ocr_needed` means a genuine
  scan. A status that names the wrong remedy is worse than none.
- **OCR text is labelled as a transcription** and its parser confidence is
  capped, so it is never presented as published text.

---

## Privacy and POPIA

Judgments are public records and party names stay as published — there is no
identity enrichment. What is stripped is incidental personal data a parser can
sweep up: South African identity numbers (validated by date, citizenship digit
and Luhn checksum before redaction), labelled bank-account, tax and passport
numbers, telephone numbers and email addresses. Each becomes `[REDACTED_PI]` and
is counted per field in `redaction_counts`.

Redaction is deliberately narrow so it cannot destroy the corpus: case numbers,
neutral citations, statute references, gazette numbers and monetary amounts are
all preserved, and this is tested. Matters that mention a minor or a sexual
offence are flagged in `parser_notes` for human review before any republication.

Nothing leaves the machine. Harvesting requires no LLM, and no document body is
sent to any hosted service.

---

## Growing coverage lawfully

The free web will not give you CCMA awards. The honest routes are:

1. **Ask.** SAFLII, AfricanLII and Laws.Africa respond to research access
   requests, and Laws.Africa offers data agreements. `sources.yaml` already has
   the entries; permission flips them on.
2. **Licence a commercial feed** (Sabinet, Juta, LexisNexis) and use it within
   that licence. This tool will not scrape them, by design.
3. **Use `inbox/`.** Drop documents you already lawfully hold — an award you or
   your client received as a party, or material covered by a licence you hold —
   into `inbox/` and run:

```bash
labour-corpus ingest-inbox --out ./data/corpus
```

Supported: `.pdf .rtf .docx .doc .html .htm .txt`

Legacy Word 97-2003 (`.doc`) is read with [antiword](https://en.wikipedia.org/wiki/Antiword)
when it is on your PATH. Without it those files are stored and flagged
`unsupported_format` rather than silently returning empty text.

Inbox files are parsed with the same ontology and the same redaction as crawled
material. Their provenance is recorded as `operator_inbox` with a `file://`
canonical URL, so nothing in the corpus ever claims a local file came from a
public website. Do not place material there that you are not entitled to hold.

---

## The citation map: measuring the gap

A corpus that says "we have no case law" is honest but useless for planning. A
corpus that says "we hold 0 of 6,410 known labour judgments" is both.

SAFLII publishes year and alphabetical index pages listing every judgment in a
court database. If you have previously harvested those index pages, their
listing lines can be read verbatim:

```
Makhanye v South African Airways (JS 265/13) [2014] ZALCJHB 6 (6 January 2014)
```

Import them with:

```bash
labour-corpus import-citation-map --from /path/to/knowledge_base/documents.jsonl --out ./data/corpus
```

This populates `known_citations` with the case name, case number, neutral
citation, delivery date and any parallel ILJ/BLLR report citations. The
coverage report then states a measured percentage instead of a vague caveat.

Three guarantees, all tested:

- **A reference is never a document.** Importing 6,410 references creates zero
  rows in `documents`, and `search` cannot return one.
- **The URL is derived, not observed.** It is constructed from the citation
  using SAFLII's URL scheme and stored with `url_is_derived = 1`.
- **The held counter really moves.** When the corpus acquires a judgment whose
  neutral citation matches, `link_held_citations` binds it to the exact
  `doc_id`.

Scope is the Republic of South Africa. Botswana, Namibia, Mozambique, East
African and SADC material, and court rolls, are excluded by the parser.

The denominator is a **floor, not a census** — it counts only what the imported
indexes covered.

## Provenance: how a copy was obtained

`canonical_url` says where a document authoritatively lives. It is **not** a
claim about how this copy reached you. That is the `provenance` field:

| Value | Meaning |
|---|---|
| `crawled` | Fetched from the public web by this crawler |
| `operator_supplied` | Dropped into `inbox/` by the operator |
| `legacy_local_harvest` | From an earlier local harvest; never fetched by this crawler |

A legacy or operator-supplied document carries `http_status = NULL`, because
nothing was fetched. COVERAGE.md reports the breakdown, so a reader can always
tell how much of the corpus this tool actually collected.

If you hold an earlier scrape, import its documents with:

```bash
labour-corpus import-legacy-documents --from /path/to/knowledge_base/documents.jsonl --out ./data/corpus
```

Listing pages, court rolls and non-South African databases are rejected at
classification time. A judgment is distinguished from a year index by citation
count and the presence of a court heading, because storing index pages as
documents is exactly what makes a legacy corpus look larger than it is.

## Pointing a RAG layer at the corpus

- **Text** for chunking: `text/{doc_id}.txt` (already redacted).
- **Metadata and filters**: `db/corpus.sqlite` — join `documents` to `tags` and
  `citations`. Filter on `forum`, `year`, `doc_type`, `outcome`, or any ontology
  tag.
- **Keyword retrieval**: `index/fts.sqlite` (FTS5), or use the `search` command.
- **Grounding**: every `parsed/{doc_id}.json` carries `canonical_url`,
  `content_sha256`, `quotes` with paragraph numbers where available, and
  `parser_confidence`.

Put the disclaimer at the top of this README into the system prompt of anything
that reads this corpus, together with the CCMA-incompleteness statement from
`COVERAGE.md`.

---

## Scans and OCR

A PDF with no text layer is stored and flagged `ocr_needed`. No text is
invented for it. Two local engines are supported:

| Engine | Install | Notes |
|---|---|---|
| `tesseract` | system package | Preferred: the better reader for long printed legal text |
| `rapidocr-onnxruntime` | `pip install rapidocr-onnxruntime` | No administrator rights needed; CPU only, slower |

Whichever is present is used automatically, tesseract first. To read those
documents, run:

```bash
labour-corpus parse --out ./data/corpus --ocr
```

OCR is opt-in and entirely local — nothing is sent anywhere. Recovered text is
marked in `parser_notes` as a machine transcription, `ocr_applied` is set, and
parser confidence is capped at 0.5, because a transcription is not the
published wording. Confirm against the original before relying on it.

If tesseract or pymupdf is missing, the run says so and leaves the documents
flagged rather than storing silence.

## CCMA Corpus Engine (`labour-corpus ccma ...`)

A catalogue of published CCMA awards, CCMA practice material and Labour Court
judgments that review CCMA awards (as *related authorities*, never as awards),
with rule-derived case cards, scenarios and strategies, served read-only over a
local MCP server. Design and gap report: `docs/ccma-corpus-architecture.md`.
Verified sources: `docs/ccma-source-map.md`. Allow-list: `ccma_sources.yaml`.

**State on 2026-09-23:** both official award publishers (SAFLII ZACCMA and the
CCMA site) refuse an identified crawler, so no live award source is harvestable.
The engine is proven on fictional fixtures and seeds from local sources.

Gates: a live source is fetched only when its `status` is `verified_open`, the
operator set `operator_confirmed: true`, and the run passes `--allow-network`
and `--popia-ack`. Processing any full text, local sources included, needs
`--popia-ack`, which records that the POPIA lawful basis is stated and the
minimisation rules are accepted.

```bash
labour-corpus ccma sources                               # allow-list and status
labour-corpus ccma harvest --dry-run --limit 5           # nothing saved
labour-corpus ccma find     --allow-network --contact-email you@example.org
labour-corpus ccma scrape URL -s SOURCE --allow-network --contact-email you@example.org
labour-corpus ccma download --allow-network --popia-ack --contact-email you@example.org
labour-corpus ccma seed --popia-ack                      # parse stored files; no network
labour-corpus ccma seed --popia-ack -s local_corpus      # LC/LAC judgments already held
labour-corpus ccma compile                               # rebuild scenarios, export JSON
labour-corpus ccma harvest --allow-network --popia-ack --limit 5 --contact-email you@example.org
labour-corpus ccma troubleshoot                          # failures, remedies, disk, secrets
labour-corpus ccma robots -s official_publication --allow-network   # robots.txt only
```

`--source`/`-s` takes a source id or a source type (`official_publication`
selects every source of that type). `--contact-email` can also come from
`LABOUR_CORPUS_CONTACT_EMAIL`.

Awards you already lawfully hold go in `data/ccma/inbox/`, then
`labour-corpus ccma harvest -s operator_inbox --popia-ack`.

Outputs: `data/ccma/catalogue.sqlite`, `data/ccma/raw/{source}/{id}/`
(original + `.sha256`; treat as personal information),
`data/ccma/knowledge/{kind}/*.json`, `reports/ccma-harvest-{run}.md`,
`logs/ccma-audit.jsonl`, `logs/ccma-events.jsonl`.

### Layer 1: official publications (`-s official_publication`)

Sources of `kind: official_publication` are official publishers' guidance and
gazetted instruments. They are seeded as `practice_note` or `statute`, never as
awards, and yield no case cards. Beyond the gates above, each one:

* is confined to allow-listed **paths** as well as hosts, checked before every
  request and after redirects;
* is refused before its first request unless its robots/ToS notes are in
  `docs/ccma-source-map.md`;
* is refused as a whole if robots.txt refuses any allow-listed path; the
  operator then sets `enabled: false` (no browser, no other route);
* is fetched by plain GET only (`harvest_mode: allowlisted_http`).

`operator_confirmed: pending_robots_check` confirms a source on condition that
robots.txt permits its paths, re-checked at the start of every run.

```bash
export LABOUR_CORPUS_CONTACT_EMAIL=you@example.org                  # or pass --contact-email
labour-corpus ccma robots  -s official_publication --allow-network            # robots.txt only
labour-corpus ccma harvest -s official_publication --allow-network --dry-run --limit 10
labour-corpus ccma harvest -s official_publication --popia-ack --allow-network --limit 20
```

State on 2026-09-23: the CCMA refuses robots.txt (HTTP 403), so
`ccma_official_publications` is disabled. gov.za allows the one allow-listed
path, and GG 53294 / Notice 3470 of 2025 (the Code of Good Practice:
Dismissal) is held as a `statute`. Its body is a scanned image, so it is
catalogued `ocr_needed` and is not yet searchable.

### MCP server

```bash
python mcp/ccma_server.py --root .
```

Standard library only; stdio transport. Tools: `ccma_search`, `ccma_get`,
`ccma_strategies`, `ccma_scenarios`, `ccma_coverage`, and `ccma_refresh`, which
refuses unless the call passes `allow_live_harvest=true` **and** the server runs
with `CCMA_MCP_ALLOW_LIVE_HARVEST=1` (off by default). Claude Code registration:

```bash
claude mcp add ccma-corpus -- python "D:/Work/Repositories/Development/SA Labour LAW/sa_labour_corpus/mcp/ccma_server.py"
```

Every strategy note cites a catalogue `native_id` and ends with: *Not legal
advice. Verify against the full award and current CCMA Rules.*

## Dismissal Navigator (`labour-corpus navigate ...`)

The product layer over the corpus. Given a few facts about a dismissal, it
answers the employee's questions: what kind of dispute this is, **the referral
deadline**, which forum to go to, the compensation ceiling, and what to test
the process against. Every legal statement quotes the statute, and the quote is
re-found in a held, non-draft copy of the Act on every run. Design and roadmap:
`docs/navigator-architecture.md`.

```bash
# Check that every quoted provision is in a held statute (exit 3 if not)
labour-corpus navigate provisions

# Assess a case (Markdown to stdout, JSON logs to stderr)
labour-corpus navigate assess examples/misconduct_after_appeal.json
labour-corpus navigate assess examples/constructive_late.json --as-of 2026-10-07 --json
echo '{"dispute": "unfair_labour_practice", "event_date": "2026-08-01"}' \
  | labour-corpus navigate assess -

# Guided plain-language questions instead of JSON (--save keeps the facts)
labour-corpus navigate interview --save facts.json

# The golden-case sheet a labour lawyer signs off (exit 1 if any case drifts)
labour-corpus navigate review-sheet > navigator-review.md

# MCP server: navigator_assess, navigator_provisions (stdio, read-only)
claude mcp add dismissal-navigator -- python mcp/navigator_server.py
```

The 30 golden fact patterns in `tests/golden/navigator_cases.yaml` are the
author's reading of the LRA and EEA and are **pending lawyer review**: until a
case carries `reviewed_by` and `reviewed_on`, its expectation is not a legal
sign-off. The MCP tool rejects any field it does not know, so a client cannot
send a name or ID number.

Facts (`CaseFacts`): `dispute` (`dismissal`, `unfair_labour_practice`,
`discrimination_not_dismissal`), `reason` (`conduct`, `capacity`,
`operational_requirements`, `automatically_unfair`, `constructive`,
`unprotected_strike`, `closed_shop`, `unknown`), `auto_unfair_ground`,
`event_date`, `final_decision_date`, `became_aware_date`, `certificate_date`,
`council` (`yes`/`no`/`unknown`), `council_name`,
`employer_has_fewer_than_10_employees`, `only_this_employee_retrenched`,
`probation`. No names or ID numbers are accepted.

Exit codes: 0 assessed and grounded; 2 invalid facts (including a decision,
certificate or became-aware date after the assessment date); 3 some relied-on
wording was not found in the held statutes, or could not be checked because no
corpus, or no held copy that identifies itself as the Act, was found. Deadlines are calendar days and are never
extended for weekends or holidays: the output says when a date falls on one.
It is general legal information, not legal advice, and it says so.

## Tests

```bash
python -m pytest
python -m pytest tests/test_navigator.py --cov=labour_corpus.navigator --cov-report=term-missing
```

The suite never touches the network and never requires a browser. Live tests are
marked `live` and browser tests require `RUN_BROWSER=1`.

---

## Licence and attribution

The code is MIT. The **documents are not** — each carries a `license_note`
recording its terms. SAFLII publishes for free access; local copies are for
private research use. Government material is Republic of South Africa
publication, and the Gazette remains the only authoritative text. Confirm every
citation against the original file before relying on it.
