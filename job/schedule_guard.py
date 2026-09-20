"""Guard for `.github/workflows/daily-search.yml`.

Only lets the scheduled run through at 08:00 America/Chicago; manual runs
(`workflow_dispatch`) always go through, skipping the guard. Written as a
plain Python function so daylight saving time transitions are covered by
pytest instead of only being checked by eye in the Actions log.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - zoneinfo is stdlib on 3.9+
    ZoneInfo = None  # type: ignore

CENTRAL = ZoneInfo("America/Chicago") if ZoneInfo else None


def should_run_now(event_name: str, now: datetime | None = None) -> bool:
    """True if the daily job should run now.

    `now`, if given, may be timezone-aware (any timezone) or naive (assumed
    to already be America/Chicago local time, for tests that don't want to
    depend on `zoneinfo`).
    """
    if event_name == "workflow_dispatch":
        return True
    if now is None:
        now = datetime.now(CENTRAL) if CENTRAL else datetime.utcnow()
    elif now.tzinfo is not None and CENTRAL is not None:
        now = now.astimezone(CENTRAL)
    return now.hour == 8


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
