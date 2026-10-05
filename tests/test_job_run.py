import asyncio
import json
import sqlite3

import pytest

import job.run as run_module
from conftest import adapt_discovery_reply
from job import config, feed_snapshot, fingerprint
from job.agent import client as agent_client
from job.agent.client import ClaudeStepResult
from job.fetchers import FetchResult
from job.models import FetchedPosting


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _not_a_ranking_refresh_day(monkeypatch):
    # These tests count companies and postings from a single fake feed. On the
    # first weekday of a quarter a daily run also imports the 100-company
    # ranking, so they failed on those real calendar days; pin the date.
    monkeypatch.setattr(run_module, "is_quarterly_refresh_day", lambda _day: False)


def _insert_company(db, **overrides):
    row = {"name": "Acme", "status": "pinned", "feed_type": "greenhouse", "feed_id": "acme"}
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    return run(db.run(f"INSERT INTO companies ({columns}) VALUES ({placeholders})", *row.values()))


def test_run_stores_matching_posting_and_verifies_it_open(empty_db, monkeypatch):
    _insert_company(empty_db)

    def fake_run_fetcher(company, session=None):
        return FetchResult(
            company_id=company["id"], company_name=company["name"],
            postings=[FetchedPosting(
                company_name="Acme", title="MBA Product Manager Intern, Summer 2027",
                url="https://boards.greenhouse.io/acme/jobs/1", location_text="San Francisco, CA",
                posted_date="2026-09-01",
            )],
        )

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)

    result = run(run_module.run(empty_db, kind="daily"))
    assert result["new_count"] == 1
    assert result["companies_checked"] == 1
    assert result["feed_errors"] == 0
    assert result["no_source_count"] == 0

    postings = run(empty_db.all("SELECT * FROM postings"))
    assert len(postings) == 1
    posting = postings[0]
    assert posting["status"] == "open"
    assert posting["verified_via"] == "feed"
    assert posting["matched_title"] == "MBA Product Manager Intern"
    assert posting["location_match"] == "match"
    assert posting["archived_at"] is None

    runs = run(empty_db.all("SELECT * FROM runs"))
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["new_count"] == 1

    stats = run(empty_db.all("SELECT * FROM run_stats"))
    assert len(stats) == 1
    assert stats[0]["found"] == 1
    assert stats[0]["useful"] == 1


def test_run_does_not_store_non_matching_postings(empty_db, monkeypatch):
    _insert_company(empty_db)

    def fake_run_fetcher(company, session=None):
        return FetchResult(
            company_id=company["id"], company_name=company["name"],
            postings=[FetchedPosting(
                company_name="Acme", title="Software Engineer Intern",
                url="https://boards.greenhouse.io/acme/jobs/2", location_text="San Francisco, CA",
            )],
        )

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)

    result = run(run_module.run(empty_db, kind="daily"))
    assert result["new_count"] == 0
    assert run(empty_db.all("SELECT * FROM postings")) == []


def test_run_archives_posting_missing_from_next_feed_check(empty_db, monkeypatch):
    company_id = _insert_company(empty_db)
    posting_id = run(empty_db.run(
        """INSERT INTO postings (company_id, company_name, title, matched_title, url, locations,
           location_match, first_seen, status)
           VALUES (?, 'Acme', 'MBA Product Manager Intern', 'MBA Product Manager Intern',
                   'https://boards.greenhouse.io/acme/jobs/1', '[]', 'match', '2026-09-19', 'open')""",
        company_id,
    ))

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))

    posting = run(empty_db.first("SELECT * FROM postings WHERE id = ?", posting_id))
    assert posting["status"] == "closed"
    assert posting["verified_via"] == "feed"
    assert posting["archived_at"] is not None
    assert posting["archive_reason"] == "No longer listed in the company's feed."


