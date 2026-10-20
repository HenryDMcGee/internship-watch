"""Budget settings validation and forecasting for the dashboard's Budgets
panel.

One billing model since phase 9: API-key dollars. Daily and monthly dollar
limits, a daily search limit, a share of the day's dollars per step, a
model per step, and the rarely changed per-run limits. The old
subscription mode and its token budgets are gone (`REMOVED_FIELDS`).

Deliberately self-contained rather than importing `job/agent/budgets.py`:
the Worker only ships what's under `api/` (see `api/wrangler.toml`), so
anything the deployed Worker needs at runtime has to live in `api/src`,
even though it duplicates `job/agent/budgets.py`'s small forecast formula.
"""
from __future__ import annotations

SHARE_FIELDS = ("share_discovery", "share_scout", "share_classification", "share_improvement")
MODEL_FIELDS = ("model_discovery", "model_scout", "model_classification", "model_improvement")
NUMERIC_FIELDS = (
    "daily_usd_budget", "monthly_usd_budget", "daily_search_budget",
    "search_limit_per_run", "search_limit_per_company", "browser_checks_per_run",
    "public_feedback_daily_limit",
)
# Numeric fields that must be whole numbers (a count, not an amount).
INTEGER_FIELDS = ("public_feedback_daily_limit",)
# Removed in phase 9. Never required, never validated, never written: a stale
# dashboard that still sends them is ignored rather than refused.
REMOVED_FIELDS = ("billing_mode", "daily_token_budget", "weekly_token_budget")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_budget_settings(doc: dict) -> list[str]:
    problems: list[str] = []

    for field in NUMERIC_FIELDS:
        value = doc.get(field)
        if field in INTEGER_FIELDS:
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                problems.append(f"{field} must be a non-negative integer")
        elif not _is_number(value) or value < 0:
            problems.append(f"{field} must be a non-negative number")

    for field in MODEL_FIELDS:
        if doc.get(field) not in ("sonnet", "haiku"):
            problems.append(f"{field} must be 'sonnet' or 'haiku'")

    shares = []
    for field in SHARE_FIELDS:
        value = doc.get(field)
        if not _is_number(value) or value < 0:
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


def daily_fraction_used(settings: dict, *, searches: float, usd: float) -> float:
    """The worse of today's dollar and search fractions -- mirrors
    `job/agent/budgets.py`'s `BudgetStatus.fraction_used()`, kept separate
    (see this module's docstring) since the Worker only ships `api/src`.
    Used for Search now's 80%-used confirmation (issue #35).
    """
    daily_budget = float(settings.get("daily_usd_budget") or 0)
    daily_fraction = (usd / daily_budget) if daily_budget > 0 else 0.0

    search_budget = float(settings.get("daily_search_budget") or 0)
    search_fraction = (searches / search_budget) if search_budget > 0 else 0.0

    return max(daily_fraction, search_fraction)
