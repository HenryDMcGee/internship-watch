from datetime import datetime
from zoneinfo import ZoneInfo

from job.schedule_guard import should_run_now

CENTRAL = ZoneInfo("America/Chicago")


def test_manual_runs_always_go_through():
    assert should_run_now("workflow_dispatch", now=datetime(2026, 9, 20, 3, 0)) is True


def test_scheduled_run_at_08_local_goes_through():
    assert should_run_now("schedule", now=datetime(2026, 9, 20, 8, 30)) is True


def test_scheduled_run_outside_08_local_is_skipped():
    assert should_run_now("schedule", now=datetime(2026, 9, 20, 7, 59)) is False
    assert should_run_now("schedule", now=datetime(2026, 9, 20, 9, 0)) is False


def test_scheduled_run_during_daylight_saving_time():
    # CDT is UTC-5. 13:00 UTC is 08:00 local; 14:00 UTC is 09:00 local.
    on_time = datetime(2026, 7, 15, 13, 0, tzinfo=ZoneInfo("UTC"))
    too_late = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("UTC"))
    assert should_run_now("schedule", now=on_time) is True
    assert should_run_now("schedule", now=too_late) is False


def test_scheduled_run_during_standard_time():
    # CST is UTC-6. 13:00 UTC is 07:00 local; 14:00 UTC is 08:00 local.
    too_early = datetime(2026, 1, 15, 13, 0, tzinfo=ZoneInfo("UTC"))
    on_time = datetime(2026, 1, 15, 14, 0, tzinfo=ZoneInfo("UTC"))
    assert should_run_now("schedule", now=too_early) is False
    assert should_run_now("schedule", now=on_time) is True
