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

ALERT_TYPES = frozenset({BUDGET_80, BUDGET_100, USAGE_SPIKE, CLAUDE_LIMIT, RUN_FAILED, CLAUDE_NOT_CONFIGURED})


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


def claude_limit_alert(message: str) -> AlertCandidate:
    return AlertCandidate(CLAUDE_LIMIT, "critical", f"A Claude step stopped early: {message}")


def run_failed_alert(message: str) -> AlertCandidate:
    return AlertCandidate(RUN_FAILED, "critical", f"The daily run failed: {message}")


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
