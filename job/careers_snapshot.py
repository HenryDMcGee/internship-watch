"""Remembers each company's last careers-page content and check outcome, so
an unchanged page is skipped with no Claude call (issue #53, phase 6 part
1) -- the `careers_page_snapshots` equivalent of `job/feed_snapshot.py`.

Unlike a feed snapshot, a careers page can *fail* to load at all (blocked,
disallowed, timed out, empty, or a Browser Run/backup error). `record_failure`
keeps a running `consecutive_failures` count for that -- reset to 0 by any
successful check (`record_changed`/`record_unchanged`) -- as a quality signal
for later use; it does not by itself change what today's run does with the
company (`job/run.py` decides that from the page result directly).
"""
from __future__ import annotations

import hashlib
import re

# After this many failed checks in a row, job/run.py flags the company as
# "careers page not working" (companies.careers_page_flagged) for the
# Sources tab and the repair scout (issue #55).
FLAG_AFTER_CONSECUTIVE_FAILURES = 3


def hash_page_text(text: str) -> str:
    """A stable fingerprint of a careers page's visible text: whitespace
    collapsed so reflowed layout alone doesn't look like a content change,
    matching `job/feed_snapshot.py`'s reasoning for feeds.
    """
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def load_snapshot(db, company_id: int) -> dict | None:
    return await db.first("SELECT * FROM careers_page_snapshots WHERE company_id = ?", company_id)


async def record_unchanged(db, company_id: int, *, method: str, now_iso: str) -> None:
    await db.run(
        "UPDATE careers_page_snapshots SET last_checked = ?, status = 'unchanged', method = ?, "
        "consecutive_failures = 0, last_error = NULL WHERE company_id = ?",
        now_iso, method, company_id,
    )


async def record_changed(
    db, company_id: int, content_hash: str, *, method: str, now_iso: str, existing: dict | None,
) -> None:
    if existing:
        await db.run(
            "UPDATE careers_page_snapshots SET content_hash = ?, last_changed = ?, last_checked = ?, "
            "status = 'changed', method = ?, consecutive_failures = 0, last_error = NULL WHERE company_id = ?",
            content_hash, now_iso, now_iso, method, company_id,
        )
    else:
        await db.run(
            "INSERT INTO careers_page_snapshots "
            "(company_id, content_hash, last_changed, last_checked, status, method, consecutive_failures) "
            "VALUES (?, ?, ?, ?, 'changed', ?, 0)",
            company_id, content_hash, now_iso, now_iso, method,
        )


async def record_failure(db, company_id: int, *, reason: str, now_iso: str, existing: dict | None) -> int:
    """Returns the new `consecutive_failures` count, so a caller (job/run.py)
    can flag the company once it reaches the "careers page not working"
    threshold (issue #55) without a second read of this row.
    """
    consecutive = ((existing or {}).get("consecutive_failures") or 0) + 1
    if existing:
        await db.run(
            "UPDATE careers_page_snapshots SET last_checked = ?, status = 'failed', "
            "consecutive_failures = ?, last_error = ? WHERE company_id = ?",
            now_iso, consecutive, reason, company_id,
        )
    else:
        await db.run(
            "INSERT INTO careers_page_snapshots (company_id, last_checked, status, consecutive_failures, last_error) "
            "VALUES (?, ?, 'failed', ?, ?)",
            company_id, now_iso, consecutive, reason,
        )
    return consecutive
