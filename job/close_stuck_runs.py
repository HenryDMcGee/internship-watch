"""Closes a daily run that was stopped before it could record its own end
(issue #70): the Actions job hit its time limit or was cancelled, so the
`runs` row is still "running" with no finish time.

    python -m job.close_stuck_runs --run-minutes 60

`daily-search.yml` runs this in a final step with `if: always()`. It also
sends the daily summary with a failed status, so the stop is never silent.
Does nothing when no run is left "running".
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from job.d1_client import D1HttpDatabase
from job import run as run_module


async def close_stuck_runs(db, *, reason: str, now_iso: str | None = None) -> int:
    now = now_iso or run_module.now_iso()
    rows = await db.all("SELECT id, kind, started_at FROM runs WHERE status = 'running' AND finished_at IS NULL")
    for row in rows:
        minutes = run_module._minutes_between(row["started_at"], now)
        took = f" after {minutes:.0f} minutes" if minutes is not None else ""
        summary = f"Run stopped{took}: {reason}. Postings saved before it stopped are kept."
        await db.run(
            "UPDATE runs SET finished_at = ?, status = 'failed', summary = ? WHERE id = ?",
            now, summary, row["id"],
        )
        await db.run(
            "UPDATE search_requests SET status = 'failed', result = ? WHERE run_id = ? AND status = 'running'",
            json.dumps({"error": reason}), row["id"],
        )
        if row["kind"] == "daily":
            await run_module._finish_daily_run(
                db, run_status="failed", run_reason=summary, run_minutes=minutes,
            )
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reason", default="the job hit its time limit or was cancelled")
    args = parser.parse_args(argv)
    closed = asyncio.run(close_stuck_runs(D1HttpDatabase.from_env(), reason=args.reason))
    print(f"Closed {closed} stuck run(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
