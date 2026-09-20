"""Internship Watch daily job entry point.

    python -m job.run                     # full daily run: every ranked/pinned company
    python -m job.run --request-id 12      # check-now: only the companies in check_requests row 12

Talks to D1 through the HTTP API (`job/d1_client.py`), using
`CLOUDFLARE_D1_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and `D1_DATABASE_ID`.

Matches `docs/plan.md`'s "Daily run, in order" for the pieces this phase
builds: load settings, quarterly ranking refresh, read company feeds,
match/classify without Claude, verify, prune, and record the run. Claude
discovery, the source scout, and daily improvement are phase 3.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - zoneinfo is stdlib on 3.9+
    ZoneInfo = None  # type: ignore

from job import config
from job.agent.alerts import claude_limit_alert, evaluate_budget_alerts, evaluate_spike_alert, record_alerts, run_failed_alert
from job.agent.budgets import load_budget_status, monthly_forecast, weekly_forecast
from job.agent.client import is_configured as claude_is_configured
from job.agent.pipeline import build_unclear_reader, run_agent_steps
from job.agent.summary import build_summary_payload, send_daily_summary
from job.agent.usage import record_usage
from job.d1_client import D1HttpDatabase
from job.fetchers import run_fetcher
from job.locations import location_match
from job.matching import classify_posting, cycle_status
from job.pruning import decide_prune
from job.ranking import is_quarterly_refresh_day, refresh_ranking
from job.verify.browser import BrowserBudget
from job.verify.pipeline import verify_posting

CENTRAL = ZoneInfo("America/Chicago") if ZoneInfo else None


def now_iso() -> str:
    return datetime.now(CENTRAL).isoformat() if CENTRAL else datetime.utcnow().isoformat()


def today_central():
    return datetime.now(CENTRAL).date() if CENTRAL else datetime.utcnow().date()


async def _fetch_and_store(db, companies: list[dict], titles_doc: dict, locations_setting: dict, run_id: int) -> tuple[int, dict[int, bool], dict[int, set[str]], int, int]:
    new_count = 0
    feed_ok: dict[int, bool] = {}
    current_urls: dict[int, set[str]] = {}
    no_source_count = 0
    feed_error_count = 0

    for company in companies:
        result = run_fetcher(company)
        feed_ok[company["id"]] = result.error is None
        current_urls[company["id"]] = {p.url for p in result.postings if p.url}

        # A company with no feed_type on file has no known source yet -- it's
        # phase 3's source scout's job to find one, not a broken feed. Only
        # count it as a real error once we knew where to look and that
        # lookup failed.
        if not company.get("feed_type"):
            feed_status = "no_source"
            feed_error_text = None
            no_source_count += 1
        elif result.error:
            feed_status = "error"
            feed_error_text = result.error
            feed_error_count += 1
        else:
            feed_status = "ok"
            feed_error_text = None

        run_stats_id = await db.run(
            """INSERT INTO run_stats (run_id, source_id, query, found, useful)
               VALUES (?, NULL, ?, ?, 0)""",
            run_id, f"{company['name']} ({company.get('feed_type') or 'no feed'})", len(result.postings),
        )
        await db.run(
            "UPDATE companies SET last_result_at = ?, last_feed_status = ?, last_feed_error = ? WHERE id = ?",
            now_iso(), feed_status, feed_error_text, company["id"],
        )

        useful = 0
        for fetched in result.postings:
            match = classify_posting(fetched.title, "", titles_doc)
            if match.decision == "reject":
                continue

            existing = await db.first(
                "SELECT id FROM postings WHERE company_id = ? AND url = ?", company["id"], fetched.url
            )
            if existing:
                continue

            loc_decision = location_match(fetched.location_text, locations_setting)
            first_seen = today_central().isoformat()
            new_id = await db.run(
                """INSERT INTO postings
                   (company_id, company_name, title, matched_title, url, source_name,
                    locations, location_match, posted_date, first_seen, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unverified')""",
                company["id"], company["name"], fetched.title, match.matched_title, fetched.url,
                company.get("feed_type"),
                json.dumps([fetched.location_text] if fetched.location_text else []),
                loc_decision, fetched.posted_date, first_seen,
            )
            new_count += 1
            useful += 1
            await db.run(
                "INSERT INTO posting_events (posting_id, type, detail, at) VALUES (?, 'found', ?, ?)",
                new_id, json.dumps({"feed_type": company.get("feed_type")}), now_iso(),
            )

        if useful:
            await db.run("UPDATE run_stats SET useful = ? WHERE id = ?", useful, run_stats_id)

    return new_count, feed_ok, current_urls, no_source_count, feed_error_count


