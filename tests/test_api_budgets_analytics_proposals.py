import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from src.main import app, central_today


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(empty_db):
    app.state.db = empty_db
    app.state.access_team_domain = ""
    app.state.access_aud = ""
    yield TestClient(app), empty_db
    app.state.db = None


def _valid_budget_doc(**overrides):
    doc = {
        "daily_usd_budget": 1.50, "monthly_usd_budget": 30.00, "daily_search_budget": 35,
        "search_limit_per_run": 35, "search_limit_per_company": 5, "browser_checks_per_run": 50,
        "public_feedback_daily_limit": 20,
        "share_discovery": 0.60, "share_scout": 0.20, "share_classification": 0.15, "share_improvement": 0.05,
        "model_discovery": "sonnet", "model_scout": "sonnet", "model_classification": "haiku",
        "model_improvement": "sonnet",
    }
    doc.update(overrides)
    return doc


# -- budgets ------------------------------------------------------------

def _keys_in(value, found=None):
    found = set() if found is None else found
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(k)
            _keys_in(v, found)
    elif isinstance(value, list):
        for v in value:
            _keys_in(v, found)
    return found


def test_get_budgets_returns_settings_and_a_dollars_forecast(client):
    tc, db = client
    resp = tc.get("/api/budgets")
    assert resp.status_code == 200
    body = resp.json()
    assert "billing_mode" not in body["settings"]
    assert body["settings"]["daily_usd_budget"] == 1.50
    assert body["settings"]["public_feedback_daily_limit"] == 20
    assert set(body["forecast"]) == {"weekly_usd", "monthly_usd"}
    # A migrated database starts with the phase 9 migration's own history rows and nothing else.
    assert {c["field"] for c in body["changes"]} == {"billing_mode", "daily_token_budget", "weekly_token_budget"}


def test_budgets_payload_has_month_dollars_the_public_site_block_and_no_token_fields(client):
    tc, db = client
    today = central_today()
    run(db.run(
        """INSERT INTO usage (run_id, step, model, input_tokens, output_tokens, cache_read_tokens,
           cache_write_tokens, web_searches, est_cost_usd, at)
           VALUES (1, 'discovery', 'sonnet', 100, 50, 0, 0, 2, 0.25, ?)""", f"{today}T08:00:00-05:00",
    ))
    for i in range(2):
        run(db.run(
            "INSERT INTO feedback (source, kind, message, visitor_hash, created_at) VALUES ('public', 'other', 'x', ?, ?)",
            f"v{i}", f"{today}T08:00:00-05:00",
        ))
    run(db.run("INSERT INTO feedback (source, kind, message, created_at) VALUES ('owner', 'bug', 'mine', ?)", f"{today}T08:00:00-05:00"))
    run(db.run("INSERT INTO public_board_views (day, views) VALUES (?, 3)", today))

    body = tc.get("/api/budgets").json()

    assert body["usage"]["daily"] == {"searches": 2, "usd": 0.25}
    assert body["usage"]["monthly"]["usd"] == pytest.approx(0.25)
    assert body["forecast"]["monthly_usd"] >= 0.25
    assert body["forecast"]["weekly_usd"] >= 0.25
    # Only visitors count toward the cap; Henry's own feedback never does.
    assert body["public_site"] == {"feedback_daily_limit": 20, "feedback_today": 2, "board_views_today": 3}
    for key in _keys_in(body):
        assert "token" not in key, key
        assert "billing" not in key, key


def test_public_site_block_is_zero_when_nothing_happened_today(client):
    tc, db = client
    body = tc.get("/api/budgets").json()
    assert body["public_site"] == {"feedback_daily_limit": 20, "feedback_today": 0, "board_views_today": 0}


