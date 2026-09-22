import asyncio

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


def seed_posting(db, **overrides):
    row = {
        "company_name": "Amazon",
        "title": "PM Intern",
        "url": "https://amazon.jobs/x",
        "source_name": "Amazon.jobs",
        "locations": "[\"Seattle, WA\"]",
        "location_match": "match",
        "first_seen": central_today(),
        "status": "open",
        "applied": 0,
        "note": "",
    }
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join(["?"] * len(row))
    return run(db.run(f"INSERT INTO postings ({columns}) VALUES ({placeholders})", *row.values()))


def test_list_postings_today_tab(client):
    tc, db = client
    seed_posting(db, company_name="Amazon")
    seed_posting(db, company_name="NVIDIA", first_seen="2020-01-01")
    resp = tc.get("/api/postings", params={"tab": "today"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["postings"]) == 1
    assert body["postings"][0]["company_name"] == "Amazon"


def test_list_postings_earlier_tab(client):
    tc, db = client
    seed_posting(db, company_name="NVIDIA", first_seen="2020-01-01")
    resp = tc.get("/api/postings", params={"tab": "earlier"})
    assert resp.json()["postings"][0]["company_name"] == "NVIDIA"


def test_closed_postings_hidden_unless_show_closed(client):
    tc, db = client
    seed_posting(db, company_name="Cisco", status="closed")
    hidden = tc.get("/api/postings", params={"tab": "today"}).json()["postings"]
    shown = tc.get("/api/postings", params={"tab": "today", "show_closed": True}).json()["postings"]
    assert hidden == []
    assert len(shown) == 1


def test_filter_query_matches_company(client):
    tc, db = client
    seed_posting(db, company_name="Amazon")
    seed_posting(db, company_name="NVIDIA")
    resp = tc.get("/api/postings", params={"tab": "today", "q": "nvidia"})
    assert [p["company_name"] for p in resp.json()["postings"]] == ["NVIDIA"]


def test_mark_applied(client):
    tc, db = client
    posting_id = seed_posting(db)
    resp = tc.post(f"/api/postings/{posting_id}/applied", json={"applied": True})
    assert resp.status_code == 200
    assert resp.json()["applied"] is True


def test_delete_archives_and_hides_from_tabs(client):
    tc, db = client
    posting_id = seed_posting(db)
    resp = tc.delete(f"/api/postings/{posting_id}")
    assert resp.status_code == 200
    assert resp.json()["archived_at"] is not None
    assert tc.get("/api/postings", params={"tab": "today"}).json()["postings"] == []
    archived = tc.get("/api/postings", params={"tab": "archived"}).json()["postings"]
    assert len(archived) == 1


def test_restore_unarchives(client):
    tc, db = client
    posting_id = seed_posting(db)
    tc.delete(f"/api/postings/{posting_id}")
    resp = tc.post(f"/api/postings/{posting_id}/restore")
    assert resp.status_code == 200
    assert resp.json()["archived_at"] is None
    assert len(tc.get("/api/postings", params={"tab": "today"}).json()["postings"]) == 1


def test_delete_missing_posting_404s(client):
    tc, _ = client
    assert tc.delete("/api/postings/999").status_code == 404


def test_sources_list_add_remove(client):
    tc, _ = client
    assert tc.get("/api/sources").json()["sources"] == []
    created = tc.post("/api/sources", json={"name": "Built In", "url": "https://builtin.com"})
    assert created.status_code == 201
    source_id = created.json()["id"]
    assert len(tc.get("/api/sources").json()["sources"]) == 1
    assert tc.delete(f"/api/sources/{source_id}").status_code == 204
    assert tc.get("/api/sources").json()["sources"] == []


def test_add_company_detects_feed_and_rejects_duplicates(client):
    tc, _ = client
    resp = tc.post("/api/companies", json={"name": "Acme", "careers_url": "https://boards.greenhouse.io/acme"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["feed_type"] == "greenhouse"
    assert body["status"] == "pinned"

    dupe = tc.post("/api/companies", json={"name": "acme"})
    assert dupe.status_code == 409


def test_pin_and_unpin_company(client):
    tc, db = client
    company_id = run(db.run("INSERT INTO companies (name, status) VALUES ('Waymo', 'review')"))
    pinned = tc.post(f"/api/companies/{company_id}/pin")
    assert pinned.json()["status"] == "pinned"
    unpinned = tc.post(f"/api/companies/{company_id}/unpin")
    assert unpinned.json()["status"] == "removed"


def test_companies_paste_preview_and_save(client):
    tc, db = client
    run(db.run("INSERT INTO companies (name, status) VALUES ('Apple', 'ranked')"))

    preview = tc.post("/api/companies/paste/preview", json={"text": "Apple\nAcme, https://jobs.lever.co/acme\nAcme"})
    entries = preview.json()["entries"]
    assert [e["match"] for e in entries] == ["existing", "new", "duplicate"]

    save = tc.post(
        "/api/companies/paste/save",
        json={"entries": [{"name": "Apple"}, {"name": "Acme", "url": "https://jobs.lever.co/acme"}]},
    )
    saved = save.json()["saved"]
    assert len(saved) == 1
    assert saved[0]["name"] == "Acme"
    assert saved[0]["feed_type"] == "lever"

    companies = tc.get("/api/companies").json()["companies"]
    assert len(companies) == 2


def test_locations_settings_get_and_update(client):
    tc, _ = client
    default = tc.get("/api/settings/locations").json()
    assert default == {"countries": ["US", "JP"], "include_remote": False}

    updated = tc.put("/api/settings/locations", json={"countries": ["US", "JP", "CA"], "include_remote": True})
    assert updated.status_code == 200
    assert tc.get("/api/settings/locations").json() == {"countries": ["US", "JP", "CA"], "include_remote": True}


def test_access_required_when_configured(client):
    tc, _ = client
    app.state.access_team_domain = "royal-band-14be.cloudflareaccess.com"
    app.state.access_aud = "some-aud"
    try:
        resp = tc.get("/api/postings")
        assert resp.status_code == 401
    finally:
        app.state.access_team_domain = ""
        app.state.access_aud = ""
