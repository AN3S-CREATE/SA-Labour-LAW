"""Optional OCR for scanned PDFs, run entirely on the operator's machine.

OCR is opt-in and local. No page image and no text is sent anywhere.

Two engines are supported, both local:

* **tesseract** -- preferred where it is installed, because it is the better
  reader for long printed legal documents. Needs a system binary.
* **rapidocr-onnxruntime** -- a pip-only ONNX engine that needs no
  administrator rights, so an operator who cannot install system packages can
  still recover a scan.

When neither is present the caller is told so plainly and the document keeps
its ``ocr_needed`` status, because a corpus that silently returns empty text
for a scan is worse than one that admits it cannot read it.

OCR output is recognised text, not published text. Every document produced here
carries a parser note saying so, its ``ocr_applied`` flag is set and its parser
confidence is capped, so a reader never mistakes a machine transcription for
the authoritative instrument.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

TESSERACT_TIMEOUT = 300
DEFAULT_DPI = 300
MAX_OCR_PAGES = 120

OCR_PROVENANCE_NOTE = (
    "text produced by local OCR, not read from a text layer. It is a machine "
    "transcription and may contain recognition errors; confirm against the "
    "original file before relying on any wording."
)


def tesseract_path() -> str | None:
    return shutil.which("tesseract")


def tesseract_available() -> bool:
    return tesseract_path() is not None


def rapidocr_available() -> bool:
    """A pip-installable ONNX engine that needs no system binary or admin."""
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def page_renderer_available() -> bool:
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        return False
    return True


def preferred_engine() -> str | None:
    """Pick an OCR engine.

    tesseract is preferred where it exists because it is the better reader for
    long printed legal documents. rapidocr is the fallback that needs only pip,
    so an operator without admin rights can still recover a scan.
    """
    if tesseract_available():
        return "tesseract"
    if rapidocr_available():
        return "rapidocr"
    return None


def ocr_toolchain_status() -> dict[str, bool | str | None]:
    """What is installed, for honest reporting to the operator."""
    renderer = page_renderer_available()
    engine = preferred_engine()
    return {
        "tesseract": tesseract_path(),
        "tesseract_available": tesseract_available(),
        "rapidocr_available": rapidocr_available(),
        "engine": engine,
        "page_renderer_available": renderer,
        "ready": engine is not None and renderer,
    }


@dataclass(slots=True)
class OcrResult:
    text: str = ""
    ok: bool = False
    pages_read: int = 0
    engine: str = ""
    note: str = ""
    notes: list[str] = field(default_factory=list)


def _install_hint() -> str:
    return (
        "OCR needs a page renderer and one OCR engine, both local. "
        "Run `pip install pymupdf rapidocr-onnxruntime` for a pip-only setup "
        "that needs no administrator rights, or install tesseract from your "
        "package manager (https://github.com/UB-Mannheim/tesseract/wiki on "
        "Windows) for the better reader. Then re-run with --ocr."
    )


def _read_page_tesseract(png: bytes, language: str) -> tuple[str | None, str | None]:
    """OCR one rendered page with tesseract. Returns (text, error)."""
    tool = tesseract_path()
    if tool is None:
        return None, "tesseract disappeared between checks"
    try:
        completed = subprocess.run(  # noqa: S603 - fixed binary, no shell
            [tool, "stdin", "stdout", "-l", language],
            input=png,
            capture_output=True,
            timeout=TESSERACT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{type(exc).__name__}"
    if completed.returncode != 0:
        return None, completed.stderr.decode("utf-8", errors="ignore").strip()[:120]
    return completed.stdout.decode("utf-8", errors="replace"), None


_RAPIDOCR_ENGINE = None


def _read_page_rapidocr(png: bytes) -> tuple[str | None, str | None]:
    """OCR one rendered page with the ONNX engine. Returns (text, error)."""
    global _RAPIDOCR_ENGINE
    try:
        if _RAPIDOCR_ENGINE is None:
            from rapidocr_onnxruntime import RapidOCR

            _RAPIDOCR_ENGINE = RapidOCR()
        result, _elapsed = _RAPIDOCR_ENGINE(png)
    except Exception as exc:  # noqa: BLE001 - engine raises broadly
        return None, f"{type(exc).__name__}: {exc}"[:160]
    if not result:
        return "", None
    # Each entry is (box, text, confidence); the engine returns reading order.
    return "\n".join(str(line[1]) for line in result if len(line) > 1), None


def ocr_pdf(data: bytes, *, dpi: int = DEFAULT_DPI, language: str = "eng") -> OcrResult:
    """Render a scanned PDF locally and OCR each page.

    Returns ``ok=False`` with an explanatory note when the toolchain is absent,
    so the caller can leave the document flagged rather than storing silence.
    """
    status = ocr_toolchain_status()
    if not status["ready"]:
        missing = []
        if status["engine"] is None:
            missing.append("an OCR engine (tesseract or rapidocr-onnxruntime)")
        if not status["page_renderer_available"]:
            missing.append("pymupdf")
        return OcrResult(
            ok=False,
            note=f"OCR unavailable: {', '.join(missing)} not installed. {_install_hint()}",
        )

    import pymupdf

    engine = str(status["engine"])
    result = OcrResult()
    chunks: list[str] = []

    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - pymupdf raises broadly on bad input
        return OcrResult(ok=False, note=f"pdf could not be opened for OCR: {exc}")

    try:
        page_count = min(document.page_count, MAX_OCR_PAGES)
        if document.page_count > MAX_OCR_PAGES:
            result.notes.append(
                f"pdf has {document.page_count} pages; only the first "
                f"{MAX_OCR_PAGES} were OCR'd"
            )
        zoom = dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)

        for index in range(page_count):
            try:
                pixmap = document.load_page(index).get_pixmap(matrix=matrix)
                png = pixmap.tobytes("png")
            except Exception as exc:  # noqa: BLE001
                result.notes.append(f"page {index + 1} could not be rendered: {exc}")
                continue

            if engine == "tesseract":
                page_text, error = _read_page_tesseract(png, language)
            else:
                page_text, error = _read_page_rapidocr(png)

            if error is not None:
                result.notes.append(f"page {index + 1} {engine} error: {error}")
                continue

            chunks.append(page_text or "")
            result.pages_read += 1
    finally:
        document.close()

    result.text = "\n\n".join(chunks).strip()
    result.ok = bool(result.text)
    result.engine = engine
    result.note = (
        f"OCR read {result.pages_read} page(s) at {dpi} dpi using {engine}. "
        f"{OCR_PROVENANCE_NOTE}"
        if result.ok
        else f"OCR with {engine} produced no text"
    )
    return result