def test_put_budgets_updates_settings_and_records_change(client):
    tc, db = client
    resp = tc.put("/api/budgets", json={"doc": _valid_budget_doc(daily_usd_budget=2.0)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"]["daily_usd_budget"] == 2.0
    assert any(c["field"] == "daily_usd_budget" for c in body["changes"])


def test_put_budgets_stores_the_feedback_cap_and_rejects_a_negative_one(client):
    tc, db = client
    resp = tc.put("/api/budgets", json={"doc": _valid_budget_doc(public_feedback_daily_limit=-1)})
    assert resp.status_code == 422
    assert any("public_feedback_daily_limit" in p for p in resp.json()["detail"]["problems"])

    resp = tc.put("/api/budgets", json={"doc": _valid_budget_doc(public_feedback_daily_limit=5)})
    assert resp.status_code == 200
    assert resp.json()["settings"]["public_feedback_daily_limit"] == 5
    assert resp.json()["public_site"]["feedback_daily_limit"] == 5


def test_put_budgets_ignores_the_removed_fields_from_a_stale_dashboard(client):
    tc, db = client
    doc = _valid_budget_doc(billing_mode="api_key", daily_token_budget=5, weekly_token_budget=6)
    resp = tc.put("/api/budgets", json={"doc": doc})
    assert resp.status_code == 200
    for key in ("billing_mode", "daily_token_budget", "weekly_token_budget"):
        assert key not in resp.json()["settings"]
    # Nothing new in the history for them either: only the migration's own rows mention them.
    stale = [c for c in resp.json()["changes"] if c["field"] in ("billing_mode", "daily_token_budget", "weekly_token_budget")]
    assert all(c["new_value"].startswith("removed") for c in stale) and len(stale) == 3


def test_put_budgets_rejects_shares_not_summing_to_100_percent(client):
    tc, db = client
    resp = tc.put("/api/budgets", json={"doc": _valid_budget_doc(share_discovery=0.99)})
    assert resp.status_code == 422


def test_get_budgets_includes_browser_run_summary(client):
    tc, db = client
    resp = tc.get("/api/budgets")
    body = resp.json()
    assert "browser_run" in body
    assert body["browser_run"]["plan"] == "free"
    assert body["browser_run"]["month"] is None
    assert "reset_at" in body["browser_run"]["cloudflare_free"]


# -- sources / careers pages (issue #55) ---------------------------------

def test_sources_response_includes_careers_page_status(client):
    tc, db = client
    company_id = run(db.run(
        "INSERT INTO companies (name, status, careers_url, careers_page_flagged) "
        "VALUES ('Acme', 'pinned', 'https://acme.example/careers', 1)"
    ))
    run(db.run(
        "INSERT INTO careers_page_snapshots (company_id, last_checked, status, consecutive_failures, last_error) "
        "VALUES (?, '2026-09-20T08:00:00', 'failed', 3, 'blocked')", company_id,
    ))
    resp = tc.get("/api/sources")
    assert resp.status_code == 200
    body = resp.json()
    assert "careers_pages" in body
    entry = next(c for c in body["careers_pages"] if c["company_id"] == company_id)
    assert entry["status"] == "blocked"
    assert entry["flagged"] is True
    assert entry["last_checked"] == "2026-09-20T08:00:00"


def test_sources_response_marks_never_checked_careers_page_as_working(client):
    tc, db = client
    company_id = run(db.run(
        "INSERT INTO companies (name, status, careers_url) VALUES ('Acme', 'pinned', 'https://acme.example/careers')"
    ))
    body = tc.get("/api/sources").json()
    entry = next(c for c in body["careers_pages"] if c["company_id"] == company_id)
    assert entry["status"] == "working"
    assert entry["flagged"] is False


# -- analytics ------------------------------------------------------------

def test_get_analytics_shape(client):
    tc, db = client
    company_id = run(db.run(
        "INSERT INTO companies (name, status, feed_type) VALUES ('Acme', 'pinned', 'greenhouse')"
    ))
    run(db.run(
        """INSERT INTO postings (company_id, company_name, title, matched_title, url, locations, location_match,
           first_seen, status, verified_via)
           VALUES (?, 'Acme', 'PM Intern', 'PM Intern', 'https://x', '[]', 'match', '2026-09-20', 'open', 'feed')""",
        company_id,
    ))
    run(db.run(
        """INSERT INTO postings (company_name, title, url, locations, location_match, first_seen, status,
           archived_at, archive_reason)
           VALUES ('Acme', 'Old role', 'https://y', '[]', 'match', '2026-09-01', 'closed',
                   '2026-09-10T00:00:00', 'No longer open.')"""
    ))
    run(db.run(
        """INSERT INTO usage (run_id, step, model, input_tokens, output_tokens, cache_read_tokens,
           cache_write_tokens, web_searches, est_cost_usd, at)
           VALUES (1, 'discovery', 'sonnet', 100, 50, 0, 0, 2, 0.01, '2026-09-20T08:00:00')"""
    ))
    run(db.run("INSERT INTO alerts (type, severity, message, created_at) VALUES ('budget_80', 'warning', 'x', '2026-09-20T08:00:00')"))

    resp = tc.get("/api/analytics")
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "coverage", "sources", "first_to_find", "postings_per_title", "verification_methods",
        "unconfirmed_count", "pruned", "usage_by_step", "budgets", "forecast", "alerts",
        "postings_per_source", "careers_pages_per_run", "discovery_searches_per_run",
    ):
        assert key in body
    assert set(body["forecast"]) == {"weekly_usd", "monthly_usd"}
    assert "billing_mode" not in body["budgets"]
    assert body["coverage"][0]["name"] == "Acme"
    assert body["coverage"][0]["posting_count"] == 1
    assert body["pruned"][0]["archive_reason"] == "No longer open."
    assert len(body["alerts"]) == 1


def test_analytics_postings_per_source_buckets_by_category(client):
    tc, db = client
    for source_name in ("greenhouse", "careers_page", "claude-discovery", None):
        run(db.run(
            """INSERT INTO postings (company_name, title, url, source_name, locations, location_match,
               first_seen, status) VALUES ('Acme', 'PM Intern', ?, ?, '[]', 'match', '2026-09-20', 'open')""",
            f"https://x/{source_name}", source_name,
        ))
    body = tc.get("/api/analytics").json()
    assert body["postings_per_source"] == {"feed": 1, "careers_page": 1, "discovery": 1, "email": 0}


def test_analytics_careers_pages_per_run_and_discovery_searches_per_run(client):
    tc, db = client
    run_id = run(db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-20T08:00:00', 'completed')"))
    run(db.run(
        """INSERT INTO browser_run_usage (run_id, at, method, pages, unchanged, changed, browser_ms, failures)
           VALUES (?, '2026-09-20T08:00:00', 'browser_run', 5, 3, 2, 60000, 1)""",
        run_id,
    ))
    run(db.run(
        """INSERT INTO browser_run_usage (run_id, at, method, pages, unchanged, changed, failures, backup_reason)
           VALUES (?, '2026-09-20T08:00:00', 'backup', 2, 1, 1, 0, 'minutes')""",
        run_id,
    ))
    run(db.run(
        """INSERT INTO usage (run_id, step, model, input_tokens, output_tokens, cache_read_tokens,
           cache_write_tokens, web_searches, est_cost_usd, at)
           VALUES (?, 'discovery', 'sonnet', 100, 50, 0, 0, 4, 0.01, '2026-09-20T08:00:00')""",
        run_id,
    ))

    body = tc.get("/api/analytics").json()
    methods = {r["method"] for r in body["careers_pages_per_run"]}
    assert methods == {"browser_run", "backup"}
    browser_row = next(r for r in body["careers_pages_per_run"] if r["method"] == "browser_run")
    assert browser_row["pages"] == 5
    assert browser_row["changed"] == 2
    assert browser_row["failures"] == 1

    assert len(body["discovery_searches_per_run"]) == 1
    assert body["discovery_searches_per_run"][0]["searches"] == 4


# -- proposals ------------------------------------------------------------

def _insert_proposal(db, kind, payload, status="pending"):
    return run(db.run(
        "INSERT INTO proposals (kind, payload, status, created_at) VALUES (?, ?, ?, '2026-09-20T08:00:00')",
        kind, json.dumps(payload), status,
    ))


def test_list_proposals(client):
    tc, db = client
    _insert_proposal(db, "company", {"action": "add", "name": "Acme"})
    resp = tc.get("/api/proposals")
    assert resp.status_code == 200
    assert len(resp.json()["proposals"]) == 1
    assert resp.json()["proposals"][0]["payload"]["name"] == "Acme"


def test_approve_company_proposal_adds_company(client):
    tc, db = client
    proposal_id = _insert_proposal(db, "company", {"action": "add", "name": "Acme", "careers_url": "https://boards.greenhouse.io/acme"})
    resp = tc.post(f"/api/proposals/{proposal_id}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    company = run(db.first("SELECT * FROM companies WHERE name = 'Acme'"))
    assert company is not None
    assert company["feed_type"] == "greenhouse"


def test_approve_source_proposal_adds_trial_source(client):
    tc, db = client
    proposal_id = _insert_proposal(db, "source", {"name": "Acme careers", "url": "https://acme.example/careers", "kind": "Company career sites"})
    resp = tc.post(f"/api/proposals/{proposal_id}/approve")
    assert resp.status_code == 200
    source = run(db.first("SELECT * FROM sources WHERE name = 'Acme careers'"))
    assert source["status"] == "trial"


def test_approve_source_retire_proposal_deletes_source(client):
    tc, db = client
    source_id = run(db.run(
        "INSERT INTO sources (name, url, kind, status, found_count) VALUES ('Dead board', 'https://x', 'Job boards', 'demoted', 0)"
    ))
    proposal_id = _insert_proposal(db, "source_retire", {"source_id": source_id})
    resp = tc.post(f"/api/proposals/{proposal_id}/approve")
    assert resp.status_code == 200
    assert run(db.first("SELECT * FROM sources WHERE id = ?", source_id)) is None


def test_reject_proposal_leaves_data_unchanged(client):
    tc, db = client
    proposal_id = _insert_proposal(db, "company", {"action": "add", "name": "Acme"})
    resp = tc.post(f"/api/proposals/{proposal_id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert run(db.first("SELECT * FROM companies WHERE name = 'Acme'")) is None


def test_cannot_decide_a_proposal_twice(client):
    tc, db = client
    proposal_id = _insert_proposal(db, "company", {"action": "add", "name": "Acme"})
    tc.post(f"/api/proposals/{proposal_id}/approve")
    resp = tc.post(f"/api/proposals/{proposal_id}/reject")
    assert resp.status_code == 409


def test_approve_missing_proposal_404(client):
    tc, db = client
    resp = tc.post("/api/proposals/999/approve")
    assert resp.status_code == 404


# -- careers_repair proposals (issue #55) --------------------------------

def _insert_flagged_company(db, **overrides):
    row = {
        "name": "Acme", "status": "pinned", "careers_url": "https://acme.example/careers",
        "careers_page_flagged": 1, "careers_url_rejected": "[]",
    }
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    return run(db.run(f"INSERT INTO companies ({columns}) VALUES ({placeholders})", *row.values()))


def test_approve_careers_repair_proposal_replaces_url_and_clears_flag(client):
    tc, db = client
    company_id = _insert_flagged_company(db)
    run(db.run(
        "INSERT INTO careers_page_snapshots (company_id, last_checked, status, consecutive_failures, last_error) "
        "VALUES (?, '2026-09-19T08:00:00', 'failed', 3, 'timeout')", company_id,
    ))
    proposal_id = _insert_proposal(db, "careers_repair", {
        "company_id": company_id, "company_name": "Acme",
        "old_url": "https://acme.example/careers", "new_url": "https://acme.example/careers?q=intern",
    })

    resp = tc.post(f"/api/proposals/{proposal_id}/approve")
    assert resp.status_code == 200

    company = run(db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["careers_url"] == "https://acme.example/careers?q=intern"
    assert company["careers_page_flagged"] == 0

    snapshot = run(db.first("SELECT * FROM careers_page_snapshots WHERE company_id = ?", company_id))
    assert snapshot["consecutive_failures"] == 0
    assert snapshot["last_error"] is None


def test_reject_careers_repair_proposal_keeps_flag_and_remembers_url(client):
    tc, db = client
    company_id = _insert_flagged_company(db)
    proposal_id = _insert_proposal(db, "careers_repair", {
        "company_id": company_id, "company_name": "Acme",
        "old_url": "https://acme.example/careers", "new_url": "https://acme.example/careers?q=intern",
    })

    resp = tc.post(f"/api/proposals/{proposal_id}/reject")
    assert resp.status_code == 200

    company = run(db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["careers_page_flagged"] == 1
    assert json.loads(company["careers_url_rejected"]) == ["https://acme.example/careers?q=intern"]
    # the old URL must be untouched by a rejection
    assert company["careers_url"] == "https://acme.example/careers"


# -- careers_url_approved and recent runs (issue #63) ---------------------

def test_approving_a_careers_repair_marks_the_new_url_as_approved(client):
    tc, db = client
    company_id = _insert_flagged_company(db, careers_note="careers page, no feed")
    proposal_id = _insert_proposal(db, "careers_repair", {
        "company_id": company_id, "company_name": "Acme",
        "old_url": "https://acme.example/careers", "new_url": "https://acme.example/careers?q=intern",
    })

    assert tc.post(f"/api/proposals/{proposal_id}/approve").status_code == 200

    company = run(db.first("SELECT * FROM companies WHERE id = ?", company_id))
    assert company["careers_url_approved"] == 1
    assert company["careers_note"] is None


def test_a_careers_link_henry_adds_or_pastes_counts_as_approved(client):
    tc, db = client
    resp = tc.post("/api/companies", json={"name": "Acme Co", "careers_url": "https://acme.example/careers"})
    assert resp.status_code == 201
    assert tc.post("/api/companies", json={"name": "Plain Co"}).status_code == 201
    tc.post("/api/companies/paste/save", json={"entries": [{"name": "Pasted Co", "url": "https://pasted.example/jobs"}]})

    rows = {r["name"]: r["careers_url_approved"] for r in run(db.all("SELECT name, careers_url_approved FROM companies"))}
    assert rows == {"Acme Co": 1, "Plain Co": 0, "Pasted Co": 1}


def test_analytics_lists_recent_runs_with_their_status(client):
    tc, db = client
    run(db.run(
        "INSERT INTO runs (kind, started_at, status, new_count, summary) VALUES "
        "('daily', '2026-10-04T08:00:00', 'degraded', 0, 'Checked 5. Run degraded, failed health check(s): x.')"
    ))
    run(db.run(
        "INSERT INTO runs (kind, started_at, status, new_count, summary) VALUES "
        "('daily', '2026-10-05T08:00:00', 'completed', 2, 'Checked 5.')"
    ))

    recent = tc.get("/api/analytics").json()["recent_runs"]

    assert [r["status"] for r in recent] == ["completed", "degraded"]  # newest id first
    assert recent[1]["new_count"] == 0
