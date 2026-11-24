"""The guard step of `.github/workflows/daily-search.yml`.

The guard used to let a scheduled run through only during the 08:00 hour in
Chicago. GitHub delivers the schedule 4 to 8.5 hours late, so that check
turned every scheduled run away, and Henry removed it (the run that fires is
the day's run, whenever GitHub gets to it). These tests pin that: a scheduled
run goes through at any hour, and the step always tells the workflow to carry
on.

Three tests here still asserted the old hour check after it was removed, and
failed on `master`. They were wrong about what the guard is for, so they were
replaced, not the code.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from job import schedule_guard
from job.schedule_guard import should_run_now


def test_manual_runs_always_go_through():
    assert should_run_now("workflow_dispatch", now=datetime(2026, 9, 20, 3, 0)) is True


@pytest.mark.parametrize("local_time", [
    datetime(2026, 9, 20, 0, 5),
    datetime(2026, 9, 20, 7, 59),
    datetime(2026, 9, 20, 8, 30),
    datetime(2026, 9, 20, 9, 0),
    datetime(2026, 9, 20, 14, 7),   # a typical late delivery
    datetime(2026, 9, 20, 23, 55),
])
def test_a_scheduled_run_goes_through_at_any_hour(local_time):
    assert should_run_now("schedule", now=local_time) is True


@pytest.mark.parametrize("utc_time", [
    datetime(2026, 7, 15, 13, 0, tzinfo=ZoneInfo("UTC")),   # 08:00 in Chicago, daylight time
    datetime(2026, 7, 15, 18, 13, tzinfo=ZoneInfo("UTC")),  # delivered 5 hours late
    datetime(2026, 1, 15, 13, 0, tzinfo=ZoneInfo("UTC")),   # 07:00 in Chicago, standard time
    datetime(2026, 1, 15, 21, 30, tzinfo=ZoneInfo("UTC")),  # delivered 8.5 hours late
])
def test_a_scheduled_run_goes_through_in_either_half_of_the_year(utc_time):
    assert should_run_now("schedule", now=utc_time) is True


def test_a_scheduled_run_goes_through_when_no_time_is_given():
    assert should_run_now("schedule") is True


@pytest.mark.parametrize("event_name", ["schedule", "workflow_dispatch"])
def test_the_step_tells_the_workflow_to_carry_on(event_name, monkeypatch, tmp_path, capsys):
    # Every later step of the workflow runs only if this line says "true".
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_EVENT_NAME", event_name)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    assert schedule_guard.main() == 0

    assert output.read_text(encoding="utf-8") == "should_run=true\n"
    assert capsys.readouterr().out.strip() == "should_run=true"


def test_the_step_works_outside_github_actions(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    assert schedule_guard.main() == 0
    assert capsys.readouterr().out.strip() == "should_run=true"
