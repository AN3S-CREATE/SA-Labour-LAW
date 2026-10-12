"""PDF text extraction.

A scanned PDF with no text layer is reported as ``ocr_needed``. The raw bytes
are still stored so the operator can OCR later. No text is ever fabricated for
a page that has none.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..models import TextStatus
from .text import classify_text, normalise

MAX_PAGES = 800

# pypdf emits a warning per embedded font it cannot fully decode, which on a
# government PDF archive is thousands of lines of noise about a condition we
# already handle. Real extraction failures are reported through PdfExtract.notes.
for _name in ("pypdf", "pypdf.generic", "pypdf._reader", "pypdf._cmap"):
    logging.getLogger(_name).setLevel(logging.ERROR)


@dataclass(slots=True)
class PdfExtract:
    text: str = ""
    pages: int = 0
    status: TextStatus = TextStatus.OK
    notes: list[str] = field(default_factory=list)


def extract_pdf_text(data: bytes) -> PdfExtract:
    """Extract text from PDF bytes, reporting scans honestly."""
    result = PdfExtract()
    if not data:
        result.status = TextStatus.EMPTY
        result.notes.append("empty pdf body")
        return result

    # Imported here rather than at module load: pypdf and the cryptography
    # providers it pulls in cost about a second, and most commands (search,
    # report, navigate) never read a PDF. Outside the try below, so the names
    # are bound when its except clauses are evaluated.
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    # PyPdfError, not PdfReadError: pypdf's LimitReachedError and ParseError
    # derive from it but not from PdfReadError, and one malformed PDF must not
    # end a whole parse run (D-008).
    try:
        import io

        reader = PdfReader(io.BytesIO(data), strict=False)
    except (PyPdfError, ValueError, OSError) as exc:
        result.status = TextStatus.EMPTY
        result.notes.append(f"pdf could not be opened: {exc}")
        return result

    try:
        page_count = len(reader.pages)
    except (PyPdfError, ValueError) as exc:
        result.status = TextStatus.EMPTY
        result.notes.append(f"pdf page count unavailable: {exc}")
        return result

    result.pages = page_count
    if page_count > MAX_PAGES:
        result.notes.append(
            f"pdf has {page_count} pages; only the first {MAX_PAGES} were read"
        )

    chunks: list[str] = []
    failed_pages = 0
    attempted = 0
    for index, page in enumerate(reader.pages[:MAX_PAGES]):
        attempted += 1
        # One bad page costs that page, not the document: pypdf's text
        # extraction also raises IndexError and AttributeError on corrupt
        # content streams.
        try:
            chunks.append(page.extract_text() or "")
        except (PyPdfError, ValueError, KeyError, TypeError,
                IndexError, AttributeError) as exc:
            failed_pages += 1
            result.notes.append(f"page {index + 1} unreadable: {type(exc).__name__}")

    if failed_pages:
        result.notes.append(f"{failed_pages} page(s) could not be read")

    result.text = normalise("\n\n".join(chunks))
    if failed_pages and failed_pages == attempted:
        # The reader failed on every page. That says nothing about whether a
        # text layer exists, so this is not a scan and OCR is not the remedy
        # (D-011: a status that names the wrong remedy is worse than none).
        result.status = TextStatus.EMPTY
        result.notes.append(
            "no page could be read by the PDF parser; this is a reader failure, "
            "not evidence of a scan"
        )
        return result
    result.status = classify_text(result.text, is_pdf=True)
    if result.status is TextStatus.OCR_NEEDED:
        result.notes.append(
            "pdf carries no usable text layer; stored raw and flagged for OCR"
        )
    return result