def test_run_keeps_user_mark_open_status_while_still_in_feed(empty_db, monkeypatch):
    # A posting Henry marked open must keep that status on later runs as long
    # as it's still listed in the company's own feed.
    company_id = _insert_company(empty_db)
    posting_id = run(empty_db.run(
        """INSERT INTO postings (company_id, company_name, title, matched_title, url, locations,
           location_match, first_seen, status, user_status, user_status_at)
           VALUES (?, 'Acme', 'MBA Product Manager Intern', 'MBA Product Manager Intern',
                   'https://boards.greenhouse.io/acme/jobs/1', '[]', 'match', '2026-09-19', 'closed',
                   'open', '2026-09-19T08:00:00')""",
        company_id,
    ))

    def fake_run_fetcher(company, session=None):
        return FetchResult(
            company_id=company["id"], company_name=company["name"],
            postings=[FetchedPosting(
                company_name="Acme", title="MBA Product Manager Intern",
                url="https://boards.greenhouse.io/acme/jobs/1", location_text="San Francisco, CA",
            )],
        )

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))

    posting = run(empty_db.first("SELECT * FROM postings WHERE id = ?", posting_id))
    assert posting["status"] == "open"
    assert posting["verified_via"] == "user"


def test_run_overrides_user_mark_open_once_posting_leaves_feed(empty_db, monkeypatch):
    # The one exception: a Mark open answer stops sticking once the posting
    # actually disappears from the company's own feed.
    company_id = _insert_company(empty_db)
    posting_id = run(empty_db.run(
        """INSERT INTO postings (company_id, company_name, title, matched_title, url, locations,
           location_match, first_seen, status, user_status, user_status_at)
           VALUES (?, 'Acme', 'MBA Product Manager Intern', 'MBA Product Manager Intern',
                   'https://boards.greenhouse.io/acme/jobs/1', '[]', 'match', '2026-09-19', 'open',
                   'open', '2026-09-19T08:00:00')""",
        company_id,
    ))

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))

    posting = run(empty_db.first("SELECT * FROM postings WHERE id = ?", posting_id))
    assert posting["status"] == "closed"
    assert posting["verified_via"] == "feed"
    assert posting["archived_at"] is not None


def test_run_recognizes_a_feed_repeat_and_still_verifies_it(empty_db, monkeypatch):
    # A posting the company's own feed lists again today (found via
    # fingerprint match rather than an exact URL) must not become a second
    # row, and must still get its normal daily status check in this same run.
    company_id = _insert_company(empty_db)
    posting_id = run(empty_db.run(
        """INSERT INTO postings (company_id, company_name, title, matched_title, url, locations,
           location_match, first_seen, status)
           VALUES (?, 'Acme', 'MBA Product Manager Intern', 'MBA Product Manager Intern',
                   'https://boards.greenhouse.io/acme/jobs/1', '[]', 'match', '2026-09-18', 'unverified')""",
        company_id,
    ))

    def fake_run_fetcher(company, session=None):
        return FetchResult(
            company_id=company["id"], company_name=company["name"],
            postings=[FetchedPosting(
                company_name="Acme", title="MBA Product Manager Intern",
                url="https://boards.greenhouse.io/acme/jobs/1", location_text="San Francisco, CA",
            )],
        )

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    result = run(run_module.run(empty_db, kind="daily"))

    assert result["repeats_recognized"] == 1
    assert result["new_count"] == 0
    postings = run(empty_db.all("SELECT * FROM postings"))
    assert len(postings) == 1
    assert postings[0]["id"] == posting_id
    assert postings[0]["status"] == "open"
    assert postings[0]["verified_via"] == "feed"
    assert postings[0]["verified_at"] is not None


def test_run_summary_reports_repeats_recognized_and_rejections_skipped(empty_db, monkeypatch):
    company_id = _insert_company(empty_db)

    existing_urls = [f"https://boards.greenhouse.io/acme/jobs/{i}" for i in range(3)]
    for i, url in enumerate(existing_urls):
        run(empty_db.run(
            """INSERT INTO postings (company_id, company_name, title, matched_title, url, locations,
               location_match, first_seen, status)
               VALUES (?, 'Acme', ?, ?, ?, '[]', 'match', '2026-09-18', 'unverified')""",
            company_id, f"MBA Product Manager Intern {i}", f"MBA Product Manager Intern {i}", url,
        ))

    settings_ver = fingerprint.settings_version(config.load_titles(), config.DEFAULT_LOCATIONS)
    rejection_urls = [f"https://boards.greenhouse.io/acme/jobs/rejected-{i}" for i in range(2)]
    for i, url in enumerate(rejection_urls):
        fp = fingerprint.compute_fingerprint(
            company_name="Acme", title=f"Marketing Intern {i}", location_text="San Francisco, CA", url=url,
        )
        run(fingerprint.record_rejection(
            empty_db, fp, reason="excluded word 'marketing'", settings_version=settings_ver,
            source="greenhouse", now_iso="2026-09-19T08:00:00",
        ))

    def fake_run_fetcher(company, session=None):
        postings = [
            FetchedPosting(
                company_name="Acme", title=f"MBA Product Manager Intern {i}", url=existing_urls[i],
                location_text="San Francisco, CA",
            )
            for i in range(3)
        ] + [
            FetchedPosting(
                company_name="Acme", title=f"Marketing Intern {i}", url=rejection_urls[i],
                location_text="San Francisco, CA",
            )
            for i in range(2)
        ]
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=postings)

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    result = run(run_module.run(empty_db, kind="daily"))

    assert result["repeats_recognized"] == 3
    assert result["rejections_skipped"] == 2
    assert "3 repeat(s) recognized" in result["summary"]
    assert "2 remembered feed rejection(s) skipped" in result["summary"]
    assert "0 remembered classifier rejection(s) skipped" in result["summary"]
    assert result["counts"]["feed_rejections_skipped"] == 2
    # The remembered rejections must not have created any new postings.
    assert len(run(empty_db.all("SELECT * FROM postings"))) == 3


