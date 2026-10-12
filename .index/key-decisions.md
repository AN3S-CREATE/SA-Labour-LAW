# Key decisions

ADR-style. The load-bearing collection-policy decisions (D-001, D-013, D-012,
D-011, D-010, D-009, D-002, D-003) are placed first deliberately. D-014 and
D-015 and D-016 are data-quality decisions; D-017 is about not losing the corpus; D-019, D-020 and D-021 govern the CCMA engine (D-021: Layer 1 official publications). D-022 and D-023 govern the Dismissal Navigator; D-024 is how an Act's type is read from its opening. D-025 to D-029 (2026-10-09) come from a verified review: robots read most restrictively, TLS intermediates verified by signature, outcomes only on decisions, "copies of the Act" must name themselves the Act, and OCR text may not retype or retitle a document by mention. Read them before changing anything about
source collection — they are policy, not unfinished work.

---

## D-029 — A machine transcription re-derives everything except identity by mention

**Date:** 2026-10-09 · **Status:** Accepted · extends D-014, D-015, D-024

**Context.** Re-deriving the 27 OCR'd documents from their transcriptions
(so they get tags, language, draft status) also ran the instrument
classifier's last stage, a 20,000-character body scan, and the self-title
finder over noisy OCR text. Measured on the first re-parse: 18 of 27 changed
type away from their source's declared type (Form BCEA3/4/5 -> regulation,
four council agreements -> statute), and a MIBCO fund agreement was titled
"Labour Relations Act" (1995) from its definitions clause. Two integration
reviewers found the same thing independently.

**Decision.** For a transcription (`build_parsed_doc(text_override=...)`),
`parse_statute(..., machine_transcription=True)` types the document only from
strong evidence: its title, an Act's self-identity (D-024) or a code heading
in its opening; otherwise the source's declared type stands. A short title is
read from a transcription only if the document self-identifies as an Act;
otherwise the filename title (D-006) stands. Draft detection, tags, language,
redaction and quotes still run on the transcription.

**Measured before re-parsing.** All 27 transcriptions keep their pre-session
type and title; the conveyor-belt draft code is still flagged as a draft.

---

## D-025 — robots.txt is read most restrictively; rules or delays that cannot be honoured are refusals

**Date:** 2026-10-09 · **Status:** Accepted · extends D-002, D-004

**Context.** `RobotsRules` read only the *first* matching group. CDN-managed
robots.txt files often prepend their own `User-agent: *` block (Content-Signal,
`Allow: /`) above the site's own `User-agent: *` rules, so the site's
`Disallow` lines and an `ai-input=no` further down were ignored (reproduced in
memory). Separately, robots.txt was buffered whole with no size cap, and a
`Crawl-delay` or `Retry-After` of `inf` or `1e12` crashed the run
(OverflowError) or would have slept for years.

**Decision.** (1) Every group naming the winning agent applies, and a path must
be allowed by *each* of them. That is stricter than the RFC 9309 merge: an
`Allow` in one group cannot lift another group's `Disallow`. The longest
Crawl-delay wins, and any Content-Signal "no" wins. (2) robots.txt is streamed
and capped at 512 KiB; a 2xx body over the cap is **not** prefix-parsed (a
later group could be a Disallow for this crawler): the host is tripped as
`robots_oversize`. (3) A non-finite Crawl-delay reads as infinite; a
Crawl-delay or Retry-After above 900 s (`http.MAX_CRAWL_DELAY`,
`MAX_RETRY_AFTER`) trips the host instead of sleeping. Retry-After accepts only
digits or an HTTP-date.

**Rationale.** Same posture as D-004: rules the crawler cannot read in full,
or a pace it cannot honour, mean "no". These choices can cost a document;
they can never take one the operator reserved.

**Consequence.** Hosts with one group behave exactly as before. Robots-driven
trips are recorded as `http_error` gaps whose detail names robots.txt.

---

## D-026 — An AIA intermediate is trusted only if a certifi root's key verifies its signature

**Date:** 2026-10-09 · **Status:** Accepted · enforces D-013

**Context.** D-013 promised that a fetched intermediate is kept only if it
"chains to a root certifi already trusts". The code tried a client verifier
that always fails for a CA certificate, then fell back to comparing the
issuer *name* with root subjects. A forged CA copying a root's name passed
(tested), and on Python 3.13 (`VERIFY_X509_PARTIAL_CHAIN` on by default)
anything written to `db/tls/{host}.pem` becomes a trust anchor for that host.

**Decision.** `tls._intermediate_is_trusted` requires BasicConstraints
`ca=True`, `keyCertSign` when KeyUsage is present, a current validity window,
and `verify_directly_issued_by` against a same-subject certifi root. AIA
downloads stream with a 64 KiB cap.

