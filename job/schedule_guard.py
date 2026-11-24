"""Guard step for `.github/workflows/daily-search.yml`.

Every run goes through. The guard used to let a scheduled run through only
during the 08:00 hour in America/Chicago, but GitHub delivers the schedule 4
to 8.5 hours late, so that check turned every scheduled run away. The run
that fires is now the day's run, whenever GitHub gets to it; the Worker's
cron trigger (`api/src/cron.py`) is what starts the workflow on time.

The step stays because every later step of the workflow reads its
`should_run` output. If a time rule is ever wanted again, this is the place
for it, with pytest covering both halves of the year.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime


def should_run_now(event_name: str, now: datetime | None = None) -> bool:
    """True if the daily job should run now, which is always. `event_name`
    and `now` are kept so a time rule can come back without changing callers.
    """
    return True


def main() -> int:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    result = should_run_now(event_name)
    line = f"should_run={'true' if result else 'false'}"
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
