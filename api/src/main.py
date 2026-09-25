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
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - zoneinfo is stdlib on 3.9+
    ZoneInfo = None  # type: ignore

import companies as companies_logic
from access import ACCESS_HEADER, AccessTokenError, fetch_jwks, verify_access_token
from budgets import (
    MODEL_FIELDS,
    NUMERIC_FIELDS,
    SHARE_FIELDS,
    daily_fraction_used,
    linear_forecast,
    validate_budget_settings,
)
from db import Database
from github_client import GitHubApiError, GitHubClient
from sql_helpers import in_json
from titles import load_titles_doc, open_or_update_titles_pr, validate_titles_doc, BASE_BRANCH, TITLES_BRANCH

REPO = "HenryDMcGee/internship-watch"
DAILY_CHECK_LIMIT = 5
SEARCH_BUDGET_WARN_FRACTION = 0.80

CENTRAL = ZoneInfo("America/Chicago") if ZoneInfo else None


def central_today() -> str:
    if CENTRAL is None:  # pragma: no cover - defensive fallback only
        return datetime.utcnow().date().isoformat()
    return datetime.now(CENTRAL).date().isoformat()


def now_iso() -> str:
    return datetime.now(CENTRAL).isoformat() if CENTRAL else datetime.utcnow().isoformat()


def _midnight_central(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time(), tzinfo=CENTRAL) if CENTRAL else datetime.combine(d, datetime.min.time())


