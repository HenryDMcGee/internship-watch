"""Issue #68: a posting with no title match is never saved, through any path; the
cleanup archives the ones saved before the fix."""
import asyncio
import json
import re
from pathlib import Path

import pytest

import job.run as run_module
from conftest import adapt_discovery_reply
from job import config, fingerprint
from job.agent import client as agent_client
from job.agent.client import ClaudeStepResult
from job.agent.pipeline import _apply_classification_decisions, run_agent_steps
from job.cleanup_unmatched import CLEANUP_REASON, cleanup_unmatched
from job.fetchers import FetchResult
from job.fetchers import careers_pages as cp
from job.models import FetchedPosting
from job.restore_rejections import restore_rejections
from job.save_posting import Candidate, save_posting
from test_agent_pipeline import SETTINGS, _insert_company as _insert_pipeline_company, _open_status

TITLES_DOC = config.load_titles()
LOCATIONS = {"countries": ["US", "JP"], "include_remote": False}
APPLE_TITLE = "AMS Lab Validation Intern"


def run(coro):
    return asyncio.run(coro)


def _insert_company(db, **overrides):
    row = {"name": "Apple", "status": "pinned", "feed_type": "greenhouse", "feed_id": "apple"}
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    return run(db.run(f"INSERT INTO companies ({columns}) VALUES ({placeholders})", *row.values()))


def _feed(monkeypatch, titles, location="", company="Apple"):
    def fake_run_fetcher(company_row, session=None):
        return FetchResult(
            company_id=company_row["id"], company_name=company_row["name"],
            postings=[
                FetchedPosting(
                    company_name=company, title=t, url=f"https://jobs.example/{i}", location_text=location,
                )
                for i, t in enumerate(titles)
            ],
        )

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)


# --- every path ----------------------------------------------------------------

def test_feed_never_saves_a_posting_with_no_title_match(empty_db, monkeypatch):
    _insert_company(empty_db)
    _feed(monkeypatch, [APPLE_TITLE], location="")  # unclear location
    run(run_module.run(empty_db, kind="daily"))
    assert run(empty_db.all("SELECT * FROM postings")) == []
    remembered = run(empty_db.first("SELECT * FROM posting_fingerprints"))
    assert remembered["posting_id"] is None and remembered["rejection_reason"]


def test_check_now_never_saves_a_posting_with_no_title_match(empty_db, monkeypatch):
    company_id = _insert_company(empty_db)
    _feed(monkeypatch, [APPLE_TITLE], location="")
    request_id = run(empty_db.run(
        "INSERT INTO check_requests (company_ids, requested_at, status) VALUES (?, '2026-10-06T08:00:00', 'pending')",
        f"[{company_id}]",
    ))
    run(run_module.run_check_request(empty_db, request_id))
    assert run(empty_db.all("SELECT * FROM postings")) == []


def test_check_now_does_not_save_a_close_title_either_without_classification(empty_db, monkeypatch):
    company_id = _insert_company(empty_db)
    _feed(monkeypatch, ["Intern Product Manager Search Maps Cloud Seattle"], location="Seattle, WA")
    request_id = run(empty_db.run(
        "INSERT INTO check_requests (company_ids, requested_at, status) VALUES (?, '2026-10-06T08:00:00', 'pending')",
        f"[{company_id}]",
    ))
    run(run_module.run_check_request(empty_db, request_id))
    assert run(empty_db.all("SELECT * FROM postings")) == []


