# Context refresh log

## 2026-10-09 — Holistic review, 60 defects fixed, corpus re-parsed (D-025 to D-029)

Count: 45 of the 72 review finding IDs closed (40 distinct defects; 5 IDs were
the same defect seen from two dimensions), plus 15 defects found while fixing
and verifying (3 by per-agent review, 5 by the integration review, 2 by
measuring re-parses, 1 by a parallel session, 4 coordinator follow-ups).

**Before.** 421 tests green (74.5 s); ruff clean; mypy 2 env-only errors. An
unlogged lint/typing pass had run 01:35-02:10 the same morning (recorded in
`REPO_ANALYSIS_MEMORY.md`). Source snapshot taken before any change (scratchpad
`baseline-2026-10-09/`; no git repo).

**Review.** 7 finder agents (performance, security, build, architecture,
correctness, quality, cost), 7 adversarial verifiers, 1 completeness critic:
72 findings, 1 refuted. Evidence: `analysis_logs/review-2026-10-09-findings.*`.

**Fixes.** 7 implementers on disjoint file sets, each adversarially reviewed
(2 blocking issues, both fixed in a fix-up round), then 7 coordinator
follow-ups, then a 3-agent cross-agent integration review (6 more defects; 5
fixed, 1 recorded), then 2 defects found by measuring the re-parsed corpus
(D-029 OCR typing; OCR reuse when the reader fails on every page) and 1 found
by a parallel health-check session (certifi bulk load). 56 files changed, 8 new;
src 13,905 -> 14,854 lines, tests 4,121 -> 7,907. Decisions D-025 to D-029.

**After.** 705 tests green (~49-60 s on a loaded machine); ruff clean; mypy
the same 2 env-only errors; `navigate provisions` 21/21 verified with copies
2 of 2 (1 of 2 on the 5 genuine D-023 disagreements); review sheet 30/30;
acceptance audit 10/11 — C5 now honestly FAILS on the historical RUN.jsonl
(20 repeat 200 downloads, ~89.5 MB, from 2026-09-20); rotate it before the
next crawl.

**Corpus re-parsed three times** (backup `data/backup-2026-10-09/`: db, text,
parsed, index, reports). Final diff against the backup, all intended: 397/397,
0 failures, 27 OCR sidecars reused; doc_type 5 changes (codes of practice that
were typed statute; 0 Acts); is_draft +1 (draft conveyor-belt code); outcome
labels on non-decisions 8 -> 0, remedy 21 -> 0 (judgments unchanged); 27 OCR
documents `partial` -> `ok` with language, 17 gain tags; 1 spaced SA ID
redacted from text/ and the FTS index; tags 1,613 -> 1,732; year-shaped
paragraph locators 275 -> 0; mis-sorted decision evidence 25 -> 0. FTS index
39.4 -> 27.3 MB, 1 segment. Parse 1,106-1,791 s under contention.

**Found and corrected during re-parsing.** Re-parse 1 retyped 18 OCR'd forms
and agreements by body-scan mentions and titled a MIBCO fund agreement
"Labour Relations Act" -> D-029. Re-parse 2 dropped GG 54438's 7,526 OCR
characters (reader fails on every page -> `empty`, sidecar reused only for
`ocr_needed`) -> sidecars reused for `empty` too. Re-parse 3 clean.

**Not done (need a human).** Conciliation-certificate deadline logic and
s136(1)(b) (lawyer); outcome inference from the order section and CCMA
holdings (corpus diff review); display-title regex rework; TLS bundle
hardening; redirect-hop policy checks; schema versioning; git init and CI;
project venv; pypdf vs PyMuPDF (AGPL) / pypdfium2; dead adapter deletion;
the unwired supersede-check wording. All in `dead-code.md`.

## 2026-10-08 — Navigator phase 2 and Act self-identity (D-024)

**Scope.** New `navigator/golden.py`, `interview.py`, `mcp_server.py`;
`navigate interview|review-sheet|mcp`; `mcp/navigator_server.py`;
`tests/golden/navigator_cases.yaml` (30 cases, pending lawyer review);
`tests/test_navigator_phase2.py` (34 tests). Shared code: `ccma/mcp_server.py`
made overridable (defaults unchanged, CCMA tests green); `parse/statute.py`
gained `self_identified_act` (+12 parser tests). Suite: 419 passed. Navigator
coverage 99%.

