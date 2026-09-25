import asyncio

import pytest
from fastapi.testclient import TestClient

from github_client import GitHubClient
from src.main import app, central_today


def run(coro):
    return asyncio.run(coro)


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, method, url, *, token, json_body=None):
        self.calls.append((method, url, json_body))
        return self.responses.pop(0)


def add_company(db, name="Acme"):
    return run(db.run("INSERT INTO companies (name, status) VALUES (?, 'pinned')", name))


@pytest.fixture
def client(empty_db):
    app.state.db = empty_db
    app.state.access_team_domain = ""
    app.state.access_aud = ""
    app.state.github = None
    yield TestClient(app), empty_db
    app.state.db = None
    app.state.github = None


# ---------------------------------------------------------------------------
# Starting a search
# ---------------------------------------------------------------------------

def test_start_search_without_github_configured(client):
    tc, _ = client
    resp = tc.post("/api/search/runs", json={})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["result"] is None


def test_start_search_dispatches_daily_search_workflow(client):
    tc, _ = client
    transport = ScriptedTransport([(204, {})])
    app.state.github = GitHubClient(repo="HenryDMcGee/internship-watch", token="tok", transport=transport)

    resp = tc.post("/api/search/runs", json={})
    assert resp.status_code == 201
    method, url, body = transport.calls[0]
    assert method == "POST"
    assert url.endswith("/actions/workflows/daily-search.yml/dispatches")
    assert body["ref"] == "master"


def test_start_search_dispatch_failure_marks_request_failed(client):
    tc, db = client
    transport = ScriptedTransport([(500, {"message": "boom"})])
    app.state.github = GitHubClient(repo="HenryDMcGee/internship-watch", token="tok", transport=transport)

    resp = tc.post("/api/search/runs", json={})
    assert resp.status_code == 502

    rows = run(db.all("SELECT * FROM search_requests"))
    assert rows[0]["status"] == "failed"


# ---------------------------------------------------------------------------
# Never run two searches at once
# ---------------------------------------------------------------------------

