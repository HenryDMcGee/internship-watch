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
import os
import sys
import time
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - zoneinfo is stdlib on 3.9+
    ZoneInfo = None  # type: ignore

from job import config
from job.agent import careers_extraction
from job.agent import client as agent_client
from job.agent.alerts import (
    claude_limit_alert,
    claude_not_configured_alert,
    claude_step_issue_alert,
    discovery_failed_alert,
    evaluate_browser_run_alerts,
    evaluate_budget_alerts,
    evaluate_long_run_alert,
    evaluate_spike_alert,
    evaluate_workers_paid_suggestion,
    record_alerts,
    run_degraded_alert,
    run_failed_alert,
)
from job.agent.budgets import load_budget_status, monthly_forecast, weekly_forecast
from job.agent.client import is_configured as claude_is_configured
from job.agent.pipeline import build_unclear_reader, run_agent_steps
from job.agent.summary import build_summary_payload, send_daily_summary
from job.agent.usage import record_usage
from job.d1_client import D1HttpDatabase
from job import careers_snapshot, feed_snapshot, fingerprint
from job.fetchers import run_fetcher
from job.fetchers import careers_pages
from job.locations import location_match
from job.matching import classify_posting, cycle_status
from job.cleanup_unmatched import cleanup_unmatched, summary_line as cleanup_summary_line
from job.save_posting import Candidate, save_posting
from job.coverage import run_backfill_once
from job.restore_rejections import delete_duplicate_rejections, restore_rejections, summary_line as restore_summary_line
from job.pruning import decide_prune
from job.ranking import is_quarterly_refresh_day, refresh_ranking
from job.verify.browser import BrowserBudget
from job.verify.pipeline import verify_posting
from sql_helpers import in_json

CENTRAL = ZoneInfo("America/Chicago") if ZoneInfo else None

# Workers Paid's included Browser Rendering hours per month, and Henry's own
# daily budget default -- both from the issue text itself rather than
# Cloudflare's docs (unreachable while building this, same gap as
# `job/fetchers/careers_pages.py`'s module docstring; flagged there for
# Henry to confirm). Duplicated in `api/src/browser_run_budgets.py` since the
# deployed Worker only ships `api/src` (see `api/src/budgets.py`'s docstring
# for the same reasoning).
BROWSER_RUN_PAID_INCLUDED_MONTHLY_HOURS = 10.0


# Issue #70: keep runs short. The careers step's default time budget, how much
# of it page loading may use, and how many extraction calls run at once.
DEFAULT_CAREERS_MINUTES_PER_RUN = 10.0
CAREERS_FETCH_SHARE = 0.6
CAREERS_EXTRACTION_WORKERS = 4
# A run longer than this is flagged in the daily summary (2,000 free Actions minutes a month).
LONG_RUN_WARNING_MINUTES = 30


def clock() -> float:
    return time.monotonic()


def now_iso() -> str:
    return datetime.now(CENTRAL).isoformat() if CENTRAL else datetime.utcnow().isoformat()


def _minutes_between(start_iso: str, end_iso: str) -> float | None:
    try:
        return (datetime.fromisoformat(end_iso) - datetime.fromisoformat(start_iso)).total_seconds() / 60.0
    except (TypeError, ValueError):
        return None


def today_central():
    return datetime.now(CENTRAL).date() if CENTRAL else datetime.utcnow().date()


async def _fetch_and_store(
    db, companies: list[dict], titles_doc: dict, locations_setting: dict, run_id: int,
    candidates: list[Candidate] | None = None,
) -> tuple[int, dict[int, bool], dict[int, set[str]], int, int, int, int, int, int]:
    """Reads every feed. Results the rules can't place go into `candidates`
    (when given) for classification; nothing is saved without a matched title."""
    new_count = 0
    feed_ok: dict[int, bool] = {}
    current_urls: dict[int, set[str]] = {}
    no_source_count = 0
    feed_error_count = 0
    sources_found = 0
    repeats_recognized = 0
    rejections_skipped = 0
    feeds_skipped = 0
    settings_ver = fingerprint.settings_version(titles_doc, locations_setting)

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

        # A feed that failed to load is never treated as unchanged -- the
        # error is already recorded above, and the last good snapshot (if
        # any) is left alone rather than read or overwritten here.
        if feed_status == "ok":
            content_hash = feed_snapshot.hash_postings(result.postings)
            snapshot = await feed_snapshot.load_snapshot(db, company["id"])
            if snapshot and snapshot["content_hash"] == content_hash:
                feeds_skipped += 1
                # Nothing in the feed changed, so nothing already on file for
                # this company can have disappeared from it either -- the
                # feed-based status check below must not report otherwise.
                current_urls[company["id"]] = feed_snapshot.ALL_FOUND
                await feed_snapshot.record_unchanged(db, company["id"], now_iso=now_iso())
                continue
            await feed_snapshot.record_changed(
                db, company["id"], content_hash, now_iso=now_iso(), existing=snapshot,
            )

        useful = 0
        for fetched in result.postings:
            source_name = company.get("feed_type") or "feed"
            outcome = await fingerprint.match_incoming(
                db, company_name=company["name"], title=fetched.title, url=fetched.url,
                location_text=fetched.location_text, external_id=fetched.external_id,
                settings_version=settings_ver, source=source_name, now_iso=now_iso(),
            )
            if outcome.kind == "posting":
                repeats_recognized += 1
                continue
            if outcome.kind == "rejection":
                rejections_skipped += 1
                continue

            match = classify_posting(fetched.title, "", titles_doc)
            if match.decision == "reject":
                await fingerprint.record_rejection(
                    db, outcome.fp, reason=match.reason, settings_version=settings_ver,
                    source=source_name, now_iso=now_iso(), existing_id=outcome.stale_fingerprint_id,
                )
                continue
            if match.decision == "classify":
                # Not saved unless classification matches it to a title.
                if candidates is not None:
                    candidates.append(Candidate(
                        id=len(candidates) + 1, company_name=company["name"], title=fetched.title,
                        url=fetched.url, source_name=company.get("feed_type"),
                        location_text=fetched.location_text or "", posted_date=fetched.posted_date,
                        fp=outcome.fp, fingerprint_source=source_name,
                        stale_fingerprint_id=outcome.stale_fingerprint_id, company_id=company["id"],
                        event_detail={"feed_type": company.get("feed_type")},
                    ))
                continue

            new_id = await save_posting(
                db, titles_doc=titles_doc, matched_title=match.matched_title, match_method=match.match_method,
                company_id=company["id"], company_name=company["name"], title=fetched.title, url=fetched.url,
                source_name=company.get("feed_type"),
                locations=[fetched.location_text] if fetched.location_text else [],
                location_match=location_match(fetched.location_text, locations_setting),
                posted_date=fetched.posted_date, first_seen=today_central().isoformat(),
                fp=outcome.fp, settings_version=settings_ver, fingerprint_source=source_name, now_iso=now_iso(),
                stale_fingerprint_id=outcome.stale_fingerprint_id,
                event_detail={"feed_type": company.get("feed_type")},
            )
            if new_id is None:
                continue
            new_count += 1
            useful += 1

        if useful:
            await db.run("UPDATE run_stats SET useful = ? WHERE id = ?", useful, run_stats_id)
            sources_found += 1

    return (
        new_count, feed_ok, current_urls, no_source_count, feed_error_count, sources_found,
        repeats_recognized, rejections_skipped, feeds_skipped,
    )


