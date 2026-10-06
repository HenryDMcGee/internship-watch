"""Fuzzy title matching (issue #68). Pure Python, standard library only, so the
same code runs in the daily job and in the Cloudflare Worker.

Both the posting title and every configured title or alias are normalized the
same way, then compared as sets of words, ignoring word order:

  * lowercase; punctuation, brackets and job ids dropped;
  * common abbreviations expanded (mgmt, PM, sr, assoc, tech, ops);
  * "intern", "internship", "summer intern", "summer analyst",
    "summer associate" and "co-op" all become the one word "intern";
  * filler such as "2027", "summer", "graduate" and "remote" dropped
    ("MBA" is kept, it is a title in its own right);
  * one small typo per word is allowed (words of five letters or more).

The score is out of 100. 90 or more matches the title; 80 to 89 is not a match
on its own and is sent to classification; below 80 is no match. On top of the
score, a posting must contain every core word of the title it is compared to,
and must not contain an excluded word.
"""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass

MATCH_SCORE = 90
CLASSIFY_SCORE = 80

_ABBREVIATIONS = {
    "mgmt": "management",
    "mgt": "management",
    "pm": "product manager",
    "sr": "senior",
    "assoc": "associate",
    "tech": "technical",
    "ops": "operations",
}

_FILLER = {
    "summer", "graduate", "remote", "the", "a", "an", "and", "of", "for", "in", "at", "to", "with",
}

# Words that say "this is a role of some kind" but not which one, so a posting
# can't match a title on them alone.
_GENERIC = {"intern", "manager", "management", "senior", "associate", "group"}

_SUMMER_ROLE_RE = re.compile(r"\bsummer\s+(?:internships?|interns?|analyst|associate)\b|\bco[\s-]?ops?\b")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_JOB_ID_WORDS = {"id", "req", "reqid", "requisition", "jr", "job"}


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(text or "")).lower()
    return text.replace("&", " and ")


def normalize_words(text: str) -> list[str]:
    """The comparable words of a title, in order, without repeats."""
    text = _clean(text)
    # "summer intern", "summer analyst", "summer associate", "co-op" -> "intern"
    text = _SUMMER_ROLE_RE.sub(" intern ", text)
    text = re.sub(r"\binternships?\b|\binterns\b", " intern ", text)
    raw = [w for w in _NON_ALNUM_RE.split(text) if w]

    words: list[str] = []
    for i, word in enumerate(raw):
        nxt = raw[i + 1] if i + 1 < len(raw) else ""
        if word in _JOB_ID_WORDS and any(ch.isdigit() for ch in nxt):
            continue  # "req 12345", "job id 12345"
        if any(ch.isdigit() for ch in word):
            continue  # job ids and years ("R12345", "2027")
        words.extend(_ABBREVIATIONS.get(word, word).split())

    out: list[str] = []
    for word in words:
        if word in _FILLER or word in out:
            continue
        out.append(word)
    return out


def words_close(a: str, b: str) -> bool:
    """Equal, or one typo apart (one letter added, dropped, changed, or two
    neighbours swapped). Short words must be exact, so "mba" never matches
    "mbaraki" or "mbo"."""
    if a == b:
        return True
    if len(a) < 5 or len(b) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1:]


def _core_words(words: list[str]) -> list[str]:
    core = [w for w in words if w not in _GENERIC]
    return core or list(words)


def _has_word(words: list[str], word: str, *, exact: bool = False) -> bool:
    return any(w == word if exact else words_close(w, word) for w in words)


def has_internship_wording(text: str) -> bool:
    return "intern" in normalize_words(text)


def word_in_text(text: str, word: str) -> bool:
    """Whole-word, case-insensitive check; "mba" is not found in "Mbaraki"."""
    return re.search(r"(?<![a-z0-9])" + re.escape(word.strip().lower()) + r"(?![a-z0-9])", _clean(text)) is not None


def score_title(posting_words: list[str], candidate: str) -> int | None:
    """0-100 for how well a posting's words fit one configured title or alias,
    or None when a hard rule fails (a core word is missing)."""
    cand = normalize_words(candidate)
    if not cand or not posting_words:
        return None

    if len(cand) == 1:
        # A one-word title ("MBA") matches any posting containing that whole word.
        return 100 if cand[0] in posting_words else None

    unused = list(posting_words)
    matched: list[str] = []
    for word in cand:
        hit = next((p for p in unused if words_close(p, word)), None)
        if hit is not None:
            unused.remove(hit)
            matched.append(word)
    if any(core not in matched for core in _core_words(cand)):
        return None

    recall = len(matched) / len(cand)
    precision = len(matched) / len(posting_words)
    return round(100 * (0.7 * recall + 0.3 * precision))


@dataclass
class FuzzyResult:
    matched_title: str
    matched_via: str
    score: int


def best_fuzzy_match(text: str, active_titles: list[dict]) -> FuzzyResult | None:
    """The best-scoring active title or alias for `text`, at any score of 80 or
    more. The caller decides what a score means (see MATCH_SCORE)."""
    posting_words = normalize_words(text)
    best: FuzzyResult | None = None
    for entry in active_titles:
        if not entry.get("active", True):
            continue
        for candidate in (entry["title"], *entry.get("aliases", [])):
            score = score_title(posting_words, candidate)
            if score is None or score < CLASSIFY_SCORE:
                continue
            if best is None or score > best.score:
                best = FuzzyResult(matched_title=entry["title"], matched_via=candidate, score=score)
    return best


# Roles that are clearly not product, strategy or operations work. A posting
# with one of these words that the matched title itself doesn't have is
# rejected even when the rest of the title looks close.
ROLE_CONFLICT_WORDS = {
    "engineer": "engineering", "engineers": "engineering", "engineering": "engineering",
    "lab": "lab", "labs": "lab", "laboratory": "lab", "validation": "lab",
    "hardware": "hardware", "silicon": "hardware", "firmware": "hardware",
    "design": "design", "designer": "design", "designers": "design",
    "sales": "sales", "undergraduate": "undergraduate", "undergrad": "undergraduate",
    "bachelors": "undergraduate", "bachelor's": "undergraduate",
}


def find_role_conflict(text: str, matched_title_text: str = "") -> str | None:
    """The first clearly off-role word in `text` (engineering, lab, hardware,
    design, sales, undergraduate-only) that the matched title doesn't contain."""
    allowed = {ROLE_CONFLICT_WORDS.get(w) for w in re.findall(r"[a-z']+", _clean(matched_title_text))}
    for word in re.findall(r"[a-z']+", _clean(text)):
        kind = ROLE_CONFLICT_WORDS.get(word)
        if kind and kind not in allowed:
            return kind
    return None