def _parse_central(value: str) -> datetime:
    """Parses a stored `at`/`reset_at` timestamp. Both are written by
    `now_iso()`, which always includes a Central offset in production;
    naive strings (some test fixtures) are treated as already being
    Central local time.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None and CENTRAL is not None:
        dt = dt.replace(tzinfo=CENTRAL)
    return dt


def _reset_cutoff(reset_rows: list[dict], window_start: datetime) -> datetime:
    """The later of `window_start` and the most recent reset in
    `reset_rows` -- usage before this cutoff doesn't count toward the
    window's budget (issue #38).
    """
    latest = None
    for row in reset_rows:
        candidate = _parse_central(row["reset_at"])
        if latest is None or candidate > latest:
            latest = candidate
    return max(latest, window_start) if latest else window_start


async def _budget_cutoffs(db: Database, today_date: date) -> tuple[datetime, datetime, date]:
    week_start_date = today_date - timedelta(days=today_date.weekday())
    daily_rows = await db.all(
        "SELECT reset_at FROM budget_resets WHERE scope = 'daily' AND substr(reset_at, 1, 10) = ?",
        today_date.isoformat(),
    )
    weekly_rows = await db.all(
        "SELECT reset_at FROM budget_resets WHERE scope = 'weekly' AND substr(reset_at, 1, 10) >= ? "
        "AND substr(reset_at, 1, 10) <= ?",
        week_start_date.isoformat(), today_date.isoformat(),
    )
    daily_cutoff = _reset_cutoff(daily_rows, _midnight_central(today_date))
    weekly_cutoff = _reset_cutoff(weekly_rows, _midnight_central(week_start_date))
    return daily_cutoff, weekly_cutoff, week_start_date


async def _current_usage(db: Database, today_date: date) -> dict:
    """Today's and this week's usage, counting only usage since the later
    of the window's start or its most recent reset (issue #38).
    """
    daily_cutoff, weekly_cutoff, week_start_date = await _budget_cutoffs(db, today_date)
    today = today_date.isoformat()
    week_start = week_start_date.isoformat()

    today_rows = await db.all(
        "SELECT input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, web_searches, "
        "est_cost_usd, at FROM usage WHERE substr(at, 1, 10) = ?",
        today,
    )
    week_rows = await db.all(
        "SELECT input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, at "
        "FROM usage WHERE substr(at, 1, 10) >= ? AND substr(at, 1, 10) <= ?",
        week_start, today,
    )

    daily_tokens = daily_searches = 0
    daily_usd = 0.0
    for row in today_rows:
        if _parse_central(row["at"]) < daily_cutoff:
            continue
        daily_tokens += (
            (row.get("input_tokens") or 0) + (row.get("output_tokens") or 0)
            + (row.get("cache_read_tokens") or 0) + (row.get("cache_write_tokens") or 0)
        )
        daily_searches += row.get("web_searches") or 0
        daily_usd += row.get("est_cost_usd") or 0

    weekly_tokens = 0
    for row in week_rows:
        if _parse_central(row["at"]) < weekly_cutoff:
            continue
        weekly_tokens += (
            (row.get("input_tokens") or 0) + (row.get("output_tokens") or 0)
            + (row.get("cache_read_tokens") or 0) + (row.get("cache_write_tokens") or 0)
        )

    return {
        "daily": {"tokens": daily_tokens, "searches": daily_searches, "usd": round(daily_usd, 4)},
        "weekly": {"tokens": weekly_tokens},
    }


async def _latest_resets(db: Database) -> dict:
    resets = {}
    for scope in ("daily", "weekly"):
        resets[scope] = await db.first(
            "SELECT * FROM budget_resets WHERE scope = ? ORDER BY reset_at DESC LIMIT 1", scope
        )
    return resets


app = FastAPI(title="Internship Watch API")
app.state.db = None
app.state.access_team_domain = os.environ.get("ACCESS_TEAM_DOMAIN", "")
app.state.access_aud = os.environ.get("ACCESS_AUD", "")
app.state.github = None  # set in the Worker entrypoint from the GITHUB_TOKEN secret

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


class ConfirmUpdate(BaseModel):
    status: str  # "open" or "closed"


@router.post("/postings/{posting_id}/confirm")
async def confirm_posting(posting_id: int, body: ConfirmUpdate, db: Database = Depends(get_db)):
    if body.status not in ("open", "closed"):
        raise HTTPException(status_code=422, detail="status must be 'open' or 'closed'")
    existing = await db.first("SELECT * FROM postings WHERE id = ?", posting_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Posting not found")
    at = now_iso()
    await db.run(
        """UPDATE postings
           SET user_status = ?, user_status_at = ?, status = ?, verified_via = 'user', verified_at = ?
           WHERE id = ?""",
        body.status, at, body.status, at, posting_id,
    )
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
# Check now
# ---------------------------------------------------------------------------

class CheckCompaniesRequest(BaseModel):
    company_ids: list[int]


def _check_request_out(row: dict) -> dict:
    row = dict(row)
    row["company_ids"] = json.loads(row.get("company_ids") or "[]")
    row["result"] = json.loads(row["result"]) if row.get("result") else None
    return row


@router.post("/companies/check", status_code=201)
async def check_companies(body: CheckCompaniesRequest, request: Request, db: Database = Depends(get_db)):
    if not body.company_ids:
        raise HTTPException(status_code=422, detail="company_ids is required")

    clause, param = in_json("id", body.company_ids)
    found = await db.all(f"SELECT id FROM companies WHERE {clause}", param)
    if len(found) != len(set(body.company_ids)):
        raise HTTPException(status_code=404, detail="One or more companies were not found")

    today = central_today()
    count_row = await db.first(
        "SELECT COUNT(*) AS n FROM check_requests WHERE substr(requested_at, 1, 10) = ?", today
    )
    if (count_row["n"] if count_row else 0) >= DAILY_CHECK_LIMIT:
        raise HTTPException(status_code=429, detail=f"Check now is limited to {DAILY_CHECK_LIMIT} requests a day. Try again tomorrow.")

    new_id = await db.run(
        "INSERT INTO check_requests (company_ids, requested_at, status) VALUES (?, ?, 'pending')",
        json.dumps(body.company_ids), now_iso(),
    )

    github: GitHubClient | None = request.app.state.github
    if github is not None:
        try:
            await github.dispatch_workflow("check-now.yml", "master", {"request_id": str(new_id)})
        except GitHubApiError as exc:
            await db.run(
                "UPDATE check_requests SET status = 'failed', result = ? WHERE id = ?",
                json.dumps({"error": str(exc)}), new_id,
            )
            raise HTTPException(status_code=502, detail=f"Could not start the check: {exc}") from exc

    created = await db.first("SELECT * FROM check_requests WHERE id = ?", new_id)
    return _check_request_out(created)


@router.get("/companies/check/{request_id}")
async def check_companies_status(request_id: int, db: Database = Depends(get_db)):
    row = await db.first("SELECT * FROM check_requests WHERE id = ?", request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Check request not found")
    return _check_request_out(row)


# ---------------------------------------------------------------------------
# Search now: runs the same daily search as the 8am schedule, across every
# company, started through GitHub's API like Check now.
# ---------------------------------------------------------------------------

class SearchStartRequest(BaseModel):
    confirm_budget: bool = False


def _search_request_out(row: dict) -> dict:
    row = dict(row)
    row["result"] = json.loads(row["result"]) if row.get("result") else None
    return row


async def _search_busy_reason(db: Database) -> str | None:
    """None if it's safe to start a new search, else the message to show.

    Covers three overlapping windows: a run already recorded in `runs`
    (any kind -- the daily job and Check now share one Actions concurrency
    group, so only one ever executes at a time), and a Check now or Search
    now request that's been dispatched but hasn't started running yet (no
    `runs` row exists for it until the Actions runner picks it up).
    """
    running_run = await db.first("SELECT id FROM runs WHERE finished_at IS NULL LIMIT 1")
    if running_run:
        return "A search is already running"
    pending_check = await db.first("SELECT id FROM check_requests WHERE status IN ('pending', 'running') LIMIT 1")
    if pending_check:
        return "A search is already running"
    pending_search = await db.first("SELECT id FROM search_requests WHERE status IN ('pending', 'running') LIMIT 1")
    if pending_search:
        return "A search is already running"
    return None


@router.get("/search/status")
async def search_status(db: Database = Depends(get_db)):
    reason = await _search_busy_reason(db)
    return {"busy": reason is not None, "reason": reason}


@router.post("/search/runs", status_code=201)
async def start_search(body: SearchStartRequest, request: Request, db: Database = Depends(get_db)):
    reason = await _search_busy_reason(db)
    if reason:
        raise HTTPException(status_code=409, detail=reason)

    settings = await db.first("SELECT * FROM budget_settings WHERE id = 1") or {}
    today = central_today()
    usage = await _current_usage(db, date.fromisoformat(today))
    fraction = daily_fraction_used(
        settings,
        tokens=usage["daily"]["tokens"],
        searches=usage["daily"]["searches"],
        usd=usage["daily"]["usd"],
    )
    if fraction >= SEARCH_BUDGET_WARN_FRACTION and not body.confirm_budget:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "budget_warning",
                "message": f"Today's budget is {fraction:.0%} used. A full search may be cut short. Search anyway?",
            },
        )

    new_id = await db.run(
        "INSERT INTO search_requests (requested_at, status) VALUES (?, 'pending')", now_iso(),
    )

    github: GitHubClient | None = request.app.state.github
    if github is not None:
        try:
            await github.dispatch_workflow("daily-search.yml", "master", {})
        except GitHubApiError as exc:
            await db.run(
                "UPDATE search_requests SET status = 'failed', result = ? WHERE id = ?",
                json.dumps({"error": str(exc)}), new_id,
            )
            raise HTTPException(status_code=502, detail=f"Could not start the search: {exc}") from exc

    created = await db.first("SELECT * FROM search_requests WHERE id = ?", new_id)
    return _search_request_out(created)


@router.get("/search/runs/{request_id}")
async def search_run_status(request_id: int, db: Database = Depends(get_db)):
    row = await db.first("SELECT * FROM search_requests WHERE id = ?", request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Search request not found")
    return _search_request_out(row)


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
# Titles
# ---------------------------------------------------------------------------

class TitlesUpdate(BaseModel):
    doc: dict[str, Any]


@router.get("/titles")
async def get_titles(request: Request):
    github: GitHubClient | None = request.app.state.github
    if github is None:
        raise HTTPException(status_code=503, detail="GitHub isn't configured for this environment")
    try:
        pending = await github.find_open_pr(TITLES_BRANCH)
        ref = TITLES_BRANCH if pending else BASE_BRANCH
        return await load_titles_doc(github, ref)
    except GitHubApiError as exc:
        raise HTTPException(status_code=502, detail=f"Could not read titles from GitHub: {exc}") from exc


@router.get("/titles/pending")
async def pending_titles_pr(request: Request):
    github: GitHubClient | None = request.app.state.github
    if github is None:
        return {"pending": None}
    try:
        pr = await github.find_open_pr(TITLES_BRANCH)
    except GitHubApiError as exc:
        raise HTTPException(status_code=502, detail=f"Could not check for a pending change: {exc}") from exc
    return {"pending": pr}


@router.put("/titles")
async def update_titles(body: TitlesUpdate, request: Request):
    problems = validate_titles_doc(body.doc)
    if problems:
        raise HTTPException(status_code=422, detail={"problems": problems})

    github: GitHubClient | None = request.app.state.github
    if github is None:
        raise HTTPException(status_code=503, detail="GitHub isn't configured for this environment")

    try:
        pr = await open_or_update_titles_pr(github, body.doc)
    except GitHubApiError as exc:
        raise HTTPException(status_code=502, detail=f"Could not open the pull request: {exc}") from exc
    return {"pending": pr}


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------

_BUDGET_FIELDS = (*NUMERIC_FIELDS, *MODEL_FIELDS, *SHARE_FIELDS, "billing_mode")


async def _budget_forecast(db: Database, settings: dict) -> dict:
    today = central_today()
    today_date = date.fromisoformat(today)
    month_start = today_date.replace(day=1).isoformat()
    days_in_month = (
        31 if today_date.month == 12
        else (date(today_date.year, today_date.month + 1, 1) - date(today_date.year, today_date.month, 1)).days
    )

    usage = await _current_usage(db, today_date)
    month_row = await db.first(
        "SELECT SUM(est_cost_usd) AS usd FROM usage WHERE substr(at, 1, 10) >= ? AND substr(at, 1, 10) <= ?",
        month_start, today,
    )
    weekly_tokens = linear_forecast(usage["weekly"]["tokens"], today_date.isoweekday(), 7)
    monthly_usd = linear_forecast((month_row or {}).get("usd") or 0, today_date.day, days_in_month)
    return {"weekly_tokens": round(weekly_tokens, 2), "monthly_usd": round(monthly_usd, 2)}


class BudgetsUpdate(BaseModel):
    doc: dict[str, Any]


class BudgetResetCreate(BaseModel):
    scope: str
    note: str | None = None


@router.get("/budgets")
async def get_budgets(db: Database = Depends(get_db)):
    settings = await db.first("SELECT * FROM budget_settings WHERE id = 1")
    changes = await db.all("SELECT * FROM budget_changes ORDER BY id DESC LIMIT 50")
    forecast = await _budget_forecast(db, settings or {})
    usage = await _current_usage(db, date.fromisoformat(central_today()))
    resets = await _latest_resets(db)
    return {"settings": settings, "changes": changes, "forecast": forecast, "usage": usage, "resets": resets}


@router.put("/budgets")
async def update_budgets(body: BudgetsUpdate, db: Database = Depends(get_db)):
    problems = validate_budget_settings(body.doc)
    if problems:
        raise HTTPException(status_code=422, detail={"problems": problems})

    existing = await db.first("SELECT * FROM budget_settings WHERE id = 1") or {}
    at = now_iso()
    for field in _BUDGET_FIELDS:
        old_value = existing.get(field)
        new_value = body.doc.get(field)
        if old_value != new_value:
            await db.run(
                "INSERT INTO budget_changes (changed_at, field, old_value, new_value) VALUES (?, ?, ?, ?)",
                at, field, str(old_value), str(new_value),
            )

    set_clause = ", ".join(f"{field} = ?" for field in _BUDGET_FIELDS)
    await db.run(
        f"UPDATE budget_settings SET {set_clause}, updated_at = ? WHERE id = 1",
        *[body.doc[field] for field in _BUDGET_FIELDS], at,
    )
    settings = await db.first("SELECT * FROM budget_settings WHERE id = 1")
    changes = await db.all("SELECT * FROM budget_changes ORDER BY id DESC LIMIT 50")
    forecast = await _budget_forecast(db, settings or {})
    usage = await _current_usage(db, date.fromisoformat(central_today()))
    resets = await _latest_resets(db)
    return {"settings": settings, "changes": changes, "forecast": forecast, "usage": usage, "resets": resets}


@router.post("/budgets/resets", status_code=201)
async def create_budget_reset(body: BudgetResetCreate, db: Database = Depends(get_db)):
    if body.scope not in ("daily", "weekly"):
        raise HTTPException(status_code=422, detail="scope must be 'daily' or 'weekly'")

    today_date = date.fromisoformat(central_today())
    usage = await _current_usage(db, today_date)
    at = now_iso()

    if body.scope == "daily":
        old_value = f"{usage['daily']['tokens']:,} tokens, {usage['daily']['searches']} searches"
    else:
        old_value = f"{usage['weekly']['tokens']:,} tokens"

    await db.run(
        "INSERT INTO budget_resets (scope, reset_at, note) VALUES (?, ?, ?)",
        body.scope, at, body.note,
    )
    await db.run(
        "INSERT INTO budget_changes (changed_at, field, old_value, new_value) VALUES (?, ?, ?, ?)",
        at, f"{body.scope}_reset", old_value, "0",
    )

    settings = await db.first("SELECT * FROM budget_settings WHERE id = 1")
    changes = await db.all("SELECT * FROM budget_changes ORDER BY id DESC LIMIT 50")
    forecast = await _budget_forecast(db, settings or {})
    usage_after = await _current_usage(db, today_date)
    resets = await _latest_resets(db)
    return {"settings": settings, "changes": changes, "forecast": forecast, "usage": usage_after, "resets": resets}


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@router.get("/analytics")
async def get_analytics(db: Database = Depends(get_db)):
    coverage = await db.all(
        """SELECT c.id, c.name, c.status, c.last_feed_status, c.last_result_at,
                  COUNT(p.id) AS posting_count
           FROM companies c LEFT JOIN postings p ON p.company_id = c.id AND p.archived_at IS NULL
           WHERE c.status IN ('ranked', 'pinned')
           GROUP BY c.id ORDER BY posting_count DESC, c.name ASC"""
    )
    sources = await db.all(
        "SELECT id, name, kind, status, found_count, dead_link_rate, last_checked "
        "FROM sources ORDER BY found_count DESC, name ASC"
    )
    first_to_find = await db.all(
        "SELECT source_name, COUNT(*) AS n FROM postings WHERE source_name IS NOT NULL "
        "GROUP BY source_name ORDER BY n DESC"
    )
    postings_per_title = await db.all(
        "SELECT matched_title, COUNT(*) AS n FROM postings WHERE archived_at IS NULL "
        "GROUP BY matched_title ORDER BY n DESC"
    )
    verification_methods = await db.all(
        "SELECT verified_via, COUNT(*) AS n FROM postings WHERE archived_at IS NULL GROUP BY verified_via"
    )
    unconfirmed_row = await db.first(
        "SELECT COUNT(*) AS n FROM postings WHERE archived_at IS NULL AND status = 'unverified'"
    )
    pruned = await db.all(
        "SELECT archive_reason, COUNT(*) AS n FROM postings WHERE archived_at IS NOT NULL "
        "GROUP BY archive_reason ORDER BY n DESC"
    )
    usage_by_step = await db.all(
        "SELECT step, model, SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens) AS tokens, "
        "SUM(web_searches) AS searches, SUM(est_cost_usd) AS usd FROM usage GROUP BY step, model"
    )
    alerts = await db.all("SELECT * FROM alerts ORDER BY id DESC LIMIT 50")
    settings = await db.first("SELECT * FROM budget_settings WHERE id = 1")
    forecast = await _budget_forecast(db, settings or {})

    return {
        "coverage": coverage,
        "sources": sources,
        "first_to_find": first_to_find,
        "postings_per_title": postings_per_title,
        "verification_methods": verification_methods,
        "unconfirmed_count": (unconfirmed_row or {}).get("n") or 0,
        "pruned": pruned,
        "usage_by_step": usage_by_step,
        "budgets": settings,
        "forecast": forecast,
        "alerts": alerts,
    }


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------

def _proposal_out(row: dict) -> dict:
    row = dict(row)
    row["payload"] = json.loads(row["payload"]) if row.get("payload") else {}
    return row


async def _apply_company_proposal(db: Database, payload: dict, at: str) -> None:
    action = payload.get("action")
    if action == "add":
        name = (payload.get("name") or "").strip()
        if not name:
            return
        existing_rows = await db.all("SELECT name FROM companies")
        normalized = companies_logic.normalize_company_name(name)
        if any(companies_logic.normalize_company_name(r["name"]) == normalized for r in existing_rows):
            return
        feed_type, feed_id = companies_logic.detect_feed(payload.get("careers_url"))
        await db.run(
            """INSERT INTO companies (name, aliases, status, careers_url, feed_type, feed_id, added_by, added_at)
               VALUES (?, '[]', 'pinned', ?, ?, ?, 'claude', ?)""",
            name, payload.get("careers_url"), feed_type, feed_id, at,
        )
    elif action == "remove" and payload.get("company_id"):
        await db.run("UPDATE companies SET status = 'removed' WHERE id = ?", payload["company_id"])


async def _apply_source_proposal(db: Database, payload: dict, at: str) -> None:
    name = (payload.get("name") or "").strip()
    url = (payload.get("url") or "").strip()
    if not name or not url:
        return
    await db.run(
        """INSERT INTO sources (name, url, kind, covers, status, discovered_by, discovered_at, found_count)
           VALUES (?, ?, ?, ?, 'trial', 'claude', ?, 0)""",
        name, url, payload.get("kind") or "Job boards", payload.get("covers") or "", at,
    )


async def _apply_source_retire_proposal(db: Database, payload: dict) -> None:
    source_id = payload.get("source_id")
    if source_id:
        await db.run("DELETE FROM sources WHERE id = ?", source_id)


_PROPOSAL_APPLIERS = {
    "company": _apply_company_proposal,
    "source": _apply_source_proposal,
}


@router.get("/proposals")
async def list_proposals(status: str | None = None, db: Database = Depends(get_db)):
    if status:
        rows = await db.all("SELECT * FROM proposals WHERE status = ? ORDER BY id DESC", status)
    else:
        rows = await db.all("SELECT * FROM proposals ORDER BY id DESC")
    return {"proposals": [_proposal_out(r) for r in rows]}


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: int, db: Database = Depends(get_db)):
    row = await db.first("SELECT * FROM proposals WHERE id = ?", proposal_id)
    if not row:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail="This proposal was already decided")

    at = now_iso()
    payload = json.loads(row["payload"]) if row.get("payload") else {}
    if row["kind"] == "source_retire":
        await _apply_source_retire_proposal(db, payload)
    else:
        applier = _PROPOSAL_APPLIERS.get(row["kind"])
        if applier:
            await applier(db, payload, at)

    await db.run("UPDATE proposals SET status = 'approved', decided_at = ? WHERE id = ?", at, proposal_id)
    return _proposal_out(await db.first("SELECT * FROM proposals WHERE id = ?", proposal_id))


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: int, db: Database = Depends(get_db)):
    row = await db.first("SELECT * FROM proposals WHERE id = ?", proposal_id)
    if not row:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail="This proposal was already decided")
    await db.run("UPDATE proposals SET status = 'rejected', decided_at = ? WHERE id = ?", now_iso(), proposal_id)
    return _proposal_out(await db.first("SELECT * FROM proposals WHERE id = ?", proposal_id))


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
            github_token = getattr(self.env, "GITHUB_TOKEN", "") or ""
            app.state.github = GitHubClient(repo=REPO, token=github_token) if github_token else None
            return await asgi.fetch(app, request, self.env)

except ImportError:
    # Not running inside a Python Worker (e.g. plain CPython under pytest).
    pass
