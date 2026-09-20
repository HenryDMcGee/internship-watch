"""Matching without Claude: titles, exclude words, and cycle rules.

Phase 3 adds Claude classification on top of this for anything these plain
rules can't decide. Until then, undecided postings are stored with status
`unverified` so they still show up for Henry instead of being silently
dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


def find_exclude_word(text: str, exclude_words: list[str]) -> str | None:
    """Return the first exclude word found in `text` (case-insensitive), if any."""
    haystack = _normalize(text)
    for word in exclude_words:
        if word.strip().lower() in haystack:
            return word
    return None


@dataclass
class TitleMatch:
    matched_title: str  # the canonical title from config/titles.json
    matched_via: str  # the exact title or alias text that matched


def match_title(text: str, active_titles: list[dict]) -> TitleMatch | None:
    """Match `text` (a posting's job title) against active titles and aliases.

    Case-insensitive substring match: a posting titled "2027 Summer MBA
    Product Manager Intern - San Francisco" matches the active title
    "Product Manager Intern" because that phrase appears in it.
    """
    haystack = _normalize(text)
    if not haystack:
        return None

    best: TitleMatch | None = None
    for entry in active_titles:
        if not entry.get("active", True):
            continue
        candidates = [entry["title"], *entry.get("aliases", [])]
        for candidate in candidates:
            needle = _normalize(candidate)
            if needle and needle in haystack:
                # Prefer the longest match, so a more specific title/alias
                # wins over a shorter one that happens to also appear.
                if best is None or len(needle) > len(_normalize(best.matched_via)):
                    best = TitleMatch(matched_title=entry["title"], matched_via=candidate)
    return best


# ---------------------------------------------------------------------------
# Cycle rules: Summer 2027 only.
# ---------------------------------------------------------------------------

# Phrases that mark a posting as belonging to a different hiring cycle, even
# though it might otherwise match a title. "MBA Class of 2027" means
# students who *graduate* in 2027 -- a full-time hire, not the class doing a
# summer 2027 internship (who graduate in 2028).
_REJECT_PATTERNS = [
    re.compile(r"\bclass of \d{4}\b"),  # "class of YYYY" is full-time hiring wording, any year
    re.compile(r"\bsummer 20(2[0-6]|28|29)\b"),  # any summer except 2027
    re.compile(r"\bfull.?time\b"),
]

_ACCEPT_PATTERNS = [
    re.compile(r"\bsummer 2027\b"),
    re.compile(r"\b2027 summer\b"),
    re.compile(r"\bsu'?27\b"),
]


def cycle_status(text: str) -> str:
    """Return "match", "reject", or "unclear" for the Summer 2027 cycle."""
    haystack = _normalize(text)
    if not haystack:
        return "unclear"
    if any(p.search(haystack) for p in _REJECT_PATTERNS):
        return "reject"
    if any(p.search(haystack) for p in _ACCEPT_PATTERNS):
        return "match"
    return "unclear"


@dataclass
class MatchResult:
    decision: str  # "match", "reject", or "unclear"
    matched_title: str | None = None
    reason: str | None = None  # why rejected, or why unclear


def classify_posting(title_text: str, description_text: str, titles_doc: dict) -> MatchResult:
    """Apply title/alias, exclude word, and cycle rules to one posting.

    `description_text` is checked for exclude words and cycle wording in
    addition to the title, since postings often only mention "MBA Class of
    2027" in the body.
    """
    from job.config import active_titles as _active_titles

    combined = f"{title_text} {description_text}"

    exclude_word = find_exclude_word(combined, titles_doc.get("exclude_words", []))
    if exclude_word:
        return MatchResult(decision="reject", reason=f"excluded word '{exclude_word}'")

    title_match = match_title(title_text, _active_titles(titles_doc))
    if title_match is None:
        return MatchResult(decision="reject", reason="no active title or alias matched")

    cycle = cycle_status(combined)
    if cycle == "reject":
        return MatchResult(decision="reject", matched_title=title_match.matched_title, reason="wrong hiring cycle")
    if cycle == "unclear":
        return MatchResult(decision="unclear", matched_title=title_match.matched_title, reason="cycle wording unclear")

    return MatchResult(decision="match", matched_title=title_match.matched_title)