async def _run_careers_pages(
    db, companies: list[dict], budget_settings: dict, titles_doc: dict, locations_setting: dict,
    *, run_id: int, run_claude, candidates: list[Candidate] | None = None,
) -> tuple[set[int], list[int], dict]:
    """Reads careers pages for companies with a `careers_url` on file and no
    recognized feed (issue #53, phase 6 part 1). Runs after classification,
    so a slow careers step can never stop postings from being saved (issue #70).

    The step has a time budget, `careers_minutes_per_run` (default 10): 60% for
    loading pages, the rest for extraction. A page not reached keeps its old
    last-checked time, so the least recently checked come first next run.
    Extraction runs up to `CAREERS_EXTRACTION_WORKERS` pages at once.

    Returns the set of company ids whose page loaded this run (so the scout
    skips them), the ids of postings saved, and a dict of counts for the run
    summary. Listings the rules couldn't place are added to `candidates`.
    """
    started = clock()
    minutes = float(budget_settings.get("careers_minutes_per_run") or DEFAULT_CAREERS_MINUTES_PER_RUN)
    fetch_deadline = started + minutes * 60 * CAREERS_FETCH_SHARE
    extract_deadline = started + minutes * 60
    pages_todo = [c for c in companies if c.get("careers_url") and not c.get("feed_type")]
    stats = {
        "browser_run": 0, "backup": 0, "unchanged": 0, "changed": 0, "failed": 0,
        "browser_run_minutes": 0.0, "switch_reason": None,
        "candidates": len(pages_todo), "attempted": 0, "rate_limited": 0, "token_problem": None,
        "failure_reasons": {}, "skipped_no_listings": 0, "extracted": 0, "flagged_empty": 0,
        "time_budget_minutes": minutes, "out_of_time": 0, "seconds": 0.0,
    }
    if not pages_todo:
        return set(), [], stats

    # Pages never checked go first, then the one checked longest ago, so a run
    # that can't reach every page still works through all of them over time.
    snapshot_rows = await db.all("SELECT company_id, last_checked FROM careers_page_snapshots")
    last_checked = {r["company_id"]: r.get("last_checked") or "" for r in snapshot_rows}
    pages_todo = sorted(pages_todo, key=lambda c: (last_checked.get(c["id"], ""), c["id"]))
    today_minutes_row = await db.first(
        "SELECT SUM(browser_ms) AS ms FROM browser_run_usage WHERE method = 'browser_run' "
        "AND substr(at, 1, 10) = ?",
        today_central().isoformat(),
    )
    minutes_used_before = ((today_minutes_row or {}).get("ms") or 0) / 60000.0

    # Page loading is blocking network work; run it off the event loop thread.
    outcome = await asyncio.to_thread(
        careers_pages.run_careers_pages,
        pages_todo,
        deadline=fetch_deadline, clock=clock,
        account_id=os.environ.get("CLOUDFLARE_ACCOUNT_ID"),
        token=os.environ.get("CLOUDFLARE_BROWSER_TOKEN"),
        pages_per_run=int(budget_settings.get("careers_pages_per_run") or careers_pages.DEFAULT_CAREERS_PAGES_PER_RUN),
        daily_minutes_budget=float(
            budget_settings.get("browser_run_daily_minutes") or careers_pages.DEFAULT_BROWSER_RUN_DAILY_MINUTES
        ),
        backup_enabled=bool(budget_settings.get("careers_backup_enabled", True)),
        backup_pages_per_run=int(
            budget_settings.get("careers_backup_pages_per_run") or careers_pages.DEFAULT_CAREERS_BACKUP_PAGES_PER_RUN
        ),
        minutes_used_before=minutes_used_before,
    )
    stats["browser_run_minutes"] = outcome.browser_run_minutes_used
    stats["switch_reason"] = outcome.switch_reason
    stats["attempted"] = outcome.attempted
    stats["rate_limited"] = outcome.rate_limit_events
    stats["token_problem"] = outcome.token_problem

    settings_ver = fingerprint.settings_version(titles_doc, locations_setting)
    companies_by_id = {c["id"]: c for c in pages_todo}
    covered_ids: set[int] = set()
    posting_ids: list[int] = []
    pending: list[tuple] = []  # (page, company, existing snapshot, content hash) waiting for extraction
    # Per-method breakdown for `browser_run_usage` (issue #55) -- kept
    # separate from `stats` (used for the run summary line only) since
    # Analytics needs the changed/unchanged split by method too.
    per_method = {
        "browser_run": {"unchanged": 0, "changed": 0, "measured_by": set()},
        "backup": {"unchanged": 0, "changed": 0},
    }

    for page in outcome.pages:
        company = companies_by_id[page.company_id]
        existing = await careers_snapshot.load_snapshot(db, page.company_id)

        if not page.ok:
            stats["failed"] += 1
            reason_key = (page.reason or "error").split(":")[0]
            stats["failure_reasons"][reason_key] = stats["failure_reasons"].get(reason_key, 0) + 1
            print(f"careers page: {company['name']}: failed ({page.reason})")
            # `careers_page_snapshots.last_error` is where the reason lives
            # (issue: "Record the reason on the company") -- `companies.
            # last_feed_status` isn't reused for it, since that column's own
            # CHECK constraint only allows the feed fetcher's three values
            # ('ok', 'error', 'no_source').
            consecutive_failures = await careers_snapshot.record_failure(
                db, page.company_id, reason=page.reason, now_iso=now_iso(), existing=existing
            )
            if consecutive_failures >= careers_snapshot.FLAG_AFTER_CONSECUTIVE_FAILURES:
                await db.run(
                    "UPDATE companies SET careers_page_flagged = 1 WHERE id = ?", page.company_id
                )
            await db.run("UPDATE companies SET last_result_at = ? WHERE id = ?", now_iso(), page.company_id)
            continue

        covered_ids.add(page.company_id)
        await db.run("UPDATE companies SET last_result_at = ? WHERE id = ?", now_iso(), page.company_id)
        if page.method == "browser_run":
            stats["browser_run"] += 1
            if page.measured_by:
                per_method["browser_run"]["measured_by"].add(page.measured_by)
        else:
            stats["backup"] += 1

        content_hash = careers_snapshot.hash_page_text(page.text)
        if existing and existing.get("content_hash") == content_hash:
            stats["unchanged"] += 1
            per_method[page.method]["unchanged"] += 1
            await careers_snapshot.record_unchanged(db, page.company_id, method=page.method, now_iso=now_iso())
            if (existing.get("empty_runs") or 0) > 0:
                await _note_empty_page(db, company, existing, stats)
            continue

        # A landing page whose job list never loaded, or a page with nothing
        # job-like on it: recorded as "no listings found" with no Claude call.
        skip = careers_extraction.skip_reason(page.text)
        if skip:
            print(f"careers page: {company['name']}: no listings found ({skip}); no Claude call")
            stats["changed"] += 1
            stats["skipped_no_listings"] += 1
            per_method[page.method]["changed"] += 1
            await careers_snapshot.record_changed(
                db, page.company_id, content_hash, method=page.method, now_iso=now_iso(), existing=existing,
            )
            await _note_empty_page(db, company, existing, stats)
            continue
        pending.append((page, company, existing, content_hash))

    # Extraction, up to CAREERS_EXTRACTION_WORKERS pages at once. A page that
    # starts after the time budget is left alone (no snapshot written), so it
    # is read again next run.
    semaphore = asyncio.Semaphore(CAREERS_EXTRACTION_WORKERS)
    model = budget_settings.get("model_classification", "haiku")

    async def extract(page, company):
        async with semaphore:
            if clock() >= extract_deadline:
                return "out_of_time", None, None
            try:
                listings, result = await asyncio.to_thread(
                    careers_extraction.run_extraction, company["name"], page.text, model=model, run_claude=run_claude,
                )
            except Exception as exc:  # noqa: BLE001 - a bad extraction call must not stop the run, same as
                # any other per-company failure in this loop (job/fetchers/__init__.py's run_fetcher).
                print(f"careers_extraction: {company['name']}: {exc}")
                return "error", None, None
            return "ok", listings, result

    extractions = await asyncio.gather(*(extract(page, company) for page, company, _, _ in pending))

    for (page, company, existing, content_hash), (state, listings, result) in zip(pending, extractions):
        if state == "out_of_time":
            stats["out_of_time"] += 1
            continue
        if state == "error":
            continue
        stats["changed"] += 1
        per_method[page.method]["changed"] += 1
        await careers_snapshot.record_changed(
            db, page.company_id, content_hash, method=page.method, now_iso=now_iso(), existing=existing,
        )
        if result is not None:
            await record_usage(db, run_id=run_id, step="careers_extraction", result=result, now_iso=now_iso())
        stats["extracted"] += len(listings)
        if listings:
            await careers_snapshot.reset_empty(db, page.company_id)
        else:
            await _note_empty_page(db, company, existing, stats)

        for listing in listings:
            fp_outcome = await fingerprint.match_incoming(
                db, company_name=company["name"], title=listing["title"], url=listing.get("link"),
                location_text=listing.get("location") or "", external_id=listing.get("job_id"),
                settings_version=settings_ver, source="careers_page", now_iso=now_iso(),
            )
            if fp_outcome.kind in ("posting", "rejection"):
                continue

            match = classify_posting(listing["title"], "", titles_doc)
            if match.decision == "reject":
                await fingerprint.record_rejection(
                    db, fp_outcome.fp, reason=match.reason, settings_version=settings_ver,
                    source="careers_page", now_iso=now_iso(), existing_id=fp_outcome.stale_fingerprint_id,
                )
                continue
            if match.decision == "classify":
                # Not saved unless classification matches it to a title.
                if candidates is not None:
                    candidates.append(Candidate(
                        id=len(candidates) + 1, company_name=company["name"], title=listing["title"],
                        url=listing.get("link"), source_name="careers_page",
                        location_text=listing.get("location") or "", posted_date=listing.get("posted_date"),
                        fp=fp_outcome.fp, fingerprint_source="careers_page",
                        stale_fingerprint_id=fp_outcome.stale_fingerprint_id, company_id=company["id"],
                        event_detail={"source": "careers_page", "method": page.method},
                    ))
                continue

            new_id = await save_posting(
                db, titles_doc=titles_doc, matched_title=match.matched_title, match_method=match.match_method,
                company_id=company["id"], company_name=company["name"], title=listing["title"],
                url=listing.get("link"), source_name="careers_page",
                locations=[listing["location"]] if listing.get("location") else [],
                location_match=location_match(listing.get("location") or "", locations_setting),
                posted_date=listing.get("posted_date"), first_seen=today_central().isoformat(),
                fp=fp_outcome.fp, settings_version=settings_ver, fingerprint_source="careers_page",
                now_iso=now_iso(), stale_fingerprint_id=fp_outcome.stale_fingerprint_id,
                event_detail={"source": "careers_page", "method": page.method},
            )
            if new_id is not None:
                posting_ids.append(new_id)

    await _record_browser_run_usage(db, run_id=run_id, outcome=outcome, per_method=per_method, now_iso=now_iso())
    stats["not_reached"] = len(pages_todo) - outcome.attempted
    stats["seconds"] = clock() - started
    return covered_ids, posting_ids, stats


