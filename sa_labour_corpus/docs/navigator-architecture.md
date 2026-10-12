# Dismissal Navigator: product vision, architecture, roadmap

Written 2026-10-07. Status: first vertical slice built and tested (D-022).

## 1. What the repository already is

`sa_labour_corpus` is a local-first, provenance-tracked corpus of South African
labour law. It has 397 documents, holds all 12 target labour Acts, and runs 73
LRA-structured ontology tags with paragraph evidence. It has an FTS5 index, a
gated CCMA engine with an MCP server, and an unusually strict honesty contract:
it never invents a citation, never presents a draft as law, and records every
access refusal as a gap.

Its stated purpose (`.index/architecture.md`) is to let "a later retrieval or
agent layer ... help a dismissed employee understand unfair-dismissal law".
**That layer did not exist.** Every module served collection; nothing answered
the employee's actual questions:

1. Was what happened to me a dismissal, and what kind?
2. **By when must I act?** (the question that loses most cases)
3. Where do I go: CCMA, a bargaining council, or the Labour Court?
4. What can I get?
5. What should I check about how it was done?
6. Where does the law actually say so?

**Core value proposition:** the corpus can answer those questions with wording
it *holds*, not wording a model remembers. That is a guarantee no chat
assistant offers. The navigator is built to deliver it.

## 2. Product vision

**Dismissal Navigator: grounded, deadline-first triage for dismissed workers
and the people who help them** (union organisers, advice offices, CCMA
walk-in desks, pro bono clinics).

Principles carried over from the corpus:

- **Grounded or flagged.** Every legal statement carries a provision whose
  verbatim wording is re-found in a held, non-draft statute at run time. If the
  wording is missing, the output says "NOT FOUND - do not rely on it" and the
  CLI exits 3. It also exits 3 when nothing could be checked (no corpus at
  `--out`).
- **Deadline first.** Missing the 30-day referral window is the most common
  way a valid claim is lost, so it leads the output.
- **Conservative arithmetic.** Calendar days, no assumed weekend or holiday
  extension. Filing a day early costs nothing; filing a day late costs the case.
- **Minimal facts.** No name, ID number or contact detail is collected
  (`extra="forbid"`), and facts are never logged (POPIA minimisation).
- **Information, not advice.** The output says so, and it points to the CCMA,
  unions and lawyers for anything that is near a deadline.

### Capabilities

| # | Capability | Status |
|---|---|---|
| 1 | **Dispute classification** under LRA s186-s188, s191(5) and EEA s10, from the reason the employee alleges | Built |
| 2 | **Deadline engine**: s191(1)(b) 30/90 days, s191(11) Labour Court 90 days after the certificate, EEA s10(2) six months, with condonation routing (s191(2), EEA s10(3)) | Built |
| 3 | **Forum router**: council or CCMA (s191(1)(a)), arbitration or Labour Court (s191(5)), s191(12) election, s191(5A) probation con-arb, s191(13) whistle-blower route | Built |
| 4 | **Statutory grounding verifier**: every quote re-found in held text, counted across all held copies of the Act | Built |
| 5 | **Remedy ceilings and onus**: s194(1)/(3)/(4), s192(2) | Built |
| 6 | **Fairness checklist** reusing the ontology's process-defect layer, so the checklist and the tagger share one vocabulary | Built |
| 7 | **Evidence retrieval**: held judgments and codes on the employee's issue; drafts, Act copies and incidental document types excluded | Built (thin: case law is gated by D-001) |
| 8 | **Case file export**: a referral-ready summary pre-filling LRA Form 7.11 fields | Roadmap |
| 9 | **MCP tool + HTTP API**, so agent and web front-ends call one engine | MCP built; HTTP API roadmap |
| 10 | **Multilingual plain-language output** (isiZulu, isiXhosa, Afrikaans, Sesotho), reviewed by humans | Roadmap |

## 3. Architecture

