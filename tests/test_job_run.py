import asyncio

import pytest

import job.run as run_module
from job.fetchers import FetchResult
from job.models import FetchedPosting


def run(coro):
    return asyncio.run(coro)


def _insert_company(db, **overrides):
    row = {"name": "Acme", "status": "pinned", "feed_type": "greenhouse", "feed_id": "acme"}
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    return run(db.run(f"INSERT INTO companies ({columns}) VALUES ({placeholders})", *row.values()))


def test_run_stores_matching_posting_and_verifies_it_open(empty_db, monkeypatch):
    _insert_company(empty_db)

    def fake_run_fetcher(company, session=None):
        return FetchResult(
            company_id=company["id"], company_name=company["name"],
            postings=[FetchedPosting(
                company_name="Acme", title="MBA Product Manager Intern, Summer 2027",
                url="https://boards.greenhouse.io/acme/jobs/1", location_text="San Francisco, CA",
                posted_date="2026-09-01",
            )],
        )

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)

    result = run(run_module.run(empty_db, kind="daily"))
    assert result["new_count"] == 1
    assert result["companies_checked"] == 1
    assert result["failed_feeds"] == 0

    postings = run(empty_db.all("SELECT * FROM postings"))
    assert len(postings) == 1
    posting = postings[0]
    assert posting["status"] == "open"
    assert posting["verified_via"] == "feed"
    assert posting["matched_title"] == "MBA Product Manager Intern"
    assert posting["location_match"] == "match"
    assert posting["archived_at"] is None

    runs = run(empty_db.all("SELECT * FROM runs"))
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["new_count"] == 1

    stats = run(empty_db.all("SELECT * FROM run_stats"))
    assert len(stats) == 1
    assert stats[0]["found"] == 1
    assert stats[0]["useful"] == 1


def test_run_does_not_store_non_matching_postings(empty_db, monkeypatch):
    _insert_company(empty_db)

    def fake_run_fetcher(company, session=None):
        return FetchResult(
            company_id=company["id"], company_name=company["name"],
            postings=[FetchedPosting(
                company_name="Acme", title="Software Engineer Intern",
                url="https://boards.greenhouse.io/acme/jobs/2", location_text="San Francisco, CA",
            )],
        )

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)

    result = run(run_module.run(empty_db, kind="daily"))
    assert result["new_count"] == 0
    assert run(empty_db.all("SELECT * FROM postings")) == []


def test_run_archives_posting_missing_from_next_feed_check(empty_db, monkeypatch):
    company_id = _insert_company(empty_db)
    posting_id = run(empty_db.run(
        """INSERT INTO postings (company_id, company_name, title, matched_title, url, locations,
           location_match, first_seen, status)
           VALUES (?, 'Acme', 'MBA Product Manager Intern', 'MBA Product Manager Intern',
                   'https://boards.greenhouse.io/acme/jobs/1', '[]', 'match', '2026-09-19', 'open')""",
        company_id,
    ))

    def fake_run_fetcher(company, session=None):
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))

    posting = run(empty_db.first("SELECT * FROM postings WHERE id = ?", posting_id))
    assert posting["status"] == "closed"
    assert posting["verified_via"] == "feed"
    assert posting["archived_at"] is not None
    assert posting["archive_reason"] == "No longer listed in the company's feed."


def test_run_prunes_wrong_cycle_posting_even_when_still_open(empty_db, monkeypatch):
    # Old seed data (phase 1) never applied the Summer 2027 cycle rule, so
    # the daily job is what catches wrong-cycle postings already in D1.
    company_id = _insert_company(empty_db)
    posting_id = run(empty_db.run(
        """INSERT INTO postings (company_id, company_name, title, matched_title, url, locations,
           location_match, first_seen, status)
           VALUES (?, 'Acme', 'MBA Class of 2027 Product Manager', 'MBA Product Manager Intern',
                   'https://boards.greenhouse.io/acme/jobs/9', '[]', 'match', '2026-09-19', 'open')""",
        company_id,
    ))

    def fake_run_fetcher(company, session=None):
        return FetchResult(
            company_id=company["id"], company_name=company["name"],
            postings=[FetchedPosting(
                company_name="Acme", title="MBA Class of 2027 Product Manager",
                url="https://boards.greenhouse.io/acme/jobs/9", location_text="San Francisco, CA",
            )],
        )

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)
    run(run_module.run(empty_db, kind="daily"))

    posting = run(empty_db.first("SELECT * FROM postings WHERE id = ?", posting_id))
    assert posting["archived_at"] is not None
    assert "cycle" in posting["archive_reason"].lower()


def test_run_check_request_only_covers_its_companies(empty_db, monkeypatch):
    included_id = _insert_company(empty_db, name="Acme", feed_id="acme")
    excluded_id = _insert_company(empty_db, name="Globex", feed_id="globex")

    calls = []

    def fake_run_fetcher(company, session=None):
        calls.append(company["id"])
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=[])

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)

    request_id = run(empty_db.run(
        "INSERT INTO check_requests (company_ids, requested_at, status) VALUES (?, '2026-09-20T08:00:00', 'pending')",
        f"[{included_id}]",
    ))

    result = run(run_module.run_check_request(empty_db, request_id))
    assert calls == [included_id]
    assert excluded_id not in calls

    request = run(empty_db.first("SELECT * FROM check_requests WHERE id = ?", request_id))
    assert request["status"] == "completed"
    assert result["kind"] == "check_now"


def test_run_marks_run_row_failed_instead_of_leaving_it_running(empty_db, monkeypatch):
    _insert_company(empty_db)

    def broken_fetcher(company, session=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(run_module, "run_fetcher", broken_fetcher)

    with pytest.raises(RuntimeError):
        run(run_module.run(empty_db, kind="daily"))

    runs = run(empty_db.all("SELECT * FROM runs"))
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert "boom" in runs[0]["summary"]
