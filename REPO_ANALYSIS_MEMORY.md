# Repository Analysis State — SA Labour Law Corpus

## Current Analysis Phase & Progress

**2026-10-09: Holistic review COMPLETE; corpus re-parsed.** 45 of 72 review
findings closed (40 distinct defects) plus 15 found while fixing; decisions
D-025..D-029. Final: 705 tests green, ruff clean, mypy = baseline (2 env-only),
navigator 21/21 verified with honest copy counts, golden 30/30, acceptance
10/11 (C5 now honestly fails on the 2026-09-20 RUN.jsonl repeats; rotate it).
Corpus diff vs `data/backup-2026-10-09/` is exactly the intended set (see
`.index/context-refresh-log.md`). **Waiting on humans:** the items under
"Next Immediate Steps" item 0000.

History of this session, as it unfolded: Baseline measured
before any change: 421 tests green (74.5 s), `ruff check .` clean, mypy 2
errors (both optional deps absent from the check env). An **unlogged pass ran
2026-10-09 01:35-02:10**: it added the `lint` extra (ruff 0.13.3, mypy 1.18.2,
types-PyYAML), `[tool.mypy]` config, and touched ~30 modules (typing fixes).
It was not recorded in `.index/` or here; this entry records it. Source
snapshot for diff/rollback: `analysis_logs/baseline-2026-10-09/` (not a git
repo; copied out of %TEMP% so it survives cleanup, D-017).

Review done (15 agents, 7 dimensions + adversarial verify + critic): **72
findings, 1 refuted.** Full evidence in `analysis_logs/review-2026-10-09-*`.
Most serious: TLS AIA gate accepted a forged intermediate by issuer *name*
(D-013 not enforced); spaced SA ID numbers survive redaction (1 real case in
corpus + FTS); robots.txt honoured only the first matching group (D-002 hole);
per-source 8-15 s CCMA delays never applied (D-012 dead config); a persistent
5xx ended a whole crawl; OCR'd docs keep metadata derived from empty text (27
docs: 0 tags, a draft not flagged); outcome labels stored on statutes; the
navigator counted fund rules/forms/a code as "copies" of the LRA/EEA. Fix
workflow applying ~38 verified, test-provable fixes on disjoint file sets.
**Deliberately not auto-fixed (need a human):** certificate-issued deadline /
condonation logic and s136(1)(b) (lawyer), outcome-label inference from the
order section and CCMA holdings (corpus diff review), TLS bundle hardening,
git init/CI, project venv, pymupdf (AGPL) vs pypdfium2, deleting dead adapters.

**2026-10-08: Navigator phase 2 done except what needs people.** Interview,
MCP server, 30 golden cases with a review sheet, and the Act doc-type fix (D-024)
with a full corpus re-parse (63 reclassified, nothing else changed). 419 tests
green. **Waiting on humans:** lawyer sign-off of the golden cases; Form 7.11
(not held); GG 53294 verification.

**2026-10-07: Phase shift from collection to product. Dismissal Navigator built
(D-022, D-023).** `labour_corpus.navigator` + `labour-corpus navigate
assess|provisions`. It turns stated facts into classification, referral
deadlines, forum route, remedy ceiling, onus and a fairness checklist. Every
legal statement quotes LRA/EEA wording that is re-found in held statutes on each
run: 21/21 verified. 373 tests green (49 new, 99% package coverage). Vision and
roadmap: `sa_labour_corpus/docs/navigator-architecture.md`. **Finding:** the
labour.gov.za LRA PDF is a pre-2014 consolidation (D-023).

**2026-09-23 (later): Layer 1 official publications automated (D-021).**
`labour-corpus ccma harvest -s official_publication --popia-ack --allow-network
--limit 20` works as specified. 324 tests green (43 new). CCMA robots.txt is
403, so `ccma_official_publications` is disabled (rule 2). gov.za allowed the
path, and GG 53294 / Notice 3470 of 2025 is held as a `statute`
(`ocr_needed`: its body is a scanned image). A one-off OCR read says it is the
**gazetted Code of Good Practice: Dismissal**, in force on publication
(4 September 2025), repealing Schedule 8. That answers Q4/Q5, subject to a
human checking the OCR wording against the image.

