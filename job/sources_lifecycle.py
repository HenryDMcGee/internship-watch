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
from job.fetchers.base import is_rate_limit_error, is_temporary_error

from companies import detect_feed  # noqa: E402

TRIAL_DAYS = 30
# How long past the 30 days an address is kept when its try got no real
# answer (BR-004.2). Long enough for a site to stop refusing us; short enough
# that an address a site answers "500" to forever does not stay on trial.
UNDECIDED_GRACE_DAYS = 14
SCOUT = "claude-scout"
# Tries per run. Each is one feed read, so this bounds the time adoption can
# add to a run that is already close to its 60-minute limit.
ADOPTION_TRIES_PER_RUN = 25
ADOPTION_SECONDS_PER_RUN = 120
# Failed reads in a row before a feed that has an address may be replaced.
# The run passes the feed-health setting (`job/feed_health.py`); this is its
# default.
FAILING_AFTER_RUNS = 5
# Platforms whose trial addresses are tried only once a tracked company's
# feed on that platform has read successfully. The Workday reader was rebuilt
# without being tried against live sites; if it is wrong, every try would
# fail and demote a good address.
READER_MUST_PROVE_ITSELF = frozenset({"workday"})
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


def unproven_feed_types(companies: list[dict]) -> frozenset[str]:
    """The platforms in `READER_MUST_PROVE_ITSELF` with no working feed among
    `companies` yet."""
    proven = {c.get("feed_type") for c in companies if coverage.feed_is_working(c)}
    return frozenset(READER_MUST_PROVE_ITSELF - proven)


@dataclass
class AdoptionOutcome:
    adopted: list[dict] = field(default_factory=list)  # {company_id, name, feed_type, url, source_id}
    failed: int = 0
    # Trial feed addresses this run had no tries left for. They are not
    # demoted for age until they have had their try.
    untried_source_ids: set[int] = field(default_factory=set)
    # Addresses whose try got no real answer: the site said slow down, had a
    # fault of its own, or did not answer (BR-004.2). They stay on trial.
    undecided_source_ids: set[int] = field(default_factory=set)

    @property
    def undecided(self) -> int:
        return len(self.undecided_source_ids)


def _append_note(existing: str | None, note: str) -> str:
    existing = (existing or "").strip()
    return f"{existing} {note}" if existing else note


def _by_importance(company: dict) -> tuple:
    rank = company.get("rank")
    return (rank is None, rank or 0, company.get("id") or 0)


def _wants_a_feed(company: dict, failing_after: int) -> bool:
    """Whether a trial address may become this company's feed:
      * it has no feed, or
      * its Workday feed has no site address on file, so there is nothing to
        read, or
      * its feed counts as failing: `failing_after` failed reads in a row,
        the same line the feed-health alert draws.
    A feed that has an address and has failed less than that is left alone.
    One bad morning is not a reason to swap a company's board for another.
    Nor is being told to slow down, however many mornings in a row: that
    says nothing about whether the address is right (BR-004.2).
    """
    feed_type = company.get("feed_type")
    if not feed_type:
        return True
    if feed_type == "workday" and workday.parse_site_link(company.get("feed_url") or company.get("careers_url")) is None:
        return True
    if is_rate_limit_error(company.get("last_feed_error")):
        return False
    return company.get("last_feed_status") == "error" and (company.get("feed_failures") or 0) >= failing_after


def _source_is_for(company: dict, names: list[str], source: dict, feed_type: str, feed_id: str | None) -> bool:
    """A source belongs to a company when its name starts with the company's
    name, or its address is on the board the company's feed already names.
    Being mentioned somewhere in the source's description is not enough:
    every "(Workday)" source mentions the company named Workday.
    """
    if (
        feed_id and company.get("feed_type") == feed_type
        and (company.get("feed_id") or "").lower() == feed_id.lower()
    ):
        return True
    source_name = f"{coverage.words(source.get('name') or '')} "
    return any(source_name.startswith(f"{name} ") for name in names)