**Consequence.** The one cached bundle (www.ccma.org.za, Certum DV TLS G2 R39
CA) re-verifies correctly. **Still open (owner decision):** cached bundles are
reused without re-validation, the broad "certificate verify failed" marker
still triggers chain completion, and PARTIAL_CHAIN is still on for per-host
contexts (see dead-code.md, "TLS bundle hardening").

---

## D-027 — Outcome and remedy labels belong only to decisions

**Date:** 2026-10-09 · **Status:** Accepted · extends D-021 to the main corpus

**Context.** The tagger infers outcome and remedy from phrases. Those were
stored on every document, so 8 statutes carried an outcome (the LRA was
"automatically_unfair" because of its s187 heading) and 21 statutes or
amendments carried a remedy.

**Decision.** `pipeline.build_parsed_doc` keeps tagger outcome/remedy (and the
`outcome_decision` audit row) only when the document is adjudicative
(JUDGMENT or AWARD); topic and dismissal tags are unchanged. Because this
makes the declared type decide, the inbox's filename guess was fixed with it:
"act" no longer matches inside "practice" or "contract", and "award" /
"arbitration" win first, so an unfair-labour-practice award keeps its outcome.

**Not decided here (open, needs review against the 12 held judgments):** the
outcome labels on judgments themselves are inferred from the first matching
phrase anywhere in the text, and the critic found 4 of 6 non-review labels
wrong (2 inverted). See dead-code.md, "Outcome labels from first mention".

---

## D-028 — A "held copy of the Act" must name itself the Act

**Date:** 2026-10-09 · **Status:** Accepted · refines D-023 with D-024

**Context.** The navigator chose copies by title substring. The 3 "LRA
copies" included MIBCO fund rules titled "Labour Relations Act"; the 5 "EEA
copies" included the HIV/AIDS code, an EEA summary and the EEA1 form. So all
21 provisions printed "the others may be older consolidations", which was
false for 16 of them and buried the real D-023 signal.

**Decision.** `CorpusSource.texts_for` keeps the title filter as the first
stage, then keeps a text only if `parse.statute.self_identified_act` reads it
as a principal Act (D-024). Excluded doc_ids are logged. The normalised text
and the identity decision are cached per doc_id on (mtime_ns, size); the SQL
selection still runs on every call.

**Consequence (measured on the real corpus).** LRA 2 copies, EEA 2 copies,
21/21 verified; the partial-copy note appears on exactly the 5 genuine
disagreements: s186(1)(e), s186(2), s191(2), s194(4) and EEA s10(2).

---

## D-024 — An Act's type comes from how it names itself, not from what it cites

**Date:** 2026-10-08 · **Status:** Accepted

**Context.** 63 held principal Acts carried the wrong `doc_type`: 43 SAFLII
consolidations as `amendment`, 18 as `regulation` and 2 as
`code_of_good_practice`, including the LRA, BCEA, EEA, COIDA, UIA, NMWA and
MHSA. `classify_doc_type` only recognised a title of the form "Name Act 66 of
1995". Anything else fell through to a 20,000-character body scan, where the
first type *pattern* in priority order won wherever it appeared. A
consolidation's amendment history ("as amended by ... Amendment Act") made it
an amendment, and the LRA's Schedule 8 made it a code.

**Decision.** Between the title check and the body scan, read the opening
(4,000 characters) for three ways an Act names itself: the SAFLII marker
"South Africa: Consolidated Acts" (always the principal Act) or "Numbered Acts"
(the name decides); a gazette assent notice ("has assented to the following
Act" plus "No. N of YYYY: Name Act"); and the long title ("ACT" on its own line,
then "To ..."; "To amend" means an amendment Act). "Amendment" in the Act's own
name separates amending from principal Acts. A title match still wins.

**Measured before re-parsing.** All 63 changes were reviewed by hand. Five
looked wrong but were right: their *display titles* were wrong. OHSA 85 of 1993
was titled "Safety Amendment Act" and the Constitution "South Africa Amendment
Act" (both read from an "as amended by" line), and the Telecommunications Act
had a filename title. The Law of Evidence Amendment Act 1988 is a standalone
principal Act despite its name, and SAFLII consolidates it as one.

**Same family as** D-011, D-015, D-016, D-023: a mention is not an identity.

---

## D-022 — The Dismissal Navigator: rules as code, law as held data, grounded at run time

**Date:** 2026-10-07 · **Status:** Accepted

**Context.** The corpus's stated purpose is to help a dismissed employee
understand the law, but nothing used it except search. The employee's first
need is procedural: the 30-day referral limit (LRA s191(1)(b)) and the right
forum. Those are deterministic rules, not retrieval questions.

