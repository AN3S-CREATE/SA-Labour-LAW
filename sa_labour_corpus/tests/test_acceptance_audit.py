"""Acceptance-audit criterion 5: resume does not re-download unchanged bodies.

The audit checks evidence, not intent, so a criterion that cannot fail
certifies nothing. Summing bytes over 304 responses alone is 0 by
construction. The guarantees under test are that a 200 repeating the body of
a stored document fails the criterion and names the repeat bytes; that a
changed body, a body that was never read, and a refetch the crawler makes on
purpose (a listing, which it walks every run, or a body whose parse failed)
do not; and that each 304 is counted once although the fetcher and the
crawler both log it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

AUDIT = Path(__file__).resolve().parents[1] / "tools" / "acceptance_audit.py"


@pytest.fixture(scope="module")
def audit():
    spec = importlib.util.spec_from_file_location("acceptance_audit", AUDIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    path_before = list(sys.path)
    spec.loader.exec_module(module)
    # Loading the tool must not touch the test process; main() does the setup.
    assert sys.path == path_before
    return module


def _log(tmp_path: Path, *events: dict) -> Path:
    path = tmp_path / "RUN.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


def _ok_200(url: str, size: int) -> dict:
    """The fetcher's event for a response read off the wire."""
    return {"stage": "fetch", "url": url, "status": 200, "bytes": size, "ms": 5}


def _parse_ok(url: str, size: int) -> dict:
    """The crawler's event for a body it parsed and stored as a document."""
    return {
        "stage": "parse", "source_id": "s", "url": url, "doc_id": "d",
        "doc_type": "judgment", "status": "ok", "bytes": size,
    }


def _parse_failed(url: str) -> tuple[dict, dict]:
    """A body whose parse failed: a gap, and the error mirrored into RUN.jsonl."""
    return (
        {"stage": "gap", "source_id": "s", "kind": "parse_failed", "url": url, "detail": "x"},
        {"stage": "parse", "status": "error", "error": "ValueError: x", "source_id": "s",
         "url": url},
    )


def _304(url: str) -> tuple[dict, dict]:
    """One 304 as RUN.jsonl records it: once by the fetcher, once by the crawler."""
    return (
        {"stage": "fetch", "url": url, "status": 304, "bytes": 0, "ms": 5},
        {"stage": "fetch", "source_id": "s", "url": url, "status": 304, "cached": True},
    )


A = "https://example.org/a.pdf"
B = "https://example.org/listing"


def test_a_repeat_200_of_equal_size_fails_and_names_the_bytes(audit, tmp_path):
    log = _log(
        tmp_path, _ok_200(A, 24_500_000), _parse_ok(A, 24_500_000),
        *_304(B), _ok_200(A, 24_500_000),
    )
    ok, evidence = audit.resume_evidence(log)
    assert ok is False
    assert "1 repeat 200 downloads of unchanged bodies across 1 URLs, 24500000 bytes" in evidence


def test_a_listing_walked_again_while_documents_get_304s_passes(audit, tmp_path):
    # The crawler sends no validators for a listing: it is walked every run.
    log = _log(
        tmp_path,
        {"stage": "crawl_start", "sources": ["s"]},
        _ok_200(B, 4000), _ok_200(A, 1000), _parse_ok(A, 1000),
        {"stage": "crawl_start", "sources": ["s"]},
        _ok_200(B, 4000), *_304(A),
    )
    ok, evidence = audit.resume_evidence(log)
    assert ok is True
    assert "0 repeat 200 downloads" in evidence
    assert evidence.endswith("covers 2 crawl runs")


def test_a_body_whose_parse_failed_fetched_again_passes(audit, tmp_path):
    # Nothing was stored, so the crawler fetches it again without validators.
    log = _log(
        tmp_path, *_304(B),
        _ok_200(A, 1000), *_parse_failed(A),
        _ok_200(A, 1000), *_parse_failed(A),
        _ok_200(A, 1000), _parse_ok(A, 1000),
    )
    ok, evidence = audit.resume_evidence(log)
    assert ok is True
    assert "0 repeat 200 downloads" in evidence


def test_an_equal_size_body_stored_as_a_new_version_is_not_a_repeat(audit, tmp_path):
    # A parse that stores a document shows the sha256 differed after all.
    log = _log(
        tmp_path, _ok_200(A, 1000), _parse_ok(A, 1000),
        *_304(B), _ok_200(A, 1000), _parse_ok(A, 1000),
    )
    ok, _ = audit.resume_evidence(log)
    assert ok is True


def test_only_304s_on_rerun_passes_and_each_304_counts_once(audit, tmp_path):
    log = _log(tmp_path, _ok_200(A, 1000), *_304(B), *_304(A))
    ok, evidence = audit.resume_evidence(log)
    assert ok is True
    assert evidence.startswith("2 conditional-GET 304 responses, 0 bytes transferred")
    assert "0 repeat 200 downloads" in evidence


def test_a_body_discarded_as_a_sha256_duplicate_fails(audit, tmp_path):
    # The first download predates this log; the crawler's sha256 check is the
    # proof that this 200 brought back the body the corpus already held.
    log = _log(
        tmp_path, *_304(B), _ok_200(A, 500),
        {"stage": "parse", "source_id": "s", "url": A, "status": "skipped_duplicate"},
    )
    ok, evidence = audit.resume_evidence(log)
    assert ok is False
    assert "across 1 URLs, 500 bytes (1 confirmed by sha256)" in evidence


def test_a_changed_body_is_not_a_repeat(audit, tmp_path):
    log = _log(tmp_path, _ok_200(A, 1000), _parse_ok(A, 1000), *_304(B), _ok_200(A, 1200))
    ok, _ = audit.resume_evidence(log)
    assert ok is True


def test_a_body_over_the_size_cap_is_not_counted(audit, tmp_path):
    # An oversize 200 logs the declared length of a body that was never read.
    log = _log(
        tmp_path, *_304(B),
        _ok_200(A, 60_000_000),
        {"stage": "gap", "source_id": "s", "kind": "oversize", "url": A, "detail": "cap"},
        _ok_200(A, 60_000_000),
        {"stage": "parse", "source_id": "s", "url": A, "status": "skipped_duplicate"},
    )
    ok, _ = audit.resume_evidence(log)
    assert ok is True


def test_a_listing_reused_from_memory_is_not_a_download(audit, tmp_path):
    log = _log(
        tmp_path, _ok_200(B, 4000), *_304(A),
        {"stage": "fetch", "source_id": "t", "url": B, "status": 200, "reused_in_run": True},
    )
    ok, _ = audit.resume_evidence(log)
    assert ok is True


def test_bytes_on_a_304_fail(audit, tmp_path):
    log = _log(tmp_path, {"stage": "fetch", "url": A, "status": 304, "bytes": 10})
    ok, evidence = audit.resume_evidence(log)
    assert ok is False
    assert "10 bytes transferred" in evidence


@pytest.mark.parametrize("events", [(), ({"stage": "fetch", "url": A, "status": 200, "bytes": 9},)])
def test_resume_never_exercised_does_not_pass(audit, tmp_path, events):
    ok, evidence = audit.resume_evidence(_log(tmp_path, *events))
    assert ok is False
    assert evidence.startswith("0 conditional-GET 304 responses")


def test_a_missing_or_malformed_log_does_not_pass(audit, tmp_path):
    assert audit.resume_evidence(tmp_path / "absent.jsonl")[0] is False
    path = tmp_path / "RUN.jsonl"
    path.write_text("not json\n[1, 2]\n", encoding="utf-8")
    assert audit.resume_evidence(path)[0] is False
