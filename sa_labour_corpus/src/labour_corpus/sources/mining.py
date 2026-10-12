"""Mining public-guidance adapter.

Covers the Mine Health and Safety Council and departmental mining material.
Mining labour sits on the ordinary LRA framework with the Mine Health and
Safety Act layered over it, so this adapter collects the instruments that make
that overlay concrete.
"""

from __future__ import annotations

import re

MINING_LINK_PATTERNS: tuple[str, ...] = (
    r"\.pdf$",
    r"/document",
    r"/publication",
    r"/guideline",
    r"/legislation",
    r"cop",
    r"mandatory",
)

# Instrument families that bind or guide mining employers.
MINING_INSTRUMENTS: dict[str, tuple[str, ...]] = {
    "Mine Health and Safety Act 29 of 1996": (
        r"Mine Health and Safety Act",
        r"\bMHSA\b",
        r"\bAct 29 of 1996\b",
    ),
    "MHSA Regulations": (
        r"Mine Health and Safety Regulations",
        r"Chapter\s+\d+\s+Regulations",
    ),
    "Guideline for a Mandatory Code of Practice": (
        r"Mandatory Code of Practice",
        r"\bDMR\s*16/3/2/\S+",
        r"\bguideline for a mandatory\b",
    ),
    "Mine Health and Safety Inspectorate instruction": (
        r"Principal Inspector of Mines",
        r"section\s*54\s*(?:notice|instruction)",
    ),
    "Occupational Diseases in Mines and Works Act 78 of 1973": (
        r"Occupational Diseases in Mines and Works Act",
        r"\bODMWA\b",
    ),
}

_INSTRUMENT_RES = {
    name: [re.compile(p, re.IGNORECASE) for p in patterns]
    for name, patterns in MINING_INSTRUMENTS.items()
}


def detect_mining_instruments(text: str) -> list[str]:
    """Which mining instruments a document appears to engage."""
    sample = text[:120_000]
    return [
        name
        for name, regexes in _INSTRUMENT_RES.items()
        if any(regex.search(sample) for regex in regexes)
    ]
