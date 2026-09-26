"""One-time backfill: fingerprint every existing posting, including
archived ones, and archive any duplicate this turns up (issue #49).

Run once, from the repository root, after applying migration
0007_posting_fingerprints.sql, using the same D1 HTTP API credentials as
the daily job (`CLOUDFLARE_D1_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`,
`D1_DATABASE_ID`):

    python -m scripts.backfill_fingerprints             # writes
    python -m scripts.backfill_fingerprints --dry-run   # report only

Safe to run more than once: a posting that already has a fingerprint, or is
already archived as a duplicate, is left alone the second time. Postings
are never deleted -- a duplicate is archived, with the older copy kept.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job import config, fingerprint  # noqa: E402
from job.d1_client import D1HttpDatabase  # noqa: E402

_DUPLICATE_PREFIX = "duplicate of #"


def _location_text(posting: dict) -> str:
    locations = posting.get("locations")
    if isinstance(locations, str):
        try:
            locations = json.loads(locations)
        except (TypeError, ValueError):
            locations = []
    if isinstance(locations, list):
        return ", ".join(locations)
    return ""


async def backfill(
    db, *, titles_doc: dict, locations_setting: dict, now_iso: str, dry_run: bool = False,
) -> dict:
    settings_version = fingerprint.settings_version(titles_doc, locations_setting)
    postings = await db.all("SELECT * FROM postings ORDER BY id ASC")

    fingerprinted = 0
    duplicates_archived = 0
    already_done = 0

    for posting in postings:
        if (posting.get("archive_reason") or "").startswith(_DUPLICATE_PREFIX):
            already_done += 1
            continue

        existing_fp = await db.first(
            "SELECT id FROM posting_fingerprints WHERE posting_id = ?", posting["id"]
        )
        if existing_fp:
            already_done += 1
            continue

        fp = fingerprint.compute_fingerprint(
            company_name=posting.get("company_name") or "", title=posting.get("title") or "",
            location_text=_location_text(posting), url=posting.get("url"),
        )
        match = await fingerprint.find_match(db, fp)

        if match and match.get("posting_id") is not None and match["posting_id"] != posting["id"]:
            older_id = match["posting_id"]
            duplicates_archived += 1
            print(f"backfill: posting #{posting['id']} is a duplicate of #{older_id}, archiving it")
            if not dry_run:
                older = await db.first("SELECT * FROM postings WHERE id = ?", older_id)
                updates: dict = {}
                if posting.get("applied") and not older.get("applied"):
                    updates["applied"] = 1
                if posting.get("user_status") and not older.get("user_status"):
                    updates["user_status"] = posting.get("user_status")
                    updates["user_status_at"] = posting.get("user_status_at")
                if updates:
                    set_clause = ", ".join(f"{key} = ?" for key in updates)
                    await db.run(
                        f"UPDATE postings SET {set_clause} WHERE id = ?", *updates.values(), older_id,
                    )
                await db.run(
                    "UPDATE postings SET archived_at = COALESCE(archived_at, ?), archive_reason = ? WHERE id = ?",
                    now_iso, f"{_DUPLICATE_PREFIX}{older_id}", posting["id"],
                )
                await fingerprint.touch(db, match["id"], source="backfill", now_iso=now_iso)
            continue

        fingerprinted += 1
        if not dry_run:
            await fingerprint.record_posting(
                db, fp, posting_id=posting["id"], settings_version=settings_version,
                source="backfill", now_iso=posting.get("first_seen") or now_iso,
            )

    return {
        "postings_seen": len(postings),
        "fingerprinted": fingerprinted,
        "duplicates_archived": duplicates_archived,
        "already_done": already_done,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would happen without writing anything"
    )
    args = parser.parse_args(argv)

    db = D1HttpDatabase.from_env()

    async def _main() -> dict:
        titles_doc = config.load_titles()
        locations_setting = await config.load_locations_setting(db)
        now_iso = datetime.now(timezone.utc).isoformat()
        return await backfill(
            db, titles_doc=titles_doc, locations_setting=locations_setting, now_iso=now_iso,
            dry_run=args.dry_run,
        )

    result = asyncio.run(_main())
    print(
        f"backfill: {result['postings_seen']} posting(s) seen, {result['fingerprinted']} fingerprinted, "
        f"{result['duplicates_archived']} duplicate(s) archived, {result['already_done']} already had a "
        "fingerprint or were already archived as a duplicate."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
