"""Classify a dispute and route it, following LRA s191 and EEA s10.

Pure functions of the stated facts. Every rule names the provision key it
rests on; the engine grounds those keys against the held statutes. Routing
turns on the reason the *employee alleges* (s191(5)), which is why the facts
are framed from the employee's side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..ontology.dismissal import PROCESS_DEFECT_TAGS
from .models import (
    AutoUnfairGround, CaseFacts, CouncilStatus, DisputeKind, ReasonGiven,
)


class Track(StrEnum):
    """Where a dispute goes if conciliation fails.

    A StrEnum, so values compare, format and serialise as the plain strings the
    golden cases and logs use, and the engine can dispatch on it exhaustively.
    """

    ARBITRATION = "arbitration"
    LABOUR_COURT = "labour_court"
    ELECTION = "election"             # s191(12): employee chooses
    EEA_LABOUR_COURT = "eea_labour_court"


@dataclass(frozen=True, slots=True)
class Classification:
    label: str
    basis: tuple[str, ...]
    track: Track
    automatically_unfair: bool = False
    notes: tuple[str, ...] = field(default=())


_REASON_LABELS: dict[ReasonGiven, str] = {
    ReasonGiven.CONDUCT: "Dismissal for a reason related to the employee's conduct",
    ReasonGiven.CAPACITY: "Dismissal for a reason related to the employee's capacity",
    ReasonGiven.OPERATIONAL_REQUIREMENTS:
        "Dismissal based on the employer's operational requirements",
    ReasonGiven.UNKNOWN: "Dismissal where the employee does not know the reason",
    ReasonGiven.UNPROTECTED_STRIKE:
        "Dismissal for participating in a strike that did not comply with Chapter IV",
    ReasonGiven.CLOSED_SHOP: "Dismissal related to a closed shop agreement",
}

_GROUND_LABELS: dict[AutoUnfairGround, str] = {
    AutoUnfairGround.FREEDOM_OF_ASSOCIATION: "freedom of association (s5)",
    AutoUnfairGround.PROTECTED_STRIKE: "participation in a protected strike or protest",
    AutoUnfairGround.REFUSED_STRIKE_WORK: "refusing to do the work of striking employees",
    AutoUnfairGround.REFUSED_DEMAND: "refusing a demand on a matter of mutual interest",
    AutoUnfairGround.EXERCISED_LRA_RIGHT: "exercising a right or taking part in LRA proceedings",
    AutoUnfairGround.PREGNANCY: "pregnancy",
    AutoUnfairGround.DISCRIMINATION: "unfair discrimination",
    AutoUnfairGround.TRANSFER_S197: "a transfer under s197 or s197A",
    AutoUnfairGround.PROTECTED_DISCLOSURE: "a protected disclosure",
}


def classify(facts: CaseFacts) -> Classification:
    if facts.dispute is DisputeKind.UNFAIR_LABOUR_PRACTICE:
        return Classification("Unfair labour practice", ("lra_186_2",), Track.ARBITRATION,
                              notes=("A whistle-blower who suffered an occupational "
                                     "detriment may also go to the Labour Court "
                                     "(s191(13)).",))
    if facts.dispute is DisputeKind.DISCRIMINATION_NOT_DISMISSAL:
        return Classification("Unfair discrimination (not a dismissal)", ("eea_10_2",),
                              Track.EEA_LABOUR_COURT)

    reason, ground = facts.reason, facts.auto_unfair_ground
    if reason is ReasonGiven.AUTOMATICALLY_UNFAIR:
        assert ground is not None  # enforced by CaseFacts
        return Classification(f"Automatically unfair dismissal: {_GROUND_LABELS[ground]}",
                              ("lra_187_1",), Track.LABOUR_COURT, automatically_unfair=True)
    if reason is ReasonGiven.CONSTRUCTIVE:
        if ground is not None:
            return Classification(
                "Constructive dismissal alleged to be automatically unfair: "
                f"{_GROUND_LABELS[ground]}",
                ("lra_186_1_e", "lra_187_1"), Track.LABOUR_COURT, automatically_unfair=True)
        return Classification("Constructive dismissal", ("lra_186_1_e",), Track.ARBITRATION,
                              notes=("The employee must first prove that resigning was a "
                                     "dismissal: that the employer made continued "
                                     "employment intolerable.",))
    if reason is ReasonGiven.OPERATIONAL_REQUIREMENTS:
        return _operational(facts)
    if reason in (ReasonGiven.UNPROTECTED_STRIKE, ReasonGiven.CLOSED_SHOP):
        return Classification(_REASON_LABELS[reason], ("lra_191_5_b",), Track.LABOUR_COURT)
    basis = ("lra_191_5_a",) if reason is ReasonGiven.UNKNOWN else ("lra_188_1",)
    return Classification(_REASON_LABELS[reason], basis, Track.ARBITRATION)


def _operational(facts: CaseFacts) -> Classification:
    label = _REASON_LABELS[ReasonGiven.OPERATIONAL_REQUIREMENTS]
    if facts.employer_has_fewer_than_10_employees or facts.only_this_employee_retrenched:
        return Classification(label, ("lra_188_1", "lra_191_12"), Track.ELECTION)
    notes: tuple[str, ...] = ()
    if facts.employer_has_fewer_than_10_employees is None or \
            facts.only_this_employee_retrenched is None:
        notes = ("If the employer has fewer than 10 employees, or only you were "
                 "retrenched, you may be able to choose arbitration instead "
                 "(s191(12)). Answer those questions to check.",)
    return Classification(label, ("lra_188_1",), Track.LABOUR_COURT, notes=notes)


def conciliation_forum(facts: CaseFacts) -> str:
    if facts.dispute is DisputeKind.DISCRIMINATION_NOT_DISMISSAL:
        return "CCMA"
    if facts.council is CouncilStatus.YES:
        return facts.council_name or "The bargaining council for your sector"
    if facts.council is CouncilStatus.NO:
        return "CCMA"
    return "Your sector's bargaining council if one covers you; otherwise the CCMA"


_DEFECTS = {t.name: t.description for t in PROCESS_DEFECT_TAGS}

_CHECKS_BY_REASON: dict[ReasonGiven, tuple[str, ...]] = {
    ReasonGiven.CONDUCT: (
        "no_hearing_at_all", "defective_charge_sheet", "evidence_not_disclosed",
        "representation_denied", "chairperson_not_impartial", "predetermined_outcome",
        "inconsistency", "no_progressive_discipline", "service_and_record_ignored",
        "zero_tolerance_without_proportionality", "after_acquired_evidence",
        "precautionary_suspension_abuse"),
    ReasonGiven.CAPACITY: (
        "no_hearing_at_all", "representation_denied", "predetermined_outcome",
        "alternatives_not_considered", "service_and_record_ignored"),
    ReasonGiven.OPERATIONAL_REQUIREMENTS: (
        "paper_consultation_s189", "selection_criteria_unfair",
        "alternatives_not_considered"),
    ReasonGiven.CONSTRUCTIVE: ("forced_resignation",),
    ReasonGiven.UNKNOWN: ("no_hearing_at_all", "predetermined_outcome"),
}


def checklist(facts: CaseFacts, late: bool) -> list[str]:
    """Process failures worth testing the dismissal against.

    Drawn from the ontology's process-defect layer, so the checklist and the
    corpus tagger share one vocabulary.
    """
    names = list(_CHECKS_BY_REASON.get(facts.reason, ())) \
        if facts.dispute is DisputeKind.DISMISSAL else []
    if late:
        names.append("condonation_late_referral")
    return [_DEFECTS[n] for n in names]
