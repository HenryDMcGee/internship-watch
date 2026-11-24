"""BR-001 end to end, through a real run against the test database:
feed failures are counted and alerted on, a partial feed never closes a
posting, dead feeds are cleared, and trial sources are adopted or demoted.
"""
import asyncio

import pytest

import job.run as run_module
from job.fetchers import FetchResult
from job.models import FetchedPosting
from job.verify.pipeline import VerifyOutcome


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _not_a_ranking_refresh_day(monkeypatch):
    monkeypatch.setattr(run_module, "is_quarterly_refresh_day", lambda _day: False)


def _insert_company(db, **overrides):
    row = {"name": "Acme", "status": "pinned", "feed_type": "greenhouse", "feed_id": "acme"}
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    return run(db.run(f"INSERT INTO companies ({columns}) VALUES ({placeholders})", *row.values()))


def _insert_source(db, **overrides):
    row = {
        "name": "Acme careers", "url": "https://boards.greenhouse.io/acme", "kind": "Company career sites",
        "covers": "", "status": "trial", "discovered_by": "claude-scout", "discovered_at": "2026-09-25T08:00:00-05:00",
    }
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    return run(db.run(f"INSERT INTO sources ({columns}) VALUES ({placeholders})", *row.values()))


def _failing(error="https://boards-api.greenhouse.io/v1/boards/acme/jobs returned HTTP 503"):
    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[], error=error)
    return fake_run_fetcher


def _ok(postings=()):
    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=list(postings))
    return fake_run_fetcher


def _company(db, company_id):
    return run(db.first("SELECT * FROM companies WHERE id = ?", company_id))


# -- BR-001.2 -----------------------------------------------------------------

def test_feed_failures_count_up_run_after_run_and_reset_when_the_feed_reads(empty_db, monkeypatch):
    company_id = _insert_company(empty_db)

    monkeypatch.setattr(run_module, "run_fetcher", _failing())
    run(run_module.run(empty_db, kind="daily"))
    assert _company(empty_db, company_id)["feed_failures"] == 1
    run(run_module.run(empty_db, kind="daily"))
    assert _company(empty_db, company_id)["feed_failures"] == 2

    monkeypatch.setattr(run_module, "run_fetcher", _ok())
    run(run_module.run(empty_db, kind="daily"))
    assert _company(empty_db, company_id)["feed_failures"] == 0


def test_a_feed_that_reaches_the_threshold_raises_an_alert_the_dashboard_shows(empty_db, monkeypatch):
    _insert_company(empty_db, name="NVIDIA", feed_failures=4)
    monkeypatch.setattr(run_module, "run_fetcher", _failing())

    run(run_module.run(empty_db, kind="daily"))

    alerts = run(empty_db.all("SELECT * FROM alerts WHERE type = 'feeds_failing'"))
    assert len(alerts) == 1
    assert "NVIDIA" in alerts[0]["message"]
    assert "5 or more runs in a row" in alerts[0]["message"]


def test_no_feed_alert_while_every_feed_is_under_the_threshold(empty_db, monkeypatch):
    _insert_company(empty_db, feed_failures=2)
    monkeypatch.setattr(run_module, "run_fetcher", _failing())

    run(run_module.run(empty_db, kind="daily"))

    assert run(empty_db.all("SELECT * FROM alerts WHERE type = 'feeds_failing'")) == []


def test_a_feed_whose_address_404s_for_the_threshold_is_cleared_and_the_summary_says_so(empty_db, monkeypatch):
    company_id = _insert_company(
        empty_db, name="Etsy", feed_id="etsy", careers_url="https://boards.greenhouse.io/etsy", feed_failures=4,
    )
    monkeypatch.setattr(run_module, "run_fetcher", _failing(
        "https://boards-api.greenhouse.io/v1/boards/etsy/jobs returned HTTP 404"
    ))

    result = run(run_module.run(empty_db, kind="daily"))

    assert _company(empty_db, company_id)["feed_type"] is None
    assert result["feeds_cleared"] == 1
    assert "Cleared 1 feed whose address no longer exists (Etsy)." in result["summary"]
    # Cleared, so it is no longer a failing feed to alert on.
    assert run(empty_db.all("SELECT * FROM alerts WHERE type = 'feeds_failing'")) == []


