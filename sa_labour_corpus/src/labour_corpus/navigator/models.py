"""Typed contracts for the Dismissal Navigator.

``CaseFacts`` is what a dismissed employee (or their representative) states.
``Assessment`` is what the navigator answers. Every statement in an
assessment that rests on law carries a :class:`ProvisionRef`, and every
provision records whether its wording was found in a statute this corpus
actually holds. Nothing here is legal advice, and the output says so.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

DISCLAIMER = (
    "This is general legal information generated from statutes held in a "
    "local corpus. It is not legal advice and it does not decide whether a "
    "dismissal was fair. Time limits are strict: if a deadline is near or has "
    "passed, contact the CCMA, a bargaining council, a trade union or a "
    "lawyer now."
)


class ReasonGiven(str, Enum):
    """The reason for dismissal as the employee alleges it.

    Routing under LRA s191(5) turns on the reason the *employee alleges*, not
    on what the employer says, so this is framed from the employee's side.
    """

    CONDUCT = "conduct"                          # misconduct
    CAPACITY = "capacity"                        # poor performance, ill health
    OPERATIONAL_REQUIREMENTS = "operational_requirements"  # retrenchment
    AUTOMATICALLY_UNFAIR = "automatically_unfair"
    CONSTRUCTIVE = "constructive"                # resigned: work made intolerable
    UNPROTECTED_STRIKE = "unprotected_strike"
    CLOSED_SHOP = "closed_shop"
    UNKNOWN = "unknown"                          # no reason given


class AutoUnfairGround(str, Enum):
    """LRA s187(1) grounds. Each maps to one paragraph of that subsection."""

    FREEDOM_OF_ASSOCIATION = "s5_freedom_of_association"
    PROTECTED_STRIKE = "a_protected_strike"
    REFUSED_STRIKE_WORK = "b_refused_strike_work"
    REFUSED_DEMAND = "c_refused_demand"
    EXERCISED_LRA_RIGHT = "d_exercised_lra_right"
    PREGNANCY = "e_pregnancy"
    DISCRIMINATION = "f_discrimination"
    TRANSFER_S197 = "g_transfer_s197"
    PROTECTED_DISCLOSURE = "h_protected_disclosure"


class DisputeKind(str, Enum):
    DISMISSAL = "dismissal"
    UNFAIR_LABOUR_PRACTICE = "unfair_labour_practice"
    DISCRIMINATION_NOT_DISMISSAL = "discrimination_not_dismissal"


class CouncilStatus(str, Enum):
    """Whether a bargaining council covers the employer and employee."""

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class CaseFacts(BaseModel):
    """What the employee states. Only facts needed for routing are asked for.

    No name, ID number or contact detail is collected: the navigator does not
    need them, and POPIA's minimisation principle says not to ask.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispute: DisputeKind = DisputeKind.DISMISSAL
    reason: ReasonGiven = ReasonGiven.UNKNOWN
    auto_unfair_ground: AutoUnfairGround | None = None
    # The date of dismissal, or of the act/omission for a ULP or discrimination.
    event_date: date
    # Date of the employer's final decision (e.g. internal appeal outcome).
    final_decision_date: date | None = None
    # For a ULP: when the employee became aware, if later than event_date.
    became_aware_date: date | None = None
    # Date a council/CCMA certified the dispute unresolved, if conciliation ended.
    certificate_date: date | None = None
    council: CouncilStatus = CouncilStatus.UNKNOWN
    council_name: str | None = Field(default=None, max_length=120)
    # s191(12) facts: only meaningful for operational-requirements dismissals.
    employer_has_fewer_than_10_employees: bool | None = None
    only_this_employee_retrenched: bool | None = None
    probation: bool = False
    # Optional dismissal date range checks happen against `today` at assess time.

    @model_validator(mode="after")
    def _consistent(self) -> "CaseFacts":
        if self.reason is ReasonGiven.AUTOMATICALLY_UNFAIR and self.auto_unfair_ground is None:
            raise ValueError("auto_unfair_ground is required when reason is automatically_unfair")
        if self.auto_unfair_ground is not None and self.reason not in (
            ReasonGiven.AUTOMATICALLY_UNFAIR, ReasonGiven.CONSTRUCTIVE,
        ):
            raise ValueError(
                "auto_unfair_ground only applies to an automatically_unfair or "
                "constructive dismissal"
            )
        for name in ("final_decision_date", "became_aware_date", "certificate_date"):
            value = getattr(self, name)
            if value is not None and value < self.event_date:
                raise ValueError(f"{name} cannot be earlier than event_date")
        if self.dispute is not DisputeKind.DISMISSAL and self.reason is not ReasonGiven.UNKNOWN:
            raise ValueError("reason only applies to a dismissal dispute")
        return self


class Grounding(str, Enum):
    """Whether a provision's quoted wording was found in a held statute."""

    VERIFIED = "verified"            # exact wording found in a held, non-draft document
    NOT_FOUND = "not_found"          # corpus searched, wording absent: do not rely on it
    NOT_CHECKED = "not_checked"      # no corpus, or no held copy that identifies itself as the Act


class ProvisionRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    cite: str                        # e.g. "LRA s191(1)(b)(i)"
    quote: str                       # verbatim wording relied on
    grounding: Grounding = Grounding.NOT_CHECKED
    doc_id: str | None = None        # the held document the wording was found in
    source_url: str | None = None
    # Held copies of the Act searched, and how many contain the wording. Fewer
    # matches than copies usually means an older consolidation is also held.
    copies_checked: int = 0
    copies_matching: int = 0


class DeadlineStatus(str, Enum):
    OPEN = "open"
    DUE_SOON = "due_soon"            # 7 days or fewer left
    EXPIRED = "expired"              # condonation (good cause) needed
    NOT_STARTED = "not_started"      # depends on a step that has not happened


class Deadline(BaseModel):
    model_config = ConfigDict(frozen=True)

    step: str
    due: date | None
    days_left: int | None
    status: DeadlineStatus
    basis: ProvisionRef
    note: str = ""


class RouteStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    order: int
    forum: str
    action: str
    basis: ProvisionRef | None = None
    note: str = ""


class EvidenceHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    issue: str
    doc_id: str
    doc_type: str
    title: str | None
    url: str
    snippet: str


class Assessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    facts: CaseFacts
    as_of: date
    classification: str
    classification_basis: list[ProvisionRef]
    deadlines: list[Deadline]
    route: list[RouteStep]
    remedy_ceiling: str | None
    remedy_basis: ProvisionRef | None
    onus: ProvisionRef | None
    checklist: list[str]
    evidence: list[EvidenceHit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    grounding_summary: dict[str, int] = Field(default_factory=dict)
    disclaimer: str = DISCLAIMER

    @property
    def fully_grounded(self) -> bool:
        return set(self.grounding_summary) <= {Grounding.VERIFIED.value}
