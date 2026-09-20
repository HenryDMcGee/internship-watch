import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from src.main import app


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
        "billing_mode": "subscription",
        "daily_token_budget": 4_000_000, "daily_search_budget": 35, "weekly_token_budget": 25_000_000,
        "daily_usd_budget": 1.50, "monthly_usd_budget": 30.00,
        "search_limit_per_run": 35, "search_limit_per_company": 5, "browser_checks_per_run": 50,
        "share_discovery": 0.60, "share_scout": 0.20, "share_classification": 0.15, "share_improvement": 0.05,
        "model_discovery": "sonnet", "model_scout": "sonnet", "model_classification": "haiku",
        "model_improvement": "sonnet",
    }
    doc.update(overrides)
    return doc


# -- budgets ------------------------------------------------------------

def test_get_budgets_returns_settings_and_forecast(client):
    tc, db = client
    resp = tc.get("/api/budgets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"]["billing_mode"] == "subscription"
    assert "weekly_tokens" in body["forecast"]
    assert body["changes"] == []


def test_put_budgets_updates_settings_and_records_change(client):
    tc, db = client
    resp = tc.put("/api/budgets", json={"doc": _valid_budget_doc(daily_token_budget=2_000_000)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"]["daily_token_budget"] == 2_000_000
    assert any(c["field"] == "daily_token_budget" for c in body["changes"])


def test_put_budgets_rejects_shares_not_summing_to_100_percent(client):
    tc, db = client
    resp = tc.put("/api/budgets", json={"doc": _valid_budget_doc(share_discovery=0.99)})
    assert resp.status_code == 422


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
    ):
        assert key in body
    assert body["coverage"][0]["name"] == "Acme"
    assert body["coverage"][0]["posting_count"] == 1
    assert body["pruned"][0]["archive_reason"] == "No longer open."
    assert len(body["alerts"]) == 1


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
