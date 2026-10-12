"""RTF extraction tests.

Three defects are pinned here. Matching each control word against a fresh
slice of the rest of the document made parsing quadratic; ``\\'hh`` escapes
were decoded as latin-1, so the cp1252 apostrophe ``\\'92`` became the
invisible control U+0092; and the ANSI fallback after ``\\uN`` was emitted as
well, so every Unicode character appeared twice.
"""

from __future__ import annotations

import re

import pytest

from labour_corpus.parse.text import normalise, rtf_to_text


def _reference_rtf_to_text(data: bytes | str) -> str:
    """The slice-based implementation as it stood before the fix.

    Kept verbatim (apart from the name) so the in-place matcher can be shown
    to give identical output wherever the code page and \\uc handling do not
    come into play.
    """
    if isinstance(data, bytes):
        raw = data.decode("latin-1", errors="ignore")
    else:
        raw = data
    if "\\rtf" not in raw[:200]:
        return normalise(raw)

    out: list[str] = []
    index = 0
    depth = 0
    length = len(raw)
    skip_groups = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "object"}

    while index < length:
        char = raw[index]
        if char == "\\":
            match = re.match(r"\\([a-zA-Z]+)(-?\d+)?[ ]?", raw[index:])
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
                    continue
                if word in ("par", "line", "pard"):
                    out.append("\n")
                elif word == "tab":
                    out.append("\t")
                elif word == "u":
                    code = match.group(2)
                    if code:
                        try:
                            out.append(chr(int(code) % 65536))
                        except ValueError:
                            pass
                index += match.end()
                continue
            if index + 1 < length and raw[index + 1] == "'":
                try:
                    out.append(bytes.fromhex(raw[index + 2:index + 4]).decode("latin-1"))
                except ValueError:
                    pass
                index += 4
                continue
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        else:
            out.append(char)
        index += 1

    return normalise("".join(out))


# \uc0 declares that no fallback follows a \uN, and \'e9 is the same
# character in latin-1 and cp1252, so the old and new parsers must agree.
_HEADER = (
    "{\\rtf1\\ansi\\ansicpg1252\\deff0\\uc0\n"
    "{\\fonttbl{\\f0\\froman\\fcharset0 Times New Roman;}{\\f1\\fswiss Arial;}}\n"
    "{\\colortbl;\\red0\\green0\\blue0;\\red255\\green0\\blue0;}\n"
    "{\\stylesheet{\\s0 Normal;}}\n"
    "{\\info{\\title Mahlangu v CCMA}{\\author SAFLII}}\n"
)
_BODY = (
    "\\pard\\plain\\s0\\ql\\f1\\fs24 IN THE LABOUR COURT OF SOUTH AFRICA\\par\n"
    "Case no: JR 1487/21\\par\n"
    "{\\pict\\wmetafile8\\picw100\\pich100 0a0b0c{\\*\\blipuid 1f2e}ffee}\\par\n"
    "[1] The employee\\u8217 s caf\\'e9 shift\\line was \\u-3913 noted.\\par\n"
    "Columns\\tab separated\\~by\\-symbols and \\{literal braces\\}\\par\n"
    "\\b Order\\b0  {\\i in a group} and R 1 250 000.00 awarded.\\par\n"
)
_FEATURES_RTF = _HEADER + _BODY + "}"


def test_in_place_matching_gives_the_same_text_as_the_slice_based_parser():
    assert rtf_to_text(_FEATURES_RTF) == _reference_rtf_to_text(_FEATURES_RTF)
    assert rtf_to_text(_FEATURES_RTF.encode("latin-1")) == \
        _reference_rtf_to_text(_FEATURES_RTF.encode("latin-1"))


def test_header_groups_are_skipped_and_body_text_kept():
    text = rtf_to_text(_FEATURES_RTF)
    for skipped in ("Times New Roman", "Arial", "red255", "Normal;", "SAFLII", "0a0b0c"):
        assert skipped not in text
    assert "IN THE LABOUR COURT OF SOUTH AFRICA" in text
    assert "Case no: JR 1487/21" in text
    assert "R 1 250 000.00" in text
    assert "caf\u00e9" in text
    assert "Columns\tseparated" not in text  # normalise turns the tab into a space
    assert "Columns separated" in text


