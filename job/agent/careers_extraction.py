"""Careers-page extraction: Haiku turns a changed careers page's text into
structured listings (title, location, link, posted date, job id), so
`job/run.py` can fingerprint and store each one the same way a feed or
discovery posting is (issue #53, phase 6 part 1).

This step only pulls what's actually on the page -- deciding whether a
listing matches an active title is still `job/agent/classification.py`'s
job, run later over every unclassified posting regardless of where it came
from (feed, discovery, or a careers page).
"""
from __future__ import annotations

from job.agent.client import ClaudeStepResult, resolve_model, run_claude_step
from job.agent.schemas import (
    ClaudeOutputError,
    parse_and_validate,
    split_careers_listings,
    validate_careers_extraction_document,
)

# The page text is truncated to a sensible size before it's sent to Haiku
# (issue: "capped at a sensible size") -- long enough to cover a careers
# page's full listing table, short enough to keep the call cheap.
MAX_PAGE_CHARS = 20000


def build_prompt(company_name: str, page_text: str, *, max_chars: int = MAX_PAGE_CHARS) -> str:
    truncated = (page_text or "")[:max_chars]
    return "\n".join([
        f"Extract every job listing on this careers page for {company_name}.",
        "For each distinct listing, give its title, location, link (a URL -- absolute if the page "
        "gives one, otherwise exactly what the page shows), posted date (ISO 8601 if shown, otherwise "
        "null), and job ID if the page shows one (otherwise null).",
        "Only extract listings actually shown on the page. Do not invent any, and do not include "
        "navigation links, filters, or anything that isn't an individual job listing.",
        "Page content:",
        truncated,
        "Reply with only JSON, no other text, in this exact shape: "
        '{"listings": [{"title": string, "location": string, "link": string, '
        '"posted_date": string or null, "job_id": string or null}]}. '
        'Use an empty "listings" list if the page has no job listings.',
    ])


def run_extraction(
    company_name: str, page_text: str, *, model: str, run_claude=run_claude_step, max_chars: int = MAX_PAGE_CHARS,
) -> tuple[list[dict], ClaudeStepResult | None]:
    prompt = build_prompt(company_name, page_text, max_chars=max_chars)
    result = run_claude("careers_extraction", prompt, model=resolve_model(model))
    payload = parse_and_validate(result.text, validate_careers_extraction_document, result=result)
    raw_listings = payload.get("listings") or []
    listings, problems = split_careers_listings(payload)
    print(
        f"careers_extraction: Claude returned {len(raw_listings)} listing(s), {len(listings)} passed validation"
        + (f", {len(problems)} dropped ({'; '.join(problems)})" if problems else "")
    )
    return listings, result