def test_a_workday_feed_with_no_address_on_file_is_cleared_and_the_summary_says_so(empty_db, monkeypatch):
    company_id = _insert_company(
        empty_db, name="Samsung Electronics", feed_type="workday", feed_id="samsung", feed_failures=4,
        careers_url="https://www.samsung.com/us/careers/internships/",
    )
    monkeypatch.setattr(run_module, "run_fetcher", _failing(
        "Samsung Electronics: feed address isn't a Workday site link"
    ))

    result = run(run_module.run(empty_db, kind="daily"))

    assert _company(empty_db, company_id)["feed_type"] is None
    assert result["feeds_cleared"] == 1
    assert "Cleared 1 Workday feed that has no site address on file (Samsung Electronics)." in result["summary"]


# -- BR-001.1: partial listings ----------------------------------------------

def _capture_verify(monkeypatch):
    seen = []

    def fake_verify(posting, **kwargs):
        seen.append({"url": posting["url"], **kwargs})
        return VerifyOutcome(status="open", verified_via="feed", verified_at=kwargs["now_iso"])

    monkeypatch.setattr(run_module, "verify_posting", fake_verify)
    return seen


def _insert_posting(db, company_id, url):
    return run(db.run(
        """INSERT INTO postings (company_id, company_name, title, matched_title, url, locations,
           location_match, first_seen, status)
           VALUES (?, 'Acme', 'MBA Product Manager Intern', 'MBA Product Manager Intern', ?, '[]', 'match',
                   '2026-11-01', 'unverified')""",
        company_id, url,
    ))


def test_a_workday_feed_is_treated_as_a_partial_listing_when_postings_are_checked(empty_db, monkeypatch):
    workday_id = _insert_company(
        empty_db, name="Acme", feed_type="workday", feed_id="acme", feed_url="https://acme.wd5.myworkdayjobs.com/Acme",
    )
    greenhouse_id = _insert_company(empty_db, name="Globex", feed_type="greenhouse", feed_id="globex")
    _insert_posting(empty_db, workday_id, "https://acme.wd5.myworkdayjobs.com/Acme/job/Austin/Found-Elsewhere_R1")
    _insert_posting(empty_db, greenhouse_id, "https://boards.greenhouse.io/globex/jobs/1")
    monkeypatch.setattr(run_module, "run_fetcher", _ok())
    seen = _capture_verify(monkeypatch)

    run(run_module.run(empty_db, kind="daily"))

    by_url = {s["url"]: s for s in seen}
    assert by_url["https://acme.wd5.myworkdayjobs.com/Acme/job/Austin/Found-Elsewhere_R1"]["feed_listing_complete"] is False
    assert by_url["https://boards.greenhouse.io/globex/jobs/1"]["feed_listing_complete"] is True


def test_a_posting_found_another_way_is_not_closed_when_the_workday_feed_comes_back(empty_db, monkeypatch):
    # The regression this change must not cause: 37 Workday feeds start reading
    # again, and every posting discovery found at those companies is missing
    # from the filtered listing.
    company_id = _insert_company(
        empty_db, name="Acme", feed_type="workday", feed_id="acme", feed_url="https://acme.wd5.myworkdayjobs.com/Acme",
    )
    posting_id = run(empty_db.run(
        """INSERT INTO postings (company_id, company_name, title, matched_title, url, locations,
           location_match, first_seen, status, user_status, user_status_at)
           VALUES (?, 'Acme', 'MBA Product Manager Intern', 'MBA Product Manager Intern',
                   'https://acme.wd5.myworkdayjobs.com/Acme/job/Austin/Found-Elsewhere_R1', '[]', 'match',
                   '2026-11-01', 'open', 'open', '2026-11-02T09:00:00')""",
        company_id,
    ))
    monkeypatch.setattr(run_module, "run_fetcher", _ok())

    run(run_module.run(empty_db, kind="daily"))

    posting = run(empty_db.first("SELECT * FROM postings WHERE id = ?", posting_id))
    assert posting["status"] == "open"
    assert posting["archived_at"] is None