async def _verify_and_prune(
    db, companies: list[dict], feed_ok: dict[int, bool], current_urls: dict[int, set[str]],
    browser_budget: BrowserBudget, *, unclear_reader=None,
) -> None:
    company_ids = [c["id"] for c in companies]
    if not company_ids:
        return
    placeholders = ", ".join("?" for _ in company_ids)
    postings = await db.all(
        f"SELECT * FROM postings WHERE archived_at IS NULL AND company_id IN ({placeholders})", *company_ids
    )

    for posting in postings:
        company_id = posting["company_id"]
        outcome = verify_posting(
            posting,
            feed_checked_ok=feed_ok.get(company_id, False),
            found_in_current_feed=posting["url"] in current_urls.get(company_id, set()),
            browser_budget=browser_budget,
            now_iso=now_iso(),
            unclear_reader=unclear_reader,
        )
        prune = decide_prune(
            status=outcome.status, note=outcome.note,
            cycle_status=cycle_status(posting.get("title") or ""),
        )

        if prune.should_archive:
            await db.run(
                """UPDATE postings SET status = ?, verified_via = ?, verified_at = ?, status_note = ?,
                   archived_at = ?, archive_reason = ? WHERE id = ?""",
                outcome.status, outcome.verified_via, outcome.verified_at, outcome.note,
                now_iso(), prune.reason, posting["id"],
            )
        else:
            await db.run(
                "UPDATE postings SET status = ?, verified_via = ?, verified_at = ?, status_note = ? WHERE id = ?",
                outcome.status, outcome.verified_via, outcome.verified_at, outcome.note, posting["id"],
            )


async def run(db, *, kind: str = "daily", company_ids: list[int] | None = None) -> dict:
    started_at = now_iso()
    run_id = await db.run(
        "INSERT INTO runs (kind, started_at, status) VALUES (?, ?, 'running')", kind, started_at
    )

    try:
        result = await _run_body(db, run_id, started_at, kind, company_ids)
    except Exception as exc:  # noqa: BLE001 - the run row must reflect failures, not stay 'running' forever
        await db.run(
            "UPDATE runs SET finished_at = ?, status = 'failed', summary = ? WHERE id = ?",
            now_iso(), f"Run failed: {exc}", run_id,
        )
        if kind == "daily":
            await _finish_daily_run(db, run_status="failed", run_reason=str(exc))
        raise

    if kind == "daily":
        await _finish_daily_run(
            db, run_status="completed", run_reason=None, agent_outcome=result.get("agent_outcome")
        )
    return result


async def _run_body(db, run_id: int, started_at: str, kind: str, company_ids: list[int] | None) -> dict:
    ranking_summary = None
    if kind == "daily" and is_quarterly_refresh_day(today_central()):
        ranking_summary = await refresh_ranking(db)

    titles_doc = config.load_titles()
    locations_setting = await config.load_locations_setting(db)
    budget_settings = await config.load_budget_settings(db)
    browser_budget = BrowserBudget(remaining=int(budget_settings.get("browser_checks_per_run") or 50))

    if company_ids is not None:
        companies = await config.load_companies_by_ids(db, company_ids)
    else:
        companies = await config.load_companies(db)

    new_count, feed_ok, current_urls, no_source_count, feed_error_count = await _fetch_and_store(
        db, companies, titles_doc, locations_setting, run_id
    )

    agent_outcome = None
    unclear_reader = None
    unclear_results: list = []
    if kind == "daily" and claude_is_configured():
        today_str = today_central().isoformat()
        status = await load_budget_status(db, budget_settings, today=today_str)
        agent_outcome = await run_agent_steps(
            db, run_id=run_id, companies=companies, titles_doc=titles_doc, locations_setting=locations_setting,
            budget_settings=budget_settings, status=status, today=today_str, now_iso=now_iso(),
        )
        new_count += agent_outcome["discovery_count"]
        unclear_reader = build_unclear_reader(
            status=status, model=budget_settings.get("model_classification", "haiku"),
            on_usage=unclear_results.append,
            on_error=lambda exc: agent_outcome["errors"].append(f"unclear_page: {exc}"),
        )
    elif kind == "daily":
        agent_outcome = {
            "discovery_count": 0, "classification_count": 0, "scout_count": 0,
            "steps_skipped": [{"step": "all", "reason": "Claude isn't configured (no "
                                                          "CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY)"}],
            "claude_limit_hit": False, "errors": [], "title_pr": None,
        }

    await _verify_and_prune(db, companies, feed_ok, current_urls, browser_budget, unclear_reader=unclear_reader)

    for unclear_result in unclear_results:
        await record_usage(db, run_id=run_id, step="unclear_page", result=unclear_result, now_iso=now_iso())

    summary = (
        f"Checked {len(companies)} companies, {new_count} new posting(s), "
        f"{feed_error_count} feed error(s), {no_source_count} with no source yet."
    )
    finished_at = now_iso()
    await db.run(
        "UPDATE runs SET finished_at = ?, status = 'completed', new_count = ?, summary = ? WHERE id = ?",
        finished_at, new_count, summary, run_id,
    )

    return {
        "run_id": run_id,
        "kind": kind,
        "started_at": started_at,
        "finished_at": finished_at,
        "new_count": new_count,
        "companies_checked": len(companies),
        "feed_errors": feed_error_count,
        "no_source_count": no_source_count,
        "ranking_refresh": ranking_summary,
        "summary": summary,
        "agent_outcome": agent_outcome,
    }


