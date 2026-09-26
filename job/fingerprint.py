"""Fingerprints every posting and every rejected result so a repost under a
new URL, or a title with different punctuation, is recognized as the same
job instead of saved -- or reclassified with Claude -- a second time
(issue #49: an NVIDIA role was found again at a different URL and saved as
a second row).

Checked in this priority order, for a posting or a rejected result alike:
  1. job_id -- the company's own hiring-system id (Workday requisition,
     Greenhouse gh_jid, Lever posting id, Amazon job id), read straight off
     the URL.
  2. clean_url -- lowercase host, tracking params stripped, no trailing
     slash or fragment.
  3. title_key -- company + normalized title + normalized location (location
     is part of the key so the same title in two cities stays separate).

Two results that both carry a job_id and disagree are always different
jobs, even when their titles match -- that's how a repost under a new
requisition id is treated as new rather than matched to the old one.

`match_incoming` is the single entry point the feed path (`job/run.py`),
discovery, and classification (`job/agent/pipeline.py`) all call before any
Claude call or insert. It checks the `posting_fingerprints` table first,
and falls back to `job.dedup.find_duplicate_posting`'s URL/title scan of
`postings` itself for rows created before this table existed (or by tests
that seed a posting directly) -- a match found that way is fingerprinted on
the spot so later lookups no longer need the fallback.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from job import config  # noqa: F401 -- adds api/src to sys.path for the imports below
from job.dedup import find_duplicate_posting

from companies import normalize_company_name  # noqa: E402
from sql_helpers import in_json  # noqa: E402

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "source", "ref", "referrer", "refid", "gh_src", "fbclid", "gclid",
}

_WORKDAY_JOB_ID_RE = re.compile(r"(JR\d+)\b", re.IGNORECASE)
_LEVER_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
_AMAZON_JOB_ID_RE = re.compile(r"/jobs/(\d+)")

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Pure normalization
# ---------------------------------------------------------------------------

def clean_url(url: str | None) -> str | None:
    """Lowercase host, tracking params removed, no trailing slash or fragment."""
    if not url or not url.strip():
        return None
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    query = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    path = parsed.path.rstrip("/")
    return urlunparse((
        parsed.scheme.lower(), parsed.netloc.lower(), path, "", urlencode(query), "",
    ))


def extract_job_id(url: str | None) -> str | None:
    """The company's own hiring-system id, read off a known feed's URL shape."""
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    query = dict(parse_qsl(parsed.query))

    if "gh_jid" in query:
        return f"greenhouse:{query['gh_jid']}"
    if "myworkdayjobs.com" in host:
        match = _WORKDAY_JOB_ID_RE.search(url)
        if match:
            return f"workday:{match.group(1).upper()}"
    if "amazon.jobs" in host:
        match = _AMAZON_JOB_ID_RE.search(parsed.path)
        if match:
            return f"amazon:{match.group(1)}"
    if "lever.co" in host:
        match = _LEVER_UUID_RE.search(url)
        if match:
            return f"lever:{match.group(0).lower()}"
    return None


def _normalize_words(text: str) -> str:
    text = _PUNCT_RE.sub(" ", text or "")
    return _WS_RE.sub(" ", text).strip().lower()


def title_key(company_name: str, title: str, location_text: str = "") -> str:
    """Company + normalized title + normalized location, so 'Summer 2027' /
    '(MBA)' / dashes vs. commas don't create a second key for the same role,
    while the same title in two different cities stays separate.
    """
    company = normalize_company_name(company_name or "")
    return "|".join([company, _normalize_words(title), _normalize_words(location_text)])


@dataclass
class Fingerprint:
    job_id: str | None
    clean_url: str | None
    title_key: str


def compute_fingerprint(
    *, company_name: str, title: str, location_text: str = "", url: str | None = None,
    external_id: str | None = None,
) -> Fingerprint:
    job_id = extract_job_id(url) or (f"ext:{external_id}" if external_id else None)
    return Fingerprint(job_id=job_id, clean_url=clean_url(url), title_key=title_key(company_name, title, location_text))


def settings_version(titles_doc: dict, locations_setting: dict) -> str:
    """A hash of the active titles, their aliases, the exclude words, and
    the location settings -- the inputs that decide whether a result
    matches. Changing any of these is what re-judges a remembered
    rejection; changing anything else (budgets, models) must not.
    """
    active = sorted(
        (
            {"title": t["title"], "aliases": sorted(t.get("aliases", []))}
            for t in (titles_doc.get("titles") or []) if t.get("active")
        ),
        key=lambda t: t["title"],
    )
    material = {
        "titles": active,
        "exclude_words": sorted(titles_doc.get("exclude_words", []) or []),
        "countries": sorted((locations_setting or {}).get("countries") or []),
        "include_remote": bool((locations_setting or {}).get("include_remote")),
    }
    encoded = json.dumps(material, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ---------------------------------------------------------------------------
# posting_fingerprints lookups
# ---------------------------------------------------------------------------

def _select_match(fp: Fingerprint, candidates: list[dict]) -> dict | None:
    by_job = None
    by_url = None
    by_title = None
    for row in candidates:
        row_job = row.get("job_id")
        if fp.job_id and row_job == fp.job_id:
            by_job = by_job or row
            continue
        if fp.job_id and row_job and row_job != fp.job_id:
            # Both sides have a job id and they disagree -- a repost under a
            # new requisition id, never the same posting even if the URL or
            # title also happens to match.
            continue
        if fp.clean_url and row.get("clean_url") == fp.clean_url:
            by_url = by_url or row
            continue
        if row.get("title_key") == fp.title_key:
            by_title = by_title or row
    return by_job or by_url or by_title


async def find_matches(db, fingerprints: list["Fingerprint"]) -> list[dict | None]:
    """Matches many fingerprints against `posting_fingerprints` in one
    query, safe at any batch size under D1's 100-bound-parameter limit --
    `sql_helpers.in_json` takes each list of values as a single JSON
    parameter rather than one bound `?` per value.
    """
    if not fingerprints:
        return []

    job_ids = sorted({fp.job_id for fp in fingerprints if fp.job_id})
    clean_urls = sorted({fp.clean_url for fp in fingerprints if fp.clean_url})
    title_keys = sorted({fp.title_key for fp in fingerprints if fp.title_key})

    clauses, params = [], []
    if job_ids:
        clause, param = in_json("job_id", job_ids)
        clauses.append(clause)
        params.append(param)
    if clean_urls:
        clause, param = in_json("clean_url", clean_urls)
        clauses.append(clause)
        params.append(param)
    if title_keys:
        clause, param = in_json("title_key", title_keys)
        clauses.append(clause)
        params.append(param)

    candidates: list[dict] = []
    if clauses:
        where = " OR ".join(clauses)
        candidates = await db.all(f"SELECT * FROM posting_fingerprints WHERE {where}", *params)

    return [_select_match(fp, candidates) for fp in fingerprints]


async def find_match(db, fp: Fingerprint) -> dict | None:
    matches = await find_matches(db, [fp])
    return matches[0] if matches else None


async def record_posting(
    db, fp: Fingerprint, *, posting_id: int, settings_version: str, source: str, now_iso: str,
    existing_id: int | None = None,
) -> int:
    if existing_id is not None:
        await db.run(
            """UPDATE posting_fingerprints SET job_id = ?, clean_url = ?, title_key = ?, posting_id = ?,
               rejection_reason = NULL, settings_version = ?, last_seen = ?, last_source = ? WHERE id = ?""",
            fp.job_id, fp.clean_url, fp.title_key, posting_id, settings_version, now_iso, source, existing_id,
        )
        return existing_id
    return await db.run(
        """INSERT INTO posting_fingerprints
           (job_id, clean_url, title_key, posting_id, settings_version, first_seen, last_seen, last_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        fp.job_id, fp.clean_url, fp.title_key, posting_id, settings_version, now_iso, now_iso, source,
    )


