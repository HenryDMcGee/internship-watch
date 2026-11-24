"""BR-001.5: scout-proposed sources prove themselves or leave
(`job/sources_lifecycle.py`).

A trial source on a feed platform is tried for the company it names and
adopted as that company's feed if it can be read. A trial source that is not
adopted within 30 days is demoted. The same page under a slightly different
address counts as the same page.
"""
import asyncio
from datetime import date

import pytest

from job import sources_lifecycle
from job.fetchers import FetchResult
from job.models import FetchedPosting


def run(coro):
    return asyncio.run(coro)


def _insert_company(db, **overrides):
    row = {"name": "Acme", "status": "pinned"}
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    return run(db.run(f"INSERT INTO companies ({columns}) VALUES ({placeholders})", *row.values()))


def _insert_source(db, **overrides):
    row = {
        "name": "Acme careers", "url": "https://boards.greenhouse.io/acme", "kind": "Company career sites",
        "covers": "", "status": "trial", "discovered_by": "claude-scout", "discovered_at": "2026-11-20T08:00:00-06:00",
    }
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    return run(db.run(f"INSERT INTO sources ({columns}) VALUES ({placeholders})", *row.values()))


def _companies(db):
    return [dict(r) for r in run(db.all("SELECT * FROM companies ORDER BY id"))]


def _fetch_ok(company, session=None):
    return FetchResult(
        company_id=company["id"], company_name=company["name"],
        postings=[FetchedPosting(company_name=company["name"], title="Intern", url="https://x.example/1")],
    )


def _fetch_fails(company, session=None):
    return FetchResult(company_id=company["id"], company_name=company["name"], postings=[], error="returned HTTP 404")


# -- the same page under a different address ----------------------------------

@pytest.mark.parametrize("url", [
    "https://jobs.adp.com/", "https://jobs.adp.com", "HTTPS://Jobs.ADP.com/", "http://jobs.adp.com/",
    "https://jobs.adp.com/?q=intern", "https://jobs.adp.com/#openings",
])
def test_the_same_page_normalizes_to_the_same_key(url):
    assert sources_lifecycle.normalize_url(url) == sources_lifecycle.normalize_url("https://jobs.adp.com")


def test_the_four_adp_variants_are_two_pages_not_four():
    variants = [
        "https://jobs.adp.com/", "https://jobs.adp.com", "https://jobs.adp.com/en/jobs/", "https://jobs.adp.com/en/jobs",
    ]
    assert len({sources_lifecycle.normalize_url(u) for u in variants}) == 2


def test_different_pages_stay_different():
    assert sources_lifecycle.normalize_url("https://careers.amd.com/students") != sources_lifecycle.normalize_url(
        "https://careers.amd.com/students/jobs"
    )
    assert sources_lifecycle.normalize_url("") == ""
    assert sources_lifecycle.normalize_url(None) == ""


# -- adoption -----------------------------------------------------------------

def test_a_trial_feed_is_adopted_for_the_company_it_names(empty_db):
    company_id = _insert_company(
        empty_db, name="Micron", feed_type="workday", feed_id="micron", last_feed_status="error", feed_failures=3,
        careers_url="https://www.micron.com/careers/university-recruiting",
    )
    source_id = _insert_source(
        empty_db, name="Micron careers (Workday)", url="https://micron.wd1.myworkdayjobs.com/External?q=intern",
    )
    companies = _companies(empty_db)

    outcome = run(sources_lifecycle.adopt_trial_feeds(
        empty_db, companies, fetch=_fetch_ok, now_iso="2026-11-24T08:00:00-06:00",
    ))

    assert [a["company_id"] for a in outcome.adopted] == [company_id]
    company = run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["feed_type"] == "workday"
    assert company["feed_id"] == "micron"
    assert company["feed_url"] == "https://micron.wd1.myworkdayjobs.com/External"
    assert company["feed_failures"] == 0
    # The careers page is Henry's to see and is not touched by adoption.
    assert company["careers_url"] == "https://www.micron.com/careers/university-recruiting"
    # The company row the run is holding is updated too, so this run reads the feed.
    assert companies[0]["feed_url"] == "https://micron.wd1.myworkdayjobs.com/External"

    source = run(empty_db.first("SELECT * FROM sources WHERE id = ?", source_id))
    assert source["status"] == "active"
    assert source["last_success_at"] == "2026-11-24T08:00:00-06:00"


