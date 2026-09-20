import asyncio

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
