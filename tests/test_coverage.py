"""Coverage and the one-time careers backfill (issue #63)."""
import asyncio
import json
from datetime import date

from job import careers_snapshot, coverage

TODAY = date(2026, 10, 5)


def run(coro):
    return asyncio.run(coro)


def _company(db, **overrides):
    row = {"name": "Acme", "status": "pinned"}
    row.update(overrides)
    columns = ", ".join(row)
    return run(db.run(f"INSERT INTO companies ({columns}) VALUES ({', '.join('?' for _ in row)})", *row.values()))


def _source(db, name, url, *, kind="Company career sites", covers="", status="trial", found_count=0):
    return run(db.run(
        "INSERT INTO sources (name, url, kind, covers, status, discovered_by, discovered_at, found_count) "
        "VALUES (?, ?, ?, ?, ?, 'claude-scout', '2026-09-20', ?)",
        name, url, kind, covers, status, found_count,
    ))


def _covered(db):
    companies = run(db.all("SELECT * FROM companies"))
    return run(coverage.covered_company_ids(db, companies, today=TODAY))


def test_a_working_feed_is_covered_but_a_feed_that_failed_is_not(empty_db):
    ok = _company(empty_db, name="Ok", feed_type="greenhouse", last_feed_status="ok")
    failing = _company(empty_db, name="Failing", feed_type="greenhouse", last_feed_status="error")
    unread = _company(empty_db, name="Unread", feed_type="greenhouse")
    assert _covered(empty_db) == {ok}
    assert failing not in _covered(empty_db) and unread not in _covered(empty_db)


def test_a_careers_page_that_loaded_in_the_last_7_days_is_covered(empty_db):
    recent = _company(empty_db, name="Recent", careers_url="https://recent.example/jobs")
    stale = _company(empty_db, name="Stale", careers_url="https://stale.example/jobs")
    failed = _company(empty_db, name="Failed", careers_url="https://failed.example/jobs")
    for company_id, now in ((recent, "2026-10-02T08:00:00"), (stale, "2026-09-20T08:00:00")):
        run(careers_snapshot.record_changed(empty_db, company_id, "h", method="backup", now_iso=now, existing=None))
    run(careers_snapshot.record_failure(empty_db, failed, reason="blocked", now_iso="2026-10-04T08:00:00", existing=None))

    assert _covered(empty_db) == {recent}


def test_a_sources_row_alone_is_not_coverage(empty_db):
    company = _company(empty_db, name="Acme", last_source_found_at="2026-10-04")
    _source(empty_db, "Acme Careers", "https://acme.example/careers", covers="Acme MBA internships")
    assert company not in _covered(empty_db)


# -- backfill ------------------------------------------------------------------

def _companies(db):
    return [dict(r, aliases=json.loads(r.get("aliases") or "[]")) for r in run(db.all("SELECT * FROM companies"))]


def test_backfill_attaches_the_best_careers_page_from_the_sources_table(empty_db):
    acme = _company(empty_db, name="Acme")
    _source(empty_db, "Acme Careers", "https://acme.example/careers", covers="Acme MBA internships.")
    _source(empty_db, "Acme Interns", "https://acme.example/careers/intern", covers="Acme internships.")

    attached = run(coverage.backfill_careers_urls(empty_db, _companies(empty_db)))

    assert [a["name"] for a in attached] == ["Acme"]
    row = run(empty_db.first("SELECT * FROM companies WHERE id = ?", acme))
    assert row["careers_url"] == "https://acme.example/careers/intern"  # the internship-filtered one wins
    assert row["careers_note"] == "careers page, no feed"
    assert row["careers_url_approved"] == 0
    assert row["feed_type"] is None


def test_backfill_sets_feed_type_when_the_page_is_on_a_recognized_platform(empty_db):
    acme = _company(empty_db, name="Acme")
    _source(empty_db, "Acme Careers", "https://boards.greenhouse.io/acme", covers="Acme MBA internships.")

    run(coverage.backfill_careers_urls(empty_db, _companies(empty_db)))

    row = run(empty_db.first("SELECT * FROM companies WHERE id = ?", acme))
    assert row["feed_type"] == "greenhouse" and row["feed_id"] == "acme"
    assert row["careers_note"] is None


def test_backfill_skips_companies_that_already_have_a_feed_or_careers_url(empty_db):
    with_feed = _company(empty_db, name="Feedco", feed_type="lever", feed_id="feedco")
    with_url = _company(empty_db, name="Urlco", careers_url="https://urlco.example/jobs")
    _source(empty_db, "Feedco Careers", "https://feedco.example/careers", covers="Feedco")
    _source(empty_db, "Urlco Careers", "https://urlco.example/other", covers="Urlco")

    attached = run(coverage.backfill_careers_urls(empty_db, _companies(empty_db)))

    assert attached == []
    assert run(empty_db.first("SELECT careers_url FROM companies WHERE id = ?", with_url))["careers_url"] == (
        "https://urlco.example/jobs"
    )
    assert run(empty_db.first("SELECT careers_url FROM companies WHERE id = ?", with_feed))["careers_url"] is None


def test_backfill_ignores_job_boards_and_demoted_sources_and_matches_whole_words(empty_db):
    _company(empty_db, name="Arm")
    _company(empty_db, name="Meta")
    _source(empty_db, "Built In", "https://builtin.com", kind="Job boards", covers="Meta and Arm listings")
    _source(empty_db, "Armstrong Careers", "https://armstrong.example/jobs", covers="Armstrong World Industries")
    _source(empty_db, "Meta Careers", "https://meta.example/jobs", covers="Meta", status="demoted")

    assert run(coverage.backfill_careers_urls(empty_db, _companies(empty_db))) == []


def test_backfill_on_the_seed_data_attaches_pages_only_to_companies_the_sources_name(empty_db):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import seed

    for sql, params in seed.build_statements():
        run(empty_db.run(sql, *params))
    companies = _companies(empty_db)
    without = [c for c in companies if not c["feed_type"] and not c["careers_url"]]

    attached = run(coverage.backfill_careers_urls(empty_db, companies))
    print(f"SEED BACKFILL: {len(attached)} of {len(without)} companies with no feed and no careers_url")

    assert len(without) == len(companies)  # the seed has no careers links on file
    assert 0 < len(attached) < len(without)
    sources = {s["url"] for s in run(empty_db.all("SELECT url FROM sources WHERE kind = 'Company career sites'"))}
    assert all(a["url"] in sources for a in attached)
    after = run(empty_db.first("SELECT COUNT(*) AS n FROM companies WHERE careers_url IS NOT NULL"))["n"]
    assert after == len(attached)


def test_run_backfill_once_runs_one_time_and_reports_how_many(empty_db, capsys):
    _company(empty_db, name="Acme")
    _company(empty_db, name="Beta")
    _source(empty_db, "Acme Careers", "https://acme.example/careers", covers="Acme")

    first = run(coverage.run_backfill_once(empty_db, _companies(empty_db), now_iso="2026-10-05T08:00:00"))
    second = run(coverage.run_backfill_once(empty_db, _companies(empty_db), now_iso="2026-10-06T08:00:00"))

    assert first == 1
    assert second is None
    assert "attached a careers page from the sources table to 1 company" in capsys.readouterr().out
    saved = json.loads(run(empty_db.first("SELECT value FROM settings WHERE key = 'careers_backfill'"))["value"])
    assert saved["attached"] == 1