def test_run_prunes_wrong_cycle_posting_even_when_still_open(empty_db, monkeypatch):
    # Old seed data (phase 1) never applied the Summer 2027 cycle rule, so
    # the daily job is what catches wrong-cycle postings already in D1.
    company_id = _insert_company(empty_db)
    posting_id = run(empty_db.run(
        """INSERT INTO postings (company_id, company_name, title, matched_title, url, locations,
           location_match, first_seen, status)
           VALUES (?, 'Acme', 'MBA Class of 2027 Product Manager', 'MBA Product Manager Intern',
                   'https://boards.greenhouse.io/acme/jobs/9', '[]', 'match', '2026-09-19', 'open')""",
        company_id,
    ))

    def fake_run_fetcher(company, session=None):
        return FetchResult(
            company_id=company["id"], company_name=company["name"],
            postings=[FetchedPosting(
                company_name="Acme", title="MBA Class of 2027 Product Manager",
                url="https://boards.greenhouse.io/acme/jobs/9", location_text="San Francisco, CA",
            )],
        )

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))

    posting = run(empty_db.first("SELECT * FROM postings WHERE id = ?", posting_id))
    assert posting["archived_at"] is not None
    assert "cycle" in posting["archive_reason"].lower()


def test_run_check_request_only_covers_its_companies(empty_db, monkeypatch):
    included_id = _insert_company(empty_db, name="Acme", feed_id="acme")
    excluded_id = _insert_company(empty_db, name="Globex", feed_id="globex")

    calls = []

    def fake_run_fetcher(company, session=None):
        calls.append(company["id"])
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)

    request_id = run(empty_db.run(
        "INSERT INTO check_requests (company_ids, requested_at, status) VALUES (?, '2026-09-20T08:00:00', 'pending')",
        f"[{included_id}]",
    ))

    result = run(run_module.run_check_request(empty_db, request_id))
    assert calls == [included_id]
    assert excluded_id not in calls

    request = run(empty_db.first("SELECT * FROM check_requests WHERE id = ?", request_id))
    assert request["status"] == "completed"
    assert result["kind"] == "check_now"


def test_run_counts_missing_feed_as_no_source_not_an_error(empty_db, monkeypatch):
    # A company with no feed_type on file (the state every seeded company
    # started in) has no known source yet -- it's phase 3's source scout's
    # job, not a broken feed, and must not inflate the error count.
    _insert_company(empty_db, feed_type=None, feed_id=None)

    result = run(run_module.run(empty_db, kind="daily"))
    assert result["feed_errors"] == 0
    assert result["no_source_count"] == 1
    assert "0 feed error(s)" in result["summary"]
    assert "1 with no source yet" in result["summary"]

    company = run(empty_db.first("SELECT * FROM companies"))
    assert company["last_feed_status"] == "no_source"
    assert company["last_feed_error"] is None


def test_run_counts_real_fetch_failure_as_a_feed_error_and_stores_it(empty_db, monkeypatch):
    _insert_company(empty_db)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[], error="boards-api.greenhouse.io returned HTTP 404")

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)

    result = run(run_module.run(empty_db, kind="daily"))
    assert result["feed_errors"] == 1
    assert result["no_source_count"] == 0
    assert "1 feed error(s)" in result["summary"]
    assert "0 with no source yet" in result["summary"]

    company = run(empty_db.first("SELECT * FROM companies"))
    assert company["last_feed_status"] == "error"
    assert company["last_feed_error"] == "boards-api.greenhouse.io returned HTTP 404"


