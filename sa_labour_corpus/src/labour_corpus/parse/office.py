"""Format sniffing and legacy Word (.doc) text extraction.

Two lessons from real government document libraries are encoded here.

First, **neither the Content-Type header nor the URL extension can be trusted**.
labour.gov.za serves Word 97-2003 files as ``application/msword`` from URLs
ending ``.docx``; opening those with a .docx reader raises ``BadZipFile``. The
bytes are the only reliable evidence, so format is decided by magic number.

Second, a file this project cannot read is not automatically a scan. A legacy
``.doc`` needs a Word reader, not OCR, and calling it ``ocr_needed`` sends the
operator to the wrong tool.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

ANTIWORD_TIMEOUT = 60


class SniffedFormat(str, Enum):
    OLE2_DOC = "ole2_doc"          # Word 97-2003
    OOXML = "ooxml"                # .docx / .xlsx / .pptx (a zip)
    PDF = "pdf"
    RTF = "rtf"
    HTML = "html"
    XML = "xml"
    PLAIN = "plain"
    UNKNOWN = "unknown"


def sniff_format(body: bytes) -> SniffedFormat:
    """Identify a payload by its magic number, ignoring headers and extensions."""
    if not body:
        return SniffedFormat.UNKNOWN

    head = body[:512]
    if head[:4] == b"\xd0\xcf\x11\xe0":
        return SniffedFormat.OLE2_DOC
    if head[:2] == b"PK":
        return SniffedFormat.OOXML
    if head[:4] == b"%PDF":
        return SniffedFormat.PDF
    if head[:5] == b"{\\rtf" or head[:4] == b"{\\rt":
        return SniffedFormat.RTF

    lowered = head.lstrip().lower()
    if lowered.startswith((b"<!doctype html", b"<html")):
        return SniffedFormat.HTML
    if lowered.startswith(b"<?xml"):
        # An XHTML document is still HTML for extraction purposes.
        return SniffedFormat.HTML if b"<html" in lowered[:400] else SniffedFormat.XML
    if b"<html" in lowered[:400] or b"<body" in lowered[:400]:
        return SniffedFormat.HTML

    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return SniffedFormat.UNKNOWN
    return SniffedFormat.PLAIN


# -- legacy Word ------------------------------------------------------------
def antiword_path() -> str | None:
    """Locate antiword, the Word 97-2003 text extractor, if it is installed."""
    return shutil.which("antiword")


def antiword_available() -> bool:
    return antiword_path() is not None


@dataclass(slots=True)
class DocExtract:
    text: str = ""
    ok: bool = False
    note: str = ""


def _run_antiword(tool: str, body: bytes, directory: Path) -> subprocess.CompletedProcess[bytes]:
    """Write the body into ``directory``, run antiword on it, remove the copy."""
    source = directory / "document.doc"
    try:
        source.write_bytes(body)
        return subprocess.run(  # noqa: S603 - fixed binary, no shell
            [tool, "-w", "0", str(source)],
            capture_output=True,
            timeout=ANTIWORD_TIMEOUT,
            check=False,
        )
    finally:
        try:
            source.unlink(missing_ok=True)
        except OSError:
            pass


def _without_path(note: str, given: Path | None) -> str:
    """Replace the temporary copy's path in a note with its fixed basename."""
    if given is None:
        return note
    # Also the doubled-backslash form, as it appears inside a repr'd argv.
    for form in (str(given), str(given).replace("\\", "\\\\"), given.as_posix()):
        note = note.replace(form, given.name)
    return note


def extract_doc_text(body: bytes, tmp_dir=None) -> DocExtract:
    """Extract text from a Word 97-2003 document using antiword.

    antiword is optional. When it is absent the caller is told so plainly
    rather than being handed empty text that looks like a successful parse.
    """
    tool = antiword_path()
    if tool is None:
        return DocExtract(
            ok=False,
            note=(
                "legacy Word 97-2003 document; antiword is not installed so no "
                "text was extracted. Install antiword (or LibreOffice) and "
                "re-run parse to recover it."
            ),
        )

    # antiword and subprocess both name the file they were given, which sits in
    # a random temporary directory. A note that changes on every run makes
    # identical bytes look re-parsed (the CCMA re-seed compares notes), so the
    # path is replaced by its fixed basename in every note.
    given: Path | None = None
    try:
        if tmp_dir:
            directory = Path(tmp_dir)
            directory.mkdir(parents=True, exist_ok=True)
            given = directory / "document.doc"
            completed = _run_antiword(tool, body, directory)
        else:
            # A private directory per document, removed with everything in it.
            # mkdtemp alone left one empty labour_doc_* directory behind per
            # .doc parsed. ignore_cleanup_errors: on Windows antiword may still
            # hold a handle when the block exits.
            with tempfile.TemporaryDirectory(
                prefix="labour_doc_", ignore_cleanup_errors=True
            ) as name:
                given = Path(name) / "document.doc"
                completed = _run_antiword(tool, body, Path(name))
    except (OSError, subprocess.SubprocessError) as exc:
        return DocExtract(
            ok=False, note=_without_path(f"antiword failed: {type(exc).__name__}: {exc}", given))

    if completed.returncode != 0:
        detail = _without_path(
            completed.stderr.decode("utf-8", errors="ignore").strip(), given)[:200]
        return DocExtract(
            ok=False,
            note=f"antiword exited {completed.returncode}: {detail or 'no detail'}",
        )

    text = completed.stdout.decode("utf-8", errors="replace")
    if not text.strip():
        return DocExtract(ok=False, note="antiword returned no text")
    return DocExtract(text=text, ok=True, note="text extracted with antiword")
