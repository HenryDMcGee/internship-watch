"""Runs Claude Code headless (`claude -p`) for one agent step.

Authenticated with `CLAUDE_CODE_OAUTH_TOKEN` by default. Switching to
`ANTHROPIC_API_KEY` is a single setting: the CLI itself picks up whichever
one is set in the environment, so this module never reads either directly
and never needs to change for the switch. Each step gets only the tools it
needs -- web search and web fetch for discovery and the scout, no tools at
all (so no shell and no file edits) for everything else -- and always
returns a fixed JSON output format that the caller validates (see
`schemas.py`) before using it.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Callable

CLAUDE_BIN = "claude"
DEFAULT_TIMEOUT_SECONDS = 300

# Every real prompt for these steps is built from real data -- company names, titles,
# postings -- and is comfortably over 700 characters even for a single item (measured
# directly from each step's `build_prompt`). A prompt under this floor means the caller
# lost its real content before the call ever went out, which is exactly how issue #20's
# steps ended up answering from memory with no searches. `unclear_page` is deliberately
# excluded: its content is a scraped page's visible text, which can legitimately be very
# short or empty -- that sparseness is often *why* the plain-text heuristics called it
# unclear in the first place (see `job/agent/unclear.py`).
MIN_PROMPT_CHARS = 400
STEPS_WITH_MIN_PROMPT_CHARS = frozenset({"discovery", "scout", "classification", "improvement"})

# Steps that call the web need the model to actually search or fetch pages;
# every other step is pure text-in, JSON-out.
STEP_TOOLS: dict[str, tuple[str, ...]] = {
    "discovery": ("WebSearch", "WebFetch"),
    "scout": ("WebSearch", "WebFetch"),
    "classification": (),
    "unclear_page": (),
    "improvement": (),
}

# Discovery and the scout can each spend a whole run's search budget (up to
# `search_limit_per_run` searches, each with its own page fetches), which
# routinely takes much longer than a single text-in/JSON-out call. The old
# one-size-fits-all 300s timeout meant those two steps reliably timed out
# before finishing while the tool-free steps never came close -- silently,
# since a timeout is caught the same as any other Claude error (see
# `pipeline.guarded`). Steps not listed here fall back to
# `DEFAULT_TIMEOUT_SECONDS`.
STEP_TIMEOUT_SECONDS: dict[str, int] = {
    "discovery": 900,
    "scout": 900,
}

# Substrings that mark a CLI failure as an account/rate limit rather than a
# bug, so the caller can raise a usage-limit alert instead of a plain error.
_LIMIT_PHRASES = ("usage limit", "rate limit", "quota", "usage cap")


class ClaudeCallError(RuntimeError):
    pass


class ClaudeLimitError(ClaudeCallError):
    """The CLI call failed because of an account or rate limit, not a bug."""


@dataclass
class ClaudeStepResult:
    step: str
    model: str
    text: str  # the model's final answer -- expected to be JSON, see schemas.py
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    web_searches: int = 0


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _usage_from_payload(payload: dict) -> tuple[int, int, int, int, int]:
    """Pulls token/search counts out of the CLI's final JSON reply.

    Issue #13: a real run logged `input_tokens` of 10-14 and `web_searches` of
    0 for calls that (per their own multi-thousand-character prompts and
    working search behavior) plainly cost more than that and did search.
    The reply's top-level `usage` is the *last* underlying API turn's own
    usage -- with prompt caching and a headless run's tool-calling turns
    (search, then read results, then answer), that's often a small delta,
    not what the whole `claude -p` call actually spent. `modelUsage`, keyed
    by model id, is the CLI's own running total across every turn of the
    call, including `webSearchRequests` -- that's the one budgets.py and the
    `usage` table need. Fall back to the top-level `usage` object only for a
    CLI version/reply that doesn't include `modelUsage`.
    """
    model_usage = payload.get("modelUsage") or {}
    if model_usage:
        totals = {
            "inputTokens": 0, "outputTokens": 0,
            "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
            "webSearchRequests": 0,
        }
        for stats in model_usage.values():
            for key in totals:
                totals[key] += int((stats or {}).get(key) or 0)
        return (
            totals["inputTokens"], totals["outputTokens"],
            totals["cacheReadInputTokens"], totals["cacheCreationInputTokens"],
            totals["webSearchRequests"],
        )
    usage = payload.get("usage") or {}
    server_tool_use = usage.get("server_tool_use") or {}
    return (
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
        int(usage.get("cache_read_input_tokens") or 0),
        int(usage.get("cache_creation_input_tokens") or 0),
        int(server_tool_use.get("web_search_requests") or 0),
    )


def _raise_for_message(step: str, message: str) -> None:
    if any(phrase in message.lower() for phrase in _LIMIT_PHRASES):
        raise ClaudeLimitError(message)
    raise ClaudeCallError(message)


def run_claude_step(
    step: str,
    prompt: str,
    *,
    model: str,
    system_prompt: str | None = None,
    timeout: int | None = None,
    runner: Runner = subprocess.run,
) -> ClaudeStepResult:
    if step not in STEP_TOOLS:
        raise ValueError(f"unknown agent step {step!r}")
    if step in STEPS_WITH_MIN_PROMPT_CHARS and len(prompt) < MIN_PROMPT_CHARS:
        raise ClaudeCallError(
            f"{step} step's prompt is only {len(prompt)} character(s), under the {MIN_PROMPT_CHARS}-character "
            "minimum for a real prompt -- refusing to call Claude with what looks like an empty or lost prompt"
        )
    if timeout is None:
        timeout = STEP_TIMEOUT_SECONDS.get(step, DEFAULT_TIMEOUT_SECONDS)

    # The prompt goes on stdin, not as a positional argument. `claude`'s own usage line
    # is `claude [options] [command] [prompt]` -- two positional slots -- and a single
    # non-flag token on the command line risks binding to `[command]` instead of
    # `[prompt]`, leaving the real prompt empty and the CLI falling back to (empty)
    # stdin. `-p`'s own help text documents stdin input as the alternative ("useful for
    # pipes"), which sidesteps that ambiguity entirely.
    args = [
        CLAUDE_BIN, "-p", "--output-format", "json", "--model", model,
        # Runs unattended in GitHub Actions: anything that would need a
        # permission decision must be auto-denied, never hang waiting for one.
        "--permission-prompts", "none",
    ]
    tools = STEP_TOOLS[step]
    # For a step that needs the web, only `--allowedTools` is passed (no
    # `--tools`): `--allowedTools` pre-approves WebSearch/WebFetch so they run
    # without a permission decision -- confirmed necessary by testing the CLI
    # directly, since without it a headless call gets "permission for the
    # WebSearch tool hasn't been granted in this session" instead of
    # searching (issue #20). `--tools` alongside it was issue #13: it isn't
    # needed (per `claude --help`, `--permission-prompts none` only
    # auto-denies whatever isn't already pre-approved, so nothing outside
    # `--allowedTools` runs anyway) and adding it changed CLI behavior enough
    # that discovery/scout usage came back wrong. `claude --help` confirms
    # this matches a terminal call of `claude -p "..." --allowedTools
    # "WebSearch,WebFetch"` with neither `--tools` nor `--permission-prompts`.
    # For a tool-free step, `--tools ""` disables every tool outright
    # (CLAUDE.md: "no shell or file-editing tools") -- there's nothing to
    # pre-approve, so `--allowedTools` is skipped instead.
    tools_value = ",".join(tools)
    if tools:
        args += ["--allowedTools", tools_value]
    else:
        args += ["--tools", ""]
    if system_prompt:
        args += ["--append-system-prompt", system_prompt]

    print(
        f"{step}: prompt is {len(prompt)} character(s), starting `{prompt[:200]!r}`; "
        f"running `{shlex.join(args)}` (prompt sent on stdin)"
    )

    try:
        completed = runner(args, input=prompt, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCallError(f"{step} step timed out after {timeout}s") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        _raise_for_message(step, f"{step} step failed (exit {completed.returncode}): {stderr[:500]}")

    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        raise ClaudeCallError(f"{step} step returned non-JSON output: {exc}") from exc

    if payload.get("is_error"):
        _raise_for_message(step, f"{step} step reported an error: {str(payload.get('result', ''))[:500]}")

    # Full, unfiltered usage objects straight from the CLI reply -- so a real
    # run's logs show exactly what's actually there instead of us guessing
    # again (issue #13: parsed `usage` fields disagreed with the run's real
    # cost, and this is the only way to check the reply itself next time).
    print(f"{step}: usage={payload.get('usage')!r}")
    print(f"{step}: modelUsage={payload.get('modelUsage')!r}")

    input_tokens, output_tokens, cache_read, cache_write, web_searches = _usage_from_payload(payload)
    return ClaudeStepResult(
        step=step, model=model, text=payload.get("result", ""),
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_tokens=cache_read, cache_write_tokens=cache_write,
        web_searches=web_searches,
    )


def is_configured() -> bool:
    """Whether the environment has credentials for the `claude` CLI to use --
    `CLAUDE_CODE_OAUTH_TOKEN` by default, or `ANTHROPIC_API_KEY` (see the
    module docstring). The daily job checks this before attempting any
    Claude step, so a run without either secret set skips them cleanly
    instead of every step failing one at a time.
    """
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"))


def resolve_model(name: str) -> str:
    """`budget_settings.model_<step>` stores 'sonnet' or 'haiku'; the CLI
    resolves either alias to its current model on its own, so this only
    guards against a typo reaching the subprocess call.
    """
    normalized = (name or "").strip().lower()
    if normalized not in ("sonnet", "haiku"):
        raise ValueError(f"model must be 'sonnet' or 'haiku', got {name!r}")
    return normalized