def test_run_marks_successful_feed_check_ok(empty_db, monkeypatch):
    _insert_company(empty_db)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))

    company = run(empty_db.first("SELECT * FROM companies"))
    assert company["last_feed_status"] == "ok"
    assert company["last_feed_error"] is None


def test_daily_run_without_claude_credentials_says_so_in_the_summary_and_alerts(empty_db, monkeypatch):
    # tests/conftest.py's autouse fixture already clears both credential env
    # vars, so this is the default state every test runs in -- issue #16:
    # a run without them must never look identical to one where the Claude
    # steps ran, silently.
    _insert_company(empty_db)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    result = run(run_module.run(empty_db, kind="daily"))

    assert "Claude steps skipped" in result["summary"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" in result["summary"]

    alerts = run(empty_db.all("SELECT * FROM alerts"))
    assert [a["type"] for a in alerts] == ["claude_not_configured"]


def test_daily_run_without_claude_credentials_alerts_once_per_day(empty_db, monkeypatch):
    _insert_company(empty_db)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))
    run(run_module.run(empty_db, kind="daily"))

    alerts = run(empty_db.all("SELECT * FROM alerts"))
    assert len(alerts) == 1


def test_check_now_run_without_claude_credentials_does_not_alert(empty_db, monkeypatch):
    # check_now runs never call the Claude steps in the first place (see
    # _run_body), so a missing credential there isn't news.
    company_id = _insert_company(empty_db)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    request_id = run(empty_db.run(
        "INSERT INTO check_requests (company_ids, requested_at, status) VALUES (?, '2026-09-20T08:00:00', 'pending')",
        f"[{company_id}]",
    ))
    run(run_module.run_check_request(empty_db, request_id))

    assert run(empty_db.all("SELECT * FROM alerts")) == []


def test_daily_run_actually_calls_discovery_and_scout_for_no_source_companies(empty_db, monkeypatch):
    # Confirms discovery and the scout run (and record their usage and
    # results) end to end through job.run.run(), not just through
    # job.agent.pipeline directly -- issue #20's bug was only visible at
    # this level, since job.run.run() is what the daily workflow calls.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    _insert_company(empty_db, feed_type=None, feed_id=None)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)

    def fake_run_claude_step(step, prompt, *, model, **kwargs):
        text = {
            "discovery": '{"results": []}',
            "scout": '{"sources": [{"company_name": "Acme", "name": "Acme careers", '
                     '"url": "https://acme.example/careers", "kind": "Company career sites", "covers": "Acme"}]}',
            "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
        }[step]
        # A real discovery/scout call with tools enabled always searches at least
        # once -- zero would now fail the step loudly (issue #20), which this test
        # isn't about; see test_daily_run_flags_it_when_discovery_and_scout_make_zero_web_searches.
        if step == "discovery":
            text = adapt_discovery_reply(text, prompt)
        web_searches = 3 if step in ("discovery", "scout") else 0
        return ClaudeStepResult(step=step, model=model, text=text, web_searches=web_searches)

    monkeypatch.setattr(agent_client, "run_claude_step", fake_run_claude_step)

    result = run(run_module.run(empty_db, kind="daily"))

    usage_steps = [r["step"] for r in run(empty_db.all("SELECT step FROM usage ORDER BY id"))]
    assert "discovery" in usage_steps
    assert "scout" in usage_steps

    sources = run(empty_db.all("SELECT * FROM sources"))
    assert len(sources) == 1

    assert "Claude step issues" not in result["summary"]
    assert run(empty_db.all("SELECT * FROM alerts")) == []