def test_an_unchanged_partial_feed_does_not_vouch_for_postings_it_never_listed(empty_db, monkeypatch):
    company_id = _insert_company(
        empty_db, name="Acme", feed_type="workday", feed_id="acme", feed_url="https://acme.wd5.myworkdayjobs.com/Acme",
    )
    listed = "https://acme.wd5.myworkdayjobs.com/Acme/job/Austin/Sales-Intern_R2"
    elsewhere = "https://acme.wd5.myworkdayjobs.com/Acme/job/Austin/Found-Elsewhere_R1"
    _insert_posting(empty_db, company_id, elsewhere)
    monkeypatch.setattr(run_module, "run_fetcher", _ok([
        FetchedPosting(company_name="Acme", title="Sales Intern", url=listed, location_text="Austin, TX"),
    ]))
    seen = _capture_verify(monkeypatch)

    run(run_module.run(empty_db, kind="daily"))
    seen.clear()
    run(run_module.run(empty_db, kind="daily"))  # same feed again: unchanged

    assert [s["found_in_current_feed"] for s in seen if s["url"] == elsewhere] == [False]


# -- BR-001.3 -----------------------------------------------------------------

def test_a_workday_company_with_only_a_careers_link_gets_its_feed_address_saved(empty_db, monkeypatch):
    company_id = _insert_company(
        empty_db, name="Zebra", feed_type="workday", feed_id="zebra",
        careers_url="https://zebra.wd1.myworkdayjobs.com/Zebra_Careers",
    )
    seen = []

    def fake_run_fetcher(company, session=None):
        seen.append(company.get("feed_url"))
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))

    assert seen == ["https://zebra.wd1.myworkdayjobs.com/Zebra_Careers"]
    assert _company(empty_db, company_id)["feed_url"] == "https://zebra.wd1.myworkdayjobs.com/Zebra_Careers"


# -- BR-001.5 -----------------------------------------------------------------

def test_a_daily_run_adopts_a_trial_feed_and_reads_it_in_the_same_run(empty_db, monkeypatch):
    # Zebra's Workday feed read last run, so the Workday reader has proved
    # itself and Workday addresses may be tried (see the next test). Added
    # with the spec change that introduced that rule.
    _insert_company(
        empty_db, name="Zebra", feed_type="workday", feed_id="zebra", last_feed_status="ok",
        feed_url="https://zebra.wd1.myworkdayjobs.com/Zebra_Careers",
    )
    company_id = _insert_company(
        empty_db, name="Micron", feed_type="workday", feed_id="micron", last_feed_status="error",
        careers_url="https://www.micron.com/careers/university-recruiting",
    )
    _insert_source(empty_db, name="Micron careers (Workday)", url="https://micron.wd1.myworkdayjobs.com/External")

    def fake_run_fetcher(company, session=None):
        if company.get("feed_url") in (
            "https://micron.wd1.myworkdayjobs.com/External", "https://zebra.wd1.myworkdayjobs.com/Zebra_Careers",
        ):
            return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[],
                           error="Micron: feed address isn't a Workday site link")

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    result = run(run_module.run(empty_db, kind="daily"))

    company = _company(empty_db, company_id)
    assert company["feed_url"] == "https://micron.wd1.myworkdayjobs.com/External"
    assert company["last_feed_status"] == "ok"
    assert result["feeds_adopted"] == 1
    assert "Adopted 1 feed from the trial sources (Micron)." in result["summary"]


def test_workday_addresses_wait_until_a_workday_feed_has_read(empty_db, monkeypatch):
    # No Workday feed has read yet (every one was failing before this change).
    # The address is not tried, so it cannot be demoted by an unproven reader,
    # and it is not demoted for age while it waits.
    company_id = _insert_company(
        empty_db, name="Micron", feed_type="workday", feed_id="micron", last_feed_status="error",
        careers_url="https://www.micron.com/careers/university-recruiting",
    )
    source_id = _insert_source(
        empty_db, name="Micron careers (Workday)", url="https://micron.wd1.myworkdayjobs.com/External",
    )
    monkeypatch.setattr(run_module, "run_fetcher", _failing("Micron: feed address isn't a Workday site link"))

    result = run(run_module.run(empty_db, kind="daily"))

    assert result["feeds_adopted"] == 0
    assert _company(empty_db, company_id)["feed_url"] is None
    assert run(empty_db.first("SELECT status FROM sources WHERE id = ?", source_id))["status"] == "trial"


