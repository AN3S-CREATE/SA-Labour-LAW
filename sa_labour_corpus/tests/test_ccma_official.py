"""Layer 1 official publications (``kind: official_publication``). Fixtures only; no network.

The operator's rules, each tested here:

1. robots/ToS notes on file before any fetch;
2. a robots refusal refuses the whole source, with no browser fallback;
3. plain GET of allow-listed URLs, confined to allow-listed paths;
4. nothing off the allow-list is requested (no login, cmsonline or SAFLII);
5. --limit, 2 workers, 1 s / 4 s / 16 s backoff, checksum + quarantine;
6. records are practice_note / statute, never awards;
7. a dry run lists without fetching records.

Every page and PDF below is a FICTIONAL fixture shaped like the real sites.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from labour_corpus.ccma.allowlist import (
    Confirmation,
    RecordKind,
    SourceKind,
    UrlRejected,
    load_allowlist,
    path_allowed,
    validate_url,
)
from labour_corpus.ccma.catalogue import Catalogue
from labour_corpus.ccma.cli import ccma_app
from labour_corpus.ccma.extract import parse_artifact
from labour_corpus.ccma.find import official_native_id, parse_official_listing
from labour_corpus.ccma.harvest import DEFAULT_CONCURRENCY, RETRY_WAITS, HarvestOptions, Harvester
from labour_corpus.ccma.paths import CcmaPaths
from labour_corpus.ccma.query import search
from labour_corpus.ccma.scrape import FixtureGetter, LiveGetter
from labour_corpus.config import ConfigError
from labour_corpus.http import Fetcher, RateLimiter

CCMA = "https://www.ccma.org.za"
LISTING = f"{CCMA}/advicecategories/information-sheets/"
PAGE2 = f"{LISTING}page/2/"
ADVICE_CONARB = f"{CCMA}/advices/con-arb-explained/"
ADVICE_WEB = f"{CCMA}/advices/guidance-web-only/"
PDF_DISMISSAL = f"{CCMA}/wp-content/uploads/2024/05/Info-Sheet-Dismissal.pdf"
PDF_CONARB = f"{CCMA}/wp-content/uploads/2023/01/Con-Arb.pdf"
PDF_RETRENCH = f"{CCMA}/wp-content/uploads/2022/11/Info-Sheet-Retrenchment.pdf"
GOVZA_PDF = "https://www.gov.za/sites/default/files/gcis_document/202509/53294gen3470.pdf"
GOVZA_TITLE = "Code of Good Practice Dismissal 2025 (GN 3470 / GG 53294)"

OFF_LIST = (
    f"{CCMA}/wp-login.php", f"{CCMA}/wp-admin/", "https://cmsonline.ccma.org.za/",
    "https://www.saflii.org/za/cases/ZACCMA/", f"{CCMA}/ccma-resources/rules.pdf",
    "https://evil.example.com/x.pdf", f"{CCMA}/wp-content/uploads/2024/05/form.docx",
)

OFFICIAL_YAML = f"""
version: 1
sources:
  - id: ccma_official_publications
    name: CCMA official publications (fixture)
    kind: official_publication
    record_kind: practice_note
    harvest_mode: allowlisted_http
    base_url: {LISTING}
    hosts: [www.ccma.org.za]
    allowed_paths: [/advicecategories/information-sheets/, /advices/, /wp-content/uploads/]
    listing_urls: [{LISTING}]
    detail_paths: [/advices/]
    link_patterns: ['^/wp-content/uploads/.+\\.pdf$']
    status: verified_open
    verified_on: 2026-09-23
    operator_confirmed: pending_robots_check
    enabled: true
    min_delay: 0
    max_delay: 0
    tos_notes: fixture; stands in for a source whose robots were checked
  - id: govza_dismissal_code_2025
    name: {GOVZA_TITLE}
    kind: official_publication
    record_kind: statute
    base_url: {GOVZA_PDF}
    hosts: [www.gov.za]
    allowed_paths: [/sites/default/files/gcis_document/202509/53294gen3470.pdf]
    urls: [{GOVZA_PDF}]
    title: {GOVZA_TITLE}
    status: verified_open
    verified_on: 2026-09-23
    operator_confirmed: pending_robots_check
    enabled: true
    min_delay: 0
    max_delay: 0
    tos_notes: fixture; gazette notice
  - id: saflii_zaccma
    name: SAFLII ZACCMA (blocked)
    kind: saflii_year_index
    record_kind: award
    base_url: https://www.saflii.org/za/cases/ZACCMA/
    hosts: [www.saflii.org]
    status: blocked_bot_management
    enabled: true
    tos_notes: 403 challenge
  - id: ccma_cmsonline
    name: CCMA Online (not a publication)
    kind: link_index
    record_kind: award
    base_url: https://cmsonline.ccma.org.za/
    hosts: [cmsonline.ccma.org.za]
    status: dropped_not_a_publication
    enabled: false