def test_daily_run_new_count_excludes_a_discovery_that_classification_rejects(empty_db, monkeypatch):
    # issue #26: run 14 reported 1 new posting in its summary, but the
    # posting hadn't actually passed classification -- new_count must only
    # reflect postings that end up matched and still on the dashboard.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    _insert_company(empty_db, feed_type=None, feed_id=None)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)

    def fake_run_claude_step(step, prompt, *, model, **kwargs):
        text = {
            "discovery": '{"results": [{"company_name": "Acme", "title": "Software Engineer Intern", '
                         '"url": "https://acme.example/1", "location_text": "SF"}]}',
            "classification": '{"decisions": [{"id": 1, "decision": "reject", "matched_title": null, '
                               '"reason": "not a PM role"}]}',
            "scout": '{"sources": []}',
            "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
        }[step]
        if step == "discovery":
            text = adapt_discovery_reply(text, prompt)
        web_searches = 3 if step in ("discovery", "scout") else 0
        return ClaudeStepResult(step=step, model=model, text=text, web_searches=web_searches)

    monkeypatch.setattr(agent_client, "run_claude_step", fake_run_claude_step)

    result = run(run_module.run(empty_db, kind="daily"))
    assert result["new_count"] == 0

    postings = run(empty_db.all("SELECT * FROM postings"))
    assert len(postings) == 1
    assert postings[0]["archived_at"] is not None

    runs = run(empty_db.all("SELECT * FROM runs"))
    assert runs[0]["new_count"] == 0


def test_daily_run_new_count_counts_a_discovery_that_classification_matches(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    _insert_company(empty_db, feed_type=None, feed_id=None)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)

    def fake_run_claude_step(step, prompt, *, model, **kwargs):
        text = {
            "discovery": '{"results": [{"company_name": "Acme", "title": "MBA Product Manager Intern", '
                         '"url": "https://acme.example/1", "location_text": "San Francisco, CA"}]}',
            "classification": '{"decisions": [{"id": 1, "decision": "match", '
                               '"matched_title": "MBA Product Manager Intern", "title_en": null}]}',
            "scout": '{"sources": []}',
            "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
        }[step]
        if step == "discovery":
            text = adapt_discovery_reply(text, prompt)
        web_searches = 3 if step in ("discovery", "scout") else 0
        return ClaudeStepResult(step=step, model=model, text=text, web_searches=web_searches)

    monkeypatch.setattr(agent_client, "run_claude_step", fake_run_claude_step)

    result = run(run_module.run(empty_db, kind="daily"))
    assert result["new_count"] == 1


def test_daily_run_flags_it_when_discovery_and_scout_do_not_actually_run(empty_db, monkeypatch):
    # Regression for issue #20: Henry's run 8 recorded a single Claude call
    # (improvement) even though all 100 companies had no source yet --
    # discovery and the scout had failed, but the run summary still read
    # "0 with no source yet" ... clean, and nothing alerted. A daily run
    # that does no discovery/scout work must never look clean.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    _insert_company(empty_db, feed_type=None, feed_id=None)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)

    def fake_run_claude_step(step, prompt, *, model, **kwargs):
        if step in ("discovery", "scout"):
            raise RuntimeError(f"{step} step timed out after 900s")
        return ClaudeStepResult(
            step=step, model=model, text='{"search_plan": null, "proposals": [], "title_suggestions": []}',
        )

    monkeypatch.setattr(agent_client, "run_claude_step", fake_run_claude_step)

    result = run(run_module.run(empty_db, kind="daily"))

    usage_steps = [r["step"] for r in run(empty_db.all("SELECT step FROM usage ORDER BY id"))]
    assert "discovery" not in usage_steps
    assert "scout" not in usage_steps

    assert "discovery" in result["summary"]
    assert "scout" in result["summary"]

    alerts = run(empty_db.all("SELECT * FROM alerts"))
    assert any(a["type"] == "claude_step_issue" for a in alerts)


def test_daily_run_flags_it_when_discovery_and_scout_make_zero_web_searches(empty_db, monkeypatch):
    # A second way run 8's failure could hide: discovery/scout replying with valid,
    # well-formed JSON but having made zero actual web searches -- Claude answering
    # from memory instead of using its tools. That must fail loudly too, not read as
    # a clean "found nothing" run (issue #20).
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    _insert_company(empty_db, feed_type=None, feed_id=None)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)

    def fake_run_claude_step(step, prompt, *, model, **kwargs):
        text = {
            "discovery": '{"results": []}',
            "scout": '{"sources": []}',
            "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
        }[step]
        return ClaudeStepResult(step=step, model=model, text=text, web_searches=0)

    monkeypatch.setattr(agent_client, "run_claude_step", fake_run_claude_step)

    result = run(run_module.run(empty_db, kind="daily"))

    usage_steps = [r["step"] for r in run(empty_db.all("SELECT step FROM usage ORDER BY id"))]
    assert "discovery" in usage_steps  # the call happened and spent real budget
    assert "scout" in usage_steps

    assert "discovery" in result["summary"]
    assert "scout" in result["summary"]

    alerts = run(empty_db.all("SELECT * FROM alerts"))
    assert any(a["type"] == "claude_step_issue" for a in alerts)


