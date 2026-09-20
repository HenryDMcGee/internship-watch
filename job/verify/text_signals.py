"""Shared "is this posting still open" text heuristics.

Used by the structured-data, browser, and mirror-site checks so all three
agree on what counts as a clear signal versus something to leave unclear
for the next step (or for phase 3's Claude classification of unclear
pages).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_CLOSED_PHRASES = [
    "no longer accepting applications",
    "no longer available",
    "this position has been filled",
    "position has been filled",
    "posting has expired",
    "job has expired",
    "this job is no longer",
    "job not found",
    "requisition is no longer",
    "we're sorry, this position is closed",
    "this position is closed",
]

_OPEN_PHRASES = [
    "apply now",
    "submit application",
    "apply for this job",
    "apply for this position",
]


@dataclass
class VerifyDecision:
    decision: str  # "open", "closed", or "unclear"
    note: str | None = None


def classify_text(text: str) -> VerifyDecision:
    haystack = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not haystack:
        return VerifyDecision(decision="unclear", note="No page text to check.")
    for phrase in _CLOSED_PHRASES:
        if phrase in haystack:
            return VerifyDecision(decision="closed", note=f"Page text says \"{phrase}\".")
    for phrase in _OPEN_PHRASES:
        if phrase in haystack:
            return VerifyDecision(decision="open", note=f"Page text says \"{phrase}\".")
    return VerifyDecision(decision="unclear", note="Page text didn't clearly say open or closed.")