def test_the_address_is_tried_before_it_is_saved(empty_db):
    company_id = _insert_company(empty_db, name="Unity", feed_type=None)
    source_id = _insert_source(empty_db, name="Unity careers (Greenhouse board)", url="https://boards.greenhouse.io/unity3d")
    tried = []

    def fetch(company, session=None):
        tried.append((company["feed_type"], company["feed_id"]))
        return _fetch_fails(company)

    outcome = run(sources_lifecycle.adopt_trial_feeds(
        empty_db, _companies(empty_db), fetch=fetch, now_iso="2026-11-24T08:00:00-06:00",
    ))

    assert tried == [("greenhouse", "unity3d")]
    assert outcome.adopted == []
    assert run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))["feed_type"] is None
    source = run(empty_db.first("SELECT * FROM sources WHERE id = ?", source_id))
    assert source["status"] == "demoted"
    assert "404" in source["notes"]


def test_a_feed_that_reads_but_lists_nothing_is_still_adopted(empty_db):
    # A correct feed can list no internships in the off-season.
    company_id = _insert_company(empty_db, name="Zebra Technologies", feed_type=None)
    _insert_source(empty_db, name="Zebra careers (Workday)", url="https://zebra.wd1.myworkdayjobs.com/Zebra_Careers")

    def fetch(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    outcome = run(sources_lifecycle.adopt_trial_feeds(
        empty_db, _companies(empty_db), fetch=fetch, now_iso="2026-11-24T08:00:00-06:00",
    ))

    assert len(outcome.adopted) == 1
    assert run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))["feed_type"] == "workday"


def test_a_company_with_a_working_feed_is_left_alone(empty_db):
    _insert_company(empty_db, name="Acme", feed_type="lever", feed_id="acme", last_feed_status="ok")
    _insert_source(empty_db)
    calls = []

    def fetch(company, session=None):
        calls.append(company)
        return _fetch_ok(company)

    outcome = run(sources_lifecycle.adopt_trial_feeds(
        empty_db, _companies(empty_db), fetch=fetch, now_iso="2026-11-24T08:00:00-06:00",
    ))

    assert calls == []
    assert outcome.adopted == []


def test_a_source_is_only_tried_for_a_company_it_names_as_whole_words(empty_db):
    _insert_company(empty_db, name="Arm", feed_type=None)
    _insert_source(empty_db, name="Armis careers", url="https://boards.greenhouse.io/armis")
    calls = []

    def fetch(company, session=None):
        calls.append(company)
        return _fetch_ok(company)

    run(sources_lifecycle.adopt_trial_feeds(
        empty_db, _companies(empty_db), fetch=fetch, now_iso="2026-11-24T08:00:00-06:00",
    ))

    assert calls == []


def test_sources_that_are_not_feeds_or_not_on_trial_are_never_tried(empty_db):
    _insert_company(empty_db, name="Acme", feed_type=None)
    _insert_source(empty_db, name="Acme careers", url="https://www.acme.example/careers")
    _insert_source(empty_db, name="Acme careers old", url="https://boards.greenhouse.io/acme-old", status="demoted")
    _insert_source(empty_db, name="Acme Workday home", url="https://acme.wd1.myworkdayjobs.com")
    calls = []

    def fetch(company, session=None):
        calls.append(company)
        return _fetch_ok(company)

    run(sources_lifecycle.adopt_trial_feeds(
        empty_db, _companies(empty_db), fetch=fetch, now_iso="2026-11-24T08:00:00-06:00",
    ))

    assert calls == []


def test_a_second_address_is_tried_when_the_first_fails_and_then_no_more(empty_db):
    company_id = _insert_company(empty_db, name="Acme", feed_type=None)
    _insert_source(empty_db, name="Acme careers A", url="https://boards.greenhouse.io/acme-a", discovered_at="2026-11-22T08:00:00")
    _insert_source(empty_db, name="Acme careers B", url="https://boards.greenhouse.io/acme-b", discovered_at="2026-11-21T08:00:00")
    _insert_source(empty_db, name="Acme careers C", url="https://boards.greenhouse.io/acme-c", discovered_at="2026-11-20T08:00:00")
    tried = []

    def fetch(company, session=None):
        tried.append(company["feed_id"])
        return _fetch_fails(company) if company["feed_id"] == "acme-a" else _fetch_ok(company)

    run(sources_lifecycle.adopt_trial_feeds(
        empty_db, _companies(empty_db), fetch=fetch, now_iso="2026-11-24T08:00:00-06:00",
    ))

    assert tried == ["acme-a", "acme-b"]  # newest first, and C is not needed
    assert run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))["feed_id"] == "acme-b"
    statuses = {r["name"]: r["status"] for r in run(empty_db.all("SELECT name, status FROM sources"))}
    assert statuses == {"Acme careers A": "demoted", "Acme careers B": "active", "Acme careers C": "trial"}


