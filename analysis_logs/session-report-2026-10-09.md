# Session report — SA Labour Law corpus — 2026-10-09

Evidence for every finding: `review-2026-10-09-findings.md` (72 findings with
verifier reasoning). Index: `.index/` (D-025..D-029, dead-code.md,
context-refresh-log.md). Working state: `REPO_ANALYSIS_MEMORY.md`.
The pasted template began at section 4; sections 1-3 below are context, plan
and execution log.

## 1 · Context

`sa_labour_corpus/` is a local-first harvester of South African labour-law
sources (397 documents, 6,410 known judgment references) with a rule-based
Dismissal Navigator and a gated CCMA engine. No LLM, no cloud. Not a git
repository. Before this session: 421 tests green; an unlogged lint/typing pass
had run at 01:35-02:10 the same morning.

## 2 · Plan

1. Load `.index/` + memory; measure the baseline (tests, ruff, mypy).
2. Snapshot the source (no git) for diff/rollback.
3. Multi-agent review: 7 dimensions, adversarial verification, completeness critic.
4. Auto-fix verified, test-provable items on disjoint file sets, each reviewed.
5. Integrate (full suite, ruff, mypy) + a cross-agent integration review.
6. Back up and re-parse the corpus; diff every field against the backup.
7. Update `.index/`, memory; report.

## 3 · Execution log

| # | Step | Result |
|---|---|---|
| 1 | Context + baseline | Done: 421 tests / 74.5 s, ruff clean, mypy 2 env-only |
| 2 | Source snapshot | Done: 116 files, scratchpad `baseline-2026-10-09/` |
| 3 | Review (15 agents) | Done: 72 findings, 1 refuted |
| 4 | Fixes (16 agents) | Done: 7 implementers + 7 reviewers + 2 fix-ups; 2 blocking issues fixed |
| 5 | Integration | Done: 7 coordinator follow-ups; 3-agent integration review -> 5 fixes + 1 recorded |
| 6 | Corpus re-parse | Done after 3 runs (2 regressions caught by the diff and fixed) |
| 7 | Index + memory | Done: D-025..D-029, register, refresh log, inventory |

Failure log (failure -> root cause -> fix):

| Failure | Root cause | Fix |
|---|---|---|
| `pip install ruff` "no versions" | pip cannot reach its index here | ran ruff/mypy/pip-audit via `uvx` |
| pytest import error `http.client` | ran from `src/labour_corpus/`; package `http.py` shadows stdlib | run from package root |
| Bash heredoc truncated | tool cut the heredoc | generator as a `.py` file |
| Workflow script parse error | backtick inside template literal | plain string concatenation |
| Re-parse launched with a 10-min tool timeout | would kill parse mid-run | stopped within seconds, relaunched detached with a log |
| mypy `registry.py:180` | `isinstance` narrowed `Any` | `str(entry.get("name") or source_id)` |
| Agent A: robots prefix-parse | could drop a later Disallow (looser) | oversize robots.txt = refusal |
| Agent G: C5 false fail | deliberate re-fetches counted | repeat only if a stored body of equal size existed |
| Re-parse 1: 18 OCR docs retyped, fund agreement titled "Labour Relations Act" | body scan on noisy OCR text | D-029; dry-measured 27/27 |
| PDF all-pages-failed rule never fired | condition counted successful pages | count attempted pages (existing test caught it) |
| Re-parse 2: GG 54438 lost 7,526 OCR chars | sidecar reuse only for `ocr_needed` | reuse for `empty` too (D-014) |
| test_tls.py syntax error | insert script expanded `\n` | repaired |
| `review-sheet --out`, exit 2 | wrong option (`--cases`) | correct invocation: 30/30 |
| Parallel session edited memory/dead-code | concurrent writer | merged, its finding fixed and credited |

## 4 · Recommendations (fixed today marked ✅; open items with priority)

