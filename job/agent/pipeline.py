"""Wires the Claude agent steps -- discovery, classification, the source
scout, and daily improvement -- into the daily job, gated by budget, with
usage recorded for every call. Unclear-page reads are wired separately,
into `job/verify/pipeline.py`, since they happen per-posting during
verification rather than once per run (see `build_unclear_reader`).

A single step failing (a bad Claude call, an invalid JSON reply) is
recorded and skipped, same as one company's broken feed doesn't stop the
whole run (`job/fetchers/__init__.py`). A Claude usage-limit error stops
all further Claude calls for the rest of the run, but not the run itself.
"""
from __future__ import annotations

import json

from job.agent import discovery, improvement, scout
from job.agent import classification as classification_mod
from job.agent import unclear as unclear_mod
from job.agent.budgets import BudgetStatus
from job.agent.client import ClaudeLimitError
from job.agent.client import run_claude_step as _default_run_claude
from job.agent.github_pr import github_client_from_env
from job.agent.schemas import ClaudeOutputError
from job.agent.titles_pr import open_title_suggestions_pr
from job.agent.usage import record_usage
from job.config import load_search_plan, load_watchlist_setting
from job.locations import location_match as compute_location_match


async def run_agent_steps(
    db, *, run_id: int, companies: list[dict], titles_doc: dict, locations_setting: dict,
    budget_settings: dict, status: BudgetStatus, today: str, now_iso: str,
    run_summary_so_far: dict | None = None,
    run_claude=_default_run_claude,
) -> dict:
    outcome = {
        "discovery_count": 0, "classification_count": 0, "scout_count": 0,
        "steps_skipped": [], "steps_succeeded": [], "claude_limit_hit": False, "errors": [], "title_pr": None,
    }

    async def record(step: str, result) -> None:
        if result is not None:
            await record_usage(db, run_id=run_id, step=step, result=result, now_iso=now_iso)

    steps_succeeded: set[str] = set()

    async def guarded(step: str, fn):
        if outcome["claude_limit_hit"]:
            outcome["steps_skipped"].append({"step": step, "reason": "a Claude usage limit stopped this run"})
            return None
        allowed, reason = status.allow_step(step)
        if not allowed:
            outcome["steps_skipped"].append({"step": step, "reason": reason})
            return None
        try:
            value = await fn()
            steps_succeeded.add(step)
            outcome["steps_succeeded"].append(step)
            return value
        except ClaudeLimitError as exc:
            outcome["claude_limit_hit"] = True
            outcome["errors"].append(f"{step}: {exc}")
            return None
        except ClaudeOutputError as exc:
            # The CLI call itself worked and spent real tokens/searches --
            # only the reply it came back with couldn't be used. Record that
            # spend before giving up, or a step that ran (and cost budget)
            # leaves no trace anywhere the usage table is read from.
            if exc.result is not None:
                await record(step, exc.result)
            outcome["errors"].append(f"{step}: {exc}")
            return None
        except Exception as exc:  # noqa: BLE001 - a broken Claude call (bad JSON, missing CLI, a
            # network error) must not stop the run, same as one company's broken feed doesn't
            # (job/fetchers/__init__.py). FileNotFoundError from a missing `claude` binary lands
            # here too, deliberately.
            outcome["errors"].append(f"{step}: {exc}")
            return None

    no_feed_companies = [c for c in companies if not c.get("feed_type")]

    async def do_discovery():
        watchlist = await load_watchlist_setting(db)
        ordered = discovery.order_companies(no_feed_companies, watchlist)
        search_plan = await load_search_plan(db, today)
        results, result = discovery.run_discovery(
            ordered, titles_doc, locations_setting, search_plan,
            model=budget_settings.get("model_discovery", "sonnet"),
            search_limit_per_run=int(budget_settings.get("search_limit_per_run") or 35),
            search_limit_per_company=int(budget_settings.get("search_limit_per_company") or 5),
            run_claude=run_claude,
        )
        await record("discovery", result)
        return results

    if no_feed_companies:
        discovery_results = await guarded("discovery", do_discovery) or []
        outcome["discovery_count"] = await _store_discovery_results(
            db, discovery_results, now_iso=now_iso, locations_setting=locations_setting
        )

    unclassified = await db.all("SELECT * FROM postings WHERE matched_title IS NULL AND archived_at IS NULL")

    async def do_classification():
        decisions, result = classification_mod.run_classification(
            unclassified, titles_doc, locations_setting,
            model=budget_settings.get("model_classification", "haiku"),
            run_claude=run_claude,
        )
        await record("classification", result)
        return decisions

    if unclassified:
        decisions = await guarded("classification", do_classification) or []
        outcome["classification_count"] = await _apply_classification_decisions(db, decisions, now_iso=now_iso)

    search_limit_per_run = int(budget_settings.get("search_limit_per_run") or 35)
    scout_batch = scout.order_companies_for_scout(no_feed_companies, limit=search_limit_per_run)

    async def do_scout():
        found, result = scout.run_scout(
            scout_batch, model=budget_settings.get("model_scout", "sonnet"),
            search_limit_per_run=search_limit_per_run,
            run_claude=run_claude,
        )
        await record("scout", result)
        return found

    if scout_batch:
        found_sources = await guarded("scout", do_scout) or []
        outcome["scout_count"] = await _store_scout_sources(db, found_sources, now_iso=now_iso)
        if "scout" in steps_succeeded:
            await _mark_companies_scouted(db, scout_batch, now_iso=now_iso)

    async def do_improvement():
        # Not `SELECT * FROM runs WHERE id = ?` -- at this point in the daily
        # job the run row's own `summary` column is still NULL (job/run.py
        # only writes it after this whole function returns), so that query
        # always fed the model a "(no summary)" placeholder instead of what
        # actually happened today. `run_summary_so_far` is what the caller
        # already knows (feed results, mostly) before Claude gets involved.
        recent_stats = await db.all("SELECT * FROM run_stats WHERE run_id = ?", run_id)
        payload, result = improvement.run_improvement(
            dict(run_summary_so_far or {}), recent_stats, titles_doc,
            model=budget_settings.get("model_improvement", "sonnet"),
            run_claude=run_claude,
        )
        await record("improvement", result)
        return payload

    improvement_payload = await guarded("improvement", do_improvement)
    if improvement_payload:
        await _store_search_plan(db, improvement_payload.get("search_plan"), today=today)
        await _store_proposals(db, improvement_payload.get("proposals") or [], now_iso=now_iso)
        title_suggestions = improvement_payload.get("title_suggestions") or []
        if title_suggestions:
            try:
                outcome["title_pr"] = await _open_title_pr(titles_doc, title_suggestions)
            except Exception as exc:  # noqa: BLE001 - a PR failure must not fail the whole run
                outcome["errors"].append(f"titles_pr: {exc}")

    return outcome


