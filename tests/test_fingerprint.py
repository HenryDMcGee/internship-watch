import asyncio
import sqlite3

import pytest

from job import fingerprint


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def test_clean_url_strips_tracking_params_case_and_trailing_slash():
    a = fingerprint.clean_url("https://Jobs.Example.com/job/123?utm_source=x&source=linkedin#apply")
    b = fingerprint.clean_url("https://jobs.example.com/job/123/")
    assert a == b


def test_clean_url_different_paths_are_different():
    a = fingerprint.clean_url("https://jobs.example.com/job/123")
    b = fingerprint.clean_url("https://jobs.example.com/job/124")
    assert a != b


def test_title_key_ignores_punctuation_differences():
    a = fingerprint.title_key(
        "NVIDIA", "Product Management MBA Intern – Data Center GPU (Summer 2027)", "Santa Clara, CA",
    )
    b = fingerprint.title_key(
        "NVIDIA", "Product Management MBA Intern, Data Center GPU, Summer 2027", "Santa Clara, CA",
    )
    assert a == b


def test_title_key_differs_by_location():
    austin = fingerprint.title_key("Acme", "Product Manager Intern", "Austin, TX")
    seattle = fingerprint.title_key("Acme", "Product Manager Intern", "Seattle, WA")
    assert austin != seattle


def test_extract_job_id_workday():
    url = "https://nvidia.wd5.myworkdayjobs.com/en-US/NvidiaExternalCareerSite/job/US-Site/XD-Product-Manager-Intern_JR2025670"
    assert fingerprint.extract_job_id(url) == "workday:JR2025670"


def test_extract_job_id_greenhouse():
    url = "https://job-boards.greenhouse.io/acme/jobs/12345?gh_jid=8198582"
    assert fingerprint.extract_job_id(url) == "greenhouse:8198582"


def test_extract_job_id_lever():
    url = "https://jobs.lever.co/acme/de305d54-75b4-431b-adb2-eb6b9e546014"
    assert fingerprint.extract_job_id(url) == "lever:de305d54-75b4-431b-adb2-eb6b9e546014"


def test_extract_job_id_amazon():
    url = "https://www.amazon.jobs/en/jobs/10509639/product-manager-intern"
    assert fingerprint.extract_job_id(url) == "amazon:10509639"


def test_extract_job_id_returns_none_for_an_unrecognized_url():
    assert fingerprint.extract_job_id("https://acme.example/careers/123") is None


def test_settings_version_changes_when_a_title_is_added():
    titles_doc = {"titles": [{"title": "PM Intern", "aliases": [], "active": True}], "exclude_words": []}
    locations = {"countries": ["US"], "include_remote": False}
    before = fingerprint.settings_version(titles_doc, locations)

    titles_doc2 = {
        "titles": [
            {"title": "PM Intern", "aliases": [], "active": True},
            {"title": "APM Intern", "aliases": [], "active": True},
        ],
        "exclude_words": [],
    }
    after = fingerprint.settings_version(titles_doc2, locations)
    assert before != after


def test_settings_version_changes_when_a_location_country_is_added():
    titles_doc = {"titles": [{"title": "PM Intern", "aliases": [], "active": True}], "exclude_words": []}
    before = fingerprint.settings_version(titles_doc, {"countries": ["US"], "include_remote": False})
    after = fingerprint.settings_version(titles_doc, {"countries": ["US", "JP"], "include_remote": False})
    assert before != after


def test_settings_version_is_stable_for_equivalent_input():
    titles_doc = {"titles": [{"title": "PM Intern", "aliases": ["APM"], "active": True}], "exclude_words": ["x"]}
    locations = {"countries": ["US"], "include_remote": False}
    assert fingerprint.settings_version(titles_doc, locations) == fingerprint.settings_version(titles_doc, locations)


# ---------------------------------------------------------------------------
# Matching against posting_fingerprints
# ---------------------------------------------------------------------------

def _insert_posting(db, posting_id: int, **overrides):
    row = {
        "id": posting_id, "company_name": "Acme", "title": "PM Intern", "url": f"https://acme.example/{posting_id}",
        "locations": "[]", "location_match": "unclear", "first_seen": "2026-09-01", "status": "unverified",
    }
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    run(db.run(f"INSERT INTO postings ({columns}) VALUES ({placeholders})", *row.values()))


def _insert_fingerprint(db, **overrides):
    row = {
        "job_id": None, "clean_url": None, "title_key": "acme|pm intern|sf",
        "posting_id": None, "rejection_reason": None, "settings_version": "v1",
        "first_seen": "2026-09-01", "last_seen": "2026-09-01", "last_source": "feed",
    }
    row.update(overrides)
    if row.get("posting_id") is not None:
        existing = run(db.first("SELECT id FROM postings WHERE id = ?", row["posting_id"]))
        if not existing:
            _insert_posting(db, row["posting_id"])
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    return run(db.run(f"INSERT INTO posting_fingerprints ({columns}) VALUES ({placeholders})", *row.values()))


def test_find_match_same_job_id_different_url_and_title_matches(empty_db):
    _insert_fingerprint(
        empty_db, job_id="workday:JR111", clean_url="https://acme.example/a", title_key="acme|pm intern|sf",
        posting_id=1,
    )
    fp = fingerprint.Fingerprint(job_id="workday:JR111", clean_url="https://acme.example/b", title_key="acme|pm intern, updated|sf")
    match = run(fingerprint.find_match(empty_db, fp))
    assert match is not None
    assert match["posting_id"] == 1