**2026-09-23: CCMA Corpus Engine built (upgrade, D-019/D-020).**
`labour_corpus/ccma/` + `mcp/ccma_server.py` + `docs/ccma-*.md` +
`ccma_sources.yaml`. 281 tests green (36 new). Acceptance criteria 1-11 met on
fixtures; dry run verified on the real workspace. **Blocked on the operator**
for P0: (b) source allow-list confirmation, (c) POPIA lawful basis, (d)
`--allow-network`. No live award source is open today (SAFLII 403 challenge,
CCMA 403 bot refusal, lawlibrary ai-input=no), so the realistic next run is
`ccma harvest -s local_corpus --popia-ack` (12 LC/LAC related authorities) plus
anything the operator drops in `data/ccma/inbox/`.


**Phase: rebuilt and verified at a durable path. 397 documents, all readable.**
`sa_labour_corpus/` is built, tested and run against the live web. All 11
acceptance criteria pass with recorded evidence. 42 Python modules, 11 test
modules, 223 tests green.

**The 2026-09-18 corpus was destroyed by Windows temp cleanup** (D-017): it
lived under `%TEMP%/lc_live2`, and 187 documents, the citation map and 11 OCR
transcriptions went with it. Nothing was unrecoverable -- the corpus is derived
data and every stage is re-runnable -- and it is being rebuilt at the durable
path `sa_labour_corpus/data/corpus` via `rebuild_corpus.sh`. **Never point
`--out` at a temp directory**; `crawl` and `parse` now warn when you do.

**Rebuilt: 397 documents, 397 readable, 12 labour judgments, 4 drafts flagged
as not-law.** More than double the lost corpus, because earlier crawls were
always capped or source-limited and had never swept `del_forms` (165) or
`del_sectoral_determinations` (43). 242 tests green, 11/11 acceptance criteria
verified by `tools/acceptance_audit.py`. Coverage
against the known universe is **12 of 6,410 (0.2%)**. **All 12 target labour
Acts are held** (LRA, BCEA, EEA, NMWA, COIDA, UIA, PEPUDA, MHSA, MPRDA, OHSA,
PDA, SDA). Live crawling of case law is still barred by D-001; the judgments
present came from the legacy import (D-010), not from SAFLII.

**The CCMA and three bargaining councils now collect** (D-012, D-013): CCMA
referral forms, information sheets and resources; MIBCO, NBCRFLI and BCCEI
constitutions and collective agreements.

## Key Architectural Insights Discovered

- **The access split, corrected 2026-09-18.** Government hosts
  (`labour.gov.za`, `gov.za`, `mhsc.org.za`, `gpwonline.co.za`), the CCMA and
  three bargaining councils all serve an honestly identified crawler.
  **`www.saflii.org` alone is blocked**: HTTP 403 behind a Cloudflare managed
  challenge on *every* request, including `/robots.txt`.
- **The CCMA was wrongly recorded as blocked (D-012, D-013).** Two separate
  causes. First, the 2026-09-17 probe used a bare token user-agent the crawler
  never sends; the production user-agent gets HTTP 200. Second, and the real
  blocker, `www.ccma.org.za` serves an **incomplete certificate chain** (leaf
  only, no intermediate), so httpx failed where curl succeeded on its own CA
  bundle. `tls.py` completes the chain via AIA with verification intact.
- **`lawlibrary.org.za` (Laws.Africa) publishes a machine-readable reservation
  of rights**: `Content-Signal: ai-train=no, search=yes, ai-input=no` for
  `User-agent: *`, plus `Disallow: /judgments/`. `ai-input` covers RAG and
  grounding, i.e. this corpus's exact purpose. Named search agents get
  `ai-input=yes`; this crawler is not one.
- **The brief's seeded `labour.gov.za/Legislation/*` paths are HTTP 404.** The
  real structure is a SharePoint document centre:
  `/DocumentCenter/Pages/{Acts,Code-of-Good-Practice,Regulations,Sectoral,Forms}.aspx`
  → folder pages (`?RootFolder=...`) → PDFs. Both levels work over plain HTTP;
  no browser is required.
- `labour.gov.za` honours conditional GETs (ETag + Last-Modified → 304), which
  is what makes resume genuinely free of re-downloads.

## Files Deeply Reviewed

Everything under `sa_labour_corpus/src/labour_corpus/` was written and verified
in this session. Highest-leverage modules:

