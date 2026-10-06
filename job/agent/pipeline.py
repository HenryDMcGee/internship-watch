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
from datetime import date

from job.agent import careers_repair, discovery, improvement, scout
from job.agent import classification as classification_mod
from job.agent import unclear as unclear_mod
from job.agent.budgets import BudgetStatus, model_for_step
from job.agent.client import ClaudeLimitError
from job.agent.client import run_claude_step as _default_run_claude
from job.agent.github_pr import github_client_from_env
from job.agent.schemas import ClaudeOutputError
from job.agent.titles_pr import open_title_suggestions_pr
from job.agent.usage import record_usage
from job.config import load_search_plan, load_watchlist_setting
from job import coverage, fingerprint
from job.locations import location_match as compute_location_match
from job.matching import classify_posting, find_role_conflict, reconsider_rejection
from job.save_posting import CLASSIFICATION_METHOD, Candidate, canonical_active_title, save_posting

# `job.config` (imported above) adds `api/src` to `sys.path`, which this
# import needs -- see `job/feeds/detect.py`'s docstring for why company
# logic lives in `api/src/companies.py` and is reused here rather than
# reimplemented.
from companies import detect_feed, normalize_company_name  # noqa: E402
from sql_helpers import in_json  # noqa: E402


async def run_agent_steps(
    db, *, run_id: int, companies: list[dict], titles_doc: dict, locations_setting: dict,
    budget_settings: dict, status: BudgetStatus, today: str, now_iso: str,
    run_summary_so_far: dict | None = None,
    careers_covered_ids: set[int] | None = None,
    candidates: list[Candidate] | None = None,
    run_claude=_default_run_claude,
) -> dict:
    outcome = {
        "discovery_count": 0, "discovery_new_count": 0, "candidate_new_count": 0, "classification_count": 0,
        "scout_count": 0,
        "scout_skipped_count": 0, "repeats_recognized": 0, "rejections_skipped": 0, "careers_repair_count": 0,
        "uncovered_count": 0, "covered_count": 0, "scout_companies_worked": 0,
        "discovery_planned_searches": 0, "discovery_searches": 0, "discovery_batches": [], "discovery_failed": None,
        "steps_skipped": [], "steps_succeeded": [], "claude_limit_hit": False, "errors": [], "title_pr": None,
    }
    settings_ver = fingerprint.settings_version(titles_doc, locations_setting)

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

    # Covered = a working feed, or a careers page that loaded in the last 7 days
    # (job/coverage.py). Discovery's web searches are the most expensive part
    # of a run, so they go to the watch list first and then to companies still
    # uncovered after feeds and careers pages. A `sources` row alone is not
    # coverage (issue #63).
    covered_ids = await coverage.covered_company_ids(db, companies, today=date.fromisoformat(today))
    covered_ids |= set(careers_covered_ids or set())
    uncovered_companies = [c for c in companies if c["id"] not in covered_ids]
    outcome["uncovered_count"] = len(uncovered_companies)
    outcome["covered_count"] = len(companies) - len(uncovered_companies)

    watchlist = await load_watchlist_setting(db)
    watch_names = {normalize_company_name(w) for w in watchlist}
    discovery_companies = [
        c for c in companies
        if c["id"] not in covered_ids or normalize_company_name(c.get("name") or "") in watch_names
    ]
    discovery_report = discovery.DiscoveryReport()

    async def do_discovery():
        ordered = discovery.order_companies(discovery_companies, watchlist)
        search_plan = await load_search_plan(db, today)
        daily_search_budget = int(budget_settings.get("daily_search_budget") or 0)
        daily_remaining = max(daily_search_budget - status.today.searches, 0) if daily_search_budget > 0 else None
        try:
            discovery.run_discovery(
                ordered, titles_doc, locations_setting, search_plan,
                model=budget_settings.get("model_discovery", "sonnet"),
                search_limit_per_run=int(budget_settings.get("search_limit_per_run") or 35),
                search_limit_per_company=int(budget_settings.get("search_limit_per_company") or 5),
                daily_searches_remaining=daily_remaining,
                run_claude=run_claude, report=discovery_report,
            )
        finally:
            # Every call spent real budget, including the ones before a usage
            # limit stopped the step.
            for usage_result in discovery_report.usage:
                await record("discovery", usage_result)
            discovery_report.usage.clear()
        return discovery_report.results

    discovery_ids: list[int] = []
    if candidates is None:
        candidates = []
    if discovery_companies:
        discovery_results = await guarded("discovery", do_discovery) or []
        outcome["discovery_planned_searches"] = discovery_report.planned_searches
        outcome["discovery_searches"] = discovery_report.searches_made
        outcome["discovery_batches"] = [b.line() for b in discovery_report.batches]
        if discovery_report.searched_ids:
            clause, param = in_json("id", sorted(discovery_report.searched_ids))
            await db.run(f"UPDATE companies SET last_discovery_searched_at = ? WHERE {clause}", now_iso, param)
        if discovery_report.failed:
            # Searches fell short even after the retry: the step failed, whatever it
            # did find is still stored below, and the daily alert names it.
            if "discovery" in steps_succeeded:
                steps_succeeded.discard("discovery")
                outcome["steps_succeeded"].remove("discovery")
            outcome["errors"].append(f"discovery: {discovery_report.failed_reason}")
            outcome["discovery_failed"] = discovery_report.failed_reason
        (
            outcome["discovery_count"], discovery_ids, discovery_repeats, discovery_rejections,
        ) = await _store_discovery_results(
            db, discovery_results, now_iso=now_iso, locations_setting=locations_setting,
            settings_version=settings_ver, titles_doc=titles_doc, candidates=candidates,
        )
        outcome["repeats_recognized"] += discovery_repeats
        outcome["rejections_skipped"] += discovery_rejections
        outcome["discovery_new_count"] = len(discovery_ids)

    # Nothing is saved without a matched title (issue #68): results the rules
    # matched were saved above, and the ones they couldn't place wait in
    # `candidates` (from discovery, feeds, and careers pages). Only a
    # classification "match" saves one; a rejection is remembered, not saved.
    async def do_classification():
        decisions, result = classification_mod.run_classification(
            [c.prompt_row() for c in candidates], titles_doc, locations_setting,
            model=budget_settings.get("model_classification", "haiku"),
            run_claude=run_claude,
        )
        await record("classification", result)
        return decisions

    if candidates:
        decisions = await guarded("classification", do_classification) or []
        saved = await _apply_classification_decisions(
            db, decisions, candidates, now_iso=now_iso, settings_version=settings_ver,
            titles_doc=titles_doc, locations_setting=locations_setting,
        )
        outcome["classification_count"] = len(saved)
        # Only what classification matched and saved is a new posting on the
        # dashboard, so that is what counts as "new" for the summary (issue #26).
        discovery_saved = {c.id for c in candidates if c.event_detail.get("discovery")}
        outcome["discovery_new_count"] += sum(1 for cid in saved if cid in discovery_saved)
        outcome["candidate_new_count"] = len(saved) - sum(1 for cid in saved if cid in discovery_saved)

    search_limit_per_run = int(budget_settings.get("search_limit_per_run") or 35)
    scout_candidates, outcome["scout_skipped_count"] = scout.split_companies_by_coverage(
        companies, covered_ids=covered_ids,
    )
    scout_batch = scout.order_companies_for_scout(scout_candidates, limit=scout.SCOUT_COMPANIES_PER_RUN)
    print(
        f"scout: {outcome['scout_skipped_count']} covered, {len(scout_candidates)} uncovered, "
        f"working on {len(scout_batch)} this run"
    )

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
        outcome["scout_count"] = await _store_scout_sources(
            db, found_sources, now_iso=now_iso, companies=scout_batch
        )
        if "scout" in steps_succeeded:
            await _mark_companies_scouted(db, scout_batch, now_iso=now_iso)
            outcome["scout_companies_worked"] = len(scout_batch)

    # Companies whose careers page has failed 3 checks in a row
    # (job/run.py's `_run_careers_pages`) -- the repair scout looks for a
    # better page for each, preferring an internship-filtered listing page
    # (issue #55).
    flagged_companies = [c for c in companies if c.get("careers_page_flagged")]

    async def do_careers_repair():
        found, result = careers_repair.run_repair(
            flagged_companies, model=model_for_step(budget_settings, "careers_repair"),
            search_limit_per_run=search_limit_per_run, run_claude=run_claude,
        )
        await record("careers_repair", result)
        return found

    if flagged_companies:
        replacements = await guarded("careers_repair", do_careers_repair) or []
        outcome["careers_repair_count"] = await _store_careers_repair_proposals(
            db, replacements, companies=flagged_companies, now_iso=now_iso,
        )

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

