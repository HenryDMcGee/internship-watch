"""Source scout: finds careers pages, feeds, and job boards for companies
with no working source first. New sources start as `trial` and become
`active` after a real open posting; sources that keep finding nothing are
`demoted`. Demoting is automatic; actually retiring (removing) a source
needs Henry's approval, so that goes to `proposals` instead (see
`pipeline.py`).
"""
from __future__ import annotations

from job.agent.client import ClaudeStepResult, resolve_model, run_claude_step
from job.agent.schemas import parse_and_validate, validate_scout_output

DEFAULT_SEARCH_LIMIT_PER_RUN = 35
PROMOTE_AFTER_FOUND = 1  # a trial source becomes active after this many real open postings
DEMOTE_AFTER_EMPTY_CHECKS = 3  # demoted after this many checks in a row finding nothing


def build_prompt(companies: list[dict], *, search_limit_per_run: int) -> str:
    company_names = [c["name"] for c in companies]
    return "\n".join([
        "Find careers pages, job feeds, or job board listings for these companies, which have no known "
        f"job source on file: {', '.join(company_names)}.",
        f"Use at most {search_limit_per_run} searches in total.",
        "Prefer each company's own careers page, or a feed platform (Greenhouse, Lever, Ashby, "
        "SmartRecruiters, Workday, Amazon.jobs), over third-party job boards. Do not search or scrape "
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
    payload = parse_and_validate(result.text, validate_scout_output)
    return payload["sources"], result


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