**Decision.** Build `labour_corpus.navigator` as a read-only layer. Routing is
plain Python over typed facts. The statutory wording it relies on sits in one
table of 21 verbatim quotes. Every run re-finds each quote in held, readable,
non-draft copies of the Act and reports `verified`, `not_found` or
`not_checked`, plus how many held copies matched. No LLM. Facts forbid extra
fields (no names or IDs) and are never logged. Deadlines are calendar days and
are never extended for weekends or holidays: the Interpretation Act and CCMA
Rules are not held, and a late filing costs far more than an early one.
Evidence is limited to judgments, awards, codes and CCMA material.

**Rationale.** The corpus's guarantee is "never invent". A model paraphrasing
s191 cannot offer that; a quote re-found in a held statute can. Rules kept as
code stay reviewable by a lawyer line by line.

**Consequence.** 21/21 provisions verified against the real corpus on
2026-10-07. Evidence is thin (12 judgments held) and stays thin until the
D-001 case-law gap closes. A misconduct case shows no evidence rather than
noise.

**Rejected.** An LLM answer layer (it cannot be grounded to the standard of
D-006 and D-020); extending deadlines automatically; guidelines and council
agreements as evidence (a measured run matched incidental wording).

---

## D-023 — Held copies of an Act can disagree; count them rather than pick one

**Date:** 2026-10-07 · **Status:** Accepted

**Context.** Grounding against the crawled labour.gov.za LRA PDF (`56dd213b…`)
failed four quotes: s186(1)(e) reads "terminated a contract of employment",
s186(2) "means any unfair act", and s191(2) and s194(4) also differ. That is
wording from before the Labour Relations Amendment Act 6 of 2014. The legacy
consolidation (`c8ea2985…`) carries the amendments. The official source serves
an outdated consolidation, and the first grounding pass found the right copy
only because it tried the largest first.

**Decision.** Search every held copy, record `copies_matching` and
`copies_checked`, and print "In 2 of 3 held copies; the others may be older
consolidations". The 2014 wording is current law, so a quote found only in the
newer copy is still `verified`.

**Rationale.** Same family as D-011, D-015 and D-016: the official-looking copy
is a proxy for the current law, not the law. A disagreement must be visible.

**Follow-up.** Mark the DEL LRA copy as an outdated consolidation in the main
corpus (dead-code.md).

**Update 2026-10-09 (D-028).** The "2 of 3" example counted MIBCO fund rules
as the third LRA copy. Copies must now self-identify as the Act: the real
corpus gives "In 1 of 2" on the 5 genuine disagreements and "2 of 2" elsewhere.

---

## D-001 — Do not defeat bot-management controls, even where the content is free

**Date:** 2026-09-17 · **Status:** Accepted

**Context.** SAFLII is the highest-value free South African case-law source and
was the primary target for the case-law half of this corpus. A live check found
that `www.saflii.org` answers **every** plain HTTP request with HTTP 403 behind
a Cloudflare managed challenge — including `/robots.txt` itself — for an
identified crawler user-agent and for an ordinary desktop browser user-agent
alike. Separately, `www.ccma.org.za` returns 403 from its Azure Application
Gateway to any user-agent that identifies itself as automated, while returning
200 to a browser user-agent. It publishes no robots.txt.

Both hosts would yield content if the crawler stopped declaring itself a robot
and presented as a desktop browser.

**Decision.** Do not do that. The User-Agent always declares the client is a
robot and always carries the operator contact address, and it is not
configurable. A challenge or a bot refusal trips the host for the run and is
recorded as a gap.

**Rationale.** Presenting as a browser to get past a control whose evident
purpose is to refuse automated access is circumvention of an access control,
regardless of whether a captcha-solving service is involved. The brief's own
collection rules require an identifiable crawler and forbid bypassing
Cloudflare. Where the live web and those rules conflict, the rules win and the
gap gets reported.

**Consequence.** Labour Court, LAC, ZACC, ZASCA and ZACCMA judgments, and the
CCMA Rules and forms, are **absent from a default run**. This is the largest
gap in the corpus and it is stated plainly in COVERAGE.md and the README.

**Do not "fix" this** by adding a browser user-agent, a stealth plugin or a
challenge-solving step. The lawful routes are in D-003.

---

## D-019 — The CCMA engine is a gated subpackage, not a new stack

**Date:** 2026-09-23 · **Status:** Accepted

**Context.** A new specification asked for a CCMA Corpus Engine (find, scrape,
download, seed, troubleshoot, knowledge objects, a local MCP server), defaulting
to a greenfield `src/ccma_corpus/` only when no app exists. An app exists.

