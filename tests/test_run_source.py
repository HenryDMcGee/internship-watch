"""Phase 1 of the Cloudflare daily trigger: every run records which path
started it, in a new nullable `runs.source` column.

Written before the column, the `run_source` workflow input and the job
change existed. They pin down:
- migration `0016` adds `runs.source`;
- `job/run.py` writes it from the `RUN_SOURCE` environment variable (the
  `run_source` workflow input), and writes `schedule` when it is absent or
  empty, which is what the Actions cron path produces;
- the analytics endpoint returns it with each recent run;
- the pending copy of `daily-search.yml` declares the input (default
  `manual`) and passes it through to the job-run step, without touching the
  crons or the guard.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import job.run as run_module
from job.fetchers import FetchResult
from src.main import app

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _not_a_ranking_refresh_day(monkeypatch):
    monkeypatch.setattr(run_module, "is_quarterly_refresh_day", lambda _day: False)


@pytest.fixture
def no_companies(monkeypatch):
    monkeypatch.setattr(run_module, "run_fetcher", lambda company, session=None: FetchResult(
        company_id=company["id"], company_name=company["name"], postings=[],
    ))


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def test_migration_adds_a_nullable_source_column_to_runs(empty_db):
    columns = {row["name"]: row for row in run(empty_db.all("PRAGMA table_info(runs)"))}
    assert "source" in columns
    assert columns["source"]["notnull"] == 0


def test_migration_file_exists_and_only_adds_the_column():
    paths = sorted((ROOT / "db" / "migrations").glob("0016_*.sql"))
    assert len(paths) == 1, "expected exactly one 0016 migration"
    sql = paths[0].read_text(encoding="utf-8")
    statements = [s.strip() for s in re.sub(r"--[^\n]*", "", sql).split(";") if s.strip()]
    assert statements == ["ALTER TABLE runs ADD COLUMN source TEXT"]


# ---------------------------------------------------------------------------
# The job writes it
# ---------------------------------------------------------------------------

def test_run_source_from_env_reads_the_workflow_input():
    assert run_module.run_source_from_env({"RUN_SOURCE": "cloudflare"}) == "cloudflare"
    assert run_module.run_source_from_env({"RUN_SOURCE": "manual"}) == "manual"
    assert run_module.run_source_from_env({"RUN_SOURCE": "  manual \n"}) == "manual"


def test_run_source_defaults_to_schedule_when_the_input_is_absent_or_empty():
    # A `schedule` event has no workflow_dispatch inputs at all, so the
    # variable is unset; an empty string is what `${{ inputs.run_source }}`
    # expands to in that case.
    assert run_module.run_source_from_env({}) == "schedule"
    assert run_module.run_source_from_env({"RUN_SOURCE": ""}) == "schedule"
    assert run_module.run_source_from_env({"RUN_SOURCE": "   "}) == "schedule"


def test_daily_run_records_its_source(empty_db, no_companies):
    run(run_module.run(empty_db, kind="daily", source="cloudflare"))
    rows = run(empty_db.all("SELECT kind, source, status FROM runs"))
    assert rows == [{"kind": "daily", "source": "cloudflare", "status": "completed"}]


def test_daily_run_without_a_source_leaves_the_column_null(empty_db, no_companies):
    # Only `main()` applies the `schedule` default; callers that say nothing record nothing.
    run(run_module.run(empty_db, kind="daily"))
    assert run(empty_db.all("SELECT source FROM runs")) == [{"source": None}]


def test_main_passes_the_source_from_the_environment(monkeypatch):
    seen = {}

    async def fake_run(db, *, kind="daily", company_ids=None, source=None):
        seen.update(kind=kind, source=source)
        return {"ok": True}

    monkeypatch.setattr(run_module, "run", fake_run)
    monkeypatch.setattr(run_module.D1HttpDatabase, "from_env", classmethod(lambda cls: object()))

    monkeypatch.setenv("RUN_SOURCE", "cloudflare")
    assert run_module.main([]) == 0
    assert seen == {"kind": "daily", "source": "cloudflare"}

    monkeypatch.delenv("RUN_SOURCE", raising=False)
    assert run_module.main([]) == 0
    assert seen == {"kind": "daily", "source": "schedule"}


# ---------------------------------------------------------------------------
# The API shows it
# ---------------------------------------------------------------------------

@pytest.fixture
def client(empty_db):
    app.state.db = empty_db
    app.state.access_team_domain = ""
    app.state.access_aud = ""
    app.state.github = None
    yield TestClient(app), empty_db
    app.state.db = None


def test_analytics_recent_runs_include_the_source(client):
    tc, db = client
    run(db.run(
        "INSERT INTO runs (kind, started_at, status, new_count, summary, source) VALUES "
        "('daily', '2026-10-15T08:00:00', 'completed', 1, 'Checked 5.', 'cloudflare')"
    ))
    run(db.run(
        "INSERT INTO runs (kind, started_at, status, new_count, summary) VALUES "
        "('daily', '2026-10-16T08:00:00', 'completed', 0, 'Checked 5.')"
    ))
    recent = tc.get("/api/analytics").json()["recent_runs"]
    assert [r["source"] for r in recent] == [None, "cloudflare"]  # newest id first


# ---------------------------------------------------------------------------
# The workflow passes it
# ---------------------------------------------------------------------------

def _pending_daily_search() -> str:
    return (ROOT / "docs" / "pending-workflows" / "daily-search.yml").read_text(encoding="utf-8")


def test_workflow_declares_the_run_source_input_defaulting_to_manual():
    text = _pending_daily_search()
    block = re.search(
        r"workflow_dispatch:\n\s+inputs:\n\s+run_source:\n((?:[ \t]+\S.*\n)+)", text,
    )
    assert block, "workflow_dispatch has no run_source input"
    assert re.search(r'default:\s*"?manual"?', block.group(1))
    assert re.search(r"required:\s*false", block.group(1))


def test_workflow_passes_run_source_to_the_job_run_step():
    text = _pending_daily_search()
    match = re.search(
        r"env:\n((?:[ \t]+\S.*\n?)+?)[ \t]*run:[ \t]*python -m job\.run\b", text,
    )
    assert match
    assert re.search(r"RUN_SOURCE:\s*\$\{\{\s*inputs\.run_source\s*\}\}", match.group(1))


def test_workflow_crons_and_guard_are_untouched_in_phase_1():
    text = _pending_daily_search()
    assert re.findall(r'- cron: "([^"]+)"', text) == ["0 13 * * *", "0 14 * * *"]
    assert "run: python -m job.schedule_guard" in text
    assert "cancel-in-progress: false" in text
