"""BR-001.2 and BR-001.3: feed health (`job/feed_health.py`).

A feed that keeps failing is counted and surfaced, a feed whose address no
longer exists is cleared instead of failing forever, and a Workday feed keeps
its own address so a careers-page change can't break it.
"""
import asyncio

from job import feed_health


def run(coro):
    return asyncio.run(coro)


def _insert_company(db, **overrides):
    row = {"name": "Acme", "status": "pinned"}
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    return run(db.run(f"INSERT INTO companies ({columns}) VALUES ({placeholders})", *row.values()))


def _company(db, company_id):
    return run(db.first("SELECT * FROM companies WHERE id = ?", company_id))


# -- counting -----------------------------------------------------------------

def test_failure_count_goes_up_on_an_error_and_resets_on_anything_else():
    assert feed_health.next_failure_count(0, "error") == 1
    assert feed_health.next_failure_count(4, "error") == 5
    assert feed_health.next_failure_count(4, "ok") == 0
    assert feed_health.next_failure_count(4, "no_source") == 0
    assert feed_health.next_failure_count(None, "error") == 1


def test_threshold_defaults_to_five_runs(empty_db):
    assert run(feed_health.load_failing_after_runs(empty_db)) == 5


def test_threshold_can_be_changed_with_a_setting(empty_db):
    run(empty_db.run("INSERT INTO settings (key, value) VALUES ('feed_failing_after_runs', '3')"))
    assert run(feed_health.load_failing_after_runs(empty_db)) == 3


def test_a_nonsense_threshold_setting_falls_back_to_the_default(empty_db):
    run(empty_db.run("INSERT INTO settings (key, value) VALUES ('feed_failing_after_runs', '\"soon\"')"))
    assert run(feed_health.load_failing_after_runs(empty_db)) == 5


def test_failing_feed_names_lists_only_tracked_companies_at_or_over_the_threshold(empty_db):
    _insert_company(empty_db, name="Zebra", feed_type="workday", feed_failures=5)
    _insert_company(empty_db, name="Acme", feed_type="greenhouse", feed_failures=7)
    _insert_company(empty_db, name="Almost", feed_type="greenhouse", feed_failures=4)
    _insert_company(empty_db, name="Gone", feed_type="greenhouse", feed_failures=9, status="removed")
    _insert_company(empty_db, name="No Feed", feed_type=None, feed_failures=9)

    assert run(feed_health.failing_feed_names(empty_db, 5)) == ["Acme", "Zebra"]


# -- BR-001.3: the feed keeps its own address ---------------------------------

def test_ensure_feed_url_copies_a_workday_site_link_from_the_careers_page(empty_db):
    company_id = _insert_company(
        empty_db, feed_type="workday", feed_id="zebra", careers_url="https://zebra.wd1.myworkdayjobs.com/Zebra_Careers",
    )
    company = _company(empty_db, company_id)

    assert run(feed_health.ensure_feed_url(empty_db, company)) is True
    assert company["feed_url"] == "https://zebra.wd1.myworkdayjobs.com/Zebra_Careers"
    assert _company(empty_db, company_id)["feed_url"] == "https://zebra.wd1.myworkdayjobs.com/Zebra_Careers"


def test_ensure_feed_url_leaves_an_existing_address_alone(empty_db):
    company_id = _insert_company(
        empty_db, feed_type="workday", feed_url="https://micron.wd1.myworkdayjobs.com/External",
        careers_url="https://micron.wd1.myworkdayjobs.com/Other",
    )
    company = _company(empty_db, company_id)

    assert run(feed_health.ensure_feed_url(empty_db, company)) is False
    assert _company(empty_db, company_id)["feed_url"] == "https://micron.wd1.myworkdayjobs.com/External"


def test_ensure_feed_url_never_copies_a_marketing_page(empty_db):
    company_id = _insert_company(
        empty_db, feed_type="workday", careers_url="https://www.micron.com/careers/university-recruiting",
    )
    company = _company(empty_db, company_id)

    assert run(feed_health.ensure_feed_url(empty_db, company)) is False
    assert _company(empty_db, company_id)["feed_url"] is None


def test_ensure_feed_url_ignores_feeds_that_need_no_address(empty_db):
    company_id = _insert_company(
        empty_db, feed_type="greenhouse", feed_id="acme", careers_url="https://boards.greenhouse.io/acme",
    )
    assert run(feed_health.ensure_feed_url(empty_db, _company(empty_db, company_id))) is False


# -- a feed whose address no longer exists is cleared --------------------------

def test_only_a_404_counts_as_a_dead_address():
    assert feed_health.is_dead_address_error("https://boards-api.greenhouse.io/v1/boards/etsy/jobs returned HTTP 404")
    assert not feed_health.is_dead_address_error("https://x.example/jobs returned HTTP 503")
    assert not feed_health.is_dead_address_error("https://x.example/jobs blocked the request (403); not retrying")
    assert not feed_health.is_dead_address_error("request to https://x.example failed: timed out")
    assert not feed_health.is_dead_address_error(None)


