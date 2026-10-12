"""Referral time limits, computed conservatively.

Days are calendar days, counted from the day after the trigger date. The
navigator never extends a deadline that lands on a weekend or public holiday:
whether an extension applies depends on rules this corpus does not hold, and a
missed deadline costs the employee far more than filing a day early. Instead it
says so in the deadline's note.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from .models import Deadline, DeadlineStatus, ProvisionRef

DUE_SOON_DAYS = 7

WEEKEND_NOTE = (
    "This date falls on a weekend. Do not assume the deadline moves to the next "
    "working day: refer before it."
)


def add_days(start: date, days: int) -> date:
    return start + timedelta(days=days)


def add_months(start: date, months: int) -> date:
    """Same day ``months`` later, clipped to the last day of a shorter month."""
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def make_deadline(step: str, due: date, today: date, basis: ProvisionRef,
                  note: str = "") -> Deadline:
    days_left = (due - today).days
    if days_left < 0:
        status = DeadlineStatus.EXPIRED
    elif days_left <= DUE_SOON_DAYS:
        status = DeadlineStatus.DUE_SOON
    else:
        status = DeadlineStatus.OPEN
    notes = [n for n in (note, WEEKEND_NOTE if due.weekday() >= 5 else "") if n]
    return Deadline(step=step, due=due, days_left=days_left, status=status,
                    basis=basis, note=" ".join(notes))


def pending_deadline(step: str, basis: ProvisionRef, note: str) -> Deadline:
    """A deadline whose clock has not started yet."""
    return Deadline(step=step, due=None, days_left=None,
                    status=DeadlineStatus.NOT_STARTED, basis=basis, note=note)