"""

SOURCE_MAP = """# CCMA source map (fixture)
### `ccma_official_publications`
robots/ToS notes written before any fetch.
### `govza_dismissal_code_2025`
robots/ToS notes written before any fetch.
"""

LISTING_HTML = """<html><head><title>Information Sheets - CCMA (fictional fixture)</title></head><body>
<nav><a href="/wp-login.php">Log in</a> <a href="/wp-admin/">Admin</a>
<a href="https://cmsonline.ccma.org.za/">CCMA Online</a>
<a href="https://www.saflii.org/za/cases/ZACCMA/">Published awards</a></nav>
<main>
<a href="/wp-content/uploads/2024/05/Info-Sheet-Dismissal.pdf">Information Sheet: Dismissal</a>
<a href="/advices/con-arb-explained/">Con-Arb explained</a>
<a href="/advices/guidance-web-only/">Web-only guidance</a>
<a href="/ccma-resources/rules.pdf">Rules (outside the path allow-list)</a>
<a href="/advices/../wp-login.php">Dot-segment escape</a>
<a href="https://evil.example.com/x.pdf">Elsewhere</a>
<a href="/wp-content/uploads/2024/05/form.docx">A Word form</a>
<a href="/advicecategories/information-sheets/page/2/">Next</a>
</main></body></html>"""

PAGE2_HTML = """<html><body><main>
<a href="/advicecategories/information-sheets/">Previous</a>
<a href="/wp-content/uploads/2022/11/Info-Sheet-Retrenchment.pdf">Information Sheet: Retrenchment</a>
</main></body></html>"""

ADVICE_CONARB_HTML = """<html><head><title>Con-Arb explained - CCMA</title></head><body><main>
<p>Con-arb combines conciliation and arbitration in one sitting.</p>
<a href="/wp-content/uploads/2023/01/Con-Arb.pdf">Download the Con-Arb information sheet</a>
<a href="/wp-content/uploads/2024/05/Info-Sheet-Dismissal.pdf">Dismissal sheet (listed already)</a>
</main></body></html>"""

ADVICE_WEB_HTML = """<html><head><title>Web-only guidance - CCMA (fictional fixture)</title></head>
<body><main><h1>What happens at an arbitration</h1>
<p>An employee who believes a dismissal was unfair may refer the dispute to the CCMA within
30 days. In the fictional matter Mokoena v Acme [2019] ZALAC 12 (JR 1487/21) the court
explained the test, and the commissioner found that the dismissal was fair.</p>
</main></body></html>"""


def text_pdf(lines: list[str]) -> bytes:
    """A minimal one-page PDF whose text layer holds ``lines`` (Helvetica)."""
    return paged_pdf([lines])


def paged_pdf(pages: list[list[str]]) -> bytes:
    """A minimal PDF with one page per entry, each page's text layer holding its lines."""

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    count = len(pages)
    font = 3 + 2 * count                      # objects: catalog, pages, (page, content)*, font
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + b" ".join(f"{3 + 2 * i} 0 R".encode() for i in range(count))
        + b"] /Count " + str(count).encode() + b" >>",
    ]
    for i, lines in enumerate(pages):
        ops = ["BT", "/F1 11 Tf", "14 TL", "72 780 Td"]
        ops += [f"({esc(line)}) Tj T*" for line in lines]
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << "
            f"/F1 {font} 0 R >> >> /Contents {4 + 2 * i} 0 R >>".encode())
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                       + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
              f"startxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


