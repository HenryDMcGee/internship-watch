"""Records every Claude call in the `usage` table, with its cost.

`est_cost_usd` matters most in `api_key` billing mode, where it's what
`budgets.py` enforces against; in `subscription` mode it's kept only for
Analytics, since budgets there are tokens and searches, not dollars.
"""
from __future__ import annotations

from job.agent.client import ClaudeStepResult

# Approximate published per-million-token rates in USD. Update if
# Anthropic's pricing changes; `est_cost_usd` is a forecasting aid, not a
# billing record (Anthropic's own billing is authoritative for that).
MODEL_PRICING_USD_PER_MTOK = {
    "sonnet": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "haiku": {"input": 0.80, "output": 4.00, "cache_write": 1.00, "cache_read": 0.08},
}


def estimate_cost_usd(
    model: str, *, input_tokens: int, output_tokens: int, cache_read_tokens: int, cache_write_tokens: int
) -> float:
    rates = MODEL_PRICING_USD_PER_MTOK.get((model or "").strip().lower(), MODEL_PRICING_USD_PER_MTOK["sonnet"])
    total = (
        input_tokens * rates["input"]
        + output_tokens * rates["output"]
        + cache_read_tokens * rates["cache_read"]
        + cache_write_tokens * rates["cache_write"]
    )
    return total / 1_000_000


async def record_usage(db, *, run_id: int, step: str, result: ClaudeStepResult, now_iso: str) -> int:
    # `result.cost_usd` is the CLI's own sum of `costUSD` across every model
    # in `modelUsage` (see `client.py`'s `_usage_from_payload`) -- it's
    # preferred over our own per-token estimate because a web-search step's
    # tokens are split across the orchestrating model and a Haiku sub-agent
    # (issue #26), and estimating from summed tokens under a single model's
    # rate misprices whichever model didn't do most of the work. Only a
    # reply with no `modelUsage` at all (older CLI) falls back to the
    # estimate below.
    cost = result.cost_usd
    if cost is None:
        cost = estimate_cost_usd(
            result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_write_tokens=result.cache_write_tokens,
        )
    return await db.run(
        """INSERT INTO usage
           (run_id, step, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
            web_searches, est_cost_usd, at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        run_id, step, result.model, result.input_tokens, result.output_tokens,
        result.cache_read_tokens, result.cache_write_tokens, result.web_searches, cost, now_iso,
    )
