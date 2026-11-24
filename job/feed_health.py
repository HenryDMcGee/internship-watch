"""Feed health (BR-001.2 and BR-001.3).

Before this module a feed could fail on every run and nothing said so: all 37
Workday feeds did, for weeks. Three things live here:

  * the count of failed reads in a row (`companies.feed_failures`) and the
    threshold at which a feed is reported as failing;
  * `ensure_feed_url`, which gives a Workday feed its own address so a change
    to the company's careers page can't break it;
  * `clear_dead_feeds`, which stops reading a feed whose address no longer
    exists, so the company falls back to the careers-page reader and the
    scout instead of erroring forever.
"""
from __future__ import annotations

import json
import re

from job import config  # noqa: F401 -- adds api/src to sys.path for the import below
from job.fetchers import workday
from job.sources_lifecycle import FAILING_AFTER_RUNS, normalize_url

from companies import detect_feed  # noqa: E402

FAILING_AFTER_RUNS_KEY = "feed_failing_after_runs"
DEFAULT_FAILING_AFTER_RUNS = FAILING_AFTER_RUNS

# Never cleared for returning "not found". The Workday reader has not been
# tried against live sites, and a mistake in how it forms the address would
# look exactly like a dead address (see issues/fix-feed-handoff.md, "Bad
# board names"). A Workday feed with no site address on file at all is a
# different matter: there is nothing to read, so that one is cleared.
NOT_CLEARED_ON_NOT_FOUND = frozenset({"workday"})

NOT_FOUND = "not_found"
NO_ADDRESS = "no_address"

_NOT_FOUND_RE = re.compile(r"\bHTTP 404\b")

# Where a board lives for a person, for feeds that have no careers link on
# file to remember the dead address by.
_BOARD_PAGES = {
    "greenhouse": "https://boards.greenhouse.io/{id}",
    "lever": "https://jobs.lever.co/{id}",
    "ashby": "https://jobs.ashbyhq.com/{id}",
    "smartrecruiters": "https://jobs.smartrecruiters.com/{id}",
}


def next_failure_count(previous: int | None, feed_status: str) -> int:
    """Failed reads in a row after this read. Anything but an error resets it."""
    return (previous or 0) + 1 if feed_status == "error" else 0


async def load_failing_after_runs(db) -> int:
    row = await db.first("SELECT value FROM settings WHERE key = ?", FAILING_AFTER_RUNS_KEY)
    if not row:
        return DEFAULT_FAILING_AFTER_RUNS
    try:
        value = json.loads(row["value"])
    except (TypeError, ValueError):
        return DEFAULT_FAILING_AFTER_RUNS
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return DEFAULT_FAILING_AFTER_RUNS
    return value


async def failing_feed_names(db, threshold: int) -> list[str]:
    """Tracked companies whose feed has failed `threshold` or more reads in a row."""
    rows = await db.all(
        "SELECT name FROM companies WHERE status IN ('ranked', 'pinned') AND feed_type IS NOT NULL "
        "AND feed_failures >= ? ORDER BY name",
        threshold,
    )
    return [r["name"] for r in rows]


async def ensure_feed_url(db, company: dict) -> bool:
    """Gives a Workday feed its own address the first time it is read, copied
    from a careers link that is a Workday site link. Updates `company` too, so
    the read that follows uses it. Returns whether anything was saved.
    """
    if company.get("feed_type") != "workday" or (company.get("feed_url") or "").strip():
        return False
    careers_url = (company.get("careers_url") or "").strip()
    if workday.parse_site_link(careers_url) is None:
        return False
    await db.run("UPDATE companies SET feed_url = ? WHERE id = ?", careers_url, company["id"])
    company["feed_url"] = careers_url
    return True


def is_dead_address_error(error: str | None) -> bool:
    """Only "not found" means the address is gone. A block (403), a server
    error or a timeout can all pass, and none of them is a reason to drop a feed.
    """
    return bool(error) and bool(_NOT_FOUND_RE.search(error))


def _careers_link_is_the_board(company: dict) -> bool:
    careers_url = (company.get("careers_url") or "").strip()
    if not careers_url:
        return False
    return detect_feed(careers_url) == (company.get("feed_type"), company.get("feed_id") or None)


def _dead_address(company: dict) -> str | None:
    feed_url = (company.get("feed_url") or "").strip()
    if feed_url:
        return feed_url
    if _careers_link_is_the_board(company):
        return company["careers_url"].strip()
    template = _BOARD_PAGES.get(company.get("feed_type") or "")
    if template and company.get("feed_id"):
        return template.format(id=company["feed_id"])
    return None


async def _remember_dead_address(db, company: dict, url: str, *, now_iso: str) -> None:
    note = f"Demoted {now_iso[:10]}: {company.get('name')}'s feed at this address returned HTTP 404 on every read."
    key = normalize_url(url)
    rows = await db.all("SELECT id, url, notes FROM sources WHERE url IS NOT NULL")
    matches = [r for r in rows if normalize_url(r["url"]) == key]
    if not matches:
        await db.run(
            "INSERT INTO sources (name, url, kind, covers, status, discovered_by, discovered_at, last_checked, notes) "
            "VALUES (?, ?, 'Company career sites', ?, 'demoted', 'feed-health', ?, ?, ?)",
            f"{company.get('name')} job feed", url, company.get("name") or "", now_iso, now_iso, note,
        )
        return
    for row in matches:
        notes = f"{row['notes'].strip()} {note}" if (row.get("notes") or "").strip() else note
        await db.run(
            "UPDATE sources SET status = 'demoted', last_checked = ?, notes = ? WHERE id = ?", now_iso, notes, row["id"],
        )


def _clear_reason(company: dict) -> str | None:
    if company["feed_type"] == "workday":
        address = company.get("feed_url") or company.get("careers_url")
        return NO_ADDRESS if workday.parse_site_link(address) is None else None
    return NOT_FOUND if is_dead_address_error(company.get("last_feed_error")) else None


async def clear_dead_feeds(db, *, threshold: int, now_iso: str) -> list[dict]:
    """Clears every feed that has failed `threshold` reads in a row because
    its address returned "not found", or (Workday) because there is no site
    address on file to read. The company keeps its place on the list and its
    postings; it is simply no longer read through a feed that isn't there.
    A careers link that was the dead board goes too, unless Henry approved it.
    A dead address is kept as a demoted source so it is not picked up again.
    Returns one `{company_id, name, reason}` per feed cleared.
    """
    rows = await db.all(
        "SELECT * FROM companies WHERE feed_type IS NOT NULL AND last_feed_status = 'error' AND feed_failures >= ? "
        "ORDER BY name",
        threshold,
    )
    cleared = []
    for row in rows:
        company = dict(row)
        reason = _clear_reason(company)
        if reason is None:
            continue
        dead_address = _dead_address(company) if reason == NOT_FOUND else None
        drop_careers_link = (
            reason == NOT_FOUND and _careers_link_is_the_board(company) and not company.get("careers_url_approved")
        )
        await db.run(
            "UPDATE companies SET feed_type = NULL, feed_id = NULL, feed_url = NULL, feed_failures = 0, "
            "last_feed_status = 'no_source', last_feed_error = NULL"
            + (", careers_url = NULL" if drop_careers_link else "")
            + " WHERE id = ?",
            company["id"],
        )
        if dead_address:
            await _remember_dead_address(db, company, dead_address, now_iso=now_iso)
        cleared.append({"company_id": company["id"], "name": company["name"], "reason": reason})
    return cleared
