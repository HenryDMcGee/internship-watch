"""End-to-end wiring of careers-page reading into job/run.py (issue #53,
phase 6 part 1): change detection skipping Claude, extraction feeding the
same fingerprint/classification pipeline discovery uses, exclusion from
discovery, and the run summary. `job.fetchers.careers_pages.run_careers_pages`
itself (Browser Run, the backup, robots.txt, budget switching) is covered in
tests/test_job_fetchers_careers_pages.py -- these tests replace it wholesale
with a fake, the same way existing tests replace `run_fetcher` wholesale.
"""
from __future__ import annotations

import asyncio
import json

import job.run as run_module
from job import careers_snapshot
from job.agent import client as agent_client
from job.agent.client import ClaudeStepResult
from job.fetchers import careers_pages as cp


def run(coro):
    return asyncio.run(coro)


def _insert_company(db, **overrides):
    row = {"name": "Acme", "status": "pinned", "feed_type": "greenhouse", "feed_id": "acme"}
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    return run(db.run(f"INSERT INTO companies ({columns}) VALUES ({placeholders})", *row.values()))


def _long_page(text: str) -> str:
    """A page long enough, and job-like enough, to be worth an extraction call."""
    return text + " Apply for our internship. " + "Open roles listed below. " * 100


def _empty_discovery_scout_improvement(step, model):
    return {
        "discovery": '{"results": []}',
        "scout": '{"sources": []}',
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
    }[step]


