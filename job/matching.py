"""Matching without Claude: titles, exclude words, and cycle rules.

Phase 3 adds Claude classification on top of this for anything these plain
rules can't decide. Until then, undecided postings are stored with status
`unverified` so they still show up for Henry instead of being silently
dropped.
"""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass

# `job.config` puts `api/src` on `sys.path`; the fuzzy matcher lives there so
# the Worker runs the very same code (see `api/src/title_matching.py`).
import job.config  # noqa: F401
from title_matching import (  # noqa: E402
    CLASSIFY_SCORE, MATCH_SCORE, best_fuzzy_match, find_role_conflict, has_internship_wording, normalize_words,
)

_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


def _match_key(text: str) -> str:
    """Punctuation-insensitive form used only for title/alias matching, so
    "Associate Product Manager, Intern", "Associate Product Manager - Intern"
    and "Product Management Intern (Master's MBA)" match the configured text.
    Padded with spaces so a match always lands on whole words.
    """
    text = unicodedata.normalize("NFKC", html.unescape(text or "")).lower().replace("&", " and ")
    return " " + _NON_ALNUM_RE.sub(" ", text).strip() + " "


def normalize_title(text: str) -> str:
    """Normalize a posting's job title for matching the same role across
    sources -- whitespace-collapsed and lowercased, same as title/alias
    matching (`match_title`) uses internally.
    """
    return _normalize(text)


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
    method: str = "exact"  # "exact", "alias", or "fuzzy"
    score: int = 100
    # A title that is one word ("MBA") says nothing about the kind of role, so
    # the posting must still say it is an internship. Same for any fuzzy match.
    needs_internship_wording: bool = False

    @property
    def method_label(self) -> str:
        """What goes in `postings.match_method`: exact, alias, or fuzzy:<score>."""
        return f"fuzzy:{self.score}" if self.method == "fuzzy" else self.method


def match_title(text: str, active_titles: list[dict]) -> TitleMatch | None:
    """Match `text` (a posting's job title) against active titles and aliases.

    1. Exact: the title or an alias appears in the posting title (ignoring
       case and punctuation, whole words only), so "2027 Summer MBA Product
       Manager Intern - San Francisco" matches "Product Manager Intern".
    2. Fuzzy: words compared ignoring order and small wording differences,
       scoring 90 or more out of 100 (`api/src/title_matching.py`).

    A fuzzy score of 80 to 89 is not a match here; see `borderline_title`.
    """
    haystack = _match_key(text)
    if not haystack.strip():
        return None

    best: TitleMatch | None = None
    best_len = 0
    for entry in active_titles:
        if not entry.get("active", True):
            continue
        candidates = [entry["title"], *entry.get("aliases", [])]
        for candidate in candidates:
            needle = _match_key(candidate)
            if needle.strip() and needle in haystack:
                # Prefer the longest match, so a more specific title/alias
                # wins over a shorter one that happens to also appear.
                if best is None or len(needle) > best_len:
                    best = TitleMatch(
                        matched_title=entry["title"], matched_via=candidate,
                        method="exact" if candidate == entry["title"] else "alias",
                        needs_internship_wording=len(normalize_words(candidate)) == 1,
                    )
                    best_len = len(needle)
    if best is not None:
        return best

    fuzzy = best_fuzzy_match(text, active_titles)
    if fuzzy is None or fuzzy.score < MATCH_SCORE:
        return None
    if len(normalize_words(fuzzy.matched_via)) == 1:
        method = "exact" if fuzzy.matched_via == fuzzy.matched_title else "alias"
        return TitleMatch(fuzzy.matched_title, fuzzy.matched_via, method, 100, needs_internship_wording=True)
    return TitleMatch(fuzzy.matched_title, fuzzy.matched_via, "fuzzy", fuzzy.score, needs_internship_wording=True)


