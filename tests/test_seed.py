import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import seed  # noqa: E402
from src.companies import normalize_company_name  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def test_map_posting_renames_old_fields():
    old = {
        "id": "amazon-pmt-intern-2027",
        "applied": True,
        "company": "Amazon",
        "firstSeen": "2026-09-18",
        "location": "Seattle, WA",
        "note": "note text",
        "postedDate": "2026-08-20",
        "source": "Amazon.jobs",
        "status": "open",
        "statusChecked": "2026-09-19",
        "statusNote": "",
        "title": "Product Manager Technical (PMT) Intern",
        "url": "https://amazon.jobs/x",
    }
    mapped = seed.map_posting(old)
    assert mapped["company_name"] == "Amazon"
    assert mapped["first_seen"] == "2026-09-18"
    assert mapped["posted_date"] == "2026-08-20"
    assert mapped["verified_at"] == "2026-09-19"
    assert mapped["applied"] is True
    assert mapped["locations"] == ["Seattle, WA"]
    assert mapped["source_name"] == "Amazon.jobs"


def test_map_posting_handles_mirror_url_and_empty_location():
    old = {"company": "NVIDIA", "title": "PM Intern", "mirrorUrl": "https://mirror/x", "location": ""}
    mapped = seed.map_posting(old)
    assert mapped["mirror_url"] == "https://mirror/x"
    assert mapped["locations"] == []


def test_map_source_renames_old_fields():
    old = {
        "id": "amazon-jobs",
        "covers": "Amazon internships.",
        "found": 2,
        "kind": "Company career sites",
        "lastChecked": "2026-09-18",
        "name": "Amazon.jobs",
        "notes": "",
        "url": "https://www.amazon.jobs",
    }
    mapped = seed.map_source(old)
    assert mapped["name"] == "Amazon.jobs"
    assert mapped["found_count"] == 2
    assert mapped["last_checked"] == "2026-09-18"
    assert mapped["status"] == "active"


def test_map_run_renames_old_fields():
    old = {"id": "2026-09-18", "date": "2026-09-18", "newCount": 9, "next": "check X", "summary": "found stuff"}
    mapped = seed.map_run(old)
    assert mapped["started_at"] == "2026-09-18"
    assert mapped["new_count"] == 9
    assert mapped["next_notes"] == "check X"
    assert mapped["kind"] == "daily"


def test_map_settings_uses_locations_panel_shape():
    settings = {"locations": {"countries": ["US", "JP"], "include_remote": False}, "watchlist": [], "last_run": "2026-09-19"}
    mapped = dict(seed.map_settings(settings))
    assert json.loads(mapped["locations"]) == {"countries": ["US", "JP"], "include_remote": False}
    assert json.loads(mapped["last_run"]) == "2026-09-19"


def test_build_statements_matches_done_criteria(empty_db):
    statements = seed.build_statements()
    for sql, params in statements:
        run(empty_db.run(sql, *params))

    postings = run(empty_db.all("SELECT * FROM postings"))
    sources = run(empty_db.all("SELECT * FROM sources"))
    runs = run(empty_db.all("SELECT * FROM runs"))
    assert len(postings) == 9
    assert sum(1 for p in postings if p["status"] == "closed") == 3
    assert len(sources) == 30
    assert len(runs) == 2


def test_build_statements_imports_ranking_as_ranked_companies():
    statements = seed.build_statements()
    ranking_rows = seed.load_ranking_csv()
    ranked_inserts = [
        params for sql, params in statements
        if sql.strip().startswith("INSERT INTO companies") and "market_cap_usd" in sql
    ]
    assert len(ranked_inserts) == len(ranking_rows) == 100


def test_map_ranked_company_detects_amazons_feed_from_ranking_csv():
    # seed/ranking.csv's Amazon row carries a careers_url so seeding sets
    # feed_type/feed_id from it, the same detection the "Add company" form
    # uses, instead of leaving Amazon with no known feed.
    ranking_rows = seed.load_ranking_csv()
    amazon = next(r for r in ranking_rows if r["name"] == "Amazon")
    mapped = seed.map_ranked_company(amazon)
    assert mapped["careers_url"] == "https://www.amazon.jobs"
    assert mapped["feed_type"] == "amazon_jobs"


def test_build_statements_gives_amazon_a_working_feed(empty_db):
    statements = seed.build_statements()
    for sql, params in statements:
        run(empty_db.run(sql, *params))
    amazon = run(empty_db.first("SELECT * FROM companies WHERE name = 'Amazon'"))
    assert amazon["feed_type"] == "amazon_jobs"


def test_build_statements_flags_unranked_companies_for_review(empty_db):
    statements = seed.build_statements()
    for sql, params in statements:
        run(empty_db.run(sql, *params))
    companies = run(empty_db.all("SELECT * FROM companies"))

    ranking_rows = seed.load_ranking_csv()
    ranked_normalized = {normalize_company_name(r["name"]) for r in ranking_rows}
    companies_json = json.loads((ROOT / "seed" / "companies.json").read_text())["companies"]
    expected_review_count = sum(
        1 for name in companies_json if normalize_company_name(name) not in ranked_normalized
    )

    review = [c for c in companies if c["status"] == "review"]
    ranked = [c for c in companies if c["status"] == "ranked"]
    assert len(ranked) == 100
    assert len(review) == expected_review_count
    assert expected_review_count > 0


def test_render_sql_escapes_single_quotes():
    sql_text = seed.render_sql([("INSERT INTO x (a) VALUES (?)", ("O'Brien",))])
    assert "O''Brien" in sql_text
