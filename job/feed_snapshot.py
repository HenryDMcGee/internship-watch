"""Skips re-processing a company's feed when its content hasn't changed
since the last run (issue #51, phase 5 part 2).

A feed is fetched and turned into `FetchedPosting`s every day regardless --
that HTTP call, and every posting's daily status check, are unaffected. What
this module lets `job/run.py` skip is the expensive part after the fetch:
matching each posting against remembered fingerprints, classifying it
against the active titles, and writing it to `postings`.

The hash is computed from each posting's meaningful fields (external id,
title, url, location, posted date) rather than the vendor's raw JSON, so
fields that change on every request without any real content change --
a response-only timestamp, a request id, per-job metadata nothing here
reads -- never look like a change. Sorting by a stable key first means a
feed that just re-orders its listing doesn't look like a change either.
"""
from __future__ import annotations

import hashlib
import json

from job.models import FetchedPosting


class _AllFound:
    """Stands in for `current_urls[company_id]` when a feed was skipped as
    unchanged: since nothing in the feed changed, every posting already on
    file for that company is still there, so the feed-based status check
    below (`url in current_urls[...]`) must never report a disappearance
    for it. A feed that changed still gets the real fetched url set, so a
    posting missing from it is still caught immediately.
    """

    def __contains__(self, _url: object) -> bool:
        return True


ALL_FOUND = _AllFound()


def _sort_key(fields: dict) -> str:
    return fields["external_id"] or fields["url"] or fields["title"] or ""


def normalize_postings(postings: list[FetchedPosting]) -> list[dict]:
    fields = [
        {
            "external_id": p.external_id,
            "title": p.title,
            "url": p.url,
            "location_text": p.location_text,
            "posted_date": p.posted_date,
        }
        for p in postings
    ]
    return sorted(fields, key=_sort_key)


def hash_postings(postings: list[FetchedPosting]) -> str:
    encoded = json.dumps(normalize_postings(postings), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def load_snapshot(db, company_id: int) -> dict | None:
    return await db.first("SELECT * FROM feed_snapshots WHERE company_id = ?", company_id)


async def record_unchanged(db, company_id: int, *, now_iso: str) -> None:
    await db.run("UPDATE feed_snapshots SET last_checked = ? WHERE company_id = ?", now_iso, company_id)


async def record_changed(db, company_id: int, content_hash: str, *, now_iso: str, existing: dict | None) -> None:
    if existing:
        await db.run(
            "UPDATE feed_snapshots SET content_hash = ?, last_changed = ?, last_checked = ? WHERE company_id = ?",
            content_hash, now_iso, now_iso, company_id,
        )
    else:
        await db.run(
            "INSERT INTO feed_snapshots (company_id, content_hash, last_changed, last_checked) VALUES (?, ?, ?, ?)",
            company_id, content_hash, now_iso, now_iso,
        )
