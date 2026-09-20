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
import subprocess
from dataclasses import dataclass
from typing import Callable

CLAUDE_BIN = "claude"
DEFAULT_TIMEOUT_SECONDS = 300

# Steps that call the web need the model to actually search or fetch pages;
# every other step is pure text-in, JSON-out.
STEP_TOOLS: dict[str, tuple[str, ...]] = {
    "discovery": ("WebSearch", "WebFetch"),
    "scout": ("WebSearch", "WebFetch"),
    "classification": (),
    "unclear_page": (),
    "improvement": (),
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
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    runner: Runner = subprocess.run,
) -> ClaudeStepResult:
    if step not in STEP_TOOLS:
        raise ValueError(f"unknown agent step {step!r}")

    args = [
        CLAUDE_BIN, "-p", prompt, "--output-format", "json", "--model", model,
        # Runs unattended in GitHub Actions: anything that would need a
        # permission decision must be auto-denied, never hang waiting for one.
        "--permission-prompts", "none",
    ]
    tools = STEP_TOOLS[step]
    # `--tools` controls which tools exist at all for this call (CLAUDE.md:
    # "no shell or file-editing tools"); `--allowedTools` pre-approves the
    # ones that do, so they don't need a permission decision either.
    args += ["--tools", ",".join(tools)]
    if tools:
        args += ["--allowedTools", " ".join(tools)]
    if system_prompt:
        args += ["--append-system-prompt", system_prompt]

    try:
        completed = runner(args, capture_output=True, text=True, timeout=timeout, check=False)
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