async def _store_discovery_results(
    db, results: list[dict], *, now_iso: str, locations_setting: dict, settings_version: str,
    titles_doc: dict, candidates: list[Candidate],
) -> tuple[int, list[int], int, int]:
    """Judges each discovery result with the plain rules. A match is saved (through
    `save_posting`, so only with a matched title), a rejection is remembered, and
    anything the rules can't place is appended to `candidates` for classification.
    Returns (results kept or queued, ids saved now, repeats recognized, rejections skipped).
    """
    stored = 0
    inserted_ids: list[int] = []
    repeats_recognized = 0
    rejections_skipped = 0
    for r in results:
        # A URL match is exact, but a company re-listing an already-known
        # role under a new URL is common enough (issue #26/#49: an NVIDIA
        # role Henry had already marked applied came back under a
        # different URL) that URL alone isn't enough --
        # `fingerprint.match_incoming` also matches on the company's own
        # job id and on company + normalized title + location so that
        # role never gets a second row, and a result already rejected once
        # (by Claude classification) is skipped without spending a call.
        outcome = await fingerprint.match_incoming(
            db, company_name=r["company_name"], title=r["title"], url=r["url"],
            location_text=r.get("location_text") or "", settings_version=settings_version,
            source="claude-discovery", now_iso=now_iso,
        )
        if outcome.kind == "posting":
            repeats_recognized += 1
            continue
        if outcome.kind == "rejection":
            rejections_skipped += 1
            continue

        match = classify_posting(r["title"], "", titles_doc)
        if match.decision == "reject":
            await fingerprint.record_rejection(
                db, outcome.fp, reason=match.reason, settings_version=settings_version,
                source="claude-discovery", now_iso=now_iso, existing_id=outcome.stale_fingerprint_id,
            )
            continue
        stored += 1
        if match.decision == "classify":
            candidates.append(Candidate(
                id=len(candidates) + 1, company_name=r["company_name"], title=r["title"], url=r["url"],
                source_name="claude-discovery", location_text=r.get("location_text") or "",
                posted_date=r.get("posted_date"), fp=outcome.fp, fingerprint_source="claude-discovery",
                stale_fingerprint_id=outcome.stale_fingerprint_id,
                event_detail={"source": "claude-discovery", "discovery": True},
            ))
            continue

        new_id = await save_posting(
            db, titles_doc=titles_doc, matched_title=match.matched_title, match_method=match.match_method,
            company_name=r["company_name"], title=r["title"], url=r["url"], source_name="claude-discovery",
            locations=[r["location_text"]] if r.get("location_text") else [],
            location_match=compute_location_match(r.get("location_text") or "", locations_setting),
            posted_date=r.get("posted_date"), first_seen=now_iso[:10], fp=outcome.fp,
            settings_version=settings_version, fingerprint_source="claude-discovery", now_iso=now_iso,
            stale_fingerprint_id=outcome.stale_fingerprint_id, event_detail={"source": "claude-discovery"},
        )
        if new_id is not None:
            inserted_ids.append(new_id)
    return stored, inserted_ids, repeats_recognized, rejections_skipped


