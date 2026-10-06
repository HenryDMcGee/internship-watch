"""Issue #60: real internships wrongly rejected and kept hidden."""
import asyncio
import json

import pytest

from job import config, fingerprint
from job.locations import location_match
from job.matching import classify_posting, cycle_status, match_title, reconsider_rejection
from job.restore_rejections import restore_rejections

TITLES_DOC = config.load_titles()
LOCATIONS = {"countries": ["US", "JP"], "include_remote": False}


def run(coro):
    return asyncio.run(coro)


# --- locations -------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Austin TX Warren MI", "Austin, TX; Warren, MI", "Spring, TX, US", "Pennsylvania, US", "Pittsburgh, PA",
    "US", "USA", "United States", "Remote - US", "Remote - United States", "California", "New York, NY",
    "Washington, DC", "Portland, OR", "Tokyo", "JPN", "JP", "Japan", "Tokyo, JPN",
])
def test_us_and_japan_locations_match(text):
    assert location_match(text, LOCATIONS) == "match"


@pytest.mark.parametrize("text", ["", None, "   ", "Somewhere nice"])
def test_missing_or_unrecognizable_location_is_unclear(text):
    assert location_match(text, LOCATIONS) == "unclear"


def test_new_mexico_is_not_mexico_and_lowercase_in_or_is_not_a_state():
    assert location_match("Santa Fe, New Mexico", {"countries": ["MX"], "include_remote": False}) == "outside"
    assert location_match("work in or around town", LOCATIONS) == "unclear"


def test_other_countries_are_still_outside():
    assert location_match("London, UK", LOCATIONS) == "outside"


# --- hiring cycle ----------------------------------------------------------

@pytest.mark.parametrize("text", [
    "MBA Class of 2028 Internship", "Graduating 2028", "graduating in 2028", "Summer 2027", "2027 Summer MBA Intern",
])
def test_right_cycle_wording_is_accepted(text):
    assert cycle_status(text) == "match"


@pytest.mark.parametrize("text", ["Summer 2026 Intern", "MBA Class of 2027", "Class of 2026", "2026 Summer Intern"])
def test_earlier_cycles_are_rejected(text):
    assert cycle_status(text) == "reject"


def test_right_cycle_wording_wins_over_a_stray_earlier_year():
    assert cycle_status("Summer 2027 internship, like Summer 2026 but bigger") == "match"


# --- titles ----------------------------------------------------------------

def _every_title_text():
    for entry in TITLES_DOC["titles"]:
        if entry["active"]:
            yield entry["title"]
            yield from entry["aliases"]


@pytest.mark.parametrize("text", list(_every_title_text()))
def test_every_active_title_and_alias_matches_its_own_exact_text(text):
    assert match_title(text, TITLES_DOC["titles"]) is not None
    result = classify_posting(text, "", TITLES_DOC)
    assert result.reason != "no active title or alias matched"


@pytest.mark.parametrize("posting_title", [
    "Associate Product Manager, Intern", "Associate Product Manager - Intern", "Associate Product Manager (Intern)",
    "Associate  Product Manager Intern", "ASSOCIATE PRODUCT MANAGER INTERN",
])
def test_punctuation_and_spacing_do_not_break_a_title_match(posting_title):
    assert match_title(posting_title, TITLES_DOC["titles"]).matched_title == "Associate Product Manager Intern"


def test_title_match_lands_on_whole_words():
    assert match_title("Reproduct Manager Interns", TITLES_DOC["titles"]) is None


# --- the wrongly rejected examples -----------------------------------------

EXAMPLES = [
    ("MBA Class of 2028 Internship Sr Product Manager Business Leadership Course", "Tokyo, JPN",
     "wrong hiring cycle", "match"),
    ("2027 Summer Intern - Digital Product - Product Management (MBA)", "Austin TX Warren MI",
     "Title matches Digital Product Manager Intern but location is missing", "match"),
    ("Product Management Intern - Workforce Services and Solutions", "Spring, TX, US",
     "Location not specified", "match"),
    ("Product Management Intern", "Pennsylvania, US", "Location not specified", "match"),
    ("Company Intern - Product Manager", "US", "Location not specified", "match"),
    ("Product Management Intern - Master's MBA", "", "Location is blank", "unclear"),
    ("Product Management Intern", "", "Location is blank", "unclear"),
    ("Associate Product Manager, Intern", "Pittsburgh, PA", "no active title or alias matched", "match"),
]


@pytest.mark.parametrize("title,loc,reason,expected", EXAMPLES)
def test_wrongly_rejected_examples_are_kept(title, loc, reason, expected):
    result = reconsider_rejection(reason, title, loc, TITLES_DOC, LOCATIONS)
    assert result is not None
    assert result.location_match == expected


def test_real_wrong_cycle_rejections_stand():
    for title in ("Product Management Intern, Summer 2026", "Product Manager Intern - MBA Class of 2027"):
        assert reconsider_rejection("wrong hiring cycle", title, "Austin, TX", TITLES_DOC, LOCATIONS) is None


def test_correct_rejections_stand():
    assert reconsider_rejection(
        "excluded word 'marketing'", "Product Marketing Intern", "Austin, TX", TITLES_DOC, LOCATIONS,
    ) is None
    assert reconsider_rejection(
        "no active title or alias matched", "Product Design Intern", "Austin, TX", TITLES_DOC, LOCATIONS,
    ) is None
    assert reconsider_rejection(
        "Location outside US and Japan", "Product Manager Intern", "London, UK", TITLES_DOC, LOCATIONS,
    ) is None


