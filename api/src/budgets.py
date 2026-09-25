"""Budget settings validation and forecasting for the dashboard's Budgets
panel.

Deliberately self-contained rather than importing `job/agent/budgets.py`:
the Worker only ships what's under `api/` (see `api/wrangler.toml`), so
anything the deployed Worker needs at runtime has to live in `api/src`,
even though it duplicates `job/agent/budgets.py`'s small forecast formula.
"""
from __future__ import annotations

SHARE_FIELDS = ("share_discovery", "share_scout", "share_classification", "share_improvement")
MODEL_FIELDS = ("model_discovery", "model_scout", "model_classification", "model_improvement")
NUMERIC_FIELDS = (
    "daily_token_budget", "daily_search_budget", "weekly_token_budget",
    "daily_usd_budget", "monthly_usd_budget", "search_limit_per_run",
    "search_limit_per_company", "browser_checks_per_run",
)


def validate_budget_settings(doc: dict) -> list[str]:
    problems: list[str] = []

    billing_mode = doc.get("billing_mode")
    if billing_mode not in ("subscription", "api_key"):
        problems.append("billing_mode must be 'subscription' or 'api_key'")

    for field in NUMERIC_FIELDS:
        value = doc.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            problems.append(f"{field} must be a non-negative number")

    for field in MODEL_FIELDS:
        if doc.get(field) not in ("sonnet", "haiku"):
            problems.append(f"{field} must be 'sonnet' or 'haiku'")

    shares = []
    for field in SHARE_FIELDS:
        value = doc.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            problems.append(f"{field} must be a non-negative number")
        else:
            shares.append(value)
    if len(shares) == len(SHARE_FIELDS) and abs(sum(shares) - 1.0) > 1e-6:
        problems.append(f"the step shares must add up to 100% (got {sum(shares):.0%})")

    return problems


def linear_forecast(used_to_date: float, elapsed_units: int, total_units: int) -> float:
    """Linear projection of `used_to_date` to the end of the period."""
    if elapsed_units <= 0:
        return used_to_date
    return used_to_date / elapsed_units * total_units


def daily_fraction_used(settings: dict, *, tokens: float, searches: float, usd: float) -> float:
    """The worse of today's token/dollar and search fractions -- mirrors
    `job/agent/budgets.py`'s `BudgetStatus.fraction_used()`, kept separate
    (see this module's docstring) since the Worker only ships `api/src`.
    Used for Search now's 80%-used confirmation (issue #35).
    """
    billing_mode = settings.get("billing_mode") or "subscription"
    if billing_mode == "api_key":
        daily_budget = float(settings.get("daily_usd_budget") or 0)
        daily_used = usd
    else:
        daily_budget = float(settings.get("daily_token_budget") or 0)
        daily_used = tokens
    daily_fraction = (daily_used / daily_budget) if daily_budget > 0 else 0.0

    search_budget = float(settings.get("daily_search_budget") or 0)
    search_fraction = (searches / search_budget) if search_budget > 0 else 0.0

    return max(daily_fraction, search_fraction)
