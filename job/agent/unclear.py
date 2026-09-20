"""Reads a job page's visible text when the phase 2 checkers (structured
page data, then plain text signals) couldn't decide whether a posting is
still open. Third-and-a-half in the verification order: only reached from
inside the browser check (`job/verify/browser.py`), and only when its own
text-signal heuristics come back "unclear".
"""
from __future__ import annotations

from job.agent.client import ClaudeStepResult, resolve_model, run_claude_step
from job.agent.schemas import parse_and_validate, validate_unclear_output
from job.verify.text_signals import VerifyDecision

MAX_PAGE_TEXT_CHARS = 6000


def build_prompt(page_text: str) -> str:
    trimmed = (page_text or "")[:MAX_PAGE_TEXT_CHARS]
    return "\n".join([
        "This is the visible text of a company careers page for one job posting.",
        "Decide whether the posting is still open for applications, closed, or unclear from this text alone. "
        "Do not guess; say unclear if the text doesn't say either way.",
        "Text:",
        trimmed,
        'Reply with only JSON, no other text, in this exact shape: '
        '{"decision": "open", "closed", or "unclear", "note": string}.',
    ])


def read_unclear_page(
    page_text: str, *, model: str, run_claude=run_claude_step,
) -> tuple[VerifyDecision, ClaudeStepResult]:
    prompt = build_prompt(page_text)
    result = run_claude("unclear_page", prompt, model=resolve_model(model))
    payload = parse_and_validate(result.text, validate_unclear_output, result=result)
    return VerifyDecision(decision=payload["decision"], note=payload.get("note")), result
