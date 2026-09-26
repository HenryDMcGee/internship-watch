"""Repairs a flagged careers page (issue #55): for each company whose
careers page has failed 3 checks in a row
(`companies.careers_page_flagged`, set in `job/run.py`), asks Claude to find
a better page -- preferring an internship-filtered listing page, such as a
careers search for "intern", over the company's general careers home page.

Each replacement found becomes a `careers_repair` proposal for Henry to
approve or reject (`job/agent/pipeline.py`'s `_store_careers_repair_proposals`
and `api/src/main.py`'s proposal appliers). A URL Henry already rejected for
a company is never proposed again -- `companies.careers_url_rejected` is
passed back into the prompt so Claude doesn't have to be told twice.
"""
from __future__ import annotations

import json

from job.agent.client import ClaudeStepResult, resolve_model, run_claude_step
from job.agent.schemas import (
    ClaudeOutputError,
    parse_and_validate,
    split_repair_replacements,
    validate_repair_document,
)

DEFAULT_SEARCH_LIMIT_PER_RUN = 20


def _rejected_urls(company: dict) -> list[str]:
    rejected = company.get("careers_url_rejected")
    if isinstance(rejected, str):
        try:
            rejected = json.loads(rejected or "[]")
        except ValueError:
            rejected = []
    return rejected or []


def build_prompt(companies: list[dict], *, search_limit_per_run: int) -> str:
    lines = [
        "Each of these companies' careers page has failed to load 3 times in a row. Find a better page for "
        "each -- prefer an internship-filtered listing page, such as a careers search for \"intern\", over "
        "the company's general careers home page.",
        f"Use at most {search_limit_per_run} searches in total.",
    ]
    for c in companies:
        rejected = _rejected_urls(c)
        line = f"- {c['name']} (currently {c.get('careers_url') or 'no URL on file'})"
        if rejected:
            line += f" -- already rejected, do not propose again: {', '.join(rejected)}"
        lines.append(line)
    lines.append(
        "Do not search or scrape LinkedIn, and don't try to get around a site that blocks automated "
        "browsers or shows a CAPTCHA. Reply with only JSON, no other text, in this exact shape: "
        '{"replacements": [{"company_name": string, "url": string, "note": string}]}. '
        "Use an empty \"replacements\" list if you found nothing better for any company."
    )
    return "\n".join(lines)


def run_repair(
    companies: list[dict], *, model: str, search_limit_per_run: int = DEFAULT_SEARCH_LIMIT_PER_RUN,
    run_claude=run_claude_step,
) -> tuple[list[dict], ClaudeStepResult | None]:
    if not companies:
        return [], None
    prompt = build_prompt(companies, search_limit_per_run=search_limit_per_run)
    result = run_claude("careers_repair", prompt, model=resolve_model(model))
    if result.web_searches == 0:
        # Same reasoning as scout/discovery: a claimed replacement page found
        # with zero web searches would be invented, not found (issue #20).
        raise ClaudeOutputError(
            f"careers_repair made 0 web searches while checking {len(companies)} flagged compan"
            f"{'y' if len(companies) == 1 else 'ies'} -- results would be unverifiable",
            result=result,
        )
    payload = parse_and_validate(result.text, validate_repair_document, result=result)
    raw = payload.get("replacements") or []
    replacements, problems = split_repair_replacements(payload)
    print(
        f"careers_repair: Claude returned {len(raw)} replacement(s), {len(replacements)} passed validation"
        + (f", {len(problems)} rejected ({'; '.join(problems)})" if problems else "")
    )
    if raw and not replacements:
        raise ClaudeOutputError("all replacements failed validation: " + "; ".join(problems), result=result)
    return replacements, result
