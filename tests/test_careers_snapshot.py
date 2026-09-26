"""job/careers_snapshot.py: change detection and consecutive-failure tracking
for careers pages (issue #53, phase 6 part 1) -- the careers_page_snapshots
equivalent of tests/test_feed_snapshot.py.
"""
import asyncio

from job import careers_snapshot


def run(coro):
    return asyncio.run(coro)


def test_hash_ignores_whitespace_reflow():
    a = careers_snapshot.hash_page_text("PM Intern\n\nSan Francisco")
    b = careers_snapshot.hash_page_text("PM Intern   San Francisco")
    assert a == b


def test_hash_changes_when_text_changes():
    a = careers_snapshot.hash_page_text("PM Intern")
    b = careers_snapshot.hash_page_text("Senior PM Intern")
    assert a != b


def test_record_changed_then_unchanged_resets_failures(empty_db):
    run(empty_db.run("INSERT INTO companies (id, name, status) VALUES (1, 'Acme', 'pinned')"))
    run(careers_snapshot.record_failure(empty_db, 1, reason="timeout", now_iso="2026-09-01T08:00:00", existing=None))
    row = run(careers_snapshot.load_snapshot(empty_db, 1))
    assert row["consecutive_failures"] == 1
    assert row["status"] == "failed"
    assert row["last_error"] == "timeout"

    run(careers_snapshot.record_failure(empty_db, 1, reason="timeout", now_iso="2026-09-02T08:00:00", existing=row))
    row = run(careers_snapshot.load_snapshot(empty_db, 1))
    assert row["consecutive_failures"] == 2

    run(careers_snapshot.record_changed(empty_db, 1, "hash-1", method="browser_run", now_iso="2026-09-03T08:00:00", existing=row))
    row = run(careers_snapshot.load_snapshot(empty_db, 1))
    assert row["consecutive_failures"] == 0
    assert row["status"] == "changed"
    assert row["content_hash"] == "hash-1"
    assert row["last_error"] is None

    run(careers_snapshot.record_unchanged(empty_db, 1, method="browser_run", now_iso="2026-09-04T08:00:00"))
    row = run(careers_snapshot.load_snapshot(empty_db, 1))
    assert row["status"] == "unchanged"
    assert row["content_hash"] == "hash-1"  # untouched by an unchanged check