def test_a_large_document_matches_the_slice_based_parser():
    """Equality only: no wall-clock assertion on a shared machine."""
    paragraph = (
        "\\pard\\plain\\s0\\ql\\f1\\fs24 [12] The commissioner\\u8217 s award "
        "caf\\'e9 was reviewed\\tab and set aside.\\par\n"
    )
    document = _HEADER + paragraph * 1_500 + "}"
    assert len(document) > 100_000
    assert rtf_to_text(document) == _reference_rtf_to_text(document)


# -- code page -----------------------------------------------------------------
def test_cp1252_apostrophe_is_a_real_quote():
    assert rtf_to_text(rb"{\rtf1\ansi\ansicpg1252 It\'92s}") == "It\u2019s"


def test_cp1252_is_assumed_when_no_code_page_is_declared():
    text = rtf_to_text(rb"{\rtf1\ansi \'93Dismissal\'94 \'96 It\'92s}")
    assert text == "\u201cDismissal\u201d \u2013 It\u2019s"
    assert not any("\x80" <= ch <= "\x9f" for ch in text)


def test_a_declared_code_page_is_honoured():
    assert rtf_to_text(rb"{\rtf1\ansi\ansicpg1251 \'c0\'e1}") == "\u0410\u0431"


def test_an_unknown_code_page_falls_back_to_cp1252():
    assert rtf_to_text(rb"{\rtf1\ansi\ansicpg99999 It\'92s}") == "It\u2019s"


def test_an_undefined_byte_is_replaced_not_passed_through_as_a_control():
    text = rtf_to_text(rb"{\rtf1\ansi\ansicpg1252 a\'81b}")
    assert text == "a\ufffdb"


# -- \uN and its ANSI fallback -------------------------------------------------
def test_unicode_escape_is_not_emitted_twice():
    text = rtf_to_text(rb"{\rtf1\ansi\ansicpg1252\uc1 The employee\u8217\'92s case}")
    assert text == "The employee\u2019s case"
    assert text.count("\u2019") == 1


def test_question_mark_fallback_is_skipped():
    assert rtf_to_text(rb"{\rtf1\ansi \u-3913?item}") == "\uf0b7item"


def test_uc_sets_the_number_of_fallback_characters():
    text = rtf_to_text(rb"{\rtf1\ansi\uc2\u12354\'82\'a0 x}")
    assert text == "\u3042 x"


def test_uc_is_restored_when_its_group_closes():
    text = rtf_to_text(rb"{\rtf1\ansi {\uc0\u8217}\u8217?y}")
    assert text == "\u2019\u2019y"


def test_uc_is_restored_after_a_skipped_group():
    text = rtf_to_text(rb"{\rtf1\ansi\uc1 {\uc0\fonttbl{\f0 Arial;}}\u8217?y}")
    assert text == "\u2019y"


@pytest.mark.parametrize("rtf,expected", [
    (rb"{\rtf1\ansi\uc1 \u8217\par next}", "\u2019\nnext"),
    (rb"{\rtf1\ansi\uc1 {\u8217}next}", "\u2019next"),
])
def test_a_control_word_or_brace_ends_the_fallback(rtf, expected):
    """A writer that omits the fallback must not lose the next real token."""
    assert rtf_to_text(rtf) == expected


def test_rtf_reaches_the_pipeline_with_real_quotes():
    from labour_corpus.models import TextStatus
    from labour_corpus.pipeline import extract_text

    body = (rb"{\rtf1\ansi\ansicpg1252 {\fonttbl{\f0 Arial;}}\f0 The applicant\'92s "
            rb"dismissal was substantively unfair.\par " + b"More text. " * 40 + b"}")
    text, _title, status, _notes = extract_text(body, "application/rtf", "https://example.org/a.rtf")
    assert status is TextStatus.OK
    assert "applicant\u2019s dismissal" in text


def test_surrogate_pair_escapes_become_one_astral_character():
    # U+1F600 is written as the signed surrogates \u-10179 (D83D) \u-8704 (DE00).
    text = rtf_to_text(rb"{\rtf1\ansi \u-10179?\u-8704? end}")
    assert text == "\U0001F600 end"
    text.encode("utf-8")


def test_a_lone_surrogate_is_replaced_not_kept():
    text = rtf_to_text(rb"{\rtf1\ansi broken \u-10179? here}")
    assert "\ufffd" in text
    text.encode("utf-8")  # a lone surrogate would raise UnicodeEncodeError