**Corpus re-parsed** (backup `db/corpus.backup-2026-10-08.sqlite`). 397/397,
0 failures, 27 OCR sidecars reused. The diff against the backup equals the dry
measurement exactly: 63 type changes (amendment→statute 43, regulation→statute
18, code→statute 2), 0 text-status and 0 draft changes. Re-indexed;
COVERAGE.md regenerated. Afterwards: 21/21 provisions verified, acceptance
audit 11/11.

**Found.** Display-title bug (OHSA titled "Safety Amendment Act", the
Constitution "South Africa Amendment Act"). Two draft codes typed `statute`
(pre-existing). An interview UX defect (a bad appeal date was rejected only
after four more questions), fixed to fail fast.

## 2026-10-07 — Dismissal Navigator (D-022, D-023)

**Scope.** New package `src/labour_corpus/navigator/` (10 modules); `cli.py`
mounts `navigate`; `tests/test_navigator.py` (49 tests, 99% line coverage of the
package); `docs/navigator-architecture.md`; `examples/*.json`; README section;
pytest-cov pinned in the test extra. Full suite: 373 passed (all 324 prior
tests still pass).

**Grounded before written.** LRA s186-s194 and EEA s10 were read from the held
consolidations and the quotes copied from them. `navigate provisions` →
21/21 verified.

**Found by running it.** (1) The DEL LRA PDF is a pre-2014 consolidation (D-023).
(2) Evidence first returned the LRA's own Schedule 8 contents three times, then
council constitutions matching incidental words. Fixed with a type allow-list
and a title exclusion for Act copies (doc types are unreliable; dead-code.md).
(3) The new suite had three test errors (an already-late default date, and a
quote that was still present in a second copy); the engine was right each time.

---
## 2026-09-23 — Layer 1 official publications (D-021)

**Scope.** `labour_corpus/ccma/`: allowlist, scrape, find, extract, harvest,
report, cli, knowledge and seed changed; `tests/test_ccma_official.py` added
(43 tests); `ccma_sources.yaml` +2 sources; source map, architecture doc and
README updated. 324 tests green (281 prior, all passing; one prior test
renamed and narrowed because the operator confirmed the Layer 1 sources).

**Order kept.** Notes written into `docs/ccma-source-map.md` before any
request → `ccma robots` (robots.txt only) → YAML/doc updated (CCMA disabled
under rule 2) → dry run `--limit 10` (1 request: gov.za robots.txt) → live
`--limit 20` (2 requests: robots.txt, the PDF) → seed-only re-run after the
scan fix (no requests). SAFLII and cmsonline were never contacted.

**Defects found by the live run and fixed.** (1) A 16-page gazette whose text
layer is running heads only was marked `parsed`; now `ocr_needed`. (2) The
draft check over that text read "passed"; it now says "not possible".
(3) Earlier in the build, reserving source-type names as ids broke the
existing `local_corpus` id; an exact id now wins over a type.

**Verification of the fetched file.** Text layer: GG 53294, 4 September 2025,
DEL Notice 3470 of 2025. One-off local OCR (not stored): Code of Good
Practice: Dismissal under LRA s203(2A), effective on publication, repeals
Schedule 8 and the 1999 operational-requirements code. Machine transcription;
confirm against the image.

---

## 2026-09-23 — CCMA Corpus Engine added

**Scope.** Upgrade. New subpackage `labour_corpus/ccma/` (16 modules), `mcp/`,
`docs/`, `ccma_sources.yaml`, `tests/test_ccma_engine.py` (36 tests) and
fictional fixtures. `http.Fetcher` gained optional `retry_waits`. 281 tests green
(36 new; all prior tests unchanged and passing).

**Verified live, with the production Fetcher.** SAFLII `/za/cases/ZACCMA/` and
`/robots.txt`: 403 Cloudflare challenge. CCMA `www.ccma.org.za` `/` and
`/robots.txt`: 403 bot refusal (still, since 2026-09-20). lawlibrary.org.za:
`ai-input=no`. Web search surfaced `caselaw.labourguide.co.za` (commercial,
dropped) and `cmsonline.ccma.org.za` (case management, dropped).

**Defects found and fixed during the build.** (1) The shared tagger's fairness
regex misses "dismissal *of the applicant* was ... fair"; the engine adds a
narrow fallback used only when the tagger returns unknown. (2) Latest-run
ordering by `started_at, run_id` was nondeterministic within one second; now
`rowid`. (3) A shell heredoc wrote literal backspace and bidi-override
characters into a regex; replaced, and the control-character class is now built
from code points so the source carries no invisible characters.