def borderline_title(text: str, active_titles: list[dict]) -> TitleMatch | None:
    """A fuzzy score of 80 to 89: not a match on its own, so classification
    decides. Returns the closest title, or None."""
    fuzzy = best_fuzzy_match(text, active_titles)
    if fuzzy and CLASSIFY_SCORE <= fuzzy.score < MATCH_SCORE:
        return TitleMatch(fuzzy.matched_title, fuzzy.matched_via, "fuzzy", fuzzy.score)
    return None


def _has_non_latin_letters(text: str) -> bool:
    return any(ch.isalpha() and not unicodedata.name(ch, "").startswith("LATIN") for ch in text or "")


# ---------------------------------------------------------------------------
# Cycle rules: Summer 2027 only.
# ---------------------------------------------------------------------------

# Summer 2027 internships are for the MBA Class of 2028 (graduating 2028), so
# "Class of 2028" is the right cycle. Only clearly earlier cycles are
# rejected: Summer 2026 and before, Class of 2027 and before ("MBA Class of
# 2027" is hiring for people who graduate a year before Henry does).
_REJECT_PATTERNS = [
    re.compile(r"\bclass of (?:19\d\d|20(?:[01]\d|2[0-7]))\b"),
    re.compile(r"\bgraduating (?:in |class of )?(?:19\d\d|20(?:[01]\d|2[0-7]))\b"),
    re.compile(r"\bsummer,? (?:19\d\d|20(?:[01]\d|2[0-6]))\b"),
    re.compile(r"\b20(?:[01]\d|2[0-6]) summer\b"),
]

_ACCEPT_PATTERNS = [
    re.compile(r"\bsummer,? 2027\b"),
    re.compile(r"\b2027 summer\b"),
    re.compile(r"\bsu'?27\b"),
    re.compile(r"\bclass of 2028\b"),
    re.compile(r"\bgraduating (?:in |class of )?2028\b"),
]


def cycle_status(text: str) -> str:
    """Return "match", "reject", or "unclear" for the Summer 2027 cycle."""
    haystack = _normalize(text)
    if not haystack:
        return "unclear"
    # Right-cycle wording wins over a stray earlier year, so a real posting
    # is never hidden because it also mentions last year's program.
    if any(p.search(haystack) for p in _ACCEPT_PATTERNS):
        return "match"
    if any(p.search(haystack) for p in _REJECT_PATTERNS):
        return "reject"
    return "unclear"


@dataclass
class MatchResult:
    # "match", "reject", "unclear" (the title matched, the cycle wording is
    # unclear), or "classify" (the rules found no match but Claude should look:
    # a fuzzy score of 80 to 89, or a title the English rules can't judge).
    decision: str
    matched_title: str | None = None
    reason: str | None = None  # why rejected, or why unclear
    match_method: str | None = None  # exact, alias, or fuzzy:<score>


def classify_posting(title_text: str, description_text: str, titles_doc: dict) -> MatchResult:
    """Apply title/alias, exclude word, role, and cycle rules to one posting.

    `description_text` is checked for exclude words and cycle wording in
    addition to the title, since postings often only mention "MBA Class of
    2027" in the body.

    Only a result with `matched_title` set may be saved (`job/save_posting.py`).
    """
    from job.config import active_titles as _active_titles

    combined = f"{title_text} {description_text}"

    exclude_word = find_exclude_word(combined, titles_doc.get("exclude_words", []))
    if exclude_word:
        return MatchResult(decision="reject", reason=f"excluded word '{exclude_word}'")

    active = _active_titles(titles_doc)
    title_match = match_title(title_text, active)
    if title_match is None:
        border = borderline_title(title_text, active)
        if border is not None:
            return MatchResult(
                decision="classify",
                reason=f"title close to '{border.matched_title}' (score {border.score}), needs classification",
            )
        if _has_non_latin_letters(title_text):
            return MatchResult(decision="classify", reason="title not in English, needs classification")
        return MatchResult(decision="reject", reason="no active title or alias matched")

    conflict = find_role_conflict(title_text, title_match.matched_via)
    if conflict:
        return MatchResult(
            decision="reject", matched_title=title_match.matched_title,
            reason=f"not a product or business role ({conflict})",
        )

    cycle = cycle_status(combined)
    if title_match.needs_internship_wording and cycle != "match" and not has_internship_wording(title_text):
        return MatchResult(
            decision="reject", matched_title=title_match.matched_title,
            reason="full-time role (no internship wording)",
        )
    method = title_match.method_label
    if cycle == "reject":
        return MatchResult(
            decision="reject", matched_title=title_match.matched_title, reason="wrong hiring cycle",
            match_method=method,
        )
    if cycle == "unclear":
        return MatchResult(
            decision="unclear", matched_title=title_match.matched_title, reason="cycle wording unclear",
            match_method=method,
        )

    return MatchResult(decision="match", matched_title=title_match.matched_title, match_method=method)