def test_attempts_are_capped_per_run_and_the_untried_sources_are_reported(empty_db):
    ids = []
    for n in range(4):
        _insert_company(empty_db, name=f"Company{n}", feed_type=None, rank=n + 1)
        ids.append(_insert_source(
            empty_db, name=f"Company{n} careers", url=f"https://boards.greenhouse.io/company{n}",
        ))
    calls = []

    def fetch(company, session=None):
        calls.append(company["name"])
        return _fetch_ok(company)

    outcome = run(sources_lifecycle.adopt_trial_feeds(
        empty_db, _companies(empty_db), fetch=fetch, now_iso="2026-11-24T08:00:00-06:00", limit=2,
    ))

    assert calls == ["Company0", "Company1"]  # most important company first
    assert outcome.untried_source_ids == {ids[2], ids[3]}


def test_adoption_stops_when_its_time_for_the_run_is_used_up(empty_db):
    # The daily run has a 60-minute limit and is close to it. Slow sites must
    # not let this step eat the run; what is left waits for the next one.
    ids = []
    for n in range(4):
        _insert_company(empty_db, name=f"Company{n}", feed_type=None, rank=n + 1)
        ids.append(_insert_source(
            empty_db, name=f"Company{n} careers", url=f"https://boards.greenhouse.io/company{n}",
        ))
    now = [0.0]
    calls = []

    def slow_fetch(company, session=None):
        calls.append(company["name"])
        now[0] += 50.0
        return _fetch_ok(company)

    outcome = run(sources_lifecycle.adopt_trial_feeds(
        empty_db, _companies(empty_db), fetch=slow_fetch, now_iso="2026-11-24T08:00:00-06:00",
        seconds_budget=120, clock=lambda: now[0],
    ))

    assert calls == ["Company0", "Company1", "Company2"]  # 0s, 50s, 100s; at 150s the time is gone
    assert outcome.untried_source_ids == {ids[3]}


def test_a_feed_that_has_never_been_read_is_not_replaced(empty_db):
    # Henry added the company with its board a moment ago. Until that feed
    # has been read and has failed, a trial source has no business replacing it.
    company_id = _insert_company(empty_db, name="Acme", feed_type="lever", feed_id="acme", last_feed_status=None)
    _insert_source(empty_db)
    calls = []

    def fetch(company, session=None):
        calls.append(company)
        return _fetch_ok(company)

    run(sources_lifecycle.adopt_trial_feeds(
        empty_db, _companies(empty_db), fetch=fetch, now_iso="2026-11-24T08:00:00-06:00",
    ))

    assert calls == []
    assert run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))["feed_type"] == "lever"


# -- which company a source belongs to, and when a feed may be replaced --------
#
# Found by running the matching over the real trial sources before this
# shipped: a looser rule would have handed Zebra's board to the company named
# Workday, and swapped 19 good Workday addresses before the fixed reader had
# read them once.

def test_a_source_that_only_mentions_a_company_in_passing_is_not_its_feed(empty_db):
    # The company named Workday is mentioned by every "(Workday)" source, and
    # the company named Strategy by anything that covers "strategy" roles.
    _insert_company(empty_db, name="Workday", feed_type=None, rank=1)
    _insert_company(empty_db, name="Strategy", feed_type=None, rank=2)
    _insert_source(empty_db, name="Zebra careers (Workday)", url="https://zebra.wd1.myworkdayjobs.com/Zebra_Careers")
    _insert_source(
        empty_db, name="Spotify - Lever", url="https://jobs.lever.co/spotify",
        covers="Live postings including Summer Internship, Business Strategy and Product Management, MBA",
    )
    calls = []

    def fetch(company, session=None):
        calls.append(company["name"])
        return _fetch_ok(company)

    outcome = run(sources_lifecycle.adopt_trial_feeds(
        empty_db, _companies(empty_db), fetch=fetch, now_iso="2026-11-24T08:00:00-06:00",
    ))

    assert calls == []
    assert outcome.adopted == []


