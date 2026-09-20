"""Classification: Claude decides whether a posting the plain title/alias
rules (`job/matching.py`) couldn't confidently place -- non-English titles
especially -- is a real match, and fills in `matched_title` and, for
non-English postings, `title_en`.
"""
from __future__ import annotations

from job.agent.client import ClaudeStepResult, resolve_model, run_claude_step
from job.agent.schemas import parse_and_validate, validate_classification_output


def _location_text(posting: dict) -> str:
    if posting.get("location_text"):
        return posting["location_text"]
    locations = posting.get("locations")
    if isinstance(locations, list):
        return ", ".join(locations)
    return ""


def build_prompt(postings: list[dict], titles_doc: dict, locations_setting: dict) -> str:
    titles = [t["title"] for t in titles_doc.get("titles", []) if t.get("active")]
    exclude_words = titles_doc.get("exclude_words", [])
    countries = locations_setting.get("countries") or ["US"]

    items = "\n".join(
        f'- id={p["id"]}, company="{p.get("company_name", "")}", title="{p.get("title", "")}", '
        f'location="{_location_text(p)}"'
        for p in postings
    )

    return "\n".join([
        "Classify each internship posting below. The title may be in any language.",
        f"An active title matches if its real role is one of these (or a clear equivalent, in any language): "
        f"{', '.join(titles)}.",
        f"Reject if the title mentions any of these words: {', '.join(exclude_words) or '(none)'}.",
        "Only Summer 2027 MBA-level internships count. Reject full-time roles, other class years "
        "(e.g. 'MBA Class of 2027' means full-time hires graduating in 2027, not summer 2027 interns), "
        "and other summer cycles.",
        f"Locations of interest: {', '.join(countries)}"
        + (" (remote included)." if locations_setting.get("include_remote") else " (remote not included)."),
        "Postings:",
        items,
        "Reply with only JSON, no other text, in this exact shape: "
        '{"decisions": [{"id": integer, "decision": "match" or "reject", "matched_title": string, '
        '"title_en": string or null, "location_match": "match", "outside", or "unclear", "reason": string}]}. '
        '"matched_title" must be one of the active titles above, and is required when decision is "match". '
        '"title_en" is the English translation of the posting title, only when the original title is not '
        "already English; otherwise null.",
    ])


def run_classification(
    postings: list[dict], titles_doc: dict, locations_setting: dict, *, model: str, run_claude=run_claude_step,
) -> tuple[list[dict], ClaudeStepResult | None]:
    if not postings:
        return [], None
    prompt = build_prompt(postings, titles_doc, locations_setting)
    result = run_claude("classification", prompt, model=resolve_model(model))
    payload = parse_and_validate(result.text, validate_classification_output)
    return payload["decisions"], result
