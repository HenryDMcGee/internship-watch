"""Loads settings, companies, sources, and titles for a job run."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TITLES_PATH = ROOT / "config" / "titles.json"

API_SRC = ROOT / "api" / "src"
if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))

from sql_helpers import in_json  # noqa: E402


def load_titles(path: Path = TITLES_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


DEFAULT_LOCATIONS = {"countries": ["US", "JP"], "include_remote": False}


async def load_locations_setting(db) -> dict:
    row = await db.first("SELECT value FROM settings WHERE key = 'locations'")
    if not row:
        return dict(DEFAULT_LOCATIONS)
    return json.loads(row["value"])


async def load_budget_settings(db) -> dict:
    row = await db.first("SELECT * FROM budget_settings WHERE id = 1")
    return row or {}


async def load_watchlist_setting(db) -> list[str]:
    row = await db.first("SELECT value FROM settings WHERE key = 'watchlist'")
    if not row:
        return []
    entries = json.loads(row["value"])
    # A real watchlist entry is an object with a "company" name and an optional
    # "note" (see seed/settings.json and the dashboard's watch-list panel) -- only
    # the name is needed here. A bare string is also accepted, since that's what
    # `discovery.order_companies` and its tests otherwise assume. Without this,
    # `order_companies` called `.strip()` on the whole entry dict and crashed
    # discovery for every run (issue #20, run 11).
    return [e["company"] if isinstance(e, dict) else e for e in entries]


async def load_search_plan(db, plan_date: str) -> dict | None:
    row = await db.first("SELECT plan FROM search_plans WHERE plan_date = ?", plan_date)
    return json.loads(row["plan"]) if row else None


async def load_companies(db, statuses: tuple[str, ...] = ("ranked", "pinned")) -> list[dict]:
    clause, param = in_json("status", statuses)
    rows = await db.all(f"SELECT * FROM companies WHERE {clause}", param)
    out = []
    for row in rows:
        row = dict(row)
        row["aliases"] = json.loads(row.get("aliases") or "[]")
        out.append(row)
    return out


async def load_companies_by_ids(db, company_ids: list[int]) -> list[dict]:
    if not company_ids:
        return []
    clause, param = in_json("id", company_ids)
    rows = await db.all(f"SELECT * FROM companies WHERE {clause}", param)
    out = []
    for row in rows:
        row = dict(row)
        row["aliases"] = json.loads(row.get("aliases") or "[]")
        out.append(row)
    return out


async def load_sources(db) -> list[dict]:
    rows = await db.all("SELECT * FROM sources")
    return [dict(r) for r in rows]


def active_titles(titles_doc: dict) -> list[dict]:
    return [t for t in titles_doc.get("titles", []) if t.get("active")]