async def _seven_day_avg_tokens(db, today: str) -> float:
    today_date = date.fromisoformat(today)
    start = (today_date - timedelta(days=7)).isoformat()
    end = (today_date - timedelta(days=1)).isoformat()
    rows = await db.all(
        "SELECT substr(at, 1, 10) AS d, "
        "SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens) AS tokens "
        "FROM usage WHERE substr(at, 1, 10) >= ? AND substr(at, 1, 10) <= ? GROUP BY d",
        start, end,
    )
    if not rows:
        return 0.0
    return sum((r["tokens"] or 0) for r in rows) / 7.0


async def _finish_daily_run(db, *, run_status: str, run_reason: str | None, agent_outcome: dict | None = None) -> None:
    """Evaluates alerts and sends the daily Claude routine summary. Runs
    after every daily run, including failed ones, per `docs/plan.md`. Never
    raises: a summary that can't be built or sent must not turn a completed
    (or already-recorded-as-failed) run into an unhandled exception.
    """
    try:
        budget_settings = await config.load_budget_settings(db)
        today_str = today_central().isoformat()
        status = await load_budget_status(db, budget_settings, today=today_str)

        candidates = list(evaluate_budget_alerts(status))
        spike = evaluate_spike_alert(status.today.tokens, await _seven_day_avg_tokens(db, today_str))
        if spike:
            candidates.append(spike)
        if agent_outcome and agent_outcome.get("claude_limit_hit"):
            candidates.append(claude_limit_alert("; ".join(agent_outcome.get("errors") or []) or "usage limit reached"))
        if run_status == "failed":
            candidates.append(run_failed_alert(run_reason or "unknown error"))

        recorded_alerts = await record_alerts(db, candidates, today=today_str, now_iso=now_iso())

        usage_by_step = [
            {"step": step, "tokens": totals.tokens, "searches": totals.searches, "usd": round(totals.usd, 4)}
            for step, totals in sorted(status.today_by_step.items())
        ]
        payload = build_summary_payload(
            date=today_str, run_status=run_status, run_reason=run_reason,
            tokens_today=status.today.tokens, searches_today=status.today.searches,
            budgets={
                "billing_mode": status.billing_mode,
                "daily_fraction_used": round(status.fraction_used(), 4),
                "weekly_fraction_used": round(status.weekly_fraction_used(), 4),
                "monthly_fraction_used": round(status.monthly_fraction_used(), 4),
            },
            tokens_week_to_date=status.week.tokens,
            usage_by_step=usage_by_step,
            forecast={
                "weekly_tokens": round(weekly_forecast(status), 2),
                "monthly_usd": round(monthly_forecast(status), 2),
            },
            alerts=[{"type": a["type"], "severity": a["severity"]} for a in recorded_alerts],
        )
        send_daily_summary(payload)
    except Exception as exc:  # noqa: BLE001 - the routine summary must never crash the job
        print(f"Could not send the daily Claude summary: {exc}", file=sys.stderr)


async def run_check_request(db, request_id: int) -> dict:
    request = await db.first("SELECT * FROM check_requests WHERE id = ?", request_id)
    if not request:
        raise ValueError(f"check_requests row {request_id} not found")

    company_ids = json.loads(request["company_ids"])
    await db.run("UPDATE check_requests SET status = 'running' WHERE id = ?", request_id)
    try:
        result = await run(db, kind="check_now", company_ids=company_ids)
    except Exception as exc:  # noqa: BLE001 - recorded on the request row either way
        await db.run(
            "UPDATE check_requests SET status = 'failed', result = ? WHERE id = ?",
            json.dumps({"error": str(exc)}), request_id,
        )
        raise
    await db.run(
        "UPDATE check_requests SET status = 'completed', result = ? WHERE id = ?",
        json.dumps(result), request_id,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--request-id", type=int, default=None,
        help="Only run the companies from this check_requests row (used by check-now.yml)",
    )
    args = parser.parse_args(argv)

    db = D1HttpDatabase.from_env()

    async def _main() -> dict:
        if args.request_id is not None:
            return await run_check_request(db, args.request_id)
        return await run(db, kind="daily")

    result = asyncio.run(_main())
    print(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