def test_start_search_rejected_while_a_run_row_is_in_progress(client):
    tc, db = client
    run(db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-25T08:00:00', 'running')"))
    resp = tc.post("/api/search/runs", json={})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "A search is already running"


def test_start_search_rejected_while_check_now_is_pending(client):
    tc, db = client
    company_id = add_company(db)
    run(db.run(
        "INSERT INTO check_requests (company_ids, requested_at, status) VALUES (?, '2026-09-25T08:00:00', 'pending')",
        f"[{company_id}]",
    ))
    resp = tc.post("/api/search/runs", json={})
    assert resp.status_code == 409


def test_start_search_rejected_while_another_search_request_is_pending(client):
    tc, db = client
    run(db.run("INSERT INTO search_requests (requested_at, status) VALUES ('2026-09-25T08:00:00', 'pending')"))
    resp = tc.post("/api/search/runs", json={})
    assert resp.status_code == 409


def test_search_status_reports_busy(client):
    tc, db = client
    assert tc.get("/api/search/status").json() == {"busy": False, "reason": None}
    run(db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-09-25T08:00:00', 'running')"))
    body = tc.get("/api/search/status").json()
    assert body["busy"] is True
    assert body["reason"] == "A search is already running"


# ---------------------------------------------------------------------------
# Budget confirmation
# ---------------------------------------------------------------------------

def _set_budget(db, **overrides):
    fields = {
        "billing_mode": "subscription", "daily_token_budget": 4_000_000, "daily_search_budget": 35,
        "weekly_token_budget": 25_000_000, "daily_usd_budget": 1.50, "monthly_usd_budget": 30.00,
        "search_limit_per_run": 35, "search_limit_per_company": 5, "browser_checks_per_run": 50,
        "share_discovery": 0.60, "share_scout": 0.20, "share_classification": 0.15, "share_improvement": 0.05,
        "model_discovery": "sonnet", "model_scout": "sonnet", "model_classification": "haiku",
        "model_improvement": "sonnet",
    }
    fields.update(overrides)
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    run(db.run(f"UPDATE budget_settings SET {set_clause} WHERE id = 1", *fields.values()))


def test_start_search_asks_for_confirmation_when_budget_is_over_80_percent(client):
    tc, db = client
    _set_budget(db, daily_token_budget=1_000_000)
    today = central_today()
    run(db.run(
        "INSERT INTO usage (run_id, step, model, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_tokens, web_searches, est_cost_usd, at) "
        "VALUES (NULL, 'discovery', 'sonnet', 900000, 0, 0, 0, 0, 0, ?)",
        f"{today}T09:00:00",
    ))

    resp = tc.post("/api/search/runs", json={})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "budget_warning"
    assert "90%" in detail["message"]

    assert run(db.all("SELECT * FROM search_requests")) == []


def test_start_search_proceeds_when_budget_confirmed(client):
    tc, db = client
    _set_budget(db, daily_token_budget=1_000_000)
    today = central_today()
    run(db.run(
        "INSERT INTO usage (run_id, step, model, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_tokens, web_searches, est_cost_usd, at) "
        "VALUES (NULL, 'discovery', 'sonnet', 900000, 0, 0, 0, 0, 0, ?)",
        f"{today}T09:00:00",
    ))

    resp = tc.post("/api/search/runs", json={"confirm_budget": True})
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"


def test_start_search_not_blocked_after_a_daily_budget_reset(client, monkeypatch):
    tc, db = client
    _set_budget(db, daily_token_budget=1_000_000)
    today = central_today()
    run(db.run(
        "INSERT INTO usage (run_id, step, model, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_tokens, web_searches, est_cost_usd, at) "
        "VALUES (NULL, 'discovery', 'sonnet', 900000, 0, 0, 0, 0, 0, ?)",
        f"{today}T09:00:00-05:00",
    ))
    # `create_budget_reset` stamps `reset_at` with `now_iso()`; pinning it
    # after the usage row's timestamp keeps this deterministic regardless of
    # what time of day the suite happens to run.
    import src.main as main_module
    monkeypatch.setattr(main_module, "now_iso", lambda: f"{today}T10:00:00-05:00")

    reset_resp = tc.post("/api/budgets/resets", json={"scope": "daily"})
    assert reset_resp.status_code == 201

    resp = tc.post("/api/search/runs", json={})
    assert resp.status_code == 201


def test_start_search_does_not_ask_for_confirmation_under_80_percent(client):
    tc, db = client
    _set_budget(db, daily_token_budget=1_000_000)
    today = central_today()
    run(db.run(
        "INSERT INTO usage (run_id, step, model, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_tokens, web_searches, est_cost_usd, at) "
        "VALUES (NULL, 'discovery', 'sonnet', 100000, 0, 0, 0, 0, 0, ?)",
        f"{today}T09:00:00",
    ))

    resp = tc.post("/api/search/runs", json={})
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Status polling
# ---------------------------------------------------------------------------

def test_search_run_status_404_when_missing(client):
    tc, _ = client
    assert tc.get("/api/search/runs/999").status_code == 404


def test_search_run_status_reports_failed_result(client):
    tc, db = client
    request_id = run(db.run("INSERT INTO search_requests (requested_at, status) VALUES ('2026-09-25T08:00:00', 'pending')"))
    run(db.run(
        "UPDATE search_requests SET status = 'failed', result = ? WHERE id = ?",
        '{"error": "job/run.py crashed"}', request_id,
    ))
    resp = tc.get(f"/api/search/runs/{request_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["result"]["error"] == "job/run.py crashed"


def test_search_run_status_reports_completed_result(client):
    tc, db = client
    request_id = run(db.run("INSERT INTO search_requests (requested_at, status) VALUES ('2026-09-25T08:00:00', 'pending')"))
    run(db.run(
        "UPDATE search_requests SET status = 'completed', result = ? WHERE id = ?",
        '{"new_count": 3, "sources_found": 2}', request_id,
    ))
    resp = tc.get(f"/api/search/runs/{request_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["result"] == {"new_count": 3, "sources_found": 2}
