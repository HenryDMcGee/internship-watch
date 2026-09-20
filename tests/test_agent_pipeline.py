import asyncio

from job.agent.budgets import BudgetStatus, UsageTotals
from job.agent.client import ClaudeStepResult
from job.agent.pipeline import build_unclear_reader, run_agent_steps

SETTINGS = {
    "billing_mode": "subscription", "daily_token_budget": 1_000_000, "daily_search_budget": 100,
    "weekly_token_budget": 5_000_000, "share_discovery": 0.60, "share_scout": 0.20,
    "share_classification": 0.15, "share_improvement": 0.05,
    "model_discovery": "sonnet", "model_scout": "sonnet", "model_classification": "haiku",
    "model_improvement": "sonnet", "search_limit_per_run": 35, "search_limit_per_company": 5,
}
TITLES_DOC = {"titles": [{"title": "PM Intern", "active": True}], "exclude_words": []}
LOCATIONS = {"countries": ["US"], "include_remote": False}


def run(coro):
    return asyncio.run(coro)


def _open_status():
    return BudgetStatus(billing_mode="subscription", settings=SETTINGS)


def _closed_status():
    return BudgetStatus(billing_mode="subscription", settings=SETTINGS, today=UsageTotals(tokens=1_000_000))


def _fake_run_claude(responses):
    """`run_claude` stand-in: `responses` maps step name to a JSON text reply."""
    calls = []

    def fake(step, prompt, *, model):
        calls.append(step)
        return ClaudeStepResult(step=step, model=model, text=responses[step])

    fake.calls = calls
    return fake


def _insert_company(db, **overrides):
    row = {"name": "Acme", "status": "pinned"}
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    return run(db.run(f"INSERT INTO companies ({columns}) VALUES ({placeholders})", *row.values()))