async def record_rejection(
    db, fp: Fingerprint, *, reason: str, settings_version: str, source: str, now_iso: str,
    existing_id: int | None = None,
) -> int:
    if existing_id is not None:
        await db.run(
            """UPDATE posting_fingerprints SET job_id = ?, clean_url = ?, title_key = ?, posting_id = NULL,
               rejection_reason = ?, settings_version = ?, last_seen = ?, last_source = ? WHERE id = ?""",
            fp.job_id, fp.clean_url, fp.title_key, reason, settings_version, now_iso, source, existing_id,
        )
        return existing_id
    return await db.run(
        """INSERT INTO posting_fingerprints
           (job_id, clean_url, title_key, rejection_reason, settings_version, first_seen, last_seen, last_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        fp.job_id, fp.clean_url, fp.title_key, reason, settings_version, now_iso, now_iso, source,
    )


async def touch(db, fingerprint_id: int, *, source: str, now_iso: str) -> None:
    await db.run(
        "UPDATE posting_fingerprints SET last_seen = ?, last_source = ? WHERE id = ?",
        now_iso, source, fingerprint_id,
    )


# ---------------------------------------------------------------------------
# The single entry point the feed, discovery, and classification paths use
# ---------------------------------------------------------------------------

@dataclass
class MatchOutcome:
    kind: str  # "posting", "rejection", or "new"
    fp: Fingerprint
    posting_id: int | None = None
    reason: str | None = None
    # Set when a remembered rejection was found but is stale (the titles or
    # location settings it was judged against have since changed) -- the
    # caller should re-judge it, then overwrite this same row rather than
    # inserting a second one for the same job.
    stale_fingerprint_id: int | None = None


async def match_incoming(
    db, *, company_name: str, title: str, url: str | None, location_text: str = "",
    external_id: str | None = None, settings_version: str, source: str, now_iso: str,
) -> MatchOutcome:
    fp = compute_fingerprint(
        company_name=company_name, title=title, location_text=location_text, url=url, external_id=external_id,
    )
    row = await find_match(db, fp)
    if row and row.get("posting_id") is not None:
        await touch(db, row["id"], source=source, now_iso=now_iso)
        return MatchOutcome(kind="posting", fp=fp, posting_id=row["posting_id"])
    if row and row.get("rejection_reason") is not None:
        if row["settings_version"] == settings_version:
            await touch(db, row["id"], source=source, now_iso=now_iso)
            return MatchOutcome(kind="rejection", fp=fp, reason=row["rejection_reason"])
        return MatchOutcome(kind="new", fp=fp, stale_fingerprint_id=row["id"])

    existing = await find_duplicate_posting(db, company_name=company_name, title=title, url=url)
    if existing:
        await record_posting(
            db, fp, posting_id=existing["id"], settings_version=settings_version, source=source, now_iso=now_iso,
        )
        return MatchOutcome(kind="posting", fp=fp, posting_id=existing["id"])

    return MatchOutcome(kind="new", fp=fp)
