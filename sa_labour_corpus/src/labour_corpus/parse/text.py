"""HTML and RTF to plain text, plus lightweight language detection.

Text extraction never invents content. When a document yields no usable text
the caller is told so via TextStatus rather than being handed an empty string
that looks like a successful parse.
"""

from __future__ import annotations

import codecs
import re
import unicodedata

from selectolax.parser import HTMLParser

from ..models import TextStatus

_DROP_TAGS = (
    "script", "style", "noscript", "nav", "header", "footer",
    "form", "button", "svg", "iframe",
)

_WS_RE = re.compile(r"[ \t\u00a0]+")
_BLANKS_RE = re.compile(r"\n{3,}")

# An RTF control word: backslash, letters, optional signed number, and one
# optional delimiting space that belongs to the word. Matched in place with
# .match(raw, index); slicing raw[index:] per word made parsing quadratic.
_RTF_WORD_RE = re.compile(r"\\([a-zA-Z]+)(-?\d+)?[ ]?")
# The ANSI code page that \'hh escapes are written in (\ansicpg1252 etc.).
_RTF_CODEPAGE_RE = re.compile(r"\\ansicpg(\d+)")
_RTF_DEFAULT_CODEPAGE = "cp1252"

# Afrikaans function words that are rare or absent in English legal prose.
_AFRIKAANS_MARKERS = (
    " die ", " nie ", " van ", " het ", " dat ", " met ", " word ",
    " sy ", " hy ", " ook ", " deur ", " deur die ", " aansoek ",
    " werknemer ", " werkgewer ", " ontslag ", " hof ", " regter ",
    " nieteenstaande ", " ooreenkoms ", " bevind ",
)
_ENGLISH_MARKERS = (
    " the ", " and ", " that ", " was ", " with ", " court ", " applicant ",
    " respondent ", " employee ", " employer ", " dismissal ", " section ",
)


def html_to_text(html: str | bytes) -> str:
    """Extract readable text from an HTML document."""
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="ignore")
    if not html.strip():
        return ""
    tree = HTMLParser(html)
    for tag in _DROP_TAGS:
        for node in tree.css(tag):
            node.decompose()
    body = tree.body or tree.root
    if body is None:
        return ""
    text = body.text(separator="\n", strip=False)
    return normalise(text)


def html_title(html: str | bytes) -> str | None:
    """Return the document title. SAFLII carries the full citation there."""
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="ignore")
    if not html.strip():
        return None
    tree = HTMLParser(html)
    node = tree.css_first("title")
    if node is None:
        node = tree.css_first("h1")
    if node is None:
        return None
    title = normalise(node.text(strip=True))
    return title or None


def _rtf_codepage(raw: str) -> str:
    """The code page an RTF header declares, cp1252 when absent or unknown."""
    match = _RTF_CODEPAGE_RE.search(raw[:2_000])
    if not match:
        return _RTF_DEFAULT_CODEPAGE
    codepage = f"cp{match.group(1)}"
    try:
        codecs.lookup(codepage)
    except LookupError:
        return _RTF_DEFAULT_CODEPAGE
    return codepage


def rtf_to_text(data: bytes | str) -> str:
    """Strip RTF control words. Adequate for SAFLII-style RTF judgments.

    ``\\'hh`` escapes are decoded in the declared ``\\ansicpgN`` code page
    (cp1252 when none is declared), so ``\\'92`` is a right single quote and
    not an invisible C1 control. After a ``\\uN`` character the ``\\ucN``
    (default 1) ANSI fallback characters that follow it are skipped, so the
    character is not emitted twice.
    """
    if isinstance(data, bytes):
        raw = data.decode("latin-1", errors="ignore")
    else:
        raw = data
    if "\\rtf" not in raw[:200]:
        return normalise(raw)

    codepage = _rtf_codepage(raw)
    out: list[str] = []
    index = 0
    depth = 0
    length = len(raw)
    skip_groups = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "object"}
    # \ucN is scoped like a character property: saved at "{", restored at "}".
    uc = 1
    uc_stack: list[int] = []
    fallback = 0  # ANSI fallback characters still to skip after a \uN

    while index < length:
        char = raw[index]
        if fallback:
            # The fallback is \'hh escapes and plain characters. A control
            # word, control symbol or brace ends it early and is processed.
            if char == "\\" and raw.startswith("'", index + 1):
                fallback -= 1
                index += 4
                continue
            if char not in "\\{}\r\n":
                fallback -= 1
                index += 1
                continue
            if char not in "\r\n":
                fallback = 0
        if char == "\\":
            match = _RTF_WORD_RE.match(raw, index)
            if match:
                word = match.group(1)
                if word in skip_groups:
                    depth_at = depth
                    while index < length and depth >= depth_at:
                        if raw[index] == "{":
                            depth += 1
                        elif raw[index] == "}":
                            depth -= 1
                        index += 1
                    if uc_stack:  # the skipped group's "{" saved a \uc value
                        uc = uc_stack.pop()
                    continue
                if word in ("par", "line", "pard"):
                    out.append("\n")
                elif word == "tab":
                    out.append("\t")
                elif word == "uc":
                    code = match.group(2)
                    if code and not code.startswith("-"):
                        uc = int(code)
                elif word == "u":
                    code = match.group(2)
                    if code:
                        try:
                            out.append(chr(int(code) % 65536))
                        except ValueError:
                            pass
                        fallback = uc
                index = match.end()  # absolute: the match ran in place
                continue
            if index + 1 < length and raw[index + 1] == "'":
                try:
                    out.append(
                        bytes.fromhex(raw[index + 2:index + 4]).decode(
                            codepage, errors="replace"))
                except ValueError:
                    pass
                index += 4
                continue
            index += 2
            continue
        if char == "{":
            depth += 1
            uc_stack.append(uc)
        elif char == "}":
            depth -= 1
            if uc_stack:
                uc = uc_stack.pop()
        else:
            out.append(char)
        index += 1

    # An astral character arrives as two \uN surrogate escapes: pair them, and
    # replace a lone surrogate, so the text can always be written as UTF-8.
    text = "".join(out).encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return normalise(text)


def normalise(text: str) -> str:
    """Normalise unicode and collapse runaway whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return _BLANKS_RE.sub("\n\n", text).strip()


def detect_language(text: str) -> str:
    """Return 'en', 'af' or 'unknown'. Judgment text is never translated."""
    if not text or len(text) < 200:
        return "unknown"
    sample = f" {text[:20_000].lower()} "
    af = sum(sample.count(marker) for marker in _AFRIKAANS_MARKERS)
    en = sum(sample.count(marker) for marker in _ENGLISH_MARKERS)
    if af == 0 and en == 0:
        return "unknown"
    if af > en * 1.2:
        return "af"
    if en > af:
        return "en"
    return "unknown"


def classify_text(text: str, *, is_pdf: bool = False) -> TextStatus:
    """Decide whether extracted text is usable."""
    stripped = (text or "").strip()
    if not stripped:
        return TextStatus.OCR_NEEDED if is_pdf else TextStatus.EMPTY
    # A PDF that yields only a handful of characters per page is a scan.
    if is_pdf and len(stripped) < 200:
        return TextStatus.OCR_NEEDED
    return TextStatus.OK
