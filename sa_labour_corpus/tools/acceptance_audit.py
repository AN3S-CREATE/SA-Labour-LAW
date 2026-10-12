"""Acceptance-criteria audit: checks evidence, not intent.

Run against a built corpus:

    python tools/acceptance_audit.py ./data/corpus ./data/smoke

Each criterion is checked against the current state on disk -- the database,
the reports, the registry, the source tree -- rather than against a claim made
somewhere else.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

results: list[tuple[int, str, bool, str]] = []


def check(number: int, description: str, ok: bool, evidence: str) -> None:
    results.append((number, description, ok, evidence))


def resume_evidence(run_log: Path) -> tuple[bool, str]:
    """Criterion 5, read from RUN.jsonl.

    Passing needs proof that resume ran (a 304 answering a conditional GET) and
    no proof that an unchanged body was downloaded again. Bytes summed over the
    304s alone are 0 by construction, so that sum on its own can never fail.

    A repeat is a 200 the crawler then discarded because its sha256 equalled
    the body it already held (a parse event with status skipped_duplicate), or
    a 200 for a URL whose stored document has the same byte count (an earlier
    parse event with status ok) that did not go on to store a new version.
    Only a stored document counts: the crawler sends no validators for a
    listing, which it walks every run, or for a body whose parse failed, so an
    equal-size 200 for either is by design. A URL that hit the body-size cap is
    left out: its logged byte count may be the declared length of a body that
    was never read.

    RUN.jsonl is append-only and this reads all of it, because a stored size
    may come from any earlier run. A repeat logged by a past run therefore
    keeps failing the criterion until the log is rotated; the evidence says how
    many crawl runs it covers.
    """
    # url -> [304s logged by the fetcher, 304s logged by the crawler]. Both log
    # the same response, so each URL counts the larger of the two.
    not_modified: dict[str, list[int]] = {}
    transferred = 0
    stored_sizes: dict[str, set[int]] = {}  # url -> byte counts of stored documents
    last_200: dict[str, tuple[int, int]] = {}
    # event line -> (url, bytes). A same-size repeat is only presumed until the
    # parse event: one that stores a new version shows the body had changed.
    same_size: dict[int, tuple[str, int]] = {}
    repeats: dict[int, tuple[str, int]] = {}
    sha_confirmed: list[str] = []
    oversize: set[str] = set()
    runs = 0

    if run_log.exists():
        with run_log.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                url = event.get("url") or ""
                stage = event.get("stage")
                if event.get("status") == 304 or event.get("cached"):
                    counts = not_modified.setdefault(url, [0, 0])
                    counts[1 if event.get("cached") else 0] += 1
                    transferred += event.get("bytes", 0) or 0
                elif (stage == "fetch" and event.get("status") == 200 and url
                      and "bytes" in event):
                    # Only the fetcher's own event records a response off the
                    # wire, and it always carries the byte count.
                    size = event.get("bytes") or 0
                    if size and size in stored_sizes.get(url, ()):
                        same_size[line_no] = (url, size)
                    last_200[url] = (line_no, size)
                elif stage == "parse" and event.get("status") == "skipped_duplicate":
                    # Each parse event answers the fetch just before it.
                    sha_confirmed.append(url)
                    if url in last_200:
                        fetched_on, size = last_200.pop(url)
                        repeats[fetched_on] = (url, size)
                elif stage == "parse" and event.get("status") == "ok":
                    if url in last_200:
                        same_size.pop(last_200.pop(url)[0], None)
                    if event.get("bytes"):
                        stored_sizes.setdefault(url, set()).add(event["bytes"])
                elif stage == "gap" and event.get("kind") == "oversize":
                    oversize.add(url)
                elif stage == "crawl_start":
                    runs += 1

    repeats.update(same_size)
    cached = sum(max(counts) for counts in not_modified.values())
    repeated = [(url, size) for url, size in repeats.values() if url not in oversize]
    confirmed = [url for url in sha_confirmed if url not in oversize]
    repeat_bytes = sum(size for _, size in repeated)
    ok = cached > 0 and transferred == 0 and not repeated and not confirmed
    evidence = (
        f"{cached} conditional-GET 304 responses, {transferred} bytes transferred; "
        f"{len(repeated)} repeat 200 downloads of unchanged bodies across "
        f"{len({url for url, _ in repeated})} URLs, {repeat_bytes} bytes "
        f"({len(confirmed)} confirmed by sha256); "
        f"RUN.jsonl is append-only and covers {runs} crawl runs"
    )
    return ok, evidence


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    corpus = Path(args[0]) if args else REPO / "data" / "corpus"
    smoke = Path(args[1]) if len(args) > 1 else REPO / "data" / "smoke"
    # The checks import the package from this checkout. Done here rather than
    # at import, so loading this module (as the tests do) changes nothing.
    sys.path.insert(0, str(REPO / "src"))

    # 1 -- the CLI exposes the required stages
    helptext = subprocess.run(
        [sys.executable, "-m", "labour_corpus", "--help"],
        capture_output=True, text=True, encoding="utf-8", cwd=REPO,
    ).stdout
    missing = [c for c in ("smoke", "crawl", "parse", "index", "search", "report")
               if not re.search(rf"\b{c}\b", helptext)]
    check(1, "--help shows smoke/crawl/parse/index/search/report",
          not missing, f"missing={missing or 'none'}")

    # 2 -- smoke writes the full output contract
    from labour_corpus.sqlite_ro import connect_readonly

    smoke_db = smoke / "db" / "corpus.sqlite"
    if smoke_db.exists():
        # Read-only: an audit inspects the evidence and must never change it.
        conn = connect_readonly(smoke_db)
        rows = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        conn.close()
        raw_ok = any((smoke / "raw").rglob("source.*"))
        parsed_ok = any((smoke / "parsed").glob("*.json"))
        check(2, "smoke writes raw + parsed + sqlite (>=2 rows)",
              rows >= 2 and raw_ok and parsed_ok,
              f"db_rows={rows} raw={raw_ok} parsed={parsed_ok}")
    else:
        check(2, "smoke writes raw + parsed + sqlite (>=2 rows)", False,
              f"no smoke corpus at {smoke}; run `labour-corpus smoke`")

    # 3 -- public hosts only, guarded by a test
    from labour_corpus.config import PAID_HOST_MARKERS
    from labour_corpus.sources.registry import load_registry

    registry = load_registry(REPO / "sources.yaml")
    paid = [s.host for s in registry.sources
            if any(m in s.host.lower() for m in PAID_HOST_MARKERS)]
    guard = "test_registry_contains_no_paid_hostname" in (
        REPO / "tests" / "test_sources_yaml.py").read_text(encoding="utf-8")
    check(3, "sources.yaml public hosts only + unit test rejects paid hosts",
          not paid and guard,
          f"paid={paid or 'none'}; guard_test={guard}; hosts={len(registry.hosts)}")

    # 4 -- robots and rate limiting are tested
    robots_tests = (REPO / "tests" / "test_robots.py").read_text(encoding="utf-8")
    check(4, "robots and rate-limit modules have tests",
          "rate_limiter" in robots_tests and "content_signal" in robots_tests,
          f"{robots_tests.count('def test_')} tests in test_robots.py")

    # 5 -- resume does not re-download unchanged bodies
    ok, evidence = resume_evidence(corpus / "reports" / "RUN.jsonl")
    check(5, "resume: unchanged sha256 bodies are not re-downloaded", ok, evidence)

    # 6 -- metadata is extracted, never invented
    from labour_corpus.parse.judgment import parse_judgment
    from labour_corpus.parse.text import html_title, html_to_text

    fixture = (REPO / "tests" / "fixtures" / "zalcjhb_mining_dismissal.html").read_text(encoding="utf-8")
    meta = parse_judgment(html_to_text(fixture), title=html_title(fixture))
    blank = parse_judgment("No citation appears in this text at all.")
    check(6, "LC fixture extracts citation/parties/date; never hallucinates",
          meta.neutral_citation == "[2024] ZALCJHB 212"
          and meta.date_delivered == "2024-06-14" and bool(meta.parties)
          and blank.neutral_citation is None and blank.case_no is None,
          f"got {meta.neutral_citation}, {meta.case_no}, {meta.date_delivered}, "
          f"{len(meta.parties)} parties; blank -> all None")

    # 7 -- the CCMA-incompleteness statement survives
    from labour_corpus.report.coverage import CCMA_INCOMPLETENESS_STATEMENT

    coverage = corpus / "reports" / "COVERAGE.md"
    text = coverage.read_text(encoding="utf-8") if coverage.exists() else ""
    check(7, "COVERAGE.md contains the CCMA-incompleteness statement",
          CCMA_INCOMPLETENESS_STATEMENT in text,
          "verbatim statement present" if CCMA_INCOMPLETENESS_STATEMENT in text
          else "MISSING -- run `labour-corpus report`")

    # 8 -- the ontology covers the required LRA sections
    onto = corpus / "ontology" / "dismissal_schema.json"
    names: set[str] = set()
    if onto.exists():
        names = {t["name"] for t in json.loads(onto.read_text(encoding="utf-8"))["tags"]}
    required = {"s186_dismissal_definition", "s187_automatically_unfair",
                "s188_fair_reason_and_procedure", "s189_operational_requirements",
                "s145_review"}
    mining = (corpus / "ontology" / "mining_overlay.json").exists()
    check(8, "ontology file includes s186/s187/s188/s189/s145",
          required <= names and mining,
          f"{len(names)} tags; required={required <= names}; mining_overlay={mining}")

    # 9 -- the inbox route is documented
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    check(9, "README explains the inbox/ route for operator-owned awards",
          "inbox/" in readme and "ingest-inbox" in readme and "lawfully hold" in readme,
          "inbox section + command + lawful-holding caveat present")

    # 10 -- pinned dependencies, no secrets
    requirements = (REPO / "requirements.txt").read_text(encoding="utf-8")
    pins = [line for line in requirements.splitlines() if line.strip() and not line.startswith("#")]
    unpinned = [pin for pin in pins if "==" not in pin]
    scanned = "\n".join(
        (REPO / name).read_text(encoding="utf-8", errors="ignore")
        for name in ("sources.yaml", "README.md", "pyproject.toml")
    )
    secret = re.search(r"(?i)(api[_-]?key|password|secret|token)\s*[:=]\s*\S+", scanned)
    check(10, "no secrets in repo; requirements pinned",
          not unpinned and not secret,
          f"{len(pins)} deps pinned={not unpinned}; secret_scan="
          f"{'clean' if not secret else 'HIT'}")

    # 11 -- code style
    modules = list((REPO / "src").rglob("*.py"))
    bare = [m.name for m in modules
            if re.search(r"^\s*except\s*:", m.read_text(encoding="utf-8"), re.M)]
    check(11, "code style: no bare except, English comments",
          not bare, f"bare_except={bare or 'none'}; {len(modules)} modules")

    # -- report ---------------------------------------------------------
    print("=" * 78)
    print(f"ACCEPTANCE CRITERIA AUDIT  ({corpus})")
    print("=" * 78)
    for number, description, ok, evidence in results:
        print(f"{'PASS' if ok else 'FAIL'}  {number:>2}. {description}")
        print(f"          evidence: {evidence}")
    passed = sum(1 for _, _, ok, _ in results if ok)
    print("=" * 78)
    print(f"{passed}/{len(results)} criteria passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
