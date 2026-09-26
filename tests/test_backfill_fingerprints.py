import asyncio

from scripts.backfill_fingerprints import backfill

TITLES_DOC = {"titles": [{"title": "PM Intern", "aliases": [], "active": True}], "exclude_words": []}
LOCATIONS = {"countries": ["US"], "include_remote": False}


def run(coro):
    return asyncio.run(coro)


def _insert_posting(db, **overrides):
    row = {
        "company_name": "Acme", "title": "PM Intern", "url": "https://acme.example/1",
        "locations": "[]", "location_match": "unclear", "first_seen": "2026-09-01", "status": "unverified",
    }
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    return run(db.run(f"INSERT INTO postings ({columns}) VALUES ({placeholders})", *row.values()))


def test_backfill_fingerprints_every_posting_including_already_archived(empty_db):
    open_id = _insert_posting(empty_db, url="https://acme.example/open")
    closed_id = _insert_posting(
        empty_db, title="Data Analyst Intern", url="https://acme.example/closed",
        archived_at="2026-09-10T08:00:00", archive_reason="No longer listed in the company's feed.",
    )

    result = run(backfill(
        empty_db, titles_doc=TITLES_DOC, locations_setting=LOCATIONS, now_iso="2026-09-25T08:00:00",
    ))

    assert result["fingerprinted"] == 2
    assert result["duplicates_archived"] == 0
    for posting_id in (open_id, closed_id):
        row = run(empty_db.first("SELECT * FROM posting_fingerprints WHERE posting_id = ?", posting_id))
        assert row is not None


def test_backfill_archives_the_newer_duplicate_and_keeps_the_older(empty_db):
    older_id = _insert_posting(empty_db, url="https://acme.example/old-listing", first_seen="2026-09-01")
    newer_id = _insert_posting(empty_db, url="https://acme.example/new-listing", first_seen="2026-09-15")

    result = run(backfill(
        empty_db, titles_doc=TITLES_DOC, locations_setting=LOCATIONS, now_iso="2026-09-25T08:00:00",
    ))

    assert result["duplicates_archived"] == 1
    older = run(empty_db.first("SELECT * FROM postings WHERE id = ?", older_id))
    newer = run(empty_db.first("SELECT * FROM postings WHERE id = ?", newer_id))
    assert older["archived_at"] is None
    assert newer["archived_at"] is not None
    assert newer["archive_reason"] == f"duplicate of #{older_id}"


def test_backfill_carries_an_applied_mark_from_the_newer_duplicate_to_the_older(empty_db):
    older_id = _insert_posting(
        empty_db, url="https://acme.example/old-listing", first_seen="2026-09-01", applied=0,
    )
    _insert_posting(empty_db, url="https://acme.example/new-listing", first_seen="2026-09-15", applied=1)

    run(backfill(empty_db, titles_doc=TITLES_DOC, locations_setting=LOCATIONS, now_iso="2026-09-25T08:00:00"))

    older = run(empty_db.first("SELECT * FROM postings WHERE id = ?", older_id))
    assert older["applied"] == 1


def test_backfill_carries_a_user_open_mark_from_the_newer_duplicate_to_the_older(empty_db):
    older_id = _insert_posting(empty_db, url="https://acme.example/old-listing", first_seen="2026-09-01")
    _insert_posting(
        empty_db, url="https://acme.example/new-listing", first_seen="2026-09-15",
        user_status="open", user_status_at="2026-09-16T08:00:00",
    )

    run(backfill(empty_db, titles_doc=TITLES_DOC, locations_setting=LOCATIONS, now_iso="2026-09-25T08:00:00"))

    older = run(empty_db.first("SELECT * FROM postings WHERE id = ?", older_id))
    assert older["user_status"] == "open"
    assert older["user_status_at"] == "2026-09-16T08:00:00"


def test_backfill_never_deletes_a_posting(empty_db):
    _insert_posting(empty_db, url="https://acme.example/old-listing", first_seen="2026-09-01")
    _insert_posting(empty_db, url="https://acme.example/new-listing", first_seen="2026-09-15")

    run(backfill(empty_db, titles_doc=TITLES_DOC, locations_setting=LOCATIONS, now_iso="2026-09-25T08:00:00"))

    assert len(run(empty_db.all("SELECT * FROM postings"))) == 2


def test_backfill_running_twice_changes_nothing(empty_db):
    older_id = _insert_posting(empty_db, url="https://acme.example/old-listing", first_seen="2026-09-01")
    _insert_posting(empty_db, url="https://acme.example/new-listing", first_seen="2026-09-15")

    first = run(backfill(
        empty_db, titles_doc=TITLES_DOC, locations_setting=LOCATIONS, now_iso="2026-09-25T08:00:00",
    ))
    assert first["duplicates_archived"] == 1

    second = run(backfill(
        empty_db, titles_doc=TITLES_DOC, locations_setting=LOCATIONS, now_iso="2026-09-26T08:00:00",
    ))
    assert second["duplicates_archived"] == 0
    assert second["fingerprinted"] == 0

    fingerprints = run(empty_db.all("SELECT * FROM posting_fingerprints"))
    assert len(fingerprints) == 1
    assert fingerprints[0]["posting_id"] == older_id