def build_unclear_reader(*, status: BudgetStatus, model: str, on_usage, on_error, run_claude=_default_run_claude):
    """A `check_via_browser(unclear_reader=...)` callable that reads an
    unclear page's text with Claude, gated by the `unclear_page` step's own
    budget check and never raising -- a failed or skipped read just keeps
    the plain-text-signals "unclear" verdict.
    """

    def reader(page_text: str):
        allowed, reason = status.allow_step("unclear_page")
        if not allowed:
            return None
        try:
            decision, result = unclear_mod.read_unclear_page(page_text, model=model, run_claude=run_claude)
        except ClaudeOutputError as exc:
            # Same reasoning as `guarded`: the call spent tokens even though
            # its reply couldn't be used, so still report that usage.
            if exc.result is not None:
                on_usage(exc.result)
            on_error(exc)
            return None
        except Exception as exc:  # noqa: BLE001 - a failed read must fall back to "unclear", not crash verification
            on_error(exc)
            return None
        on_usage(result)
        return decision

    return reader


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _store_discovery_results(db, results: list[dict], *, now_iso: str, locations_setting: dict) -> int:
    stored = 0
    for r in results:
        existing = await db.first("SELECT id FROM postings WHERE url = ?", r["url"])
        if existing:
            continue
        loc_decision = compute_location_match(r.get("location_text") or "", locations_setting)
        await db.run(
            """INSERT INTO postings
               (company_name, title, url, source_name, locations, location_match, posted_date,
                first_seen, status)
               VALUES (?, ?, ?, 'claude-discovery', ?, ?, ?, ?, 'unverified')""",
            r["company_name"], r["title"], r["url"],
            json.dumps([r["location_text"]] if r.get("location_text") else []),
            loc_decision, r.get("posted_date"), now_iso[:10],
        )
        stored += 1
    return stored


