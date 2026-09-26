"""api/src/browser_run_budgets.py: the Budgets panel's Browser Run summary
(issue #55) -- today's minutes, Cloudflare's own free-allowance day
boundary, the Paid plan's monthly hours/overage, backup usage, the 7-day
series, and the last-10-runs table.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from src.browser_run_budgets import cloudflare_day_key, summarize

CENTRAL = ZoneInfo("America/Chicago")
SETTINGS = {"browser_run_plan": "free", "browser_run_daily_minutes": 9.0, "careers_backup_pages_per_run": 40}


def _row(run_id, at, method, **overrides):
    row = {
        "run_id": run_id, "at": at, "method": method, "pages": 1, "unchanged": 0, "changed": 1,
        "browser_ms": None, "measured_by": None, "failures": 0, "backup_reason": None,
    }
    row.update(overrides)
    return row


def test_today_minutes_used_and_left():
    now = datetime(2026, 9, 20, 12, 0, tzinfo=CENTRAL)
    rows = [
        _row(1, "2026-09-20T08:00:00-05:00", "browser_run", browser_ms=126000),  # 2.1 min
        _row(2, "2026-09-20T09:00:00-05:00", "browser_run", browser_ms=126000),  # 2.1 min
    ]
    out = summarize(rows, settings=SETTINGS, now=now)
    assert out["today"]["minutes_used"] == 4.2
    assert out["today"]["minutes_budget"] == 9.0
    assert out["today"]["minutes_left"] == 4.8


def test_a_run_crossing_cloudflares_utc_reset_counts_toward_the_correct_day():
    # 11:55pm Central on Sept 20 is already Sept 21 UTC (Central is behind
    # UTC) -- test case 7: this must bucket into the *next* Cloudflare day,
    # not the 20th.
    now = datetime(2026, 9, 21, 0, 30, tzinfo=CENTRAL)
    late_run_at = "2026-09-20T23:55:00-05:00"  # 2026-09-21T04:55:00 UTC
    rows = [_row(1, late_run_at, "browser_run", browser_ms=60000)]
    out = summarize(rows, settings=SETTINGS, now=now)
    at = datetime.fromisoformat(late_run_at)
    assert cloudflare_day_key(at) == "2026-09-21"
    assert cloudflare_day_key(now) == "2026-09-21"
    assert out["cloudflare_free"]["minutes_used"] == 1.0


def test_a_run_on_the_previous_cloudflare_day_does_not_count_today():
    now = datetime(2026, 9, 21, 1, 0, tzinfo=CENTRAL)  # already 2026-09-21 in UTC
    earlier_run_at = "2026-09-20T10:00:00-05:00"  # 2026-09-20T15:00:00 UTC -- previous Cloudflare day
    rows = [_row(1, earlier_run_at, "browser_run", browser_ms=600000)]
    out = summarize(rows, settings=SETTINGS, now=now)
    assert out["cloudflare_free"]["minutes_used"] == 0.0


def test_month_hours_used_and_overage_only_on_paid_plan():
    now = datetime(2026, 9, 20, 12, 0, tzinfo=CENTRAL)
    rows = [_row(1, "2026-09-05T08:00:00-05:00", "browser_run", browser_ms=int(10.5 * 3600000))]

    free_out = summarize(rows, settings=SETTINGS, now=now)
    assert free_out["month"] is None

    paid_settings = dict(SETTINGS, browser_run_plan="paid")
    paid_out = summarize(rows, settings=paid_settings, now=now)
    assert paid_out["month"]["hours_used"] == 10.5
    assert paid_out["month"]["hours_included"] == 10.0
    assert paid_out["month"]["hours_left"] == 0.0
    assert paid_out["month"]["overage_usd"] == round(0.5 * 0.09, 2)


def test_backup_today_pages_and_reasons():
    now = datetime(2026, 9, 20, 12, 0, tzinfo=CENTRAL)
    rows = [
        _row(1, "2026-09-20T08:00:00-05:00", "backup", pages=12, backup_reason="minutes"),
        _row(2, "2026-09-19T08:00:00-05:00", "backup", pages=99, backup_reason="quota"),  # a different day
    ]
    out = summarize(rows, settings=SETTINGS, now=now)
    assert out["backup_today"]["pages"] == 12
    assert out["backup_today"]["reasons"] == ["Browser Run daily minutes reached"]
    assert out["backup_today"]["pages_cap"] == 40


def test_series_7d_has_one_entry_per_day_including_zero_days():
    now = datetime(2026, 9, 20, 12, 0, tzinfo=CENTRAL)
    rows = [_row(1, "2026-09-20T08:00:00-05:00", "browser_run", browser_ms=60000)]
    out = summarize(rows, settings=SETTINGS, now=now)
    assert len(out["series_7d"]) == 7
    assert out["series_7d"][-1]["date"] == "2026-09-20"
    assert out["series_7d"][-1]["minutes"] == 1.0
    assert out["series_7d"][0]["date"] == "2026-09-14"
    assert out["series_7d"][0]["minutes"] == 0.0


def test_last_runs_lists_newest_first_with_per_method_counts():
    now = datetime(2026, 9, 20, 12, 0, tzinfo=CENTRAL)
    rows = [
        _row(1, "2026-09-18T08:00:00-05:00", "browser_run", pages=3, browser_ms=180000, failures=1),
        _row(1, "2026-09-18T08:00:00-05:00", "backup", pages=2, failures=0),
        _row(2, "2026-09-19T08:00:00-05:00", "browser_run", pages=5, browser_ms=300000, failures=0),
    ]
    out = summarize(rows, settings=SETTINGS, now=now)
    assert [r["run_id"] for r in out["last_runs"]] == [2, 1]
    run1 = next(r for r in out["last_runs"] if r["run_id"] == 1)
    assert run1["browser_run_pages"] == 3
    assert run1["browser_run_minutes"] == 3.0
    assert run1["backup_pages"] == 2
    assert run1["failures"] == 1


def test_measured_by_elapsed_is_labeled_in_the_raw_row_not_hidden_by_the_summary():
    # test case 13: the summary doesn't need to expose measured_by itself,
    # but it must not choke on a row that carries it.
    now = datetime(2026, 9, 20, 12, 0, tzinfo=CENTRAL)
    rows = [_row(1, "2026-09-20T08:00:00-05:00", "browser_run", browser_ms=60000, measured_by="elapsed")]
    out = summarize(rows, settings=SETTINGS, now=now)
    assert out["today"]["minutes_used"] == 1.0