**Real-workspace check.** `ccma harvest --dry-run --limit 5`: exit 0, 0 raw
files, 5 local would-seed records, report + audit written. No live harvest and
no seeding of real data: awaiting operator confirmation (P0 b-d).

---

## 2026-09-17 — Initial build and full index creation

**Scope.** Greenfield. Built `sa_labour_corpus/` from a specification in one
session: 37 Python modules, 6 test modules, ~7,000 lines.

**What was verified live, not assumed.**
- robots.txt and access behaviour for saflii.org, ccma.org.za, labour.gov.za,
  gov.za, mhsc.org.za, gpwonline.co.za, lawlibrary.org.za.
- The seeded `labour.gov.za/Legislation/*` paths in the brief are HTTP 404. The
  real structure is a SharePoint document centre at
  `/DocumentCenter/Pages/{Acts,Code-of-Good-Practice,Regulations,Sectoral,Forms}.aspx`,
  each listing folders whose pages carry the PDFs. Corrected in `sources.yaml`.
- Conditional GET support on labour.gov.za (ETag + Last-Modified, returns 304).

**Results.** 58 real documents harvested (LRA, EEA, BCEA, codes of good
practice, amendments, gazettes). Full test suite green. Smoke green.
Resume verified live: 60 conditional GETs, 0 bytes transferred.

**Defects found and fixed during the build.**
1. LRA misclassified as a code of good practice because s188(2) *mentions* codes
   — classification now weights the document's own title over a citation.
2. Party extraction split "Commission for Conciliation, Mediation and
   Arbitration" on "and", manufacturing fictional parties — "and" removed as a
   separator.
3. Judge extraction captured headings and sentence fragments — added a stopword
   guard.
4. Neutral citation was missed because SAFLII carries it in `<title>` — title is
   now searched.
5. `mhsa_s54_stoppage` matched any statute's "section 54" (5 false positives on
   DEL documents) — now requires mining context.
6. rich spinner glyphs crashed on a cp1252 Windows console — streams are
   reconfigured to UTF-8 with an ASCII-safe fallback.
7. A legacy `.doc` served as `.docx` raised `BadZipFile` and ended a run — now
   reported as `ocr_needed`, and the crawler isolates all parse failures.
8. Awards were not given judgment-style metadata extraction; CCMA case-number
   and commissioner formats were unsupported — all three fixed.

**Index created.** `.index/README.md`, `file-inventory.md`, `architecture.md`,
`key-decisions.md` (D-001 to D-008), `dead-code.md`, this log.

**Next.** Case-law coverage depends on access permission, not on more code. The
SAFLII adapter is written and waits on D-001/D-003.

## 2026-09-17 (later) — Legacy corpus triage and citation-map import

**Scope.** Assessed the prior scrape at
`D-Offload/.../CascadeProjects/windsurf-project/` (4,891 tagged records,
733 MB JSONL) and its mirror at `H-Offload/.../downloaded_documents`.

**Finding.** Not a labour corpus. 4,891 records resolve to 201 distinct titles
because index and listing pages were stored as documents: 1,240 copies of one
Gauteng court roll, 992 Botswana Court of Appeal records, 121 records titled
with a Cloudflare interstitial, and 12 unique labour judgments (one duplicated
23x on disk, mis-filed under `UNKNOWN/`). Legislation coverage was real:
89 consolidated Acts including LRA, BCEA, EEA, MHSA, COIDA, NMWA.

**Action.** Imported the one real asset — 154 SAFLII labour index pages — as
judgment *references*. Built `sources/citation_map.py`, a `known_citations`
table, an `import-citation-map` command, and measured-coverage output in
COVERAGE.md. Non-SA material and court rolls excluded at parse time
(2,530 records dropped).

**Result.** 6,410 known labour judgments across 7 forums, 1997-2026. 99.7% carry
a case number, 100% a delivery date, 2,002 a parallel ILJ/BLLR citation.
COVERAGE.md now reports "holds 0 of 6,410 (0.0%)" per forum and year instead of
a prose caveat. 158 tests green.

**Decision recorded.** D-009.

**Not done, by the operator's choice.** The 12 legacy judgments and 89 Acts were
not ingested this pass; only references were imported.

## 2026-09-17 (third pass) — Legacy documents, provenance, and debt resolution

**Scope.** Applied the three outstanding recommendations.

**1. Legacy documents imported.** 12 unique labour judgments and 65 unique
consolidated Acts, content-hash deduplicated from 4,942 legacy records; 146
listing pages rejected and 2,530 non-SA records excluded. Coverage moved from
0 to **12 of 6,410** known labour judgments — the first proof the held counter
works on real data. Recorded as D-010, which supersedes part of D-009.