**Decision.** Build it as `labour_corpus.ccma`, reusing `http.Fetcher`, the
parsers, the tagger and redaction, with its own catalogue at
`data/ccma/catalogue.sqlite`. Gate every live fetch on five conditions:
`status: verified_open` (measured), `enabled`, `operator_confirmed: true`,
`--allow-network`, `--popia-ack`. Gate every full-text processing run, local
sources included, on `--popia-ack`. The only shared-code change is an optional
`Fetcher(retry_waits=...)`, default unchanged.

**Rationale.** The collection policy (D-001, D-002, D-004, D-013) lives in the
shared fetcher; a second stack would have had to re-implement it and could
drift from it. The confirmation gates are the spec's HITL halt points (P0 a-d)
made executable rather than documented.

**Consequence.** On 2026-09-23 no live award source passes the first gate, so
nothing is fetched live. `operator_confirmed` is false on every source until the
operator answers.

---

## D-020 — Labour Court judgments are related authorities, never awards; knowledge is rule-derived

**Date:** 2026-09-23 · **Status:** Accepted

**Decision.** Records carry `record_kind` in `award | related_authority |
practice_material`. LC/LAC judgments from the main corpus enter as
`related_authority`, and every card and strategy built from one says it is not
a CCMA award. Knowledge units are built by rules, not a language model: holdings
are verbatim quoted sentences with paragraph locators, `high` confidence only
for such a quote (each logged to the audit log), scenarios only where two or
more cards share a dispute type and a material issue. Units pass a stricter
PII minimiser than the corpus redactor and are dropped, with a
`minimisation_failed` error row, if any identifier survives.

**Rationale.** Same family as D-006 and D-016: a paraphrase presented as an
award, or a judgment counted as an award, is a wrong statement about the law
that a reader cannot detect. Precision over recall.

**Consequence.** Scenario "fact patterns" are structural, not narrative. That is
the price of never generating text the source did not contain.

---

## D-021 — Layer 1 official publications: robots first, paths allow-listed, never awards

**Date:** 2026-09-23 · **Status:** Accepted

**Context.** The operator asked for Layer 1 publications to be automated: CCMA
information sheets, advice pages and published Rules/forms PDFs, plus the
gov.za PDF of GN 3470 / GG 53294. The instruction came with seven rules:
robots/ToS notes before any fetch; disable a source if robots disallow; plain
GET of allow-listed URLs (Playwright only for a JS-rendered listing on a path
robots allows); no login, CAPTCHA, WAF bypass, cmsonline or SAFLII; `--limit`,
2 workers, 1/4/16 s backoff, checksum and quarantine; seed as practice_note or
statute, never as an award; dry run `--limit 10`, then live `--limit 20` only
if robots_ok.

**Decision.** A new source kind, `official_publication`, inside the CCMA
engine (the D-019 reasoning holds: one fetcher, one policy). Rules 1 and 2
become code: `AllowList.notes_refusal` and `Harvester._robots_preflight` refuse
the whole source before its first request. Rule 3 becomes a path allow-list
enforced in `validate_url`, and `harvest_mode` has no browser value. Rule 6
becomes a loader check plus `RecordKind.is_decision`, which strips case
number, outcome, dispute type and knowledge units from anything that decides
nothing. `operator_confirmed: pending_robots_check` is a conditional
confirmation, re-evaluated every run. The engine never edits the allow-list:
disabling a source under rule 2 is the operator's recorded act.

**Consequence.** Measured 2026-09-23T02:33Z: the CCMA answers robots.txt with
403, so `ccma_official_publications` is disabled; gov.za allows the one path.
The gazette was fetched (1 request after robots) and seeded as a `statute`.

**Found in doing it.** The gazette PDF's text layer is running heads only; the
notice is a scanned image. The shared rule "a PDF under 200 characters is a
scan" passed it, and a draft check over the running heads read as "passed". The
engine now sets aside lines that recur across pages and calls a multi-page PDF
with under 40 characters per page of anything else `ocr_needed`. A draft check
with no body text now says "not possible". Same family as D-011 and D-016: a
proxy (a text layer exists) is not the thing (the text is readable).

**Rejected.** Adding `/advicecategories/ccma-referral-forms/` or other CCMA
paths the operator did not list; any browser path; reusing an archived or
mirrored copy of CCMA pages while the host refuses the crawler.

---

## D-009 — Import judgment references, not judgment text, from legacy indexes

**Date:** 2026-09-17 · **Status:** Accepted

**Context.** A legacy scrape at `windsurf-project/` held 4,891 tagged records.
On inspection it was not a labour corpus: 4,891 records resolved to 201 distinct
titles, because index and listing pages had been stored as documents. It
contained 1,240 copies of a Gauteng court-roll page, 992 Botswana Court of
Appeal records, 121 records whose title was a Cloudflare interstitial
("We're getting things ready"), and only 12 unique labour judgments, one of them
duplicated 23 times on disk.

