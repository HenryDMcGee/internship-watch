import asyncio

import pytest

from job.agent.client import ClaudeStepResult
from job.agent.usage import estimate_cost_usd, record_usage


def run(coro):
    return asyncio.run(coro)


def test_estimate_cost_usd_sonnet():
    cost = estimate_cost_usd("sonnet", input_tokens=1_000_000, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0)
    assert cost == 3.00


def test_estimate_cost_usd_haiku_cheaper_than_sonnet():
    haiku = estimate_cost_usd("haiku", input_tokens=1_000_000, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0)
    sonnet = estimate_cost_usd("sonnet", input_tokens=1_000_000, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0)
    assert haiku < sonnet


def test_estimate_cost_usd_unknown_model_falls_back_to_sonnet_rates():
    assert estimate_cost_usd("mystery", input_tokens=1_000_000, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0) == 3.00


def test_record_usage_run_13_discovery_step(empty_db):
    # Issue #26: run 13's real discovery `modelUsage` -- Sonnet orchestrates
    # (14 input tokens, 0 searches) while a Haiku sub-agent does the actual
    # searching (344,843 input tokens, 35 searches). The old code read only
    # the near-empty top-level fields and recorded ~38 input tokens and 0
    # searches; summing every model in `modelUsage` must record the real
    # totals, and `est_cost_usd` must be the CLI's own summed `costUSD`
    # rather than an estimate that prices Haiku's tokens at Sonnet's rate.
    result = ClaudeStepResult(
        step="discovery", model="sonnet", text="{}",
        input_tokens=14 + 344843, output_tokens=17748 + 14469,
        cache_read_tokens=349774, cache_write_tokens=69252,
        web_searches=0 + 35, cost_usd=0.5244708 + 0.767188,
    )
    row_id = run(record_usage(empty_db, run_id=1, step="discovery", result=result, now_iso="2026-09-20T08:00:00"))
    row = run(empty_db.first("SELECT * FROM usage WHERE id = ?", row_id))
    assert row["web_searches"] == 35
    assert row["input_tokens"] > 100_000
    assert row["cache_read_tokens"] == 349774
    assert row["cache_write_tokens"] == 69252
    assert row["est_cost_usd"] == pytest.approx(0.5244708 + 0.767188)


def test_record_usage_falls_back_to_estimate_without_cost_usd(empty_db):
    # No `modelUsage` in the CLI reply (older CLI version) means
    # `ClaudeStepResult.cost_usd` is `None` -- `record_usage` must still
    # store a non-zero estimate rather than writing `0` (or erroring).
    result = ClaudeStepResult(
        step="classification", model="haiku", text="{}",
        input_tokens=1000, output_tokens=500,
        cache_read_tokens=100, cache_write_tokens=50, web_searches=0,
        cost_usd=None,
    )
    row_id = run(record_usage(empty_db, run_id=1, step="classification", result=result, now_iso="2026-09-20T08:00:00"))
    row = run(empty_db.first("SELECT * FROM usage WHERE id = ?", row_id))
    expected = estimate_cost_usd(
        "haiku", input_tokens=1000, output_tokens=500, cache_read_tokens=100, cache_write_tokens=50
    )
    assert row["est_cost_usd"] == pytest.approx(expected)
    assert expected > 0


def test_record_usage_inserts_row(empty_db):
    result = ClaudeStepResult(
        step="discovery", model="sonnet", text="{}", input_tokens=1000, output_tokens=500,
        cache_read_tokens=100, cache_write_tokens=50, web_searches=3,
    )
    row_id = run(record_usage(empty_db, run_id=1, step="discovery", result=result, now_iso="2026-09-20T08:00:00"))
    assert row_id is not None
    row = run(empty_db.first("SELECT * FROM usage WHERE id = ?", row_id))
    assert row["run_id"] == 1
    assert row["step"] == "discovery"
    assert row["model"] == "sonnet"
    assert row["input_tokens"] == 1000
    assert row["web_searches"] == 3
    assert row["est_cost_usd"] > 0
