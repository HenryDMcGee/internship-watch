"""Discovery: Claude searches the open web for companies with no working
feed, starting with the watch list and today's search plan.
"""
from __future__ import annotations

from job.agent.client import ClaudeStepResult, resolve_model, run_claude_step
from job.agent.schemas import parse_and_validate, validate_discovery_output

DEFAULT_SEARCH_LIMIT_PER_RUN = 35
DEFAULT_SEARCH_LIMIT_PER_COMPANY = 5


def order_companies(companies: list[dict], watchlist: list[str]) -> list[dict]:
    """Watch-list companies first, in watch-list order, then the rest as given."""
    watch_normalized = [w.strip().lower() for w in watchlist]

    def sort_key(company: dict) -> tuple[int, str]:
        name = (company.get("name") or "").strip().lower()
        rank = watch_normalized.index(name) if name in watch_normalized else len(watch_normalized)
        return (rank, name)

    return sorted(companies, key=sort_key)


def build_prompt(
    companies: list[dict], titles_doc: dict, locations_setting: dict, search_plan: dict | None,
    *, search_limit_per_run: int, search_limit_per_company: int,
) -> str:
    titles = [t["title"] for t in titles_doc.get("titles", []) if t.get("active")]
    company_names = [c["name"] for c in companies]
    countries = locations_setting.get("countries") or ["US"]
    plan = search_plan or {}
    query_templates = plan.get("query_templates") or []
    plan_notes = plan.get("notes")

    lines = [
        "You are helping find open MBA-level product internships for Summer 2027.",
        f"Search for postings at these companies, which have no working job feed on file: "
        f"{', '.join(company_names)}.",
        f"Look only for these title types, or close variants: {', '.join(titles)}.",
        f"Locations of interest: {', '.join(countries)}"
        + (" (remote included)." if locations_setting.get("include_remote") else " (remote not included)."),
        f"Use at most {search_limit_per_run} searches in total, and at most {search_limit_per_company} per company.",
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
        "Reply with only JSON, no other text, in this exact shape: "
        '{"results": [{"company_name": string, "title": string, "url": string, '
        '"location_text": string, "posted_date": string or null}]}. '
        'Use "" for location_text if unknown, and an empty "results" list if you found nothing.'
    )
    return "\n".join(lines)


def run_discovery(
    companies: list[dict], titles_doc: dict, locations_setting: dict, search_plan: dict | None,
    *, model: str,
    search_limit_per_run: int = DEFAULT_SEARCH_LIMIT_PER_RUN,
    search_limit_per_company: int = DEFAULT_SEARCH_LIMIT_PER_COMPANY,
    run_claude=run_claude_step,
) -> tuple[list[dict], ClaudeStepResult | None]:
    if not companies:
        return [], None
    prompt = build_prompt(
        companies, titles_doc, locations_setting, search_plan,
        search_limit_per_run=search_limit_per_run, search_limit_per_company=search_limit_per_company,
    )
    result = run_claude("discovery", prompt, model=resolve_model(model))
    payload = parse_and_validate(result.text, validate_discovery_output)
    return payload["results"], result
