import pytest

from src.budgets import (
    MODEL_FIELDS,
    NUMERIC_FIELDS,
    SHARE_FIELDS,
    daily_fraction_used,
    linear_forecast,
    validate_budget_settings,
)


def _valid_doc(**overrides):
    doc = {
        "billing_mode": "subscription",
        "daily_token_budget": 4_000_000, "daily_search_budget": 35, "weekly_token_budget": 25_000_000,
        "daily_usd_budget": 1.50, "monthly_usd_budget": 30.00,
        "search_limit_per_run": 35, "search_limit_per_company": 5, "browser_checks_per_run": 50,
        "share_discovery": 0.60, "share_scout": 0.20, "share_classification": 0.15, "share_improvement": 0.05,
        "model_discovery": "sonnet", "model_scout": "sonnet", "model_classification": "haiku",
        "model_improvement": "sonnet",
    }
    doc.update(overrides)
    return doc


def test_validate_budget_settings_accepts_valid_document():
    assert validate_budget_settings(_valid_doc()) == []


def test_validate_budget_settings_rejects_bad_billing_mode():
    problems = validate_budget_settings(_valid_doc(billing_mode="crypto"))
    assert any("billing_mode" in p for p in problems)


def test_validate_budget_settings_rejects_negative_numeric_field():
    problems = validate_budget_settings(_valid_doc(daily_token_budget=-1))
    assert any("daily_token_budget" in p for p in problems)


def test_validate_budget_settings_rejects_bad_model():
    problems = validate_budget_settings(_valid_doc(model_discovery="opus"))
    assert any("model_discovery" in p for p in problems)


def test_validate_budget_settings_rejects_shares_not_summing_to_100_percent():
    problems = validate_budget_settings(_valid_doc(share_discovery=0.50))
    assert any("100%" in p for p in problems)


def test_validate_budget_settings_accepts_shares_within_floating_point_tolerance():
    problems = validate_budget_settings(_valid_doc(share_discovery=0.6000001))
    assert problems == []


def test_all_field_groups_covered_by_valid_doc():
    doc = _valid_doc()
    for field in (*NUMERIC_FIELDS, *MODEL_FIELDS, *SHARE_FIELDS):
        assert field in doc


def test_linear_forecast_projects_to_end_of_period():
    assert linear_forecast(200, 2, 7) == 700


def test_linear_forecast_handles_zero_elapsed():
    assert linear_forecast(50, 0, 7) == 50


def test_daily_fraction_used_subscription_mode_uses_tokens():
    settings = _valid_doc()
    fraction = daily_fraction_used(settings, tokens=3_200_000, searches=0, usd=0)
    assert fraction == 0.80


def test_daily_fraction_used_api_key_mode_uses_dollars():
    settings = _valid_doc(billing_mode="api_key")
    fraction = daily_fraction_used(settings, tokens=0, searches=0, usd=1.20)
    assert fraction == pytest.approx(0.80)


def test_daily_fraction_used_is_the_worse_of_tokens_and_searches():
    settings = _valid_doc()
    fraction = daily_fraction_used(settings, tokens=0, searches=35, usd=0)
    assert fraction == 1.0


def test_daily_fraction_used_zero_budget_never_divides_by_zero():
    settings = _valid_doc(daily_token_budget=0, daily_search_budget=0)
    assert daily_fraction_used(settings, tokens=100, searches=10, usd=0) == 0.0
