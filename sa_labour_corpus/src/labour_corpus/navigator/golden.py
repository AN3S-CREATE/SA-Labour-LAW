"""Golden fact patterns: check them, and render the sheet a lawyer signs off.

The cases live in ``tests/golden/navigator_cases.yaml``. Checking compares the
navigator's routing against each stated expectation. It deliberately ignores
grounding, which ``navigate provisions`` covers. The review sheet sets each
expectation beside what the navigator actually produced, so a reviewer signs
off on observed behaviour, not on a promise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .engine import Navigator
from .models import Assessment, CaseFacts
from .routing import classify

DEFAULT_CASES = Path(__file__).resolve().parents[3] / "tests" / "golden" / "navigator_cases.yaml"
PENDING = "pending_lawyer_review"


@dataclass(frozen=True, slots=True)
class GoldenCase:
    id: str
    as_of: date
    facts: CaseFacts
    expect: dict[str, Any]
    reviewed_by: str | None = None
    reviewed_on: date | None = None

    @property
    def reviewed(self) -> bool:
        return bool(self.reviewed_by and self.reviewed_on)


@dataclass(frozen=True, slots=True)
class GoldenSet:
    status: str
    instructions: str
    cases: list[GoldenCase]


def load_cases(path: Path = DEFAULT_CASES) -> GoldenSet:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    review = data.get("review") or {}
    cases = []
    seen: set[str] = set()
    for raw in data["cases"]:
        if raw["id"] in seen:
            raise ValueError(f"duplicate golden case id {raw['id']!r}")
        seen.add(raw["id"])
        if "track" not in raw["expect"]:
            raise ValueError(f"golden case {raw['id']!r} has no expected track")
        cases.append(GoldenCase(id=raw["id"], as_of=raw["as_of"],
                                facts=CaseFacts.model_validate(raw["facts"]),
                                expect=raw["expect"], reviewed_by=raw.get("reviewed_by"),
                                reviewed_on=raw.get("reviewed_on")))
    return GoldenSet(review.get("status", PENDING),
                     " ".join(str(review.get("reviewer_instructions", "")).split()), cases)


def observed(a: Assessment) -> dict[str, Any]:
    """The navigator's outcome in the vocabulary of the expectation keys."""
    notes = [*a.warnings, *(d.note for d in a.deadlines if d.note)]
    return {
        "track": classify(a.facts).track.value,
        "classification_has": a.classification,
        "due": [d.due.isoformat() if d.due else "pending" for d in a.deadlines],
        "status": [d.status.value for d in a.deadlines],
        "route": [s.basis.key if s.basis else None for s in a.route],
        "remedy": a.remedy_basis.key if a.remedy_basis else None,
        "onus": a.onus is not None,
        "forum_has": a.route[0].forum,
        "warning_has": " ".join(notes),
    }


_CONTAINS = {"classification_has", "forum_has", "warning_has"}


def mismatches(case: GoldenCase, a: Assessment) -> list[str]:
    seen = observed(a)
    problems = []
    for key, want in case.expect.items():
        if key not in seen:
            problems.append(f"unknown expectation key {key!r}")
            continue
        got = seen[key]
        if key == "due":
            want = [w if w == "pending" else str(w) for w in want]
        ok = str(want).lower() in got.lower() if key in _CONTAINS else got == want
        if not ok:
            problems.append(f"{key}: expected {want!r}, got {got!r}")
    return problems


def check(golden: GoldenSet, navigator: Navigator) -> dict[str, list[str]]:
    """Map each failing case id to its mismatches; empty when all pass."""
    failures = {}
    for case in golden.cases:
        problems = mismatches(case, navigator.assess(case.facts, case.as_of))
        if problems:
            failures[case.id] = problems
    return failures


def _facts_line(facts: CaseFacts) -> str:
    data = facts.model_dump(mode="json", exclude_defaults=True)
    return ", ".join(f"{k}={v}" for k, v in data.items())


def review_sheet(golden: GoldenSet, navigator: Navigator) -> str:
    reviewed = sum(c.reviewed for c in golden.cases)
    out = ["# Dismissal Navigator: golden case review sheet", "",
           f"Status: **{golden.status}**. {reviewed} of {len(golden.cases)} cases signed off.",
           "", golden.instructions, "",
           "Each case shows the facts and what the navigator actually produced "
           "today, and whether that matches the recorded expectation.", ""]
    for n, case in enumerate(golden.cases, 1):
        a = navigator.assess(case.facts, case.as_of)
        seen = observed(a)
        problems = mismatches(case, a)
        signed = (f"{case.reviewed_by} on {case.reviewed_on.isoformat()}"
                  if case.reviewed_by and case.reviewed_on else "-")
        out += [f"## {n}. `{case.id}`", "",
                f"- **As of:** {case.as_of.isoformat()}",
                f"- **Facts:** {_facts_line(case.facts)}",
                f"- **Classification:** {a.classification}",
                f"- **Track:** {seen['track']}",
                "- **Deadlines:** " + "; ".join(
                    f"{d.step}: {d.due.isoformat() if d.due else 'not started'} "
                    f"({d.basis.cite})" for d in a.deadlines),
                "- **Route:** " + " -> ".join(
                    f"{s.forum}: {s.action}" + (f" ({s.basis.cite})" if s.basis else "")
                    for s in a.route),
                f"- **Remedy ceiling:** {a.remedy_ceiling or 'none stated'}"
                + (f" ({a.remedy_basis.cite})" if a.remedy_basis else ""),
                f"- **Matches expectation:** {'yes' if not problems else 'NO: ' + '; '.join(problems)}",
                f"- **Signed off:** {signed}",
                "", "Reviewer: [ ] AGREE  [ ] DISAGREE  Notes: ______________________", ""]
    return "\n".join(out)