### Performance
| What / where | Why | How | Impact | Effort | Priority |
|---|---|---|---|---|---|
| ✅ Typer string annotations (`cli.py`, `ccma/cli.py`, `navigator/cli.py`) | every CLI call eval'd 250 annotations | removed future import; cross-app test | command build 1.185 s -> 0.012 s; navigator tests ~42% faster | S | Done |
| ✅ Lazy pypdf (`parse/pdf.py`, `ccma/extract.py`) | ~1 s import on every command | import inside functions | ~19% CLI import | S | Done |
| ✅ Navigator MCP reload (`grounding.TextCache`) | 0.53-0.79 s per call re-reading Acts | per-file (mtime,size) cache, SQL every call | warm 50-140 ms | S | Done |
| ✅ `normalise`, RTF, citation index, test sleeps | measured hot spots | chained replace; in-place regex; `idx_documents_neutral`; fake clocks | 8x; 16.9->0.47 s; 20x; ~9 s off suite | S | Done |
| Tagger: 393 full-text regex scans/doc (`ontology/tagger.py:97`) | 1,437 s = most of parse CPU | single finditer; `X(?<!\w\w)`; literal-prefix skip | ~3-4x parse | M | Next Sprint |
| pypdf ~10x slower than PyMuPDF (`parse/pdf.py`) | parse wall time | benchmark pypdfium2 (PyMuPDF is AGPL) | ~10x extraction | M | Next Sprint |
| Evidence snippet over-fetch (`navigator/evidence.py:86`) | 75-80% of query time wasted | bm25 first, snippets for kept rows | 4-5x per issue | S | Backlog |
| CCMA FTS deletes scan (`ccma/catalogue.py:314`) | O(n²) seeding | rowid-keyed FTS + migration | linear | M | Backlog |

### Security
| What / where | Why | How | Impact | Effort | Priority |
|---|---|---|---|---|---|
| ✅ AIA gate trusted by issuer *name* (`tls.py`) | forged CA could anchor a host | `verify_directly_issued_by`, CA + validity checks; roots loaded singly (D-026) | closes interception path | S | Done |
| ✅ Spaced SA ID numbers unredacted (`parse/redaction.py`) | POPIA; 1 real ID in corpus + index | 6-4-3 pattern, Luhn-validated | 1 ID removed | S | Done |
| ✅ robots first-group only (`robots.py`) | ignored a site's Disallow / ai-input=no | all groups, most restrictive; caps (D-025) | D-002 hole closed | S | Done |
| ✅ Per-source delays unused (`http.py`, crawler) | D-012 8-15 s not enforced | `RateLimiter.set_host_delay` | CCMA ~7.7x gentler | S | Done |
| ✅ RO SQLite URIs, URL guard https/443, DOCX bomb, robots/AIA caps, strict config, pytest CVE pin | each verified | see `.index/key-decisions.md`, inventory | — | S | Done |
| Redirect hops fetched before guards (`ccma/scrape.py:85`, crawler) | robots/Content-Signal skipped for redirect targets (latent) | manual redirect loop with `check_policy` per hop | — | M | Next Sprint |
| TLS bundle hardening (`http.py:213`, `tls.py`) | PARTIAL_CHAIN on; cache never revalidated | clear flag; re-run gate on cache hit | — | S | Next Sprint |
| `browser.py` bypasses policy | untested, no `check_policy` | delete or route via Fetcher | — | S | Backlog |

### Bundle / Build Size
| What / where | Why | How | Impact | Effort | Priority |
|---|---|---|---|---|---|
| ✅ FTS never compacted (`index/fts.py`) | 10 segments, free pages | optimize + VACUUM | 39.4 -> 27.3 MB (-31%) | S | Done |
| ✅ Unused beautifulsoup4 | never imported | removed; lxml marked transitive | -1.09 MB | S | Done |
| README installs ~501 MB | playwright/OCR/lint for every user | document core / `[ocr]` / `[browser]` / `[test,lint]` | core 68 MB (-86%) | S | Next Sprint |
| Lock file | 17 transitive packages float | `uv lock` or pip-compile --generate-hashes | reproducible installs | S | Next Sprint |
| `cryptography` optional extra | 12.6 MB only for tls.py | `[tls]` extra | -19% core | S | Backlog |
| Contentless FTS | text stored twice (~20 MB) | content='' + own snippets | -20 MB | M | Backlog |

