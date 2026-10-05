"""Which companies are covered, and the one-time careers-page backfill (issue #63).

A company is covered only when something is actually reading it today:
  * a working feed -- `feed_type` is set and the last feed read succeeded, or
  * a `careers_url` that loaded successfully within the last 7 days.
A row in the `sources` table alone does not count: that is exactly how run
36 to 42 logged "skipped 182 already-covered companies" while 182 companies
had no source.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from job import config  # noqa: F401 -- adds api/src to sys.path for the import below

from companies import detect_feed, normalize_company_name  # noqa: E402

CAREERS_LOADED_WITHIN_DAYS = 7
CAREERS_NO_FEED_NOTE = "careers page, no feed"
BACKFILL_SETTING_KEY = "careers_backfill"

# Source kinds that are a company's own careers page; job boards, trackers,
# and school boards list many companies and aren't a page for one.
_CAREERS_PAGE_KINDS = ("Company career sites", "Added by you")

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def feed_is_working(company: dict) -> bool:
    return bool(company.get("feed_type")) and company.get("last_feed_status") == "ok"


async def careers_loaded_recently_ids(db, *, today: date) -> set[int]:
    cutoff = (today - timedelta(days=CAREERS_LOADED_WITHIN_DAYS)).isoformat()
    rows = await db.all(
        "SELECT s.company_id AS company_id, s.last_checked AS last_checked FROM careers_page_snapshots s "
        "JOIN companies c ON c.id = s.company_id "
        "WHERE s.status IN ('changed', 'unchanged') AND c.careers_url IS NOT NULL AND TRIM(c.careers_url) != ''"
    )
    return {r["company_id"] for r in rows if (r.get("last_checked") or "")[:10] >= cutoff}


async def covered_company_ids(db, companies: list[dict], *, today: date) -> set[int]:
    """Ids from `companies` that are covered right now. Reads each company's
    current feed status from the database rather than trusting the copy loaded
    at the start of the run, since feeds are read after that.
    """
    ids = [c["id"] for c in companies if c.get("id") is not None]
    if not ids:
        return set()
    rows = await db.all("SELECT id, feed_type, last_feed_status FROM companies")
    wanted = set(ids)
    covered = {r["id"] for r in rows if r["id"] in wanted and feed_is_working(r)}
    return covered | (await careers_loaded_recently_ids(db, today=today) & wanted)


def _words(text: str) -> str:
    return _NON_WORD_RE.sub(" ", (text or "").lower()).strip()


def _names_for(company: dict) -> list[str]:
    names = [company.get("name") or ""]
    names += [a for a in company.get("aliases") or [] if isinstance(a, str)]
    out = []
    for name in names:
        for candidate in (_words(name), _words(normalize_company_name(name))):
            if candidate and candidate not in out:
                out.append(candidate)
    return out


def _source_mentions(source: dict, company_names: list[str]) -> bool:
    text = f" {_words(source.get('name') or '')} {_words(source.get('covers') or '')} "
    return any(f" {name} " in text for name in company_names)


def _source_rank(source: dict) -> tuple:
    url = (source.get("url") or "").lower()
    return (
        0 if source.get("kind") == "Company career sites" else 1,
        0 if "intern" in url else 1,
        -(source.get("found_count") or 0),
        source.get("id") or 0,
    )


def best_source_for(company: dict, sources: list[dict]) -> dict | None:
    names = _names_for(company)
    usable = [
        s for s in sources
        if s.get("kind") in _CAREERS_PAGE_KINDS and s.get("status") != "demoted" and (s.get("url") or "").strip()
        and _source_mentions(s, names)
    ]
    return min(usable, key=_source_rank) if usable else None


async def backfill_careers_urls(db, companies: list[dict]) -> list[dict]:
    """For every company with no feed and no careers_url, attaches the best
    careers page already in the `sources` table. A page on a recognized
    platform also gets `feed_type`/`feed_id`; any other is marked "careers
    page, no feed". Returns one `{company_id, name, url}` per company that got
    one. Nothing is attached as Henry-approved.
    """
    candidates = [c for c in companies if not c.get("feed_type") and not (c.get("careers_url") or "").strip()]
    if not candidates:
        return []
    sources = [dict(r) for r in await db.all("SELECT * FROM sources")]
    attached = []
    for company in candidates:
        source = best_source_for(company, sources)
        if not source:
            continue
        url = source["url"].strip()
        feed_type, feed_id = detect_feed(url)
        await db.run(
            "UPDATE companies SET careers_url = ?, feed_type = ?, feed_id = ?, careers_note = ? WHERE id = ?",
            url, feed_type, feed_id, None if feed_type else CAREERS_NO_FEED_NOTE, company["id"],
        )
        attached.append({"company_id": company["id"], "name": company["name"], "url": url})
    return attached


async def run_backfill_once(db, companies: list[dict], *, now_iso: str) -> int | None:
    """Runs the backfill the first time only, logging how many companies got a
    careers page. Returns that count, or None if it already ran.
    """
    done = await db.first("SELECT value FROM settings WHERE key = ?", BACKFILL_SETTING_KEY)
    if done:
        return None
    attached = await backfill_careers_urls(db, companies)
    value = json.dumps({"at": now_iso, "attached": len(attached)})
    await db.run("INSERT INTO settings (key, value) VALUES (?, ?)", BACKFILL_SETTING_KEY, value)
    print(f"careers backfill: attached a careers page from the sources table to {len(attached)} compan"
          f"{'y' if len(attached) == 1 else 'ies'}")
    return len(attached)