# ---------------------------------------------------------------------------
# Second look at a rejection (issue #60)
# ---------------------------------------------------------------------------

# Rejection reasons about things the plain rules now judge correctly: the
# location, the hiring cycle, or a title that was said not to match. A reason
# about anything else (an excluded word, a wrong role) is left alone.
_RECOVERABLE_REASON_RE = re.compile(
    r"location|hiring cycle|\bcycle\b|class of|graduating|\bsummer\b|no active title|"
    r"title (?:does not|doesn't|did not) match|\btitle matches\b",
    re.IGNORECASE,
)

# "Title matches Product Manager Intern but location ..." -- the reason
# admits the title is right, so it can't also be a reason to reject.
_CONTRADICTION_RE = re.compile(r"\btitle (?:matches|match|is a match)\b.*\bbut\b", re.IGNORECASE | re.DOTALL)


_TITLE_REASON_RE = re.compile(r"\btitle\b|no active", re.IGNORECASE)


@dataclass
class Reconsidered:
    matched_title: str
    location_match: str  # "match", "outside", or "unclear"
    match_method: str = "classification"  # how the title matched: exact, alias, fuzzy:<score>, or classification


def is_recoverable_reason(reason: str | None) -> bool:
    return bool(reason and _RECOVERABLE_REASON_RE.search(reason))


def reconsider_rejection(
    reason: str | None, title_text: str, location_text: str, titles_doc: dict, locations_setting: dict,
) -> Reconsidered | None:
    """Re-judges a rejection that was about location, hiring cycle, or an
    unmatched title under the current rules. Returns what to keep the posting
    as, or None when the rejection stands.

    A missing or unrecognizable location never rejects: it is kept with
    location_match "unclear". A location that is clearly outside the chosen
    countries is still saved (hidden by the dashboard), not dropped.
    """
    from job.config import active_titles as _active_titles
    from job.locations import location_match as _location_match

    if not is_recoverable_reason(reason):
        return None
    if find_exclude_word(title_text, titles_doc.get("exclude_words", [])):
        return None

    active = _active_titles(titles_doc)
    title_match = match_title(title_text, active)
    contradiction = bool(_CONTRADICTION_RE.search(reason or ""))
    named_in_reason = False
    if title_match is None and contradiction:
        # The reason names the matching title even if the stored title text
        # is different from it.
        title_match = match_title(reason or "", active)
        named_in_reason = title_match is not None
    if title_match is None:
        # No active title or alias matched, so there is nothing to keep the
        # posting under: a location or cycle reason never overrides a failed
        # title match (issue #68).
        return None
    matched_title = title_match.matched_title
    if find_role_conflict(title_text, title_match.matched_via):
        return None
    if title_match.needs_internship_wording and not has_internship_wording(title_text):
        return None

    if cycle_status(title_text) == "reject":
        return None

    loc = _location_match(location_text, locations_setting)
    if loc == "outside":
        # A contradictory reason can't be trusted to mean "outside"; any
        # other outside-location rejection is correct.
        if not contradiction:
            return None
        loc = "unclear"
    elif contradiction and loc != "match":
        loc = "unclear"
    method = "classification" if named_in_reason else title_match.method_label
    return Reconsidered(matched_title=matched_title, location_match=loc, match_method=method)