async def count_visible_postings(db, ids: list[int]) -> int:
    """How many of `ids` are still on the dashboard: classified with a
    matched title and not archived. Used to count discovery's real
    contribution to a run's `new_count` only after classification has run.
    """
    if not ids:
        return 0
    clause, param = in_json("id", ids)
    row = await db.first(
        f"SELECT COUNT(*) AS n FROM postings WHERE archived_at IS NULL AND matched_title IS NOT NULL "
        f"AND {clause}",
        param,
    )
    return (row or {}).get("n") or 0


async def _apply_classification_decisions(
    db, decisions: list[dict], candidates: list[Candidate], *, now_iso: str, settings_version: str,
    titles_doc: dict, locations_setting: dict,
) -> set[int]:
    """Applies classification's decisions to the candidates it was sent and returns
    the ids of the candidates that were saved.

    Only a "match" naming an active title is saved, through `save_posting`. A
    rejection, a match that names no active title, or a clearly off-role title
    (engineering, lab, hardware, design, sales, undergraduate) is remembered as a
    rejection with its reason and never saved. A candidate with no decision is left
    alone, so it is judged again on a later run.
    """
    by_id = {c.id: c for c in candidates}
    saved: set[int] = set()
    unknown_ids = []
    for d in decisions:
        candidate = by_id.get(d.get("id"))
        if candidate is None:
            unknown_ids.append(d.get("id"))
            continue
        if candidate.id in saved:
            continue

        match_method = CLASSIFICATION_METHOD
        # A rejection about location, hiring cycle, or an unmatched title is
        # checked against the plain rules before it sticks: a missing or
        # unrecognizable location never rejects, and a reason that admits
        # the title matches is treated as unclear (issue #60).
        if d["decision"] == "reject":
            recovered = reconsider_rejection(
                d.get("reason"), candidate.title, candidate.location_text, titles_doc, locations_setting,
            )
            if recovered is not None:
                d = {
                    **d, "decision": "match", "matched_title": recovered.matched_title,
                    "location_match": recovered.location_match,
                }
                match_method = recovered.match_method

        reason = d.get("reason") or "not a match"
        matched_title = canonical_active_title(d.get("matched_title"), titles_doc)
        if d["decision"] == "match":
            conflict = find_role_conflict(candidate.title, matched_title or "")
            if conflict:
                d = {**d, "decision": "reject"}
                reason = f"not a product or business role ({conflict})"
            elif matched_title is None:
                d = {**d, "decision": "reject"}
                reason = "no matching title"

        if d["decision"] == "match":
            new_id = await save_posting(
                db, titles_doc=titles_doc, matched_title=matched_title, match_method=match_method,
                company_id=candidate.company_id, company_name=candidate.company_name, title=candidate.title,
                title_en=d.get("title_en"), url=candidate.url, source_name=candidate.source_name,
                locations=[candidate.location_text] if candidate.location_text else [],
                location_match=d.get("location_match")
                or compute_location_match(candidate.location_text, locations_setting),
                posted_date=candidate.posted_date, first_seen=now_iso[:10], fp=candidate.fp,
                settings_version=settings_version, fingerprint_source="claude-classification", now_iso=now_iso,
                stale_fingerprint_id=candidate.stale_fingerprint_id, event_detail=candidate.event_detail,
            )
            if new_id is not None:
                saved.add(candidate.id)
        else:
            await fingerprint.record_rejection(
                db, candidate.fp, reason=reason, settings_version=settings_version,
                source="claude-classification", now_iso=now_iso, existing_id=candidate.stale_fingerprint_id,
            )
    if unknown_ids:
        print(f"classification: {len(unknown_ids)} decision(s) referenced unknown candidate id(s): {unknown_ids}")
    return saved


