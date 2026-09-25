import asyncio

import pytest
from fastapi.testclient import TestClient

import src.main as main_module
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


def _insert_usage(db, *, tokens: int, searches: int, at: str) -> None:
    run(db.run(
        """INSERT INTO usage (run_id, step, model, input_tokens, output_tokens, cache_read_tokens,
           cache_write_tokens, web_searches, est_cost_usd, at)
           VALUES (1, 'discovery', 'sonnet', ?, 0, 0, 0, ?, 0, ?)""",
        tokens, searches, at,
    ))


def _freeze_now(monkeypatch, at: str) -> None:
    # `create_budget_reset` stamps `reset_at` with `now_iso()`; pinning it
    # (rather than relying on the real wall clock) keeps the "usage before
    # vs. after the reset" tests deterministic regardless of what time of
    # day the suite happens to run.
    monkeypatch.setattr(main_module, "now_iso", lambda: at)


# -- GET /api/budgets shape -------------------------------------------------

def test_get_budgets_includes_usage_and_resets(client):
    tc, db = client
    resp = tc.get("/api/budgets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["usage"] == {"daily": {"tokens": 0, "searches": 0, "usd": 0}, "weekly": {"tokens": 0}}
    assert body["resets"] == {"daily": None, "weekly": None}


# -- POST /api/budgets/resets ------------------------------------------------

def test_create_reset_rejects_bad_scope(client):
    tc, db = client
    resp = tc.post("/api/budgets/resets", json={"scope": "monthly"})
    assert resp.status_code == 422


def test_daily_reset_excludes_usage_from_before_it(client, monkeypatch):
    tc, db = client
    today = central_today()
    _insert_usage(db, tokens=1000, searches=5, at=f"{today}T07:00:00-05:00")
    _freeze_now(monkeypatch, f"{today}T08:00:00-05:00")

    resp = tc.post("/api/budgets/resets", json={"scope": "daily"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["usage"]["daily"]["tokens"] == 0
    assert body["resets"]["daily"] is not None

    _insert_usage(db, tokens=150, searches=1, at=f"{today}T09:00:00-05:00")
    resp = tc.get("/api/budgets")
    assert resp.json()["usage"]["daily"]["tokens"] == 150
    assert resp.json()["usage"]["daily"]["searches"] == 1


def test_daily_reset_does_not_affect_weekly_usage(client, monkeypatch):
    tc, db = client
    today = central_today()
    _insert_usage(db, tokens=1000, searches=5, at=f"{today}T07:00:00-05:00")
    _freeze_now(monkeypatch, f"{today}T08:00:00-05:00")

    resp = tc.post("/api/budgets/resets", json={"scope": "daily"})
    assert resp.status_code == 201
    assert resp.json()["usage"]["weekly"]["tokens"] == 1000


def test_weekly_reset_excludes_usage_from_before_it(client, monkeypatch):
    tc, db = client
    today = central_today()
    _insert_usage(db, tokens=2000, searches=0, at=f"{today}T07:00:00-05:00")
    _freeze_now(monkeypatch, f"{today}T08:00:00-05:00")

    resp = tc.post("/api/budgets/resets", json={"scope": "weekly"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["usage"]["weekly"]["tokens"] == 0
    # A daily reset was never made, so today's usage still counts.
    assert body["usage"]["daily"]["tokens"] == 2000


def test_reset_is_recorded_in_change_history(client):
    tc, db = client
    resp = tc.post("/api/budgets/resets", json={"scope": "weekly", "note": "testing"})
    assert resp.status_code == 201
    changes = resp.json()["changes"]
    assert any(c["field"] == "weekly_reset" and c["new_value"] == "0" for c in changes)


def test_analytics_usage_by_step_is_unaffected_by_a_reset(client, monkeypatch):
    tc, db = client
    today = central_today()
    _insert_usage(db, tokens=1000, searches=5, at=f"{today}T07:00:00-05:00")
    _freeze_now(monkeypatch, f"{today}T08:00:00-05:00")

    tc.post("/api/budgets/resets", json={"scope": "daily"})

    resp = tc.get("/api/analytics")
    assert resp.status_code == 200
    usage_by_step = resp.json()["usage_by_step"]
    assert sum(u["tokens"] for u in usage_by_step) == 1000
