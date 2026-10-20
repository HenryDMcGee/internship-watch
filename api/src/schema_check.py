"""Checks that the tables and columns the API writes to exist in the connected database.

An unapplied migration used to surface as an error nobody could see. `check_schema`
returns one plain-English problem per missing table or column, so the Worker (and the
deploy check in `scripts/check_schema.py`) can fail loudly instead.

`REQUIRED_SCHEMA` lists what `api/src/main.py` writes: every table in an INSERT, UPDATE,
or DELETE, and the columns those statements name. A pytest keeps it covering every written
table, and runs the check against the real migrations.
"""
from __future__ import annotations

from typing import Any, Protocol

REQUIRED_SCHEMA: dict[str, tuple[str, ...]] = {
    "postings": ("applied", "public_hidden", "archived_at", "archive_reason"),
    "sources": ("name", "url", "kind", "covers", "status", "discovered_by", "discovered_at", "found_count", "notes"),
    "companies": (
        "name", "aliases", "status", "careers_url", "careers_url_approved", "feed_type", "feed_id",
        "added_by", "added_at", "careers_note", "careers_page_flagged", "careers_url_rejected",
    ),
    "careers_page_snapshots": ("company_id", "consecutive_failures", "last_error"),
    "check_requests": ("company_ids", "requested_at", "status", "result"),
    "search_requests": ("requested_at", "status", "result"),
    "settings": ("key", "value"),
    "feedback": (
        "source", "kind", "posting_id", "message", "page_url", "visitor_hash", "status", "issue_url",
        "created_at", "decided_at", "issue_created_at",
    ),
    "budget_settings": (
        "id", "updated_at",
        "daily_usd_budget", "monthly_usd_budget", "daily_search_budget",
        "search_limit_per_run", "search_limit_per_company", "browser_checks_per_run",
        "public_feedback_daily_limit",
        "feedback_issues_per_day", "model_discovery", "model_scout", "model_classification", "model_improvement",
        "share_discovery", "share_scout", "share_classification", "share_improvement",
    ),
    "budget_changes": ("changed_at", "field", "old_value", "new_value"),
    "budget_resets": ("scope", "reset_at", "note"),
    "proposals": ("status", "decided_at"),
    "public_board_views": ("day", "views"),
}

# Worker secrets the API cannot work without. Only the names are checked, never the values.
REQUIRED_SECRETS: tuple[str, ...] = ("FEEDBACK_SALT",)


def secret_problems(present: set[str]) -> list[str]:
    """One plain-English problem per required secret that is not set."""
    return [f"secret {name} is not set" for name in REQUIRED_SECRETS if name not in present]


class _Reader(Protocol):
    async def all(self, sql: str, *params: Any) -> list[dict]: ...


def problems_for(existing: dict[str, set[str]], required: dict[str, tuple[str, ...]] = REQUIRED_SCHEMA) -> list[str]:
    """Compares {table: columns that exist} with what is required. Empty means fine."""
    problems = []
    for table, columns in required.items():
        have = existing.get(table)
        if not have:
            problems.append(f"table {table} is missing")
            continue
        missing = [c for c in columns if c not in have]
        if missing:
            problems.append(f"table {table} is missing column(s): {', '.join(missing)}")
    return problems


async def check_schema(db: _Reader) -> list[str]:
    """Reads the live columns with PRAGMA table_info and returns the problems found."""
    existing: dict[str, set[str]] = {}
    for table in REQUIRED_SCHEMA:
        rows = await db.all(f"PRAGMA table_info({table})")
        existing[table] = {row["name"] for row in rows}
    return problems_for(existing)