**2. Provenance added to the document contract.** New `Provenance` enum and
`documents.provenance` column with an additive migration, so a legacy copy can
carry a correct canonical URL without implying this crawler fetched it.
`store_raw` now writes doc_type/forum/provenance into fetch_meta.json, and
`parse` prefers those over the registry — so documents whose source is not a
crawl target re-parse correctly. Verified: 135 re-parsed, 0 failed, doc_type
preserved.

**3. Missing statutes.** Swept all 8 DEL Acts folders uncapped (36 docs, then 0
new). MHSA, UIA and OHSA are now held; NMWA and PDA are genuinely absent from
the DEL library and recorded as gaps.

**4. `query_terms` debt resolved by removal.** gov.za ignores `?search=` on
`/documents` and returns the default newest-first listing, so the field could
never work. Dropped from `sources.yaml` and `SourceDef` rather than faked.

**Defect found and fixed.** Judge extraction read the law-report abbreviation
"ILJ" in a parallel citation — "(2000) 21 ILJ 571 (LAC)" — as a judge named
"IL" with title "J", on 10 of 12 judgments. The title now requires preceding
whitespace, report abbreviations are stopwords, names are stripped to their
final line, and duplicates are collapsed case-insensitively. Real judges now
extract: Zondo AJP, Waglay JP, Steenkamp J, Tlaletsi DJP, Snyman AJ.

**New reporting.** COVERAGE.md gained a provenance breakdown and a text-quality
section: **18 of 143 documents (12.6%) are scanned PDFs with no text layer**,
stored but not searchable or tagged. This is now the largest recoverable
quality gap and is logged in dead-code.md.

**Result.** 143 documents, 6,410 references, 9 recorded gaps, 176 tests green.

## 2026-09-17 (fourth pass) — Format correction, OCR, and the last missing Acts

**The finding that mattered.** 18 documents were flagged `ocr_needed` and
reported as scans. They were not. Magic-byte inspection showed **14 were Word
97-2003 OLE2 files**, served by labour.gov.za as `application/msword` from URLs
ending `.docx`. python-docx raised `BadZipFile` and the code mislabelled the
result as needing OCR — pointing the operator at the wrong remedy for the
Skills Development Act, COIDA, OHSA, the Unemployment Insurance Acts, three
Codes of Good Practice, and BCEA summaries in Sotho, IsiZulu and IsiNdebele.

**Fixes.**
1. `parse/office.py`: format decided by magic number, never by Content-Type or
   extension. Legacy Word read with `antiword` (present on this machine).
2. New `TextStatus.UNSUPPORTED_FORMAT`, distinct from `ocr_needed`: a readable
   format with a missing reader is not a scan. Recorded as D-011.
3. `parse/ocr.py` + `parse --ocr`: opt-in, local-only tesseract OCR for genuine
   scans. Output is labelled a machine transcription, `ocr_applied` is set and
   confidence is capped at 0.5. tesseract is not installed here, and the
   command says so rather than storing silence.
4. Bounded pagination for listing sources (`max_pages`, `page_param`,
   `title_filter`) plus a new `govza_acts` source.

**Results.**
- All 14 Word documents recovered; `ocr_needed` fell 18 → 4 (genuine scans).
- **NMWA 9 of 2018 and PDA 26 of 2000 recovered** from the gov.za national Acts
  listing. **All 12 target labour Acts are now held.**
- Corpus: 150 documents, 146 with usable text, 1,098 tags, 6,410 references,
  9 recorded gaps. 181 tests green (1 skipped: antiword-dependent).

**Still open.** 4 genuine scanned PDFs; install tesseract and run
`parse --ocr`. Case law remains barred by D-001.

## 2026-09-18 — OCR, and two corrections that unblocked the CCMA

**1. OCR completed without a system install.** tesseract needs administrator
rights, so `parse --ocr` gained a second local backend:
`rapidocr-onnxruntime`, pip-only and CPU-based. tesseract is still preferred
when present. 104 pages across 4 scanned PDFs at roughly 22 s per page.

**2. CORRECTION: the CCMA is collectable.** D-001 recorded it as refusing
identified crawlers. That was measured with a bare token user-agent such as
`SALabourCorpusBot/0.1 (+mailto:...)`, which the gateway does reject. The
user-agent the crawler actually sends is served HTTP 200 with real content.
Recorded as D-012.

