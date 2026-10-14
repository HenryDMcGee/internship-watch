"""Issue #89: the public feedback form returned a bare 503 with nothing logged.

Pins down: every failure and rejection is logged with its cause and status, the visitor
sees a message that matches the real failure, and a missing table or column is caught by
a schema check instead of surfacing as a silent error.
"""
import asyncio
import logging
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.main as main_module
from github_client import GitHubApiError
from schema_check import REQUIRED_SCHEMA, check_schema
from src.db import SqliteDatabase
from src.main import app
from tests.conftest import SCHEMA_SQL

SALT = "test-salt"
SERVER_MESSAGE = "Something went wrong on our side. Please try again later."


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(empty_db):
    app.state.db = empty_db
    app.state.access_team_domain = ""
    app.state.access_aud = ""
    app.state.github = None
    app.state.feedback_salt = SALT
    app.state.schema_problems = []
    yield TestClient(app, raise_server_exceptions=False), empty_db
    app.state.db = None
    app.state.github = None
    app.state.feedback_salt = ""
    app.state.schema_problems = []


def post_feedback(tc, **overrides):
    body = {"kind": "dead_link", "message": "The link is dead"}
    body.update(overrides)
    return tc.post("/api/public/feedback", json=body, headers={"CF-Connecting-IP": "1.2.3.4"})


def feedback_count(db):
    return run(db.first("SELECT COUNT(*) AS n FROM feedback"))["n"]


