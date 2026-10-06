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

import re

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

# A page shorter than this is a landing page whose job list never loaded.
MIN_PAGE_CHARS = 2000

_JOB_LINK_RE = re.compile(
    r"https?://\S*(?:/jobs?\b|/job-|/careers?/\S+|/positions?\b|/openings?\b|/requisitions?\b"
    r"|/apply\b|jobid=|job_id=|gh_jid=)",
    re.IGNORECASE,
)
_APPLY_WORDS_RE = re.compile(r"\b(?:intern|interns|internship|internships|mba|apply)\b", re.IGNORECASE)
_RELEVANT_RE = re.compile(
    r"\b(?:intern|interns|internship|internships|mba|product)\b|" + _JOB_LINK_RE.pattern, re.IGNORECASE,
)


def skip_reason(page_text: str) -> str | None:
    """Why a page isn't worth a Claude call, or None if it is: under
    `MIN_PAGE_CHARS` characters, or with no job-like links and none of the
    words "intern", "internship", "MBA", or "apply".
    """
    text = (page_text or "").strip()
    if len(text) < MIN_PAGE_CHARS:
        return f"page too short ({len(text)} characters)"
    if not _JOB_LINK_RE.search(text) and not _APPLY_WORDS_RE.search(text):
        return "no job-like links or words"
    return None


def relevant_text(page_text: str, *, max_chars: int = MAX_PAGE_CHARS) -> str:
    """The page as is if it fits. Otherwise only the lines that contain a job
    link or "intern", "MBA", or "product" (plus one line of context on each
    side), in page order, up to `max_chars` -- rather than the whole page cut
    off at the cap.
    """
    text = page_text or ""
    if len(text) <= max_chars:
        return text
    lines = text.splitlines()
    keep: set[int] = set()
    for i, line in enumerate(lines):
        if _RELEVANT_RE.search(line):
            keep.update(j for j in (i - 1, i, i + 1) if 0 <= j < len(lines))
    if not keep:
        return text[:max_chars]
    out: list[str] = []
    used = 0
    for i in sorted(keep):
        line = lines[i][:max_chars]
        if used + len(line) + 1 > max_chars:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def build_prompt(company_name: str, page_text: str, *, max_chars: int = MAX_PAGE_CHARS) -> str:
    truncated = relevant_text(page_text, max_chars=max_chars)
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