### Architecture & Scalability
| What / where | Why | How | Impact | Effort | Priority |
|---|---|---|---|---|---|
| No git, no CI | nothing revertible; golden gate unenforced | `git init`; CI: ruff, mypy, pytest, review-sheet, smoke | — | S/M | **Do Now** |
| ✅ D-008 boundaries (`cli.parse`, inbox), fail-fast rebuild script | one bad file ended a re-parse | broad catch at document boundary; `set -euo pipefail` | — | S | Done |
| ✅ Crawler resume / per-run dedup / 5xx -> gap | lost documents; crashed crawls | see architecture.md | — | S | Done |
| ✅ Navigator: self-identifying copies (D-028), exhaustive `Track`, exit 3 on not_checked, future dates refused | wrong copy counts; latent misrouting | — | — | S | Done |
| Schema versioning (`state.py:157`, catalogue) | old DB crashes readers | `user_version` + migrations | — | M | Next Sprint |
| Dead adapters (4 modules, ~250 lines) | misleading map | delete / wire | — | S | Next Sprint |
| Project `.venv` | global interpreter drift | venv + `pip install -e ".[test,lint]"` | — | S | Next Sprint |

### Code Quality
| What / where | Why | How | Impact | Effort | Priority |
|---|---|---|---|---|---|
| Navigator: certificate on file -> "condonation needed" (`navigator/engine.py:50`) | false legal statement; no s136(1)(b) deadline | lawyer-reviewed rule + golden cases | user-facing correctness | M | **Do Now (lawyer)** |
| COVERAGE.md claims an unwired supersede check (`report/coverage.py:385`) | honesty contract | wire or reword | — | S | **Do Now (decision)** |
| Outcome labels from first mention (`ontology/tagger.py:160`) | 4 of 6 wrong, 2 inverted | infer from the order section only | data correctness | M | Next Sprint |
| CCMA `find_holding` misquotes (`ccma/knowledge.py:111`) | claims shown as findings | decision section; reject claim verbs | before local_corpus harvest | M | Next Sprint |
| Display-title regex (`parse/statute.py:25`) | OHSA "Safety Amendment Act" etc. | measured design (23 corrections) | titles | M | Next Sprint |
| ✅ OCR helper dedup, RO-URI dedup, GapKind, UP035, temp-dir leak, paragraph locators | duplication / leaks / bad locators | — | 275 bogus locators -> 0; 247 temp dirs cleaned | S | Done |

### Cost Optimisation (no cloud or API spend exists: local CPU, disk, network politeness)
| What / where | Why | How | Impact | Effort | Priority |
|---|---|---|---|---|---|
| ✅ Repeat URLs per crawl (`sources/crawler.py`) | 33 repeats, 89.6 MB | per-run seen set + listing cache | -7.4% requests, -25.8% bytes | S | Done |
| ✅ Navigator per-call CPU | full reload every call | text cache | ~99% less after first call | S | Done |
| Apache `-gzip` ETags never 304 (`http.py:320`) | 89.4 MB per MIBCO walk | send stripped ETag too; confirm live once | up to 89 MB/run | S | Next Sprint |
| Rebuild parses twice (`rebuild_corpus.sh`) | 15-25 min | PARSER_VERSION + `--changed-only` | per rebuild | M | Backlog |
| CCMA engine: no conditional GET, no OCR sidecar | re-download/re-OCR | validators in meta; `ccma seed --ocr` | small today | M | Backlog |

## 5 · Session report and system resources

Work completed (durations measured where marked; others approximate):

