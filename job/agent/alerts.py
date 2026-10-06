"""Alert rules: 80% and 100% of budget, a usage spike, a run stopped by a
Claude limit, a failed run, and Claude credentials missing from the
environment. One alert per type per day, saved to `alerts` -- enforced here
by checking for an existing row of that type dated today before inserting
another.
"""
from __future__ import annotations

from dataclasses import dataclass

from job.agent.budgets import BudgetStatus

BUDGET_80 = "budget_80"
BUDGET_100 = "budget_100"
USAGE_SPIKE = "usage_spike"
CLAUDE_LIMIT = "claude_limit"
RUN_FAILED = "run_failed"
CLAUDE_NOT_CONFIGURED = "claude_not_configured"
CLAUDE_STEP_ISSUE = "claude_step_issue"
BROWSER_RUN_80 = "browser_run_80"
BROWSER_RUN_100 = "browser_run_100"
BROWSER_RUN_MONTH_80 = "browser_run_month_80"
BROWSER_RUN_MONTH_100 = "browser_run_month_100"
WORKERS_PAID_SUGGESTED = "workers_paid_suggested"
DISCOVERY_FAILED = "discovery_failed"
RUN_DEGRADED = "run_degraded"
LONG_RUN = "long_run"

ALERT_TYPES = frozenset({
    DISCOVERY_FAILED, RUN_DEGRADED, LONG_RUN,
    BUDGET_80, BUDGET_100, USAGE_SPIKE, CLAUDE_LIMIT, RUN_FAILED, CLAUDE_NOT_CONFIGURED, CLAUDE_STEP_ISSUE,
    BROWSER_RUN_80, BROWSER_RUN_100, BROWSER_RUN_MONTH_80, BROWSER_RUN_MONTH_100, WORKERS_PAID_SUGGESTED,
})

# Days within a trailing week the backup has to have taken over for minutes-
# or quota-capacity reasons before the daily summary suggests Workers Paid
# (issue #55). An outage-caused switch never counts -- that's a site/network
# problem, not a Browser Run capacity one.
WORKERS_PAID_SUGGESTION_DAYS = 3


@dataclass
class AlertCandidate:
    type: str
    severity: str  # "warning" or "critical"
    message: str


def evaluate_budget_alerts(status: BudgetStatus) -> list[AlertCandidate]:
    fraction = status.worst_fraction_used()
    if fraction >= 1.0:
        return [AlertCandidate(BUDGET_100, "critical", f"Today's Claude budget is at {fraction:.0%}.")]
    if fraction >= 0.8:
        return [AlertCandidate(BUDGET_80, "warning", f"Today's Claude budget is at {fraction:.0%}.")]
    return []


def evaluate_spike_alert(tokens_today: int, seven_day_avg_tokens: float) -> AlertCandidate | None:
    if seven_day_avg_tokens > 0 and tokens_today > 2 * seven_day_avg_tokens:
        return AlertCandidate(
            USAGE_SPIKE, "warning",
            f"Today's token usage ({tokens_today:,}) is more than double the 7-day average "
            f"({seven_day_avg_tokens:,.0f}).",
        )
    return None


def evaluate_browser_run_alerts(
    *, minutes_used_today: float, daily_minutes_budget: float,
    plan: str = "free", hours_used_month: float | None = None, hours_included_month: float | None = None,
) -> list[AlertCandidate]:
    """Browser Run minutes at 80%/100% of the job's own daily budget, and --
    on the Paid plan only -- 80%/100% of the monthly included hours (issue
    #55). Each fires at most once a day, same as every other alert here
    (`record_alerts`'s de-duplication, not this function).
    """
    candidates: list[AlertCandidate] = []
    daily_fraction = (minutes_used_today / daily_minutes_budget) if daily_minutes_budget > 0 else 0.0
    if daily_fraction >= 1.0:
        candidates.append(AlertCandidate(
            BROWSER_RUN_100, "critical",
            f"Browser Run's daily minutes budget is at {daily_fraction:.0%} ({minutes_used_today:.1f} of "
            f"{daily_minutes_budget:.1f} min).",
        ))
    elif daily_fraction >= 0.8:
        candidates.append(AlertCandidate(
            BROWSER_RUN_80, "warning",
            f"Browser Run's daily minutes budget is at {daily_fraction:.0%} ({minutes_used_today:.1f} of "
            f"{daily_minutes_budget:.1f} min).",
        ))

    if plan == "paid" and hours_included_month and hours_included_month > 0 and hours_used_month is not None:
        monthly_fraction = hours_used_month / hours_included_month
        if monthly_fraction >= 1.0:
            candidates.append(AlertCandidate(
                BROWSER_RUN_MONTH_100, "critical",
                f"Browser Run's included monthly hours are at {monthly_fraction:.0%} ({hours_used_month:.1f} of "
                f"{hours_included_month:.1f} hours) -- extra usage is billed as overage.",
            ))
        elif monthly_fraction >= 0.8:
            candidates.append(AlertCandidate(
                BROWSER_RUN_MONTH_80, "warning",
                f"Browser Run's included monthly hours are at {monthly_fraction:.0%} ({hours_used_month:.1f} of "
                f"{hours_included_month:.1f} hours).",
            ))
    return candidates