def db_without_column(table, column):
    """The real schema, but with one column missing: what an unapplied migration looks like."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(SCHEMA_SQL)
    conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.commit()
    return SqliteDatabase(conn)


# -- the feedback endpoint ------------------------------------------------------------

def test_valid_submission_is_saved(client, caplog):
    tc, db = client
    with caplog.at_level(logging.WARNING):
        assert post_feedback(tc).status_code == 204
    assert feedback_count(db) == 1
    assert not caplog.records


def test_database_error_is_logged_with_its_cause_and_is_a_server_failure(client, caplog):
    tc, db = client
    run(db.run("DROP TABLE feedback"))
    with caplog.at_level(logging.WARNING):
        resp = post_feedback(tc)
    assert resp.status_code == 500
    assert resp.json()["detail"] == SERVER_MESSAGE
    text = caplog.text
    assert "OperationalError" in text and "no such table: feedback" in text and "500" in text


def test_missing_column_is_a_logged_error_not_a_silent_503(empty_db, caplog):
    db = db_without_column("feedback", "visitor_hash")
    app.state.db, app.state.feedback_salt, app.state.schema_problems = db, SALT, []
    try:
        with caplog.at_level(logging.WARNING):
            resp = post_feedback(TestClient(app, raise_server_exceptions=False))
        assert resp.status_code == 500
        assert resp.json()["detail"] == SERVER_MESSAGE
        assert "visitor_hash" in caplog.text
        assert any("feedback" in p and "visitor_hash" in p for p in run(check_schema(db)))
    finally:
        app.state.db, app.state.feedback_salt = None, ""


def test_missing_salt_is_logged_with_reason_and_status(client, caplog):
    tc, db = client
    app.state.feedback_salt = ""
    with caplog.at_level(logging.WARNING):
        resp = post_feedback(tc)
    assert resp.status_code == 503
    assert "FEEDBACK_SALT" in caplog.text and "503" in caplog.text
    assert feedback_count(db) == 0


@pytest.mark.parametrize("overrides,status,reason", [
    ({"kind": "nope"}, 400, "Unknown feedback type"),
    ({"message": "   "}, 400, "Message must be 1 to"),
    ({"posting_id": 999}, 400, "Unknown posting"),
])
def test_validation_rejections_are_logged_and_keep_their_message(client, caplog, overrides, status, reason):
    tc, db = client
    with caplog.at_level(logging.WARNING):
        resp = post_feedback(tc, **overrides)
    assert resp.status_code == status
    assert reason in resp.json()["detail"]
    assert reason in caplog.text and str(status) in caplog.text
    assert feedback_count(db) == 0


def test_rate_limit_rejection_is_logged(client, caplog):
    tc, db = client
    for _ in range(main_module.PUBLIC_FEEDBACK_PER_VISITOR_PER_DAY):
        assert post_feedback(tc).status_code == 204
    with caplog.at_level(logging.WARNING):
        assert post_feedback(tc).status_code == 429
    assert "429" in caplog.text


# -- other endpoints ------------------------------------------------------------------

class FailingGitHub:
    async def dispatch_workflow(self, *a, **k):
        raise GitHubApiError("dispatch refused")

    async def find_open_pr(self, *a, **k):
        raise GitHubApiError("pr lookup refused")

    async def create_issue(self, *a, **k):
        raise GitHubApiError("issue refused")


def test_github_failures_are_logged_with_cause_and_status(client, caplog):
    tc, db = client
    app.state.github = FailingGitHub()
    run(db.run("INSERT INTO feedback (source, kind, message, created_at) VALUES ('public', 'other', 'hi', '2026-10-14T09:00:00-05:00')"))
    cid = run(db.run("INSERT INTO companies (name, status) VALUES ('Acme', 'pinned')"))
    with caplog.at_level(logging.WARNING):
        assert tc.post("/api/companies/check", json={"company_ids": [cid]}).status_code == 502
        assert tc.post("/api/search", json={}).status_code == 502
        assert tc.post("/api/feedback/1/issue").status_code == 502
        assert tc.get("/api/titles/pending").status_code == 502
        assert tc.get("/api/titles").status_code == 502
    for cause in ("dispatch refused", "issue refused", "pr lookup refused"):
        assert cause in caplog.text
    assert caplog.text.count("GitHubApiError") >= 5


def test_access_jwks_failure_is_logged(client, caplog, monkeypatch):
    tc, _ = client
    app.state.access_team_domain, app.state.access_aud = "team.example", "aud"
    main_module._jwks_cache.clear()

    async def boom(domain):
        raise RuntimeError("jwks down")

    monkeypatch.setattr(main_module, "fetch_jwks", boom)
    with caplog.at_level(logging.WARNING):
        resp = tc.get("/api/postings", headers={main_module.ACCESS_HEADER: "tok"})
    assert resp.status_code == 503
    assert "RuntimeError" in caplog.text and "jwks down" in caplog.text


def test_unhandled_error_is_logged_and_returns_a_server_message(client, caplog, monkeypatch):
    tc, _ = client

    async def boom(*a, **k):
        raise ValueError("kaboom")

    monkeypatch.setattr(main_module, "_budgets_payload", boom)
    with caplog.at_level(logging.ERROR):
        resp = tc.get("/api/budgets")
    assert resp.status_code == 500
    assert "ValueError" in caplog.text and "kaboom" in caplog.text


def test_no_swallowed_exceptions_left_in_api_source():
    """Every `except ... as exc` in api/src that raises an HTTPException must log first."""
    source = (Path(main_module.__file__)).read_text(encoding="utf-8")
    blocks = re.findall(r"except [^\n]+ as exc:[^\n]*\n((?:[ \t]+[^\n]*\n|\n)+)", source)
    offenders = [b for b in blocks if "HTTPException" in b and "_log" not in b]
    assert offenders == []


# -- the schema check -----------------------------------------------------------------

def test_schema_check_passes_on_the_migrated_schema(empty_db):
    assert run(check_schema(empty_db)) == []


def test_schema_check_names_a_missing_table(empty_db):
    run(empty_db.run("DROP TABLE feedback"))
    assert any("feedback" in p and "table" in p for p in run(check_schema(empty_db)))


def test_schema_check_names_a_missing_column():
    problems = run(check_schema(db_without_column("postings", "public_hidden")))
    assert problems and "postings" in problems[0] and "public_hidden" in problems[0]


def test_required_schema_covers_every_table_the_api_writes_to():
    source = Path(main_module.__file__).read_text(encoding="utf-8")
    written = set(re.findall(r"(?:INSERT (?:OR \w+ )?INTO|UPDATE|DELETE FROM)\s+(\w+)", source))
    written.discard("SET")
    assert written <= set(REQUIRED_SCHEMA), written - set(REQUIRED_SCHEMA)
    assert set(main_module._BUDGET_FIELDS) <= set(REQUIRED_SCHEMA["budget_settings"])


def test_failed_schema_check_stops_api_requests_loudly(client, caplog):
    tc, db = client
    run(db.run("DROP TABLE feedback"))
    app.state.schema_checked = False
    with caplog.at_level(logging.ERROR):
        run(main_module.ensure_schema_checked(app, db))
    assert app.state.schema_problems
    assert "feedback" in caplog.text
    resp = tc.get("/api/feedback")
    assert resp.status_code == 503
    assert "feedback" in resp.json()["detail"]
    app.state.schema_checked = False