async def _note_empty_page(db, company: dict, existing: dict | None, stats: dict) -> None:
    """One more run with no listings on this company's page. At
    `FLAG_AFTER_EMPTY_RUNS` the company is flagged, which queues it for the
    scout to look for a better URL (an internship-filtered search page, or
    the company's job platform feed).
    """
    count = await careers_snapshot.record_empty(db, company["id"], existing=existing)
    if count >= careers_snapshot.FLAG_AFTER_EMPTY_RUNS and not company.get("careers_page_flagged"):
        await db.run("UPDATE companies SET careers_page_flagged = 1 WHERE id = ?", company["id"])
        company["careers_page_flagged"] = 1  # the scout step later in this run reads this list
        stats["flagged_empty"] += 1


async def _record_browser_run_usage(db, *, run_id: int, outcome, per_method: dict, now_iso: str) -> None:
    """One `browser_run_usage` row per method actually used this run (issue
    #55) -- skipped entirely for a method with nothing to show (no pages
    loaded or failed via it), so a quiet run doesn't pile up empty rows.
    """
    measured_by_seen = per_method["browser_run"]["measured_by"]
    # "elapsed" is the less accurate of the two, so a run that measured any
    # page's time that way is labeled "elapsed" even if others in the same
    # run got Cloudflare's own reported time (test case 13) -- rather than
    # silently claiming a precision the run didn't actually have throughout.
    if "elapsed" in measured_by_seen:
        measured_by = "elapsed"
    elif "cloudflare_header" in measured_by_seen:
        measured_by = "cloudflare_header"
    else:
        measured_by = None

    if outcome.browser_run_pages or outcome.browser_run_failures:
        await db.run(
            """INSERT INTO browser_run_usage
               (run_id, at, method, pages, unchanged, changed, browser_ms, measured_by, failures, backup_reason)
               VALUES (?, ?, 'browser_run', ?, ?, ?, ?, ?, ?, NULL)""",
            run_id, now_iso, outcome.browser_run_pages,
            per_method["browser_run"]["unchanged"], per_method["browser_run"]["changed"],
            round(outcome.browser_run_minutes_used * 60000), measured_by, outcome.browser_run_failures,
        )
    if outcome.backup_pages or outcome.backup_failures:
        backup_reason = outcome.switch_reason
        await db.run(
            """INSERT INTO browser_run_usage
               (run_id, at, method, pages, unchanged, changed, browser_ms, measured_by, failures, backup_reason)
               VALUES (?, ?, 'backup', ?, ?, ?, NULL, NULL, ?, ?)""",
            run_id, now_iso, outcome.backup_pages,
            per_method["backup"]["unchanged"], per_method["backup"]["changed"],
            outcome.backup_failures, backup_reason,
        )


