"""Phase 9: the daily job enforces dollar and search budgets only."""
import asyncio

import pytest

from job.agent.budgets import (
    BudgetStatus,
    UsageTotals,
    forecast,
    load_budget_status,
    monthly_forecast,
    weekly_forecast,
)

SETTINGS = {
    "daily_usd_budget": 10.0,
    "monthly_usd_budget": 100.0,
    "daily_search_budget": 10,
    "share_discovery": 0.60,
    "share_scout": 0.20,
    "share_classification": 0.15,
    "share_improvement": 0.05,
}


def run(coro):
    return asyncio.run(coro)


def _status(**overrides):
    kwargs = dict(settings=SETTINGS)
    kwargs.update(overrides)
    return BudgetStatus(**kwargs)


# -- fractions and enforcement -------------------------------------------

def test_there_is_no_billing_mode_any_more():
    with pytest.raises(TypeError):
        BudgetStatus(billing_mode="api_key", settings=SETTINGS)


def test_daily_fraction_used_uses_dollars():
    status = _status(today=UsageTotals(usd=5.0))
    assert status.daily_fraction_used() == 0.5


def test_tokens_never_count_toward_the_budget():
    status = _status(today=UsageTotals(tokens=10**9, usd=0.0))
    assert status.daily_fraction_used() == 0.0
    assert status.fraction_used() == 0.0
    assert status.allow_step("discovery") == (True, None)


def test_fraction_used_is_the_worse_of_dollars_and_searches():
    status = _status(today=UsageTotals(usd=1.0, searches=9))  # 10% dollars, 90% searches
    assert status.fraction_used() == 0.9


def test_allow_step_true_when_under_budget():
    status = _status(today=UsageTotals(usd=1.0))
    allowed, reason = status.allow_step("discovery")
    assert allowed is True
    assert reason is None


def test_allow_step_false_once_daily_dollars_used_up():
    status = _status(today=UsageTotals(usd=10.0))
    allowed, reason = status.allow_step("discovery")
    assert allowed is False
    assert "budget" in reason


def test_allow_step_false_once_daily_searches_used_up():
    status = _status(today=UsageTotals(searches=10))
    allowed, reason = status.allow_step("discovery")
    assert allowed is False


def test_allow_step_always_true_for_classification_and_pruning_even_over_budget():
    status = _status(today=UsageTotals(usd=999.0))
    for step in ("classification", "pruning"):
        allowed, reason = status.allow_step(step)
        assert allowed is True
        assert reason is None


def test_allow_step_respects_per_step_share_in_dollars():
    # discovery's share is 60% of $10 = $6; classification's spend doesn't count against it.
    status = _status(
        today=UsageTotals(usd=6.5),
        today_by_step={"discovery": UsageTotals(usd=6.5)},
    )
    allowed, reason = status.allow_step("discovery")
    assert allowed is False
    assert "share" in reason


def test_share_used_ignores_tokens():
    status = _status(
        today=UsageTotals(usd=1.0, tokens=10**9),
        today_by_step={"discovery": UsageTotals(usd=1.0, tokens=10**9)},
    )
    assert status.share_used("discovery") == 1.0
    assert status.allow_step("discovery") == (True, None)


def test_allow_step_unclear_page_rides_classifications_share():
    status = _status(
        today=UsageTotals(usd=2.0),
        today_by_step={"classification": UsageTotals(usd=2.0)},
    )
    # classification's share is 15% of $10 = $1.50; already over.
    allowed, reason = status.allow_step("unclear_page")
    assert allowed is False


# -- weekly and monthly ------------------------------------------------------

def test_weekly_usage_is_never_enforced():
    status = _status(week=UsageTotals(usd=10**6, tokens=10**9))
    assert not hasattr(status, "weekly_fraction_used")
    assert status.worst_fraction_used() == 0.0
    assert status.allow_step("discovery") == (True, None)


def test_monthly_fraction_used_uses_dollars():
    status = _status(month=UsageTotals(usd=50.0))
    assert status.monthly_fraction_used() == 0.5


def test_worst_fraction_used_is_the_worse_of_today_and_this_month():
    assert _status(today=UsageTotals(usd=3.0), month=UsageTotals(usd=90.0)).worst_fraction_used() == 0.9
    assert _status(today=UsageTotals(usd=9.0), month=UsageTotals(usd=9.0)).worst_fraction_used() == 0.9


# -- cut order ------------------------------------------------------------

def test_cut_order_nothing_cut_below_80_percent():
    status = _status(today=UsageTotals(usd=7.0))  # 70%
    assert status.steps_to_cut() == []