def test_a_source_is_matched_to_the_company_its_name_starts_with(empty_db):
    company_id = _insert_company(empty_db, name="Workday", feed_type=None)
    _insert_source(
        empty_db, name="Workday Early Career", url="https://workday.wd5.myworkdayjobs.com/Workday_Early_Career",
    )

    outcome = run(sources_lifecycle.adopt_trial_feeds(
        empty_db, _companies(empty_db), fetch=_fetch_ok, now_iso="2026-11-24T08:00:00-06:00",
    ))

    assert [a["company_id"] for a in outcome.adopted] == [company_id]


def test_a_source_on_the_companys_own_board_is_matched_whatever_it_is_called(empty_db):
    # "Fair Isaac (FICO)" is never spelled that way by a source, but the
    # address is on the tenant the company's feed already names.
    company_id = _insert_company(
        empty_db, name="Fair Isaac (FICO)", feed_type="workday", feed_id="fico", last_feed_status="error",
        careers_url="https://www.fico.com/en/careers",
    )
    _insert_source(empty_db, name="FICO Careers (Workday)", url="https://fico.wd1.myworkdayjobs.com/External")

    outcome = run(sources_lifecycle.adopt_trial_feeds(
        empty_db, _companies(empty_db), fetch=_fetch_ok, now_iso="2026-11-24T08:00:00-06:00",
    ))

    assert [a["company_id"] for a in outcome.adopted] == [company_id]
    assert run(empty_db.first("SELECT feed_url FROM companies WHERE id = ?", company_id))["feed_url"] == (
        "https://fico.wd1.myworkdayjobs.com/External"
    )


def test_a_feed_with_a_readable_address_is_not_replaced_until_it_counts_as_failing(empty_db):
    # NVIDIA's address was right all along; the reader was wrong. One failed
    # read, or four, is not a reason to swap it for another board.
    company_id = _insert_company(
        empty_db, name="NVIDIA", feed_type="workday", feed_id="nvidia", last_feed_status="error", feed_failures=4,
        feed_url="https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
    )
    _insert_source(
        empty_db, name="NVIDIA University Jobs (Workday)", url="https://nvidia.wd5.myworkdayjobs.com/en-US/UniversityJobs",
    )
    calls = []

    def fetch(company, session=None):
        calls.append(company["name"])
        return _fetch_ok(company)

    run(sources_lifecycle.adopt_trial_feeds(
        empty_db, _companies(empty_db), fetch=fetch, now_iso="2026-11-24T08:00:00-06:00", failing_after=5,
    ))

    assert calls == []
    assert run(empty_db.first("SELECT feed_url FROM companies WHERE id = ?", company_id))["feed_url"] == (
        "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
    )


def test_a_feed_that_counts_as_failing_can_be_replaced(empty_db):
    company_id = _insert_company(
        empty_db, name="NVIDIA", feed_type="workday", feed_id="nvidia", last_feed_status="error", feed_failures=5,
        feed_url="https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
    )
    _insert_source(
        empty_db, name="NVIDIA University Jobs (Workday)", url="https://nvidia.wd5.myworkdayjobs.com/en-US/UniversityJobs",
    )

    outcome = run(sources_lifecycle.adopt_trial_feeds(
        empty_db, _companies(empty_db), fetch=_fetch_ok, now_iso="2026-11-24T08:00:00-06:00", failing_after=5,
    ))

    assert [a["company_id"] for a in outcome.adopted] == [company_id]


def test_a_fetcher_that_raises_counts_as_a_failed_try_and_does_not_stop_the_rest(empty_db):
    _insert_company(empty_db, name="Alpha", feed_type=None, rank=1)
    beta = _insert_company(empty_db, name="Beta", feed_type=None, rank=2)
    _insert_source(empty_db, name="Alpha careers", url="https://boards.greenhouse.io/alpha")
    _insert_source(empty_db, name="Beta careers", url="https://boards.greenhouse.io/beta")

    def fetch(company, session=None):
        if company["name"] == "Alpha":
            raise RuntimeError("boom")
        return _fetch_ok(company)

    outcome = run(sources_lifecycle.adopt_trial_feeds(
        empty_db, _companies(empty_db), fetch=fetch, now_iso="2026-11-24T08:00:00-06:00",
    ))

    assert [a["company_id"] for a in outcome.adopted] == [beta]
    assert outcome.failed == 1


