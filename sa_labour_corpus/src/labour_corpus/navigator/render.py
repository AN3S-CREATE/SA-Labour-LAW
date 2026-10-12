"""Render an assessment as plain Markdown for a reader who is not a lawyer."""

from __future__ import annotations

from .models import Assessment, DeadlineStatus, Grounding, ProvisionRef

_MARK = {Grounding.VERIFIED: "verified in held text",
         Grounding.NOT_FOUND: "NOT FOUND in held text - do not rely on it",
         Grounding.NOT_CHECKED: "unverified - no statute text available"}

_STATUS = {DeadlineStatus.OPEN: "open", DeadlineStatus.DUE_SOON: "DUE SOON",
           DeadlineStatus.EXPIRED: "PASSED - condonation needed",
           DeadlineStatus.NOT_STARTED: "not started"}


def _cite(ref: ProvisionRef) -> str:
    return f"{ref.cite} ({_MARK[ref.grounding]})"


def _copies(ref: ProvisionRef) -> str:
    if not ref.copies_checked:
        return ""
    text = f" In {ref.copies_matching} of {ref.copies_checked} held copies"
    if 0 < ref.copies_matching < ref.copies_checked:
        text += "; the others may be older consolidations"
    return text + "."


def _link(url: str) -> str:
    if url.startswith("legacy:"):
        return "(local copy from an earlier harvest; no public URL recorded)"
    return f"<{url}>"


def to_markdown(a: Assessment) -> str:
    out = [f"# Dismissal Navigator assessment ({a.as_of.isoformat()})", "",
           f"> {a.disclaimer}", "", "## What this looks like", "",
           f"**{a.classification}**. Basis: "
           + "; ".join(_cite(r) for r in a.classification_basis) + "."]
    if a.onus:
        out += ["", f"Once you show you were dismissed, the employer must prove the "
                    f"dismissal was fair: {_cite(a.onus)}."]
    if a.warnings:
        out += ["", "## Warnings", "", *(f"- {w}" for w in a.warnings)]

    out += ["", "## Deadlines", "", "| Step | Due | Days left | Status | Basis |",
            "|---|---|---|---|---|"]
    for d in a.deadlines:
        due = d.due.isoformat() if d.due else "-"
        left = "-" if d.days_left is None else str(d.days_left)
        out.append(f"| {d.step} | {due} | {left} | {_STATUS[d.status]} | {_cite(d.basis)} |")
    notes = [f"- {d.step}: {d.note}" for d in a.deadlines if d.note]
    if notes:
        out += ["", *notes]

    out += ["", "## Where to go", ""]
    for s in a.route:
        basis = f" ({_cite(s.basis)})" if s.basis else ""
        note = f" {s.note}" if s.note else ""
        out.append(f"{s.order}. **{s.forum}**: {s.action}{basis}.{note}")

    if a.remedy_ceiling and a.remedy_basis:
        out += ["", "## Remedies", "", f"{a.remedy_ceiling}: {_cite(a.remedy_basis)}."]
    if a.checklist:
        out += ["", "## Questions to test the dismissal against", "",
                *(f"- {c}" for c in a.checklist)]
    if a.evidence:
        out += ["", "## Held documents on these issues", "",
                "These discuss the issues; they are not rulings on your case.", ""]
        for e in a.evidence:
            kind = e.doc_type.replace("_", " ")
            out.append(f"- *{e.issue}* ({kind}): {e.title or e.doc_id} {_link(e.url)}  \n"
                       f"  {e.snippet}")

    out += ["", "## Provisions quoted", ""]
    seen: set[str] = set()
    for ref in _all_refs(a):
        if ref.key in seen:
            continue
        seen.add(ref.key)
        where = f" Found in `{ref.doc_id}`." if ref.doc_id else ""
        where += _copies(ref)
        out.append(f'- **{ref.cite}**: "{ref.quote}" ({_MARK[ref.grounding]}).{where}')
    return "\n".join(out) + "\n"


def _all_refs(a: Assessment) -> list[ProvisionRef]:
    refs = [*a.classification_basis, *(d.basis for d in a.deadlines),
            *(s.basis for s in a.route if s.basis)]
    refs += [r for r in (a.remedy_basis, a.onus) if r]
    return refs