def test_careers_page_never_saves_a_posting_with_no_title_match(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    company_id = _insert_company(
        empty_db, feed_type=None, feed_id=None, careers_url="https://apple.example/careers",
    )

    def fake_run_careers_pages(candidates, **kwargs):
        return cp.CareersRunOutcome(
            pages=[cp.PageOutcome(company_id=company_id, url="https://apple.example/careers", ok=True,
                                   text="listings", method="browser_run", browser_minutes=1.0)],
            browser_run_minutes_used=1.0, switch_reason=None,
        )

    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages", fake_run_careers_pages)

    def fake_claude(step, prompt, *, model, **kwargs):
        if step == "careers_extraction":
            listings = {"listings": [
                {"title": APPLE_TITLE, "location": "", "link": "https://apple.example/jobs/58",
                 "posted_date": None, "job_id": "58"},
            ]}
            return ClaudeStepResult(step=step, model=model, text=json.dumps(listings))
        text = {
            "discovery": '{"results": []}', "scout": '{"sources": []}',
            "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
        }[step]
        return ClaudeStepResult(step=step, model=model, text=text, web_searches=3 if step in ("discovery", "scout") else 0)

    monkeypatch.setattr(agent_client, "run_claude_step", fake_claude)
    run(run_module.run(empty_db, kind="daily"))
    assert run(empty_db.all("SELECT * FROM postings")) == []


def test_discovery_never_saves_a_posting_with_no_title_match(empty_db):
    _insert_pipeline_company(empty_db, feed_type=None)
    run_id = run(empty_db.run(
        "INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-10-06T08:00:00', 'running')"
    ))
    calls = []

    def fake(step, prompt, *, model):
        calls.append(step)
        text = {
            "discovery": '{"results": [{"company_name": "Apple", "title": "%s", "url": "https://apple.example/58", '
                         '"location_text": ""}]}' % APPLE_TITLE,
            "scout": '{"sources": []}',
            "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
        }[step]
        if step == "discovery":
            text = adapt_discovery_reply(text, prompt)
        return ClaudeStepResult(step=step, model=model, text=text, web_searches=3 if step != "improvement" else 0)

    run(run_agent_steps(
        empty_db, run_id=run_id, companies=[{"id": 1, "name": "Acme", "feed_type": None}],
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS, budget_settings=SETTINGS,
        status=_open_status(), today="2026-10-06", now_iso="2026-10-06T08:00:00", run_claude=fake,
    ))
    assert run(empty_db.all("SELECT * FROM postings")) == []
    assert "classification" not in calls  # the rules reject it, no Claude call needed


def _candidate(title, location="", cid=1):
    return Candidate(
        id=cid, company_name="Apple", title=title, url=f"https://apple.example/{cid}", source_name="careers_page",
        location_text=location, posted_date=None,
        fp=fingerprint.compute_fingerprint(company_name="Apple", title=title, url=f"https://apple.example/{cid}"),
        fingerprint_source="careers_page",
    )


@pytest.mark.parametrize("decision", [
    {"id": 1, "decision": "match", "matched_title": "Made Up Title", "location_match": "unclear"},
    {"id": 1, "decision": "match", "matched_title": "Product Manager Intern", "location_match": "unclear"},
    {"id": 1, "decision": "reject", "reason": "Location not specified"},
])
def test_classification_never_saves_the_lab_role(empty_db, decision):
    saved = run(_apply_classification_decisions(
        empty_db, [decision], [_candidate(APPLE_TITLE)], now_iso="2026-10-06T08:00:00", settings_version="v",
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS,
    ))
    assert saved == set()
    assert run(empty_db.all("SELECT * FROM postings")) == []
    assert run(empty_db.first("SELECT * FROM posting_fingerprints"))["rejection_reason"]


def test_classification_match_is_saved_with_its_method(empty_db):
    decision = {"id": 1, "decision": "match", "matched_title": "Product Manager Intern", "location_match": "unclear"}
    saved = run(_apply_classification_decisions(
        empty_db, [decision], [_candidate("Product Manager Intern Search Maps Cloud Seattle")],
        now_iso="2026-10-06T08:00:00", settings_version="v", titles_doc=TITLES_DOC, locations_setting=LOCATIONS,
    ))
    assert saved == {1}
    posting = run(empty_db.first("SELECT * FROM postings"))
    assert posting["match_method"] == "classification" and posting["location_match"] == "unclear"


def test_restore_never_brings_back_a_posting_with_no_title_match(empty_db):
    # Archived by classification for its location; the title matches nothing.
    run(empty_db.run(
        """INSERT INTO postings (company_name, title, url, locations, location_match, first_seen, status,
                                 archived_at, archive_reason)
           VALUES ('Apple', ?, 'https://apple.example/58', '[]', 'unclear', '2026-10-05', 'unverified',
                   '2026-10-05T08:00:00', 'Claude classification: Location not specified')""",
        APPLE_TITLE,
    ))
    fp = fingerprint.compute_fingerprint(company_name="Apple", title=APPLE_TITLE, location_text="",
                                         url="https://apple.example/58")
    run(fingerprint.record_rejection(
        empty_db, fp, reason="Location not specified", settings_version="old", source="test",
        now_iso="2026-10-05T08:00:00",
    ))
    result = run(restore_rejections(
        empty_db, titles_doc=TITLES_DOC, locations_setting=LOCATIONS, now_iso="2026-10-06T08:00:00",
    ))
    assert result["restored"] == 0 and result["queued_for_next_feed_read"] == 0
    assert run(empty_db.first("SELECT * FROM postings"))["archived_at"] is not None


def test_save_posting_is_the_gate(empty_db):
    fp = fingerprint.compute_fingerprint(company_name="Apple", title=APPLE_TITLE, url="https://apple.example/58")
    common = dict(
        titles_doc=TITLES_DOC, match_method="exact", company_name="Apple", title=APPLE_TITLE,
        url="https://apple.example/58", source_name="x", locations=[], location_match="unclear",
        posted_date=None, first_seen="2026-10-06", fp=fp, settings_version="v", fingerprint_source="x",
        now_iso="2026-10-06T08:00:00",
    )
    assert run(save_posting(empty_db, matched_title=None, **common)) is None
    assert run(save_posting(empty_db, matched_title="", **common)) is None
    assert run(save_posting(empty_db, matched_title="Not An Active Title", **common)) is None
    assert run(empty_db.all("SELECT * FROM postings")) == []
    new_id = run(save_posting(empty_db, matched_title="Product Manager Intern", **common))
    assert new_id is not None
    posting = run(empty_db.first("SELECT * FROM postings"))
    assert posting["matched_title"] == "Product Manager Intern" and posting["match_method"] == "exact"


# --- location -------------------------------------------------------------------

def test_unclear_location_with_a_matching_title_is_kept_and_tagged(empty_db, monkeypatch):
    _insert_company(empty_db)
    _feed(monkeypatch, ["Product Management Intern, Summer 2027"], location="")
    run(run_module.run(empty_db, kind="daily"))
    posting = run(empty_db.first("SELECT * FROM postings"))
    assert posting["location_match"] == "unclear"
    assert posting["matched_title"] == "Product Management Intern"
    assert posting["match_method"] == "exact"


def test_unclear_location_without_a_title_match_is_rejected(empty_db, monkeypatch):
    _insert_company(empty_db)
    _feed(monkeypatch, [APPLE_TITLE, "Product Management Intern, Summer 2027"], location="")
    run(run_module.run(empty_db, kind="daily"))
    assert [p["title"] for p in run(empty_db.all("SELECT * FROM postings"))] == [
        "Product Management Intern, Summer 2027"
    ]


def test_fuzzy_feed_match_records_its_score(empty_db, monkeypatch):
    _insert_company(empty_db)
    _feed(monkeypatch, ["Product Manger Intern - Summer 2027"], location="Austin, TX")
    run(run_module.run(empty_db, kind="daily"))
    posting = run(empty_db.first("SELECT * FROM postings"))
    assert re.fullmatch(r"fuzzy:\d+", posting["match_method"])


# --- cleanup ----------------------------------------------------------------------

def _posting(db, title, *, first_seen, matched_title=None, applied=0, user_status=None, company="Apple"):
    return run(db.run(
        """INSERT INTO postings (company_name, title, matched_title, url, locations, location_match,
                                 first_seen, status, applied, user_status)
           VALUES (?, ?, ?, ?, '[]', 'unclear', ?, 'unverified', ?, ?)""",
        company, title, matched_title, f"https://x.example/{title}", first_seen, applied, user_status,
    ))


def test_cleanup_archives_unmatched_october_postings_and_leaves_the_rest(empty_db):
    lab = _posting(empty_db, APPLE_TITLE, first_seen="2026-10-05")
    engineer = _posting(empty_db, "Software Engineer Intern", first_seen="2026-10-01")
    real = _posting(empty_db, "Product Management Intern", first_seen="2026-10-03")
    seeded = _posting(empty_db, "Some Old Seeded Role", first_seen="2026-09-18")
    applied = _posting(empty_db, "Odd Applied Role", first_seen="2026-10-02", applied=1)
    marked_open = _posting(empty_db, "Odd Open Role", first_seen="2026-10-02", user_status="open")
    marked_closed = _posting(empty_db, "Odd Closed Role", first_seen="2026-10-02", user_status="closed")
    matched = _posting(empty_db, "Product Manager Intern", first_seen="2026-10-04", matched_title="Product Manager Intern")

    result = run(cleanup_unmatched(empty_db, titles_doc=TITLES_DOC, now_iso="2026-10-06T12:00:00", dry_run=True))
    assert result["archived"] == 2
    assert run(empty_db.first("SELECT archived_at FROM postings WHERE id = ?", lab))["archived_at"] is None

    result = run(cleanup_unmatched(empty_db, titles_doc=TITLES_DOC, now_iso="2026-10-06T12:00:00"))
    assert result["archived"] == 2 and result["matched"] == 1
    assert {e["title"] for e in result["examples"]} == {APPLE_TITLE, "Software Engineer Intern"}

    def row(pid):
        return run(empty_db.first("SELECT * FROM postings WHERE id = ?", pid))

    for pid in (lab, engineer):
        assert row(pid)["archived_at"] and row(pid)["archive_reason"] == CLEANUP_REASON
    # A real title that was saved without its match is kept and filled in.
    assert row(real)["archived_at"] is None and row(real)["matched_title"] == "Product Management Intern"
    for pid in (seeded, applied, marked_open, marked_closed, matched):
        assert row(pid)["archived_at"] is None
    assert row(seeded)["matched_title"] is None and row(applied)["matched_title"] is None

    # Safe to run twice; rows are archived, never deleted.
    again = run(cleanup_unmatched(empty_db, titles_doc=TITLES_DOC, now_iso="2026-10-07T12:00:00"))
    assert again["considered"] == 0
    assert len(run(empty_db.all("SELECT * FROM postings"))) == 8


# --- the Worker can run the matcher -------------------------------------------------

def test_title_matching_is_standard_library_only_and_lives_in_api_src():
    source = (Path(__file__).resolve().parent.parent / "api" / "src" / "title_matching.py").read_text()
    imported = set(re.findall(r"^(?:from|import) (\w+)", source, re.MULTILINE))
    assert imported <= {"__future__", "html", "re", "unicodedata", "dataclasses"}
