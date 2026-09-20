"""Pruning: archive postings that are gone, expired, or the wrong cycle.

Archiving only ever sets `archived_at` and `archive_reason`; nothing is
deleted (Henry's own "Delete" button in the dashboard is the only other
thing that archives a posting, and even that keeps the row so he can
restore it).
"""
from __future__ import annotations

from dataclasses import dataclass

WRONG_CYCLE_REASON = "Wrong hiring cycle (not Summer 2027)."
DEFAULT_CLOSED_REASON = "No longer open."


@dataclass
class PruneDecision:
    should_archive: bool
    reason: str | None = None


def decide_prune(*, status: str, note: str | None = None, cycle_status: str | None = None) -> PruneDecision:
    """Decide whether a posting should be archived, and why.

    `status` is the outcome of verification (open/closing/closed/unverified).
    `cycle_status` is `job.matching.cycle_status`'s answer, when known --
    "reject" always prunes, regardless of open/closed status, since a wrong
    cycle posting was never a match in the first place.
    """
    if cycle_status == "reject":
        return PruneDecision(should_archive=True, reason=WRONG_CYCLE_REASON)
    if status == "closed":
        return PruneDecision(should_archive=True, reason=note or DEFAULT_CLOSED_REASON)
    return PruneDecision(should_archive=False)
