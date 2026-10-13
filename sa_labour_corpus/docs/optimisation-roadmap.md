# Optimisation and improvement roadmap

Last reviewed: 2026-10-13

This roadmap turns the holistic review into an ordered implementation backlog.
It does not weaken robots, provenance, POPIA, legal-currency, or human-review
controls to gain speed.

| Category | What to change | Why it matters | Technical outline | Priority | Status |
|---|---|---|---|---|---|
| Performance | Add parser and knowledge version stamps plus `parse --changed-only` | A rule-only change currently invites a 397-document re-parse and may depend on optional native readers | Store parser/extractor versions per record; select only hash/version-stale documents; emit a before/after field manifest | Next Sprint | Planned |
| Performance | Preserve the Navigator statute cache across MCP calls | Re-reading and normalising held Acts dominates interactive latency | Keep one `CorpusSource`/`TextCache` per server and invalidate by file metadata | Do Now | Completed in `NavigatorServer` |
| Performance | Add mandatory-literal prefilters to tagger regexes | Hundreds of full-text scans dominate parse CPU | Derive safe literal prefixes, skip impossible regexes, and prove identical spans on the corpus | Next Sprint | Planned |
| Security | Revalidate cached AIA chains and narrow TLS repair triggers | A cached intermediate must not outlive current trust decisions | Re-run issuer/signature/root checks on cache hit; clear partial-chain flags unless required; add forged-cache tests | Next Sprint | Planned |
| Security | Keep browser automation absent | Browser fallback could bypass crawler policy and rate limits | Preserve compile/test checks that no Playwright/browser path is reintroduced | Do Now | Completed (D-030) |
| Security | Pin GitHub Actions to reviewed immutable SHAs | Floating action tags increase supply-chain exposure | Resolve the reviewed major tags to commit SHAs and retain version comments for updates | Next Sprint | Completed after hosted run 38428505021 |
| Bundle / Build | Keep core, OCR and contributor profiles separate | Most users do not need OCR models or development tools | Maintain `.[ocr]`, `.[test,lint]`, and the full combined profile; test each profile in CI periodically | Do Now | Completed |
| Bundle / Build | Benchmark pypdfium2 before any PDF-engine migration | Speed alone does not prove text/error parity | Compare every held PDF for text, page count, status, metadata and failure mode; retain pypdf unless parity is demonstrated | Backlog | Decision recorded (D-031) |
| Architecture | Add schema migrations and minimum-reader versions | Read-only consumers currently fail opaquely on stale SQLite schemas | Introduce `PRAGMA user_version`, ordered migrations and `open_ro(min_version)` diagnostics | Next Sprint | Planned |
| Architecture | Version CCMA extraction and knowledge units | Rule changes do not automatically rebuild unchanged records | Store extractor/knowledge versions and add `ccma seed --rebuild` | Next Sprint | Planned |
| Architecture | Guard every redirect hop | Automatic redirects can cross host/policy boundaries before validation | Follow at most five hops manually; apply allow-list, robots, paid-host and rate-limit checks per hop | Next Sprint | Planned |
| Code quality | Expand final-order outcome fixtures | Legal documents contain submissions, quotations, negation and substituted orders | Add fixtures for review dismissal, remittal, quoted authorities, contradictory orders and negated dismissal | Do Now | Core regression fixtures added; expansion planned |
| Code quality | Add a received-referral date if exact CCMA Rule 18 calculation is required | Certificate date alone cannot represent every earlier-of scenario | Extend `CaseFacts`, interview and golden cases only after practitioner approval of the input and wording | Next Sprint | Awaiting lawyer review |
| Cost | Send both compressed and uncompressed ETag variants where appropriate | `-gzip` ETags caused repeat downloads of large unchanged PDFs | Preserve both validator forms, verify one live 304, then extend resume tests | Next Sprint | Awaiting valid operator contact/live run |
| Cost | Add CCMA conditional requests and version-aware reseeding | Unchanged records are downloaded and parsed again | Persist ETag/Last-Modified, map 304 to unchanged, and rebuild only stale extractor versions | Backlog | Planned |

## Release gates

Before describing the Navigator as legally reviewed or the build as remotely
green, all of these must be evidenced:

1. **Pending:** a named qualified South African labour-law practitioner signs
   the 30-case review sheet, with corrections incorporated and re-tested.
2. **Completed 2026-10-13:** an identified `bc_nbcrfli` crawl using the
   operator-supplied contact recorded one genuine conditional 304, zero repeat
   200 downloads and zero transferred document bytes; acceptance is 11/11.
3. **Completed 2026-10-13:** the operator-confirmed GitHub remote contains the
   merged history and Python 3.11/3.13 run 38428505021 passed.