- `config.py` — `build_user_agent` is the integrity chokepoint: one honest UA,
  not configurable. `assert_not_paid_host` is a hard error.
- `robots.py` — RFC 9309 matching plus `ContentSignal`. Deliberate deviation:
  a challenge on robots.txt means disallow, not allow-all.
- `http.py` — rate limiter, tenacity retry on 429/5xx only, conditional GET,
  host tripping on challenge.
- `parse/judgment.py` — the never-hallucinate guarantee lives here. Handles both
  `JR 1487/21` and CCMA `GAJB12345-24` formats; commissioner extraction rejects
  prose about a commissioner.
- `ontology/dismissal.py` + `tagger.py` — 73 tags, LRA-structured, with
  paragraph-level evidence for every applied tag.
- `sources/crawler.py` — every refusal becomes a row in `gaps`.
- `report/coverage.py` — the honesty contract, rendered.

## Legacy corpus assessed (2026-09-17)

`D-Offload/.../CascadeProjects/windsurf-project/` + its mirror at
`H-Offload/.../downloaded_documents`. **It is not a labour corpus.** 4,891
records resolve to 201 distinct titles because index pages were stored as
documents: 1,240 copies of one Gauteng court roll, 992 Botswana Court of Appeal
records, 121 records titled with a Cloudflare interstitial ("We're getting
things ready"), and only 12 unique labour judgments — one duplicated 23x on disk
and mis-filed under `UNKNOWN/`.

Its real assets: 154 SAFLII labour index pages (imported as 6,410 references)
and 89 consolidated Acts (LRA, BCEA, EEA, MHSA, COIDA, NMWA present; UIA, PDA,
OHSA absent — and all three are fetchable from labour.gov.za).

**Now ingested (D-010):** the 12 judgments and 65 unique Acts, with
`provenance = legacy_local_harvest` so nothing implies this crawler fetched
them. 146 listing pages rejected, 2,530 non-SA records excluded.

## Open Questions & Areas Needing Investigation

- Q1: Will SAFLII / AfricanLII grant research access on request? This is the
  single highest-value unknown; it converts the largest gap into coverage with
  no new code.
- Q2: Does Laws.Africa offer a data agreement that would set `ai-input=yes` for
  an identified agent? Their `/api/` is disallowed for anonymous crawlers, which
  reads like "get a key".
- Q3: Which bargaining councils publish constitutions and main agreements under
  a robots policy that permits collection? Each needs an individual check.
- Q4 **ANSWERED, and the answer matters (D-016).** The Department publishes
  only a **DRAFT** Code of Good Practice on Dismissal for public comment -- its
  Codes of Good Practice / Labour Relations folder contains that draft and
  nothing else -- and no gazetted final code was found on gov.za. The brief's
  GG 53294 / GN 3470 / 4 September 2025 details are now marked
  `verified: False` in `del_gov.CURRENT_DISMISSAL_CODE`. **Confirming the
  operative dismissal code against the Government Gazette is the single most
  important outstanding legal-accuracy task.**
- Q5 **ANSWERED 2026-09-23 (pending a human read of the image).** Yes:
  GG 53294, 4 September 2025, DEL Notice 3470 of 2025, fetched from
  `https://www.gov.za/sites/default/files/gcis_document/202509/53294gen3470.pdf`.
  The text layer carries only running heads (GG no., date, "NOTICE 3470 OF 2025").
  OCR gives: the Code of Good Practice: Dismissal, issued under s203(2A) after
  NEDLAC; effective on publication; repeals Schedule 8 and the 1999
  operational-requirements code (GN 1517 / GG 20254). The details in
  `del_gov.CURRENT_DISMISSAL_CODE` match, but `verified` is still False there.
  Flipping it is main-corpus work and was left to the operator.

## Decisions Made & Rationale

Full ADRs are in `.index/key-decisions.md` (D-001 … D-009). The load-bearing
ones:

- **D-001 — do not defeat bot-management controls.** Both blocked hosts would
  yield content if the crawler presented as a browser. That is circumvention of
  an access control, so it is not done; the gap is reported instead. **Do not
  "fix" the SAFLII adapter by adding a browser user-agent.**
- **D-002 — honour `ai-input=no`.** A CC-BY licence on content does not override
  the operator's stated position on automated collection.
- **D-003 — blocked sources stay registered**, so COVERAGE.md can state what is
  missing and a future permission flips a flag rather than needing new code.
- **D-006 — titles may be derived from a filename; citations may not.**
- **D-009 — import judgment references from legacy indexes.** A reference is
  never a document; the held counter is what makes coverage measurable.
- **D-011 — decide format by magic bytes; an unreadable file is not a scan.**
  14 Word 97-2003 files were wrongly flagged `ocr_needed`; `unsupported_format`
  now names the right remedy.
- **D-010 — import the legacy documents too, with `provenance` recorded.**
  `canonical_url` says where a document lives; `provenance` says how this copy
  was obtained. Supersedes D-009's "references only".
- **D-025..D-029 (2026-10-09).** robots read most restrictively (every
  matching group must allow; oversize/unhonourable delays trip the host);
  AIA intermediates trusted only by signature against a certifi root; outcome
  and remedy only on judgments/awards; a "held copy of the Act" must
  self-identify as the Act; OCR text may not retype or retitle by mention.

## Next Immediate Steps

0000. **After 2026-10-09 (in priority order):** (a) `git init` at the project
      root + baseline commit (the `.gitignore` is ready) — nothing today is
      revertible except via the scratchpad snapshot and `data/backup-2026-10-09/`;
      (b) a labour lawyer reviews the conciliation-certificate deadline logic
      (navigator says "condonation needed" with a certificate on file; no
      s136(1)(b) 90-day arbitration deadline) together with the 30 golden cases;
      (c) fix outcome inference from the order section (4 of 6 judgment labels
      wrong, 2 inverted) and CCMA `find_holding` before any `ccma harvest -s
      local_corpus`; (d) decide the COVERAGE.md "supersede check" wording (the
      check does not exist); (e) project `.venv` + `pip install -e ".[test,lint]"`
      (refreshes stale editable metadata); (f) rotate `reports/RUN.jsonl` before
      the next crawl so acceptance C5 measures the new resume behaviour.
      Full list with evidence: `.index/dead-code.md`.


000. **Navigator, after 2026-10-08:** (a) a labour lawyer runs `navigate
     review-sheet` and signs off the cases (set `reviewed_by`/`reviewed_on` in
     the YAML); (b) a Form 7.11 summary once the form is held via inbox;
     (c) fix display titles and draft-code typing (dead-code.md); (d) mark the
     DEL LRA copy outdated (D-023); (e) Phase 3: s189A, s198, s197, s186(1)(b)
     routes; an HTTP API. Verifying GG 53294 (item 00a) removes the code warning.

00. **Layer 1 follow-ups (D-021):** (a) a human confirms the OCR reading of
    GG 53294 against the image, then `del_gov.CURRENT_DISMISSAL_CODE.verified`
    and the main corpus's draft-only state get updated; (b) OCR support in the
    CCMA engine (`raw/.../ocr.txt` sidecar, as D-014), so the code becomes
    searchable; (c) page-furniture scan detection in the shared `parse/pdf.py`
    (see dead-code.md); (d) CCMA stays disabled until robots.txt permits the
    paths. Re-check with `ccma robots` only after research access is granted.

0. **CCMA engine:** get operator answers on P0 (b)-(d); then seed
   `local_corpus` and `operator_inbox` with `--popia-ack`; register the MCP
   server (`claude mcp add ccma-corpus -- python .../mcp/ccma_server.py`).
   Research-access requests to SAFLII and the CCMA now also unlock awards.

1. Request research access from SAFLII / AfricanLII, and ask Laws.Africa about a
   data agreement. Q1 and Q2 gate all case-law coverage. The citation map now
   quantifies the prize: 6,410 judgments, 1997-2026.
2. If access is granted, set the relevant `access: open` in `sources.yaml`; the
   SAFLII index walker and filters are already written and unit-tested.
3. ~~Verify each bargaining council's robots policy~~ **DONE and CLOSED.**
   MIBCO, NBCRFLI and BCCEI registered. **MEIBC checked with Playwright on
   2026-09-20 and rejected**: `/circulars` renders 613k characters but exposes
   zero document links even in a real browser, and the homepage offers only
   annual reports, not the constitution or main agreement. Reaching its
   documents would mean reverse-engineering its client API for material that is
   not the dismissal-relevant instruments. Not worth it; do not retry blindly.
4. ~~Install tesseract~~ **DONE differently**: `rapidocr-onnxruntime` is a
   pip-only local engine needing no admin rights, and is what runs here.
   tesseract remains preferred if it can be installed — it is faster and more
   accurate on long printed legal text.
5. Optional and explicitly not "done" per the brief: local embeddings, UI.

**Resolved this pass:** the 18 "scans" were mostly mislabelled Word files (14
recovered, D-011); NMWA and PDA were recovered from the gov.za Acts listing via
bounded pagination; `query_terms` was dropped as unworkable.

**Resolved:** `query_terms` was dropped, not implemented — gov.za ignores
`?search=` on `/documents` and returns the default newest-first listing.

## Patterns & Recurring Issues Noticed

- **Mentions are not identities — and proxies are not the thing.** The single
  most persistent bug class in this project, now five instances: the LRA
  classified from a citation in s188(2); `mhsa_s54_stoppage` firing on any
  statute's "section 54"; judges named "IL J" from the law-report abbreviation
  ILJ; a host called hostile because the wrong user-agent was tested (D-012);
  and council agreements titled "Labour Relations Act" because the title search
  read a cross-reference in the body (D-015). **Measure the actual artefact,
  and weight the document's own opening over anything it cites.**
  2026-10-09 added three more: the navigator counted fund rules and forms as
  "copies" of the LRA/EEA by title (D-028); OCR'd forms were retyped by a
  body-scan mention (D-029); the inbox read "act" inside "practice".
- **Re-parsing is the real test of a parser change.** Unit tests passed every
  time on 2026-10-09; the corpus diff found two regressions the tests did not
  (D-029 typing; dropped OCR reuse). Always dry-measure over data/corpus/text
  and diff a re-parse against a backup before calling a parser change done.
- **Fixes can loosen policy by accident.** A robots.txt size cap with prefix
  parsing would have dropped a later Disallow; RFC-style group merging lets an
  Allow lift another group's Disallow. Review every collection change with the
  question "can this fetch anything the old code refused?"
- A document *citing* The LRA was tagged
  `code_of_good_practice` because s188(2) refers to codes; `mhsa_s54_stoppage`
  fired on any statute's "section 54". Fix pattern: weight the document's own
  title, and require context proximity in regexes.
- **Precision matters more than recall here.** A false tag or an invented
  citation is worse than a missing one, because a downstream reader cannot tell
  the difference. Redaction is held to the same standard: it must not eat case
  numbers or citations.
- **Windows portability bites.** cp1252 consoles cannot render rich's glyphs;
  Git Bash `/tmp` is not Python's `/tmp`.
- **Third-party parsers fail broadly.** A legacy `.doc` served as `.docx` raised
  `BadZipFile` and ended a run. Parse failures are now isolated per document.

## Session Log

- [2026-10-09] Holistic optimisation review and fix pass (multi-agent):
  review (15 agents) -> fixes (16 agents, 7 file-disjoint implementers each
  adversarially reviewed) -> integration review (3 agents) -> 3 corpus
  re-parses with a backup and a per-field diff each time. 421 -> 705 tests.
  D-025..D-029. Fixed the parallel session's certifi finding (`tls._trusted_roots`)
  and its two index gaps (refresh-log entry written, phase updated). Removed
  247 empty leaked `%TEMP%/labour_doc_*` dirs. Report:
  `analysis_logs/session-report-2026-10-09.md`.

- [2026-10-09] Workspace health check + roadmap (read-only except index notes).
  Verified: 703 tests pass (exit 0, 86 s, global Python 3.13.15), `ruff check`
  clean; still no `.git`, no CI, no `.venv`. Found: `tls.py:169` bulk-loads
  certifi and trips a cryptography deprecation (logged in dead-code.md).
  Index gap: `context-refresh-log.md` has no 2026-10-09 entry although
  `file-inventory.md` points to one, and "Current Analysis Phase" above still
  says the review is in progress although the inventory records 38 fixes
  landed. Recommended first move: `git init` + baseline commit.

- [2026-10-08] Navigator phase 2: interview, MCP server, golden cases + review
  sheet. Found and fixed the root cause of the wrong Act doc types (D-024),
  measured 63 changes dry, re-parsed with a backup, and confirmed an identical
  diff. Recorded the display-title and draft-code typing bugs. 419 tests green.

- [2026-10-07] Product pass. Read the held LRA s186-s194 and EEA s10, then built
  the Dismissal Navigator on them (D-022). The live run found the DEL LRA copy
  is pre-2014 (D-023) and that evidence was noisy (Act copies, council
  constitutions); both were fixed. Act-copy doc types were found to be
  unreliable (dead-code.md). 373 tests green; navigator coverage 99%.

- [2026-09-23] Layer 1 official publications (D-021). Wrote robots/ToS notes
  before any request; added `official_publication` (path allow-list, notes
  gate, robots preflight, `pending_robots_check`, `ccma robots`, `-s <type>`,
  `LABOUR_CORPUS_CONTACT_EMAIL`). Robots: CCMA 403 → disabled; gov.za 200 →
  dry run (limit 10) then live (limit 20): GG 53294 fetched and seeded as a
  statute. The live run exposed a scanned gazette marked `parsed` and a draft
  check "passing" on running heads; both fixed in the engine. 324 tests green.

- [2026-09-23] Built the CCMA Corpus Engine from a new spec as an upgrade.
  Re-measured sources with the production Fetcher: SAFLII ZACCMA and CCMA both
  403, lawlibrary ai-input=no; Labour Guide (commercial) and CCMA Online
  (case management) dropped. Found and fixed: tagger misses "dismissal of the
  applicant was ... fair" (engine fallback), nondeterministic last-run ordering,
  hidden characters written by a shell heredoc. 281 tests green.

- [2026-09-18] Applied the outstanding recommendations. Added a pip-only local
  OCR backend (rapidocr) and recovered all 11 scanned documents; made OCR
  durable via `raw/.../ocr.txt` sidecars (D-014). **Corrected the CCMA finding**:
  it is collectable — the earlier probe used a user-agent the crawler never
  sends (D-012), and the real blocker was an incomplete TLS certificate chain,
  now completed via AIA with verification fully intact (D-013). Registered
  MIBCO, NBCRFLI and BCCEI after checking each council's robots policy (Q3
  closed). Fixed act-title extraction, which was reading citations and bare
  "Act" fragments as titles (D-015). Corpus 150 -> 187 documents, all readable.
  223 tests green, 11/11 acceptance criteria.

