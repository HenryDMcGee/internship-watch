"""Re-judges remembered rejections that the old location, hiring-cycle, and
title rules got wrong (issue #60).

`posting_fingerprints` remembers every rejected result so it isn't judged
again. A rejection whose reason mentions the location, the hiring cycle, or
"no active title" is judged again here under the current rules
(`job.matching.reconsider_rejection`):

  * Rejected by Claude classification, so an archived row is still in
    `postings`: the row is restored (unarchived, matched title and
    location_match filled in) and the fingerprint points at it again.
  * Rejected by the plain feed rules, so no row was ever saved: the
    remembered rejection is marked stale and the company's feed snapshot is
    cleared, so the next feed read judges the result again with its real
    title and location and saves it.

Rejections that are still correct (product design, product marketing, a
clearly earlier cycle, a clearly outside location) are left alone. Postings
are never deleted. Safe to run more than once. Run by the daily job before
it reads the feeds, or by hand:

    python -m job.restore_rejections             # writes
    python -m job.restore_rejections --dry-run   # report only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

from job import config, fingerprint
from job.matching import is_recoverable_reason, reconsider_rejection

_CLAUDE_PREFIX = "Claude classification:"
_STALE_VERSION = "restore-rejections"


def _posting_location_text(posting: dict) -> str:
    locations = posting.get("locations")
    if isinstance(locations, str):
        try:
            locations = json.loads(locations)
        except (TypeError, ValueError):
            locations = []
    return ", ".join(locations) if isinstance(locations, list) else ""


def _split_title_key(key: str) -> tuple[str, str, str]:
    parts = (key or "").split("|")
    parts += [""] * (3 - len(parts))
    return parts[0], parts[1], parts[2]


async def restore_rejections(
    db, *, titles_doc: dict, locations_setting: dict, now_iso: str, dry_run: bool = False,
) -> dict:
    settings_version = fingerprint.settings_version(titles_doc, locations_setting)
    rows = await db.all(
        "SELECT * FROM posting_fingerprints WHERE rejection_reason IS NOT NULL AND posting_id IS NULL "
        "AND settings_version != ?",
        _STALE_VERSION,
    )
    archived = await db.all(
        "SELECT * FROM postings WHERE archived_at IS NOT NULL AND archive_reason LIKE ?", f"{_CLAUDE_PREFIX}%"
    )
    by_url: dict[str, dict] = {}
    by_key: dict[str, dict] = {}
    for posting in archived:
        by_url.setdefault(fingerprint.clean_url(posting.get("url")) or "", posting)
        key = fingerprint.title_key(
            posting.get("company_name") or "", posting.get("title") or "", _posting_location_text(posting)
        )
        by_key.setdefault(key, posting)

    result = {
        "considered": 0, "restored": 0, "queued_for_next_feed_read": 0, "left_rejected": 0,
        "examples": [],
    }
    for row in rows:
        reason = row.get("rejection_reason")
        if not is_recoverable_reason(reason):
            continue
        result["considered"] += 1

        posting = (by_url.get(row.get("clean_url") or "") if row.get("clean_url") else None) or by_key.get(
            row.get("title_key")
        )
        _, key_title, key_location = _split_title_key(row.get("title_key") or "")
        title_text = (posting or {}).get("title") or key_title
        location_text = _posting_location_text(posting) if posting else key_location

        recovered = reconsider_rejection(reason, title_text, location_text, titles_doc, locations_setting)
        if recovered is None:
            result["left_rejected"] += 1
            continue

        example = {
            "title": title_text, "was": reason, "location_match": recovered.location_match,
            "matched_title": recovered.matched_title, "restored_row": bool(posting),
        }
        if posting:
            result["restored"] += 1
            if not dry_run:
                await db.run(
                    "UPDATE postings SET archived_at = NULL, archive_reason = NULL, matched_title = ?, "
                    "location_match = ? WHERE id = ?",
                    recovered.matched_title, recovered.location_match, posting["id"],
                )
                await db.run(
                    "INSERT INTO posting_events (posting_id, type, detail, at) VALUES (?, 'restored', ?, ?)",
                    posting["id"], json.dumps({"was_rejected_as": reason}), now_iso,
                )
                await fingerprint.record_posting(
                    db, fingerprint.Fingerprint(
                        job_id=row.get("job_id"), clean_url=row.get("clean_url"), title_key=row["title_key"],
                    ),
                    posting_id=posting["id"], settings_version=settings_version, source="restore-rejections",
                    now_iso=now_iso, existing_id=row["id"],
                )
        else:
            result["queued_for_next_feed_read"] += 1
            if not dry_run:
                await db.run(
                    "UPDATE posting_fingerprints SET settings_version = ? WHERE id = ?", _STALE_VERSION, row["id"],
                )
        if len(result["examples"]) < 15:
            result["examples"].append(example)

    if result["queued_for_next_feed_read"] and not dry_run:
        # An unchanged feed is skipped outright, which would leave these
        # results unjudged; clearing the hash makes every feed read in full once.
        await db.run("UPDATE feed_snapshots SET content_hash = ''")
    return result


def summary_line(result: dict) -> str | None:
    if not result["considered"]:
        return None
    return (
        f"Re-judged {result['considered']} remembered rejection(s): {result['restored']} restored, "
        f"{result['queued_for_next_feed_read']} queued for the next feed read, "
        f"{result['left_rejected']} left rejected."
    )


def main(argv: list[str] | None = None) -> int:
    from job.d1_client import D1HttpDatabase

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen without writing anything")
    args = parser.parse_args(argv)
    db = D1HttpDatabase.from_env()

    async def _main() -> dict:
        titles_doc = config.load_titles()
        locations_setting = await config.load_locations_setting(db)
        return await restore_rejections(
            db, titles_doc=titles_doc, locations_setting=locations_setting,
            now_iso=datetime.now(timezone.utc).isoformat(), dry_run=args.dry_run,
        )

    result = asyncio.run(_main())
    print(summary_line(result) or "No location, cycle, or title rejections to re-judge.")
    for example in result["examples"]:
        print(f"  {example['title']!r} (was: {example['was']}) -> {example['location_match']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