def test_cut_order_cuts_scout_first_at_80_percent():
    status = _status(today=UsageTotals(usd=8.0))  # 80%
    # careers_repair (issue #55) rides scout's own share and threshold, so
    # it's cut at the same point scout is.
    assert status.steps_to_cut() == ["scout", "careers_repair"]


def test_cut_order_cuts_scout_and_low_yield_discovery_at_90_percent():
    status = _status(today=UsageTotals(usd=9.0))  # 90%
    assert status.steps_to_cut() == ["scout", "careers_repair", "discovery_low_yield"]


def test_cut_order_cuts_everything_non_mandatory_at_100_percent():
    status = _status(today=UsageTotals(usd=10.0))  # 100%
    assert status.steps_to_cut() == ["scout", "careers_repair", "discovery_low_yield", "unclear_page", "improvement"]


def test_cut_order_never_includes_classification_or_pruning():
    status = _status(today=UsageTotals(usd=999.0))
    cut = status.steps_to_cut()
    assert "classification" not in cut
    assert "pruning" not in cut


# -- forecast -------------------------------------------------------------

def test_forecast_linear_projection():
    assert forecast(200, 2, 7) == 700  # 100/day for 7 days


def test_forecast_handles_zero_elapsed_units():
    assert forecast(50, 0, 7) == 50


def test_weekly_forecast_is_in_dollars():
    status = _status(week=UsageTotals(usd=2.0, tokens=200), day_of_week=2)
    assert weekly_forecast(status) == 7.0


def test_monthly_forecast_uses_day_of_month():
    status = _status(month=UsageTotals(usd=30.0), day_of_month=10, days_in_month=30)
    assert monthly_forecast(status) == 90.0


# -- loading from the database --------------------------------------------

def test_load_budget_status_sums_usage_rows(empty_db):
    run(empty_db.run(
        "UPDATE budget_settings SET daily_usd_budget = 1.0, daily_search_budget = 10 WHERE id = 1"
    ))
    run(empty_db.run(
        """INSERT INTO usage (run_id, step, model, input_tokens, output_tokens, cache_read_tokens,
           cache_write_tokens, web_searches, est_cost_usd, at)
           VALUES (1, 'discovery', 'sonnet', 100, 50, 0, 0, 3, 0.01, '2026-09-20T08:00:00')"""
    ))
    run(empty_db.run(
        """INSERT INTO usage (run_id, step, model, input_tokens, output_tokens, cache_read_tokens,
           cache_write_tokens, web_searches, est_cost_usd, at)
           VALUES (1, 'classification', 'haiku', 20, 10, 0, 0, 0, 0.001, '2026-09-20T09:00:00')"""
    ))
    # A different day must not count toward today's totals.
    run(empty_db.run(
        """INSERT INTO usage (run_id, step, model, input_tokens, output_tokens, cache_read_tokens,
           cache_write_tokens, web_searches, est_cost_usd, at)
           VALUES (1, 'discovery', 'sonnet', 999, 999, 0, 0, 5, 1.0, '2026-09-19T08:00:00')"""
    ))

    budget_settings = run(empty_db.first("SELECT * FROM budget_settings WHERE id = 1"))
    status = run(load_budget_status(empty_db, budget_settings, today="2026-09-20"))

    assert status.today.usd == pytest.approx(0.011)
    assert status.today.tokens == 180  # 100+50+20+10, still recorded for Analytics and the spike alert
    assert status.today.searches == 3
    assert status.today_by_step["discovery"].usd == pytest.approx(0.01)
    assert status.today_by_step["classification"].usd == pytest.approx(0.001)
    assert status.week.usd == pytest.approx(0.011 + 1.0)  # same ISO week
    assert status.month.usd == pytest.approx(0.011 + 1.0)
    assert status.daily_fraction_used() == pytest.approx(0.011)
    assert status.settings["daily_usd_budget"] == 1.0


# -- resets (issue #38) ----------------------------------------------------

def _insert_usage(db, *, tokens: int, searches: int, at: str, step: str = "discovery") -> None:
    run(db.run(
        """INSERT INTO usage (run_id, step, model, input_tokens, output_tokens, cache_read_tokens,
           cache_write_tokens, web_searches, est_cost_usd, at)
           VALUES (1, ?, 'sonnet', ?, 0, 0, 0, ?, 0, ?)""",
        step, tokens, searches, at,
    ))


def _insert_reset(db, *, scope: str, reset_at: str, note: str | None = None) -> None:
    run(db.run("INSERT INTO budget_resets (scope, reset_at, note) VALUES (?, ?, ?)", scope, reset_at, note))


