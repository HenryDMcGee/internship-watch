import asyncio

import pytest

import job.run as run_module
from job.agent import client as agent_client
from job.agent.client import ClaudeStepResult
from job.fetchers import FetchResult
from job.models import FetchedPosting


def run(coro):
    return asyncio.run(coro)


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