def evaluate_workers_paid_suggestion(capacity_backup_days: "set[str] | list[str]") -> AlertCandidate | None:
    """Suggests Workers Paid once the backup has taken over for a capacity
    reason ('minutes' or 'quota', never 'outage' or 'missing_token') on at
    least `WORKERS_PAID_SUGGESTION_DAYS` distinct days within a trailing
    week (issue #55).
    """
    if len(set(capacity_backup_days)) >= WORKERS_PAID_SUGGESTION_DAYS:
        return AlertCandidate(
            WORKERS_PAID_SUGGESTED, "warning",
            f"The backup took over because Browser Run's minutes or quota ran out on "
            f"{len(set(capacity_backup_days))} of the last 7 days. Consider Workers Paid for more Browser "
            f"Rendering minutes.",
        )
    return None


def claude_limit_alert(message: str) -> AlertCandidate:
    return AlertCandidate(CLAUDE_LIMIT, "critical", f"A Claude step stopped early: {message}")


def run_failed_alert(message: str) -> AlertCandidate:
    return AlertCandidate(RUN_FAILED, "critical", f"The daily run failed: {message}")


def evaluate_long_run_alert(run_minutes: float | None, *, limit_minutes: float = 30) -> AlertCandidate | None:
    """A run over `limit_minutes` (issue #70): the private repo has 2,000 free
    Actions minutes a month, and a run this long is a sign a step is too slow."""
    if run_minutes is not None and run_minutes > limit_minutes:
        return AlertCandidate(
            LONG_RUN, "warning",
            f"Today's run took {run_minutes:.0f} minutes, over {limit_minutes:.0f}. The private repo has "
            "2,000 free Actions minutes a month.",
        )
    return None


def discovery_failed_alert(reason: str) -> AlertCandidate:
    return AlertCandidate(DISCOVERY_FAILED, "critical", f"Discovery failed: {reason}")


def run_degraded_alert(failed_checks: list[str]) -> AlertCandidate:
    return AlertCandidate(
        RUN_DEGRADED, "critical", "Today's run is degraded: " + "; ".join(failed_checks) + ".",
    )


def claude_step_issue_alert(details: list[str], *, no_claude_work_done: bool) -> AlertCandidate:
    """A Claude step was skipped or failed today, even though Claude is
    configured and budget isn't the reason. Fires whenever `agent_outcome`
    (see `pipeline.run_agent_steps`) has anything in `errors` or
    `steps_skipped` -- three separate bugs hid behind those two lists going
    unreported (issue #20): a skipped or failed step must never look like a
    clean run. Critical when nothing at all succeeded that day (discovery,
    classification, the scout, and daily improvement all skipped or failed),
    since that's a run that did no Claude work while looking otherwise
    normal; a warning when it's a partial issue among steps that did run.
    """
    severity = "critical" if no_claude_work_done else "warning"
    lead = "No Claude step completed today" if no_claude_work_done else "A Claude step was skipped or failed today"
    return AlertCandidate(CLAUDE_STEP_ISSUE, severity, f"{lead}: {'; '.join(details)}")


def claude_not_configured_alert() -> AlertCandidate:
    # A run without either secret set still completes (feeds, matching,
    # verification, pruning), so this can't be a run_failed alert -- but
    # discovery, classification, the source scout, and daily improvement
    # all silently did nothing, which is exactly the failure mode that
    # went unnoticed until issue #16. Fires once a day for as long as the
    # environment is missing both secrets.
    return AlertCandidate(
        CLAUDE_NOT_CONFIGURED, "critical",
        "Claude steps were skipped: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set, "
        "so discovery, classification, the source scout, and daily improvement did not run today.",
    )


async def record_alerts(db, candidates: list[AlertCandidate], *, today: str, now_iso: str) -> list[dict]:
    recorded: list[dict] = []
    for candidate in candidates:
        existing = await db.first(
            "SELECT id FROM alerts WHERE type = ? AND substr(created_at, 1, 10) = ?", candidate.type, today
        )
        if existing:
            continue
        new_id = await db.run(
            "INSERT INTO alerts (type, severity, message, created_at) VALUES (?, ?, ?, ?)",
            candidate.type, candidate.severity, candidate.message, now_iso,
        )
        recorded.append({
            "id": new_id, "type": candidate.type, "severity": candidate.severity,
            "message": candidate.message, "created_at": now_iso,
        })
    return recorded
