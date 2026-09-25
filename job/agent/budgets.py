"""Budget tracking and enforcement for Claude calls.

Loads today's, this week's, and this month's usage from the `usage` table
and compares it against `budget_settings`, per `docs/plan.md`'s Budgets
feature: daily/weekly/monthly limits, a share per step, and search limits.
Budget is checked before every Claude step (`allow_step`).

Order for cutting steps as usage climbs toward and past the daily budget,
per `docs/plan.md`: scout, then low-yield discovery, then the rest
(unclear-page reads and daily improvement). Classification and pruning
always run -- pruning never calls Claude at all, and `budget_settings` has
no share of its own for unclear-page reads, so those draw from
classification's share for the per-step check but are still cut under
`CUT_ORDER` like any other non-mandatory step.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - zoneinfo is stdlib on 3.9+
    ZoneInfo = None  # type: ignore

CENTRAL = ZoneInfo("America/Chicago") if ZoneInfo else None

ALWAYS_RUN = frozenset({"classification", "pruning"})

# Cut thresholds are fractions of the daily budget used. Once usage reaches
# a step's threshold, that step (and everything before it in the tuple) is
# skipped for the rest of the run.
CUT_ORDER: tuple[str, ...] = ("scout", "discovery_low_yield", "unclear_page", "improvement")
CUT_THRESHOLDS: dict[str, float] = {
    "scout": 0.80,
    "discovery_low_yield": 0.90,
    "unclear_page": 1.00,
    "improvement": 1.00,
}

# Which `budget_settings` share field funds a step's per-step budget.
# unclear_page has no dedicated share, so it rides classification's.
SHARE_SETTING: dict[str, str] = {
    "discovery": "share_discovery",
    "scout": "share_scout",
    "classification": "share_classification",
    "unclear_page": "share_classification",
    "improvement": "share_improvement",
}


@dataclass
class UsageTotals:
    tokens: int = 0
    searches: int = 0
    usd: float = 0.0


@dataclass
class BudgetStatus:
    billing_mode: str
    settings: dict
    today: UsageTotals = field(default_factory=UsageTotals)
    today_by_step: dict[str, UsageTotals] = field(default_factory=dict)
    week: UsageTotals = field(default_factory=UsageTotals)
    month: UsageTotals = field(default_factory=UsageTotals)
    day_of_week: int = 1  # ISO weekday, 1 (Monday) .. 7 (Sunday)
    day_of_month: int = 1
    days_in_month: int = 30

    # -- daily --------------------------------------------------------

    def daily_budget(self) -> float:
        if self.billing_mode == "api_key":
            return float(self.settings.get("daily_usd_budget") or 0)
        return float(self.settings.get("daily_token_budget") or 0)

    def daily_used(self) -> float:
        return self.today.usd if self.billing_mode == "api_key" else self.today.tokens

    def daily_fraction_used(self) -> float:
        budget = self.daily_budget()
        return (self.daily_used() / budget) if budget > 0 else 0.0

    def search_fraction_used(self) -> float:
        budget = float(self.settings.get("daily_search_budget") or 0)
        return (self.today.searches / budget) if budget > 0 else 0.0

    def fraction_used(self) -> float:
        """The worse of the token/dollar and search fractions -- either can force cuts."""
        return max(self.daily_fraction_used(), self.search_fraction_used())

    # -- weekly / monthly ----------------------------------------------

    def weekly_fraction_used(self) -> float:
        if self.billing_mode == "api_key":
            return 0.0
        budget = float(self.settings.get("weekly_token_budget") or 0)
        return (self.week.tokens / budget) if budget > 0 else 0.0

    def monthly_fraction_used(self) -> float:
        if self.billing_mode != "api_key":
            return 0.0
        budget = float(self.settings.get("monthly_usd_budget") or 0)
        return (self.month.usd / budget) if budget > 0 else 0.0

    def worst_fraction_used(self) -> float:
        return max(self.fraction_used(), self.weekly_fraction_used(), self.monthly_fraction_used())

    # -- per-step gating -------------------------------------------------

    def steps_to_cut(self) -> list[str]:
        fraction = self.fraction_used()
        return [step for step in CUT_ORDER if fraction >= CUT_THRESHOLDS[step]]

    def share_budget(self, step: str) -> float:
        share = float(self.settings.get(SHARE_SETTING.get(step, ""), 0) or 0)
        return self.daily_budget() * share

    def share_used(self, step: str) -> float:
        share_key = SHARE_SETTING.get(step, step)
        used = 0.0
        for other_step, totals in self.today_by_step.items():
            if SHARE_SETTING.get(other_step, other_step) == share_key:
                used += totals.usd if self.billing_mode == "api_key" else totals.tokens
        return used

    def allow_step(self, step: str) -> tuple[bool, str | None]:
        if step in ALWAYS_RUN:
            return True, None
        if step in self.steps_to_cut():
            return False, f"{step} skipped: {self.fraction_used():.0%} of today's budget used"
        if self.fraction_used() >= 1.0:
            return False, f"{step} skipped: today's budget is used up"
        share_budget = self.share_budget(step)
        if share_budget > 0 and self.share_used(step) >= share_budget:
            return False, f"{step} skipped: its share of today's budget is used up"
        return True, None


def forecast(used_to_date: float, elapsed_units: int, total_units: int) -> float:
    """Linear projection of `used_to_date` to the end of the period."""
    if elapsed_units <= 0:
        return used_to_date
    return used_to_date / elapsed_units * total_units


def weekly_forecast(status: BudgetStatus) -> float:
    return forecast(status.week.tokens, status.day_of_week, 7)


def monthly_forecast(status: BudgetStatus) -> float:
    return forecast(status.month.usd, status.day_of_month, status.days_in_month)


def _row_totals(row: dict) -> UsageTotals:
    tokens = (
        int(row.get("input_tokens") or 0)
        + int(row.get("output_tokens") or 0)
        + int(row.get("cache_read_tokens") or 0)
        + int(row.get("cache_write_tokens") or 0)
    )
    return UsageTotals(tokens=tokens, usd=float(row.get("est_cost_usd") or 0))


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def _midnight_central(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=CENTRAL) if CENTRAL else datetime.combine(d, time.min)


def _parse_central(value: str) -> datetime:
    """Parses a stored `at`/`reset_at` timestamp. Both are written by
    `now_iso()` helpers that always include a Central offset in production;
    naive strings (some test fixtures, and `SqliteDatabase` doesn't care
    either way) are treated as already being Central local time.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None and CENTRAL is not None:
        dt = dt.replace(tzinfo=CENTRAL)
    return dt