```
CaseFacts (pydantic, extra=forbid, no PII)
   |
   v
routing.classify ──► Track (arbitration | labour_court | election | eea)
   |                        |
   |                        v
   |              engine._deadlines / _route / _remedy
   |                        |   (provision keys only)
   v                        v
provisions.PROVISIONS ──► grounding.ground ──► ProvisionRef
   (verbatim quotes)        |                   verified | not_found | not_checked
                            v                   + doc_id + copies_matching/checked
                     CorpusSource (read-only SQLite + text/)
   |
evidence.CorpusEvidence (read-only FTS5; type allow-list; drafts and Act copies out)
   |
   v
Assessment (frozen pydantic) ──► render.to_markdown | model_dump_json
   |
   v
cli: labour-corpus navigate assess | provisions        log: JSON lines on stderr
```

### Directory

```
src/labour_corpus/navigator/
  models.py      CaseFacts, Assessment, ProvisionRef, Deadline, RouteStep, EvidenceHit
  provisions.py  21 verbatim provisions (LRA s186-s194, EEA s10)
  grounding.py   normalise, StatuteSource protocol, CorpusSource, InMemorySource, ground
  routing.py     classify, conciliation_forum, checklist (pure)
  deadlines.py   add_days, add_months, make_deadline (pure)
  evidence.py    issues_for, CorpusEvidence (read-only)
  engine.py      Navigator.assess: composition only, no I/O of its own
  render.py      Markdown for a non-lawyer
  log.py         JSON formatter; facts never logged
  cli.py         `navigate assess`, `navigate provisions`
tests/test_navigator.py   49 tests; 99% line coverage of the package
examples/*.json           sample facts files
```

### Key design decisions

- **Rules as code, law as data, grounding at run time.** Routing logic is
  plain Python that a lawyer can review line by line. The *wording* it relies
  on lives in one table and is verified against the corpus on every run, so a
  corpus that loses or replaces a statute surfaces as a warning, not a silent
  wrong answer. No LLM is in the loop (same reasoning as D-020).
- **Ports, not globals.** `StatuteSource` and `EvidenceFinder` are protocols.
  Tests use `InMemorySource`; production uses read-only SQLite. A future
  Laws.Africa or API-backed source drops in without engine changes.
- **Read-only against the corpus.** SQLite is opened `mode=ro` through
  `sqlite_ro.connect_readonly`, whose URI is percent-encoded, so a `#` or `%`
  in a folder name cannot drop `mode=ro`. The navigator cannot corrupt the
  corpus and needs no write lock while a crawl runs.
- **Copy counting.** The official labour.gov.za LRA PDF turned out to be a
  pre-2014 consolidation. Grounding therefore counts matches across *every*
  held copy and the output says "In 1 of 2 held copies; the others may be older
  consolidations", so the disagreement is visible rather than hidden.
- **A copy is a document that names itself as the Act.** Copies are picked by
  title, then kept only if their opening identifies them as a principal Act
  (`parse.statute.self_identified_act`, D-024). Without that second step, fund
  rules, a code, a summary and a form that carry an Act's name counted as
  "copies", and all 21 provisions read as partial matches ("2 of 3", "1 of 5")
  even where the genuine consolidations agree.
- **Exhaustive routing.** `Track` is a `StrEnum` and the engine names every
  track in its deadline and route dispatch; an unknown track raises instead of
  falling through to another track's forum and grounded basis.
- **Exit codes as contract:** 0 grounded; 2 invalid facts (including a date
  after the assessment date); 3 some relied-on wording not found, or not
  checked because no corpus or no copy of the Act was found. CI and agents can
  gate on them.

### Data layer

No new store. The navigator reads `db/corpus.sqlite`, `text/` and
`index/fts.sqlite`. Case facts live only in the process and in whatever file
the caller supplied. A future saved-case feature needs its own encrypted store
with retention limits (POPIA s14), kept outside `data/corpus/`.

### Auth model

- **Today (local CLI / stdio MCP):** no auth. The trust boundary is the
  operator's machine, matching the corpus's local-first design.
