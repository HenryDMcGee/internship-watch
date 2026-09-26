"""Browser Run usage summary for the Budgets panel (issue #55): today's
minutes against the job's own daily budget (Central boundary, matching
every other budget here), Cloudflare's own free-tier daily allowance (its
own reset boundary), the Paid plan's included monthly hours and overage,
today's backup usage and why it ran, a 7-day series, and the last 10 runs.

Self-contained rather than importing from `job/`, matching `budgets.py`'s
reasoning: the deployed Worker only ships `api/src` (see `api/wrangler.toml`).
`job/run.py` writes the `browser_run_usage` rows this reads; its own
`BROWSER_RUN_PAID_INCLUDED_MONTHLY_HOURS` constant is duplicated here for
the same reason.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - zoneinfo is stdlib on 3.9+
    ZoneInfo = None  # type: ignore

CENTRAL = ZoneInfo("America/Chicago") if ZoneInfo else None

CLOUDFLARE_FREE_DAILY_MINUTES = 10.0
PAID_INCLUDED_MONTHLY_HOURS = 10.0
OVERAGE_USD_PER_HOUR = 0.09

_BACKUP_REASON_TEXT = {
    "minutes": "Browser Run daily minutes reached",
    "quota": "Browser Run quota exceeded",
    "outage": "Browser Run had an outage",
    "missing_token": "Browser Run isn't configured",
}


def _parse_at(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None and CENTRAL is not None:
        dt = dt.replace(tzinfo=CENTRAL)
    return dt


def cloudflare_day_key(at: datetime) -> str:
    """Which of Cloudflare's own free-allowance days `at` falls into.

    Cloudflare's current Browser Rendering docs weren't reachable while
    building this (see `job/fetchers/careers_pages.py`'s module docstring
    for the same gap) -- this assumes the free daily allowance resets at UTC
    midnight, the default every other Cloudflare daily quota uses. Flagged
    here for Henry to confirm against Cloudflare's own docs.
    """
    return at.astimezone(timezone.utc).date().isoformat()


def cloudflare_next_reset_central(now_utc: datetime) -> datetime:
    """The next UTC-midnight reset after `now_utc`, in Central time for display."""
    tomorrow = now_utc.astimezone(timezone.utc).date() + timedelta(days=1)
    reset_utc = datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc)
    return reset_utc.astimezone(CENTRAL) if CENTRAL else reset_utc


def summarize(rows: list[dict[str, Any]], *, settings: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Builds the Budgets panel's `browser_run` section from raw
    `browser_run_usage` rows (already the small number that matter -- the
    last 8 days' worth is plenty; callers aren't expected to hand this the
    whole table).
    """
    plan = settings.get("browser_run_plan") or "free"
    daily_budget = float(settings.get("browser_run_daily_minutes") or 9.0)

    today_central = now.astimezone(CENTRAL).date() if CENTRAL else now.date()
    today_key = today_central.isoformat()
    month_start_key = today_central.replace(day=1).isoformat()
    cloudflare_today_key = cloudflare_day_key(now)

    minutes_used_today = 0.0
    hours_used_month = 0.0
    cloudflare_minutes_used_today = 0.0
    backup_pages_today = 0
    backup_reasons_today: set[str] = set()
    series_by_day: dict[str, float] = {}
    runs_seen: dict[int, dict[str, Any]] = {}

    for row in rows:
        at = _parse_at(row["at"])
        day_key = at.astimezone(CENTRAL).date().isoformat() if CENTRAL else at.date().isoformat()
        method = row["method"]
        browser_ms = row.get("browser_ms") or 0

        if method == "browser_run":
            if day_key == today_key:
                minutes_used_today += browser_ms / 60000.0
            if day_key >= month_start_key and day_key <= today_key:
                hours_used_month += browser_ms / 3600000.0
            if cloudflare_day_key(at) == cloudflare_today_key:
                cloudflare_minutes_used_today += browser_ms / 60000.0
            series_by_day[day_key] = series_by_day.get(day_key, 0.0) + browser_ms / 60000.0
        elif method == "backup" and day_key == today_key:
            backup_pages_today += row.get("pages") or 0
            if row.get("backup_reason"):
                backup_reasons_today.add(row["backup_reason"])

        run_id = row.get("run_id")
        if run_id is not None:
            entry = runs_seen.setdefault(run_id, {
                "run_id": run_id, "at": row["at"],
                "browser_run_pages": 0, "browser_run_minutes": 0.0, "backup_pages": 0, "failures": 0,
            })
            entry["at"] = min(entry["at"], row["at"])
            if method == "browser_run":
                entry["browser_run_pages"] += row.get("pages") or 0
                entry["browser_run_minutes"] += browser_ms / 60000.0
            else:
                entry["backup_pages"] += row.get("pages") or 0
            entry["failures"] += row.get("failures") or 0

    series_7d = []
    for offset in range(6, -1, -1):
        d = (today_central - timedelta(days=offset)).isoformat()
        series_7d.append({"date": d, "minutes": round(series_by_day.get(d, 0.0), 2)})

    last_runs = sorted(runs_seen.values(), key=lambda r: r["at"], reverse=True)[:10]
    for r in last_runs:
        r["browser_run_minutes"] = round(r["browser_run_minutes"], 2)

    month = None
    if plan == "paid":
        hours_left = max(0.0, PAID_INCLUDED_MONTHLY_HOURS - hours_used_month)
        overage_hours = max(0.0, hours_used_month - PAID_INCLUDED_MONTHLY_HOURS)
        month = {
            "hours_used": round(hours_used_month, 2),
            "hours_included": PAID_INCLUDED_MONTHLY_HOURS,
            "hours_left": round(hours_left, 2),
            "overage_usd": round(overage_hours * OVERAGE_USD_PER_HOUR, 2),
        }

    return {
        "plan": plan,
        "today": {
            "minutes_used": round(minutes_used_today, 2),
            "minutes_budget": daily_budget,
            "minutes_left": round(max(0.0, daily_budget - minutes_used_today), 2),
        },
        "cloudflare_free": {
            "daily_minutes": CLOUDFLARE_FREE_DAILY_MINUTES,
            "minutes_used": round(cloudflare_minutes_used_today, 2),
            "reset_at": cloudflare_next_reset_central(now).isoformat(),
        },
        "month": month,
        "backup_today": {
            "pages": backup_pages_today,
            "pages_cap": int(settings.get("careers_backup_pages_per_run") or 40),
            "reasons": [_BACKUP_REASON_TEXT.get(r, r) for r in sorted(backup_reasons_today)],
        },
        "series_7d": series_7d,
        "last_runs": last_runs,
    }