Its one real asset was 154 SAFLII labour *index pages*, whose listing lines
carry case name, case number, neutral citation, delivery date and parallel ILJ
or BLLR citations.

**Decision.** Import the listing lines as **references** into a `known_citations`
table, and import nothing else. Do not ingest the legacy judgment text, the
court rolls, or the non-South African material.

**Rationale.** The references convert the case-law gap from a caveat into a
measurement: COVERAGE.md can now state "holds 0 of 6,410 known labour
judgments" per forum and year. That is more useful to a reader, and more honest,
than a prose disclaimer. Importing the rest would have imported duplication,
interstitials and another jurisdiction.

**Consequence.** 6,410 references across seven labour forums, 1997-2026; 99.7%
carry a case number, 100% a delivery date, 2,002 a parallel report citation.
Three invariants are tested: a reference never becomes a document, the derived
URL is flagged `url_is_derived = 1`, and the held counter binds to a real
`doc_id` when a matching judgment is acquired.

**The denominator is a floor, not a census.** It counts only what the imported
indexes covered.

**Partly superseded by D-010**, which imports the legacy documents as well. The
reasoning about listing pages, court rolls and non-SA material still stands and
is enforced in `legacy_import.classify_legacy_record`.

---

## D-016 — A draft is not the law, and the corpus must say so

**Date:** 2026-09-20 · **Status:** Accepted

**Context.** Checking Q4 -- whether the 2025 Code of Good Practice on Dismissal
is current -- turned up something worse than an unanswered question. The
Department's Codes of Good Practice library has a Labour Relations folder
containing exactly one document: *"Draft Code of Good Practice on Dismissal for
public comments"*. And the parser classified it `code_of_good_practice`, with
`is_dismissal_code()` returning True -- identical treatment to an operative
code.

For a corpus whose stated purpose is helping a dismissed employee understand
the law that binds them, presenting a draft as the operative instrument is a
serious error. A draft published for comment has no legal effect.

**Decision.** Detect drafts and mark them. `parse/statute.py:detect_draft`
weights the title, where "Draft ... for public comments" is stated plainly,
over the body, where "draft" may appear in ordinary discussion, and treats
commencement wording ("comes into operation", "date of commencement",
"assented to") as evidence the instrument is in force. `ParsedDoc.is_draft`
and `documents.is_draft` carry it, `parser_notes` records that it is not in
force, and COVERAGE.md leads with a section naming every draft held.

**Rationale.** The corpus already refuses to invent a citation. Refusing to
present a draft as law is the same guarantee applied to legal status rather
than provenance.

**Consequence.** Anything reading this corpus can filter `is_draft = 0` for
questions about current law, and COVERAGE.md tells a human reader plainly.

---

## D-018 — A derived artefact must say when it is older than the corpus

**Date:** 2026-09-20 · **Status:** Accepted

**Context.** The search index and COVERAGE.md are built from the database. A
re-parse that finishes after they are built leaves both silently describing an
older corpus. This happened here: the index was written at 15:32 and the last
document at 15:36, so a search would have missed four documents that were
present, with nothing to indicate it.

**Decision.** `stale_artefact_warning` compares the artefact's mtime against
`MAX(updated_at)` in `documents` and returns a message when the artefact is
older. `search` prints it before returning results.

The check returns the message rather than printing it, so the decision is
testable apart from the console -- rich holds the stdout handle it captured at
import, which `capsys` cannot intercept.

**Rationale.** Same family as D-017: the failure is silence. A stale index does
not error, it just quietly answers from less than the corpus holds, and for a
tool whose value rests on not overstating coverage that is exactly the wrong
way to fail.

**Not done:** no automatic reindex. Rebuilding an index mid-search would hide
the very condition the operator needs to see.

---

## D-017 — The corpus must not live in a temporary directory

**Date:** 2026-09-20 · **Status:** Accepted

**Context.** The working corpus was built under
`C:/Users/.../AppData/Local/Temp/lc_live2` because that path started as a
scratch location for a smoke test and was then simply reused. Windows cleaned
the temp directory between sessions and **the entire corpus was destroyed** --
187 documents, the 6,410-reference citation map, and 11 OCR transcriptions that
had cost about ninety minutes of CPU.

Nothing was unrecoverable, because the corpus is derived data and every stage
is re-runnable, which is the design working as intended. But the rebuild was
avoidable.

**Decision.** `CorpusPaths.is_volatile` recognises system temp locations, and
`crawl` and `parse` warn plainly before writing to one. The default `--out`
remains `./data/corpus`, a durable path, and `rebuild_corpus.sh` runs the full
pipeline in order.

