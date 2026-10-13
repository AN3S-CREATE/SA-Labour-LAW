"""Compose an :class:`Assessment` from stated facts.

The engine is deterministic: the same facts, ``as_of`` date and corpus give
the same assessment. It holds no state and writes nothing.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date
from typing import assert_never

from ..sources.del_gov import CURRENT_DISMISSAL_CODE
from .deadlines import add_days, add_months, make_deadline, pending_deadline
from .evidence import EvidenceFinder, issues_for
from .grounding import StatuteSource, ground
from .models import (
    Assessment, CaseFacts, Deadline, DeadlineStatus, DisputeKind, Grounding,
    ProvisionRef, RouteStep,
)
from .provisions import get
from .routing import Track, checklist, classify, conciliation_forum

log = logging.getLogger(__name__)

# Shown while del_gov.CURRENT_DISMISSAL_CODE is unverified (D-016, D-021).
DISMISSAL_CODE_WARNING = (
    "Fairness is measured against the Code of Good Practice: Dismissal (LRA s188(2)). "
    "A new code was reportedly gazetted on 4 September 2025 (GG 53294), replacing "
    "Schedule 8, but this corpus has not yet verified it against the gazette. The "
    "checklist below uses long-standing process questions, not a specific code."
)


class Navigator:
    def __init__(self, statutes: StatuteSource | None = None,
                 evidence: EvidenceFinder | None = None, evidence_per_issue: int = 3) -> None:
        self.statutes = statutes
        self.evidence = evidence
        self.evidence_per_issue = evidence_per_issue

    def ref(self, key: str) -> ProvisionRef:
        return ground(get(key), self.statutes)

    def assess(self, facts: CaseFacts, as_of: date) -> Assessment:
        if facts.event_date > as_of:
            raise ValueError("event_date is after the assessment date")
        # A date that has not happened yet is a typo, and a later start date
        # would push a deadline out by the same amount.
        for name in ("final_decision_date", "became_aware_date", "certificate_date"):
            value = getattr(facts, name)
            if value is not None and value > as_of:
                raise ValueError(f"{name} is after the assessment date")
        cls = classify(facts)
        deadlines = self._deadlines(facts, cls.track, as_of)
        active = deadlines[-1:] if facts.certificate_date is not None else deadlines[:1]
        late = any(d.status is DeadlineStatus.EXPIRED for d in active)
        route = self._route(facts, cls.track, late)
        remedy, remedy_basis = self._remedy(facts, cls.automatically_unfair)

        warnings = list(cls.notes)
        if facts.dispute is DisputeKind.DISMISSAL and not CURRENT_DISMISSAL_CODE["verified"]:
            warnings.append(DISMISSAL_CODE_WARNING)
        refs = [*map(self.ref, cls.basis), *(d.basis for d in deadlines),
                *(s.basis for s in route if s.basis), *([remedy_basis] if remedy_basis else [])]
        onus = self.ref("lra_192_2") if facts.dispute is DisputeKind.DISMISSAL else None
        if onus:
            refs.append(onus)
        summary = Counter(r.grounding.value for r in refs)
        if summary.get(Grounding.NOT_FOUND.value):
            warnings.append("Some statutory wording relied on here was not found in the "
                            "held statutes. Treat those parts as unverified.")
        if summary.get(Grounding.NOT_CHECKED.value):
            warnings.append("No statute text was available to check against. Every "
                            "provision below is unverified.")

        evidence = []
        if self.evidence is not None:
            for issue, query in issues_for(facts, late):
                evidence.extend(self.evidence.find(issue, query, self.evidence_per_issue))

        assessment = Assessment(
            facts=facts, as_of=as_of, classification=cls.label,
            classification_basis=[self.ref(k) for k in cls.basis],
            deadlines=deadlines, route=route, remedy_ceiling=remedy,
            remedy_basis=remedy_basis, onus=onus, checklist=checklist(facts, late),
            evidence=evidence, warnings=warnings, grounding_summary=dict(summary),
        )
        log.info("assessment_completed", extra={
            "dispute": facts.dispute.value, "track": cls.track,
            "deadline_status": [d.status.value for d in deadlines],
            "grounding": dict(summary), "evidence_hits": len(evidence),
        })
        return assessment

    # -- parts ---------------------------------------------------------------

    def _deadlines(self, facts: CaseFacts, track: Track, today: date) -> list[Deadline]:
        if facts.dispute is DisputeKind.DISCRIMINATION_NOT_DISMISSAL:
            return [make_deadline("Refer the dispute to the CCMA",
                                  add_months(facts.event_date, 6), today,
                                  self.ref("eea_10_2"))]
        if facts.dispute is DisputeKind.UNFAIR_LABOUR_PRACTICE:
            start = max(facts.event_date, facts.became_aware_date or facts.event_date)
            out = [make_deadline("Refer the dispute for conciliation", add_days(start, 90),
                                 today, self.ref("lra_191_1_b_ii"))]
            if facts.certificate_date is not None and not facts.probation:
                out.append(make_deadline(
                    "Request arbitration", add_days(facts.certificate_date, 90),
                    today, self.ref("lra_136_1_b"),
                    note="LRA s136 permits condonation on good cause if this "
                         "90-day request period is missed.",
                ))
            return out
        start = max(facts.event_date, facts.final_decision_date or facts.event_date)
        out = [make_deadline("Refer the dismissal dispute for conciliation",
                             add_days(start, 30), today, self.ref("lra_191_1_b_i"),
                             note="Counted from the later of the dismissal and the "
                                  "employer's final decision (e.g. an internal appeal).")]
        # Every track is named, so a new one cannot silently miss a deadline.
        match track:
            case Track.LABOUR_COURT | Track.ELECTION:
                step = "Refer the dispute to the Labour Court"
                basis = self.ref("lra_191_11")
                if facts.certificate_date is None:
                    out.append(pending_deadline(step, basis, "Starts when the council or "
                                                "commissioner certifies the dispute "
                                                "unresolved."))
                else:
                    out.append(make_deadline(step, add_days(facts.certificate_date, 90),
                                             today, basis))
            case Track.ARBITRATION:
                if facts.certificate_date is not None and not facts.probation:
                    out.append(make_deadline(
                        "Request arbitration", add_days(facts.certificate_date, 90),
                        today, self.ref("lra_136_1_b"),
                        note="LRA s136 permits condonation on good cause if this "
                             "90-day request period is missed.",
                    ))
            case Track.EEA_LABOUR_COURT:
                pass
            case _:
                assert_never(track)
        return out

    def _route(self, facts: CaseFacts, track: Track, late: bool) -> list[RouteStep]:
        first_basis = "eea_10_2" if track == Track.EEA_LABOUR_COURT else "lra_191_1_a"
        conciliation_done = facts.certificate_date is not None
        forum = conciliation_forum(facts)
        steps = [] if conciliation_done else [RouteStep(
            order=1, forum=forum,
            action="Refer the dispute for conciliation and serve a copy on the employer",
            basis=self.ref(first_basis),
        )]
        if late:
            if conciliation_done and track is Track.ARBITRATION:
                steps.append(RouteStep(
                    order=2, forum=forum,
                    action="Apply for condonation of the late arbitration request, "
                           "showing good cause",
                    basis=self.ref("lra_136_1_b"),
                ))
            elif not conciliation_done:
                key = "eea_10_3" if track == Track.EEA_LABOUR_COURT else "lra_191_2"
                steps.append(RouteStep(order=1, forum=forum,
                                       action="Apply for condonation of the late referral, "
                                              "showing good cause",
                                       basis=self.ref(key)))
        if track == Track.ARBITRATION:
            note = ""
            if facts.probation:
                note = "For a probation-related dismissal, arbitration starts immediately."
            steps.append(RouteStep(order=2, forum=forum,
                                   action=("Request arbitration" if conciliation_done else
                                           "If conciliation fails, request arbitration"),
                                   basis=self.ref("lra_191_5_a"), note=note))
            if facts.probation:
                steps.append(RouteStep(order=2, forum=forum,
                                       action="Arbitration follows the certificate "
                                              "without a new request",
                                       basis=self.ref("lra_191_5A")))
            if facts.dispute is DisputeKind.UNFAIR_LABOUR_PRACTICE:
                steps.append(RouteStep(order=2, forum="Labour Court",
                                       action="If the detriment was for a protected "
                                              "disclosure, you may refer to the Labour Court",
                                       basis=self.ref("lra_191_13")))
        elif track == Track.LABOUR_COURT:
            steps.append(RouteStep(order=2, forum="Labour Court",
                                   action="If conciliation fails, refer the dispute for "
                                          "adjudication",
                                   basis=self.ref("lra_191_5_b")))
        elif track == Track.ELECTION:
            steps.append(RouteStep(order=2, forum="Arbitration or the Labour Court",
                                   action="If conciliation fails, choose arbitration or "
                                          "the Labour Court",
                                   basis=self.ref("lra_191_12")))
        elif track == Track.EEA_LABOUR_COURT:
            steps.append(RouteStep(order=2, forum="Labour Court",
                                   action="If conciliation fails, refer the dispute for "
                                          "adjudication",
                                   basis=self.ref("eea_10_6_a"),
                                   note="Other routes may be open; ask the CCMA."))
        else:
            # A track with no route of its own must fail loudly, not borrow
            # another track's forum and grounded basis.
            assert_never(track)
        return steps

    def _remedy(self, facts: CaseFacts, auto: bool) -> tuple[str | None, ProvisionRef | None]:
        if facts.dispute is DisputeKind.DISCRIMINATION_NOT_DISMISSAL:
            return None, None
        if facts.dispute is DisputeKind.UNFAIR_LABOUR_PRACTICE:
            return "If compensation is awarded, at most 12 months' remuneration", self.ref("lra_194_4")
        if auto:
            return "If compensation is awarded, at most 24 months' remuneration", self.ref("lra_194_3")
        return "If compensation is awarded, at most 12 months' remuneration", self.ref("lra_194_1")