def test_clear_dead_feeds_clears_a_feed_that_has_404d_for_the_threshold(empty_db):
    company_id = _insert_company(
        empty_db, name="Etsy", feed_type="greenhouse", feed_id="etsy", careers_url="https://boards.greenhouse.io/etsy",
        last_feed_status="error", feed_failures=5,
        last_feed_error="https://boards-api.greenhouse.io/v1/boards/etsy/jobs returned HTTP 404",
    )

    cleared = run(feed_health.clear_dead_feeds(empty_db, threshold=5, now_iso="2026-11-24T08:00:00"))

    assert [c["name"] for c in cleared] == ["Etsy"]
    company = _company(empty_db, company_id)
    assert company["feed_type"] is None
    assert company["feed_id"] is None
    assert company["feed_url"] is None
    assert company["feed_failures"] == 0
    assert company["last_feed_status"] == "no_source"
    assert company["last_feed_error"] is None
    # The careers link was the dead board itself, so it goes too and the scout
    # looks for a real page.
    assert company["careers_url"] is None


def test_clear_dead_feeds_keeps_a_careers_page_that_is_not_the_dead_board(empty_db):
    company_id = _insert_company(
        empty_db, name="Unity", feed_type="greenhouse", feed_id="unity3d", careers_url="https://careers.unity.com/",
        last_feed_status="error", feed_failures=6,
        last_feed_error="https://boards-api.greenhouse.io/v1/boards/unity3d/jobs returned HTTP 404",
    )

    run(feed_health.clear_dead_feeds(empty_db, threshold=5, now_iso="2026-11-24T08:00:00"))

    company = _company(empty_db, company_id)
    assert company["feed_type"] is None
    assert company["careers_url"] == "https://careers.unity.com/"


def test_clear_dead_feeds_never_removes_a_link_henry_approved(empty_db):
    company_id = _insert_company(
        empty_db, name="Circle", feed_type="greenhouse", feed_id="circle", careers_url="https://boards.greenhouse.io/circle",
        careers_url_approved=1, last_feed_status="error", feed_failures=5,
        last_feed_error="https://boards-api.greenhouse.io/v1/boards/circle/jobs returned HTTP 404",
    )

    run(feed_health.clear_dead_feeds(empty_db, threshold=5, now_iso="2026-11-24T08:00:00"))

    company = _company(empty_db, company_id)
    assert company["feed_type"] is None
    assert company["careers_url"] == "https://boards.greenhouse.io/circle"


def test_clear_dead_feeds_leaves_feeds_that_are_under_the_threshold_or_failing_another_way(empty_db):
    recent = _insert_company(
        empty_db, name="Recent", feed_type="greenhouse", feed_id="recent", last_feed_status="error", feed_failures=2,
        last_feed_error="https://boards-api.greenhouse.io/v1/boards/recent/jobs returned HTTP 404",
    )
    outage = _insert_company(
        empty_db, name="Outage", feed_type="greenhouse", feed_id="outage", last_feed_status="error", feed_failures=9,
        last_feed_error="https://boards-api.greenhouse.io/v1/boards/outage/jobs returned HTTP 503",
    )

    assert run(feed_health.clear_dead_feeds(empty_db, threshold=5, now_iso="2026-11-24T08:00:00")) == []
    assert _company(empty_db, recent)["feed_type"] == "greenhouse"
    assert _company(empty_db, outage)["feed_type"] == "greenhouse"


def test_clear_dead_feeds_remembers_the_dead_address_so_it_is_not_adopted_again(empty_db):
    _insert_company(
        empty_db, name="Etsy", feed_type="greenhouse", feed_id="etsy", careers_url="https://boards.greenhouse.io/etsy",
        last_feed_status="error", feed_failures=5,
        last_feed_error="https://boards-api.greenhouse.io/v1/boards/etsy/jobs returned HTTP 404",
    )
    run(empty_db.run(
        "INSERT INTO sources (name, url, kind, status, discovered_by, discovered_at) "
        "VALUES ('Etsy careers', 'https://boards.greenhouse.io/etsy/', 'Company career sites', 'trial', 'claude-scout', "
        "'2026-11-20T08:00:00')"
    ))

    run(feed_health.clear_dead_feeds(empty_db, threshold=5, now_iso="2026-11-24T08:00:00"))

    sources = run(empty_db.all("SELECT * FROM sources"))
    assert len(sources) == 1  # the existing row is reused, matched on its normalized address
    assert sources[0]["status"] == "demoted"
    assert "404" in sources[0]["notes"]


def test_clear_dead_feeds_records_a_demoted_row_when_none_exists(empty_db):
    _insert_company(
        empty_db, name="Etsy", feed_type="greenhouse", feed_id="etsy", careers_url="https://boards.greenhouse.io/etsy",
        last_feed_status="error", feed_failures=5,
        last_feed_error="https://boards-api.greenhouse.io/v1/boards/etsy/jobs returned HTTP 404",
    )

    run(feed_health.clear_dead_feeds(empty_db, threshold=5, now_iso="2026-11-24T08:00:00"))

    source = run(empty_db.first("SELECT * FROM sources"))
    assert source["status"] == "demoted"
    assert source["url"] == "https://boards.greenhouse.io/etsy"