STATUTE_LINES = [
    "FICTIONAL FIXTURE shaped like a Government Gazette notice",
    "GOVERNMENT GAZETTE Vol. 723 4 September 2025 No. 53294",
    "DEPARTMENT OF EMPLOYMENT AND LABOUR",
    "NO. 3470 4 September 2025",
    "LABOUR RELATIONS ACT, 1995",
    "CODE OF GOOD PRACTICE: DISMISSAL",
    "I hereby issue the Code of Good Practice: Dismissal, after the draft was published.",
    "A dismissal is fair if it is effected for a fair reason and in accordance with a fair",
    "procedure. The dismissal was fair where the employer followed the procedure.",
]


# -- helpers -------------------------------------------------------------------

class OrderedGetter(FixtureGetter):
    """A FixtureGetter that logs robots checks and page requests in one sequence."""

    def __init__(self, mapping: dict[str, Path], robots_refusals: dict[str, str] | None = None):
        super().__init__(mapping, robots_refusals)
        self.events: list[tuple[str, str]] = []

    def get(self, url: str):
        self.events.append(("get", url))
        return super().get(url)

    def check_robots(self, url: str):
        self.events.append(("robots", url))
        return super().check_robots(url)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "ccma_sources.yaml").write_text(OFFICIAL_YAML, encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ccma-source-map.md").write_text(SOURCE_MAP, encoding="utf-8")
    return tmp_path


@pytest.fixture
def pages(tmp_path: Path) -> dict[str, Path]:
    folder = tmp_path / "pages"
    folder.mkdir()
    content = {
        LISTING: ("listing.html", LISTING_HTML.encode()),
        PAGE2: ("page2.html", PAGE2_HTML.encode()),
        ADVICE_CONARB: ("conarb.html", ADVICE_CONARB_HTML.encode()),
        ADVICE_WEB: ("web.html", ADVICE_WEB_HTML.encode()),
        PDF_DISMISSAL: ("dismissal.pdf", text_pdf([
            "CCMA Information Sheet: Dismissal (FICTIONAL FIXTURE)",
            "Refer an unfair dismissal dispute within 30 days. Compare [2020] ZALAC 5."])),
        PDF_CONARB: ("conarb.pdf", text_pdf(["CCMA Information Sheet: Con-Arb (FICTIONAL FIXTURE)"])),
        PDF_RETRENCH: ("retrench.pdf", text_pdf([
            "CCMA Information Sheet: Retrenchment (FICTIONAL FIXTURE)",
            "Section 189 of the Labour Relations Act requires consultation."])),
        GOVZA_PDF: ("gazette.pdf", text_pdf(STATUTE_LINES)),
    }
    mapping = {}
    for url, (name, body) in content.items():
        path = folder / name
        path.write_bytes(body)
        mapping[url] = path
    return mapping


def run(root: Path, getter: FixtureGetter, **kw):
    allowlist = load_allowlist(root / "ccma_sources.yaml")
    options = HarvestOptions(root=root, source_ids=kw.pop("source_ids", ["official_publication"]),
                             getter_factory=lambda source: getter, **kw)
    return Harvester(allowlist, options, out=lambda line: None).run()


def live(root: Path, getter: FixtureGetter, **kw):
    return run(root, getter, allow_network=True, popia_ack=True, **kw)


def by_id(result, source_id: str):
    return next(s for s in result.sources if s.source_id == source_id)


def raw_files(root: Path) -> list[Path]:
    raw = CcmaPaths(root).raw
    return [p for p in raw.rglob("*") if p.is_file()] if raw.exists() else []


def entry(**overrides) -> str:
    """One official-publication YAML entry, with overrides, as a whole allow-list."""
    fields = {
        "id": "x", "name": "x", "kind": "official_publication", "record_kind": "statute",
        "base_url": GOVZA_PDF, "hosts": "[www.gov.za]",
        "allowed_paths": "[/sites/default/files/gcis_document/202509/53294gen3470.pdf]",
        "urls": f"[{GOVZA_PDF}]", "status": "unverified", "enabled": "true",
        "tos_notes": "notes",
    }
    fields.update(overrides)
    body = "\n".join(f"    {k}: {v}" for k, v in fields.items() if v is not None)
    return f"version: 1\nsources:\n  - {body.lstrip()}\n"


# -- allow-list rules ----------------------------------------------------------------

@pytest.mark.parametrize("overrides, message", [
    ({"record_kind": "award"}, "never an award"),
    ({"record_kind": "practice_material"}, "never an award"),
    ({"allowed_paths": None}, "allowed_paths"),
    ({"urls": f"[{CCMA}/wp-content/uploads/x.pdf]", "hosts": "[www.gov.za, www.ccma.org.za]"},
     "path allow-list"),
    ({"urls": None}, "urls or listing_urls"),
    ({"harvest_mode": "browser"}, "harvest_mode"),
    ({"tos_notes": None}, "robots/ToS note"),
    ({"detail_paths": "[/wp-admin/]"}, "outside allowed_paths"),
    # A typo is an error, never a silent default (enabled, delays, confirmation).
    ({"enabled": None, "enable": "false"}, r"unknown key\(s\): enable$"),
    ({"min-delay": "8"}, r"unknown key\(s\): min-delay$"),
    ({"confirmation": "confirmed"}, r"unknown key\(s\): confirmation$"),
    ({"enabled": '"false"'}, "enabled: true or false"),
    ({"min_delay": "9", "max_delay": "8"}, "min_delay 9.0 above max_delay 8.0"),
])
def test_official_entry_rules_are_enforced_at_load(tmp_path: Path, overrides, message):
    path = tmp_path / "ccma_sources.yaml"
    path.write_text(entry(**overrides), encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_allowlist(path)


def test_an_unsupported_allowlist_version_is_refused(tmp_path: Path):
    path = tmp_path / "ccma_sources.yaml"
    path.write_text(entry().replace("version: 1", "version: 2"), encoding="utf-8")
    with pytest.raises(ConfigError, match="version 2 is not supported"):
        load_allowlist(path)


def test_pending_robots_check_is_a_conditional_confirmation(root: Path):
    registry = load_allowlist(root / "ccma_sources.yaml")
    src = registry.get("govza_dismissal_code_2025")
    assert src.confirmation is Confirmation.PENDING_ROBOTS_CHECK
    assert src.operator_confirmed and src.needs_robots_preflight
    assert not registry.get("saflii_zaccma").operator_confirmed
    src.operator_confirmed = False
    assert "confirmed" in src.live_refusal(allow_network=True, popia_ack=True, dry_run=False)


def test_source_type_selects_every_official_source_and_an_id_wins(root: Path):
    registry = load_allowlist(root / "ccma_sources.yaml")
    assert [s.id for s in registry.select(["official_publication"])] == [
        "ccma_official_publications", "govza_dismissal_code_2025"]
    assert [s.id for s in load_allowlist().select(["local_corpus"])] == ["local_corpus"]
    with pytest.raises(ConfigError):
        registry.select(["no_such_source"])


@pytest.mark.parametrize("path, allowed", [
    ("/advices/unfair-dismissal/", True),
    ("/wp-content/uploads/2024/05/Info%20Sheet.pdf", True),
    ("/sites/default/files/gcis_document/202509/53294gen3470.pdf", True),
    ("/sites/default/files/gcis_document/202509/53294gen3470.pdf.evil", False),
    ("/sites/default/files/gcis_document/202509/other.pdf", False),
    ("/wp-login.php", False),
    ("/advices/../wp-login.php", False),
    ("/advices/%2e%2e/wp-login.php", False),
    ("/advices/..%2fwp-login.php", False),
    ("/advices/\\..\\wp-login.php", False),
    ("/advicecategories/information-sheets", False),     # the directory itself needs its slash
])
def test_path_guard(path: str, allowed: bool):
    entries = ("/advices/", "/wp-content/uploads/", "/advicecategories/information-sheets/",
               "/sites/default/files/gcis_document/202509/53294gen3470.pdf")
    assert path_allowed(path, entries) is allowed


def test_url_guard_checks_paths_as_well_as_hosts():
    paths = ("/advices/",)
    assert validate_url(f"{CCMA}/advices/x/?page=2", frozenset({"www.ccma.org.za"}), paths)
    for url in (f"{CCMA}/wp-admin/", "https://cmsonline.ccma.org.za/advices/"):
        with pytest.raises(UrlRejected):
            validate_url(url, frozenset({"www.ccma.org.za"}), paths)


def test_plain_http_links_on_the_source_hosts_are_upgraded_not_dropped(root: Path):
    """The guard fetches https only, so an http:// link in a listing is rewritten to
    https before the path checks; listing recall must not drop."""
    src = load_allowlist(root / "ccma_sources.yaml").get("ccma_official_publications")
    html = """<main>
    <a href="http://www.ccma.org.za/wp-content/uploads/2024/05/Info-Sheet-Dismissal.pdf">Dismissal</a>
    <a href="http://www.ccma.org.za/advices/con-arb-explained/">Con-Arb</a>
    <a href="http://www.ccma.org.za/advicecategories/information-sheets/page/2/">Next</a>
    <a href="http://www.ccma.org.za/wp-login.php">Log in</a>
    <a href="http://evil.example.com/wp-content/uploads/x.pdf">Elsewhere</a>
    </main>"""
    documents, details, pages = parse_official_listing(
        html, LISTING, src, "/advicecategories/information-sheets/")
    assert documents == [(PDF_DISMISSAL, "Dismissal")]
    assert details == [(ADVICE_CONARB, "Con-Arb")]
    assert pages == [PAGE2]
    for url in (PDF_DISMISSAL, ADVICE_CONARB, PAGE2):
        validate_url(url, frozenset(src.hosts), src.allowed_paths)


def test_native_ids_are_stable_and_namespaced():
    entries = ("/advices/", "/wp-content/uploads/",
               "/sites/default/files/gcis_document/202509/53294gen3470.pdf")
    assert official_native_id("/wp-content/uploads/2024/05/Info-Sheet-Dismissal.pdf",
                              entries) == "uploads_2024_05_Info-Sheet-Dismissal"
    assert official_native_id("/advices/guidance-web-only/", entries) == "advices_guidance-web-only"
    assert official_native_id("/sites/default/files/gcis_document/202509/53294gen3470.pdf",
                              entries) == "53294gen3470"


# -- rule 1: notes before any fetch ---------------------------------------------------------

def test_missing_notes_refuse_the_source_before_any_request(root: Path, pages):
    (root / "docs" / "ccma-source-map.md").write_text("# no notes yet\n", encoding="utf-8")
    getter = OrderedGetter(pages)
    result = live(root, getter)
    assert getter.events == []                      # not even robots.txt was read
    for s in result.sources:
        assert not s.attempted and "robots/ToS notes" in s.refusal
    assert raw_files(root) == []


def test_shipped_official_sources_have_notes_on_file():
    registry = load_allowlist()
    official = registry.select(["official_publication"])
    assert {s.id for s in official} == {"ccma_official_publications", "govza_dismissal_code_2025"}
    for s in official:
        assert registry.notes_refusal(s) is None
        assert s.record_kind is not RecordKind.AWARD
    # Out of scope and never live: SAFLII stays blocked, CCMA Online stays dropped.
    assert registry.get("ccma_cmsonline").enabled is False
    assert registry.get("saflii_zaccma").kind is not SourceKind.OFFICIAL_PUBLICATION


# -- rule 2: robots refusal refuses the source ---------------------------------------------

def test_robots_refusal_refuses_the_whole_source_and_fetches_nothing_from_it(root: Path, pages):
    getter = OrderedGetter(pages, robots_refusals={
        f"{CCMA}/advices/": "ChallengeDetected: http_403_bot_refusal"})
    result = live(root, getter)
    ccma = by_id(result, "ccma_official_publications")
    assert not ccma.attempted and "robots preflight refused 1 of 3" in ccma.refusal
    assert not [u for kind, u in getter.events if kind == "get" and "ccma.org.za" in u]
    govza = by_id(result, "govza_dismissal_code_2025")          # the other source continues
    assert govza.attempted and govza.seeded == 1
    with Catalogue(CcmaPaths(root).catalogue) as cat:
        rows = [e for e in cat.errors() if e["source"] == "ccma_official_publications"]
        assert [e["class"] for e in rows] == ["policy_block"] and rows[0]["retryable"] == 0
    report = result.report_path.read_text(encoding="utf-8")
    assert "## Robots preflight" in report and "REFUSED" in report
    assert "Set `enabled: false`" in report


def test_robots_is_checked_for_every_allowed_path_before_the_first_request(root: Path, pages):
    getter = OrderedGetter(pages)
    live(root, getter, source_ids=["ccma_official_publications"])
    robots = [i for i, (kind, _) in enumerate(getter.events) if kind == "robots"]
    gets = [i for i, (kind, _) in enumerate(getter.events) if kind == "get"]
    assert len(robots) == 3 and max(robots) < min(gets)
    assert [u for kind, u in getter.events if kind == "robots"] == [
        LISTING, f"{CCMA}/advices/", f"{CCMA}/wp-content/uploads/"]


# -- rules 3, 4 and 7: allow-listed GETs only; dry run lists without fetching records --------

def test_dry_run_limit_10_lists_records_without_fetching_them(root: Path, pages):
    getter = OrderedGetter(pages)
    result = run(root, getter, dry_run=True, allow_network=True, limit=10)
    assert result.exit_code == 0
    govza = by_id(result, "govza_dismissal_code_2025")
    assert govza.would_fetch == [GOVZA_PDF]
    ccma = by_id(result, "ccma_official_publications")
    assert set(ccma.would_fetch) == {PDF_DISMISSAL, PDF_CONARB, ADVICE_WEB, PDF_RETRENCH}
    requested = [u for kind, u in getter.events if kind == "get"]
    assert not {GOVZA_PDF, PDF_DISMISSAL, PDF_CONARB, PDF_RETRENCH} & set(requested)
    assert raw_files(root) == []
    with Catalogue(CcmaPaths(root).catalogue) as cat:
        assert cat.count("records") == 0 and cat.count("discovered") == 0
    assert "Would fetch or seed" in result.report_path.read_text(encoding="utf-8")


def test_nothing_off_the_allow_list_is_ever_requested(root: Path, pages):
    getter = OrderedGetter(pages)
    live(root, getter, limit=20)
    requested = {u for kind, u in getter.events if kind == "get"}
    assert requested and not requested & set(OFF_LIST)
    registry = load_allowlist(root / "ccma_sources.yaml")
    for url in requested:
        src = next(s for s in registry.select(["official_publication"]) if url.split("/")[2] in s.hosts)
        validate_url(url, frozenset(src.hosts), src.allowed_paths)


def test_limit_caps_records_across_listing_and_detail_pages(root: Path, pages):
    result = live(root, OrderedGetter(pages), limit=2, source_ids=["ccma_official_publications"])
    assert by_id(result, "ccma_official_publications").found == 2


# -- rule 6: practice notes and statutes, never awards ---------------------------------------

def test_live_run_seeds_practice_notes_and_a_statute_never_awards(root: Path, pages):
    result = live(root, OrderedGetter(pages), limit=20)
    assert result.exit_code == 0
    with Catalogue(CcmaPaths(root).catalogue) as cat:
        rows = {r["native_id"]: r for r in cat.conn.execute("SELECT * FROM records")}
        assert {r["record_kind"] for r in rows.values()} == {"practice_note", "statute"}
        assert cat.count("knowledge_units") == 0              # they decide nothing
        statute = rows["53294gen3470"]
        assert statute["record_kind"] == "statute" and statute["parse_status"] == "parsed"
        assert statute["title"] == GOVZA_TITLE and statute["provenance"] == "crawled"
        notes = json.loads(statute["parse_notes"])
        assert "GN 3470 from the title occurs in the document text" in notes
        assert "GG 53294 from the title occurs in the document text" in notes
        assert any(n.startswith("draft check passed") for n in notes)
        assert statute["outcome_label"] == "not_determined"   # "the dismissal was fair" is not a finding
        web = rows["advices_guidance-web-only"]
        # The page cites [2019] ZALAC 12 and JR 1487/21; a mention is not an identity.
        assert web["case_number"] is None and web["neutral_citation"] is None
        assert web["outcome_label"] == "not_determined" and web["dispute_type"] == "unclassified"
        assert rows["uploads_2024_05_Info-Sheet-Dismissal"]["title"] == "Information Sheet: Dismissal"
    held = CcmaPaths(root).record_dir("govza_dismissal_code_2025", "53294gen3470") / "original.pdf"
    assert held.exists() and held.with_name("original.pdf.sha256").exists()
    conn = sqlite3.connect(CcmaPaths(root).catalogue)
    conn.row_factory = sqlite3.Row
    hits = search(conn, "fair reason procedure")
    assert hits["count"] >= 1 and hits["results"][0]["record_kind"] == "statute"
    conn.close()
    report = result.report_path.read_text(encoding="utf-8")
    assert "Statutes held: draft status and number checks" in report


def test_rerun_is_idempotent_and_changed_bytes_are_quarantined(root: Path, pages, tmp_path: Path):
    live(root, OrderedGetter(pages))
    changed = tmp_path / "gazette-changed.pdf"
    changed.write_bytes(text_pdf(STATUTE_LINES + ["An amendment line."]))
    second = live(root, OrderedGetter({**pages, GOVZA_PDF: changed}))
    govza = by_id(second, "govza_dismissal_code_2025")
    assert govza.quarantined == 1 and govza.downloaded == 0
    held = CcmaPaths(root).record_dir("govza_dismissal_code_2025", "53294gen3470")
    assert (held / "original.pdf").read_bytes() == pages[GOVZA_PDF].read_bytes()
    assert len(list(held.glob("quarantine/*.pdf"))) == 1
    ccma = by_id(second, "ccma_official_publications")
    assert ccma.downloaded == 0 and ccma.skipped == ccma.found   # unchanged bytes: nothing redone


def test_scanned_gazette_with_only_running_heads_is_ocr_needed_not_parsed():
    """Shaped like the real GG 53294 file: 16 pages, text layer = page furniture only."""
    pages = [["This gazette is also available free online at www.gpwonline.co.za",
              f"STAATSKOERANT, 4 SEPTEMBER 2025 No. 53294 {n}" if n % 2 else
              f"{n} No. 53294 GOVERNMENT GAZETTE, 4 SEPTEMBER 2025"] for n in range(3, 19)]
    pages[0] += ["DEPARTMENT OF EMPLOYMENT AND LABOUR", "NOTICE 3470 OF 2025"]
    parsed = parse_artifact(paged_pdf(pages), "application/pdf", GOVZA_PDF, RecordKind.STATUTE,
                            title_hint=GOVZA_TITLE)
    assert parsed.parse_status == "ocr_needed"
    assert any(n.startswith("OCR NEEDED: the PDF text layer holds only running heads") for n in parsed.notes)
    # The numbers are in the running heads, so they check out; the title words do not.
    assert "GN 3470 from the title occurs in the document text" in parsed.notes
    assert "GG 53294 from the title occurs in the document text" in parsed.notes
    assert any(n.startswith("UNVERIFIED: title words not found") and "dismissal" in n
               for n in parsed.notes)
    # A draft check over running heads must never read as passed.
    assert any(n.startswith("draft check NOT possible") for n in parsed.notes)
    assert not any(n.startswith("draft check passed") for n in parsed.notes)


def test_multi_page_pdf_with_real_body_text_stays_parsed():
    bodies = (
        "An employee who was dismissed may refer the dispute to the CCMA within thirty days.",
        "At conciliation a commissioner tries to help the parties settle the dispute by agreement.",
        "If conciliation fails, the dispute may go to arbitration, where a binding award is made.",
    )
    pages = [[f"CCMA Information Sheet page {n}", body] for n, body in enumerate(bodies, start=1)]
    parsed = parse_artifact(paged_pdf(pages), "application/pdf", PDF_DISMISSAL,
                            RecordKind.PRACTICE_NOTE)
    assert parsed.parse_status == "parsed"
    assert not any(n.startswith("OCR NEEDED") for n in parsed.notes)


def test_statute_numbers_absent_from_the_text_are_flagged_and_drafts_named():
    pdf = text_pdf(["DRAFT CODE OF GOOD PRACTICE ON DISMISSAL", "Published for public comment.",
                    "Interested parties may submit written comments within 30 days of this notice.",
                    "The draft sets out guidelines on dismissal for misconduct and incapacity."])
    parsed = parse_artifact(pdf, "application/pdf", GOVZA_PDF, RecordKind.STATUTE,
                            title_hint="Some Code (GN 9999 / GG 11111)")
    assert any(n.startswith("UNVERIFIED: GN 9999") for n in parsed.notes)
    assert any(n.startswith("UNVERIFIED: GG 11111") for n in parsed.notes)
    assert any(n.startswith("DRAFT: not in force") for n in parsed.notes)


# -- rule 5: politeness constants -------------------------------------------------------------

def test_concurrency_and_backoff_defaults():
    assert DEFAULT_CONCURRENCY == 2
    assert RETRY_WAITS == (1.0, 4.0, 16.0)


# -- the live client: robots verdicts and the path guard after redirects ------------------

def _client(handler) -> tuple[Fetcher, list[str]]:
    seen: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record), follow_redirects=True)
    return Fetcher("Mozilla/5.0 (compatible; test)", client=client, limiter=RateLimiter(0, 0)), seen


