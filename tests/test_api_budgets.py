"""Phase 9: the dollars-only budget settings shape (`api/src/budgets.py`)."""
import pytest

from src.budgets import (
    MODEL_FIELDS,
    NUMERIC_FIELDS,
    REMOVED_FIELDS,
    SHARE_FIELDS,
    daily_fraction_used,
    linear_forecast,
    validate_budget_settings,
)


def _valid_doc(**overrides):
    doc = {
        "daily_usd_budget": 1.50, "monthly_usd_budget": 30.00, "daily_search_budget": 35,
        "search_limit_per_run": 35, "search_limit_per_company": 5, "browser_checks_per_run": 50,
        "public_feedback_daily_limit": 20,
        "share_discovery": 0.60, "share_scout": 0.20, "share_classification": 0.15, "share_improvement": 0.05,
        "model_discovery": "sonnet", "model_scout": "sonnet", "model_classification": "haiku",
        "model_improvement": "sonnet",
    }
    doc.update(overrides)
    return doc


# -- the new shape ---------------------------------------------------------

def test_validate_budget_settings_accepts_the_dollars_only_document():
    assert validate_budget_settings(_valid_doc()) == []


def test_the_removed_fields_are_gone_from_the_shape():
    assert REMOVED_FIELDS == ("billing_mode", "daily_token_budget", "weekly_token_budget")
    for field in REMOVED_FIELDS:
        assert field not in NUMERIC_FIELDS
    assert "public_feedback_daily_limit" in NUMERIC_FIELDS


def test_a_document_without_a_billing_mode_is_valid_and_a_stale_one_is_ignored():
    # Older dashboards (or a stale tab) may still send the removed keys; they are
    # neither required nor validated, and the API never writes them.
    assert validate_budget_settings(_valid_doc(billing_mode="crypto", daily_token_budget=-1)) == []


def test_validate_budget_settings_reports_each_bad_field():
    problems = validate_budget_settings(_valid_doc(
        daily_usd_budget=-1, daily_search_budget="35", model_scout="opus", public_feedback_daily_limit=-5,
    ))
    assert len(problems) == 4
    for field in ("daily_usd_budget", "daily_search_budget", "model_scout", "public_feedback_daily_limit"):
        assert any(field in p for p in problems), field


def test_validate_budget_settings_rejects_negative_numeric_field():
    problems = validate_budget_settings(_valid_doc(monthly_usd_budget=-1))
    assert any("monthly_usd_budget" in p for p in problems)


def test_validate_budget_settings_rejects_a_missing_numeric_field():
    doc = _valid_doc()
    del doc["daily_usd_budget"]
    problems = validate_budget_settings(doc)
    assert any("daily_usd_budget" in p for p in problems)


@pytest.mark.parametrize("bad", [-1, 2.5, True, "20", None])
def test_feedback_cap_must_be_a_non_negative_integer(bad):
    problems = validate_budget_settings(_valid_doc(public_feedback_daily_limit=bad))
    assert any("public_feedback_daily_limit" in p and "integer" in p for p in problems), problems


def test_feedback_cap_of_zero_is_allowed():
    assert validate_budget_settings(_valid_doc(public_feedback_daily_limit=0)) == []


def test_validate_budget_settings_rejects_bad_model():
    problems = validate_budget_settings(_valid_doc(model_discovery="opus"))
    assert any("model_discovery" in p for p in problems)


def test_validate_budget_settings_rejects_shares_not_summing_to_100_percent():
    problems = validate_budget_settings(_valid_doc(share_discovery=0.50))
    assert any("100%" in p for p in problems)


def test_validate_budget_settings_accepts_shares_within_floating_point_tolerance():
    problems = validate_budget_settings(_valid_doc(share_discovery=0.6000001))
    assert problems == []


def test_shares_are_still_decimals_between_zero_and_one():
    # The dashboard shows whole percents, but the API contract stays 0-1 decimals.
    problems = validate_budget_settings(_valid_doc(share_discovery=60, share_scout=20, share_classification=15, share_improvement=5))
    assert any("100%" in p for p in problems)


def test_all_field_groups_covered_by_valid_doc():
    doc = _valid_doc()
    for field in (*NUMERIC_FIELDS, *MODEL_FIELDS, *SHARE_FIELDS):
        assert field in doc
    assert set(doc) == set((*NUMERIC_FIELDS, *MODEL_FIELDS, *SHARE_FIELDS))


# -- forecast ----------------------------------------------------------------

def test_linear_forecast_projects_to_end_of_period():
    assert linear_forecast(200, 2, 7) == 700


def test_linear_forecast_handles_zero_elapsed():
    assert linear_forecast(50, 0, 7) == 50


# -- daily fraction (Search now's 80% confirmation, issue #35) -------------

def test_daily_fraction_used_uses_dollars():
    fraction = daily_fraction_used(_valid_doc(), searches=0, usd=1.20)
    assert fraction == pytest.approx(0.80)


def test_daily_fraction_used_is_the_worse_of_dollars_and_searches():
    assert daily_fraction_used(_valid_doc(), searches=35, usd=0) == 1.0
    assert daily_fraction_used(_valid_doc(), searches=7, usd=1.50) == pytest.approx(1.0)


def test_daily_fraction_used_has_no_token_input():
    with pytest.raises(TypeError):
        daily_fraction_used(_valid_doc(), tokens=1, searches=0, usd=0)


def test_daily_fraction_used_zero_budget_never_divides_by_zero():
    settings = _valid_doc(daily_usd_budget=0, daily_search_budget=0)
    assert daily_fraction_used(settings, searches=10, usd=5.0) == 0.0