def test_a_daily_run_demotes_stale_trial_sources_and_reports_the_count(empty_db, monkeypatch):
    _insert_company(empty_db, last_feed_status="ok")
    for n in range(3):
        _insert_source(empty_db, name=f"Stale {n}", url=f"https://stale{n}.example/careers", discovered_at="2026-09-25")
    monkeypatch.setattr(run_module, "run_fetcher", _ok())

    result = run(run_module.run(empty_db, kind="daily"))

    assert result["sources_demoted"] == 3
    assert "Demoted 3 trial sources not adopted within 30 days." in result["summary"]
    assert run(empty_db.first("SELECT COUNT(*) AS n FROM sources WHERE status = 'demoted'"))["n"] == 3


def test_a_check_now_run_neither_adopts_nor_demotes(empty_db, monkeypatch):
    company_id = _insert_company(empty_db, name="Acme", feed_type=None, feed_id=None)
    _insert_source(empty_db)
    monkeypatch.setattr(run_module, "run_fetcher", _ok())

    result = run(run_module.run(empty_db, kind="check_now", company_ids=[company_id]))

    assert result["feeds_adopted"] == 0
    assert result["sources_demoted"] == 0
    assert run(empty_db.first("SELECT status FROM sources"))["status"] == "trial"
    assert _company(empty_db, company_id)["feed_type"] is None


def test_a_broken_lifecycle_step_never_stops_the_run(empty_db, monkeypatch):
    _insert_company(empty_db)
    monkeypatch.setattr(run_module, "run_fetcher", _ok())

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(run_module.sources_lifecycle, "adopt_trial_feeds", boom)
    monkeypatch.setattr(run_module.sources_lifecycle, "demote_stale_trials", boom)
    monkeypatch.setattr(run_module.feed_health, "clear_dead_feeds", boom)

    result = run(run_module.run(empty_db, kind="daily"))

    assert result["run_status"] in ("completed", "degraded")


# -- BR-004: a temporary refusal never costs us a source, and it is visible -----

RATE_LIMITED = "https://boards-api.greenhouse.io/v1/boards/acme/jobs returned HTTP 429"


def test_a_trial_address_that_is_refused_waits_for_the_next_run_and_the_summary_says_so(empty_db, monkeypatch):
    company_id = _insert_company(empty_db, name="Acme", feed_type=None, feed_id=None)
    source_id = _insert_source(empty_db, discovered_at="2026-10-20T08:00:00-05:00")  # past the 30 days
    monkeypatch.setattr(run_module, "today_central", lambda: __import__("datetime").date(2026, 11, 25))
    monkeypatch.setattr(run_module, "run_fetcher", _failing(RATE_LIMITED))

    result = run(run_module.run(empty_db, kind="daily"))

    assert run(empty_db.first("SELECT status FROM sources WHERE id = ?", source_id))["status"] == "trial"
    assert _company(empty_db, company_id)["feed_type"] is None
    assert result["feeds_adopted"] == 0
    assert result["sources_undecided"] == 1
    assert result["sources_demoted"] == 0
    assert "1 trial feed address could not be judged this run and waits for the next." in result["summary"]


def test_the_failing_feeds_alert_says_how_many_were_told_to_slow_down(empty_db, monkeypatch):
    _insert_company(
        empty_db, name="NVIDIA", feed_type="workday", feed_id="nvidia", feed_failures=4,
        feed_url="https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
    )
    monkeypatch.setattr(run_module, "run_fetcher", _failing(
        "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs returned HTTP 429"
    ))

    run(run_module.run(empty_db, kind="daily"))

    alert = run(empty_db.first("SELECT message FROM alerts WHERE type = 'feeds_failing'"))
    assert "NVIDIA" in alert["message"]
    assert "1 was told to slow down (HTTP 429)." in alert["message"]