**Rationale.** A warning is the right strength: the operator may legitimately
want a throwaway corpus for a smoke test, so this is not made an error. What
must not happen is losing hours of work silently.

**Not done:** no automatic relocation or backup. Moving the operator's data
without being asked would be worse than the warning.

---

## D-014 — OCR output is persisted beside the raw bytes

**Date:** 2026-09-18 · **Status:** Accepted

**Context.** OCR text lived only in the database. `parse` always re-extracts
from `raw/`, and re-extracting a scanned PDF yields nothing — so the next
`parse` without `--ocr` would silently push the document back to `ocr_needed`
and discard an hour of recognition.

**Decision.** Write the transcription to `raw/.../ocr.txt` beside the bytes it
came from. `parse` reuses it when re-extraction yields no text, and records in
`parser_notes` that the text is a stored machine transcription.

**Rationale.** OCR is expensive and its output cannot be recovered from the
scan. It belongs with the raw artefact, not only in a derived table, and
reusing it keeps `parse` genuinely idempotent for scanned documents.

**Consequence.** Re-parsing after a parser change no longer destroys OCR work.
The capped confidence and the transcription note survive the round trip.

---

## D-015 — A document titles itself in its opening; later Act names are citations

**Date:** 2026-09-18 · **Status:** Accepted

**Context.** Council agreements and CCMA documents were being titled
"Labour Relations Act" or "All the provisions of the Labour Relations Act",
because the short-title search ran over the first 40,000 characters and matched
a cross-reference in the body.

Two defects underneath it: the title pattern required "Act" in title case, so
ALL-CAPS statute title pages never matched at all, and the connectors "of" and
"No" were case-sensitive, so "ACT 66 OF 1995" was invisible.

**Decision.** Search only the opening 3,000 characters, require every word of
the name to start with a capital (which covers both title case and ALL CAPS),
match "Act", "No" and "of" case-insensitively, and skip any match preceded by
cross-reference wording such as "in terms of the" or "provisions of the".

**Consequence.** ALL-CAPS title pages now resolve; a council agreement citing
the LRA is no longer titled as the LRA. This is the same failure mode as D-011
and D-012 — a proxy mistaken for the thing itself — and is the reason that
pattern is called out in `REPO_ANALYSIS_MEMORY.md`.

---

## D-013 — Complete a missing certificate chain; never disable verification

**Date:** 2026-09-18 · **Status:** Accepted