def test_run_records_sources_found_as_companies_with_a_useful_posting(empty_db, monkeypatch):
    company_a = _insert_company(empty_db, name="Acme", feed_id="acme")
    company_b = _insert_company(empty_db, name="Globex", feed_id="globex")

    def fake_run_fetcher(company, session=None):
        if company["id"] == company_a:
            return FetchResult(
                company_id=company["id"], company_name=company["name"],
                postings=[FetchedPosting(
                    company_name="Acme", title="MBA Product Manager Intern, Summer 2027",
                    url="https://boards.greenhouse.io/acme/jobs/1", location_text="San Francisco, CA",
                )],
            )
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)

    result = run(run_module.run(empty_db, kind="daily"))
    assert result["sources_found"] == 1


# ---------------------------------------------------------------------------
# Search now: linking a `search_requests` row to the daily run it started
# ---------------------------------------------------------------------------

def test_daily_run_claims_the_pending_search_request_and_marks_it_completed(empty_db, monkeypatch):
    _insert_company(empty_db)
    request_id = run(empty_db.run(
        "INSERT INTO search_requests (requested_at, status) VALUES ('2026-09-25T08:00:00', 'pending')"
    ))

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))

    request = run(empty_db.first("SELECT * FROM search_requests WHERE id = ?", request_id))
    assert request["status"] == "completed"
    assert request["run_id"] is not None
    result = json.loads(request["result"])
    assert result["kind"] == "daily"


def test_daily_run_marks_the_search_request_failed_on_a_crash(empty_db, monkeypatch):
    _insert_company(empty_db)
    request_id = run(empty_db.run(
        "INSERT INTO search_requests (requested_at, status) VALUES ('2026-09-25T08:00:00', 'pending')"
    ))

    def broken_fetcher(company, session=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(run_module, "run_fetcher", broken_fetcher)
    with pytest.raises(RuntimeError):
        run(run_module.run(empty_db, kind="daily"))

    request = run(empty_db.first("SELECT * FROM search_requests WHERE id = ?", request_id))
    assert request["status"] == "failed"
    assert "boom" in json.loads(request["result"])["error"]


def test_daily_run_without_a_pending_search_request_leaves_the_table_empty(empty_db, monkeypatch):
    # A scheduled 8am run or a manual run from the Actions tab has no
    # dashboard-side request to link -- must not create or touch one.
    _insert_company(empty_db)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))

    assert run(empty_db.all("SELECT * FROM search_requests")) == []


def test_check_now_run_does_not_claim_a_pending_search_request(empty_db, monkeypatch):
    # search_requests is for Search now (kind="daily") only; a Check now run
    # (kind="check_now") must leave a pending search request alone.
    company_id = _insert_company(empty_db)
    request_id = run(empty_db.run(
        "INSERT INTO search_requests (requested_at, status) VALUES ('2026-09-25T08:00:00', 'pending')"
    ))
    check_request_id = run(empty_db.run(
        "INSERT INTO check_requests (company_ids, requested_at, status) VALUES (?, '2026-09-25T08:00:00', 'pending')",
        f"[{company_id}]",
    ))

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run_check_request(empty_db, check_request_id))

    request = run(empty_db.first("SELECT * FROM search_requests WHERE id = ?", request_id))
    assert request["status"] == "pending"


def test_run_marks_run_row_failed_instead_of_leaving_it_running(empty_db, monkeypatch):
    _insert_company(empty_db)

    def broken_fetcher(company, session=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(run_module, "run_fetcher", broken_fetcher)

    with pytest.raises(RuntimeError):
        run(run_module.run(empty_db, kind="daily"))

    runs = run(empty_db.all("SELECT * FROM runs"))
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert "boom" in runs[0]["summary"]
    # A crashed run must still get a finish time, or the dashboard's Search
    # now button (gated on no run being "still running") stays locked forever.
    assert runs[0]["finished_at"] is not None


def test_verify_and_prune_survives_more_than_100_companies(empty_db):
    # The daily run crashed in `_verify_and_prune` once the number of
    # ranked/pinned companies passed D1's 100-bound-parameter limit (issue
    # #43): the `company_id IN (?, ?, ...)` clause bound one parameter per
    # company. Pinning the test connection's own SQLite variable limit to
    # the same 100 makes this test fail the same way a real D1 query would
    # if the `json_each`-based fix regresses.
    empty_db._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 100)
    companies = [{"id": _insert_company(empty_db, name=f"Company {i}")} for i in range(500)]

    run(run_module._verify_and_prune(
        empty_db, companies, feed_ok={}, current_urls={},
        browser_budget=run_module.BrowserBudget(remaining=50),
    ))