def _careers_pages_summary_line(stats: dict | None) -> str | None:
    if not stats:
        return None
    total_loaded = stats["browser_run"] + stats["backup"]
    if total_loaded == 0 and stats["failed"] == 0:
        if stats.get("candidates"):
            return f"Careers pages: 0 of {stats['candidates']} attempted."
        return None
    parts = []
    if stats["browser_run"]:
        parts.append(f"{stats['browser_run']} via Browser Run ({stats['browser_run_minutes']:.1f} min)")
    if stats["backup"]:
        parts.append(f"{stats['backup']} via backup")
    parts.append(f"{stats['unchanged']} unchanged")
    parts.append(f"{stats['changed']} changed")
    if stats["failed"]:
        reasons = stats.get("failure_reasons") or {}
        detail = " (" + ", ".join(f"{n} {r}" for r, n in sorted(reasons.items())) + ")" if reasons else ""
        parts.append(f"{stats['failed']} failed{detail}")
    if stats.get("skipped_no_listings"):
        parts.append(f"{stats['skipped_no_listings']} with no listings found (no Claude call)")
    if stats.get("flagged_empty"):
        parts.append(f"{stats['flagged_empty']} flagged for a better URL")
    line = "Careers pages: " + ", ".join(parts) + "."
    left = int(stats.get("not_reached") or 0) + int(stats.get("out_of_time") or 0)
    if left:
        line += (
            f" Stopped at the {stats.get('time_budget_minutes'):g}-minute careers limit; {left} page(s) "
            "wait for the next run, least recently checked first."
        )

    if stats.get("rate_limited"):
        line += (
            f" Browser Run rate-limited us {stats['rate_limited']} time(s); the job waited, retried, and "
            "slowed down, and did not switch to the backup."
        )
    switch_reason = stats.get("switch_reason")
    if switch_reason == "token":
        line += (
            f" Cloudflare's API rejected the Browser Run token ({stats.get('token_problem') or 'HTTP 401/403'}) "
            "-- this is a token problem, not a site blocking the page. Check CLOUDFLARE_BROWSER_TOKEN and its "
            "Browser Rendering permission; the rest used the backup."
        )
    elif switch_reason in ("quota", "minutes"):
        line += (
            " Browser Run's free daily capacity ran out this run, so the rest used the backup -- "
            "consider Workers Paid for more Browser Rendering minutes."
        )
    elif switch_reason == "missing_token":
        line += " CLOUDFLARE_BROWSER_TOKEN isn't set, so every page used the backup."
    elif switch_reason == "outage":
        line += " Browser Run had an outage this run (3 failed requests in a row), so the rest used the backup."
    return line