def test_reason_that_contradicts_its_own_title_is_treated_as_unclear():
    result = reconsider_rejection(
        "Title matches Product Manager Intern but location is not in the target countries",
        "Product Manager Intern", "London, UK", TITLES_DOC, LOCATIONS,
    )
    assert result is not None
    assert result.location_match == "unclear"


# --- cleanup of remembered rejections --------------------------------------

def _remember(db, *, company, title, location, reason, url=None, posting_id=None):
    fp = fingerprint.compute_fingerprint(company_name=company, title=title, location_text=location, url=url)
    return run(fingerprint.record_rejection(
        db, fp, reason=reason, settings_version="old", source="test", now_iso="2026-09-20T08:00:00",
    ))


def test_cleanup_restores_eligible_postings_and_leaves_correct_rejections(empty_db):
    db = empty_db
    # Rejected by Claude: an archived row exists.
    posting_id = run(db.run(
        """INSERT INTO postings (company_name, title, url, locations, location_match, first_seen, status,
                                 archived_at, archive_reason)
           VALUES ('Amazon', 'MBA Class of 2028 Internship Sr Product Manager Business Leadership Course',
                   'https://amazon.jobs/en/jobs/1', ?, 'unclear', '2026-09-20', 'unverified',
                   '2026-09-20T08:00:00', 'Claude classification: wrong hiring cycle')""",
        json.dumps(["Tokyo, JPN"]),
    ))
    _remember(
        db, company="Amazon", title="MBA Class of 2028 Internship Sr Product Manager Business Leadership Course",
        location="Tokyo, JPN", reason="wrong hiring cycle", url="https://amazon.jobs/en/jobs/1",
    )
    # Rejected by the feed rules: no row was saved.
    _remember(db, company="Duolingo", title="Associate Product Manager, Intern", location="Pittsburgh, PA",
              reason="no active title or alias matched")
    _remember(db, company="Datadog", title="Product Management Intern", location="", reason="Location not specified")
    # Correct rejections.
    _remember(db, company="Acme", title="Product Design Intern", location="Austin, TX",
              reason="no active title or alias matched")
    _remember(db, company="Acme", title="Product Marketing Intern", location="Austin, TX",
              reason="excluded word 'marketing'")
    _remember(db, company="Acme", title="Product Manager Intern, Summer 2026", location="Austin, TX",
              reason="wrong hiring cycle")
    run(db.run("INSERT INTO companies (name, status) VALUES ('Duolingo', 'ranked')"))
    run(db.run(
        "INSERT INTO feed_snapshots (company_id, content_hash, last_changed, last_checked) "
        "VALUES (1, 'abc', '2026-09-20', '2026-09-20')"
    ))

    result = run(restore_rejections(
        db, titles_doc=TITLES_DOC, locations_setting=LOCATIONS, now_iso="2026-10-01T08:00:00",
    ))

    assert result["restored"] == 1
    assert result["queued_for_next_feed_read"] == 2
    assert result["left_rejected"] == 2  # product design and the Summer 2026 posting
    posting = run(db.first("SELECT * FROM postings WHERE id = ?", posting_id))
    assert posting["archived_at"] is None
    assert posting["matched_title"] == "Senior Product Manager Intern"
    assert posting["location_match"] == "match"
    linked = run(db.first("SELECT * FROM posting_fingerprints WHERE posting_id = ?", posting_id))
    assert linked["rejection_reason"] is None
    # An unchanged feed would otherwise be skipped.
    assert run(db.first("SELECT content_hash FROM feed_snapshots"))["content_hash"] == ""
    # Correct rejections keep their reason and their settings version.
    still = run(db.all("SELECT * FROM posting_fingerprints WHERE rejection_reason IS NOT NULL"))
    assert {row["title_key"].split("|")[1] for row in still if row["settings_version"] == "old"} == {
        "product design intern", "product marketing intern", "product manager intern summer 2026",
    }

    # A second run has nothing left to do.
    again = run(restore_rejections(
        db, titles_doc=TITLES_DOC, locations_setting=LOCATIONS, now_iso="2026-10-02T08:00:00",
    ))
    assert again["restored"] == 0 and again["queued_for_next_feed_read"] == 0


def test_dry_run_changes_nothing(empty_db):
    _remember(empty_db, company="Datadog", title="Product Management Intern", location="",
              reason="Location not specified")
    result = run(restore_rejections(
        empty_db, titles_doc=TITLES_DOC, locations_setting=LOCATIONS, now_iso="2026-10-01T08:00:00", dry_run=True,
    ))
    assert result["queued_for_next_feed_read"] == 1
    row = run(empty_db.first("SELECT settings_version FROM posting_fingerprints"))
    assert row["settings_version"] == "old"


def _candidate(title, location="", cid=1):
    from job.save_posting import Candidate

    return Candidate(
        id=cid, company_name="HP", title=title, url=f"https://hp.example/{cid}", source_name="careers_page",
        location_text=location, posted_date=None,
        fp=fingerprint.compute_fingerprint(company_name="HP", title=title, url=f"https://hp.example/{cid}"),
        fingerprint_source="careers_page",
    )


def test_classification_does_not_reject_a_rejection_that_contradicts_its_title(empty_db):
    from job.agent.pipeline import _apply_classification_decisions

    candidate = _candidate("Product Management Intern")
    decisions = [{"id": 1, "decision": "reject", "reason": "Location not specified"}]

    saved = run(_apply_classification_decisions(
        empty_db, decisions, [candidate], now_iso="2026-10-01T08:00:00", settings_version="v",
        titles_doc=TITLES_DOC, locations_setting=LOCATIONS,
    ))

    assert saved == {1}
    posting = run(empty_db.first("SELECT * FROM postings"))
    assert posting["archived_at"] is None
    assert posting["matched_title"] == "Product Management Intern"
    assert posting["location_match"] == "unclear"
