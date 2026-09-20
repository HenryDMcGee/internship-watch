"""Daily improvement: writes tomorrow's search plan (small wording/order
changes apply automatically), proposes company and source changes for
Henry's approval, and suggests title changes for `titles_pr.py` to turn
into a pull request.
"""
from __future__ import annotations

from datetime import date, timedelta

from job.agent.client import ClaudeStepResult, resolve_model, run_claude_step
from job.agent.schemas import parse_and_validate, validate_improvement_output


def build_prompt(run_summary: dict, recent_run_stats: list[dict], titles_doc: dict) -> str:
    titles = [t["title"] for t in titles_doc.get("titles", []) if t.get("active")]
    stats_lines = "\n".join(
        f'- {s.get("query", "")}: found {s.get("found", 0)}, useful {s.get("useful", 0)}'
        for s in recent_run_stats
    ) or "(none)"

    return "\n".join([
        "You help run a daily search for Summer 2027 MBA product internships.",
        f"Today's run: {run_summary.get('summary', '') or '(no summary)'}",
        "Today's search results by query (found = postings seen, useful = postings that matched a title):",
        stats_lines,
        f"Active titles: {', '.join(titles)}.",
        "Suggest small wording or ordering changes for tomorrow's search plan. Only propose adding or "
        "removing a company or a source if you have a good reason -- those need Henry's approval, so put "
        "them in 'proposals' rather than applying them yourself. Only suggest a title change if you saw a "
        "posting that looks like a real match but doesn't fit any active title or alias.",
        "Reply with only JSON, no other text, in this exact shape: "
        '{"search_plan": {"query_templates": [string], "notes": string}, '
        '"proposals": [{"kind": "company", "source", or "source_retire", "payload": object, "reason": string}], '
        '"title_suggestions": [{"action": "add_title", "add_alias", or "deactivate", "title": string, '
        '"group": string, "aliases": [string], "alias": string, "reason": string}]}. '
        "Use empty lists for anything you have nothing to suggest.",
    ])


def tomorrow_iso(today: date) -> str:
    return (today + timedelta(days=1)).isoformat()


def run_improvement(
    run_summary: dict, recent_run_stats: list[dict], titles_doc: dict, *, model: str, run_claude=run_claude_step,
) -> tuple[dict, ClaudeStepResult]:
    prompt = build_prompt(run_summary, recent_run_stats, titles_doc)
    result = run_claude("improvement", prompt, model=resolve_model(model))
    payload = parse_and_validate(result.text, validate_improvement_output)
    return payload, result
