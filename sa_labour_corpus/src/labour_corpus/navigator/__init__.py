"""Dismissal Navigator: the product layer over the corpus.

Turns a dismissed employee's stated facts into deadlines, a forum route, a
remedy ceiling and a fairness checklist, each tied to statutory wording that is
re-found in the held statutes at assessment time. See
``docs/navigator-architecture.md``.
"""

from .engine import Navigator
from .models import Assessment, CaseFacts

__all__ = ["Assessment", "CaseFacts", "Navigator"]
