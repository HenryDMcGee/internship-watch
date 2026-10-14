"""Phase 7: the public job board, public and owner feedback, hostname routing,
and the daily summary's feedback line (issue #73)."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import job.run as run_module
import src.main as main_module
from github_client import GitHubApiError  # the module name main.py imports it under
from src.main import app, central_today, route_for_host, rewrite_public_path

PUBLIC = "jobs.mcgeehenry.com"
PRIVATE = "internship-watch.mcgeehenry.com"
SALT = "test-salt"
PUBLIC_KEYS = {"id", "company_name", "title", "title_en", "locations", "posted_date", "first_seen", "url"}


def run(coro):
    return asyncio.run(coro)


class FakeGitHub:
    def __init__(self):
        self.calls = []

    async def create_issue(self, title, body_text, labels):
        self.calls.append({"title": title, "body": body_text, "labels": labels})
        return {"html_url": f"https://github.com/HenryDMcGee/internship-watch/issues/{len(self.calls)}"}


@pytest.fixture
def client(empty_db):
    app.state.db = empty_db
    app.state.access_team_domain = ""
    app.state.access_aud = ""
    app.state.github = None
    app.state.feedback_salt = SALT
    yield TestClient(app), empty_db
    app.state.db = None
    app.state.github = None
    app.state.feedback_salt = ""


def seed_posting(db, **overrides):
    row = {
        "company_name": "Amazon", "title": "PM Intern", "url": "https://amazon.jobs/x", "source_name": "Amazon.jobs",
        "locations": '["Seattle, WA"]', "location_match": "match", "first_seen": central_today(), "status": "open",
    }
    row.update(overrides)
    columns = ", ".join(row)
    return run(db.run(f"INSERT INTO postings ({columns}) VALUES ({', '.join('?' for _ in row)})", *row.values()))


def enable_public(db, value="true"):
    run(db.run("INSERT OR REPLACE INTO settings (key, value) VALUES ('public_site_enabled', ?)", value))


def feedback_rows(db):
    return run(db.all("SELECT * FROM feedback ORDER BY id"))


def post_feedback(tc, ip="1.2.3.4", **overrides):
    body = {"kind": "dead_link", "message": "The link is dead"}
    body.update(overrides)
    return tc.post("/api/public/feedback", json=body, headers={"CF-Connecting-IP": ip})


# -- public postings -----------------------------------------------------------------

def test_public_payload_has_exactly_the_public_keys(client):
    tc, db = client
    enable_public(db)
    seed_posting(db)
    body = tc.get("/api/public/postings").json()
    assert body["enabled"] is True
    assert len(body["postings"]) == 1
    assert set(body["postings"][0]) == PUBLIC_KEYS
    assert body["postings"][0]["locations"] == ["Seattle, WA"]


def test_private_values_never_appear_in_the_public_response(client):
    tc, db = client
    enable_public(db)
    seed_posting(db, applied=1, note="secret-note", status_note="secret-status-note", user_status="open",
                 user_status_at="2026-10-01", verified_via="secret-via", mirror_url="https://mirror.example/secret",
                 matched_title="secret-matched", archive_reason=None)
    resp = tc.get("/api/public/postings")
    assert len(resp.json()["postings"]) == 1
    for value in ("secret-note", "secret-status-note", "secret-via", "mirror.example", "secret-matched", "Amazon.jobs"):
        assert value not in resp.text


def test_public_list_filters(client):
    tc, db = client
    enable_public(db)
    seed_posting(db, title="visible open")
    seed_posting(db, title="visible closing", status="closing")
    seed_posting(db, title="archived", archived_at="2026-10-01")
    seed_posting(db, title="closed", status="closed")
    seed_posting(db, title="unverified", status="unverified")
    seed_posting(db, title="outside", location_match="outside")
    seed_posting(db, title="unclear", location_match="unclear")
    seed_posting(db, title="hidden", public_hidden=1)
    titles = {p["title"] for p in tc.get("/api/public/postings").json()["postings"]}
    assert titles == {"visible open", "visible closing"}


def test_public_list_is_newest_first(client):
    tc, db = client
    enable_public(db)
    seed_posting(db, title="old", first_seen="2026-09-01")
    seed_posting(db, title="new", first_seen="2026-10-01")
    assert [p["title"] for p in tc.get("/api/public/postings").json()["postings"]] == ["new", "old"]


def test_site_off_returns_nothing(client):
    tc, db = client
    seed_posting(db)
    off = {"enabled": False, "postings": [], "last_run": None}
    assert tc.get("/api/public/postings").json() == off  # setting missing
    enable_public(db, "false")
    assert tc.get("/api/public/postings").json() == off
    enable_public(db, "true")
    assert len(tc.get("/api/public/postings").json()["postings"]) == 1


def test_public_postings_are_cacheable_for_five_minutes(client):
    tc, db = client
    assert tc.get("/api/public/postings").headers["cache-control"] == "public, max-age=300"
    enable_public(db)
    assert tc.get("/api/public/postings").headers["cache-control"] == "public, max-age=300"


def test_last_run_is_the_latest_finished_daily_run(client):
    tc, db = client
    enable_public(db)
    assert tc.get("/api/public/postings").json()["last_run"] is None
    run(db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-10-13T08:00:00-05:00', 'running')"))
    assert tc.get("/api/public/postings").json()["last_run"] is None
    run(db.run("INSERT INTO runs (kind, started_at, finished_at, status) VALUES ('daily', 'a', '2026-10-12T08:20:00-05:00', 'completed')"))
    run(db.run("INSERT INTO runs (kind, started_at, finished_at, status) VALUES ('daily', 'b', '2026-10-13T08:20:00-05:00', 'completed')"))
    run(db.run("INSERT INTO runs (kind, started_at, finished_at, status) VALUES ('check_now', 'c', '2026-10-14T09:00:00-05:00', 'completed')"))
    assert tc.get("/api/public/postings").json()["last_run"] == "2026-10-13T08:20:00-05:00"


# -- hostname routing ----------------------------------------------------------------

def test_public_hostname_routes():
    assert route_for_host(PUBLIC, "/", PUBLIC) == "public"
    assert rewrite_public_path("/") == "/public/index.html"
    assert rewrite_public_path("/index.html") == "/public/index.html"
    assert rewrite_public_path("/public/public.js") == "/public/public.js"
    for path in ("/", "/index.html", "/public/public.js", "/style.css", "/api/public/postings", "/api/public/feedback"):
        assert route_for_host(PUBLIC, path, PUBLIC) == "public", path
    for path in ("/api/postings", "/api/budgets", "/api/settings/locations", "/api/feedback", "/app.js", "/api", "/api/",
                 "/public", "/api/publicx", "//api/postings", "/anything"):
        assert route_for_host(PUBLIC, path, PUBLIC) == "not_found", path


def test_private_hostname_is_unchanged():
    for path in ("/api/postings", "/", "/app.js", "/style.css", "/anything"):
        assert route_for_host(PRIVATE, path, PUBLIC) == "private"
    assert route_for_host("", "/api/postings", PUBLIC) == "private"
    assert route_for_host(PUBLIC, "/api/postings", "") == "private"  # no public hostname configured


def test_hostname_comparison_ignores_case_and_port():
    assert route_for_host("JOBS.McGeeHenry.com", "/api/postings", PUBLIC) == "not_found"
    assert route_for_host("jobs.mcgeehenry.com:8787", "/api/postings", PUBLIC) == "not_found"
    assert route_for_host("jobs.mcgeehenry.com:8787", "/", "Jobs.McGeeHenry.com") == "public"


def test_wrangler_keeps_its_settings_and_adds_the_public_route():
    from conftest import ROOT
    text = (ROOT / "api" / "wrangler.toml").read_text(encoding="utf-8")
    assert "workers_dev = false" in text
    assert "preview_urls = false" in text
    assert 'pattern = "internship-watch.mcgeehenry.com/*"' in text
    assert 'pattern = "jobs.mcgeehenry.com/*", zone_name = "mcgeehenry.com"' in text
    assert 'PUBLIC_HOSTNAME = "jobs.mcgeehenry.com"' in text
    assert 'binding = "ASSETS"' in text and 'binding = "DB"' in text
    assert not [line for line in text.splitlines() if line.strip().startswith("FEEDBACK_SALT")]  # the secret never goes in the file


# -- private endpoints stay private --------------------------------------------------

def test_private_endpoints_need_access_but_public_ones_do_not(client):
    tc, db = client
    posting_id = seed_posting(db)
    enable_public(db)
    app.state.access_team_domain = "royal-band-14be.cloudflareaccess.com"
    app.state.access_aud = "some-aud"
    try:
        assert tc.get("/api/feedback").status_code == 401
        assert tc.put("/api/settings/public-site", json={"enabled": True}).status_code == 401
        assert tc.get("/api/settings/public-site").status_code == 401
        assert tc.post(f"/api/postings/{posting_id}/public-hidden", json={"hidden": True}).status_code == 401
        assert tc.post("/api/feedback/1/status", json={"status": "read"}).status_code == 401
        assert tc.post("/api/feedback/1/issue").status_code == 401
        assert tc.get("/api/public/postings").status_code == 200
        assert post_feedback(tc).status_code == 204
    finally:
        app.state.access_team_domain = ""
        app.state.access_aud = ""


# -- public feedback -----------------------------------------------------------------

def test_valid_public_feedback_is_stored_with_a_hashed_visitor(client):
    tc, db = client
    posting_id = seed_posting(db)
    resp = post_feedback(tc, ip="203.0.113.9", posting_id=posting_id, page_url="https://jobs.mcgeehenry.com/")
    assert resp.status_code == 204
    (row,) = feedback_rows(db)
    assert row["source"] == "public"
    assert row["kind"] == "dead_link"
    assert row["message"] == "The link is dead"
    assert row["posting_id"] == posting_id
    assert row["status"] == "new"
    assert row["visitor_hash"] and "203.0.113.9" not in row["visitor_hash"]
    assert row["visitor_hash"] == main_module.visitor_hash(SALT, "203.0.113.9")
    assert "203.0.113.9" not in json.dumps(row)


def test_filled_honeypot_is_discarded_silently(client):
    tc, db = client
    resp = post_feedback(tc, website="http://spam.example")
    assert resp.status_code == 204
    assert feedback_rows(db) == []


def test_daily_limits_per_visitor_and_in_total(client, monkeypatch):
    tc, db = client
    for _ in range(10):
        assert post_feedback(tc, ip="9.9.9.9").status_code == 204
    assert post_feedback(tc, ip="9.9.9.9").status_code == 429
    assert len(feedback_rows(db)) == 10
    assert post_feedback(tc, ip="8.8.8.8").status_code == 204  # another visitor is unaffected

    # Counts reset on the next Central day.
    monkeypatch.setattr(main_module, "central_today", lambda: "2999-01-01")
    assert post_feedback(tc, ip="9.9.9.9").status_code == 204
    monkeypatch.undo()

    # 200 a day in total, across visitors.
    run(db.run("DELETE FROM feedback"))
    today = central_today()
    for i in range(199):
        run(db.run(
            "INSERT INTO feedback (source, kind, message, visitor_hash, created_at) VALUES ('public', 'other', 'x', ?, ?)",
            f"v{i}", today + "T08:00:00-05:00",
        ))
    assert post_feedback(tc, ip="7.7.7.7").status_code == 204  # the 200th
    assert post_feedback(tc, ip="6.6.6.6").status_code == 429  # the 201st


@pytest.mark.parametrize("overrides", [
    {"message": ""},
    {"message": "   "},
    {"message": "x" * 2001},
    {"kind": "nonsense"},
    {"kind": "bug"},  # visitors can't file a Bug
    {"posting_id": 9999},
])
def test_invalid_public_feedback_is_rejected(client, overrides):
    tc, db = client
    assert post_feedback(tc, **overrides).status_code == 400
    assert feedback_rows(db) == []


def test_message_of_exactly_2000_characters_is_accepted(client):
    tc, db = client
    assert post_feedback(tc, message="x" * 2000).status_code == 204


def test_feedback_about_a_posting_the_public_cannot_see_is_rejected(client):
    tc, db = client
    for overrides in ({"archived_at": "2026-10-01"}, {"status": "closed"}, {"location_match": "outside"}, {"public_hidden": 1}):
        pid = seed_posting(db, **overrides)
        assert post_feedback(tc, posting_id=pid).status_code == 400
    assert feedback_rows(db) == []


def test_script_tags_are_stored_as_literal_text(client):
    tc, db = client
    text = "<script>alert(1)</script> & <b>bold</b>"
    assert post_feedback(tc, message=text).status_code == 204
    assert feedback_rows(db)[0]["message"] == text
    assert tc.get("/api/feedback").json()["feedback"][0]["message"] == text


def test_missing_salt_returns_503_and_stores_nothing(client):
    tc, db = client
    app.state.feedback_salt = ""
    assert post_feedback(tc).status_code == 503
    assert feedback_rows(db) == []


# -- owner feedback and the Feedback view ---------------------------------------------

def test_private_api_has_no_owner_feedback_submission(client):
    tc, db = client
    assert tc.post("/api/feedback", json={"kind": "bug", "message": "x"}).status_code == 405
    assert feedback_rows(db) == []


def test_feedback_list_is_newest_first_with_the_posting_joined(client):
    tc, db = client
    pid = seed_posting(db, company_name="Stripe", title="Product Intern")
    run(db.run("INSERT INTO feedback (source, kind, message, created_at, visitor_hash) VALUES ('public', 'other', 'older', '2026-10-12T09:00:00-05:00', 'secret-hash')"))
    run(db.run("INSERT INTO feedback (source, kind, posting_id, message, created_at) VALUES ('public', 'dead_link', ?, 'newer', '2026-10-13T09:00:00-05:00')", pid))
    rows = tc.get("/api/feedback").json()["feedback"]
    assert [r["message"] for r in rows] == ["newer", "older"]
    assert rows[0]["posting_company"] == "Stripe" and rows[0]["posting_title"] == "Product Intern"
    assert rows[1]["posting_company"] is None
    assert "visitor_hash" not in rows[0] and "secret-hash" not in json.dumps(rows)


def test_mark_read_and_dismiss_update_status_and_decided_at(client):
    tc, db = client
    fid = run(db.run("INSERT INTO feedback (source, kind, message, created_at) VALUES ('public', 'other', 'hi', '2026-10-13T09:00:00-05:00')"))
    assert tc.post(f"/api/feedback/{fid}/status", json={"status": "read"}).json()["status"] == "read"
    row = feedback_rows(db)[0]
    assert row["status"] == "read" and row["decided_at"]
    assert tc.post(f"/api/feedback/{fid}/status", json={"status": "dismissed"}).json()["status"] == "dismissed"
    assert tc.post(f"/api/feedback/{fid}/status", json={"status": "issue_opened"}).status_code == 422
    assert tc.post("/api/feedback/999/status", json={"status": "read"}).status_code == 404


def test_open_as_github_issue_creates_exactly_one_issue(client):
    tc, db = client
    github = FakeGitHub()
    app.state.github = github
    pid = seed_posting(db, company_name="Stripe", title="Product Intern", url="https://stripe.com/jobs/1")
    message = "The apply button goes nowhere. " + "x" * 100
    fid = run(db.run(
        "INSERT INTO feedback (source, kind, posting_id, message, created_at) VALUES ('public', 'dead_link', ?, ?, '2026-10-13T09:00:00-05:00')",
        pid, message,
    ))
    resp = tc.post(f"/api/feedback/{fid}/issue")
    assert resp.status_code == 200
    assert len(github.calls) == 1
    call = github.calls[0]
    assert call["title"] == "Feedback: Dead link: " + message[:60]
    assert call["labels"] == ["feedback"]
    assert message in call["body"] and "https://stripe.com/jobs/1" in call["body"]
    assert "Visitor" in call["body"] and "2026-10-13" in call["body"]
    row = feedback_rows(db)[0]
    assert row["status"] == "issue_opened"
    assert row["issue_url"] == "https://github.com/HenryDMcGee/internship-watch/issues/1"
    assert resp.json()["issue_url"] == row["issue_url"]

    assert tc.post(f"/api/feedback/{fid}/issue").status_code == 200  # again: no second issue
    assert len(github.calls) == 1


def test_github_failure_leaves_the_row_alone(client):
    tc, db = client

    class Failing:
        async def create_issue(self, *args, **kwargs):
            raise GitHubApiError("nope")

    app.state.github = Failing()
    fid = run(db.run("INSERT INTO feedback (source, kind, message, created_at) VALUES ('owner', 'bug', 'x', '2026-10-13T09:00:00-05:00')"))
    assert tc.post(f"/api/feedback/{fid}/issue").status_code == 502
    assert feedback_rows(db)[0]["status"] == "new"


def test_open_issue_without_github_returns_503_and_changes_nothing(client):
    tc, db = client
    fid = run(db.run("INSERT INTO feedback (source, kind, message, created_at) VALUES ('owner', 'bug', 'x', '2026-10-13T09:00:00-05:00')"))
    assert tc.post(f"/api/feedback/{fid}/issue").status_code == 503
    row = feedback_rows(db)[0]
    assert row["status"] == "new" and row["issue_url"] is None


# -- dashboard data -------------------------------------------------------------------

def test_private_list_has_public_hidden_and_visitor_reports(client):
    tc, db = client
    pid = seed_posting(db)
    other = seed_posting(db, title="Other")
    for kind, status in (("dead_link", "new"), ("dead_link", "read"), ("closed", "dismissed"), ("suggestion", "new")):
        run(db.run(
            "INSERT INTO feedback (source, kind, posting_id, message, status, created_at) VALUES ('public', ?, ?, 'm', ?, '2026-10-13T09:00:00-05:00')",
            kind, pid, status,
        ))
    postings = {p["id"]: p for p in tc.get("/api/postings?tab=today").json()["postings"]}
    assert postings[pid]["visitor_reports"] == 2
    assert postings[pid]["public_hidden"] is False
    assert postings[other]["visitor_reports"] == 0


def test_a_visitor_report_never_changes_the_posting(client):
    tc, db = client
    pid = seed_posting(db)
    before = run(db.first("SELECT * FROM postings WHERE id = ?", pid))
    assert post_feedback(tc, kind="closed", posting_id=pid).status_code == 204
    assert post_feedback(tc, kind="dead_link", posting_id=pid).status_code == 204
    after = run(db.first("SELECT * FROM postings WHERE id = ?", pid))
    assert after == before
    assert after["status"] == "open" and after["user_status"] is None and after["archived_at"] is None


def test_hide_from_public_removes_the_posting_from_the_public_list(client):
    tc, db = client
    enable_public(db)
    pid = seed_posting(db)
    assert len(tc.get("/api/public/postings").json()["postings"]) == 1
    resp = tc.post(f"/api/postings/{pid}/public-hidden", json={"hidden": True})
    assert resp.status_code == 200 and resp.json()["public_hidden"] is True
    assert tc.get("/api/public/postings").json()["postings"] == []
    assert tc.post(f"/api/postings/{pid}/public-hidden", json={"hidden": False}).json()["public_hidden"] is False
    assert len(tc.get("/api/public/postings").json()["postings"]) == 1
    assert tc.post("/api/postings/999/public-hidden", json={"hidden": True}).status_code == 404


def test_public_site_setting_defaults_off_and_round_trips(client):
    tc, db = client
    assert tc.get("/api/settings/public-site").json() == {"enabled": False}
    assert tc.put("/api/settings/public-site", json={"enabled": True}).json() == {"enabled": True}
    assert tc.get("/api/settings/public-site").json() == {"enabled": True}
    assert run(db.first("SELECT value FROM settings WHERE key = 'public_site_enabled'"))["value"] == "true"
    assert tc.put("/api/settings/public-site", json={"enabled": False}).json() == {"enabled": False}
    assert tc.get("/api/settings/public-site").json() == {"enabled": False}


# -- daily summary ---------------------------------------------------------------------

def _add_feedback(db, kind, created_at, message="PRIVATE MESSAGE TEXT"):
    run(db.run("INSERT INTO feedback (source, kind, message, created_at) VALUES ('public', ?, ?, ?)", kind, message, created_at))


def test_summary_line_counts_new_feedback_without_message_text(empty_db):
    run(empty_db.run("INSERT INTO runs (kind, started_at, finished_at, status) VALUES ('daily', '2026-10-12T08:00:00-05:00', '2026-10-12T08:20:00-05:00', 'completed')"))
    run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-10-13T08:00:00-05:00', 'running')"))
    _add_feedback(empty_db, "other", "2026-10-11T12:00:00-05:00")  # before the previous run: not new
    _add_feedback(empty_db, "dead_link", "2026-10-12T12:00:00-05:00")
    _add_feedback(empty_db, "suggestion", "2026-10-12T13:00:00-05:00")
    _add_feedback(empty_db, "closed", "2026-10-13T07:00:00-05:00")
    line = run(run_module.feedback_summary_line(empty_db))
    assert line == "3 new feedback items, 1 about a dead link."
    assert "PRIVATE" not in line


def test_summary_line_is_absent_without_new_feedback(empty_db):
    run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-10-13T08:00:00-05:00', 'running')"))
    assert run(run_module.feedback_summary_line(empty_db)) is None
    _add_feedback(empty_db, "other", "2026-10-12T12:00:00-05:00")
    run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-10-14T08:00:00-05:00', 'running')"))
    assert run(run_module.feedback_summary_line(empty_db)) is None


def test_summary_line_singular(empty_db):
    run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-10-13T08:00:00-05:00', 'running')"))
    _add_feedback(empty_db, "other", "2026-10-12T12:00:00-05:00")
    assert run(run_module.feedback_summary_line(empty_db)) == "1 new feedback item."


def test_summary_payload_includes_the_feedback_line_only_when_there_is_one():
    from job.agent.summary import build_summary_payload
    kwargs = dict(
        date="2026-10-14", run_status="completed", run_reason=None, tokens_today=0, searches_today=0, budgets={},
        tokens_week_to_date=0, usage_by_step=[], forecast={}, alerts=[],
    )
    assert "feedback_line" not in build_summary_payload(**kwargs)
    payload = build_summary_payload(**kwargs, feedback_line="3 new feedback items, 1 about a dead link.")
    assert payload["feedback_line"] == "3 new feedback items, 1 about a dead link."


def test_daily_finish_sends_the_feedback_line(empty_db, monkeypatch):
    run(empty_db.run("INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-10-13T08:00:00-05:00', 'running')"))
    _add_feedback(empty_db, "dead_link", "2026-10-12T12:00:00-05:00")
    _add_feedback(empty_db, "other", "2026-10-12T13:00:00-05:00")
    _add_feedback(empty_db, "other", "2026-10-12T14:00:00-05:00")
    sent = []
    monkeypatch.setattr(run_module, "send_daily_summary", sent.append)
    run(run_module._finish_daily_run(empty_db, run_status="completed", run_reason=None))
    assert sent[0]["feedback_line"] == "3 new feedback items, 1 about a dead link."
    assert "PRIVATE" not in json.dumps(sent[0])
