"""The one place a new posting is saved (issue #68).

Feeds, Claude discovery, careers pages, Check now, and restored rejections all
create postings through `save_posting`. A posting is saved only if it matched an
active title or alias (`matched_title`), whether the plain rules matched it or
classification did. Anything else is remembered as a rejection and never saved,
so a posting with no title match can't reach the dashboard through any path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from job import fingerprint
from job.config import active_titles

NO_TITLE_REASON = "no matching title"
CLASSIFICATION_METHOD = "classification"


@dataclass
class Candidate:
    """A result the plain rules couldn't place (fuzzy score 80 to 89, or a title
    in another language). It exists only in memory: it is saved, through
    `save_posting`, only if classification matches it to an active title."""
    id: int  # position in this run's batch; what the classification prompt refers to
    company_name: str
    title: str
    url: str | None
    source_name: str | None
    location_text: str
    posted_date: str | None
    fp: "fingerprint.Fingerprint"
    fingerprint_source: str
    stale_fingerprint_id: int | None = None
    company_id: int | None = None
    event_detail: dict = field(default_factory=dict)

    def prompt_row(self) -> dict:
        return {
            "id": self.id, "company_name": self.company_name, "title": self.title,
            "location_text": self.location_text,
        }


def canonical_active_title(matched_title: str | None, titles_doc: dict) -> str | None:
    """The active title in `titles_doc` that `matched_title` names, or None.
    An alias or a made-up title is not a title; callers pass the canonical one."""
    wanted = (matched_title or "").strip().lower()
    if not wanted:
        return None
    for entry in active_titles(titles_doc):
        if entry["title"].strip().lower() == wanted:
            return entry["title"]
    return None


async def save_posting(
    db, *, titles_doc: dict, matched_title: str | None, match_method: str | None,
    company_name: str, title: str, url: str | None, source_name: str | None,
    locations: list[str], location_match: str, posted_date: str | None, first_seen: str,
    fp: "fingerprint.Fingerprint", settings_version: str, fingerprint_source: str, now_iso: str,
    stale_fingerprint_id: int | None = None, company_id: int | None = None,
    title_en: str | None = None, event_detail: dict | None = None,
) -> int | None:
    """Saves a new `unverified` posting and returns its id, or returns None
    (saving nothing, remembering the rejection) when `matched_title` is not an
    active title."""
    canonical = canonical_active_title(matched_title, titles_doc)
    if canonical is None:
        await fingerprint.record_rejection(
            db, fp, reason=NO_TITLE_REASON, settings_version=settings_version, source=fingerprint_source,
            now_iso=now_iso, existing_id=stale_fingerprint_id,
        )
        return None

    new_id = await db.run(
        """INSERT INTO postings
           (company_id, company_name, title, title_en, matched_title, match_method, url, source_name,
            locations, location_match, posted_date, first_seen, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unverified')""",
        company_id, company_name, title, title_en, canonical, match_method or CLASSIFICATION_METHOD, url,
        source_name, json.dumps(locations), location_match, posted_date, first_seen,
    )
    await db.run(
        "INSERT INTO posting_events (posting_id, type, detail, at) VALUES (?, 'found', ?, ?)",
        new_id, json.dumps(event_detail or {}), now_iso,
    )
    await fingerprint.record_posting(
        db, fp, posting_id=new_id, settings_version=settings_version, source=fingerprint_source,
        now_iso=now_iso, existing_id=stale_fingerprint_id,
    )
    return new_id
