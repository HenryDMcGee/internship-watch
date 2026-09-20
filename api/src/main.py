"""Internship Watch API: FastAPI app for the Cloudflare Python Worker.

Entry point for `pywrangler` (see api/wrangler.toml: `main = "src/main.py"`).
Everything under `/api/*` is handled by the `app` FastAPI instance below;
every other path is handed to the `ASSETS` binding so the dashboard in
`frontend/` loads. That split happens in the `Default` Worker entrypoint
at the bottom of this file, since only the entrypoint has access to `env`.

Run locally with `uv run pywrangler dev` from `api/` (see the README).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - zoneinfo is stdlib on 3.9+
    ZoneInfo = None  # type: ignore

import companies as companies_logic
from access import ACCESS_HEADER, AccessTokenError, fetch_jwks, verify_access_token
from db import Database

CENTRAL = ZoneInfo("America/Chicago") if ZoneInfo else None


def central_today() -> str:
    if CENTRAL is None:  # pragma: no cover - defensive fallback only
        return datetime.utcnow().date().isoformat()
    return datetime.now(CENTRAL).date().isoformat()


def now_iso() -> str:
    return datetime.now(CENTRAL).isoformat() if CENTRAL else datetime.utcnow().isoformat()


def _load_titles() -> dict:
    here = Path(__file__).resolve().parent
    bundled = here / "titles.json"
    if bundled.exists():
        return json.loads(bundled.read_text(encoding="utf-8"))
    # Fallback for local dev/tests when scripts/bundle_titles.py hasn't run
    # yet: read straight from the repo. Not available inside the deployed
    # Worker, which only has the files wrangler bundled.
    repo_config = here.parent.parent / "config" / "titles.json"
    return json.loads(repo_config.read_text(encoding="utf-8"))


app = FastAPI(title="Internship Watch API")
app.state.db = None
app.state.access_team_domain = os.environ.get("ACCESS_TEAM_DOMAIN", "")
app.state.access_aud = os.environ.get("ACCESS_AUD", "")

_jwks_cache: dict[str, dict[str, Any]] = {}


def get_db(request: Request) -> Database:
    if request.app.state.db is None:
        raise HTTPException(status_code=500, detail="Database is not configured")
    return request.app.state.db


async def require_access(request: Request) -> None:
    team_domain = request.app.state.access_team_domain
    aud = request.app.state.access_aud
    if not team_domain or not aud:
        # No Access config: local development without Cloudflare Access in
        # front of the server. See the README's "Local development"
        # section. In production, wrangler.toml always sets both.
        return

    token = request.headers.get(ACCESS_HEADER)
    if not token:
        raise HTTPException(status_code=401, detail="Missing Cloudflare Access token")

    cached = _jwks_cache.get(team_domain)
    if cached is None or cached["fetched_at"] < time.time() - 3600:
        try:
            jwks = await fetch_jwks(team_domain)
        except Exception as exc:  # noqa: BLE001 - surfaced as a 503 below
            raise HTTPException(status_code=503, detail="Could not reach Cloudflare Access") from exc
        cached = {"jwks": jwks, "fetched_at": time.time()}
        _jwks_cache[team_domain] = cached

    try:
        verify_access_token(token, cached["jwks"], audience=aud, team_domain=team_domain)
    except AccessTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


router = APIRouter(prefix="/api", dependencies=[Depends(require_access)])


# ---------------------------------------------------------------------------
# Postings
# ---------------------------------------------------------------------------

class AppliedUpdate(BaseModel):
    applied: bool


def _posting_out(row: dict) -> dict:
    row = dict(row)
    row["locations"] = json.loads(row.get("locations") or "[]")
    row["applied"] = bool(row.get("applied"))
    return row


@router.get("/postings")
async def list_postings(tab: str = "today", show_closed: bool = False, q: str | None = None, db: Database = Depends(get_db)):
    if tab not in ("today", "earlier", "archived"):
        raise HTTPException(status_code=400, detail="tab must be today, earlier, or archived")

    today = central_today()
    clauses: list[str] = []
    params: list[Any] = []

    if tab == "archived":
        clauses.append("archived_at IS NOT NULL")
    else:
        clauses.append("archived_at IS NULL")
        clauses.append("first_seen = ?" if tab == "today" else "first_seen != ?")
        params.append(today)
        if not show_closed:
            clauses.append("status != 'closed'")

    if q:
        clauses.append("(lower(company_name) LIKE ? OR lower(title) LIKE ? OR lower(locations) LIKE ? OR lower(coalesce(source_name, '')) LIKE ?)")
        like = f"%{q.lower()}%"
        params.extend([like, like, like, like])

    where = " AND ".join(clauses)
    if tab == "earlier":
        order = "ORDER BY first_seen DESC, company_name ASC"
    elif tab == "archived":
        order = "ORDER BY archived_at DESC"
    else:
        order = "ORDER BY company_name ASC"

    rows = await db.all(f"SELECT * FROM postings WHERE {where} {order}", *params)
    return {"tab": tab, "today": today, "postings": [_posting_out(r) for r in rows]}


@router.post("/postings/{posting_id}/applied")
async def mark_applied(posting_id: int, body: AppliedUpdate, db: Database = Depends(get_db)):
    existing = await db.first("SELECT * FROM postings WHERE id = ?", posting_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Posting not found")
    await db.run("UPDATE postings SET applied = ? WHERE id = ?", 1 if body.applied else 0, posting_id)
    updated = await db.first("SELECT * FROM postings WHERE id = ?", posting_id)
    return _posting_out(updated)


@router.delete("/postings/{posting_id}")
async def delete_posting(posting_id: int, db: Database = Depends(get_db)):
    existing = await db.first("SELECT * FROM postings WHERE id = ?", posting_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Posting not found")
    await db.run(
        "UPDATE postings SET archived_at = ?, archive_reason = ? WHERE id = ?",
        now_iso(), "Deleted by Henry", posting_id,
    )
    updated = await db.first("SELECT * FROM postings WHERE id = ?", posting_id)
    return _posting_out(updated)


@router.post("/postings/{posting_id}/restore")
async def restore_posting(posting_id: int, db: Database = Depends(get_db)):
    existing = await db.first("SELECT * FROM postings WHERE id = ?", posting_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Posting not found")
    await db.run("UPDATE postings SET archived_at = NULL, archive_reason = NULL WHERE id = ?", posting_id)
    updated = await db.first("SELECT * FROM postings WHERE id = ?", posting_id)
    return _posting_out(updated)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

class SourceCreate(BaseModel):
    name: str
    url: str
    kind: str = "Added by you"


@router.get("/sources")
async def list_sources(db: Database = Depends(get_db)):
    rows = await db.all("SELECT * FROM sources ORDER BY kind ASC, found_count DESC, name ASC")
    return {"sources": rows}


@router.post("/sources", status_code=201)
async def add_source(body: SourceCreate, db: Database = Depends(get_db)):
    if not body.name.strip() or not body.url.strip():
        raise HTTPException(status_code=422, detail="name and url are required")
    new_id = await db.run(
        """INSERT INTO sources (name, url, kind, covers, status, discovered_by, discovered_at, found_count, notes)
           VALUES (?, ?, ?, '', 'trial', 'henry', ?, 0, '')""",
        body.name.strip(), body.url.strip(), body.kind, now_iso(),
    )
    created = await db.first("SELECT * FROM sources WHERE id = ?", new_id)
    return created


@router.delete("/sources/{source_id}", status_code=204)
async def remove_source(source_id: int, db: Database = Depends(get_db)):
    existing = await db.first("SELECT * FROM sources WHERE id = ?", source_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Source not found")
    await db.run("DELETE FROM sources WHERE id = ?", source_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------

class CompanyCreate(BaseModel):
    name: str
    careers_url: str | None = None


class PastePreviewRequest(BaseModel):
    text: str


class PasteEntry(BaseModel):
    name: str
    url: str | None = None


class PasteSaveRequest(BaseModel):
    entries: list[PasteEntry]


def _company_out(row: dict) -> dict:
    row = dict(row)
    row["aliases"] = json.loads(row.get("aliases") or "[]")
    return row


@router.get("/companies")
async def list_companies(status: str | None = None, db: Database = Depends(get_db)):
    if status:
        rows = await db.all(
            "SELECT * FROM companies WHERE status = ? ORDER BY rank IS NULL, rank ASC, name ASC", status
        )
    else:
        rows = await db.all("SELECT * FROM companies ORDER BY rank IS NULL, rank ASC, name ASC")
    return {"companies": [_company_out(r) for r in rows]}


@router.post("/companies/{company_id}/pin")
async def pin_company(company_id: int, db: Database = Depends(get_db)):
    existing = await db.first("SELECT * FROM companies WHERE id = ?", company_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Company not found")
    await db.run("UPDATE companies SET status = 'pinned' WHERE id = ?", company_id)
    return _company_out(await db.first("SELECT * FROM companies WHERE id = ?", company_id))


@router.post("/companies/{company_id}/unpin")
async def unpin_company(company_id: int, db: Database = Depends(get_db)):
    existing = await db.first("SELECT * FROM companies WHERE id = ?", company_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Company not found")
    await db.run("UPDATE companies SET status = 'removed' WHERE id = ?", company_id)
    return _company_out(await db.first("SELECT * FROM companies WHERE id = ?", company_id))


@router.post("/companies", status_code=201)
async def add_company(body: CompanyCreate, db: Database = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    existing_rows = await db.all("SELECT name FROM companies")
    normalized = companies_logic.normalize_company_name(name)
    if any(companies_logic.normalize_company_name(r["name"]) == normalized for r in existing_rows):
        raise HTTPException(status_code=409, detail=f"{name} is already on the list")
    feed_type, feed_id = companies_logic.detect_feed(body.careers_url)
    new_id = await db.run(
        """INSERT INTO companies (name, aliases, status, careers_url, feed_type, feed_id, added_by, added_at)
           VALUES (?, '[]', 'pinned', ?, ?, ?, 'henry', ?)""",
        name, body.careers_url, feed_type, feed_id, now_iso(),
    )
    return _company_out(await db.first("SELECT * FROM companies WHERE id = ?", new_id))


@router.post("/companies/paste/preview")
async def preview_pasted_companies(body: PastePreviewRequest, db: Database = Depends(get_db)):
    existing_rows = await db.all("SELECT name FROM companies")
    existing_names = [r["name"] for r in existing_rows]
    entries = companies_logic.parse_pasted_companies(body.text, existing_names)
    return {"entries": [vars(e) for e in entries]}


@router.post("/companies/paste/save")
async def save_pasted_companies(body: PasteSaveRequest, db: Database = Depends(get_db)):
    existing_rows = await db.all("SELECT name FROM companies")
    existing_normalized = {companies_logic.normalize_company_name(r["name"]) for r in existing_rows}
    saved = []
    seen_normalized: set[str] = set()
    for entry in body.entries:
        name = entry.name.strip()
        if not name:
            continue
        normalized = companies_logic.normalize_company_name(name)
        if normalized in existing_normalized or normalized in seen_normalized:
            continue
        seen_normalized.add(normalized)
        feed_type, feed_id = companies_logic.detect_feed(entry.url)
        new_id = await db.run(
            """INSERT INTO companies (name, aliases, status, careers_url, feed_type, feed_id, added_by, added_at)
               VALUES (?, '[]', 'pinned', ?, ?, ?, 'paste', ?)""",
            name, entry.url, feed_type, feed_id, now_iso(),
        )
        saved.append(_company_out(await db.first("SELECT * FROM companies WHERE id = ?", new_id)))
    return {"saved": saved}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class LocationsUpdate(BaseModel):
    countries: list[str]
    include_remote: bool


@router.get("/settings/locations")
async def get_locations(db: Database = Depends(get_db)):
    row = await db.first("SELECT value FROM settings WHERE key = 'locations'")
    value = json.loads(row["value"]) if row else {"countries": ["US", "JP"], "include_remote": False}
    return value


@router.put("/settings/locations")
async def update_locations(body: LocationsUpdate, db: Database = Depends(get_db)):
    value = json.dumps({"countries": body.countries, "include_remote": body.include_remote})
    existing = await db.first("SELECT key FROM settings WHERE key = 'locations'")
    if existing:
        await db.run("UPDATE settings SET value = ? WHERE key = 'locations'", value)
    else:
        await db.run("INSERT INTO settings (key, value) VALUES ('locations', ?)", value)
    return json.loads(value)


# ---------------------------------------------------------------------------
# Titles (read only)
# ---------------------------------------------------------------------------

@router.get("/titles")
async def get_titles():
    return _load_titles()


app.include_router(router)


# ---------------------------------------------------------------------------
# Worker entrypoint: only this part touches Cloudflare's `env`. Everything
# above is plain FastAPI/ASGI and is what the pytest suite exercises.
# ---------------------------------------------------------------------------

try:
    from workers import WorkerEntrypoint  # type: ignore
    import asgi  # type: ignore

    from db import D1Database

    class Default(WorkerEntrypoint):  # pragma: no cover - only runs under Pyodide
        async def fetch(self, request):
            from js import URL  # type: ignore

            path = URL.new(request.url).pathname
            if not path.startswith("/api"):
                return await self.env.ASSETS.fetch(request)

            app.state.db = D1Database(self.env.DB)
            app.state.access_team_domain = getattr(self.env, "ACCESS_TEAM_DOMAIN", "") or ""
            app.state.access_aud = getattr(self.env, "ACCESS_AUD", "") or ""
            return await asgi.fetch(app, request, self.env)

except ImportError:
    # Not running inside a Python Worker (e.g. plain CPython under pytest).
    pass