def test_daily_reset_excludes_usage_recorded_before_it(empty_db):
    _insert_usage(empty_db, tokens=1000, searches=5, at="2026-09-25T07:00:00-05:00")
    _insert_reset(empty_db, scope="daily", reset_at="2026-09-25T08:00:00-05:00")
    _insert_usage(empty_db, tokens=200, searches=1, at="2026-09-25T09:00:00-05:00")

    budget_settings = run(empty_db.first("SELECT * FROM budget_settings WHERE id = 1"))
    status = run(load_budget_status(empty_db, budget_settings, today="2026-09-25"))

    assert status.today.tokens == 200
    assert status.today.searches == 1


def test_daily_reset_does_not_affect_weekly_total(empty_db):
    _insert_usage(empty_db, tokens=1000, searches=5, at="2026-09-25T07:00:00-05:00")
    _insert_reset(empty_db, scope="daily", reset_at="2026-09-25T08:00:00-05:00")
    _insert_usage(empty_db, tokens=200, searches=1, at="2026-09-25T09:00:00-05:00")

    budget_settings = run(empty_db.first("SELECT * FROM budget_settings WHERE id = 1"))
    status = run(load_budget_status(empty_db, budget_settings, today="2026-09-25"))

    assert status.week.tokens == 1200  # the daily reset doesn't touch the weekly count


def test_weekly_reset_excludes_usage_recorded_before_it(empty_db):
    # 2026-09-21 is a Monday; the reset happens midweek on the 23rd.
    _insert_usage(empty_db, tokens=5000, searches=0, at="2026-09-21T08:00:00-05:00")
    _insert_reset(empty_db, scope="weekly", reset_at="2026-09-23T10:00:00-05:00")
    _insert_usage(empty_db, tokens=300, searches=0, at="2026-09-24T08:00:00-05:00")

    budget_settings = run(empty_db.first("SELECT * FROM budget_settings WHERE id = 1"))
    status = run(load_budget_status(empty_db, budget_settings, today="2026-09-24"))

    assert status.week.tokens == 300


def test_reset_from_last_week_does_not_carry_into_this_week(empty_db):
    # A weekly reset on 2026-09-16 (in last week's window) must not suppress
    # this week's (starting 2026-09-21) usage.
    _insert_reset(empty_db, scope="weekly", reset_at="2026-09-16T10:00:00-05:00")
    _insert_usage(empty_db, tokens=400, searches=0, at="2026-09-21T08:00:00-05:00")

    budget_settings = run(empty_db.first("SELECT * FROM budget_settings WHERE id = 1"))
    status = run(load_budget_status(empty_db, budget_settings, today="2026-09-21"))

    assert status.week.tokens == 400


def test_day_boundary_holds_across_the_fall_daylight_saving_time_change(empty_db):
    # 2026-11-01 is when Central switches from CDT (-05:00) to CST (-06:00)
    # in the US. Usage just before local midnight on 2026-11-01 must not
    # count as 2026-11-02, and usage just after must not count as 2026-11-01.
    _insert_usage(empty_db, tokens=111, searches=0, at="2026-11-01T23:30:00-06:00")
    _insert_usage(empty_db, tokens=222, searches=0, at="2026-11-02T00:30:00-06:00")

    budget_settings = run(empty_db.first("SELECT * FROM budget_settings WHERE id = 1"))
    status_1 = run(load_budget_status(empty_db, budget_settings, today="2026-11-01"))
    status_2 = run(load_budget_status(empty_db, budget_settings, today="2026-11-02"))

    assert status_1.today.tokens == 111
    assert status_2.today.tokens == 222


def test_daily_reset_cutoff_is_a_real_time_comparison_not_a_string_one(empty_db):
    # A reset made right at the spring-forward change (2026-03-08, when
    # Central jumps from -06:00 to -05:00) must still correctly separate
    # earlier and later usage once converted to real instants in time.
    _insert_usage(empty_db, tokens=50, searches=0, at="2026-03-08T01:30:00-06:00")
    _insert_reset(empty_db, scope="daily", reset_at="2026-03-08T03:15:00-05:00")
    _insert_usage(empty_db, tokens=70, searches=0, at="2026-03-08T03:30:00-05:00")

    budget_settings = run(empty_db.first("SELECT * FROM budget_settings WHERE id = 1"))
    status = run(load_budget_status(empty_db, budget_settings, today="2026-03-08"))

    assert status.today.tokens == 70
