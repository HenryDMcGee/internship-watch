"""Health checks, the degraded status, matching summary counts, and rejection-memory
cleanup (issue #63)."""
import asyncio
import json
import re
from datetime import timedelta

import pytest

import job.run as run_module
from conftest import adapt_discovery_reply
from job import fingerprint
from job.agent import client as agent_client
from job.agent.client import ClaudeStepResult
from job.fetchers import FetchResult
from job.fetchers import careers_pages as cp
from job.models import FetchedPosting
from job.restore_rejections import delete_duplicate_rejections


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _pinned_run_environment(monkeypatch):
    monkeypatch.setattr(run_module, "is_quarterly_refresh_day", lambda _day: False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    monkeypatch.setattr(
        run_module, "run_fetcher",
        lambda company, session=None: FetchResult(company_id=company["id"], company_name=company["name"], postings=[]),
    )


def _company(db, **overrides):
    row = {"name": "Acme", "status": "pinned", "feed_type": None, "feed_id": None}
    row.update(overrides)
    columns = ", ".join(row)
    return run(db.run(f"INSERT INTO companies ({columns}) VALUES ({', '.join('?' for _ in row)})", *row.values()))


def _posting(db):
    return run(db.run(
        "INSERT INTO postings (company_name, title, url, locations, location_match, first_seen, status) "
        "VALUES ('Acme', 'PM Intern', 'https://acme.example/1', '[]', 'match', '2026-10-01', 'open')"
    ))


def _fingerprint_row(db, *, title_key, posting_id=None, rejection_reason=None, job_id=None):
    return run(db.run(
        "INSERT INTO posting_fingerprints (job_id, title_key, posting_id, rejection_reason, settings_version, "
        "first_seen, last_seen) VALUES (?, ?, ?, ?, 'v1', '2026-10-01', '2026-10-01')",
        job_id, title_key, posting_id, rejection_reason,
    ))


def _patch_claude(monkeypatch, *, discovery_searches=None, scout_searches=3):
    def fake(step, prompt, *, model, **kwargs):
        text = {
            "discovery": '{"results": []}', "scout": '{"sources": []}',
            "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
        }[step]
        if step == "discovery":
            text = adapt_discovery_reply(text, prompt)
            names = re.search(r"watch list: (.*?)\.\nLook only", prompt, re.S).group(1).split(";")
            searches = discovery_searches if discovery_searches is not None else len(names)
        else:
            searches = scout_searches if step == "scout" else 0
        return ClaudeStepResult(step=step, model=model, text=text, web_searches=searches)

    monkeypatch.setattr(agent_client, "run_claude_step", fake)


def _capture_summary(monkeypatch):
    sent = []
    monkeypatch.setattr(run_module, "send_daily_summary", sent.append)
    return sent


# -- rejection memory cleanup ---------------------------------------------------------

def test_a_rejected_fingerprint_with_a_healthy_twin_is_deleted(empty_db):
    posting_id = _posting(empty_db)
    key = "acme|pm intern|austin"
    old_rejection = _fingerprint_row(empty_db, title_key=key, rejection_reason="location outside", job_id="JR1")
    restored_copy = _fingerprint_row(empty_db, title_key=key, posting_id=posting_id, job_id="JR2")
    lone_rejection = _fingerprint_row(empty_db, title_key="acme|marketing intern|austin", rejection_reason="wrong role")
    other_rejection = _fingerprint_row(empty_db, title_key=key + "|x", rejection_reason="wrong role")

    deleted = run(delete_duplicate_rejections(empty_db))

    assert deleted == 1
    remaining = {r["id"] for r in run(empty_db.all("SELECT id FROM posting_fingerprints"))}
    assert old_rejection not in remaining
    assert {restored_copy, lone_rejection, other_rejection} <= remaining
    assert run(empty_db.first("SELECT COUNT(*) AS n FROM postings"))["n"] == 1  # postings are never deleted


def test_two_rejections_with_the_same_key_and_no_healthy_twin_are_both_kept(empty_db):
    _fingerprint_row(empty_db, title_key="acme|x|y", rejection_reason="a")
    _fingerprint_row(empty_db, title_key="acme|x|y", rejection_reason="b")
    assert run(delete_duplicate_rejections(empty_db)) == 0
    assert run(empty_db.first("SELECT COUNT(*) AS n FROM posting_fingerprints"))["n"] == 2


def test_dry_run_counts_without_deleting(empty_db):
    posting_id = _posting(empty_db)
    _fingerprint_row(empty_db, title_key="k", rejection_reason="r")
    _fingerprint_row(empty_db, title_key="k", posting_id=posting_id)
    assert run(delete_duplicate_rejections(empty_db, dry_run=True)) == 1
    assert run(empty_db.first("SELECT COUNT(*) AS n FROM posting_fingerprints"))["n"] == 2


def test_the_daily_run_cleans_up_duplicate_rejections_and_says_so(empty_db, monkeypatch):
    posting_id = _posting(empty_db)
    _fingerprint_row(empty_db, title_key="k", rejection_reason="r")
    _fingerprint_row(empty_db, title_key="k", posting_id=posting_id)
    _patch_claude(monkeypatch)
    _capture_summary(monkeypatch)

    result = run(run_module.run(empty_db, kind="daily"))

    assert "Removed 1 old rejection(s) that already have a restored twin." in result["summary"]
    assert run(empty_db.first("SELECT COUNT(*) AS n FROM posting_fingerprints"))["n"] == 1


# -- the health checks -------------------------------------------------------------------

def _outcome(**overrides):
    base = {
        "discovery_planned_searches": 35, "discovery_searches": 35, "uncovered_count": 10,
        "scout_companies_worked": 10,
    }
    base.update(overrides)
    return base


def _failed(outcome, careers=None, candidates=3):
    return [c["check"] for c in run_module.health_failures(outcome, careers, careers_candidates=candidates)]


def test_a_healthy_run_has_no_failed_checks():
    assert _failed(_outcome(), {"attempted": 3}) == []


def test_discovery_must_make_at_least_half_its_planned_searches():
    assert _failed(_outcome(discovery_searches=18), {"attempted": 3}) == []  # 18 of 35 is over 50%
    assert _failed(_outcome(discovery_searches=17), {"attempted": 3}) == ["discovery_searches"]
    assert _failed(_outcome(discovery_searches=4), {"attempted": 3}) == ["discovery_searches"]


def test_discovery_check_is_skipped_when_nothing_was_planned():
    assert _failed(_outcome(discovery_planned_searches=0, discovery_searches=0), {"attempted": 3}) == []


def test_the_scout_must_work_on_at_least_one_company_when_any_are_uncovered():
    assert _failed(_outcome(scout_companies_worked=0), {"attempted": 3}) == ["scout_ran"]
    assert _failed(_outcome(scout_companies_worked=0, uncovered_count=0), {"attempted": 3}) == []


def test_a_careers_page_must_be_attempted_when_any_company_has_a_careers_url():
    assert _failed(_outcome(), {"attempted": 0}) == ["careers_attempted"]
    assert _failed(_outcome(), None) == ["careers_attempted"]  # the careers step itself crashed
    assert _failed(_outcome(), {"attempted": 0}, candidates=0) == []


def _no_careers_pages(monkeypatch):
    monkeypatch.setattr(
        run_module.careers_pages, "run_careers_pages",
        lambda candidates, **kw: cp.CareersRunOutcome(pages=[], browser_run_minutes_used=0.0),
    )


def test_a_healthy_daily_run_is_completed_not_degraded(empty_db, monkeypatch):
    _company(empty_db)
    _patch_claude(monkeypatch)
    sent = _capture_summary(monkeypatch)

    result = run(run_module.run(empty_db, kind="daily"))

    assert result["run_status"] == "completed"
    assert result["health"]["failed_checks"] == []
    assert run(empty_db.first("SELECT status FROM runs WHERE id = ?", result["run_id"]))["status"] == "completed"
    assert sent[0]["run_status"] == "completed"
    assert sent[0]["health"]["failed_checks"] == []


def test_run_is_degraded_when_discovery_makes_too_few_searches(empty_db, monkeypatch):
    for i in range(20):
        _company(empty_db, name=f"Company {i:02d}")
    _patch_claude(monkeypatch, discovery_searches=3)
    sent = _capture_summary(monkeypatch)

    result = run(run_module.run(empty_db, kind="daily"))

    assert result["run_status"] == "degraded"
    assert [c["check"] for c in result["health"]["failed_checks"]] == ["discovery_searches"]
    row = run(empty_db.first("SELECT status, summary FROM runs WHERE id = ?", result["run_id"]))
    assert row["status"] == "degraded"
    assert "Run degraded, failed health check(s): discovery made" in row["summary"]
    # the step failed outright and says so, in the alert and the daily summary
    assert result["agent_outcome"]["discovery_failed"]
    alert_types = {a["type"] for a in run(empty_db.all("SELECT type FROM alerts"))}
    assert {"discovery_failed", "run_degraded"} <= alert_types
    assert sent[0]["run_status"] == "degraded"
    assert sent[0]["health"]["failed_checks"] == ["discovery_searches"]
    assert {"discovery_failed", "run_degraded"} <= {a["type"] for a in sent[0]["alerts"]}


def test_run_is_degraded_when_companies_are_uncovered_but_the_scout_worked_on_none(empty_db, monkeypatch):
    _company(empty_db)
    # A scout call with zero searches fails the scout step, so it "worked on" nothing.
    _patch_claude(monkeypatch, scout_searches=0)
    _capture_summary(monkeypatch)

    result = run(run_module.run(empty_db, kind="daily"))

    assert result["run_status"] == "degraded"
    assert "scout_ran" in [c["check"] for c in result["health"]["failed_checks"]]
    assert run(empty_db.first("SELECT status FROM runs WHERE id = ?", result["run_id"]))["status"] == "degraded"


def test_run_is_degraded_when_a_company_has_a_careers_url_but_no_page_was_attempted(empty_db, monkeypatch):
    _company(empty_db, careers_url="https://acme.example/careers")
    _patch_claude(monkeypatch)
    _no_careers_pages(monkeypatch)
    sent = _capture_summary(monkeypatch)

    result = run(run_module.run(empty_db, kind="daily"))

    assert result["run_status"] == "degraded"
    assert "careers_attempted" in [c["check"] for c in result["health"]["failed_checks"]]
    assert run(empty_db.first("SELECT status FROM runs WHERE id = ?", result["run_id"]))["status"] == "degraded"
    assert "run_degraded" in {a["type"] for a in run(empty_db.all("SELECT type FROM alerts"))}
    assert "careers_attempted" in sent[0]["health"]["failed_checks"]


# -- zero new postings three days in a row ----------------------------------------------------

def _earlier_run(db, days_ago, new_count=0, status="completed"):
    day = (run_module.today_central() - timedelta(days=days_ago)).isoformat()
    run(db.run(
        "INSERT INTO runs (kind, started_at, status, new_count) VALUES ('daily', ?, ?, ?)",
        f"{day}T08:00:00", status, new_count,
    ))


def test_three_days_with_nothing_new_adds_a_per_step_breakdown(empty_db, monkeypatch):
    _company(empty_db)
    _earlier_run(empty_db, 1)
    _earlier_run(empty_db, 2, status="degraded")
    _patch_claude(monkeypatch)
    sent = _capture_summary(monkeypatch)

    result = run(run_module.run(empty_db, kind="daily"))

    breakdown = result["health"]["step_breakdown"]
    assert result["health"]["zero_new_days"] == 3
    assert set(breakdown) == {"searches", "covered", "pages_loaded", "classified"}
    assert "No new postings for 3 days in a row. Per step today:" in result["summary"]
    assert sent[0]["health"]["step_breakdown"] == breakdown
    assert sent[0]["health"]["zero_new_days"] == 3


def test_no_breakdown_after_only_two_quiet_days_or_after_a_day_with_finds(empty_db, monkeypatch):
    _company(empty_db)
    _earlier_run(empty_db, 1)
    _earlier_run(empty_db, 2, new_count=4)
    _patch_claude(monkeypatch)
    _capture_summary(monkeypatch)

    result = run(run_module.run(empty_db, kind="daily"))

    assert result["health"]["zero_new_days"] == 2
    assert result["health"]["step_breakdown"] is None
    assert "No new postings for" not in result["summary"]


def test_a_gap_in_the_days_breaks_the_streak(empty_db, monkeypatch):
    _company(empty_db)
    _earlier_run(empty_db, 1)
    _earlier_run(empty_db, 3)  # nothing ran two days ago
    _patch_claude(monkeypatch)
    _capture_summary(monkeypatch)

    assert run(run_module.run(empty_db, kind="daily"))["health"]["zero_new_days"] == 2


# -- the summary and agent_outcome report the same counts --------------------------------------

def test_summary_counts_match_agent_outcome_and_label_feed_and_classifier_rejections(empty_db, monkeypatch):
    _company(empty_db, name="Acme", feed_type="greenhouse", feed_id="acme", last_feed_status="ok")
    _company(empty_db, name="Beta")
    settings_version = fingerprint.settings_version(
        run_module.config.load_titles(), run_module.config.DEFAULT_LOCATIONS,
    )
    feed_urls = [f"https://boards.greenhouse.io/acme/jobs/{i}" for i in range(4)]
    for i, url in enumerate(feed_urls):
        run(fingerprint.record_rejection(
            empty_db, fingerprint.compute_fingerprint(company_name="Acme", title=f"Sales Intern {i}", url=url),
            reason="not a PM role", settings_version=settings_version, source="greenhouse", now_iso="2026-10-01",
        ))
    discovery_url = "https://beta.example/jobs/9"
    run(fingerprint.record_rejection(
        empty_db, fingerprint.compute_fingerprint(company_name="Beta", title="Sales Intern", url=discovery_url),
        reason="not a PM role", settings_version=settings_version, source="claude-classification", now_iso="2026-10-01",
    ))

    monkeypatch.setattr(run_module, "run_fetcher", lambda company, session=None: FetchResult(
        company_id=company["id"], company_name=company["name"],
        postings=[
            FetchedPosting(company_name="Acme", title=f"Sales Intern {i}", url=u, location_text="Austin, TX")
            for i, u in enumerate(feed_urls)
        ] if company["name"] == "Acme" else [],
    ))

    def fake(step, prompt, *, model, **kwargs):
        text = {
            "discovery": json.dumps({"results": [
                {"company_name": "Beta", "title": "Sales Intern", "url": discovery_url, "location_text": "Austin, TX"},
            ]}),
            "scout": '{"sources": []}',
            "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
        }[step]
        if step == "discovery":
            text = adapt_discovery_reply(text, prompt)
        return ClaudeStepResult(step=step, model=model, text=text, web_searches=0 if step == "improvement" else 3)

    monkeypatch.setattr(agent_client, "run_claude_step", fake)
    _capture_summary(monkeypatch)

    result = run(run_module.run(empty_db, kind="daily"))

    outcome = result["agent_outcome"]
    assert result["counts"] == outcome["counts"]
    assert outcome["feed_rejections_skipped"] == 4
    assert outcome["classifier_rejections_skipped"] == 1
    assert outcome["rejections_skipped"] == 5
    summary = result["summary"]
    assert f"{outcome['counts']['feed_rejections_skipped']} remembered feed rejection(s) skipped" in summary
    assert f"{outcome['counts']['classifier_rejections_skipped']} remembered classifier rejection(s) skipped" in summary
    assert f"{outcome['repeats_recognized']} repeat(s) recognized" in summary
    assert result["rejections_skipped"] == outcome["rejections_skipped"]