@pytest.mark.parametrize("robots, allowed, reason", [
    (httpx.Response(403, text="<html>Forbidden</html>"), False, "http_403_bot_refusal"),
    (httpx.Response(200, text="User-agent: *\nDisallow: /sites/\n"), False, "RobotsDisallowed"),
    (httpx.Response(200, text="User-agent: *\nContent-Signal: ai-input=no\n"), False, "ai-input=no"),
    (httpx.Response(404), True, "no robots rules published"),
    (httpx.Response(200, text="User-agent: *\nDisallow: /admin/\n"), True, "permits this path"),
])
def test_check_robots_reads_only_robots_txt(root: Path, robots, allowed, reason):
    fetcher, seen = _client(lambda request: robots if request.url.path == "/robots.txt"
                            else httpx.Response(200, content=b"%PDF-1.4"))
    src = load_allowlist(root / "ccma_sources.yaml").get("govza_dismissal_code_2025")
    verdict = LiveGetter(fetcher, src).check_robots(GOVZA_PDF)
    assert verdict.allowed is allowed and reason in verdict.reason
    assert seen == ["/robots.txt"]


def test_redirect_off_the_path_allow_list_is_refused(root: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path.endswith("53294gen3470.pdf"):
            return httpx.Response(302, headers={"location": "https://www.gov.za/other/elsewhere.pdf"})
        return httpx.Response(200, content=b"%PDF-1.4")

    fetcher, _ = _client(handler)
    src = load_allowlist(root / "ccma_sources.yaml").get("govza_dismissal_code_2025")
    with pytest.raises(UrlRejected, match="path allow-list"):
        LiveGetter(fetcher, src).get(GOVZA_PDF)


def test_robots_command_needs_network_permission(root: Path):
    result = CliRunner().invoke(ccma_app, [
        "robots", "-s", "official_publication", "--allowlist", str(root / "ccma_sources.yaml")])
    assert result.exit_code == 3
    try:
        text = result.output + result.stderr
    except ValueError:            # older Click: stderr mixed into output
        text = result.output
    assert "--allow-network" in text


def test_cli_annotations_are_real_objects_and_every_command_builds():
    """ccma/cli.py has no `from __future__ import annotations`: Typer gets the
    annotation objects, and every command's signature evaluates at import.
    Building `ccma --help` builds every command, so it would fail on any one."""
    from labour_corpus.ccma import cli

    assert cli.sources_cmd.__annotations__["allowlist"] is Path
    assert cli.harvest_cmd.__annotations__["source"] == list[str]
    runner = CliRunner()
    assert runner.invoke(ccma_app, ["--help"]).exit_code == 0
    assert runner.invoke(ccma_app, ["harvest", "--help"]).exit_code == 0


def test_page_count_survives_any_pypdf_error(monkeypatch):
    """LimitReachedError is a PyPdfError but not a PdfReadError; it must not end a seed."""
    import pypdf
    from pypdf.errors import LimitReachedError

    from labour_corpus.ccma.extract import pdf_page_count

    body = paged_pdf([["Code of Good Practice"]])
    assert pdf_page_count(body) == 1

    def refuse(*args, **kwargs):
        raise LimitReachedError("decompression limit")

    monkeypatch.setattr(pypdf, "PdfReader", refuse)
    assert pdf_page_count(body) == 0
