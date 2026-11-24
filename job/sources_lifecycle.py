"""What happens to a source the scout proposed (BR-001.5).

The scout writes `trial` rows to `sources`. Until now nothing read them, so
none was ever promoted and 622 sat on trial for more than a month. A trial
source now has one of two ends:

  * adopted -- it is on a feed platform, it names a company that has no
    working feed, and reading it works. It becomes that company's feed and
    the row turns `active`.
  * demoted -- it was tried and could not be read, or 30 days passed without
    it being adopted. Rows are kept, never deleted, so the same page is not
    proposed again.

`normalize_url` is how "the same page" is decided everywhere.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urlparse

from job import config  # noqa: F401 -- adds api/src to sys.path for the import below
from job import coverage
from job.fetchers import workday

from companies import detect_feed  # noqa: E402

TRIAL_DAYS = 30
SCOUT = "claude-scout"
# Tries per run. Each is one feed read, so this bounds the time adoption can
# add to a run that is already close to its 60-minute limit.
ADOPTION_TRIES_PER_RUN = 25
ADOPTION_SECONDS_PER_RUN = 120
# amazon.jobs lists every Amazon company in one feed, so a page there says
# nothing about which company it belongs to.
ADOPTABLE_FEEDS = frozenset({"greenhouse", "lever", "ashby", "smartrecruiters", "workday"})


def normalize_url(url: str | None) -> str:
    """One key per page: host in lowercase, no scheme, no trailing slash, no
    query, no fragment. `https://jobs.adp.com/`, `http://Jobs.ADP.com` and
    `https://jobs.adp.com/?q=intern` are the same page.
    """
    text = (url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"https://{text}")
    return f"{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def strip_query(url: str) -> str:
    parsed = urlparse(url.strip())
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def feed_for_url(url: str | None) -> tuple[str, str | None, str] | None:
    """(feed_type, feed_id, feed address) when `url` is an address a feed can
    be read from, else None. A Workday link must name its site; the others
    must name their board.
    """
    feed_type, feed_id = detect_feed(url)
    if feed_type not in ADOPTABLE_FEEDS:
        return None
    if feed_type == "workday":
        if workday.parse_site_link(url) is None:
            return None
    elif not feed_id:
        return None
    return feed_type, feed_id, strip_query(url)


@dataclass
class AdoptionOutcome:
    adopted: list[dict] = field(default_factory=list)  # {company_id, name, feed_type, url, source_id}
    failed: int = 0
    # Trial feed addresses this run had no tries left for. They are not
    # demoted for age until they have had their try.
    untried_source_ids: set[int] = field(default_factory=set)


def _append_note(existing: str | None, note: str) -> str:
    existing = (existing or "").strip()
    return f"{existing} {note}" if existing else note


def _by_importance(company: dict) -> tuple:
    rank = company.get("rank")
    return (rank is None, rank or 0, company.get("id") or 0)


def _wants_a_feed(company: dict) -> bool:
    """No feed at all, or a feed whose last read failed. A feed that has
    never been read has not failed yet and is left alone.
    """
    if not company.get("feed_type"):
        return True
    return company.get("last_feed_status") == "error"


async def adopt_trial_feeds(
    db, companies: list[dict], *, fetch, now_iso: str, limit: int = ADOPTION_TRIES_PER_RUN,
    seconds_budget: float = ADOPTION_SECONDS_PER_RUN, clock=time.monotonic,
) -> AdoptionOutcome:
    """Tries trial feed addresses for the companies in `companies` that have
    no working feed, most important company first and newest address first,
    and adopts the first one that can be read. `fetch` is the feed reader
    (`job.fetchers.run_fetcher`); a read with no error is a success even when
    it lists nothing, since a correct feed can be empty out of season.

    The address is tried before anything is saved. On success the company row
    and the dict in `companies` both get the feed, so the same run reads it.

    At most `limit` tries and `seconds_budget` seconds a run. Addresses left
    over are reported as untried and wait for the next run.
    """
    outcome = AdoptionOutcome()
    wanting = sorted((c for c in companies if _wants_a_feed(c)), key=_by_importance)
    if not wanting:
        return outcome
    rows = await db.all("SELECT * FROM sources WHERE status = 'trial' ORDER BY discovered_at DESC, id DESC")
    candidates = []
    for row in rows:
        feed = feed_for_url(row.get("url"))
        if feed:
            candidates.append((dict(row), feed))
    if not candidates:
        return outcome

    tries = 0
    started = clock()
    for company in wanting:
        names = coverage.company_names(company)
        for source, (feed_type, feed_id, feed_url) in candidates:
            if source["status"] != "trial" or not coverage.source_mentions(source, names):
                continue
            if tries >= limit or clock() - started >= seconds_budget:
                outcome.untried_source_ids.add(source["id"])
                continue
            tries += 1
            trial_company = {**company, "feed_type": feed_type, "feed_id": feed_id, "feed_url": feed_url}
            try:
                result = fetch(trial_company)
                error = result.error
            except Exception as exc:  # noqa: BLE001 - one bad address must not stop the rest
                error = f"unexpected error: {exc}"
            if error:
                outcome.failed += 1
                source["status"] = "demoted"
                note = f"Demoted {now_iso[:10]}: tried as {company.get('name')}'s feed and could not be read ({error})."
                await db.run(
                    "UPDATE sources SET status = 'demoted', last_checked = ?, notes = ? WHERE id = ?",
                    now_iso, _append_note(source.get("notes"), note), source["id"],
                )
                continue
            source["status"] = "active"
            await db.run(
                "UPDATE companies SET feed_type = ?, feed_id = ?, feed_url = ?, feed_failures = 0 WHERE id = ?",
                feed_type, feed_id, feed_url, company["id"],
            )
            await db.run(
                "UPDATE sources SET status = 'active', last_checked = ?, last_success_at = ? WHERE id = ?",
                now_iso, now_iso, source["id"],
            )
            company.update(feed_type=feed_type, feed_id=feed_id, feed_url=feed_url, feed_failures=0)
            outcome.adopted.append({
                "company_id": company["id"], "name": company.get("name"), "feed_type": feed_type,
                "url": feed_url, "source_id": source["id"],
            })
            break
    return outcome


async def demote_stale_trials(db, *, today: date, exclude_ids: set[int] | frozenset[int] = frozenset()) -> int:
    """Demotes every scout-proposed source that has been on trial for more
    than 30 days. `exclude_ids` are feed addresses still waiting for their
    try. One statement however many rows there are (D1 allows 100 bound
    parameters a query; the ids go in as one JSON value). Returns the count.
    """
    cutoff = (today - timedelta(days=TRIAL_DAYS)).isoformat()
    where = (
        "status = 'trial' AND discovered_by = ? AND discovered_at IS NOT NULL AND substr(discovered_at, 1, 10) < ? "
        "AND id NOT IN (SELECT value FROM json_each(?))"
    )
    params = (SCOUT, cutoff, json.dumps(sorted(exclude_ids)))
    row = await db.first(f"SELECT COUNT(*) AS n FROM sources WHERE {where}", *params)
    count = (row or {}).get("n") or 0
    if not count:
        return 0
    note = f"Demoted {today.isoformat()}: not adopted as a company's feed within {TRIAL_DAYS} days."
    await db.run(
        "UPDATE sources SET status = 'demoted', "
        "notes = CASE WHEN notes IS NULL OR TRIM(notes) = '' THEN ? ELSE TRIM(notes) || ' ' || ? END "
        f"WHERE {where}",
        note, note, *params,
    )
    return count
