import asyncio

from job.agent.alerts import (
    ALERT_TYPES,
    BUDGET_100,
    BUDGET_80,
    CLAUDE_LIMIT,
    RUN_FAILED,
    USAGE_SPIKE,
    claude_limit_alert,
    evaluate_budget_alerts,
    evaluate_spike_alert,
    record_alerts,
    run_failed_alert,
)
from job.agent.budgets import BudgetStatus, UsageTotals

SETTINGS = {"billing_mode": "subscription", "daily_token_budget": 1000, "daily_search_budget": 100}


def run(coro):
    return asyncio.run(coro)


def _status(tokens):
    return BudgetStatus(billing_mode="subscription", settings=SETTINGS, today=UsageTotals(tokens=tokens))


# -- rules ------------------------------------------------------------------

def test_no_budget_alert_below_80_percent():
    assert evaluate_budget_alerts(_status(700)) == []


def test_budget_80_alert_at_80_percent():
    alerts = evaluate_budget_alerts(_status(800))
    assert [a.type for a in alerts] == [BUDGET_80]


def test_budget_100_alert_at_100_percent_not_also_80():
    alerts = evaluate_budget_alerts(_status(1000))
    assert [a.type for a in alerts] == [BUDGET_100]


def test_spike_alert_above_double_seven_day_average():
    alert = evaluate_spike_alert(tokens_today=500, seven_day_avg_tokens=200)
    assert alert is not None
    assert alert.type == USAGE_SPIKE


def test_no_spike_alert_at_exactly_double():
    assert evaluate_spike_alert(tokens_today=400, seven_day_avg_tokens=200) is None


def test_no_spike_alert_with_zero_average():
    assert evaluate_spike_alert(tokens_today=500, seven_day_avg_tokens=0) is None


def test_claude_limit_alert_type():
    assert claude_limit_alert("hit a wall").type == CLAUDE_LIMIT


def test_run_failed_alert_type():
    assert run_failed_alert("boom").type == RUN_FAILED


def test_all_alert_types_registered():
    assert ALERT_TYPES == {BUDGET_80, BUDGET_100, USAGE_SPIKE, CLAUDE_LIMIT, RUN_FAILED}


# -- one per type per day ----------------------------------------------------

def test_record_alerts_inserts_new_alert(empty_db):
    candidates = evaluate_budget_alerts(_status(800))
    recorded = run(record_alerts(empty_db, candidates, today="2026-09-20", now_iso="2026-09-20T08:00:00"))
    assert len(recorded) == 1
    rows = run(empty_db.all("SELECT * FROM alerts"))
    assert len(rows) == 1
    assert rows[0]["type"] == BUDGET_80


def test_record_alerts_skips_duplicate_type_same_day(empty_db):
    candidates = evaluate_budget_alerts(_status(800))
    run(record_alerts(empty_db, candidates, today="2026-09-20", now_iso="2026-09-20T08:00:00"))
    second = run(record_alerts(empty_db, candidates, today="2026-09-20", now_iso="2026-09-20T09:00:00"))
    assert second == []
    rows = run(empty_db.all("SELECT * FROM alerts"))
    assert len(rows) == 1


def test_record_alerts_allows_same_type_on_a_new_day(empty_db):
    candidates = evaluate_budget_alerts(_status(800))
    run(record_alerts(empty_db, candidates, today="2026-09-20", now_iso="2026-09-20T08:00:00"))
    second = run(record_alerts(empty_db, candidates, today="2026-09-21", now_iso="2026-09-21T08:00:00"))
    assert len(second) == 1
    rows = run(empty_db.all("SELECT * FROM alerts"))
    assert len(rows) == 2