async def _verify_and_prune(
    db, companies: list[dict], feed_ok: dict[int, bool], current_urls: dict[int, set[str]],
    browser_budget: BrowserBudget, *, unclear_reader=None,
) -> None:
    company_ids = [c["id"] for c in companies]
    if not company_ids:
        return
    clause, param = in_json("company_id", company_ids)
    postings = await db.all(
        f"SELECT * FROM postings WHERE archived_at IS NULL AND {clause}", param
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


async def _claim_pending_search_request(db, run_id: int) -> int | None:
    """Links this daily run to the newest pending `search_requests` row, if
    any, so the dashboard's Search now button can poll a specific run's
    status. `daily-search.yml` has no workflow_dispatch inputs (unlike
    check-now.yml's request_id), so the link is made here by claiming the
    request instead of the job being told which one started it -- safe
    because the shared Actions concurrency group means only one daily run is
    ever in flight at a time.
    """
    row = await db.first("SELECT id FROM search_requests WHERE status = 'pending' ORDER BY id DESC LIMIT 1")
    if not row:
        return None
    await db.run("UPDATE search_requests SET status = 'running', run_id = ? WHERE id = ?", run_id, row["id"])
    return row["id"]


async def run(db, *, kind: str = "daily", company_ids: list[int] | None = None) -> dict:
    started_at = now_iso()
    run_id = await db.run(
        "INSERT INTO runs (kind, started_at, status) VALUES (?, ?, 'running')", kind, started_at
    )
    search_request_id = await _claim_pending_search_request(db, run_id) if kind == "daily" else None

    try:
        result = await _run_body(db, run_id, started_at, kind, company_ids)
    except Exception as exc:  # noqa: BLE001 - the run row must reflect failures, not stay 'running' forever
        await db.run(
            "UPDATE runs SET finished_at = ?, status = 'failed', summary = ? WHERE id = ?",
            now_iso(), f"Run failed: {exc}", run_id,
        )
        if search_request_id:
            await db.run(
                "UPDATE search_requests SET status = 'failed', result = ? WHERE id = ?",
                json.dumps({"error": str(exc)}), search_request_id,
            )
        if kind == "daily":
            await _finish_daily_run(db, run_status="failed", run_reason=str(exc))
        raise

    if search_request_id:
        await db.run(
            "UPDATE search_requests SET status = 'completed', result = ? WHERE id = ?",
            json.dumps(result, default=str), search_request_id,
        )
    if kind == "daily":
        await _finish_daily_run(
            db, run_status=result.get("run_status") or "completed", run_reason=None,
            agent_outcome=result.get("agent_outcome"), health=result.get("health"),
            run_minutes=result.get("run_minutes"),
        )
    return result


MIN_DISCOVERY_SEARCH_FRACTION = 0.5
ZERO_NEW_STREAK_DAYS = 3


def health_failures(agent_outcome: dict, careers_stats: dict | None, *, careers_candidates: int) -> list[dict]:
    """The run's failed health checks, each `{"check": id, "message": text}`:
      * discovery made at least 50% of its planned searches;
      * if any companies are uncovered, the scout worked on at least one;
      * if any companies have a careers_url (and no feed), at least one careers
        page was attempted.
    """
    failed = []
    planned = int(agent_outcome.get("discovery_planned_searches") or 0)
    made = int(agent_outcome.get("discovery_searches") or 0)
    if planned > 0 and made < planned * MIN_DISCOVERY_SEARCH_FRACTION:
        failed.append({
            "check": "discovery_searches",
            "message": f"discovery made {made} of {planned} planned searches (under 50%)",
        })
    uncovered = int(agent_outcome.get("uncovered_count") or 0)
    if uncovered > 0 and int(agent_outcome.get("scout_companies_worked") or 0) < 1:
        failed.append({
            "check": "scout_ran",
            "message": f"{uncovered} companies are uncovered but the scout worked on none",
        })
    attempted = int((careers_stats or {}).get("attempted") or 0)
    if careers_candidates > 0 and attempted < 1:
        failed.append({
            "check": "careers_attempted",
            "message": f"{careers_candidates} companies have a careers page but none was attempted",
        })
    return failed


async def _zero_new_streak_days(db, today: str, new_count: int) -> int:
    """How many days in a row, ending today, daily runs found 0 new postings."""
    if new_count > 0:
        return 0
    rows = await db.all(
        "SELECT substr(started_at, 1, 10) AS d, SUM(new_count) AS n FROM runs "
        "WHERE kind = 'daily' AND status IN ('completed', 'degraded') AND substr(started_at, 1, 10) < ? "
        "GROUP BY d ORDER BY d DESC LIMIT 14",
        today,
    )
    streak = 1
    expected = date.fromisoformat(today) - timedelta(days=1)
    for row in rows:
        if row["d"] != expected.isoformat() or (row["n"] or 0) > 0:
            break
        streak += 1
        expected -= timedelta(days=1)
    return streak


def _claude_step_issues(agent_outcome: dict) -> list[str]:
    """Every skipped-or-failed Claude step in `agent_outcome`, as a
    human-readable line -- used for both the run summary and the alert, so
    the two always agree on what went wrong.
    """
    issues = [f"{s['step']} skipped ({s['reason']})" for s in agent_outcome.get("steps_skipped") or []]
    issues += list(agent_outcome.get("errors") or [])
    return issues


async def _run_body(db, run_id: int, started_at: str, kind: str, company_ids: list[int] | None) -> dict:
    ranking_summary = None
    if kind == "daily" and is_quarterly_refresh_day(today_central()):
        ranking_summary = await refresh_ranking(db)

    titles_doc = config.load_titles()
    locations_setting = await config.load_locations_setting(db)
    budget_settings = await config.load_budget_settings(db)
    browser_budget = BrowserBudget(remaining=int(budget_settings.get("browser_checks_per_run") or 50))

    # Rejections the old location, cycle, and title rules got wrong are
    # judged again before the feeds are read (issue #60).
    restore_line = None
    try:
        restored = await restore_rejections(
            db, titles_doc=titles_doc, locations_setting=locations_setting, now_iso=now_iso(),
        )
        restore_line = restore_summary_line(restored)
    except Exception as exc:  # noqa: BLE001 - cleanup must never stop the run
        print(f"restore_rejections: {exc}")

    # Postings saved since 2026-10-01 without a matched title are re-judged:
    # archived unless a title matches now (issue #68). Finds nothing once done.
    cleanup_line = None
    if kind == "daily":
        try:
            cleanup_line = cleanup_summary_line(await cleanup_unmatched(db, titles_doc=titles_doc, now_iso=now_iso()))
        except Exception as exc:  # noqa: BLE001 - cleanup must never stop the run
            print(f"cleanup_unmatched: {exc}")

    # Old rejections that already have a restored or re-matched twin (issue #63).
    duplicates_deleted = 0
    if kind == "daily":
        try:
            duplicates_deleted = await delete_duplicate_rejections(db)
        except Exception as exc:  # noqa: BLE001 - cleanup must never stop the run
            print(f"delete_duplicate_rejections: {exc}")

    if company_ids is not None:
        companies = await config.load_companies_by_ids(db, company_ids)
    else:
        companies = await config.load_companies(db)
        if kind == "daily":
            # One time only: give every company with no feed and no careers page the
            # best careers page already in the sources table (issue #63).
            try:
                if await run_backfill_once(db, companies, now_iso=now_iso()):
                    companies = await config.load_companies(db)
            except Exception as exc:  # noqa: BLE001 - the backfill must never stop the run
                print(f"careers backfill: {exc}")

    # Results the rules can't place (fuzzy score 80 to 89, other languages) wait here
    # for classification. They are saved only if it matches them to a title, so
    # without Claude (a Check now run, or no credentials) none are saved.
    candidates: list[Candidate] = []
    (
        new_count, feed_ok, current_urls, no_source_count, feed_error_count, sources_found,
        repeats_recognized, rejections_skipped, feeds_skipped,
    ) = await _fetch_and_store(db, companies, titles_doc, locations_setting, run_id, candidates)

    agent_outcome = None
    unclear_reader = None
    unclear_results: list = []
    careers_stats = None
    # Remembered rejections skipped while reading feeds, and (below) while
    # handling discovery results that would otherwise go to the classifier.
    feed_rejections_skipped = rejections_skipped
    classifier_rejections_skipped = 0
    claude_configured = claude_is_configured()
    if kind == "daily" and claude_configured:
        today_str = today_central().isoformat()
        status = await load_budget_status(db, budget_settings, today=today_str)

        careers_posting_ids: list[int] = []

        # Order (issue #70): feeds, discovery, classification of everything found so far,
        # then this careers step, then the scout and improvement -- so a slow careers step
        # can never stop postings from being classified and saved.
        async def careers_step(shared_candidates: list[Candidate]) -> set[int]:
            nonlocal careers_stats
            try:
                covered, posting_ids, careers_stats = await _run_careers_pages(
                    db, companies, budget_settings, titles_doc, locations_setting,
                    run_id=run_id, run_claude=agent_client.run_claude_step, candidates=shared_candidates,
                )
            except Exception as exc:  # noqa: BLE001 - a careers-pages bug must not stop the run, same as
                # every other per-step isolation in this function.
                print(f"careers_pages: {exc}")
                return set()
            careers_posting_ids.extend(posting_ids)
            return covered

        agent_outcome = await run_agent_steps(
            db, run_id=run_id, companies=companies, titles_doc=titles_doc, locations_setting=locations_setting,
            budget_settings=budget_settings, status=status, today=today_str, now_iso=now_iso(),
            careers_step=careers_step, candidates=candidates,
            run_summary_so_far={
                "summary": (
                    f"Checked {len(companies)} companies, {new_count} new posting(s) so far, "
                    f"{feed_error_count} feed error(s), {no_source_count} with no source yet."
                ),
            },
            # Looked up on the module here (rather than relying on
            # `run_agent_steps`'s own default) so tests can swap in a fake
            # by patching `job.agent.client.run_claude_step` directly,
            # matching how `run_fetcher` is mocked below.
            run_claude=agent_client.run_claude_step,
        )
        new_count += agent_outcome["discovery_new_count"]
        new_count += agent_outcome.get("candidate_new_count", 0)
        new_count += len(careers_posting_ids)
        classifier_rejections_skipped = agent_outcome.get("rejections_skipped", 0)
        repeats_recognized += agent_outcome.get("repeats_recognized", 0)
        unclear_reader = build_unclear_reader(
            status=status, model=budget_settings.get("model_classification", "haiku"),
            on_usage=unclear_results.append,
            on_error=lambda exc: agent_outcome["errors"].append(f"unclear_page: {exc}"),
        )
    elif kind == "daily":
        agent_outcome = {
            "discovery_count": 0, "discovery_new_count": 0, "classification_count": 0, "scout_count": 0,
            "scout_skipped_count": 0, "repeats_recognized": 0, "rejections_skipped": 0,
            "uncovered_count": 0, "covered_count": 0, "scout_companies_worked": 0,
            "discovery_planned_searches": 0, "discovery_searches": 0, "discovery_batches": [],
            "discovery_failed": None,
            "steps_skipped": [{"step": "all", "reason": "Claude isn't configured (no "
                                                          "CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY)"}],
            "steps_succeeded": [], "claude_limit_hit": False, "errors": [], "title_pr": None,
        }

    await _verify_and_prune(db, companies, feed_ok, current_urls, browser_budget, unclear_reader=unclear_reader)

    for unclear_result in unclear_results:
        await record_usage(db, run_id=run_id, step="unclear_page", result=unclear_result, now_iso=now_iso())

    # One set of counts, written to both the summary below and `agent_outcome`
    # (issue #63): the summary used to add feed and discovery rejections together
    # while `agent_outcome` only had discovery's, so thousands in one read 0 or 1
    # in the other. Feed and classifier rejections are labeled separately.
    rejections_skipped = feed_rejections_skipped + classifier_rejections_skipped
    counts = {
        "repeats_recognized": repeats_recognized,
        "feed_rejections_skipped": feed_rejections_skipped,
        "classifier_rejections_skipped": classifier_rejections_skipped,
    }
    if agent_outcome is not None:
        agent_outcome["counts"] = dict(counts)
        agent_outcome["repeats_recognized"] = repeats_recognized
        agent_outcome["feed_rejections_skipped"] = feed_rejections_skipped
        agent_outcome["classifier_rejections_skipped"] = classifier_rejections_skipped
        agent_outcome["rejections_skipped"] = rejections_skipped

    summary = (
        f"Checked {len(companies)} companies, {new_count} new posting(s), "
        f"{feed_error_count} feed error(s), {no_source_count} with no source yet, "
        f"{counts['repeats_recognized']} repeat(s) recognized, "
        f"{counts['feed_rejections_skipped']} remembered feed rejection(s) skipped, "
        f"{counts['classifier_rejections_skipped']} remembered classifier rejection(s) skipped, "
        f"{feeds_skipped} feed(s) unchanged and skipped."
    )
    if duplicates_deleted:
        summary += f" Removed {duplicates_deleted} old rejection(s) that already have a restored twin."
    if restore_line:
        summary += f" {restore_line}"
    if cleanup_line:
        summary += f" {cleanup_line}"
    careers_line = _careers_pages_summary_line(careers_stats)
    if careers_line:
        summary += " " + careers_line
    if kind == "daily" and not claude_configured:
        # Never let this go unsaid (issue #16): a run without Claude
        # credentials still completes, but discovery, classification, the
        # source scout, and daily improvement all silently did nothing.
        summary += " Claude steps skipped: no CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY is set."
    elif kind == "daily" and agent_outcome:
        # Same principle, for the case credentials ARE set but a step still
        # didn't run (budget cut it) or ran and failed (a bad CLI call, a
        # reply that didn't parse): issue #20 was three separate bugs like
        # this hiding behind a summary that looked clean.
        step_issues = _claude_step_issues(agent_outcome)
        if step_issues:
            summary += " Claude step issues: " + "; ".join(step_issues) + "."

    # Health checks (issue #63): a search that quietly did nothing must not look
    # like a clean run. Any failed check marks the run "degraded".
    health = None
    run_status = "completed"
    if kind == "daily" and claude_configured and agent_outcome:
        careers_candidates = len([c for c in companies if c.get("careers_url") and not c.get("feed_type")])
        failed_checks = health_failures(agent_outcome, careers_stats, careers_candidates=careers_candidates)
        zero_days = await _zero_new_streak_days(db, today_central().isoformat(), new_count)
        breakdown = None
        if zero_days >= ZERO_NEW_STREAK_DAYS:
            searches_row = await db.first("SELECT SUM(web_searches) AS n FROM usage WHERE run_id = ?", run_id)
            breakdown = {
                "searches": (searches_row or {}).get("n") or 0,
                "covered": agent_outcome.get("covered_count", 0),
                "pages_loaded": ((careers_stats or {}).get("browser_run", 0) + (careers_stats or {}).get("backup", 0)),
                "classified": agent_outcome.get("classification_count", 0),
            }
        health = {"failed_checks": failed_checks, "zero_new_days": zero_days, "step_breakdown": breakdown}
        if failed_checks:
            run_status = "degraded"
            summary += " Run degraded, failed health check(s): " + "; ".join(c["message"] for c in failed_checks) + "."
        if breakdown:
            summary += (
                f" No new postings for {zero_days} days in a row. Per step today: {breakdown['searches']} search(es), "
                f"{breakdown['covered']} companies covered, {breakdown['pages_loaded']} careers page(s) loaded, "
                f"{breakdown['classified']} posting(s) classified."
            )
    finished_at = now_iso()
    run_minutes = _minutes_between(started_at, finished_at)
    if run_minutes is not None:
        summary += f" Run time: {run_minutes:.1f} min."
        if run_minutes > LONG_RUN_WARNING_MINUTES:
            summary += (
                f" Warning: this run took more than {LONG_RUN_WARNING_MINUTES} minutes "
                "(the private repo has 2,000 free Actions minutes a month)."
            )
    await db.run(
        "UPDATE runs SET finished_at = ?, status = ?, new_count = ?, summary = ? WHERE id = ?",
        finished_at, run_status, new_count, summary, run_id,
    )

    return {
        "run_minutes": run_minutes,
        "run_status": run_status,
        "health": health,
        "run_id": run_id,
        "kind": kind,
        "started_at": started_at,
        "finished_at": finished_at,
        "new_count": new_count,
        "sources_found": sources_found,
        "companies_checked": len(companies),
        "feed_errors": feed_error_count,
        "no_source_count": no_source_count,
        "repeats_recognized": repeats_recognized,
        "rejections_skipped": rejections_skipped,
        "counts": counts,
        "feeds_skipped": feeds_skipped,
        "ranking_refresh": ranking_summary,
        "summary": summary,
        "agent_outcome": agent_outcome,
        "careers_pages": careers_stats,
    }


async def feedback_summary_line(db) -> str | None:
    """"3 new feedback items, 1 about a dead link." -- counts only, never message
    text. Counts feedback created since the previous daily run started; None when
    there is none. The current run is the latest daily run, so the previous one is
    the second latest.
    """
    runs = await db.all("SELECT started_at FROM runs WHERE kind = 'daily' ORDER BY id DESC LIMIT 2")
    since = runs[1]["started_at"] if len(runs) > 1 and runs[1].get("started_at") else ""
    row = await db.first(
        "SELECT COUNT(*) AS n, COALESCE(SUM(kind = 'dead_link'), 0) AS dead FROM feedback WHERE created_at > ?", since
    )
    total, dead = (row or {}).get("n") or 0, (row or {}).get("dead") or 0
    if not total:
        return None
    line = f"{total} new feedback item{'' if total == 1 else 's'}"
    if dead:
        line += f", {dead} about a dead link"
    return line + "."


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


async def _browser_run_alert_inputs(db, *, today: str) -> tuple[float, float, list[str]]:
    """(minutes_used_today, hours_used_this_month, capacity_backup_days) for
    the Browser Run alerts (issue #55) -- all from `browser_run_usage`,
    bucketed by Central calendar day/month like every other budget here.
    """
    today_date = date.fromisoformat(today)
    month_start = today_date.replace(day=1).isoformat()
    week_start = (today_date - timedelta(days=6)).isoformat()

    today_row = await db.first(
        "SELECT SUM(browser_ms) AS ms FROM browser_run_usage "
        "WHERE method = 'browser_run' AND substr(at, 1, 10) = ?",
        today,
    )
    minutes_used_today = ((today_row or {}).get("ms") or 0) / 60000.0

    month_row = await db.first(
        "SELECT SUM(browser_ms) AS ms FROM browser_run_usage "
        "WHERE method = 'browser_run' AND substr(at, 1, 10) >= ? AND substr(at, 1, 10) <= ?",
        month_start, today,
    )
    hours_used_month = ((month_row or {}).get("ms") or 0) / 3600000.0

    capacity_rows = await db.all(
        "SELECT DISTINCT substr(at, 1, 10) AS d FROM browser_run_usage "
        "WHERE method = 'backup' AND backup_reason IN ('minutes', 'quota') "
        "AND substr(at, 1, 10) >= ? AND substr(at, 1, 10) <= ?",
        week_start, today,
    )
    capacity_backup_days = [r["d"] for r in capacity_rows]

    return minutes_used_today, hours_used_month, capacity_backup_days


async def _finish_daily_run(
    db, *, run_status: str, run_reason: str | None, agent_outcome: dict | None = None, health: dict | None = None,
    run_minutes: float | None = None,
) -> None:
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

        minutes_used_today, hours_used_month, capacity_backup_days = await _browser_run_alert_inputs(db, today=today_str)
        candidates.extend(evaluate_browser_run_alerts(
            minutes_used_today=minutes_used_today,
            daily_minutes_budget=float(
                budget_settings.get("browser_run_daily_minutes") or careers_pages.DEFAULT_BROWSER_RUN_DAILY_MINUTES
            ),
            plan=budget_settings.get("browser_run_plan") or "free",
            hours_used_month=hours_used_month,
            hours_included_month=BROWSER_RUN_PAID_INCLUDED_MONTHLY_HOURS,
        ))
        workers_paid_suggestion = evaluate_workers_paid_suggestion(capacity_backup_days)
        if workers_paid_suggestion:
            candidates.append(workers_paid_suggestion)
        if agent_outcome and agent_outcome.get("claude_limit_hit"):
            candidates.append(claude_limit_alert("; ".join(agent_outcome.get("errors") or []) or "usage limit reached"))
        if not claude_is_configured():
            candidates.append(claude_not_configured_alert())
        elif agent_outcome and not agent_outcome.get("claude_limit_hit"):
            # A limit hit already gets its own, clearer alert above; this
            # covers everything else that skipped or failed -- budget cuts
            # (also covered by the budget alerts above, but named here too)
            # and a step that ran and failed outright. A run doing no Claude
            # work at all must never look clean (issue #20).
            step_issues = _claude_step_issues(agent_outcome)
            if step_issues:
                candidates.append(claude_step_issue_alert(
                    step_issues, no_claude_work_done=not agent_outcome.get("steps_succeeded"),
                ))
        if agent_outcome and agent_outcome.get("discovery_failed"):
            candidates.append(discovery_failed_alert(agent_outcome["discovery_failed"]))
        failed_checks = (health or {}).get("failed_checks") or []
        if failed_checks:
            candidates.append(run_degraded_alert([c["message"] for c in failed_checks]))
        long_run = evaluate_long_run_alert(run_minutes, limit_minutes=LONG_RUN_WARNING_MINUTES)
        if long_run:
            candidates.append(long_run)
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
            health={
                "failed_checks": [c["check"] for c in failed_checks],
                "zero_new_days": (health or {}).get("zero_new_days", 0),
                "step_breakdown": (health or {}).get("step_breakdown"),
            },
            run_minutes=run_minutes,
            feedback_line=await feedback_summary_line(db),
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