def _reset_cutoff(reset_rows: list[dict], window_start: datetime) -> datetime:
    """The later of `window_start` and the most recent reset in
    `reset_rows` -- usage before this cutoff doesn't count toward the
    window's budget.
    """
    latest = None
    for row in reset_rows:
        candidate = _parse_central(row["reset_at"])
        if latest is None or candidate > latest:
            latest = candidate
    return max(latest, window_start) if latest else window_start


async def load_budget_status(db, budget_settings: dict, *, today: str) -> BudgetStatus:
    today_date = date.fromisoformat(today)
    week_start_date = today_date - timedelta(days=today_date.weekday())  # Monday
    week_start = week_start_date.isoformat()
    month_start = today_date.replace(day=1).isoformat()

    daily_reset_rows = await db.all(
        "SELECT reset_at FROM budget_resets WHERE scope = 'daily' AND substr(reset_at, 1, 10) = ?",
        today,
    )
    weekly_reset_rows = await db.all(
        "SELECT reset_at FROM budget_resets WHERE scope = 'weekly' AND substr(reset_at, 1, 10) >= ? "
        "AND substr(reset_at, 1, 10) <= ?",
        week_start, today,
    )
    daily_cutoff = _reset_cutoff(daily_reset_rows, _midnight_central(today_date))
    weekly_cutoff = _reset_cutoff(weekly_reset_rows, _midnight_central(week_start_date))

    today_rows = await db.all(
        "SELECT step, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, web_searches, "
        "est_cost_usd, at FROM usage WHERE substr(at, 1, 10) = ?",
        today,
    )
    week_rows = await db.all(
        "SELECT input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, est_cost_usd, at "
        "FROM usage WHERE substr(at, 1, 10) >= ? AND substr(at, 1, 10) <= ?",
        week_start, today,
    )
    month_rows = await db.all(
        "SELECT input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, est_cost_usd "
        "FROM usage WHERE substr(at, 1, 10) >= ? AND substr(at, 1, 10) <= ?",
        month_start, today,
    )

    today_totals = UsageTotals()
    today_by_step: dict[str, UsageTotals] = {}
    for row in today_rows:
        if _parse_central(row["at"]) < daily_cutoff:
            continue
        totals = _row_totals(row)
        searches = int(row.get("web_searches") or 0)
        today_totals.tokens += totals.tokens
        today_totals.searches += searches
        today_totals.usd += totals.usd
        step_totals = today_by_step.setdefault(row["step"], UsageTotals())
        step_totals.tokens += totals.tokens
        step_totals.searches += searches
        step_totals.usd += totals.usd

    week_totals = UsageTotals()
    for row in week_rows:
        if _parse_central(row["at"]) < weekly_cutoff:
            continue
        totals = _row_totals(row)
        week_totals.tokens += totals.tokens
        week_totals.usd += totals.usd

    month_totals = UsageTotals()
    for row in month_rows:
        totals = _row_totals(row)
        month_totals.tokens += totals.tokens
        month_totals.usd += totals.usd

    return BudgetStatus(
        billing_mode=budget_settings.get("billing_mode") or "subscription",
        settings=budget_settings,
        today=today_totals,
        today_by_step=today_by_step,
        week=week_totals,
        month=month_totals,
        day_of_week=today_date.isoweekday(),
        day_of_month=today_date.day,
        days_in_month=_days_in_month(today_date.year, today_date.month),
    )