| # | Task | Result | Time |
|---|---|---|---|
| 1 | Context, baseline, snapshot | 421 green; ruff/mypy measured | ~15 min (mypy 287 s) |
| 2 | Review workflow | 15 agents, 72 findings | 2 h 14 min (measured) |
| 3 | Fix workflow | 16 agents, 0 errors | 59 min (measured) |
| 4 | Coordinator integration | 7 follow-ups, 687 green | ~20 min |
| 5 | Integration review | 3 agents, 6 findings | 25 min (measured) |
| 6 | Re-parse 1 + diff | found D-029 regression | 20 min (1,150 s parse) |
| 7 | Integration fixes + re-parse 2 + diff | found OCR-reuse regression | ~45 min (1,791 s parse) |
| 8 | Fix + re-parse 3 + verification | clean; 705 green; 21/21; 30/30 | ~25 min (1,106 s parse) |
| 9 | Index, memory, report | D-025..D-029 | ~15 min |

Subagents: 34 in total, ~8.4 M tokens. Code: 56 files changed, 8 new;
src +949 lines, tests +3,786 lines.

Resources (three samples: during the review / during re-parse 2 / at the end):

| Metric | Values | Healthy | Status |
|---|---|---|---|
| CPU | 98.2% / 100% / 77.9% | <80% | ⚠️ |
| RAM | 27.55 of 27.86 GB (98.9%) / 84.2% / 97.1% | <85% | ⚠️ |
| Disk I/O | C: 194% busy, 76 MB/s / 6.4% / 315%, 90 MB/s; D: (project) 0.4-8.5% | <80% busy | ⚠️ C:, ✅ D: |
| Disk space | C: 74%, D: 79% (project data 391 -> 442 MB incl. 63 MB backup), F: 98.4% | <90% | ✅ C/D, ⚠️ F |
| Network | 3.4 MB/s / 154 KB/s / 307 KB/s (project: no crawling; only uvx/pip-audit) | — | ✅ |
| Processes | 1,012 / 1,155 / 1,099; 56 python = 14 x (mcp-server-fetch + mcp-server-git) | — | ⚠️ |

System resources are **not fully utilised by this project, and the machine is
over-subscribed by other software.** CPU and RAM were saturated before the work
started, mostly by sync clients (OneDrive Sync 20,354 CPU-s, OneDrive 12,948,
Google Drive 9,913, TeraBox ~12,000), Everything, Cursor and 14 Claude
sessions, each with its own fetch+git MCP servers. The project's re-parse is
one single-threaded process (~52 MB). Parallel parsing could use the 12 logical
cores, but only after the machine is freed; crawling must stay one request at a
time per host (D-001/D-012).

## 6 · Build status and what's next

**Is the build complete?** ✅ Yes for this session's scope: review, verified
auto-fixes, corpus re-parse and verification are done and green. 🔧 The product
still has open items that need a human:

- [ ] `git init` + baseline commit; then CI (ruff, mypy, pytest, review-sheet, smoke)
- [ ] Lawyer review: conciliation-certificate deadline logic, s136(1)(b), 30 golden cases
- [ ] Outcome-label inference from the order section; CCMA `find_holding`
- [ ] COVERAGE.md supersede-check wording (or wire the check)
- [ ] Project `.venv`; rotate `reports/RUN.jsonl`; README install profiles
- [ ] Display-title regex; redirect-hop guards; TLS bundle hardening; schema versions
- [ ] Owner decisions: dead adapters, `browser.py`, pypdf vs PyMuPDF/pypdfium2

**Critical path:** git init (minutes) -> lawyer review (external, blocks
navigator release) -> outcome-inference fix (M, ~1 day incl. corpus diff) ->
CCMA local_corpus harvest. Case-law coverage still depends on SAFLII/
Laws.Africa research access (D-001/D-002), not on code.

**Rollback:** pre-change source, `.index/` and memory file in
`analysis_logs/baseline-2026-10-09/` (diff with `git diff --no-index`); corpus
backup in `sa_labour_corpus/data/backup-2026-10-09/`. Both durable.