def test_find_match_different_job_ids_same_title_do_not_match(empty_db):
    _insert_fingerprint(
        empty_db, job_id="workday:JR111", clean_url="https://acme.example/a", title_key="acme|pm intern|sf",
        posting_id=1,
    )
    fp = fingerprint.Fingerprint(job_id="workday:JR222", clean_url="https://acme.example/b", title_key="acme|pm intern|sf")
    match = run(fingerprint.find_match(empty_db, fp))
    assert match is None


def test_find_match_falls_back_from_url_to_title_key(empty_db):
    _insert_fingerprint(
        empty_db, job_id=None, clean_url="https://acme.example/old", title_key="acme|pm intern|sf", posting_id=1,
    )
    # Matches by clean_url.
    by_url = fingerprint.Fingerprint(job_id=None, clean_url="https://acme.example/old", title_key="acme|different|sf")
    assert run(fingerprint.find_match(empty_db, by_url))["posting_id"] == 1

    # Fails by clean_url, matches by title_key.
    by_title = fingerprint.Fingerprint(job_id=None, clean_url="https://acme.example/new", title_key="acme|pm intern|sf")
    assert run(fingerprint.find_match(empty_db, by_title))["posting_id"] == 1


def test_find_match_nvidia_case_mirror_site_with_no_job_id_matches_by_title(empty_db):
    # An existing posting found through a Workday URL (so it has a job_id on
    # file) is rediscovered at a mirror site with no job id at all -- must
    # still match by company + title + location.
    _insert_fingerprint(
        empty_db, job_id="workday:JR2025670", clean_url="https://nvidia.wd5.myworkdayjobs.com/job/x",
        title_key="nvidia|product management mba intern data center gpu|santa clara ca", posting_id=42,
    )
    mirror_fp = fingerprint.compute_fingerprint(
        company_name="NVIDIA", title="Product Management MBA Intern, Data Center GPU",
        location_text="Santa Clara, CA", url="https://mirror.example/nvidia/123",
    )
    match = run(fingerprint.find_match(empty_db, mirror_fp))
    assert match is not None
    assert match["posting_id"] == 42


def test_find_matches_handles_500_fingerprints_under_the_100_variable_limit(empty_db):
    empty_db._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 100)
    fps = [
        fingerprint.Fingerprint(job_id=None, clean_url=None, title_key=f"acme|role {i}|sf")
        for i in range(500)
    ]
    matches = run(fingerprint.find_matches(empty_db, fps))
    assert len(matches) == 500
    assert all(m is None for m in matches)


# ---------------------------------------------------------------------------
# match_incoming: the entry point the feed/discovery/classification paths use
# ---------------------------------------------------------------------------

def test_match_incoming_returns_new_when_nothing_matches(empty_db):
    outcome = run(fingerprint.match_incoming(
        empty_db, company_name="Acme", title="PM Intern", url="https://acme.example/1",
        location_text="SF", settings_version="v1", source="feed", now_iso="2026-09-25T08:00:00",
    ))
    assert outcome.kind == "new"


def test_match_incoming_recognizes_a_remembered_rejection_and_keeps_the_reason(empty_db):
    fp = fingerprint.compute_fingerprint(
        company_name="Acme", title="Software Engineer Intern", location_text="SF", url="https://acme.example/9",
    )
    run(fingerprint.record_rejection(
        empty_db, fp, reason="not a PM role", settings_version="v1", source="feed", now_iso="2026-09-24T08:00:00",
    ))

    outcome = run(fingerprint.match_incoming(
        empty_db, company_name="Acme", title="Software Engineer Intern", url="https://acme.example/9",
        location_text="SF", settings_version="v1", source="feed", now_iso="2026-09-25T08:00:00",
    ))
    assert outcome.kind == "rejection"
    assert outcome.reason == "not a PM role"

    row = run(empty_db.first("SELECT * FROM posting_fingerprints"))
    assert row["last_seen"] == "2026-09-25T08:00:00"
    assert row["last_source"] == "feed"


def test_match_incoming_treats_a_stale_rejection_as_new(empty_db):
    fp = fingerprint.compute_fingerprint(
        company_name="Acme", title="Software Engineer Intern", location_text="SF", url="https://acme.example/9",
    )
    stale_id = run(fingerprint.record_rejection(
        empty_db, fp, reason="not a PM role", settings_version="v1", source="feed", now_iso="2026-09-24T08:00:00",
    ))

    outcome = run(fingerprint.match_incoming(
        empty_db, company_name="Acme", title="Software Engineer Intern", url="https://acme.example/9",
        location_text="SF", settings_version="v2", source="feed", now_iso="2026-09-25T08:00:00",
    ))
    assert outcome.kind == "new"
    assert outcome.stale_fingerprint_id == stale_id


def test_match_incoming_falls_back_to_legacy_matching_for_un_fingerprinted_postings(empty_db):
    # A posting created before fingerprinting existed (or seeded directly by
    # a test) has no posting_fingerprints row -- match_incoming must still
    # recognize it via the same company+title fallback job.dedup always used,
    # and self-heal by writing a fingerprint for it.
    posting_id = run(empty_db.run(
        """INSERT INTO postings (company_name, title, url, locations, location_match, first_seen, status, applied)
           VALUES ('Acme', 'PM Intern', 'https://acme.example/old', '[]', 'unclear', '2026-09-18', 'unverified', 1)"""
    ))

    outcome = run(fingerprint.match_incoming(
        empty_db, company_name="Acme", title="PM Intern", url="https://acme.example/new",
        location_text="", settings_version="v1", source="claude-discovery", now_iso="2026-09-25T08:00:00",
    ))
    assert outcome.kind == "posting"
    assert outcome.posting_id == posting_id

    fp_row = run(empty_db.first("SELECT * FROM posting_fingerprints WHERE posting_id = ?", posting_id))
    assert fp_row is not None
