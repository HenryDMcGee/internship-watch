import asyncio

import pytest
from fastapi.testclient import TestClient

from src.github_client import GitHubClient
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


@pytest.fixture
def client(empty_db):
    app.state.db = empty_db
    app.state.access_team_domain = ""
    app.state.access_aud = ""
    app.state.github = None
    yield TestClient(app), empty_db
    app.state.db = None
    app.state.github = None


def add_company(db, name="Acme"):
    return run(db.run("INSERT INTO companies (name, status) VALUES (?, 'pinned')", name))


# ---------------------------------------------------------------------------
# Check now
# ---------------------------------------------------------------------------

def test_check_companies_requires_known_ids(client):
    tc, _ = client
    resp = tc.post("/api/companies/check", json={"company_ids": [999]})
    assert resp.status_code == 404


def test_check_companies_without_github_configured(client):
    tc, db = client
    company_id = add_company(db)
    resp = tc.post("/api/companies/check", json={"company_ids": [company_id]})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["company_ids"] == [company_id]


def test_check_companies_dispatches_workflow(client):
    tc, db = client
    company_id = add_company(db)
    transport = ScriptedTransport([(204, {})])
    app.state.github = GitHubClient(repo="HenryDMcGee/internship-watch", token="tok", transport=transport)

    resp = tc.post("/api/companies/check", json={"company_ids": [company_id]})
    assert resp.status_code == 201
    method, url, body = transport.calls[0]
    assert method == "POST"
    assert url.endswith("/actions/workflows/check-now.yml/dispatches")
    assert body["inputs"]["request_id"] == str(resp.json()["id"])


def test_check_companies_dispatch_failure_marks_request_failed(client):
    tc, db = client
    company_id = add_company(db)
    transport = ScriptedTransport([(500, {"message": "boom"})])
    app.state.github = GitHubClient(repo="HenryDMcGee/internship-watch", token="tok", transport=transport)

    resp = tc.post("/api/companies/check", json={"company_ids": [company_id]})
    assert resp.status_code == 502

    rows = run(db.all("SELECT * FROM check_requests"))
    assert rows[0]["status"] == "failed"


def test_check_companies_daily_limit(client):
    tc, db = client
    company_id = add_company(db)
    today = central_today()
    for _ in range(5):
        run(db.run(
            "INSERT INTO check_requests (company_ids, requested_at, status) VALUES ('[]', ?, 'completed')",
            f"{today}T09:00:00",
        ))
    resp = tc.post("/api/companies/check", json={"company_ids": [company_id]})
    assert resp.status_code == 429


def test_check_companies_status_endpoint(client):
    tc, db = client
    company_id = add_company(db)
    created = tc.post("/api/companies/check", json={"company_ids": [company_id]}).json()
    status = tc.get(f"/api/companies/check/{created['id']}")
    assert status.status_code == 200
    assert status.json()["id"] == created["id"]


def test_check_companies_status_404_when_missing(client):
    tc, _ = client
    assert tc.get("/api/companies/check/999").status_code == 404


# ---------------------------------------------------------------------------
# Confirm (Mark open / Mark closed)
# ---------------------------------------------------------------------------

def test_confirm_posting_open(client):
    tc, db = client
    posting_id = run(db.run(
        "INSERT INTO postings (company_name, title, url, locations, first_seen, status) VALUES ('Acme', 'PM Intern', 'https://x', '[]', '2026-09-20', 'unverified')"
    ))
    resp = tc.post(f"/api/postings/{posting_id}/confirm", json={"status": "open"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "open"
    assert body["user_status"] == "open"
    assert body["verified_via"] == "user"


def test_confirm_posting_rejects_bad_status(client):
    tc, db = client
    posting_id = run(db.run(
        "INSERT INTO postings (company_name, title, url, locations, first_seen, status) VALUES ('Acme', 'PM Intern', 'https://x', '[]', '2026-09-20', 'unverified')"
    ))
    resp = tc.post(f"/api/postings/{posting_id}/confirm", json={"status": "sideways"})
    assert resp.status_code == 422


def test_confirm_posting_404_when_missing(client):
    tc, _ = client
    assert tc.post("/api/postings/999/confirm", json={"status": "open"}).status_code == 404


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

VALID_DOC = {
    "version": 1,
    "exclude_words": ["marketing"],
    "titles": [{"title": "MBA Product Manager Intern", "group": "Core PM", "aliases": [], "active": True}],
}


def test_pending_titles_pr_none_without_github(client):
    tc, _ = client
    resp = tc.get("/api/titles/pending")
    assert resp.json() == {"pending": None}


def test_update_titles_requires_github_configured(client):
    tc, _ = client
    resp = tc.put("/api/titles", json={"doc": VALID_DOC})
    assert resp.status_code == 503


def test_update_titles_rejects_invalid_document(client):
    tc, db = client
    transport = ScriptedTransport([])
    app.state.github = GitHubClient(repo="HenryDMcGee/internship-watch", token="tok", transport=transport)
    bad_doc = dict(VALID_DOC, version=2)
    resp = tc.put("/api/titles", json={"doc": bad_doc})
    assert resp.status_code == 422
    assert transport.calls == []


def test_update_titles_opens_pull_request(client):
    tc, db = client
    transport = ScriptedTransport([
        (404, {}),
        (200, {"object": {"sha": "base-sha"}}),
        (201, {}),
        (404, {}),
        (201, {"content": {"sha": "new-sha"}}),
        (200, []),
        (201, {"number": 4, "html_url": "https://github.com/x/y/pull/4"}),
    ])
    app.state.github = GitHubClient(repo="HenryDMcGee/internship-watch", token="tok", transport=transport)

    resp = tc.put("/api/titles", json={"doc": VALID_DOC})
    assert resp.status_code == 200
    assert resp.json()["pending"]["number"] == 4