async def _apply_classification_decisions(db, decisions: list[dict], *, now_iso: str) -> int:
    applied = 0
    for d in decisions:
        posting = await db.first("SELECT id FROM postings WHERE id = ?", d.get("id"))
        if not posting:
            continue
        if d["decision"] == "match":
            await db.run(
                "UPDATE postings SET matched_title = ?, title_en = ?, location_match = COALESCE(?, location_match) "
                "WHERE id = ?",
                d["matched_title"], d.get("title_en"), d.get("location_match"), posting["id"],
            )
            applied += 1
        else:
            await db.run(
                "UPDATE postings SET archived_at = ?, archive_reason = ? WHERE id = ?",
                now_iso, f"Claude classification: {d.get('reason') or 'not a match'}", posting["id"],
            )
    return applied


async def _store_scout_sources(db, found: list[dict], *, now_iso: str) -> int:
    # `scout.next_source_status` (trial -> active after a real open posting,
    # or -> demoted after repeated empty checks) needs a found/empty-check
    # count this step doesn't have -- Claude proposing a URL again isn't
    # proof of an open posting. That promotion/demotion signal belongs with
    # whatever actually checks the source (a future increment); for now,
    # re-finding an already-known source just refreshes when it was last
    # seen, and status changes stay a later step's job.
    stored = 0
    for f in found:
        existing = await db.first("SELECT * FROM sources WHERE url = ?", f["url"])
        if existing:
            await db.run("UPDATE sources SET last_checked = ? WHERE id = ?", now_iso, existing["id"])
            continue
        row = scout.new_source_row(f, now_iso=now_iso)
        await db.run(
            """INSERT INTO sources (name, url, kind, covers, status, discovered_by, discovered_at,
               found_count, last_checked)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row["name"], row["url"], row["kind"], row["covers"], row["status"],
            row["discovered_by"], row["discovered_at"], row["found_count"], row["last_checked"],
        )
        stored += 1
    return stored


async def _mark_companies_scouted(db, companies: list[dict], *, now_iso: str) -> None:
    for company in companies:
        company_id = company.get("id")
        if company_id is None:
            continue
        await db.run("UPDATE companies SET last_scouted_at = ? WHERE id = ?", now_iso, company_id)


async def _store_search_plan(db, plan: dict | None, *, today: str) -> None:
    if not plan:
        return
    from datetime import date

    plan_date = improvement.tomorrow_iso(date.fromisoformat(today))
    existing = await db.first("SELECT plan_date FROM search_plans WHERE plan_date = ?", plan_date)
    if existing:
        await db.run("UPDATE search_plans SET plan = ? WHERE plan_date = ?", json.dumps(plan), plan_date)
    else:
        await db.run(
            "INSERT INTO search_plans (plan_date, plan) VALUES (?, ?)", plan_date, json.dumps(plan)
        )


async def _store_proposals(db, proposals: list[dict], *, now_iso: str) -> None:
    for p in proposals:
        await db.run(
            "INSERT INTO proposals (kind, payload, status, created_at) VALUES (?, ?, 'pending', ?)",
            p["kind"], json.dumps({**p["payload"], "reason": p.get("reason")}), now_iso,
        )


async def _open_title_pr(titles_doc: dict, title_suggestions: list[dict]) -> dict | None:
    github = github_client_from_env()
    if github is None:
        return None
    return await open_title_suggestions_pr(github, titles_doc, title_suggestions)
