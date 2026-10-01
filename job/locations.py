"""Location parsing from feed fields.

Feeds describe locations as free text ("San Francisco, CA", "Tokyo, Japan",
"Remote - US", "London, UK"). This turns that text into one of match,
outside, or unclear against the Locations setting (countries plus a remote
switch), without calling any geocoding service.
"""
from __future__ import annotations

import re

_WS_RE = re.compile(r"\s+")

_US_STATE_ABBRS = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia",
    "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt",
    "va", "wa", "wv", "wi", "wy", "dc",
}

# Abbreviations that are also ordinary English words ("in", "or", "me", ...).
# They only count as a state when written in capitals ("Portland, OR"), so a
# lowercase "in" or "or" in free text never turns a location into the US.
_AMBIGUOUS_ABBRS = {"in", "or", "me", "hi", "id", "ok", "de", "la", "ma", "oh", "al", "ar", "co"}

_US_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming", "district of columbia",
]

# Keyword -> ISO 3166-1 alpha-2 country code. Checked as whole words/phrases
# against the normalized location text.
_COUNTRY_KEYWORDS: list[tuple[str, str]] = [
    ("united states", "US"), ("usa", "US"), ("u.s.a", "US"), ("u.s.", "US"), ("us", "US"),
    ("japan", "JP"), ("jpn", "JP"), ("jp", "JP"),
    ("tokyo", "JP"), ("osaka", "JP"), ("yokohama", "JP"), ("kyoto", "JP"), ("nagoya", "JP"),
    ("united kingdom", "GB"), ("uk", "GB"),
    ("canada", "CA"),
    ("germany", "DE"),
    ("france", "FR"),
    ("india", "IN"),
    ("china", "CN"),
    ("singapore", "SG"),
    ("australia", "AU"),
    ("ireland", "IE"),
    ("netherlands", "NL"),
    ("south korea", "KR"), ("korea", "KR"),
    ("brazil", "BR"),
    ("mexico", "MX"),
    ("switzerland", "CH"),
]

_REMOTE_RE = re.compile(r"\bremote\b")


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


def _has_word(haystack: str, word: str) -> bool:
    return re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", haystack) is not None


def detect_countries(location_text: str) -> set[str]:
    """Best-effort set of ISO country codes mentioned in the location text."""
    haystack = _normalize(location_text)
    found: set[str] = set()

    # US state names first, blanked out afterwards so "New Mexico" doesn't
    # also read as the country Mexico.
    for name in _US_STATE_NAMES:
        pattern = r"(?<![a-z])" + re.escape(name) + r"(?![a-z])"
        if re.search(pattern, haystack):
            found.add("US")
            haystack = re.sub(pattern, " ", haystack)

    for keyword, code in _COUNTRY_KEYWORDS:
        if _has_word(haystack, keyword):
            found.add(code)

    if "US" not in found:
        # Two-letter abbreviations, with or without commas: "Austin, TX",
        # "Austin TX Warren MI". Read from the original text so the
        # ambiguous ones can require capitals.
        for token in re.split(r"[^A-Za-z]+", location_text or ""):
            lower = token.lower()
            if len(token) != 2 or lower not in _US_STATE_ABBRS:
                continue
            if lower in _AMBIGUOUS_ABBRS and not token.isupper():
                continue
            found.add("US")
            break
    return found


def location_match(location_text: str, settings: dict) -> str:
    """Return "match", "outside", or "unclear" against the Locations setting."""
    haystack = _normalize(location_text)
    countries = set(settings.get("countries") or [])
    include_remote = bool(settings.get("include_remote"))

    is_remote = bool(_REMOTE_RE.search(haystack))
    if is_remote and include_remote:
        return "match"

    detected = detect_countries(location_text)
    if not detected:
        # A bare "Remote" with remote not enabled is a definite "outside",
        # not unclear -- Henry has explicitly not turned it on.
        if is_remote:
            return "outside"
        return "unclear"

    if detected & countries:
        return "match"
    return "outside"