def test_unchanged_careers_page_makes_zero_claude_calls_and_discovery_runs_first(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    covered_id = _insert_company(empty_db, name="Acme", feed_type=None, feed_id=None, careers_url="https://acme.example/careers")
    uncovered_id = _insert_company(empty_db, name="Beta", feed_type=None, feed_id=None)

    page_text = "PM Intern - San Francisco"
    run(careers_snapshot.record_changed(
        empty_db, covered_id, careers_snapshot.hash_page_text(page_text),
        method="browser_run", now_iso="2026-09-01T08:00:00", existing=None,
    ))

    def fake_run_careers_pages(candidates, **kwargs):
        assert {c["id"] for c in candidates} == {covered_id}
        return cp.CareersRunOutcome(
            pages=[cp.PageOutcome(company_id=covered_id, url="https://acme.example/careers", ok=True,
                                   text=page_text, method="browser_run", browser_minutes=1.0)],
            browser_run_minutes_used=1.0, switch_reason=None,
        )

    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages", fake_run_careers_pages)

    discovery_prompts = []

    def fake_run_claude_step(step, prompt, *, model, **kwargs):
        if step == "discovery":
            discovery_prompts.append(prompt)
        if step == "careers_extraction":
            raise AssertionError("careers_extraction must not be called for an unchanged page")
        return ClaudeStepResult(step=step, model=model, text=_empty_discovery_scout_improvement(step, model),
                                 web_searches=3 if step in ("discovery", "scout") else 0)

    monkeypatch.setattr(agent_client, "run_claude_step", fake_run_claude_step)

    result = run(run_module.run(empty_db, kind="daily"))

    assert "careers_extraction" not in [r["step"] for r in run(empty_db.all("SELECT step FROM usage"))]
    # Discovery now runs before the careers step (issue #70), so a company whose
    # page loads later in the same run is still in this run's discovery list.
    assert "Beta" in discovery_prompts[0]

    snapshot = run(careers_snapshot.load_snapshot(empty_db, covered_id))
    assert snapshot["status"] == "unchanged"
    assert "Careers pages:" in result["summary"]


def test_changed_careers_page_extracts_and_only_new_listings_get_classified(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    company_id = _insert_company(empty_db, name="Acme", feed_type=None, feed_id=None, careers_url="https://acme.example/careers")

    # A posting already on file from a previous run, fingerprinted by its job id --
    # the careers page still lists it, and it must not be re-classified or duplicated.
    existing_posting_id = run(empty_db.run(
        """INSERT INTO postings (company_id, company_name, title, matched_title, url, locations,
           location_match, first_seen, status)
           VALUES (?, 'Acme', 'Data Analyst Intern', 'Data Analyst Intern',
                   'https://acme.example/jobs/OLD', '[]', 'match', '2026-08-01', 'open')""",
        company_id,
    ))
    from job import fingerprint
    fp = fingerprint.compute_fingerprint(company_name="Acme", title="Data Analyst Intern", url="https://acme.example/jobs/OLD", external_id="already-known")
    run(fingerprint.record_posting(
        empty_db, fp, posting_id=existing_posting_id, settings_version="irrelevant-for-this-test",
        source="feed", now_iso="2026-08-01T08:00:00",
    ))

    page_text = _long_page("listings page")

    def fake_run_careers_pages(candidates, **kwargs):
        return cp.CareersRunOutcome(
            pages=[cp.PageOutcome(company_id=company_id, url="https://acme.example/careers", ok=True,
                                   text=page_text, method="browser_run", browser_minutes=2.0)],
            browser_run_minutes_used=2.0, switch_reason=None,
        )

    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages", fake_run_careers_pages)

    extraction_calls = []

    def fake_run_claude_step(step, prompt, *, model, **kwargs):
        if step == "careers_extraction":
            extraction_calls.append(prompt)
            listings = {
                "listings": [
                    {"title": "Data Analyst Intern", "location": "SF", "link": "https://acme.example/jobs/OLD",
                     "posted_date": None, "job_id": "already-known"},
                    {"title": "MBA Product Manager Intern", "location": "San Francisco, CA",
                     "link": "https://acme.example/jobs/NEW", "posted_date": "2026-09-01", "job_id": "new-job"},
                ],
            }
            return ClaudeStepResult(step=step, model=model, text=json.dumps(listings))
        if step == "classification":
            return ClaudeStepResult(step=step, model=model, text=prompt_to_decision(prompt))
        return ClaudeStepResult(step=step, model=model, text=_empty_discovery_scout_improvement(step, model),
                                 web_searches=3 if step in ("discovery", "scout") else 0)

    def prompt_to_decision(prompt: str) -> str:
        # The classification prompt lists each unclassified posting's real id
        # (`id=<n>`) -- pull it out instead of hardcoding one, since it depends
        # on autoincrement state from earlier inserts in this test.
        import re
        match = re.search(r"id=(\d+)", prompt)
        posting_id = int(match.group(1))
        return json.dumps({"decisions": [{"id": posting_id, "decision": "match",
                                           "matched_title": "MBA Product Manager Intern", "reason": "matches"}]})

    monkeypatch.setattr(agent_client, "run_claude_step", fake_run_claude_step)

    result = run(run_module.run(empty_db, kind="daily"))

    assert len(extraction_calls) == 1

    postings = run(empty_db.all("SELECT * FROM postings ORDER BY id"))
    # the already-known listing must not have been duplicated
    assert len(postings) == 2
    new_posting = next(p for p in postings if p["url"] == "https://acme.example/jobs/NEW")
    assert new_posting["matched_title"] == "MBA Product Manager Intern"
    assert new_posting["archived_at"] is None
    assert result["new_count"] == 1


def test_failed_careers_page_is_recorded_and_included_in_discovery(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    blocked_id = _insert_company(empty_db, name="Acme", feed_type=None, feed_id=None, careers_url="https://acme.example/careers")

    def fake_run_careers_pages(candidates, **kwargs):
        return cp.CareersRunOutcome(
            pages=[cp.PageOutcome(company_id=blocked_id, url="https://acme.example/careers", ok=False,
                                   reason=cp.REASON_BLOCKED)],
            browser_run_minutes_used=0.0, switch_reason=None,
        )

    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages", fake_run_careers_pages)

    discovery_prompts = []

    def fake_run_claude_step(step, prompt, *, model, **kwargs):
        if step == "discovery":
            discovery_prompts.append(prompt)
        return ClaudeStepResult(step=step, model=model, text=_empty_discovery_scout_improvement(step, model),
                                 web_searches=3 if step in ("discovery", "scout") else 0)

    monkeypatch.setattr(agent_client, "run_claude_step", fake_run_claude_step)

    result = run(run_module.run(empty_db, kind="daily"))

    assert "Acme" in discovery_prompts[0]
    snapshot = run(careers_snapshot.load_snapshot(empty_db, blocked_id))
    assert snapshot["status"] == "failed"
    assert snapshot["last_error"] == cp.REASON_BLOCKED
    assert snapshot["consecutive_failures"] == 1
    assert "1 failed" in result["summary"]


def test_backup_loaded_page_reuses_change_detection_across_runs(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    company_id = _insert_company(empty_db, name="Acme", feed_type=None, feed_id=None, careers_url="https://acme.example/careers")
    page_text = _long_page("same content every run")

    def fake_run_careers_pages(candidates, **kwargs):
        return cp.CareersRunOutcome(
            pages=[cp.PageOutcome(company_id=company_id, url="https://acme.example/careers", ok=True,
                                   text=page_text, method="backup")],
            browser_run_minutes_used=0.0, switch_reason="missing_token",
        )

    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages", fake_run_careers_pages)

    extraction_calls = {"n": 0}

    def fake_run_claude_step(step, prompt, *, model, **kwargs):
        if step == "careers_extraction":
            extraction_calls["n"] += 1
            return ClaudeStepResult(step=step, model=model, text='{"listings": []}')
        return ClaudeStepResult(step=step, model=model, text=_empty_discovery_scout_improvement(step, model),
                                 web_searches=3 if step in ("discovery", "scout") else 0)

    monkeypatch.setattr(agent_client, "run_claude_step", fake_run_claude_step)

    run(run_module.run(empty_db, kind="daily"))
    assert extraction_calls["n"] == 1

    run(run_module.run(empty_db, kind="daily"))
    assert extraction_calls["n"] == 1  # unchanged the second time -- no second extraction call

    snapshot = run(careers_snapshot.load_snapshot(empty_db, company_id))
    assert snapshot["method"] == "backup"
    assert snapshot["status"] == "unchanged"


def test_company_flagged_after_3_failures_in_a_row(empty_db, monkeypatch):
    # issue #55 test case 1: 3 failures in a row flags the company; one
    # success in between resets the count and prevents the flag.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    company_id = _insert_company(empty_db, name="Acme", feed_type=None, feed_id=None, careers_url="https://acme.example/careers")

    call_count = {"n": 0}
    outcomes = [
        cp.PageOutcome(company_id=company_id, url="https://acme.example/careers", ok=False, reason=cp.REASON_TIMEOUT),
        cp.PageOutcome(company_id=company_id, url="https://acme.example/careers", ok=True, text="jobs page",
                       method="browser_run", browser_minutes=1.0),
        cp.PageOutcome(company_id=company_id, url="https://acme.example/careers", ok=False, reason=cp.REASON_TIMEOUT),
        cp.PageOutcome(company_id=company_id, url="https://acme.example/careers", ok=False, reason=cp.REASON_TIMEOUT),
        cp.PageOutcome(company_id=company_id, url="https://acme.example/careers", ok=False, reason=cp.REASON_TIMEOUT),
    ]

    def fake_run_careers_pages(candidates, **kwargs):
        page = outcomes[call_count["n"]]
        call_count["n"] += 1
        return cp.CareersRunOutcome(
            pages=[page], browser_run_minutes_used=page.browser_minutes or 0.0, switch_reason=None,
            browser_run_pages=1 if page.ok else 0, browser_run_failures=0 if page.ok else 1,
        )

    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages", fake_run_careers_pages)

    def fake_run_claude_step(step, prompt, *, model, **kwargs):
        return ClaudeStepResult(step=step, model=model, text=_empty_discovery_scout_improvement(step, model),
                                 web_searches=3 if step in ("discovery", "scout") else 0)

    monkeypatch.setattr(agent_client, "run_claude_step", fake_run_claude_step)

    # Failure 1 of a possible streak.
    run(run_module.run(empty_db, kind="daily"))
    company = run(empty_db.first("SELECT careers_page_flagged FROM companies WHERE id = ?", company_id))
    assert company["careers_page_flagged"] == 0

    # A success in between resets the streak.
    run(run_module.run(empty_db, kind="daily"))
    company = run(empty_db.first("SELECT careers_page_flagged FROM companies WHERE id = ?", company_id))
    assert company["careers_page_flagged"] == 0

    # 1 failure since the reset.
    run(run_module.run(empty_db, kind="daily"))
    company = run(empty_db.first("SELECT careers_page_flagged FROM companies WHERE id = ?", company_id))
    assert company["careers_page_flagged"] == 0

    # 2 failures in a row since the reset -- still not flagged.
    run(run_module.run(empty_db, kind="daily"))
    company = run(empty_db.first("SELECT careers_page_flagged FROM companies WHERE id = ?", company_id))
    assert company["careers_page_flagged"] == 0

    # 3rd failure in a row since the reset -- now flagged.
    run(run_module.run(empty_db, kind="daily"))
    company = run(empty_db.first("SELECT careers_page_flagged FROM companies WHERE id = ?", company_id))
    assert company["careers_page_flagged"] == 1


def test_browser_run_usage_recorded_per_method(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    browser_id = _insert_company(empty_db, name="Acme", feed_type=None, feed_id=None, careers_url="https://acme.example/careers")
    backup_id = _insert_company(empty_db, name="Beta", feed_type=None, feed_id=None, careers_url="https://beta.example/careers")

    def fake_run_careers_pages(candidates, **kwargs):
        return cp.CareersRunOutcome(
            pages=[
                cp.PageOutcome(company_id=browser_id, url="https://acme.example/careers", ok=True, text="jobs",
                               method="browser_run", browser_minutes=2.0, measured_by=cp.MEASURED_BY_ELAPSED),
                cp.PageOutcome(company_id=backup_id, url="https://beta.example/careers", ok=True, text="jobs",
                               method="backup"),
            ],
            browser_run_minutes_used=2.0, switch_reason="minutes",
            browser_run_pages=1, browser_run_failures=0, backup_pages=1, backup_failures=0,
        )

    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages", fake_run_careers_pages)

    def fake_run_claude_step(step, prompt, *, model, **kwargs):
        if step == "careers_extraction":
            return ClaudeStepResult(step=step, model=model, text='{"listings": []}')
        return ClaudeStepResult(step=step, model=model, text=_empty_discovery_scout_improvement(step, model),
                                 web_searches=3 if step in ("discovery", "scout") else 0)

    monkeypatch.setattr(agent_client, "run_claude_step", fake_run_claude_step)

    run(run_module.run(empty_db, kind="daily"))

    rows = run(empty_db.all("SELECT * FROM browser_run_usage ORDER BY method"))
    assert len(rows) == 2
    backup_row = next(r for r in rows if r["method"] == "backup")
    browser_row = next(r for r in rows if r["method"] == "browser_run")

    assert browser_row["pages"] == 1
    assert browser_row["changed"] == 1
    assert browser_row["browser_ms"] == 120000
    assert browser_row["measured_by"] == "elapsed"
    assert browser_row["failures"] == 0
    assert browser_row["backup_reason"] is None

    assert backup_row["pages"] == 1
    assert backup_row["changed"] == 1
    assert backup_row["browser_ms"] is None
    assert backup_row["failures"] == 0
    assert backup_row["backup_reason"] == "minutes"


def test_browser_run_100_alert_fires_when_daily_minutes_budget_is_reached(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    company_id = _insert_company(empty_db, name="Acme", feed_type=None, feed_id=None, careers_url="https://acme.example/careers")

    def fake_run_careers_pages(candidates, **kwargs):
        return cp.CareersRunOutcome(
            pages=[cp.PageOutcome(company_id=company_id, url="https://acme.example/careers", ok=True,
                                   text="jobs", method="browser_run", browser_minutes=9.0,
                                   measured_by=cp.MEASURED_BY_CLOUDFLARE_HEADER)],
            browser_run_minutes_used=9.0, switch_reason=None,
            browser_run_pages=1, browser_run_failures=0,
        )

    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages", fake_run_careers_pages)

    def fake_run_claude_step(step, prompt, *, model, **kwargs):
        if step == "careers_extraction":
            return ClaudeStepResult(step=step, model=model, text='{"listings": []}')
        return ClaudeStepResult(step=step, model=model, text=_empty_discovery_scout_improvement(step, model),
                                 web_searches=3 if step in ("discovery", "scout") else 0)

    monkeypatch.setattr(agent_client, "run_claude_step", fake_run_claude_step)

    run(run_module.run(empty_db, kind="daily"))

    alerts = run(empty_db.all("SELECT * FROM alerts"))
    assert any(a["type"] == "browser_run_100" for a in alerts)


def _stats(**overrides) -> dict:
    row = {
        "browser_run": 0, "backup": 0, "unchanged": 0, "changed": 0, "failed": 0,
        "browser_run_minutes": 0.0, "switch_reason": None,
    }
    row.update(overrides)
    return row


def test_summary_line_counts_pages_per_method():
    # issue #53 test case 23: 5 unchanged, 3 changed, 2 failed, 4 of the 8
    # loaded pages via the backup (so 4 via Browser Run).
    line = run_module._careers_pages_summary_line(_stats(
        browser_run=4, backup=4, unchanged=5, changed=3, failed=2, browser_run_minutes=6.0,
    ))
    assert "4 via Browser Run (6.0 min)" in line
    assert "4 via backup" in line
    assert "5 unchanged" in line
    assert "3 changed" in line
    assert "2 failed" in line


def test_summary_line_has_no_workers_paid_note_for_an_outage():
    # issue #53 test case 24: only a quota/minutes switch suggests Workers
    # Paid -- an outage isn't a capacity problem, so it gets its own note
    # with no such suggestion.
    line = run_module._careers_pages_summary_line(_stats(browser_run=2, backup=1, switch_reason="outage"))
    assert "Workers Paid" not in line
    assert "outage" in line


def test_summary_line_suggests_workers_paid_for_quota_or_minutes():
    for reason in ("quota", "minutes"):
        line = run_module._careers_pages_summary_line(_stats(browser_run=2, backup=1, switch_reason=reason))
        assert "Workers Paid" in line


def test_summary_line_is_none_when_nothing_happened():
    assert run_module._careers_pages_summary_line(_stats()) is None
    assert run_module._careers_pages_summary_line(None) is None


# ---------------------------------------------------------------------------
# A careers-page find counts as confirmed open (public board not updating
# after a search: new careers-page postings stayed "Status not confirmed")
# ---------------------------------------------------------------------------

def _offline_page_checks(monkeypatch):
    """Page-data and browser checks can't decide, as on most careers sites."""
    import requests

    from job.verify import browser as browser_module
    from job.verify import structured as structured_module

    def no_network(*args, **kwargs):
        raise requests.ConnectionError("offline in tests")

    def no_browser(url):
        raise RuntimeError("no browser in tests")

    monkeypatch.setattr(structured_module.requests, "get", no_network)
    monkeypatch.setattr(browser_module, "_load_page_text", no_browser)


def _careers_run(empty_db, monkeypatch, company_id, listings):
    page_text = _long_page("listings page " + json.dumps(listings))

    def fake_run_careers_pages(candidates, **kwargs):
        return cp.CareersRunOutcome(
            pages=[cp.PageOutcome(company_id=company_id, url="https://acme.example/careers", ok=True,
                                   text=page_text, method="browser_run", browser_minutes=1.0)],
            browser_run_minutes_used=1.0, switch_reason=None,
        )

    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages", fake_run_careers_pages)

    def fake_run_claude_step(step, prompt, *, model, **kwargs):
        if step == "careers_extraction":
            return ClaudeStepResult(step=step, model=model, text=json.dumps({"listings": listings}))
        if step == "classification":
            import re
            ids = [int(i) for i in re.findall(r"id=(\d+)", prompt)]
            return ClaudeStepResult(step=step, model=model, text=json.dumps({"decisions": [
                {"id": i, "decision": "match", "matched_title": "MBA Product Manager Intern", "reason": "matches"}
                for i in ids
            ]}))
        return ClaudeStepResult(step=step, model=model, text=_empty_discovery_scout_improvement(step, model),
                                 web_searches=3 if step in ("discovery", "scout") else 0)

    monkeypatch.setattr(agent_client, "run_claude_step", fake_run_claude_step)
    return run(run_module.run(empty_db, kind="daily"))


def test_posting_found_on_a_careers_page_is_open_after_the_run(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    _offline_page_checks(monkeypatch)
    company_id = _insert_company(empty_db, name="Acme", feed_type=None, feed_id=None,
                                 careers_url="https://acme.example/careers")

    result = _careers_run(empty_db, monkeypatch, company_id, [
        {"title": "MBA Product Manager Intern", "location": "San Francisco, CA",
         "link": "https://acme.example/jobs/NEW", "posted_date": "2026-10-01", "job_id": "new-job"},
    ])

    assert result["new_count"] == 1
    posting = run(empty_db.first("SELECT * FROM postings WHERE url = 'https://acme.example/jobs/NEW'"))
    assert posting["status"] == "open"
    assert posting["verified_via"] == "careers_page"
    assert posting["archived_at"] is None


def test_posting_gone_from_a_careers_page_is_not_closed(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    _offline_page_checks(monkeypatch)
    company_id = _insert_company(empty_db, name="Acme", feed_type=None, feed_id=None,
                                 careers_url="https://acme.example/careers")
    earlier_id = run(empty_db.run(
        """INSERT INTO postings (company_id, company_name, title, matched_title, url, locations,
           location_match, first_seen, status, verified_via, verified_at)
           VALUES (?, 'Acme', 'MBA Product Manager Intern', 'MBA Product Manager Intern',
                   'https://acme.example/jobs/EARLIER', '[]', 'match', '2026-10-01', 'open',
                   'careers_page', '2026-10-01T08:00:00')""",
        company_id,
    ))

    _careers_run(empty_db, monkeypatch, company_id, [
        {"title": "MBA Product Manager Intern", "location": "San Francisco, CA",
         "link": "https://acme.example/jobs/OTHER", "posted_date": "2026-10-01", "job_id": "other-job"},
    ])

    earlier = run(empty_db.first("SELECT * FROM postings WHERE id = ?", earlier_id))
    assert earlier["status"] == "open"
    assert earlier["archived_at"] is None