async def _store_scout_sources(db, found: list[dict], *, now_iso: str, companies: list[dict]) -> int:
    # `scout.next_source_status` (trial -> active after a real open posting,
    # or -> demoted after repeated empty checks) needs a found/empty-check
    # count this step doesn't have -- Claude proposing a URL again isn't
    # proof of an open posting. That promotion/demotion signal belongs with
    # whatever actually checks the source (a future increment); for now,
    # re-finding an already-known source just refreshes when it was last
    # seen, and status changes stay a later step's job.
    companies_by_name = {
        normalize_company_name(c["name"]): c for c in companies if c.get("name")
    }
    stored = 0
    companies_updated = 0
    companies_covered: set[int] = set()
    found_by_company: dict[int, list[dict]] = {}
    for f in found:
        existing = await db.first("SELECT * FROM sources WHERE url = ?", f["url"])
        if existing:
            await db.run("UPDATE sources SET last_checked = ? WHERE id = ?", now_iso, existing["id"])
        else:
            row = scout.new_source_row(f, now_iso=now_iso)
            await db.run(
                """INSERT INTO sources (name, url, kind, covers, status, discovered_by, discovered_at,
                   found_count, last_checked)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                row["name"], row["url"], row["kind"], row["covers"], row["status"],
                row["discovered_by"], row["discovered_at"], row["found_count"], row["last_checked"],
            )
            stored += 1
        company = companies_by_name.get(normalize_company_name(f.get("company_name") or ""))
        if company and company.get("id") is not None:
            found_by_company.setdefault(company["id"], []).append(f)
            if company["id"] not in companies_covered:
                # Informational only: coverage no longer reads this (issue #63) --
                # it needs a working feed or a careers page that loaded.
                await db.run("UPDATE companies SET last_source_found_at = ? WHERE id = ?", now_iso, company["id"])
                companies_covered.add(company["id"])
    # One page per company: a company the scout reports several pages for gets
    # the best one (a feed platform, then an internship-filtered listing page).
    for company_id, candidates in found_by_company.items():
        best = min(candidates, key=_scout_candidate_rank)
        if await _apply_scout_company_feed(db, best, companies_by_name):
            companies_updated += 1
    print(
        f"scout: wrote {stored} new source(s) ({len(found) - stored} already known), "
        f"set feed info on {companies_updated} compan{'y' if companies_updated == 1 else 'ies'}"
    )
    return stored


async def _apply_scout_company_feed(db, found: dict, companies_by_name: dict) -> bool:
    """Attaches a scout result to the company it names, on the company's own
    row, so the company stops counting as uncovered (issues #20, #63):
      * a URL on a recognized feed platform sets `feed_type` and `feed_id`;
      * any other URL sets `careers_url` and marks it "careers page, no feed",
        for the careers-page reader.
    Never overwrites a working feed, or a `careers_url` Henry approved
    (`careers_url_approved`); both are read fresh from the row, not from the
    copy loaded at the start of the run. Returns whether the row changed.
    """
    company = companies_by_name.get(normalize_company_name(found.get("company_name") or ""))
    url = (found.get("url") or "").strip()
    if not company or not url:
        return False
    row = await db.first("SELECT * FROM companies WHERE id = ?", company["id"])
    if not row or coverage.feed_is_working(row):
        return False
    approved = bool(row.get("careers_url_approved"))
    feed_type, feed_id = detect_feed(url)

    if feed_type:
        if approved:
            await db.run(
                "UPDATE companies SET feed_type = ?, feed_id = ?, careers_note = NULL WHERE id = ?",
                feed_type, feed_id, company["id"],
            )
        else:
            await db.run(
                "UPDATE companies SET careers_url = ?, feed_type = ?, feed_id = ?, careers_note = NULL WHERE id = ?",
                url, feed_type, feed_id, company["id"],
            )
        return True

    if approved or row.get("careers_url") == url:
        return False
    await db.run(
        "UPDATE companies SET careers_url = ?, careers_note = ?, careers_page_flagged = 0 WHERE id = ?",
        url, coverage.CAREERS_NO_FEED_NOTE, company["id"],
    )
    await db.run(
        "UPDATE careers_page_snapshots SET consecutive_failures = 0, last_error = NULL WHERE company_id = ?",
        company["id"],
    )
    return True


def _scout_candidate_rank(found: dict) -> tuple[int, int]:
    """Lower is better: a recognized feed platform first, then a URL that
    looks like an internship-filtered listing page.
    """
    url = found.get("url") or ""
    feed_type, _ = detect_feed(url)
    return (0 if feed_type else 1, 0 if "intern" in url.lower() else 1)


async def _store_careers_repair_proposals(
    db, replacements: list[dict], *, companies: list[dict], now_iso: str,
) -> int:
    """One `careers_repair` proposal per usable replacement (issue #55).
    Skipped without a proposal (not an error) when Claude names a company
    that wasn't in the flagged batch it was sent, proposes the company's
    current URL back, or proposes a URL Henry already rejected for that
    company -- `companies.careers_url_rejected`, updated by
    `api/src/main.py`'s reject handler, is exactly what keeps that last case
    from repeating run after run.
    """
    companies_by_name = {normalize_company_name(c["name"]): c for c in companies if c.get("name")}
    stored = 0
    for r in replacements:
        company = companies_by_name.get(normalize_company_name(r.get("company_name") or ""))
        if not company:
            continue
        new_url = (r.get("url") or "").strip()
        if not new_url or new_url == company.get("careers_url"):
            continue
        rejected = company.get("careers_url_rejected")
        if isinstance(rejected, str):
            rejected = json.loads(rejected or "[]")
        if new_url in (rejected or []):
            continue
        await db.run(
            "INSERT INTO proposals (kind, payload, status, created_at) VALUES ('careers_repair', ?, 'pending', ?)",
            json.dumps({
                "company_id": company["id"], "company_name": company["name"],
                "old_url": company.get("careers_url"), "new_url": new_url, "note": r.get("note"),
            }),
            now_iso,
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