**3. The deeper cause: an incomplete TLS certificate chain.** Even after the
user-agent question was settled, the crawler still failed with
`unable to get local issuer certificate`. `www.ccma.org.za` sends only its leaf
certificate and omits the intermediate; curl succeeded because it carries its
own CA bundle. New `tls.py` fetches the missing intermediate from the
certificate's AIA extension, **verifies it chains to a root certifi already
trusts**, and builds a per-host bundle. Verification is never disabled, and a
test asserts `verify=False` appears nowhere. Recorded as D-013.

Result: CCMA collects — 4 referral forms and 5 information sheets in a capped
test, with full verification retained.

**4. Bargaining councils resolved (Q3).** MIBCO, NBCRFLI and BCCEI each checked
individually: robots permit, identified crawler served 200, and each publishes
a constitution and collective agreements. All three registered. MEIBC deferred:
a JS-rendered Next.js site with no robots.txt, so its document routes need
confirming without a browser first.

**5. Robustness.** `CorpusStore` now sets a 30 s busy timeout. Without it a
second writer failed instantly with "database is locked", turning a normal
overlap into a crash.

**Registry now:** 25 sources, 14 collectable. 213 tests green.

### Final state of the 2026-09-18 session

**Corpus: 187 documents, all 187 readable, zero scans remaining.**

| Measure | Value |
|---|---|
| Documents | 187 (110 crawled, 77 legacy) |
| Readable text | 187 / 187 |
| Labour judgments | 12 |
| Known-judgment coverage | 12 of 6,410 (0.2%) |
| Ontology tags | 1,377 |
| Recorded gaps | 9 |
| Tests | 223 green |
| Acceptance criteria | 11 / 11 |

**OCR:** 11 documents recovered in total, 11 `ocr.txt` sidecars written. A
re-parse *without* `--ocr` reported "reused 11 stored OCR transcription(s)" and
left all 187 documents readable, which is the proof that D-014 works — OCR
survives a parser change.

**Two further defects found and fixed at the end.**
1. Titles came out as bare `"Act"` / `"ACT"` on MIBCO documents: the pattern
   allowed zero words before "Act", so a citation fragment qualified as a
   title. Now at least one capitalised word is required (D-015).
2. Resume was re-verified at scale: 262 conditional-GET 304 responses,
   0 bytes transferred.

**Sources: 25 registered, 14 collectable.** SAFLII is the only host still
blocked, and D-001 stands for it alone.

## 2026-09-20 — Corpus lost and rebuilt; drafts; CCMA access proved conditional

**The corpus was destroyed.** It lived under `%TEMP%/lc_live2` and Windows
cleaned it between sessions: 187 documents, the citation map and 11 OCR
transcriptions, gone -- `raw/` included. Nothing was unrecoverable because the
corpus is derived data and every stage is re-runnable, but the rebuild was
avoidable. `CorpusPaths.is_volatile` now warns, `rebuild_corpus.sh` runs the
full pipeline, and the corpus lives at `sa_labour_corpus/data/corpus` (D-017).

**The rebuild is more than twice the size of what was lost: 397 documents.**
The previous corpus had never swept `del_forms` (165 documents) or
`del_sectoral_determinations` (43), because earlier crawls were always capped
or source-limited. Running every collectable source uncapped found them.

**Q4 answered, and it exposed a real defect (D-016).** The Department publishes
only a *Draft* Code of Good Practice on Dismissal for public comment, and the
parser was classifying it `code_of_good_practice` with `is_dismissal_code()`
True -- identical treatment to an instrument in force. A draft has no legal
effect. `detect_draft` now flags drafts and bills, `is_draft` is on the
document contract and in the database, and COVERAGE.md leads with the drafts it
holds. **On live data it caught 4 drafts**, including the dismissal code.
`CURRENT_DISMISSAL_CODE` is marked `verified: False`.

**CCMA access is conditional, not open (D-012 updated).** After the ~78
documents collected on 2026-09-18, the gateway now returns HTTP 403 for every
path to the same user-agent over a verified TLS chain -- a rate or reputation
response. The answer is to back off: delays raised to 8-15 s, no user-agent
rotation, no quieter crawling to stay under a threshold.

**Q3 closed properly.** MEIBC tested with Playwright and rejected: `/circulars`
renders 613k characters with zero document links even in a real browser, and
the homepage carries only annual reports.

**MHSC repointed.** The site root yields no documents; the promulgated codes
page yields 20, including guidelines on fire prevention and change management
in mining. `mhsc_public` disabled, `mhsc_codes_of_practice` added.

