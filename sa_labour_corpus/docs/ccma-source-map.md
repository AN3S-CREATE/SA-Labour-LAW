# CCMA source map

Every status below was **measured on 2026-09-23 with the production client**
(`labour_corpus.http.Fetcher`, identified User-Agent carrying the operator
contact, TLS chain completion on). Per D-012, a finding about the live web is
only recorded after testing it with the exact client that will make the request.
Machine-readable copy: `ccma_sources.yaml`.

Nothing here is operator-confirmed yet. Until the operator answers the
allow-list question, every source is **research only**.

## Summary

| Source | Official? | Record type | Status 2026-09-23 | Harvest? |
|---|---|---|---|---|
| SAFLII ZACCMA collection | Yes (LII) | Published CCMA awards (small, selected subset) | HTTP 403, Cloudflare challenge on `/robots.txt` and the index | **No** — blocked (D-001) |
| CCMA website (`www.ccma.org.za`) | Yes | Rules, guidelines, info sheets, forms. **No award archive** | HTTP 403 bot refusal on every path incl. `/robots.txt` | **No** — blocked; was open until 2026-09-20 (D-012) |
| Laws.Africa Law Library | Yes (LII) | Judgments incl. labour | robots: `Content-Signal ai-input=no`, `/judgments/` disallowed | **No** — reservation of rights honoured (D-002) |
| Labour Guide case law (`caselaw.labourguide.co.za`) | **No** (commercial) | CCMA / council awards | not probed | **No** — dropped: unofficial |
| CCMA Online (`cmsonline.ccma.org.za`) | Yes, but not a publication | Referral / case management | not probed | **No** — dropped: parties' case data |
| Main corpus on disk (`data/corpus`) | n/a (local) | 12 LC/LAC judgments → `related_authority` | local | Yes, with `--popia-ack` |
| Operator inbox (`data/ccma/inbox`) | n/a (local) | Awards the operator lawfully holds | local | Yes, with `--popia-ack` |
| `ccma_official_publications` (Layer 1) | Yes | Information sheets, advice pages, Rules and forms → `practice_note` | robots.txt HTTP 403 bot refusal | **No** — disabled under rule 2 |
| `govza_dismissal_code_2025` (Layer 1) | Yes | Code of Good Practice: Dismissal, 2025 (GN 3470 / GG 53294) → `statute` | robots.txt HTTP 200; path allowed | **Yes** — exact URL only |

Paid databases (Juta, LexisNexis, Sabinet and similar) are out of scope and are
a hard error at registry load (`config.assert_not_paid_host`).

## Per source

### SAFLII — South Africa: Commission for Conciliation, Mediation and Arbitration (ZACCMA)

* **Base URL:** `https://www.saflii.org/za/cases/ZACCMA/`
* **Robots:** `GET /robots.txt` → HTTP 403 with Cloudflare "Just a moment..."
  interstitial. Treated as disallow-all (D-004).
* **Terms:** SAFLII publishes for free access; the collection is a small
  selected subset of CCMA awards (awards carry the CCMA watermark notice). It is
  not the CCMA's archive and is never described as such.
* **Identity scheme:** `/za/cases/ZACCMA/{year}/{n}.html` → `native_id =
  ZACCMA-{year}-{n}`; CCMA case numbers (`GAJB1234-24`) and neutral citations
  (`[2024] ZACCMA 12`) are read from the text.
* **Pagination:** database index → year pages → record pages (+ original PDF
  link). Walked by `find.parse_saflii_database_index` /
  `parse_saflii_year_listing`, which reuse the existing SAFLII walkers tested on
  154 real SAFLII index pages from the legacy import (D-009).
* **File types:** HTML rendering; original PDF/RTF.
* **Rate-limit evidence:** none observable (challenge precedes everything).
* **Bulk download offered officially:** not found.
* **Route to coverage:** request research access from SAFLII / AfricanLII. On a
  grant, set `status: verified_open` after re-measuring, and
  `operator_confirmed: true`; no code change is needed.

### CCMA — official website

* **Base URL:** `https://www.ccma.org.za/ccma-resources/` (also
  `/advicecategories/…` for forms and information sheets).
* **Robots:** `/robots.txt` → HTTP 403 today; it was HTTP 404 (no rules) when
  the site last served this crawler.
* **Access history:** served the identified crawler on 2026-09-18 (D-012, after
  the TLS chain fix D-013), collected ~78 documents, then from 2026-09-20 began
  answering 403 to every path. Re-measured 2026-09-23: still 403. Recorded as
  conditional; the response is to back off, not to adapt (no UA rotation, no
  quieter crawling to stay under a threshold).
* **Record types:** CCMA Rules, guidelines, information sheets, referral forms
  → `practice_material`. The CCMA **does not publish its award archive** on the
  site, and nothing on it is treated as an award.
