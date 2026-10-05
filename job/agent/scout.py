"""Source scout: finds careers pages, feeds, and job boards for companies
with no working source first. New sources start as `trial` and become
`active` after a real open posting; sources that keep finding nothing are
`demoted`. Demoting is automatic; actually retiring (removing) a source
needs Henry's approval, so that goes to `proposals` instead (see
`pipeline.py`).
"""
from __future__ import annotations

from job.agent.client import ClaudeStepResult, resolve_model, run_claude_step
from job.agent.schemas import (
    ClaudeOutputError,
    parse_and_validate,
    split_scout_sources,
    validate_scout_document,
)

DEFAULT_SEARCH_LIMIT_PER_RUN = 35
PROMOTE_AFTER_FOUND = 1  # a trial source becomes active after this many real open postings
DEMOTE_AFTER_EMPTY_CHECKS = 3  # demoted after this many checks in a row finding nothing
SCOUT_COMPANIES_PER_RUN = 35  # uncovered companies the scout works through each run


def split_companies_by_coverage(
    companies: list[dict], *, covered_ids: set[int],
) -> tuple[list[dict], int]:
    """Splits `companies` into (needs_scout, covered_count).

    Covered means something is reading the company today: a working feed, or
    a careers page that loaded in the last 7 days (`job/coverage.py`). A row
    in the `sources` table, or a recent `last_source_found_at`, does not
    count -- runs 36 to 42 logged "skipped 182 already-covered companies"
    while 182 companies had no source at all (issue #63).
    """
    needs_scout = [c for c in companies if c.get("id") not in covered_ids]
    return needs_scout, len(companies) - len(needs_scout)


def order_companies_for_scout(companies: list[dict], *, limit: int | None = None) -> list[dict]:
    """Never-scouted companies first, then the least-recently-scouted, with
    rank (lower is more important) as the tie-break within each group.

    One run's search budget can't reach all 100 no-source companies, so
    without this, every run would hand Claude the same leading names and
    the rest would never get a turn. Sorting on `last_scouted_at` first
    means an uncovered company always jumps ahead of an already-tried one;
    once everyone's had a turn, it naturally cycles back starting with the
    highest-priority (lowest rank) company, so coverage keeps moving
    forward across runs instead of stalling on the same batch.
    """

    def sort_key(company: dict) -> tuple[str, float, str]:
        last_scouted = company.get("last_scouted_at") or ""
        rank = company.get("rank")
        rank_key = float(rank) if rank is not None else float("inf")
        name = (company.get("name") or "").strip().lower()
        return (last_scouted, rank_key, name)

    ordered = sorted(companies, key=sort_key)
    return ordered[:limit] if limit is not None else ordered


def build_prompt(companies: list[dict], *, search_limit_per_run: int) -> str:
    company_names = [c["name"] for c in companies]
    return "\n".join([
        "Find careers pages, job feeds, or job board listings for these companies, which have no known "
        f"job source on file: {', '.join(company_names)}.",
        f"Use at most {search_limit_per_run} searches in total.",
        "Prefer each company's own careers page, or a feed platform (Greenhouse, Lever, Ashby, "
        "SmartRecruiters, Workday, Amazon.jobs), over third-party job boards. Where the company's careers "
        "site has a listing page filtered to internships (a search for \"intern\", or an early-careers or "
        "students page), give that page rather than the careers home page. Do not search or scrape "
        "LinkedIn, and don't try to get around a site that blocks automated browsers or shows a CAPTCHA.",
        "Reply with only JSON, no other text, in this exact shape: "
        '{"sources": [{"company_name": string, "name": string, "url": string, '
        '"kind": "Company career sites", "Job boards", "Trackers and aggregators", "School job boards", '
        'or "Added by you", "covers": string}]}. Use an empty "sources" list if you found nothing.',
    ])


def run_scout(
    companies: list[dict], *, model: str, search_limit_per_run: int = DEFAULT_SEARCH_LIMIT_PER_RUN,
    run_claude=run_claude_step,
) -> tuple[list[dict], ClaudeStepResult | None]:
    if not companies:
        return [], None
    prompt = build_prompt(companies, search_limit_per_run=search_limit_per_run)
    result = run_claude("scout", prompt, model=resolve_model(model))
    if result.web_searches == 0:
        # Same reasoning as discovery: WebSearch/WebFetch are enabled and there were
        # real companies to look for, so zero searches means Claude answered from
        # memory -- any source it reports would be invented, not found (issue #20).
        raise ClaudeOutputError(
            f"scout made 0 web searches while checking {len(companies)} compan"
            f"{'y' if len(companies) == 1 else 'ies'} -- results would be unverifiable",
            result=result,
        )
    payload = parse_and_validate(result.text, validate_scout_document, result=result)
    raw_sources = payload.get("sources") or []
    sources, problems = split_scout_sources(payload)
    print(
        f"scout: Claude returned {len(raw_sources)} source(s), {len(sources)} passed validation"
        + (f", {len(problems)} rejected ({'; '.join(problems)})" if problems else "")
    )
    if raw_sources and not sources:
        # Every result this call found failed per-item validation -- that's
        # the same "spent budget, produced nothing usable" failure as a
        # step that can't be parsed at all, and must not look like a clean
        # "found 0 sources" run (issue #20).
        raise ClaudeOutputError("all sources failed validation: " + "; ".join(problems), result=result)
    return sources, result


def new_source_row(found: dict, *, now_iso: str) -> dict:
    """Column values for a `sources` INSERT for one scout-found source."""
    return {
        "name": found["name"],
        "url": found["url"],
        "kind": found.get("kind") or "Job boards",
        "covers": found.get("covers") or "",
        "status": "trial",
        "discovered_by": "claude-scout",
        "discovered_at": now_iso,
        "found_count": 0,
        "last_checked": now_iso,
    }


def next_source_status(status: str, found_count: int, empty_checks_in_a_row: int) -> str:
    """The status a source should move to after this run's check, if any."""
    if found_count >= PROMOTE_AFTER_FOUND:
        return "active"
    if status in ("trial", "active") and empty_checks_in_a_row >= DEMOTE_AFTER_EMPTY_CHECKS:
        return "demoted"
    return status
