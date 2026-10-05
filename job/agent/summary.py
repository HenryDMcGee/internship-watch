"""Posts the daily Claude routine summary, after every run including failed
ones, to `CLAUDE_ROUTINE_URL`.

Kept in one module since the routine API (`anthropic-beta:
experimental-cc-routine-2026-04-01`) is a research preview and the most
likely thing here to change shape later.

The payload never includes postings, company names, or anything else
personal -- only aggregate counts, budget settings, and alert text (which
callers must keep free of posting/company detail too; see
`pipeline.py`'s alert messages).
"""
from __future__ import annotations

import json
import os

import requests

ROUTINE_ANTHROPIC_VERSION = "2023-06-01"
ROUTINE_BETA = "experimental-cc-routine-2026-04-01"

# Keys allowed anywhere in the payload. Used by tests (and could be used
# defensively here) to prove nothing posting- or company-shaped leaks in.
FORBIDDEN_KEY_SUBSTRINGS = ("posting", "company", "title", "url", "location")


def build_summary_payload(
    *,
    date: str,
    run_status: str,
    run_reason: str | None,
    tokens_today: int,
    searches_today: int,
    budgets: dict,
    tokens_week_to_date: int,
    usage_by_step: list[dict],
    forecast: dict,
    alerts: list[dict],
    health: dict | None = None,
) -> dict:
    payload = {
        "date": date,
        "run_status": run_status,
        "run_reason": run_reason,
        "today": {"tokens": tokens_today, "searches": searches_today},
        "budgets": budgets,
        "week_to_date_tokens": tokens_week_to_date,
        "usage_by_step": usage_by_step,
        "forecast": forecast,
        "alerts": alerts,
    }
    if health is not None:
        # Failed check ids, how many days in a row found nothing new, and -- once that
        # reaches 3 -- a per-step breakdown (searches, covered, pages_loaded, classified).
        payload["health"] = health
    return payload


def send_daily_summary(payload: dict, *, url: str | None = None, token: str | None = None, session=None) -> bool:
    """POST the summary. Returns False (without raising) if the routine
    isn't configured, so a run without it set still completes normally.
    """
    url = url if url is not None else os.environ.get("CLAUDE_ROUTINE_URL")
    token = token if token is not None else os.environ.get("CLAUDE_ROUTINE_TOKEN")
    if not url or not token:
        return False

    session = session or requests
    session.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-version": ROUTINE_ANTHROPIC_VERSION,
            "anthropic-beta": ROUTINE_BETA,
            "Content-Type": "application/json",
        },
        json={"text": json.dumps(payload)},
        timeout=30,
    )
    return True