* **Pagination:** single listing pages of PDF links (`parse_link_index`).
* **Route:** request research access from the CCMA.

### Laws.Africa Law Library (`lawlibrary.org.za`)

* robots.txt reachable (HTTP 200). For `User-agent: *`:
  `Content-Signal: ai-train=no, search=yes, ai-input=no`, and `Disallow:
  /judgments/`. `ai-input` covers retrieval and grounding — this engine's exact
  purpose — so the source is disabled regardless of the CC-BY content licence.
* **Route:** a data agreement that grants `ai-input` to an identified agent.

### Labour Guide (`caselaw.labourguide.co.za`)

* Found by web search as a searchable award database. It is a private
  commercial publisher, not an official source; the spec admits official
  sources only. Dropped without probing.

### CCMA Online (`cmsonline.ccma.org.za`)

* The CCMA's referral and case-management portal. It is not a publication of
  awards and holds parties' own case data. Dropped; never fetched.

## Layer 1 — official publications (`kind: official_publication`)

Added 2026-09-23 on the operator's instruction. **These notes were written
before any request to either host**, per the operator's rule 1. The robots
results are recorded afterwards, under "Robots check results" below.

These are official publications, not awards. Every record is seeded as
`practice_note` or `statute`, never as an award, and none yields a case card,
strategy or scenario. The scope is two hosts and the paths listed below.
SAFLII and CCMA Online (`cmsonline.ccma.org.za`) are out of scope and are not
touched.

**Harvest mode `allowlisted_http`.** Plain HTTP GET through the production
client (`http.Fetcher`, identified User-Agent, robots enforced per request).
Every URL is checked against the source's host **and path** allow-list before
the request and again after redirects. No browser is used. Playwright would be
considered only for a listing shown to be JS-rendered **and** whose path
robots.txt allows. Neither condition has been established, so the engine has
no browser mode.

**Order of operations** (operator rules 1, 2 and 7):

1. These notes are written before any request.
2. robots.txt is checked with the production client
   (`labour-corpus ccma robots`). That command fetches robots.txt and nothing
   else.
3. If robots.txt disallows an allow-listed path, the source is disabled
   (`enabled: false`). The same applies if the host refuses robots.txt itself
   (bot refusal or challenge, D-004). No browser and no alternative route is
   tried.
4. A dry run with `--limit 10` comes next. The live run with `--limit 20`
   happens only if robots_ok.
5. No login, no CAPTCHA, no WAF bypass, no user-agent change (D-001, D-012).
   Every harvest re-checks robots for every allow-listed path before fetching
   anything.

### `ccma_official_publications` — CCMA information sheets, advice pages, Rules and forms

* **Host:** `www.ccma.org.za` only.
* **Allowed paths:**
  * `/advicecategories/information-sheets/` (listing)
  * `/advices/` (advice detail pages)
  * `/wp-content/uploads/` (published PDFs: Rules, forms, information sheets)

  Nothing else on the host is fetched. In particular `/wp-admin/`, search and
  the `cmsonline` subdomain are excluded.
* **Record kind:** `practice_note`.
* **Robots, as recorded before this check:**
  * 2026-09-18: `/robots.txt` was HTTP 404 (no rules) while the site served
    this crawler (D-012).
  * From 2026-09-20: HTTP 403 (Azure Application Gateway bot refusal) on every
    path, including `/robots.txt`.
  * 2026-09-23, earlier today: still HTTP 403.

  A 403 on robots.txt is treated as disallow-all (D-004). **Expected outcome:
  disabled, unless the refusal has lifted.**
* **Terms:** the CCMA is the statutory body established by LRA s112 and
  publishes this guidance for the public. The site's terms of use were not
  reviewed in this pass, because reading them would itself mean fetching from
  a host that currently refuses this crawler. Material is stored locally for
  research and retrieval and is not republished.
* **Politeness:** 8-15 s between requests (D-012), at most 2 workers. Backoff
  is 1 s / 4 s / 16 s, on 429 and 5xx only. A refusal is never retried.

### `govza_dismissal_code_2025` — Code of Good Practice: Dismissal, 2025 (GN 3470 / GG 53294)

* **URL:**
  `https://www.gov.za/sites/default/files/gcis_document/202509/53294gen3470.pdf`.
  This exact file is the only allow-listed path on the host.
* **Record kind:** `statute` (a gazetted instrument). The title is the
  operator's label. The gazette and notice numbers are checked against the
  text of the PDF, never taken from the filename (D-006).
* **Robots, as recorded before this check:** gov.za served this crawler
  HTTP 200 for its `/documents` listings (main corpus, 2026-09-17). The robots
  rules for `/sites/default/files/` have not been checked for this path; that
  check comes next.
* **Terms:** gov.za is the government portal run by GCIS. Under Copyright Act
  98 of 1978 s12(8)(a), no copyright subsists in official texts of a
  legislative, administrative or legal nature. The Government Gazette remains
  the authoritative text; this is a research copy. The gov.za terms page was
  not reviewed in this pass.
