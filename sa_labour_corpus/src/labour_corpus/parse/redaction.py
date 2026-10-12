"""POPIA-minded redaction of personal identifiers.

Court judgments are public records and party names stay as published. What
gets removed is the incidental personal data a parser can sweep up: identity
numbers, bank account numbers, direct contact details.

The redactor is deliberately narrow. Over-redaction would destroy citations,
case numbers and statutory references, which are the whole point of the
corpus, so every rule is anchored and validated before it fires.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PLACEHOLDER = "[REDACTED_PI]"

# South African identity number: YYMMDD SSSS C A Z with a Luhn check digit.
# Often printed in groups ("800101 5009 087"), so one space or no-break space
# may separate the date, sequence and check groups. Never \s: a tab or a line
# break between digit runs is a table column or a wrapped line, not one number.
_SA_ID_RE = re.compile(
    r"(?<![\d/-])(\d{6})[  ]?(\d{4})[  ]?(\d{3})(?![\d/-])"
)

# Bank account numbers only when a nearby label makes the meaning unambiguous.
_BANK_RE = re.compile(
    r"\b(account(?:\s+(?:number|no\.?|nr\.?))?|acc(?:t)?\.?\s*no\.?|"
    r"bank\s+account)\b[\s:#]*([0-9][0-9 \-]{6,20}[0-9])",
    re.IGNORECASE,
)

# South African telephone numbers, written locally or internationally.
_PHONE_RE = re.compile(
    r"(?<![\w/])(?:\+27[\s-]?\d{2}[\s-]?\d{3}[\s-]?\d{4}"
    r"|0\d{2}[\s-]?\d{3}[\s-]?\d{4})(?![\w/])"
)

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Tax and passport style identifiers, label-anchored.
_TAX_RE = re.compile(
    r"\b(income\s+tax\s+(?:reference\s+)?number|tax\s+number|SARS\s+number|"
    r"passport\s+(?:number|no\.?))\b[\s:#]*([A-Z0-9-]{6,20})",
    re.IGNORECASE,
)

# Contexts that mark a matter as one where party names deserve extra care.
_SENSITIVE_CONTEXT_RE = re.compile(
    r"\b(minor child|the minor|a minor\b|complainant is a minor|"
    r"sexual offence|sexual assault|rape\b|identity of the complainant|"
    r"section 154\b|not be published|anonymi[sz]ed)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class RedactionReport:
    text: str
    counts: dict[str, int] = field(default_factory=dict)
    sensitive_context: bool = False

    @property
    def redacted(self) -> bool:
        return any(self.counts.values())

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _luhn_ok(digits: str) -> bool:
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _plausible_sa_id(digits: str) -> bool:
    """Validate the date prefix, the citizenship digit and the checksum.

    Requiring all three keeps ordinary 13-digit strings from being redacted.
    """
    if len(digits) != 13:
        return False
    month = int(digits[2:4])
    day = int(digits[4:6])
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return False
    if digits[10] not in "01":  # citizenship indicator
        return False
    return _luhn_ok(digits)


def redact(text: str) -> RedactionReport:
    """Redact personal identifiers, returning the text and per-field counts."""
    if not text:
        return RedactionReport(text="", counts={})

    counts: dict[str, int] = {}

    def _bump(field_name: str, amount: int = 1) -> None:
        if amount:
            counts[field_name] = counts.get(field_name, 0) + amount

    def _id_sub(match: re.Match[str]) -> str:
        digits = "".join(match.groups())
        if _plausible_sa_id(digits):
            _bump("sa_id_number")
            return PLACEHOLDER
        return match.group(0)

    result = _SA_ID_RE.sub(_id_sub, text)

    def _labelled_sub(field_name: str):
        def _inner(match: re.Match[str]) -> str:
            _bump(field_name)
            return f"{match.group(1)}: {PLACEHOLDER}"
        return _inner

    result, n = _BANK_RE.subn(_labelled_sub("bank_account"), result)
    result, n = _TAX_RE.subn(_labelled_sub("tax_or_passport"), result)

    result, n = _PHONE_RE.subn(PLACEHOLDER, result)
    _bump("phone_number", n)

    result, n = _EMAIL_RE.subn(PLACEHOLDER, result)
    _bump("email_address", n)

    return RedactionReport(
        text=result,
        counts=counts,
        sensitive_context=bool(_SENSITIVE_CONTEXT_RE.search(text)),
    )
