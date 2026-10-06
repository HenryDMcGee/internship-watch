"""Cleanup of postings that were saved without a matched title (issue #68).

Before the fix, discovery and careers pages saved postings first and classified
them later, so a posting that matched no title (for example Apple's "AMS Lab
Validation Intern") could stay on the dashboard. This re-judges every posting
first seen on or after 2026-10-01 that has no `matched_title`:

  * if the current rules match an active title, the title is filled in and the
    posting stays;
  * otherwise it is archived with the reason "no matching title (cleanup)".

Never touched: postings first seen before 2026-10-01 (which includes the original
seeded postings), and any posting Henry marked applied, open, or closed. Nothing is
deleted. Safe to run more than once. The daily job runs it before reading feeds,
or by hand:

    python -m job.cleanup_unmatched             # writes
    python -m job.cleanup_unmatched --dry-run   # report only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

from job import config
from job.matching import classify_posting

CLEANUP_REASON = "no matching title (cleanup)"
CLEANUP_FIRST_SEEN = "2026-10-01"
MAX_EXAMPLES = 15


async def cleanup_unmatched(db, *, titles_doc: dict, now_iso: str, dry_run: bool = False) -> dict:
    rows = await db.all(
        "SELECT id, company_name, title, first_seen FROM postings "
        "WHERE first_seen >= ? AND matched_title IS NULL AND archived_at IS NULL "
        "AND applied = 0 AND user_status IS NULL ORDER BY id",
        CLEANUP_FIRST_SEEN,
    )
    result = {"considered": len(rows), "archived": 0, "matched": 0, "examples": []}
    for row in rows:
        decision = classify_posting(row["title"] or "", "", titles_doc)
        if decision.matched_title and decision.decision in ("match", "unclear"):
            result["matched"] += 1
            if not dry_run:
                await db.run(
                    "UPDATE postings SET matched_title = ?, match_method = ? WHERE id = ?",
                    decision.matched_title, decision.match_method, row["id"],
                )
            continue

        result["archived"] += 1
        if len(result["examples"]) < MAX_EXAMPLES:
            result["examples"].append({
                "id": row["id"], "company": row["company_name"], "title": row["title"],
                "first_seen": row["first_seen"],
            })
        if not dry_run:
            await db.run(
                "UPDATE postings SET archived_at = ?, archive_reason = ? WHERE id = ?",
                now_iso, CLEANUP_REASON, row["id"],
            )
            await db.run(
                "INSERT INTO posting_events (posting_id, type, detail, at) VALUES (?, 'archived', ?, ?)",
                row["id"], json.dumps({"reason": CLEANUP_REASON, "rules_said": decision.reason}), now_iso,
            )
    return result


def summary_line(result: dict) -> str | None:
    if not result["archived"] and not result["matched"]:
        return None
    line = (
        f"Cleanup: archived {result['archived']} posting(s) with no matching title"
        + (f", kept {result['matched']} that match a title" if result["matched"] else "")
        + "."
    )
    examples = [e["title"] for e in result["examples"][:3]]
    return line + (f" For example: {'; '.join(examples)}." if examples else "")


def main(argv: list[str] | None = None) -> int:
    from job.d1_client import D1HttpDatabase

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen without writing anything")
    args = parser.parse_args(argv)
    db = D1HttpDatabase.from_env()

    result = asyncio.run(cleanup_unmatched(
        db, titles_doc=config.load_titles(), now_iso=datetime.now(timezone.utc).isoformat(), dry_run=args.dry_run,
    ))
    print(summary_line(result) or "No postings without a matched title since 2026-10-01.")
    for example in result["examples"]:
        print(f"  #{example['id']} {example['company']}: {example['title']!r} (first seen {example['first_seen']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