**Context.** Even after D-012 established that the CCMA serves the production
user-agent HTTP 200, the crawler still could not reach it. The real error was

    [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate

`openssl s_client` confirms the cause: `www.ccma.org.za` sends **only its leaf
certificate** and omits the intermediate CA, giving
`Verify return code: 21 (unable to verify the first certificate)`. curl and
browsers hide this by fetching the missing intermediate from the certificate's
Authority Information Access (AIA) extension. Python's ssl module does not, so
httpx failed on a site a browser opens without complaint.

That is why the crawler tripped the host while curl succeeded: two different
trust stores and one missing intermediate, not a refusal.

**Decision.** Do what a browser does, and nothing more. `tls.py`:

1. reads the server's certificate over one connection that exchanges **no
   application data**;
2. reads the AIA "CA Issuers" URL from it and fetches the intermediate;
3. **verifies that the intermediate chains to a root certifi already trusts**,
   discarding it otherwise;
4. writes a per-host bundle of certifi plus that intermediate, used for every
   real request.

It runs only after a verification failure whose text matches an incomplete
chain, once per host per run, and the bundle is cached under `db/tls/`.

**Explicitly rejected:** `verify=False`, a per-host "insecure" flag, or adding
the leaf to the trust store. Each would turn a correctable server
misconfiguration into a standing hole in the crawler's transport security, for
every host, for the life of the corpus.

**Consequence.** CCMA collects: 4 referral forms and 5 information sheets in a
capped test, with full verification retained. The same mechanism fixes any
other public body that ships an incomplete chain, which is common.

---

## D-012 — CCMA is collectable after all; the earlier finding was measured wrong

**Date:** 2026-09-18 · **Status:** Accepted, corrects part of D-001

**Context.** D-001 recorded `www.ccma.org.za` as refusing identified crawlers,
alongside SAFLII. A re-check on 2026-09-18 shows that conclusion was drawn from
a badly chosen probe.

The 2026-09-17 test compared three user-agents, but the "identified crawler"
variants were both **short token forms** such as
`SALabourCorpusBot/0.1 (+mailto:...)`. Those are still rejected with HTTP 403 by
the Azure Application Gateway. The user-agent this project actually sends is the
Mozilla-compatible form carrying the product token, project URL and operator
contact — and that form is served **HTTP 200 with real content**, consistently,
across `/`, `/resources/` and `/about-us/`. `robots.txt` is HTTP 404, so no
restrictions are published.

**Decision.** Correct the record and enable the CCMA. Three sources registered:
referral forms, information sheets, and resources.

**Rationale.** The test measured a user-agent the crawler never sends, and the
conclusion was generalised from it. A finding recorded as fact about the live
web has to be measured with the exact client that will make the request.

**What does not change.** SAFLII remains blocked; D-001 stands for that host.
And the CCMA still publishes rules, guidance and forms here, **not** its award
archive, so the CCMA-incompleteness statement in COVERAGE.md is unaffected.

**Superseded in part on 2026-09-20 — access proved conditional.** After the
~78 documents collected under this decision, the gateway began returning
HTTP 403 for **every** path, including `/`, to the same user-agent over a fully
verified TLS chain. The most likely reading is a rate or reputation response by
the WAF, which is the control working as intended.

The response is to back off, not to adapt around it: no user-agent rotation, no
slower-but-quieter crawling to stay under a threshold. Both would be
circumvention with extra steps, and D-001's reasoning covers them. The CCMA
sources now carry 8-15 second delays so that a future run, if access returns,
is gentler than the one that lost it. The durable fix is to ask the CCMA for
research access.

So the accurate statement is neither "blocked" nor "collectable" but
**conditional**: it served an identified crawler, then stopped.

**Lesson.** This is the third instance of the same failure mode in this project:
a conclusion drawn from a proxy for the real thing. The LRA was classified from
a citation rather than its title; 14 Word files were called scans because a
reader failed; and here a host was called hostile because the wrong client was
tested. Measure the actual artefact.

---

## D-011 — Decide format by magic bytes; an unreadable file is not a scan

**Date:** 2026-09-17 · **Status:** Accepted

**Context.** 18 documents were flagged `ocr_needed`, and the earlier report
described them as scanned PDFs. Inspection of the bytes showed 14 of them were
Word 97-2003 OLE2 documents: labour.gov.za serves them as
`application/msword` from URLs ending `.docx`. python-docx raised `BadZipFile`,
and the code turned that into `ocr_needed`.

That label was wrong in a way that matters: it pointed the operator at OCR for
files that only needed a Word reader. Among them were the Skills Development
Act, COIDA, the Occupational Health and Safety Act, the Unemployment Insurance
Acts, three Codes of Good Practice, and BCEA summaries in Sotho, IsiZulu and
IsiNdebele.

**Decision.** Decide format from the magic number, never from the Content-Type
header or the URL extension, and separate two distinct failures:

* `ocr_needed` — a genuine scan with no text layer; OCR would help.
* `unsupported_format` — a readable format whose reader is not installed.

Legacy Word is read with `antiword` when available, and when it is not, the
document is reported as `unsupported_format` with an install hint.

**Rationale.** Server labels are routinely wrong on government document
libraries; bytes are the only reliable evidence. And a status that names the
wrong remedy is worse than no status, because the operator acts on it.

**Consequence.** All 14 recovered on re-parse. `ocr_needed` fell from 18 to 4,
and those 4 are genuine scans. Titles improved too, because they are now read
from the document rather than derived from the filename.

---

## D-010 — Import the legacy documents too, with provenance recorded

**Date:** 2026-09-17 · **Status:** Accepted, supersedes part of D-009

**Context.** D-009 imported only references. The operator then asked for the
documents as well. The legacy corpus held 12 unique labour judgments and 65
unique consolidated Acts once content-hash deduplicated, against 146 listing
pages and 2,530 non-SA records that must not be imported.

**Decision.** Import the documents, and add a `provenance` field to the document
contract with three values: `crawled`, `operator_supplied`,
`legacy_local_harvest`. A legacy document carries its correct canonical URL --
that is where the document authoritatively lives -- while `provenance` and
`http_status = NULL` record that this crawler never fetched it.

**Rationale.** `canonical_url` answers "where does this document live"; it is
not a claim about how the copy was obtained. Conflating the two was the risk,
and a dedicated field resolves it better than mangling the URL. The same
ontology and redaction apply, so a legacy judgment is analysed exactly like a
crawled one.

**Consequence.** 77 documents imported; coverage moved from 0 to 12 of 6,410
known labour judgments (0.2%), which is the first proof that the held counter
works on real data. COVERAGE.md now reports a provenance breakdown.

Listing pages are still rejected, by citation count and court-heading test.
Court rolls and non-SA databases are still excluded.

---

## D-002 — Honour `Content-Signal: ai-input=no`

**Date:** 2026-09-17 · **Status:** Accepted

**Context.** `lawlibrary.org.za` (Laws.Africa) is reachable, well structured and
publishes under CC-BY. Its robots.txt sets, for `User-agent: *`,
`Content-Signal: ai-train=no, search=yes, ai-input=no`, disallows `/judgments/`
and `/akn/za/judgment/`, and frames the restriction as an express reservation of
rights. Named search agents get `ai-input=yes`; this crawler is not one.

**Decision.** Treat `ai-input=no` as barring collection for this corpus, because
`ai-input` covers retrieval-augmented generation and grounding — precisely this
corpus's purpose. The source is registered but disabled.

**Rationale.** A machine-readable reservation of rights is exactly the kind of
signal a well-behaved crawler exists to respect. A CC-BY licence on the content
does not override the site operator's stated position on automated collection.

**Consequence.** `robots.ContentSignal.permits_rag_corpus()` gates collection,
and `http.Fetcher.check_policy` raises `ContentSignalOptOut`.

---

## D-003 — Blocked sources stay registered; the operator inbox is the growth path

**Date:** 2026-09-17 · **Status:** Accepted

**Decision.** Sources that cannot be collected remain in `sources.yaml` with an
`access` value recording why, rather than being deleted. `preflight_gaps` writes
a gap for each before any request. The `inbox/` drop-folder is the supported way
to add documents the operator already lawfully holds.

**Rationale.** A corpus that silently omits a source cannot tell a downstream
model what is missing. Registering the gap is what lets COVERAGE.md be honest,
and it means a future permission or data agreement flips a flag rather than
requiring new code.

**Consequence.** `sources.yaml` lists 19 sources of which 7 are collectable.
Inbox documents are recorded as `operator_inbox` with a `file://` canonical URL
so nothing ever claims a local file came from a public website.

---

## D-004 — A challenge on robots.txt means disallow, not allow-all

**Date:** 2026-09-17 · **Status:** Accepted

**Context.** RFC 9309 says an "unavailable" status (4xx other than 429) means a
crawler may access any resource. SAFLII returns 403 on `/robots.txt`, so a naive
RFC reading would license crawling the entire site.

**Decision.** Deviate. A challenge or bot refusal on robots.txt marks the host
`blocked_by_challenge`, and `can_fetch` returns False for everything. 5xx and
429 are treated as unreachable and also disallow.

**Rationale.** Reading "I am refusing your robot" as "crawl anything" inverts
the operator's evident intent.

---

## D-005 — Rule-based tagging, with paragraph evidence, before any embeddings

**Date:** 2026-09-17 · **Status:** Accepted

**Decision.** The tagger is regular expressions over the LRA's own structure.
Each tag records the patterns that fired and the paragraph markers that
contained them. `mining` needs at least two distinct mining signals.
`automatically_unfair` writes an explicit audit reason. Embeddings are out of
scope for v1.

**Rationale.** A rule-based tag is auditable and cheap; an embedding is neither.
For a corpus whose purpose is to help someone understand why a dismissal may
have been unfair, being able to point at the paragraph matters more than recall.

**Consequence.** Precision bugs are real and must be fixed as such — e.g.
`mhsa_s54_stoppage` originally matched any statute's "section 54" and now
requires mining context within 120 characters.

---

## D-006 — Titles may be derived from a filename; citations may not

**Date:** 2026-09-17 · **Status:** Accepted

**Decision.** When a document carries no title of its own (the norm for PDFs), a
display title is derived from the published filename and a `parser_note` records
that it was derived. Citations, case numbers and dates are **never** derived
from a URL or filename; they are read from the text or left null.

**Rationale.** A title is a convenience label. A citation is a claim about what a
court decided, and a wrong one is worse than a missing one.

---

## D-007 — Redaction is narrow and validated

**Date:** 2026-09-17 · **Status:** Accepted

**Decision.** Identity numbers are redacted only after passing a date,
citizenship-digit and Luhn checksum test. Bank, tax and passport numbers require
a nearby label. Case numbers, neutral citations, statute references, gazette
numbers and monetary amounts are explicitly preserved and tested.

**Rationale.** Over-redaction would destroy the corpus's entire value. Party
names stay as published because judgments are public records; POPIA
minimisation here means adding no identity beyond what the judgment already
carries.

---

## D-008 — Broad exception handling at the document boundary only

**Date:** 2026-09-17 · **Status:** Accepted

**Decision.** `Crawler._collect` catches `Exception` around parsing, with a
comment explaining why. Everywhere else, exception handling is specific and
there are no bare `except:` clauses.

**Rationale.** Third-party parsers raise a wide, poorly documented range of
errors on corrupt input — a legacy `.doc` served as `.docx` raised `BadZipFile`
and ended a run during development. One malformed government PDF must never end
a multi-hour crawl. The failure is recorded with its exception type and the run
continues.