- [2026-09-17] Format correction and last Acts. Found that 14 of 18 "scans" were
  Word 97-2003 files mislabelled by the server; added magic-byte sniffing,
  antiword support and `UNSUPPORTED_FORMAT` (D-011), recovering all 14. Added
  opt-in local OCR (`parse --ocr`) for the 4 genuine scans. Added bounded
  listing pagination and a `govza_acts` source, recovering NMWA 9 of 2018 and
  PDA 26 of 2000 — all 12 target labour Acts now held. 181 tests green.
- [2026-09-17] Applied the outstanding recommendations: imported 77 legacy
  documents (12 judgments, 65 Acts), added the `provenance` field with an
  additive migration, made fetch_meta.json carry doc_type/forum/provenance so
  `parse` works for non-crawl sources, swept the DEL Acts library uncapped,
  dropped the unworkable `query_terms`, and added provenance and text-quality
  reporting to COVERAGE.md. Fixed a judge-extraction defect where the law report
  abbreviation "ILJ" parsed as a judge "IL J" on 10 of 12 judgments. Recorded
  D-010. 176 tests green.
- [2026-09-17] Legacy triage + citation-map import. Assessed the windsurf-project
  scrape, found it to be index pages rather than documents, and imported its one
  real asset as 6,410 judgment references. Built `citation_map.py`,
  `known_citations`, `import-citation-map`, and measured coverage in COVERAGE.md.
  Excluded 2,530 non-SA records. Recorded D-009. 158 tests green.
- [2026-09-17] Greenfield build. Live-verified every seed host before writing
  source code; corrected the DEL paths from the brief (404 → DocumentCenter).
  Built the full pipeline, ontology, tests and reports. Harvested 58 real
  documents. Fixed 8 defects found by live running (see
  `.index/context-refresh-log.md`). All 11 acceptance criteria audited with
  evidence. `.index/` created and populated. Memory file initialised.
