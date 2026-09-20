import asyncio

from job.agent.budgets import (
    BudgetStatus,
    UsageTotals,
    forecast,
    load_budget_status,
    monthly_forecast,
    weekly_forecast,
)

SETTINGS = {
    "billing_mode": "subscription",
    "daily_token_budget": 1000,
    "daily_search_budget": 10,
    "weekly_token_budget": 5000,
    "daily_usd_budget": 1.50,
    "monthly_usd_budget": 30.00,
    "share_discovery": 0.60,
    "share_scout": 0.20,
    "share_classification": 0.15,
    "share_improvement": 0.05,
}


def run(coro):
    return asyncio.run(coro)


def _status(**overrides):
    kwargs = dict(billing_mode="subscription", settings=SETTINGS)
    kwargs.update(overrides)
    return BudgetStatus(**kwargs)


# -- fractions and enforcement -------------------------------------------

def test_daily_fraction_used_subscription_mode_uses_tokens():
    status = _status(today=UsageTotals(tokens=500))
    assert status.daily_fraction_used() == 0.5


def test_daily_fraction_used_api_key_mode_uses_dollars():
    status = _status(billing_mode="api_key", today=UsageTotals(usd=0.75))
    assert status.daily_fraction_used() == 0.5


def test_fraction_used_is_the_worse_of_tokens_and_searches():
    status = _status(today=UsageTotals(tokens=100, searches=9))  # 10% tokens, 90% searches
    assert status.fraction_used() == 0.9


def test_allow_step_true_when_under_budget():
    status = _status(today=UsageTotals(tokens=100))
    allowed, reason = status.allow_step("discovery")
    assert allowed is True
    assert reason is None


def test_allow_step_false_once_daily_budget_used_up():
    status = _status(today=UsageTotals(tokens=1000))
    allowed, reason = status.allow_step("discovery")
    assert allowed is False
    assert "budget" in reason


def test_allow_step_always_true_for_classification_and_pruning_even_over_budget():
    status = _status(today=UsageTotals(tokens=999999))
    for step in ("classification", "pruning"):
        allowed, reason = status.allow_step(step)
        assert allowed is True
        assert reason is None


def test_allow_step_respects_per_step_share():
    # discovery's share is 60% of 1000 = 600 tokens; classification's usage
    # doesn't count against it.
    status = _status(
        today=UsageTotals(tokens=650),
        today_by_step={"discovery": UsageTotals(tokens=650)},
    )
    allowed, reason = status.allow_step("discovery")
    assert allowed is False
    assert "share" in reason


def test_allow_step_unclear_page_rides_classifications_share():
    status = _status(
        today=UsageTotals(tokens=200),
        today_by_step={"classification": UsageTotals(tokens=200)},
    )
    # classification's share is 15% of 1000 = 150 tokens; already over.
    allowed, reason = status.allow_step("unclear_page")
    assert allowed is False


# -- cut order ------------------------------------------------------------

def test_cut_order_nothing_cut_below_80_percent():
    status = _status(today=UsageTotals(tokens=700))  # 70%
    assert status.steps_to_cut() == []


def test_cut_order_cuts_scout_first_at_80_percent():
    status = _status(today=UsageTotals(tokens=800))  # 80%
    assert status.steps_to_cut() == ["scout"]


def test_cut_order_cuts_scout_and_low_yield_discovery_at_90_percent():
    status = _status(today=UsageTotals(tokens=900))  # 90%
    assert status.steps_to_cut() == ["scout", "discovery_low_yield"]


def test_cut_order_cuts_everything_non_mandatory_at_100_percent():
    status = _status(today=UsageTotals(tokens=1000))  # 100%
    assert status.steps_to_cut() == ["scout", "discovery_low_yield", "unclear_page", "improvement"]


def test_cut_order_never_includes_classification_or_pruning():
    status = _status(today=UsageTotals(tokens=999999))
    cut = status.steps_to_cut()
    assert "classification" not in cut
    assert "pruning" not in cut


# -- forecast -------------------------------------------------------------

def test_forecast_linear_projection():
    assert forecast(200, 2, 7) == 700  # 100/day for 7 days


def test_forecast_handles_zero_elapsed_units():
    assert forecast(50, 0, 7) == 50


def test_weekly_forecast_uses_day_of_week():
    status = _status(week=UsageTotals(tokens=200), day_of_week=2)
    assert weekly_forecast(status) == 700


def test_monthly_forecast_uses_day_of_month():
    status = _status(billing_mode="api_key", month=UsageTotals(usd=30.0), day_of_month=10, days_in_month=30)
    assert monthly_forecast(status) == 90.0


# -- loading from the database --------------------------------------------

def test_load_budget_status_sums_usage_rows(empty_db):
    run(empty_db.run(
        "UPDATE budget_settings SET daily_token_budget = 1000, daily_search_budget = 10 WHERE id = 1"
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

    assert status.today.tokens == 180  # 100+50+20+10
    assert status.today.searches == 3
    assert status.today_by_step["discovery"].tokens == 150
    assert status.today_by_step["classification"].tokens == 30
    assert status.week.tokens == 180 + (999 + 999)  # same ISO week
