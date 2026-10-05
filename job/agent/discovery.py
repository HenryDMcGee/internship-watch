"""Discovery: Claude searches the open web, company by company, for the
watch list and for companies with no feed and no working careers page.

Issue #63: one call used to cover about 180 companies, and made 3 to 6
searches for all of them. Now each call covers one batch of 15 to 20
companies, must answer for every company in it (searched or not, the queries
used, the postings found or "none found"), and a company it didn't search is
retried once in a later batch. If searches still fall short after the retry
the step is reported as failed, so discovery can't quietly do nothing again.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from job import config  # noqa: F401 -- adds api/src to sys.path for the import below
from job.agent.client import ClaudeCallError, ClaudeLimitError, ClaudeStepResult, resolve_model, run_claude_step
from job.agent.schemas import NONE_FOUND, ClaudeOutputError, parse_and_validate, validate_discovery_batch_output

from companies import normalize_company_name  # noqa: E402

DEFAULT_SEARCH_LIMIT_PER_RUN = 35
DEFAULT_SEARCH_LIMIT_PER_COMPANY = 5
MAX_BATCH_SIZE = 20  # batches are 15 to 20 companies when there are enough companies to fill one
MAX_CONSECUTIVE_EMPTY_CALLS = 2  # two calls in a row that made no searches: stop, don't burn budget
REPLY_HEAD_CHARS = 500


@dataclass
class BatchLog:
    label: str
    companies: list[str]
    searches: int
    results: int
    reply_head: str | None = None  # first 500 characters of a reply that returned no results
    problem: str | None = None

    def line(self) -> str:
        text = (
            f"discovery {self.label}: {len(self.companies)} compan{'y' if len(self.companies) == 1 else 'ies'}, "
            f"{self.searches} search(es), {self.results} result(s)"
        )
        if self.problem:
            text += f" ({self.problem})"
        if self.reply_head is not None:
            text += f"; reply began: {self.reply_head!r}"
        return text


@dataclass
class DiscoveryReport:
    """Filled in as discovery runs, so a caller can still use what was found
    and record what was spent when a later batch raises (a usage limit).
    """
    results: list[dict] = field(default_factory=list)
    usage: list[ClaudeStepResult] = field(default_factory=list)
    batches: list[BatchLog] = field(default_factory=list)
    planned_companies: int = 0
    planned_searches: int = 0
    searches_made: int = 0
    searched_ids: set = field(default_factory=set)
    unsearched: list[str] = field(default_factory=list)
    failed_reason: str | None = None

    @property
    def failed(self) -> bool:
        return self.failed_reason is not None


def order_companies(companies: list[dict], watchlist: list[str]) -> list[dict]:
    """Watch-list companies first, then the rest. Within each group, the
    company searched longest ago goes first (never searched counts as
    longest), so every company gets a turn before any gets a second one.
    Ties keep the watch list's own order, then rank, then name.
    """
    watch_normalized = [normalize_company_name(w) for w in watchlist]

    def watch_index(company: dict) -> int | None:
        name = normalize_company_name(company.get("name") or "")
        return watch_normalized.index(name) if name in watch_normalized else None

    def sort_key(company: dict):
        index = watch_index(company)
        rank = company.get("rank")
        rank_key = float(rank) if rank is not None else float("inf")
        return (
            0 if index is not None else 1,
            company.get("last_discovery_searched_at") or "",
            index if index is not None else 0,
            rank_key,
            (company.get("name") or "").strip().lower(),
        )

    return sorted(companies, key=sort_key)


def split_batches(companies: list[dict], *, max_size: int = MAX_BATCH_SIZE) -> list[list[dict]]:
    """Even batches of at most `max_size`: 180 companies is 9 batches of 20,
    35 is 2 batches of 18.
    """
    if not companies:
        return []
    count = math.ceil(len(companies) / max_size)
    size = math.ceil(len(companies) / count)
    return [companies[i : i + size] for i in range(0, len(companies), size)]


def build_prompt(
    batch: list[dict], titles_doc: dict, locations_setting: dict, search_plan: dict | None,
    *, search_limit_per_company: int,
) -> str:
    titles = [t["title"] for t in titles_doc.get("titles", []) if t.get("active")]
    company_names = [c["name"] for c in batch]
    countries = locations_setting.get("countries") or ["US"]
    plan = search_plan or {}
    query_templates = plan.get("query_templates") or []
    plan_notes = plan.get("notes")

    lines = [
        "You are helping find open MBA-level product internships for Summer 2027.",
        f"These {len(company_names)} companies have no working job feed on file, or are on the watch list: "
        f"{'; '.join(company_names)}.",
        f"Look only for these title types, or close variants: {', '.join(titles)}.",
        f"Locations of interest: {', '.join(countries)}"
        + (" (remote included)." if locations_setting.get("include_remote") else " (remote not included)."),
        "Search for EACH company by name, one company at a time. For every company run its own web searches, "
        "for example \"<company> MBA product manager intern Summer 2027\" and "
        "\"<company> careers internship MBA\". Do not stop after a few general searches: every company in "
        "the list needs at least one search of its own, so expect at least "
        f"{len(company_names)} searches in total, and use at most {search_limit_per_company} per company.",
        "Only include postings you found a real, working URL for. Do not invent results.",
        "Do not search or scrape LinkedIn. Prefer each company's own careers page or a known feed platform "
        "(Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Amazon.jobs). Don't try to get around a site "
        "that blocks automated browsers or shows a CAPTCHA -- move on instead.",
    ]
    if query_templates:
        lines.append("Query wording to try first: " + "; ".join(query_templates))
    if plan_notes:
        lines.append(f"Notes from yesterday's search: {plan_notes}")
    lines.append(
        "Reply with only JSON, no other text, with exactly one entry per company in the list above, in this "
        'exact shape: {"companies": [{"company_name": string, "searched": true or false, '
        '"queries": [the search queries you ran for this company], '
        '"postings": [{"title": string, "url": string, "location_text": string, "posted_date": string or null}] '
        f'or "{NONE_FOUND}"}}]}}. '
        'Set "searched" to false for a company you did not search. '
        'Use "" for location_text if unknown, and "none found" for postings when you searched and found nothing.'
    )
    return "\n".join(lines)


def _entries_by_company(payload: dict) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for entry in payload.get("companies") or []:
        entries.setdefault(normalize_company_name(entry["company_name"]), entry)
    return entries


def _run_batch(
    batch: list[dict], *, label: str, titles_doc: dict, locations_setting: dict, search_plan: dict | None,
    model: str, search_limit_per_company: int, run_claude, report: DiscoveryReport,
) -> tuple[list[dict], int]:
    """One Claude call for `batch`. Returns (companies still unsearched, searches this call made)."""
    names = [c["name"] for c in batch]
    prompt = build_prompt(
        batch, titles_doc, locations_setting, search_plan, search_limit_per_company=search_limit_per_company,
    )
    try:
        result = run_claude("discovery", prompt, model=model)
    except ClaudeLimitError:
        raise
    except ClaudeCallError as exc:
        log = BatchLog(label, names, 0, 0, problem=f"call failed: {exc}")
        report.batches.append(log)
        print(log.line())
        return list(batch), 0

    report.usage.append(result)
    searches = int(result.web_searches or 0)
    report.searches_made += searches

    try:
        payload = parse_and_validate(result.text, validate_discovery_batch_output, result=result)
    except ClaudeOutputError as exc:
        log = BatchLog(label, names, searches, 0, reply_head=(result.text or "")[:REPLY_HEAD_CHARS], problem=str(exc)[:300])
        report.batches.append(log)
        print(log.line())
        return list(batch), searches

    entries = _entries_by_company(payload)
    found = 0
    claimed_searched: list[dict] = []
    for company in batch:
        entry = entries.get(normalize_company_name(company["name"]))
        if entry is None:
            continue
        postings = entry.get("postings")
        for posting in postings if isinstance(postings, list) else []:
            found += 1
            report.results.append({
                "company_name": company["name"], "title": posting["title"], "url": posting["url"],
                "location_text": posting.get("location_text") or "", "posted_date": posting.get("posted_date"),
            })
        if entry.get("searched") is True:
            claimed_searched.append(company)

    # A company can't have been searched by name without at least one search,
    # so a call that made fewer searches than companies can only vouch for
    # that many of them -- the rest are unsearched, whatever the reply says.
    credited = claimed_searched[:searches]
    credited_ids = {c["id"] for c in credited if c.get("id") is not None}
    report.searched_ids |= credited_ids
    unsearched = [c for c in batch if c not in credited]

    log = BatchLog(
        label, names, searches, found,
        reply_head=(result.text or "")[:REPLY_HEAD_CHARS] if found == 0 else None,
        problem=f"{len(unsearched)} unsearched" if unsearched else None,
    )
    report.batches.append(log)
    print(log.line())
    return unsearched, searches


def run_discovery(
    companies: list[dict], titles_doc: dict, locations_setting: dict, search_plan: dict | None,
    *, model: str,
    search_limit_per_run: int = DEFAULT_SEARCH_LIMIT_PER_RUN,
    search_limit_per_company: int = DEFAULT_SEARCH_LIMIT_PER_COMPANY,
    daily_searches_remaining: int | None = None,
    run_claude=run_claude_step,
    report: DiscoveryReport | None = None,
) -> DiscoveryReport:
    """`companies` must already be in search order (`order_companies`). At
    most `search_limit_per_run` of them (fewer if the daily search allowance
    is lower) are searched this run, in batches of 15 to 20.
    """
    report = report if report is not None else DiscoveryReport()
    if not companies:
        return report

    cap = max(int(search_limit_per_run), 0)
    if daily_searches_remaining is not None:
        cap = min(cap, max(int(daily_searches_remaining), 0))
    planned = companies[:cap]
    report.planned_companies = len(planned)
    report.planned_searches = len(planned)
    if not planned:
        return report

    model = resolve_model(model)
    batches = split_batches(planned)
    unsearched: list[dict] = []
    empty_calls_in_a_row = 0
    stopped_early = False

    def run(batch: list[dict], label: str) -> None:
        nonlocal empty_calls_in_a_row, stopped_early
        missed, searches = _run_batch(
            batch, label=label, titles_doc=titles_doc, locations_setting=locations_setting,
            search_plan=search_plan, model=model, search_limit_per_company=search_limit_per_company,
            run_claude=run_claude, report=report,
        )
        unsearched.extend(missed)
        empty_calls_in_a_row = empty_calls_in_a_row + 1 if searches == 0 else 0
        if empty_calls_in_a_row >= MAX_CONSECUTIVE_EMPTY_CALLS:
            stopped_early = True

    for number, batch in enumerate(batches, start=1):
        if stopped_early:
            unsearched.extend(batch)
            continue
        run(batch, f"batch {number}/{len(batches)}")

    # One retry for companies that were not searched, in later batches, within
    # whatever search allowance this run has left.
    retry_pool = list(unsearched)
    unsearched.clear()
    remaining = max(cap - report.searches_made, 0)
    if retry_pool and not stopped_early:
        retry_now, retry_skipped = retry_pool[:remaining], retry_pool[remaining:]
        unsearched.extend(retry_skipped)
        retry_batches = split_batches(retry_now)
        for number, batch in enumerate(retry_batches, start=1):
            if stopped_early:
                unsearched.extend(batch)
                continue
            run(batch, f"retry {number}/{len(retry_batches)}")
    else:
        unsearched.extend(retry_pool)

    report.unsearched = [c["name"] for c in unsearched]
    if report.unsearched:
        report.failed_reason = (
            f"discovery made {report.searches_made} search(es) for {report.planned_companies} "
            f"compan{'y' if report.planned_companies == 1 else 'ies'}; {len(report.unsearched)} still unsearched "
            f"after the retry ({', '.join(report.unsearched[:10])}{', ...' if len(report.unsearched) > 10 else ''})"
            + ("; stopped after two calls in a row made no searches" if stopped_early else "")
        )
    return report
