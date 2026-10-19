"""The Worker's cron trigger: starts the daily search at 08:00 America/Chicago.

Cloudflare fires the two UTC crons in `api/wrangler.toml` (13:00 and 14:00)
every day and calls `Default.scheduled` in `main.py` for each. Exactly one
of them is 08:00 Central on any given day: 13:00 UTC under daylight time,
14:00 UTC under standard time. `should_dispatch` picks it, and
`run_scheduled` then POSTs a `workflow_dispatch` for `daily-search.yml` so
GitHub Actions runs the sweep (the Worker only keeps time; it has no
Chromium and no 40 minutes of CPU).

Daylight saving is done with date arithmetic rather than a timezone
database. US law fixes it at 02:00 local on the second Sunday in March to
02:00 local on the first Sunday in November, so the switch is a known UTC
instant each year and needs no data file. Everything here is plain Python
so pytest covers it without the Workers runtime.

The dispatch token is the `GITHUB_DISPATCH_TOKEN` Worker secret, separate
from the dashboard's `GITHUB_TOKEN` and scoped to Actions: write only.
Nothing on the HTTP path (`Default.fetch`) calls this module.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from github_client import GitHubClient, Transport, _default_transport

logger = logging.getLogger("internship_watch.cron")

REPO = "HenryDMcGee/internship-watch"
WORKFLOW_FILE = "daily-search.yml"
BRANCH = "master"
# Phase 4's schedule guard exempts only `run_source: manual`, and the workflow
# input defaults to `manual` when the field is missing. Always send it.
DISPATCH_INPUTS: dict[str, str] = {"run_source": "cloudflare"}

# Must match api/wrangler.toml's `crons` character for character: Cloudflare
# hands the handler the configured string and the comparison is textual.
DAYLIGHT_CRON = "0 13 * * *"  # 08:00 CDT (UTC-5)
STANDARD_CRON = "0 14 * * *"  # 08:00 CST (UTC-6)

UTC = timezone.utc
_SWITCH_HOUR_LOCAL = 2  # both transitions happen at 02:00 local time
_STANDARD_OFFSET_HOURS = 6  # CST is UTC-6
_DAYLIGHT_OFFSET_HOURS = 5  # CDT is UTC-5


def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    first_sunday = first + timedelta(days=(6 - first.weekday()) % 7)
    return first_sunday + timedelta(days=7 * (n - 1))


def _as_utc(when: datetime) -> datetime:
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)


def is_daylight_saving(when: datetime) -> bool:
    """True if US Central time is on daylight time at the UTC instant `when`.

    Starts 02:00 CST on the second Sunday in March (08:00 UTC) and ends
    02:00 CDT on the first Sunday in November (07:00 UTC). A naive datetime
    is taken as UTC.
    """
    when = _as_utc(when)
    start_day = _nth_sunday(when.year, 3, 2)
    end_day = _nth_sunday(when.year, 11, 1)
    start = datetime(start_day.year, start_day.month, start_day.day,
                     _SWITCH_HOUR_LOCAL + _STANDARD_OFFSET_HOURS, tzinfo=UTC)
    end = datetime(end_day.year, end_day.month, end_day.day,
                   _SWITCH_HOUR_LOCAL + _DAYLIGHT_OFFSET_HOURS, tzinfo=UTC)
    return start <= when < end


def normalize_cron(cron: str) -> str:
    return " ".join((cron or "").split())


def should_dispatch(cron: str, when: datetime) -> bool:
    """True if the cron that fired is the one that equals 08:00 Central at `when`."""
    wanted = DAYLIGHT_CRON if is_daylight_saving(when) else STANDARD_CRON
    return normalize_cron(cron) == wanted


def scheduled_time(scheduled_ms: Any) -> datetime:
    """`controller.scheduledTime` (milliseconds since the epoch, UTC) as a datetime; now if absent."""
    if scheduled_ms is None:
        return datetime.now(UTC)
    return datetime.fromtimestamp(float(scheduled_ms) / 1000.0, tz=UTC)


async def run_scheduled(
    cron: str,
    when: datetime,
    *,
    token: str,
    transport: Transport = _default_transport,
) -> dict:
    """Decides, dispatches if due, and returns what happened.

    Raises when a dispatch was due but could not be made: a missing token
    (`RuntimeError`) or a non-2xx answer from GitHub (`GitHubApiError`).
    Either one fails the cron invocation so it shows as an error in
    Cloudflare's Past Events rather than looking like a quiet day.
    """
    when = _as_utc(when)
    cron = normalize_cron(cron)
    due = should_dispatch(cron, when)
    period = "daylight" if is_daylight_saving(when) else "standard"
    result: dict = {"cron": cron, "at": when.isoformat(), "period": period, "dispatched": False, "status": None}
    if not due:
        logger.info("Cron %r at %s (%s time) is not 08:00 Central; nothing to do", cron, when.isoformat(), period)
        return result

    if not token:
        logger.error("Cron %r at %s is 08:00 Central but GITHUB_DISPATCH_TOKEN is not set; no dispatch", cron, when.isoformat())
        raise RuntimeError("GITHUB_DISPATCH_TOKEN is not set; cannot dispatch the daily search")

    client = GitHubClient(repo=REPO, token=token, transport=transport)
    logger.info("Cron %r at %s (%s time) is 08:00 Central; dispatching %s on %s", cron, when.isoformat(), period, WORKFLOW_FILE, BRANCH)
    try:
        status = await client.dispatch_workflow(WORKFLOW_FILE, BRANCH, dict(DISPATCH_INPUTS))
    except Exception as exc:
        logger.error("Dispatch of %s failed: %s", WORKFLOW_FILE, exc)
        raise
    logger.info("Dispatch of %s accepted by GitHub (%s)", WORKFLOW_FILE, status)
    result.update(dispatched=True, status=status)
    return result