* **Why it matters:** the main corpus holds only the **draft** dismissal code
  (D-016), and `del_gov.CURRENT_DISMISSAL_CODE` is marked `verified: False`
  (open questions Q4 and Q5). If this PDF is the gazetted code, it settles the
  project's most important open legal-accuracy question.

### Robots check results

Measured **2026-09-23T02:33Z** with
`labour-corpus ccma robots -s official_publication --allow-network`, using the
production client (identified User-Agent, TLS chain completion on). The
command requested robots.txt from each host and nothing else. Each verdict is
also in `logs/ccma-audit.jsonl` (`decision: robots_check`).

| Source | robots.txt | Verdict | Action |
|---|---|---|---|
| `ccma_official_publications` | HTTP 403 (Azure Application Gateway bot refusal, `http_403_bot_refusal`); host tripped | **Refused** for `/advicecategories/information-sheets/`, `/advices/` and `/wp-content/uploads/` (D-004) | **Disabled** (`enabled: false`, `status: blocked_bot_management`, `robots_ok: false`) per rule 2. No browser, no other route. |
| `govza_dismissal_code_2025` | HTTP 200, standard Drupal rules for `User-agent: *` | **Allowed.** The only disallows are Drupal machinery: `/core/`, `/profiles/`, `/admin/`, `/search/`, `/user/*`, `/node/add/`, `/comment/reply/`, `/media/oembed` and their `/index.php/` forms. Nothing covers `/sites/default/files/`. No Crawl-delay, no Content-Signal. | `status: verified_open`, `robots_ok: true`. Dry run, then the live run. |

The CCMA result matches the record for 2026-09-20 and earlier on 2026-09-23.
The Layer 1 CCMA material stays out of reach until the CCMA grants research
access. It is **not** to be collected through a browser, a different
user-agent or a mirror.

Rules and forms: even when the CCMA is reachable, the allow-list admits Rules
and forms PDFs only when they are linked from the information-sheets listing
or an `/advices/` page, or are added one by one as exact `urls:`. The CCMA's
own forms listing (`/advicecategories/ccma-referral-forms/`) is outside the
path allow-list and has not been added.

### Runs, 2026-09-23

| Run | Command | Requests made | Result |
|---|---|---|---|
| `20260923T023601Z-05c0` | `ccma harvest -s official_publication --dry-run --limit 10 --allow-network` | gov.za `/robots.txt` only | CCMA source skipped (disabled; no request). gov.za: robots allowed; would fetch the one PDF. Nothing saved. |
| `20260923T023650Z-76e5` | `ccma harvest -s official_publication --popia-ack --allow-network --limit 20` | gov.za `/robots.txt`, then the PDF | 1 found, 1 downloaded (1,962,751 bytes, sha256 `7e7db0ba…4993`), 1 seeded as `statute`. 0 failures. |
| `20260923T024631Z-713b` | `ccma seed -s govza_dismissal_code_2025 --popia-ack` | none | Re-seeded from the stored bytes once scan detection existed: `ocr_needed` (see below). |

**What the gov.za file is.** It is Government Gazette No. 53294 of 4 September
2025, 16 pages. The PDF's text layer holds **only running heads and footers**
(94 characters of anything else over 16 pages); the notice itself is a
scanned image. From the text layer alone, the file is verifiably GG 53294,
4 September 2025, Department of Employment and Labour, Notice 3470 of 2025.
Nothing in the text layer names the Code. The catalogue therefore records the
record as `ocr_needed`, with the GN and GG numbers confirmed, and the title
words and draft status explicitly **unverified**.

A one-off local OCR read (rapidocr, 220 dpi, not stored in the catalogue) gives
the notice as: *Labour Relations Act, 1995 — Code of Good Practice:
Dismissal*, "issued by the Minister of Employment and Labour after
consideration by NEDLAC in terms of section 203(2A)", taking effect on the
date of publication, and repealing Schedule 8 and the Code of Good Practice
on operational requirements (GN 1517, GG 20254, 16 July 1999). It is signed
by the Minister and dated 22 August 2025, and carries no draft or
public-comment wording. **This is a machine transcription**; confirm the
wording against the gazette image before relying on it. If confirmed, it
answers open questions Q4 and Q5: the final code is gazetted, in force from
4 September 2025, and the main corpus's draft is superseded.

## Unverified — do not harvest

* Any other "CCMA awards" URL. The engine only fetches hosts listed in
  `ccma_sources.yaml`, checked before each request and after redirects.

## What "all published CCMA awards" means here

Assumption A-ALL: the records published and downloadable on operator-approved
official sources at run time. Measured today, that set is **empty** for an
identified crawler, because both official award publishers refuse automated
access. Every run report states its denominator and the unobserved slices;
nothing claims completeness.
