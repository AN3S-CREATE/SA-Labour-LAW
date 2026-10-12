"""A guided interview that builds :class:`CaseFacts` in plain language.

For advice-office staff and workers who should not have to write JSON. The
question flow is a pure function over an :class:`Asker`, so it is tested with
scripted answers; the CLI supplies a terminal asker. Questions are asked in the
worker's terms ("Did you resign because...") and mapped to the LRA categories
the router needs. No names, ID numbers or contact details are asked for.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, TypedDict
from collections.abc import Callable

import typer

from .models import AutoUnfairGround, CaseFacts, CouncilStatus, DisputeKind, ReasonGiven

Options = list[tuple[str, str]]          # (key, label shown to the person)


class Asker(Protocol):
    def choose(self, question: str, options: Options) -> str: ...
    def when(self, question: str, required: bool) -> date | None: ...
    def yes_no(self, question: str, allow_unknown: bool) -> bool | None: ...
    def text(self, question: str) -> str | None: ...


class _CouncilFacts(TypedDict, total=False):
    council: CouncilStatus
    council_name: str | None


class _ReasonFacts(TypedDict, total=False):
    employer_has_fewer_than_10_employees: bool | None
    only_this_employee_retrenched: bool | None
    probation: bool


WHAT_HAPPENED: Options = [
    ("dismissal", "I was dismissed, retrenched, or my contract was not renewed"),
    ("constructive", "I resigned because my employer made work intolerable"),
    ("ulp", "I was treated unfairly at work but not dismissed (promotion, demotion, "
            "suspension, discipline short of dismissal, probation, training, benefits)"),
    ("discrimination", "I was discriminated against at work but not dismissed"),
]

REASONS: Options = [
    ("conduct", "Misconduct: something I allegedly did wrong"),
    ("capacity", "Poor work performance, ill health or injury"),
    ("operational_requirements", "Retrenchment: restructuring, closure or cost-cutting"),
    ("automatically_unfair", "Really because of a protected reason (pregnancy, union "
                             "activity, a protected strike, discrimination, whistle-blowing...)"),
    ("unprotected_strike", "Taking part in a strike that was not protected"),
    ("closed_shop", "Not belonging to the union in a closed shop agreement"),
    ("unknown", "I was not told why"),
]

GROUNDS: Options = [
    (AutoUnfairGround.PREGNANCY.value, "Pregnancy or intended pregnancy"),
    (AutoUnfairGround.DISCRIMINATION.value, "Discrimination: race, gender, age, disability, "
                                            "religion, sexual orientation, family responsibility..."),
    (AutoUnfairGround.FREEDOM_OF_ASSOCIATION.value, "Joining or being active in a union"),
    (AutoUnfairGround.PROTECTED_STRIKE.value, "Taking part in a protected strike or protest"),
    (AutoUnfairGround.REFUSED_STRIKE_WORK.value, "Refusing to do the work of striking workers"),
    (AutoUnfairGround.REFUSED_DEMAND.value, "Refusing to accept the employer's demand on pay "
                                            "or conditions"),
    (AutoUnfairGround.EXERCISED_LRA_RIGHT.value, "Using a right under the LRA or taking a case"),
    (AutoUnfairGround.TRANSFER_S197.value, "The business being transferred to a new owner"),
    (AutoUnfairGround.PROTECTED_DISCLOSURE.value, "Reporting wrongdoing (whistle-blowing)"),
]


def build_facts(ask: Asker) -> CaseFacts:
    what = ask.choose("What happened?", WHAT_HAPPENED)
    if what == "ulp":
        start = _date(ask, "On what date did the unfair treatment happen?")
        aware = ask.when("If you only found out later, on what date did you find out? "
                         "(leave blank if not)", required=False)
        return CaseFacts(dispute=DisputeKind.UNFAIR_LABOUR_PRACTICE, event_date=start,
                         became_aware_date=aware, **_council(ask))
    if what == "discrimination":
        start = _date(ask, "On what date did the discrimination happen?")
        return CaseFacts(dispute=DisputeKind.DISCRIMINATION_NOT_DISMISSAL, event_date=start)

    ground = None
    if what == "constructive":
        reason = ReasonGiven.CONSTRUCTIVE
        if ask.yes_no("Was it really because of a protected reason, such as pregnancy, "
                      "union activity, discrimination or whistle-blowing?", allow_unknown=False):
            ground = AutoUnfairGround(ask.choose("Which reason?", GROUNDS))
    else:
        reason = ReasonGiven(ask.choose("Why were you dismissed? (choose what you believe "
                                        "the real reason was)", REASONS))
        if reason is ReasonGiven.AUTOMATICALLY_UNFAIR:
            ground = AutoUnfairGround(ask.choose("Which reason?", GROUNDS))

    event = _date(ask, "On what date did you resign?" if what == "constructive"
                  else "On what date were you dismissed?")
    final = None
    if what != "constructive" and ask.yes_no("Did you appeal internally, or did the "
                                             "employer make a later final decision?",
                                             allow_unknown=False):
        final = ask.when("On what date was the final decision?", required=False)
        if final is not None and final < event:
            # Say so now, rather than after every remaining question.
            raise ValueError("the final decision cannot be earlier than the dismissal")

    extra: _ReasonFacts = {}
    if reason is ReasonGiven.OPERATIONAL_REQUIREMENTS:
        extra["employer_has_fewer_than_10_employees"] = ask.yes_no(
            "Does the employer have fewer than 10 employees?", allow_unknown=True)
        extra["only_this_employee_retrenched"] = ask.yes_no(
            "Were you the only person retrenched?", allow_unknown=True)
    if reason in (ReasonGiven.CONDUCT, ReasonGiven.CAPACITY):
        extra["probation"] = bool(ask.yes_no("Were you on probation?", allow_unknown=False))

    certificate = None
    if ask.yes_no("Has the CCMA or a bargaining council already issued a certificate "
                  "that the dispute is unresolved?", allow_unknown=False):
        certificate = _date(ask, "On what date was the certificate issued?")

    return CaseFacts(dispute=DisputeKind.DISMISSAL, reason=reason, auto_unfair_ground=ground,
                     event_date=event, final_decision_date=final,
                     certificate_date=certificate, **_council(ask), **extra)


def _date(ask: Asker, question: str) -> date:
    """A required date. TerminalAsker re-asks until it has one; any Asker must."""
    answer = ask.when(question, required=True)
    if answer is None:
        raise ValueError(f"a date is required: {question}")
    return answer


def _council(ask: Asker) -> _CouncilFacts:
    covered = ask.yes_no("Is your workplace covered by a bargaining council (for example "
                         "MIBCO, NBCRFLI, MEIBC)?", allow_unknown=True)
    if covered is None:
        return {"council": CouncilStatus.UNKNOWN}
    if not covered:
        return {"council": CouncilStatus.NO}
    return {"council": CouncilStatus.YES,
            "council_name": ask.text("Which council? (leave blank if unsure)")}


class TerminalAsker:
    """Asks on the terminal, re-asking until the answer is usable."""

    def __init__(self, prompt: Callable[..., str] = typer.prompt,
                 echo: Callable[[str], None] = typer.echo) -> None:
        self.prompt = prompt
        self.echo = echo

    def choose(self, question: str, options: Options) -> str:
        self.echo(question)
        for n, (_, label) in enumerate(options, 1):
            self.echo(f"  {n}. {label}")
        while True:
            answer = self.prompt("Number").strip()
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1][0]
            self.echo(f"Please enter a number from 1 to {len(options)}.")

    def when(self, question: str, required: bool) -> date | None:
        while True:
            answer = self.prompt(f"{question} (YYYY-MM-DD)", default="",
                                 show_default=False).strip()
            if not answer and not required:
                return None
            try:
                return date.fromisoformat(answer)
            except ValueError:
                self.echo("Please give the date as YYYY-MM-DD, e.g. 2026-09-30.")

    def yes_no(self, question: str, allow_unknown: bool) -> bool | None:
        hint = "y/n/?" if allow_unknown else "y/n"
        while True:
            answer = self.prompt(f"{question} [{hint}]").strip().lower()
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            if allow_unknown and answer in ("?", "dont know", "don't know", "unsure"):
                return None
            self.echo(f"Please answer {hint}.")

    def text(self, question: str) -> str | None:
        answer = self.prompt(question, default="", show_default=False).strip()
        return answer[:120] or None