- **Hosted API (roadmap):** stateless `POST /assess` with no facts persisted;
  API keys per partner organisation (advice office, union) with rate limits;
  no end-user accounts, because they would mean collecting personal
  information the engine does not need. Any saved-case feature requires
  per-organisation tenancy, encryption at rest and an explicit POPIA lawful
  basis before it is built.

## 4. Verified behaviour (2026-10-07, real corpus)

- `labour-corpus navigate provisions`: **21/21 verified**. LRA quotes found in
  the consolidated text `c8ea2985…`; EEA quotes in `a63a17c3…`.
- The crawled DEL copy `56dd213b…` lacks the 2014 wording of s186(1)(e),
  s186(2), s191(2) and s194(4). It is a pre-2014 consolidation.
- Re-measured 2026-10-09 after the self-identity filter: 2 LRA copies
  (`c8ea2985…`, `56dd213b…`) and 2 EEA copies (`a63a17c3…`, `29dee777…`) are
  checked. Four title matches are excluded: MIBCO fund rules titled "Labour
  Relations Act", the HIV/AIDS code, an EEA summary and the EEA1 declaration
  form. Exactly five provisions match 1 of 2 copies: the four LRA sections above
  and EEA s10(2). The other 16 match 2 of 2.
- Constructive dismissal → surfaces `[2011] ZALCCT 10`; unfair labour practice
  → `[2014] ZALCJHB 364`, `[2026] ZALCCT 40`, `[2000] ZALAC 4`. Misconduct → no
  hits: the corpus holds 12 judgments, and the honest answer is none rather
  than a council constitution matching "misconduct" incidentally.

## 5. Roadmap

**Phase 2: make it usable by the people who help workers.**
1. **DONE 2026-10-08.** `navigate interview`: plain-language questions build
   `CaseFacts` (`navigator/interview.py`); `--save` writes them only on request.
2. **BLOCKED.** Case summary export pre-filling the LRA Form 7.11 referral
   fields, plus a "serve a copy on the employer" checklist (s191(3)). The form
   is **not held** (checked 2026-10-07: the corpus only cites it, and the CCMA
   host refuses the crawler), so it must arrive via `inbox/` or research access.
3. **DONE 2026-10-08.** MCP server `dismissal-navigator` with
   `navigator_assess` and `navigator_provisions` (`navigator/mcp_server.py`,
   `mcp/navigator_server.py`). It reuses the CCMA server's JSON-RPC handling;
   invalid or personal fields come back as `isError` results. Every call
   re-queries which copies are held; the server keeps normalised Act texts
   keyed by file modification time and size, so an unchanged file is not read
   again and a changed or newly held copy is picked up without a restart.
4. **HALF DONE 2026-10-08.** 30 golden fact patterns covering every track and
   deadline edge case (`tests/golden/navigator_cases.yaml`), run in the test
   suite, plus `navigate review-sheet` for sign-off. **Still needed: a
   practising labour lawyer's review.** Until then every case is
   `pending_lawyer_review`.

**Phase 3: law-quality hardening.**
5. Hold the Interpretation Act and the CCMA Rules, then ground day-counting
   and weekend/holiday rules instead of declining to extend.
6. Verify GG 53294 (the 2025 dismissal code) against the image and flip
   `CURRENT_DISMISSAL_CODE.verified`; replace generic checklist items with the
   code's own grounded provisions; the warning disappears automatically.
7. **DONE 2026-10-08 (D-024).** Acts that identify themselves in their
   opening (SAFLII consolidated/numbered marker, gazette assent notice, long
   title) are now classified by that identity. The evidence title exclusion
   stays as defence in depth.
8. s189A large-scale retrenchment, s198 TES, s197 transfers, fixed-term
   non-renewal (s186(1)(b)) as explicit routes.

**Phase 4: reach.**
9. Stateless HTTP API (FastAPI) with the auth model above; a small web
   front-end with plain-language, mobile-first output.
10. Multilingual output with human-reviewed translations; never machine
    translation of statutory quotes.
11. Case-law depth: when SAFLII/AfricanLII access is granted (Q1), evidence
    retrieval gains real precedent per issue, plus an outcome-pattern view.
