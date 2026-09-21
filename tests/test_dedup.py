import asyncio

from job.dedup import find_duplicate_posting


def run(coro):
    return asyncio.run(coro)


def _insert_posting(db, **overrides):
    row = {
        "company_name": "Acme", "title": "PM Intern", "url": "https://acme.example/1",
        "locations": "[]", "location_match": "unclear", "first_seen": "2026-09-18", "status": "unverified",
    }
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    return run(db.run(f"INSERT INTO postings ({columns}) VALUES ({placeholders})", *row.values()))


def test_find_duplicate_posting_matches_by_url(empty_db):
    posting_id = _insert_posting(empty_db, url="https://acme.example/1")
    found = run(find_duplicate_posting(
        empty_db, company_name="Acme", title="Something else entirely", url="https://acme.example/1",
    ))
    assert found["id"] == posting_id


def test_find_duplicate_posting_matches_by_company_and_normalized_title_when_url_differs(empty_db):
    # issue #26: an NVIDIA role Henry had already marked applied, seeded
    # under one URL, got re-discovered by Claude under a new URL and was
    # saved as a second, duplicate posting because only the URL was
    # checked. Company + normalized title must catch this even though the
    # URL is different.
    posting_id = _insert_posting(
        empty_db, company_name="NVIDIA",
        title="Product Management MBA Intern, Data Center GPU - Summer 2027",
        url="https://nvidia.example/old-listing",
    )
    found = run(find_duplicate_posting(
        empty_db, company_name="NVIDIA",
        title="  product management mba intern, data center gpu - summer 2027  ",
        url="https://nvidia.example/new-listing",
    ))
    assert found["id"] == posting_id


def test_find_duplicate_posting_returns_none_for_a_genuinely_new_role(empty_db):
    _insert_posting(empty_db, company_name="Acme", title="PM Intern", url="https://acme.example/1")
    found = run(find_duplicate_posting(
        empty_db, company_name="Acme", title="Data Analyst Intern", url="https://acme.example/2",
    ))
    assert found is None


def test_find_duplicate_posting_does_not_match_a_different_company_with_the_same_title(empty_db):
    _insert_posting(empty_db, company_name="Acme", title="PM Intern", url="https://acme.example/1")
    found = run(find_duplicate_posting(
        empty_db, company_name="Globex", title="PM Intern", url="https://globex.example/1",
    ))
    assert found is None
