"""Pure logic for companies: name matching, feed detection, and paste parsing.

No FastAPI or D1 imports here, so this module is safe to import from
scripts/seed.py and from tests without pulling in the Worker runtime.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

_PAREN_RE = re.compile(r"\([^)]*\)")
_PUNCT_RE = re.compile(r"[.,]")
_WS_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://\S+")

# Trailing words stripped one at a time (outer to inner) so "Walmart Global
# Tech" and "Arm Holdings" normalize the same as "Walmart" and "Arm".
_TRAILING_STOPWORDS = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "holdings",
    "holding",
    "technologies",
    "technology",
    "tech",
    "global",
    "ltd",
    "limited",
    "plc",
    "group",
    "platforms",
    "electronics",
}


def normalize_company_name(name: str) -> str:
    """Normalize a company name for matching, e.g. 'Arm Holdings' == 'Arm'."""
    text = _PAREN_RE.sub(" ", name or "")
    text = _PUNCT_RE.sub("", text)
    text = text.lower().strip()
    tokens = _WS_RE.split(text) if text else []
    while tokens and tokens[-1] in _TRAILING_STOPWORDS:
        tokens.pop()
    return " ".join(tokens)


# (feed_type, matcher) -- matcher returns the feed_id, or "" if there isn't one.
def _greenhouse_id(url):
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[0] if parts else ""


def _lever_id(url):
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[0] if parts else ""


def _ashby_id(url):
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[0] if parts else ""


def _smartrecruiters_id(url):
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[0] if parts else ""


def _workday_id(url):
    host = urlparse(url).netloc
    return host.split(".")[0] if host else ""


_FEED_RULES = [
    ("greenhouse", ("greenhouse.io",), _greenhouse_id),
    ("lever", ("lever.co",), _lever_id),
    ("ashby", ("ashbyhq.com",), _ashby_id),
    ("smartrecruiters", ("smartrecruiters.com",), _smartrecruiters_id),
    ("workday", ("myworkdayjobs.com",), _workday_id),
    ("amazon_jobs", ("amazon.jobs",), lambda url: ""),
]


def detect_feed(url: str | None) -> tuple[str | None, str | None]:
    """Detect a known feed type (and its id) from a careers link, if any."""
    if not url:
        return None, None
    host = urlparse(url).netloc.lower()
    if not host:
        return None, None
    for feed_type, domains, id_fn in _FEED_RULES:
        if any(host == d or host.endswith("." + d) for d in domains):
            return feed_type, (id_fn(url) or None)
    return None, None


@dataclass
class PastedCompany:
    raw: str
    name: str | None
    url: str | None
    feed_type: str | None
    feed_id: str | None
    match: str  # "new", "existing", "duplicate", "unclear"


def parse_pasted_companies(text: str, existing_names: "list[str] | set[str]") -> list[PastedCompany]:
    """Parse a pasted list of companies (one per line) for the preview step.

    Each line may be just a name, a name with a careers link, or just a
    link. Lines are classified as new, already on the list ("existing"),
    a duplicate of an earlier line in the same paste, or unclear (no
    company name could be read from the line).
    """
    existing_normalized = {normalize_company_name(n) for n in existing_names}
    seen_normalized: set[str] = set()
    results: list[PastedCompany] = []

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        url_match = _URL_RE.search(line)
        url = url_match.group(0).rstrip(").,") if url_match else None
        name_part = line
        if url_match:
            name_part = (line[: url_match.start()] + line[url_match.end() :]).strip()
        name_part = name_part.strip(" -|,\t")
        name = name_part or None

        if not name:
            results.append(PastedCompany(raw=raw_line, name=None, url=url, feed_type=None, feed_id=None, match="unclear"))
            continue

        normalized = normalize_company_name(name)
        feed_type, feed_id = detect_feed(url)
        if normalized in seen_normalized:
            match = "duplicate"
        elif normalized in existing_normalized:
            match = "existing"
        else:
            match = "new"
            seen_normalized.add(normalized)

        results.append(
            PastedCompany(raw=raw_line, name=name, url=url, feed_type=feed_type, feed_id=feed_id, match=match)
        )

    return results