# ---------------------------------------------------------------------------
# Feed snapshots: skip unchanged feeds (issue #51)
# ---------------------------------------------------------------------------

def _greenhouse_postings(*, order=None, raw_variant=None):
    jobs = [
        FetchedPosting(
            company_name="Acme", title="MBA Product Manager Intern", url="https://boards.greenhouse.io/acme/jobs/1",
            location_text="San Francisco, CA", posted_date="2026-09-01", external_id="1",
            raw={"updated_at": raw_variant or "2026-09-01T00:00:00Z"},
        ),
        FetchedPosting(
            company_name="Acme", title="Product Strategy Intern", url="https://boards.greenhouse.io/acme/jobs/2",
            location_text="Austin, TX", posted_date="2026-09-02", external_id="2",
        ),
    ]
    if order == "reversed":
        jobs.reverse()
    return jobs


def test_feed_with_no_snapshot_is_processed_and_a_snapshot_is_saved(empty_db, monkeypatch):
    company_id = _insert_company(empty_db)
    postings = _greenhouse_postings()

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=postings)

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    result = run(run_module.run(empty_db, kind="daily"))

    assert result["feeds_skipped"] == 0
    assert result["new_count"] == 2
    snapshot = run(empty_db.first("SELECT * FROM feed_snapshots WHERE company_id = ?", company_id))
    assert snapshot is not None
    assert snapshot["content_hash"] == feed_snapshot.hash_postings(postings)


def test_identical_feed_on_next_run_is_skipped_with_zero_parser_calls(empty_db, monkeypatch):
    _insert_company(empty_db)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=_greenhouse_postings())

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))  # first run: saves the snapshot

    calls = []
    real_classify = run_module.classify_posting

    def counting_classify(*args, **kwargs):
        calls.append(args)
        return real_classify(*args, **kwargs)

    monkeypatch.setattr(run_module, "classify_posting", counting_classify)
    result = run(run_module.run(empty_db, kind="daily"))

    assert calls == []
    assert result["feeds_skipped"] == 1
    assert result["new_count"] == 0


def test_feed_differing_only_in_timestamps_is_skipped(empty_db, monkeypatch):
    _insert_company(empty_db)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=_greenhouse_postings())

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))

    def fake_run_fetcher_new_timestamps(company, session=None):
        return FetchResult(
            company_id=company["id"], company_name=company["name"],
            postings=_greenhouse_postings(raw_variant="2026-09-05T09:30:00Z"),
        )

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher_new_timestamps)
    result = run(run_module.run(empty_db, kind="daily"))

    assert result["feeds_skipped"] == 1
    assert result["new_count"] == 0


def test_feed_with_same_jobs_reordered_is_skipped(empty_db, monkeypatch):
    _insert_company(empty_db)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=_greenhouse_postings())

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))

    def fake_run_fetcher_reordered(company, session=None):
        return FetchResult(
            company_id=company["id"], company_name=company["name"],
            postings=_greenhouse_postings(order="reversed"),
        )

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher_reordered)
    result = run(run_module.run(empty_db, kind="daily"))

    assert result["feeds_skipped"] == 1
    assert result["new_count"] == 0


def test_feed_with_one_new_job_is_processed_and_snapshot_updates(empty_db, monkeypatch):
    company_id = _insert_company(empty_db)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=_greenhouse_postings())

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))
    first_snapshot = run(empty_db.first("SELECT * FROM feed_snapshots WHERE company_id = ?", company_id))

    new_job = FetchedPosting(
        company_name="Acme", title="Business Development Intern", url="https://boards.greenhouse.io/acme/jobs/3",
        location_text="New York, NY", posted_date="2026-09-06", external_id="3",
    )

    def fake_run_fetcher_with_new_job(company, session=None):
        return FetchResult(
            company_id=company["id"], company_name=company["name"],
            postings=_greenhouse_postings() + [new_job],
        )

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher_with_new_job)
    result = run(run_module.run(empty_db, kind="daily"))

    assert result["feeds_skipped"] == 0
    assert result["new_count"] == 1
    postings = run(empty_db.all("SELECT * FROM postings WHERE url = ?", new_job.url))
    assert len(postings) == 1

    second_snapshot = run(empty_db.first("SELECT * FROM feed_snapshots WHERE company_id = ?", company_id))
    assert second_snapshot["content_hash"] != first_snapshot["content_hash"]


