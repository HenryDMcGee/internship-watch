import asyncio

from conftest import adapt_discovery_reply
from job import careers_snapshot, fingerprint
from job.agent.budgets import BudgetStatus, UsageTotals
from job.agent.client import ClaudeStepResult
from job.agent.pipeline import _store_scout_sources, build_unclear_reader, run_agent_steps

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
    """`run_claude` stand-in: `responses` maps step name to a JSON text reply.

    Discovery and the scout default to a nonzero `web_searches` -- a real call with
    those tools enabled always searches at least once, and a zero count now fails the
    step loudly (issue #20), which most of these tests aren't about.
    """
    calls = []

    def fake(step, prompt, *, model):
        calls.append(step)
        web_searches = 3 if step in ("discovery", "scout", "careers_repair") else 0
        text = responses[step]
        if step == "discovery":
            text = adapt_discovery_reply(text, prompt)
        return ClaudeStepResult(step=step, model=model, text=text, web_searches=web_searches)

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
    assert outcome["discovery_new_count"] == 1
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


def test_run_agent_steps_stores_careers_repair_proposals_for_flagged_companies(empty_db):
    # issue #55: a company flagged with a broken careers page gets a
    # `careers_repair` proposal for each usable replacement Claude finds.
    company_id = run(empty_db.run(
        "INSERT INTO companies (name, status, feed_type, careers_url, careers_page_flagged) "
        "VALUES ('Acme', 'pinned', NULL, 'https://acme.example/careers', 1)"
    ))
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    fake = _fake_run_claude({
        "discovery": '{"results": []}',
        "scout": '{"sources": []}',
        "careers_repair": (
            '{"replacements": [{"company_name": "Acme", "url": "https://acme.example/careers?q=intern", '
            '"note": "internship search"}]}'
        ),
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    company = {
        "id": company_id, "name": "Acme", "feed_type": None,
        "careers_url": "https://acme.example/careers", "careers_page_flagged": 1,
        "careers_url_rejected": "[]",
    }
    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[company],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    assert outcome["careers_repair_count"] == 1
    assert "careers_repair" in fake.calls

    proposals = run(empty_db.all("SELECT * FROM proposals WHERE kind = 'careers_repair'"))
    assert len(proposals) == 1
    import json
    payload = json.loads(proposals[0]["payload"])
    assert payload["company_id"] == company_id
    assert payload["new_url"] == "https://acme.example/careers?q=intern"
    assert payload["old_url"] == "https://acme.example/careers"


def test_run_agent_steps_skips_careers_repair_when_nothing_is_flagged(empty_db):
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

    assert outcome["careers_repair_count"] == 0
    assert "careers_repair" not in fake.calls


def test_run_agent_steps_discovery_new_count_excludes_a_classification_reject(empty_db):
    # issue #26: a discovered posting that classification goes on to reject
    # (and archive) in the same run must not still count toward the run's
    # "new" total -- it never actually lands on the dashboard.
    _insert_company(empty_db, feed_type=None)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    fake = _fake_run_claude({
        "discovery": '{"results": [{"company_name": "Acme", "title": "Software Engineer Intern", '
                     '"url": "https://acme.example/1", "location_text": "SF"}]}',
        "classification": '{"decisions": [{"id": 1, "decision": "reject", "matched_title": null, '
                           '"reason": "not a PM role"}]}',
        "scout": '{"sources": []}',
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": 1, "name": "Acme", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    assert outcome["discovery_count"] == 1
    assert outcome["discovery_new_count"] == 0
    assert outcome["classification_count"] == 0

    posting = run(empty_db.first("SELECT * FROM postings WHERE id = 1"))
    assert posting["archived_at"] is not None


def test_run_agent_steps_does_not_store_a_discovery_result_matching_an_existing_posting(empty_db):
    # issue #26: an NVIDIA posting Henry had already marked applied got
    # rediscovered under a new URL and saved as a second row. Company +
    # normalized title must catch that even though the URL differs.
    _insert_company(empty_db, feed_type=None)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))
    run(empty_db.run(
        """INSERT INTO postings (company_name, title, url, locations, location_match, first_seen, status, applied)
           VALUES ('Acme', 'PM Intern', 'https://acme.example/old', '[]', 'unclear', '2026-09-18', 'unverified', 1)"""
    ))

    fake = _fake_run_claude({
        "discovery": '{"results": [{"company_name": "Acme", "title": "PM Intern", '
                     '"url": "https://acme.example/new", "location_text": "SF"}]}',
        "scout": '{"sources": []}',
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": 1, "name": "Acme", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    assert outcome["discovery_count"] == 0
    postings = run(empty_db.all("SELECT * FROM postings"))
    assert len(postings) == 1
    assert postings[0]["url"] == "https://acme.example/old"
    assert postings[0]["applied"] == 1


def test_run_agent_steps_recognizes_a_mirror_site_repeat_via_fingerprint(empty_db):
    # issue #49's NVIDIA case: an existing posting found through a Workday
    # URL (so it has a job id on file) is rediscovered at a mirror site with
    # no job id at all, under the same company/title/location -- must match
    # by fingerprint, not create a second row, and never reach classification.
    _insert_company(empty_db, name="NVIDIA", feed_type=None)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    workday_url = (
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NvidiaExternalCareerSite/job/US-Site/"
        "XD-Product-Manager-Intern_JR2025670"
    )
    posting_id = run(empty_db.run(
        """INSERT INTO postings (company_name, title, matched_title, url, locations, location_match,
           first_seen, status)
           VALUES ('NVIDIA', 'Product Management MBA Intern, Data Center GPU', 'PM Intern', ?,
                   '["Santa Clara, CA"]', 'match', '2026-09-18', 'open')""",
        workday_url,
    ))
    settings_ver = fingerprint.settings_version(TITLES_DOC, LOCATIONS)
    fp = fingerprint.compute_fingerprint(
        company_name="NVIDIA", title="Product Management MBA Intern, Data Center GPU",
        location_text="Santa Clara, CA", url=workday_url,
    )
    run(fingerprint.record_posting(
        empty_db, fp, posting_id=posting_id, settings_version=settings_ver, source="workday",
        now_iso="2026-09-18T08:00:00",
    ))

    fake = _fake_run_claude({
        "discovery": '{"results": [{"company_name": "NVIDIA", '
                     '"title": "Product Management MBA Intern, Data Center GPU", '
                     '"url": "https://mirror.example/nvidia/123", "location_text": "Santa Clara, CA"}]}',
        "scout": '{"sources": []}',
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": 1, "name": "NVIDIA", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    assert "classification" not in fake.calls
    assert outcome["repeats_recognized"] == 1
    postings = run(empty_db.all("SELECT * FROM postings"))
    assert len(postings) == 1

    fp_row = run(empty_db.first("SELECT * FROM posting_fingerprints WHERE posting_id = ?", posting_id))
    assert fp_row["last_source"] == "claude-discovery"
    assert fp_row["last_seen"] == "2026-09-20T08:00:00"


def test_run_agent_steps_skips_a_remembered_rejection_with_no_claude_call(empty_db):
    _insert_company(empty_db, feed_type=None)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    settings_ver = fingerprint.settings_version(TITLES_DOC, LOCATIONS)
    fp = fingerprint.compute_fingerprint(
        company_name="Acme", title="Software Engineer Intern", location_text="SF", url="https://acme.example/1",
    )
    run(fingerprint.record_rejection(
        empty_db, fp, reason="not a PM role", settings_version=settings_ver,
        source="claude-discovery", now_iso="2026-09-19T08:00:00",
    ))

    fake = _fake_run_claude({
        "discovery": '{"results": [{"company_name": "Acme", "title": "Software Engineer Intern", '
                     '"url": "https://acme.example/1", "location_text": "SF"}]}',
        "scout": '{"sources": []}',
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": 1, "name": "Acme", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    assert "classification" not in fake.calls
    assert outcome["rejections_skipped"] == 1
    assert run(empty_db.all("SELECT * FROM postings")) == []


def test_run_agent_steps_keeps_remembering_a_rejection_when_only_budgets_change(empty_db):
    # Budgets aren't part of the settings version -- changing them must
    # never re-judge a remembered rejection.
    _insert_company(empty_db, feed_type=None)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    settings_ver = fingerprint.settings_version(TITLES_DOC, LOCATIONS)
    fp = fingerprint.compute_fingerprint(
        company_name="Acme", title="Software Engineer Intern", location_text="SF", url="https://acme.example/1",
    )
    run(fingerprint.record_rejection(
        empty_db, fp, reason="not a PM role", settings_version=settings_ver,
        source="claude-discovery", now_iso="2026-09-19T08:00:00",
    ))

    different_budget_settings = dict(SETTINGS, search_limit_per_run=5, model_discovery="haiku")
    fake = _fake_run_claude({
        "discovery": '{"results": [{"company_name": "Acme", "title": "Software Engineer Intern", '
                     '"url": "https://acme.example/1", "location_text": "SF"}]}',
        "scout": '{"sources": []}',
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": 1, "name": "Acme", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=different_budget_settings,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    assert "classification" not in fake.calls
    assert outcome["rejections_skipped"] == 1


def test_run_agent_steps_rejudges_a_stale_rejection_after_a_title_is_added(empty_db):
    _insert_company(empty_db, feed_type=None)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    old_version = fingerprint.settings_version(TITLES_DOC, LOCATIONS)
    fp = fingerprint.compute_fingerprint(
        company_name="Acme", title="Growth Product Manager Intern", location_text="SF",
        url="https://acme.example/1",
    )
    run(fingerprint.record_rejection(
        empty_db, fp, reason="no active title or alias matched", settings_version=old_version,
        source="claude-discovery", now_iso="2026-09-19T08:00:00",
    ))

    new_titles_doc = {
        "titles": [
            {"title": "PM Intern", "active": True},
            {"title": "Growth Product Manager Intern", "active": True},
        ],
        "exclude_words": [],
    }
    assert fingerprint.settings_version(new_titles_doc, LOCATIONS) != old_version

    fake = _fake_run_claude({
        "discovery": '{"results": [{"company_name": "Acme", "title": "Growth Product Manager Intern", '
                     '"url": "https://acme.example/1", "location_text": "SF"}]}',
        "classification": '{"decisions": [{"id": 1, "decision": "match", '
                           '"matched_title": "Growth Product Manager Intern", "title_en": null}]}',
        "scout": '{"sources": []}',
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": 1, "name": "Acme", "feed_type": None}],
        titles_doc=new_titles_doc, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    assert fake.calls.count("classification") == 1
    assert outcome["classification_count"] == 1
    postings = run(empty_db.all("SELECT * FROM postings"))
    assert len(postings) == 1
    assert postings[0]["matched_title"] == "Growth Product Manager Intern"

    # The rejection is overwritten in place, not duplicated into a second row.
    fingerprints = run(empty_db.all("SELECT * FROM posting_fingerprints"))
    assert len(fingerprints) == 1
    assert fingerprints[0]["rejection_reason"] is None
    assert fingerprints[0]["posting_id"] == postings[0]["id"]


def test_run_agent_steps_rejudges_a_stale_rejection_after_a_location_country_is_added(empty_db):
    _insert_company(empty_db, feed_type=None)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    old_version = fingerprint.settings_version(TITLES_DOC, LOCATIONS)
    fp = fingerprint.compute_fingerprint(
        company_name="Acme", title="PM Intern", location_text="Tokyo, Japan", url="https://acme.example/1",
    )
    run(fingerprint.record_rejection(
        empty_db, fp, reason="outside locations", settings_version=old_version,
        source="claude-discovery", now_iso="2026-09-19T08:00:00",
    ))

    new_locations = {"countries": ["US", "JP"], "include_remote": False}
    assert fingerprint.settings_version(TITLES_DOC, new_locations) != old_version

    fake = _fake_run_claude({
        "discovery": '{"results": [{"company_name": "Acme", "title": "PM Intern", '
                     '"url": "https://acme.example/1", "location_text": "Tokyo, Japan"}]}',
        "classification": '{"decisions": [{"id": 1, "decision": "match", '
                           '"matched_title": "PM Intern", "title_en": null, "location_match": "match"}]}',
        "scout": '{"sources": []}',
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": 1, "name": "Acme", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=new_locations, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    assert fake.calls.count("classification") == 1
    assert outcome["classification_count"] == 1


def test_run_agent_steps_never_rejudges_a_posting_henry_marked_applied(empty_db):
    # Settings-version staleness only ever applies to remembered rejections
    # -- a posting Henry has already marked applied, open, or closed must
    # never be pulled back into classification, even after titles change.
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))
    run(empty_db.run(
        """INSERT INTO postings (company_name, title, matched_title, url, locations, location_match,
           first_seen, status, applied)
           VALUES ('Acme', 'PM Intern', 'PM Intern', 'https://acme.example/1', '[]', 'match',
                   '2026-09-18', 'open', 1)"""
    ))

    new_titles_doc = {
        "titles": [{"title": "PM Intern", "active": True}, {"title": "APM Intern", "active": True}],
        "exclude_words": [],
    }
    fake = _fake_run_claude({"improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}'})

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[], titles_doc=new_titles_doc, locations_setting=LOCATIONS,
        budget_settings=SETTINGS, status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00",
        run_claude=fake,
    ))

    assert "classification" not in fake.calls
    assert outcome["classification_count"] == 0
    posting = run(empty_db.first("SELECT * FROM postings WHERE applied = 1"))
    assert posting["matched_title"] == "PM Intern"


def test_run_agent_steps_classification_keeps_good_decisions_when_one_entry_is_invalid(empty_db):
    # issue #26: classification could spend real tokens on a batch of
    # decisions and leave every posting in it unclassified if a single
    # decision in the reply was malformed -- the same "one bad item sinks
    # the whole batch" failure already fixed for the scout in issue #20.
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))
    good_id = run(empty_db.run(
        """INSERT INTO postings (company_name, title, url, locations, location_match, first_seen, status)
           VALUES ('Acme', 'PM Intern', 'https://acme.example/1', '[]', 'unclear', '2026-09-20', 'unverified')"""
    ))
    other_id = run(empty_db.run(
        """INSERT INTO postings (company_name, title, url, locations, location_match, first_seen, status)
           VALUES ('Acme', 'Other Role', 'https://acme.example/2', '[]', 'unclear', '2026-09-20', 'unverified')"""
    ))

    fake = _fake_run_claude({
        "classification": (
            '{"decisions": [{"id": %d, "decision": "match", "matched_title": "PM Intern", "title_en": null}, '
            '{"id": "not-an-int", "decision": "reject"}]}' % good_id
        ),
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[], titles_doc=TITLES_DOC, locations_setting=LOCATIONS,
        budget_settings=SETTINGS, status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00",
        run_claude=fake,
    ))

    assert "classification" in outcome["steps_succeeded"]
    assert outcome["classification_count"] == 1

    good_posting = run(empty_db.first("SELECT * FROM postings WHERE id = ?", good_id))
    assert good_posting["matched_title"] == "PM Intern"

    other_posting = run(empty_db.first("SELECT * FROM postings WHERE id = ?", other_id))
    assert other_posting["matched_title"] is None
    assert other_posting["archived_at"] is None


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


def _scout_found(company_name, url):
    return {"company_name": company_name, "name": f"{company_name} careers", "url": url,
            "kind": "Company career sites", "covers": company_name}


def _store_scout(db, found, companies):
    return run(_store_scout_sources(db, found, now_iso="2026-09-20T08:00:00", companies=companies))


def test_scout_does_not_overwrite_a_working_feed(empty_db):
    company_id = _insert_company(
        empty_db, feed_type="lever", feed_id="acme", careers_url="https://jobs.lever.co/acme", last_feed_status="ok",
    )
    _store_scout(
        empty_db, [_scout_found("Acme", "https://boards.greenhouse.io/acme")],
        [{"id": company_id, "name": "Acme"}],
    )

    company = run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["feed_type"] == "lever"
    assert company["careers_url"] == "https://jobs.lever.co/acme"


def test_scout_sets_feed_type_for_a_recognized_platform(empty_db):
    company_id = _insert_company(empty_db, feed_type=None)
    _store_scout(
        empty_db, [_scout_found("Acme", "https://boards.greenhouse.io/acme")],
        [{"id": company_id, "name": "Acme"}],
    )

    company = run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["feed_type"] == "greenhouse"
    assert company["feed_id"] == "acme"
    assert company["careers_url"] == "https://boards.greenhouse.io/acme"


def test_scout_sets_careers_url_and_note_for_an_unrecognized_careers_page(empty_db):
    company_id = _insert_company(empty_db, feed_type=None)
    _store_scout(
        empty_db, [_scout_found("Acme", "https://acme.example/careers?q=intern")],
        [{"id": company_id, "name": "Acme"}],
    )

    company = run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["feed_type"] is None
    assert company["careers_url"] == "https://acme.example/careers?q=intern"
    assert company["careers_note"] == "careers page, no feed"
    assert company["careers_url_approved"] == 0


def test_scout_never_overwrites_a_careers_url_henry_approved(empty_db):
    company_id = _insert_company(
        empty_db, feed_type=None, careers_url="https://acme.example/henry-picked", careers_url_approved=1,
    )
    _store_scout(
        empty_db,
        [_scout_found("Acme", "https://acme.example/careers"), _scout_found("Acme", "https://boards.greenhouse.io/acme")],
        [{"id": company_id, "name": "Acme"}],
    )

    company = run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["careers_url"] == "https://acme.example/henry-picked"
    # A recognized feed can still be attached; it just doesn't replace the approved page.
    assert company["feed_type"] == "greenhouse"


def test_scout_may_replace_a_careers_url_nobody_approved(empty_db):
    company_id = _insert_company(empty_db, feed_type=None, careers_url="https://acme.example/old")
    _store_scout(
        empty_db, [_scout_found("Acme", "https://acme.example/careers/intern")],
        [{"id": company_id, "name": "Acme"}],
    )

    company = run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["careers_url"] == "https://acme.example/careers/intern"


def test_scout_prefers_an_internship_filtered_page_when_it_finds_several(empty_db):
    company_id = _insert_company(empty_db, feed_type=None)
    _store_scout(
        empty_db,
        [_scout_found("Acme", "https://acme.example/careers"), _scout_found("Acme", "https://acme.example/careers?q=intern")],
        [{"id": company_id, "name": "Acme"}],
    )

    company = run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["careers_url"] == "https://acme.example/careers?q=intern"


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


def test_run_agent_steps_records_last_source_found_at_even_without_a_recognized_feed(empty_db):
    # A "Job boards" source Claude finds for a company isn't something
    # `detect_feed` recognizes as an automatic feed, so `feed_type` stays
    # empty -- but it's still proof the company is covered, which is what
    # `scout.split_companies_by_coverage` needs to stop re-scouting it every
    # run (issue #43).
    company_id = _insert_company(empty_db, feed_type=None)
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))

    fake = _fake_run_claude({
        "scout": (
            '{"sources": [{"company_name": "Acme", "name": "Acme board", '
            '"url": "https://boards.example/acme", "kind": "Job boards", "covers": "Acme"}]}'
        ),
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": company_id, "name": "Acme", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00", run_claude=fake,
    ))

    company = run(empty_db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["feed_type"] is None
    assert company["last_source_found_at"] == "2026-09-20T08:00:00"


def _scout_batch_prompt(db, companies_rows, *, now="2026-09-20T08:00:00"):
    run_id = run(db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))
    captured_prompts = {}

    def fake(step, prompt, *, model):
        captured_prompts[step] = prompt
        web_searches = 3 if step in ("scout", "discovery") else 0
        text = {
            "scout": '{"sources": []}',
            "discovery": '{"companies": []}',
        }.get(step, '{"search_plan": null, "proposals": [], "title_suggestions": []}')
        return ClaudeStepResult(step=step, model=model, text=text, web_searches=web_searches)

    outcome = run(run_agent_steps(
        db, run_id=run_id, companies=companies_rows,
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-09-20", now_iso=now, run_claude=fake,
    ))
    return outcome, captured_prompts


def test_scout_skips_a_company_with_a_working_feed(empty_db):
    covered_id = _insert_company(empty_db, name="Covered Co", feed_type="greenhouse", last_feed_status="ok")
    uncovered_id = _insert_company(empty_db, name="Uncovered Co", feed_type=None)

    outcome, prompts = _scout_batch_prompt(empty_db, [
        {"id": covered_id, "name": "Covered Co", "feed_type": "greenhouse"},
        {"id": uncovered_id, "name": "Uncovered Co", "feed_type": None},
    ])

    assert outcome["scout_skipped_count"] == 1
    assert outcome["uncovered_count"] == 1
    assert "Uncovered Co" in prompts["scout"]
    assert "Covered Co" not in prompts["scout"]


def test_scout_skips_a_company_whose_careers_page_loaded_in_the_last_7_days(empty_db):
    covered_id = _insert_company(empty_db, name="Covered Co", feed_type=None, careers_url="https://covered.example/jobs")
    uncovered_id = _insert_company(empty_db, name="Uncovered Co", feed_type=None)
    run(careers_snapshot.record_changed(
        empty_db, covered_id, "hash", method="browser_run", now_iso="2026-09-18T08:00:00", existing=None,
    ))

    outcome, prompts = _scout_batch_prompt(empty_db, [
        {"id": covered_id, "name": "Covered Co", "feed_type": None, "careers_url": "https://covered.example/jobs"},
        {"id": uncovered_id, "name": "Uncovered Co", "feed_type": None},
    ])

    assert outcome["scout_skipped_count"] == 1
    assert "Covered Co" not in prompts["scout"]


def test_scout_includes_a_company_that_only_has_a_sources_table_row(empty_db):
    # Runs 36 to 42: "skipped 182 already-covered companies" while 182 had no source.
    # A sources row, or a recent `last_source_found_at`, is not coverage.
    company_id = _insert_company(empty_db, name="Sources Only Co", feed_type=None, last_source_found_at="2026-09-19")
    run(empty_db.run(
        "INSERT INTO sources (name, url, kind, covers, status, discovered_by, discovered_at) "
        "VALUES ('Sources Only Co careers', 'https://sourcesonly.example/careers', 'Company career sites', "
        "'Sources Only Co', 'trial', 'claude-scout', '2026-09-19')"
    ))

    outcome, prompts = _scout_batch_prompt(empty_db, [
        {"id": company_id, "name": "Sources Only Co", "feed_type": None, "last_source_found_at": "2026-09-19"},
    ])

    assert outcome["scout_skipped_count"] == 0
    assert "Sources Only Co" in prompts["scout"]


def test_scout_includes_a_company_whose_careers_page_loaded_only_long_ago_or_failed(empty_db):
    old_id = _insert_company(empty_db, name="Old Co", feed_type=None, careers_url="https://old.example/jobs")
    failed_id = _insert_company(empty_db, name="Failed Co", feed_type=None, careers_url="https://failed.example/jobs")
    run(careers_snapshot.record_changed(
        empty_db, old_id, "hash", method="backup", now_iso="2026-09-01T08:00:00", existing=None,
    ))
    run(careers_snapshot.record_failure(
        empty_db, failed_id, reason="blocked", now_iso="2026-09-19T08:00:00", existing=None,
    ))

    outcome, prompts = _scout_batch_prompt(empty_db, [
        {"id": old_id, "name": "Old Co", "feed_type": None},
        {"id": failed_id, "name": "Failed Co", "feed_type": None},
    ])

    assert outcome["scout_skipped_count"] == 0
    assert "Old Co" in prompts["scout"] and "Failed Co" in prompts["scout"]


def test_scout_works_on_at_most_35_uncovered_companies_a_run_never_scouted_first(empty_db):
    ids = [_insert_company(empty_db, name=f"Company {i:02d}", feed_type=None, rank=i) for i in range(40)]
    run(empty_db.run("UPDATE companies SET last_scouted_at = '2026-09-10T08:00:00' WHERE id IN (1, 2, 3)"))
    rows = [{"id": i, "name": f"Company {n:02d}", "feed_type": None, "rank": n,
             "last_scouted_at": "2026-09-10T08:00:00" if i in (1, 2, 3) else None}
            for n, i in enumerate(ids)]

    outcome, prompts = _scout_batch_prompt(empty_db, rows)

    assert outcome["scout_companies_worked"] == 35
    assert outcome["uncovered_count"] == 40
    # 40 uncovered, 35 slots: the 3 already scouted lose out to never-scouted ones.
    for scouted_before in ("Company 00", "Company 01", "Company 02"):
        assert scouted_before not in prompts["scout"]


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


# -- careers_repair is a registered step (issue #63) -----------------------

def test_careers_repair_runs_through_the_real_client_without_an_unknown_step_error(empty_db):
    import json
    import subprocess

    from job.agent import client as agent_client
    from job.agent.budgets import MODEL_SETTING, SHARE_SETTING, model_for_step

    company_id = run(empty_db.run(
        "INSERT INTO companies (name, status, feed_type, careers_url, careers_page_flagged) "
        "VALUES ('Acme', 'pinned', NULL, 'https://acme.example/careers', 1)"
    ))
    run_id = run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'running')"))
    seen_args = []

    def runner(args, **kwargs):
        seen_args.append(args)
        reply = {"replacements": [
            {"company_name": "Acme", "url": "https://acme.example/careers?q=intern", "note": "internship search"},
        ]}
        payload = {
            "result": json.dumps(reply),
            "modelUsage": {"m": {"inputTokens": 5, "outputTokens": 5, "webSearchRequests": 2}},
        }
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

    other_steps = _fake_run_claude({
        "discovery": '{"results": []}', "scout": '{"sources": []}',
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    })

    def real_client_step(step, prompt, *, model):
        if step != "careers_repair":
            return other_steps(step, prompt, model=model)
        return agent_client.run_claude_step(step, prompt, model=model, runner=runner)

    company = {
        "id": company_id, "name": "Acme", "feed_type": None, "careers_url": "https://acme.example/careers",
        "careers_page_flagged": 1, "careers_url_rejected": "[]",
    }
    outcome = run(run_agent_steps(
        empty_db, run_id=run_id, companies=[company], titles_doc=TITLES_DOC, locations_setting=LOCATIONS,
        budget_settings=SETTINGS, status=_open_status(), today="2026-09-20", now_iso="2026-09-20T08:00:00",
        run_claude=real_client_step,
    ))

    assert not any("unknown agent step" in e for e in outcome["errors"])
    assert "careers_repair" in outcome["steps_succeeded"]
    assert outcome["careers_repair_count"] == 1
    assert "WebSearch,WebFetch" in seen_args[0]
    assert "careers_repair" in [r["step"] for r in run(empty_db.all("SELECT step FROM usage"))]
    # registered for budgets: rides the scout's share and model
    assert SHARE_SETTING["careers_repair"] == "share_scout"
    assert MODEL_SETTING["careers_repair"] == "model_scout"
    assert model_for_step({"model_scout": "haiku"}, "careers_repair") == "haiku"
    assert model_for_step({}, "careers_repair") == "sonnet"