# -- the 30-day rule ----------------------------------------------------------

def test_a_trial_source_older_than_30_days_is_demoted(empty_db):
    old = _insert_source(empty_db, name="Old", url="https://old.example/careers", discovered_at="2026-10-20T08:00:00-05:00")
    fresh = _insert_source(empty_db, name="Fresh", url="https://fresh.example/careers", discovered_at="2026-11-10T08:00:00-06:00")

    count = run(sources_lifecycle.demote_stale_trials(empty_db, today=date(2026, 11, 24)))

    assert count == 1
    old_row = run(empty_db.first("SELECT * FROM sources WHERE id = ?", old))
    assert old_row["status"] == "demoted"
    assert "30 days" in old_row["notes"]
    assert "2026-11-24" in old_row["notes"]
    assert run(empty_db.first("SELECT status FROM sources WHERE id = ?", fresh))["status"] == "trial"


def test_a_source_exactly_30_days_old_gets_its_full_30_days(empty_db):
    source_id = _insert_source(empty_db, discovered_at="2026-10-25T08:00:00-05:00")

    assert run(sources_lifecycle.demote_stale_trials(empty_db, today=date(2026, 11, 24))) == 0
    assert run(empty_db.first("SELECT status FROM sources WHERE id = ?", source_id))["status"] == "trial"


def test_active_sources_and_sources_henry_added_are_never_demoted_by_age(empty_db):
    active = _insert_source(empty_db, name="Active", url="https://a.example", status="active", discovered_at="2026-09-01")
    mine = _insert_source(
        empty_db, name="Mine", url="https://m.example", discovered_by="you", discovered_at="2026-09-01",
    )

    assert run(sources_lifecycle.demote_stale_trials(empty_db, today=date(2026, 11, 24))) == 0
    assert run(empty_db.first("SELECT status FROM sources WHERE id = ?", active))["status"] == "active"
    assert run(empty_db.first("SELECT status FROM sources WHERE id = ?", mine))["status"] == "trial"


def test_a_feed_address_still_waiting_for_its_try_is_not_demoted(empty_db):
    waiting = _insert_source(empty_db, name="Waiting", url="https://boards.greenhouse.io/w", discovered_at="2026-09-25")
    other = _insert_source(empty_db, name="Other", url="https://o.example/careers", discovered_at="2026-09-25")

    count = run(sources_lifecycle.demote_stale_trials(empty_db, today=date(2026, 11, 24), exclude_ids={waiting}))

    assert count == 1
    assert run(empty_db.first("SELECT status FROM sources WHERE id = ?", waiting))["status"] == "trial"
    assert run(empty_db.first("SELECT status FROM sources WHERE id = ?", other))["status"] == "demoted"


def test_demoting_keeps_an_existing_note(empty_db):
    source_id = _insert_source(empty_db, discovered_at="2026-09-25", notes="Found via campus page.")

    run(sources_lifecycle.demote_stale_trials(empty_db, today=date(2026, 11, 24)))

    notes = run(empty_db.first("SELECT notes FROM sources WHERE id = ?", source_id))["notes"]
    assert notes.startswith("Found via campus page.")
    assert "Demoted" in notes


def test_demoting_many_sources_is_one_statement_not_one_per_row(empty_db):
    # D1 allows 100 bound parameters a query, and every statement is a network
    # round trip in production; 600 stale rows must not mean 600 calls.
    for n in range(250):
        _insert_source(empty_db, name=f"S{n}", url=f"https://s{n}.example", discovered_at="2026-09-25")
    statements = []
    real_run = empty_db.run

    async def counting_run(sql, *params):
        statements.append(sql)
        assert len(params) <= 100
        return await real_run(sql, *params)

    empty_db.run = counting_run
    count = run(sources_lifecycle.demote_stale_trials(empty_db, today=date(2026, 11, 24), exclude_ids=set(range(1, 121))))

    assert count == 130
    assert len(statements) == 1