**240 tests green. 26 sources, 14 collectable.**

### Final state, 2026-09-20

| Measure | Value |
|---|---|
| Documents | **397** (was 187 before the loss) |
| Readable text | **397 / 397** |
| Drafts flagged, not law | 4 |
| Labour judgments | 12 |
| Known-judgment coverage | 12 of 6,410 (0.2%) |
| Ontology tags | 1,612 |
| Recorded gaps | 10 |
| Tests | 242 green |
| Acceptance criteria | 11 / 11 |
| Sources | 26 registered, 14 collectable |

**Last defect fixed:** LRA Forms 3.7, 3.8, 3.17 and 3.18 extracted as empty
because python-docx's `.paragraphs` skips tables, and a form keeps nearly all
its content in tables. Table cells are now read as well. That took the corpus
to 397/397 readable.

**New:** `tools/acceptance_audit.py` runs the 11 criteria against a built
corpus, checking evidence on disk rather than a claim made elsewhere.

### Post-rebuild verification, 2026-09-20

Checking the rebuild rather than assuming it found one more silent failure: the
index and COVERAGE.md had been generated at 15:32, four minutes before the last
document write at 15:36, so both described a corpus of 393 rather than 397.
Regenerated, and `search` now warns when the index predates the corpus (D-018).

**Verified final:** 397 documents, 397 readable, 4 drafts flagged, 1,613 tags,
397 indexed, 245 tests green, 11/11 acceptance criteria via
`tools/acceptance_audit.py`.
# 2026-10-13 — Git/CI and post-review completion refresh

- Initialized Git on `main`; committed reviewed baseline `b723a26`.
- Added Python 3.11/3.13 CI (Ruff, mypy, pytest, navigator review sheet,
  offline smoke) and documented install profiles.
- Corrected certificate-stage routing and added LRA s136(1)(b); prepared the
  30-case practitioner review pack without claiming lawyer approval.
- Outcome and CCMA holding inference now prefer the final labelled decision
  section; final suite: 707 passed, 1 skipped; Ruff clean; golden 30/30 and
  offline smoke passed.
- Deleted four unreferenced adapters and the unused browser path (D-030), kept
  pypdf (D-031), and made supersede checking explicitly manual (D-032).
- Rotated historical RUN.jsonl to
  `data/corpus/reports/archive/RUN-through-2026-10-13.jsonl` after SHA-256
  verification (`8DE7CF6144C2673F8F6339B1A105E374BCAB3C9FD10AF4CC0AA05377FFD1A308`).

## 2026-10-13 — Outcome refresh, verification and optimisation roadmap

- Backed up all 397 parsed records and `corpus.sqlite`; verified database
  SHA-256 `0FEA06B3D994647E55B198C6D501420746486C5E389658731B40DE7BCBC29943`.
- Rejected and rolled back a full parse whose environment lacked `antiword`
  and would have introduced 69 unrelated legacy-Word metadata changes.
- Re-tagged the 12 adjudicative documents from preserved text; 7 records
  changed. Rebuilt the 397-document FTS index and regenerated COVERAGE.md.
- Acceptance audit is 10/11: criterion 5 correctly remains pending because the
  rotated run history has no new real conditional-request crawl evidence.
- Mypy reports no issues in 72 source files. Removed both ignored failed
  `.venv-broken-*` directories and verified the working `.venv` imports.
- Added `sa_labour_corpus/docs/optimisation-roadmap.md`; refreshed inventory,
  dead-code/debt state, and repository analysis memory from inspected files.
- External gates: practitioner signature, valid crawler contact/live run, and
  operator-confirmed Git remote plus hosted CI.

## 2026-10-13 — Remote integration and acceptance closure

- Added operator-confirmed remote `AN3S-CREATE/SA-Labour-LAW`, fetched its
  unrelated two-line initial commit, and merged the histories without force.
- Pushed `main`; hosted Python 3.11/3.13 CI run 38428505021 passed.
- Ran `bc_nbcrfli` with a one-document cap and the operator-supplied contact.
  Robots allowed the run; the stored PDF returned a genuine conditional 304,
  with zero repeat 200 downloads, zero document bytes and no gaps/failures.
- Acceptance audit now passes 11/11 against the newly rotated run history.
- Pinned `actions/checkout` v4 and `actions/setup-python` v5 to their resolved
  commit SHAs, retaining major-version comments for reviewed updates.
- Practitioner sign-off of the 30 golden cases remains the sole release gate.

---
