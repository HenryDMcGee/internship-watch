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


async def load_companies(db, statuses: tuple[str, ...] = ("ranked", "pinned")) -> list[dict]:
    placeholders = ", ".join("?" for _ in statuses)
    rows = await db.all(f"SELECT * FROM companies WHERE status IN ({placeholders})", *statuses)
    out = []
    for row in rows:
        row = dict(row)
        row["aliases"] = json.loads(row.get("aliases") or "[]")
        out.append(row)
    return out


async def load_companies_by_ids(db, company_ids: list[int]) -> list[dict]:
    if not company_ids:
        return []
    placeholders = ", ".join("?" for _ in company_ids)
    rows = await db.all(f"SELECT * FROM companies WHERE id IN ({placeholders})", *company_ids)
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