def test_run_agent_steps_tracks_which_steps_succeeded(empty_db):
    _insert_company(empty_db, feed_type=None)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    fake = _fake_run_claude({
        "discovery": '{"results": []}',
        "scout": '{"sources": []}',
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": 1, "name": "Acme", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    assert set(outcome["steps_succeeded"]) == {"discovery", "scout", "improvement"}


def test_run_agent_steps_still_records_usage_when_discovery_reply_is_not_valid_json(empty_db):
    # A step that ran (and spent real tokens/searches) but replied with
    # something we can't parse must not vanish without a trace -- that's
    # exactly what left issue #20's discovery and scout runs invisible.
    _insert_company(empty_db, feed_type=None)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    fake = _fake_run_claude({
        "discovery": "not json at all",
        "scout": '{"sources": []}',
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": 1, "name": "Acme", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    assert outcome["discovery_count"] == 0
    assert any("discovery" in e for e in outcome["errors"])
    assert "discovery" not in outcome["steps_succeeded"]

    usage_rows = run(empty_db.all("SELECT step FROM usage ORDER BY id"))
    assert "discovery" in [r["step"] for r in usage_rows]


def test_run_agent_steps_uses_run_summary_so_far_for_improvement_prompt(empty_db):
    # Querying the `runs` row for its own `summary` column always found
    # NULL here -- job/run.py doesn't write that column until after every
    # agent step (including this one) has already run. `run_summary_so_far`
    # is the caller's own up-to-date description, passed in directly.
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))
    captured_prompts = {}

    def fake(step, prompt, *, model):
        captured_prompts[step] = prompt
        if step == "improvement":
            return ClaudeStepResult(
                step=step, model=model,
                text='{"search_plan": null, "proposals": [], "title_suggestions": []}',
            )
        raise AssertionError(f"unexpected step {step}")

    run(run_agent_steps(
        empty_db, run_id=run_id, companies=[], titles_doc=TITLES_DOC, locations_setting=LOCATIONS,
        budget_settings=SETTINGS, status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00",
        run_summary_so_far={"summary": "Checked 100 companies, 0 new posting(s), 0 feed error(s), 100 with no source yet."},
        run_claude=fake,
    ))

    assert "Checked 100 companies" in captured_prompts["improvement"]


def test_run_agent_steps_stores_discovery_results_and_classifies_them(empty_db):
    _insert_company(empty_db, feed_type=None)  # no working feed -> eligible for discovery/scout
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    fake = _fake_run_claude({
        "discovery": '{"results": [{"company_name": "Acme", "title": "PM Intern", "url": "https://acme.example/1", "location_text": "SF"}]}',
        "classification": '{"decisions": [{"id": 1, "decision": "match", "matched_title": "PM Intern", "title_en": null}]}',
        "scout": '{"sources": [{"company_name": "Acme", "name": "Acme careers", "url": "https://acme.example/careers", "kind": "Company career sites", "covers": "Acme"}]}',
        "improvement": '{"search_plan": {"query_templates": [], "notes": "n"}, "proposals": [], "title_suggestions": []}',
    })

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": 1, "name": "Acme", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    assert outcome["discovery_count"] == 1
    assert outcome["classification_count"] == 1
    assert outcome["scout_count"] == 1
    assert outcome["errors"] == []
    assert fake.calls == ["discovery", "classification", "scout", "improvement"]

    postings = run(empty_db.all("SELECT * FROM postings"))
    assert len(postings) == 1
    assert postings[0]["matched_title"] == "PM Intern"

    sources = run(empty_db.all("SELECT * FROM sources"))
    assert len(sources) == 1
    assert sources[0]["status"] == "trial"

    usage_rows = run(empty_db.all("SELECT step FROM usage ORDER BY id"))
    assert [r["step"] for r in usage_rows] == ["discovery", "classification", "scout", "improvement"]

    plans = run(empty_db.all("SELECT * FROM search_plans"))
    assert len(plans) == 1
    assert plans[0]["plan_date"] == "2026-09-21"


def test_run_agent_steps_sets_company_feed_from_a_recognized_scout_url(empty_db):
    """issue #20: a scout result naming one of the scouted companies, on a
    URL a known feed platform (here Greenhouse), must update that
    company's own careers_url/feed_type -- not just add a generic row to
    `sources`, which is the only thing the old code did, so a company could
    stay "no source" forever even after the scout found its careers page.
    """
    company_id = _insert_company(empty_db, feed_type=None)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    fake = _fake_run_claude({
        "scout": (
            '{"sources": [{"company_name": "Acme", "name": "Acme careers", '
            '"url": "https://boards.greenhouse.io/acme", "kind": "Company career sites", "covers": "Acme"}]}'
        ),
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": company_id, "name": "Acme", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    company = run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["feed_type"] == "greenhouse"
    assert company["careers_url"] == "https://boards.greenhouse.io/acme"
    assert company["feed_id"] == "acme"


def test_run_agent_steps_does_not_overwrite_an_existing_company_feed(empty_db):
    company_id = _insert_company(empty_db, feed_type="lever", feed_id="acme", careers_url="https://jobs.lever.co/acme")
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    fake = _fake_run_claude({
        "scout": (
            '{"sources": [{"company_name": "Acme", "name": "Acme careers", '
            '"url": "https://boards.greenhouse.io/acme", "kind": "Company career sites", "covers": "Acme"}]}'
        ),
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    # Passing a company that already has a feed_type wouldn't happen in the
    # real job (it's filtered to `no_feed_companies` before scouting), but
    # the guard must hold regardless of how it's called.
    run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": company_id, "name": "Acme", "feed_type": "lever"}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    company = run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["feed_type"] == "lever"
    assert company["careers_url"] == "https://jobs.lever.co/acme"


def test_run_agent_steps_fails_the_scout_step_loudly_when_every_result_is_invalid(empty_db):
    """A scout call that spent real tokens/searches but returned only
    unusable entries must show up as a failed step (run summary + alert),
    not look like a clean "found nothing" run (issue #20)."""
    _insert_company(empty_db, feed_type=None)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    fake = _fake_run_claude({
        "scout": '{"sources": [{"company_name": "Acme", "url": "https://acme.example/careers"}]}',  # missing "name"
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": 1, "name": "Acme", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    assert "scout" not in outcome["steps_succeeded"]
    assert any("scout" in e for e in outcome["errors"])
    # The call still cost real tokens even though its reply was unusable --
    # that spend must still be recorded (see `ClaudeOutputError.result`).
    usage_rows = run(empty_db.all("SELECT step FROM usage"))
    assert any(r["step"] == "scout" for r in usage_rows)
    sources = run(empty_db.all("SELECT * FROM sources"))
    assert sources == []


def test_run_agent_steps_keeps_the_good_scout_results_when_only_some_are_invalid(empty_db):
    _insert_company(empty_db, feed_type=None)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    fake = _fake_run_claude({
        "scout": (
            '{"sources": ['
            '{"name": "Acme careers", "url": "https://acme.example/careers", "kind": "Company career sites"}, '
            '{"url": "https://bad.example"}'  # missing "name" -- must not sink the good entry above
            ']}'
        ),
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": 1, "name": "Acme", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    assert "scout" in outcome["steps_succeeded"]
    assert outcome["scout_count"] == 1
    sources = run(empty_db.all("SELECT * FROM sources"))
    assert len(sources) == 1
    assert sources[0]["url"] == "https://acme.example/careers"


def test_run_agent_steps_records_last_scouted_at_for_the_batch_sent(empty_db):
    company_id = _insert_company(empty_db, feed_type=None, rank=1)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    fake = _fake_run_claude({
        "scout": '{"sources": []}',
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": company_id, "name": "Acme", "feed_type": None, "rank": 1}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    company = run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["last_scouted_at"] == "2026-09-20T08:00:00"


def test_run_agent_steps_does_not_record_last_scouted_at_when_scout_is_skipped(empty_db):
    company_id = _insert_company(empty_db, feed_type=None, rank=1)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    fake = _fake_run_claude({"improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}'})

    run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": company_id, "name": "Acme", "feed_type": None, "rank": 1}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_closed_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    company = run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["last_scouted_at"] is None


def test_run_agent_steps_archives_a_classification_reject(empty_db):
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))
    posting_id = run(empty_db.run(
        """INSERT INTO postings (company_name, title, url, locations, location_match, first_seen, status)
           VALUES ('Acme', 'Some Other Role', 'https://acme.example/2', '[]', 'unclear', '2026-09-20', 'unverified')"""
    ))

    fake = _fake_run_claude({
        "classification": (
            '{"decisions": [{"id": %d, "decision": "reject", "matched_title": null, "reason": "not a PM role"}]}'
            % posting_id
        ),
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[], titles_doc=TITLES_DOC, locations_setting=LOCATIONS,
        budget_settings=SETTINGS, status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00",
        run_claude=fake,
    ))
    assert outcome["classification_count"] == 0

    posting = run(empty_db.first("SELECT * FROM postings WHERE id = ?", posting_id))
    assert posting["archived_at"] is not None
    assert "not a PM role" in posting["archive_reason"]


def test_run_agent_steps_skips_scout_when_budget_says_no(empty_db):
    _insert_company(empty_db, feed_type=None)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    fake = _fake_run_claude({"improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}'})

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": 1, "name": "Acme", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_closed_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    skipped_steps = {s["step"] for s in outcome["steps_skipped"]}
    assert "scout" in skipped_steps
    assert "discovery" in skipped_steps
    assert "discovery" not in fake.calls
    assert "scout" not in fake.calls
    # improvement is over-budget too at 100%, so it's cut as well.
    assert "improvement" in skipped_steps


def test_run_agent_steps_stops_all_claude_calls_after_a_limit_error(empty_db):
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    def fake(step, prompt, *, model):
        from job.agent.client import ClaudeLimitError
        raise ClaudeLimitError("usage limit reached")

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": 1, "name": "Acme", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))
    assert outcome["claude_limit_hit"] is True
    # improvement always runs last; once the limit was hit it must be skipped too.
    assert any(s["step"] == "improvement" for s in outcome["steps_skipped"])


def test_build_unclear_reader_returns_none_when_budget_says_no():
    reader = build_unclear_reader(
        status=_closed_status(), model="haiku", on_usage=lambda r: None, on_error=lambda e: None,
    )
    assert reader("some page text") is None


def test_build_unclear_reader_returns_decision_and_reports_usage():
    usages = []
    fake = _fake_run_claude({"unclear_page": '{"decision": "open", "note": "apply now"}'})
    reader = build_unclear_reader(
        status=_open_status(), model="haiku", on_usage=usages.append, on_error=lambda e: None, run_claude=fake,
    )
    decision = reader("some page text")
    assert decision.decision == "open"
    assert len(usages) == 1


def test_build_unclear_reader_swallows_errors_and_reports_them():
    errors = []

    def fake(step, prompt, *, model):
        raise RuntimeError("boom")

    reader = build_unclear_reader(status=_open_status(), model="haiku", on_usage=lambda r: None, on_error=errors.append, run_claude=fake)
    assert reader("some page text") is None
    assert len(errors) == 1