def test_feed_load_failure_is_not_treated_as_unchanged_and_keeps_last_snapshot(empty_db, monkeypatch):
    company_id = _insert_company(empty_db)

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=_greenhouse_postings())

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))
    first_snapshot = run(empty_db.first("SELECT * FROM feed_snapshots WHERE company_id = ?", company_id))

    def fake_run_fetcher_failing(company, session=None):
        return FetchResult(
            company_id=company["id"], company_name=company["name"], postings=[],
            error="boards-api.greenhouse.io returned HTTP 500",
        )

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher_failing)
    result = run(run_module.run(empty_db, kind="daily"))

    assert result["feeds_skipped"] == 0
    assert result["feed_errors"] == 1

    company = run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["last_feed_error"] == "boards-api.greenhouse.io returned HTTP 500"

    snapshot = run(empty_db.first("SELECT * FROM feed_snapshots WHERE company_id = ?", company_id))
    assert snapshot == first_snapshot


def test_postings_in_a_skipped_feed_still_get_their_daily_status_check(empty_db, monkeypatch):
    _insert_company(empty_db)
    postings = _greenhouse_postings()

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=postings)

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))  # creates the two postings and the snapshot

    stored = run(empty_db.all("SELECT * FROM postings ORDER BY id"))
    assert len(stored) == 2
    for row in stored:
        run(empty_db.run("UPDATE postings SET verified_at = NULL, verified_via = NULL WHERE id = ?", row["id"]))

    result = run(run_module.run(empty_db, kind="daily"))  # identical feed -> skipped
    assert result["feeds_skipped"] == 1

    reverified = run(empty_db.all("SELECT * FROM postings ORDER BY id"))
    for row in reverified:
        assert row["verified_at"] is not None
        assert row["verified_via"] == "feed"
        assert row["status"] == "open"


def test_posting_missing_from_a_changed_feed_is_checked_immediately(empty_db, monkeypatch):
    _insert_company(empty_db)
    postings = _greenhouse_postings()

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=postings)

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))

    def fake_run_fetcher_one_job_gone(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[postings[0]])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher_one_job_gone)
    result = run(run_module.run(empty_db, kind="daily"))

    assert result["feeds_skipped"] == 0
    gone = run(empty_db.first("SELECT * FROM postings WHERE url = ?", postings[1].url))
    assert gone["status"] == "closed"
    assert gone["archived_at"] is not None


def test_run_summary_reports_correct_skipped_feed_count(empty_db, monkeypatch):
    unchanged_ids = [_insert_company(empty_db, name=f"Unchanged{i}", feed_id=f"unchanged-{i}") for i in range(5)]
    changed_ids = [_insert_company(empty_db, name=f"Changed{i}", feed_id=f"changed-{i}") for i in range(2)]

    def postings_for(name):
        return [FetchedPosting(
            company_name=name, title="MBA Product Manager Intern",
            url=f"https://boards.greenhouse.io/{name}/jobs/1", location_text="San Francisco, CA",
            posted_date="2026-09-01", external_id="1",
        )]

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=postings_for(company["name"]))

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))  # first run: all 7 processed, snapshots saved
    assert unchanged_ids and changed_ids  # sanity: both groups exist

    def fake_run_fetcher_second_run(company, session=None):
        if company["id"] in changed_ids:
            return FetchResult(
                company_id=company["id"], company_name=company["name"],
                postings=postings_for(company["name"]) + [FetchedPosting(
                    company_name=company["name"], title="Product Strategy Intern",
                    url=f"https://boards.greenhouse.io/{company['name']}/jobs/2",
                    location_text="Austin, TX", posted_date="2026-09-02", external_id="2",
                )],
            )
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=postings_for(company["name"]))

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher_second_run)
    result = run(run_module.run(empty_db, kind="daily"))

    assert result["feeds_skipped"] == 5
    assert "5 feed(s) unchanged and skipped" in result["summary"]
