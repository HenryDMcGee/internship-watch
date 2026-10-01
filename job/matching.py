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


def match_title(text: str, active_titles: list[dict]) -> TitleMatch | None:
    """Match `text` (a posting's job title) against active titles and aliases.

    Case-insensitive substring match: a posting titled "2027 Summer MBA
    Product Manager Intern - San Francisco" matches the active title
    "Product Manager Intern" because that phrase appears in it.
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
                    best = TitleMatch(matched_title=entry["title"], matched_via=candidate)
                    best_len = len(needle)
    return best


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
    if title_match is None and contradiction:
        # The reason names the matching title even if the stored title text
        # is different from it.
        title_match = match_title(reason or "", active)
    matched_title = title_match.matched_title if title_match else None
    if matched_title is None and not _TITLE_REASON_RE.search(reason or "") and re.search(
        r"\bintern", title_text or "", re.IGNORECASE,
    ):
        # The rejection was about location or cycle, not the title, so
        # Claude had already accepted the title as a clear equivalent of an
        # active one (e.g. "Company Intern Product Manager"). Keep the
        # posting's own title as its matched title so it shows up.
        matched_title = (title_text or "").strip()
    if matched_title is None:
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
    return Reconsidered(matched_title=matched_title, location_match=loc)