async def adopt_trial_feeds(
    db, companies: list[dict], *, fetch, now_iso: str, limit: int = ADOPTION_TRIES_PER_RUN,
    seconds_budget: float = ADOPTION_SECONDS_PER_RUN, clock=time.monotonic,
    failing_after: int = FAILING_AFTER_RUNS, hold_feed_types: frozenset[str] = frozenset(),
) -> AdoptionOutcome:
    """Tries trial feed addresses for the companies in `companies` that want
    a feed (`_wants_a_feed`), most important company first and newest address
    first, and adopts the first one that can be read. `fetch` is the feed reader
    (`job.fetchers.run_fetcher`); a read with no error is a success even when
    it lists nothing, since a correct feed can be empty out of season.

    The address is tried before anything is saved. On success the company row
    and the dict in `companies` both get the feed, so the same run reads it.

    At most `limit` tries and `seconds_budget` seconds a run. Addresses left
    over are reported as untried and wait for the next run. A try that gets
    no real answer (`is_temporary_error`) decides nothing: the address stays
    on trial and is reported as undecided. Addresses on a platform in
    `hold_feed_types` wait too: a failed try demotes for good, so the
    run holds a platform back until its reader has read a feed successfully
    (`READER_MUST_PROVE_ITSELF`).
    """
    outcome = AdoptionOutcome()
    wanting = sorted((c for c in companies if _wants_a_feed(c, failing_after)), key=_by_importance)
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
    asked_to_slow_down: set[str] = set()  # platforms that answered 429 this run
    for company in wanting:
        names = coverage.company_names(company)
        for source, (feed_type, feed_id, feed_url) in candidates:
            if source["status"] != "trial" or not _source_is_for(company, names, source, feed_type, feed_id):
                continue
            if (
                feed_type in hold_feed_types or feed_type in asked_to_slow_down
                or tries >= limit or clock() - started >= seconds_budget
            ):
                outcome.untried_source_ids.add(source["id"])
                continue
            tries += 1
            trial_company = {**company, "feed_type": feed_type, "feed_id": feed_id, "feed_url": feed_url}
            try:
                result = fetch(trial_company)
                error = result.error
            except Exception as exc:  # noqa: BLE001 - one bad address must not stop the rest
                error = f"unexpected error: {exc}"
            if error and is_temporary_error(error):
                # No real answer, so no verdict. It stays on trial for
                # another run, and a platform that says slow down is not
                # asked again in this one.
                outcome.undecided_source_ids.add(source["id"])
                if is_rate_limit_error(error):
                    asked_to_slow_down.add(feed_type)
                await db.run("UPDATE sources SET last_checked = ? WHERE id = ?", now_iso, source["id"])
                continue
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


async def demote_stale_trials(
    db, *, today: date, exclude_ids: set[int] | frozenset[int] = frozenset(),
    grace_ids: set[int] | frozenset[int] = frozenset(),
) -> int:
    """Demotes every scout-proposed source that has been on trial for more
    than 30 days. `exclude_ids` are feed addresses still waiting for their
    try. `grace_ids` are addresses whose try got no real answer this run;
    they are spared until they are `UNDECIDED_GRACE_DAYS` past the 30. One statement however many rows there are (D1 allows 100 bound
    parameters a query; the ids go in as one JSON value). Returns the count.
    """
    cutoff = (today - timedelta(days=TRIAL_DAYS)).isoformat()
    where = (
        "status = 'trial' AND discovered_by = ? AND discovered_at IS NOT NULL AND substr(discovered_at, 1, 10) < ? "
        "AND id NOT IN (SELECT value FROM json_each(?)) "
        "AND NOT (id IN (SELECT value FROM json_each(?)) AND substr(discovered_at, 1, 10) >= ?)"
    )
    grace_cutoff = (today - timedelta(days=TRIAL_DAYS + UNDECIDED_GRACE_DAYS)).isoformat()
    params = (SCOUT, cutoff, json.dumps(sorted(exclude_ids)), json.dumps(sorted(grace_ids)), grace_cutoff)
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
