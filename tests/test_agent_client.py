import subprocess

import pytest

from job.agent.client import (
    DEFAULT_TIMEOUT_SECONDS,
    MIN_PROMPT_CHARS,
    STEP_TIMEOUT_SECONDS,
    ClaudeCallError,
    ClaudeLimitError,
    STEP_TOOLS,
    is_configured,
    resolve_model,
    run_claude_step,
)

# Real step prompts are always well over `MIN_PROMPT_CHARS` (see the constant's
# docstring); tests that aren't specifically about prompt length use this instead of a
# short literal so they don't trip the minimum-length guard.
SAMPLE_PROMPT = "Search for real internship postings at these companies: Acme, Globex. " * 10


def _fake_runner(stdout="", stderr="", returncode=0):
    def runner(args, **kwargs):
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)
    return runner


def _json_result(result_text, **usage_overrides):
    usage = {
        "input_tokens": 100, "output_tokens": 50,
        "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5,
        "server_tool_use": {"web_search_requests": 2},
    }
    usage.update(usage_overrides)
    import json
    return json.dumps({"result": result_text, "usage": usage, "is_error": False})


def test_run_claude_step_parses_usage_and_result():
    runner = _fake_runner(stdout=_json_result('{"ok": true}'))
    result = run_claude_step("classification", SAMPLE_PROMPT, model="haiku", runner=runner)
    assert result.text == '{"ok": true}'
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.cache_read_tokens == 10
    assert result.cache_write_tokens == 5
    assert result.web_searches == 2
    assert result.model == "haiku"


def test_run_claude_step_prefers_modelusage_over_the_top_level_usage_object():
    # Issue #13: the top-level `usage` object is only the last underlying API
    # turn's usage -- small and search-free once tool-calling turns and prompt
    # caching are in play, even on a call that spent plenty and searched.
    # `modelUsage` is the CLI's own running total for the whole call and is
    # what's actually right; it must win when both are present.
    import json

    payload = {
        "result": "{}",
        "is_error": False,
        "usage": {
            "input_tokens": 12, "output_tokens": 8,
            "cache_read_input_tokens": 4, "cache_creation_input_tokens": 0,
            "server_tool_use": {"web_search_requests": 0},
        },
        "modelUsage": {
            "claude-sonnet-4-5-20250929": {
                "inputTokens": 2900, "outputTokens": 400,
                "cacheReadInputTokens": 1500, "cacheCreationInputTokens": 200,
                "webSearchRequests": 5,
            }
        },
    }
    runner = _fake_runner(stdout=json.dumps(payload))
    result = run_claude_step("scout", SAMPLE_PROMPT, model="sonnet", runner=runner)
    assert result.input_tokens == 2900
    assert result.output_tokens == 400
    assert result.cache_read_tokens == 1500
    assert result.cache_write_tokens == 200
    assert result.web_searches == 5


def test_run_claude_step_sums_modelusage_across_multiple_models():
    import json

    payload = {
        "result": "{}",
        "is_error": False,
        "modelUsage": {
            "claude-sonnet-4-5-20250929": {
                "inputTokens": 100, "outputTokens": 10,
                "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                "webSearchRequests": 2,
            },
            "claude-haiku-4-5-20251001": {
                "inputTokens": 50, "outputTokens": 5,
                "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                "webSearchRequests": 1,
            },
        },
    }
    runner = _fake_runner(stdout=json.dumps(payload))
    result = run_claude_step("scout", SAMPLE_PROMPT, model="sonnet", runner=runner)
    assert result.input_tokens == 150
    assert result.output_tokens == 15
    assert result.web_searches == 3


def test_run_claude_step_sums_cost_usd_across_multiple_models():
    # Issue #26: run 13's real discovery reply -- Sonnet orchestrates and
    # barely spends anything itself, while a Haiku sub-agent does every
    # search and burns most of the tokens. Estimating cost from the summed
    # tokens under "sonnet" (the step's own model) would price Haiku's chunk
    # at Sonnet's rate; the CLI's own per-model `costUSD` must be summed
    # instead and carried through untouched.
    import json

    payload = {
        "result": "{}",
        "is_error": False,
        "modelUsage": {
            "claude-sonnet-5": {
                "inputTokens": 14, "outputTokens": 17748,
                "cacheReadInputTokens": 349774, "cacheCreationInputTokens": 69252,
                "webSearchRequests": 0, "costUSD": 0.5244708,
            },
            "claude-haiku-4-5": {
                "inputTokens": 344843, "outputTokens": 14469,
                "webSearchRequests": 35, "costUSD": 0.767188,
            },
        },
    }
    runner = _fake_runner(stdout=json.dumps(payload))
    result = run_claude_step("discovery", SAMPLE_PROMPT, model="sonnet", runner=runner)
    assert result.web_searches == 35
    assert result.input_tokens == 344857  # 14 + 344843, hundreds of thousands
    assert result.cost_usd == pytest.approx(0.5244708 + 0.767188)


def test_run_claude_step_cost_usd_is_none_without_modelusage():
    # No `modelUsage` at all (older CLI) means no per-model `costUSD` to sum
    # either -- `usage.py` must fall back to its own estimate, which it can
    # only do if this is `None` rather than `0` (a call really can cost $0).
    runner = _fake_runner(stdout=_json_result('{"ok": true}'))
    result = run_claude_step("classification", SAMPLE_PROMPT, model="haiku", runner=runner)
    assert result.cost_usd is None


def test_run_claude_step_falls_back_to_top_level_usage_without_modelusage():
    # Older CLI versions (or a reply shape without `modelUsage`) must still work.
    runner = _fake_runner(stdout=_json_result('{"ok": true}'))
    result = run_claude_step("classification", SAMPLE_PROMPT, model="haiku", runner=runner)
    assert result.input_tokens == 100
    assert result.web_searches == 2


def test_run_claude_step_logs_the_full_usage_and_modelusage_objects(capsys):
    import json

    payload = {
        "result": "{}",
        "is_error": False,
        "usage": {"input_tokens": 12, "output_tokens": 8},
        "modelUsage": {"claude-sonnet-4-5-20250929": {"inputTokens": 2900, "webSearchRequests": 5}},
    }
    runner = _fake_runner(stdout=json.dumps(payload))
    run_claude_step("scout", SAMPLE_PROMPT, model="sonnet", runner=runner)
    output = capsys.readouterr().out
    assert "usage={'input_tokens': 12, 'output_tokens': 8}" in output
    assert "modelUsage={'claude-sonnet-4-5-20250929'" in output
    assert "2900" in output
    assert "webSearchRequests" in output


def test_run_claude_step_builds_tools_flags_for_web_steps():
    captured = {}

    def runner(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout=_json_result("{}"), stderr="")

    run_claude_step("discovery", SAMPLE_PROMPT, model="sonnet", runner=runner)
    args = captured["args"]
    # Confirmed by testing the CLI directly: a headless call without `--allowedTools`
    # gets "permission for the WebSearch tool hasn't been granted in this session"
    # instead of actually searching (issue #20) -- WebSearch/WebFetch must be
    # pre-approved, comma-separated, for every step that needs the web.
    assert "--allowedTools" in args
    assert args[args.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    # No `--tools` for a web step (issue #13): `--allowedTools` alone is what a
    # terminal call confirmed works, and `--permission-prompts none` already
    # auto-denies anything not pre-approved, so `--tools` was redundant at best.
    assert "--tools" not in args
    assert "--permission-prompts" in args
    assert args[args.index("--permission-prompts") + 1] == "none"


def test_run_claude_step_sends_the_prompt_on_stdin_not_as_an_argument():
    # `claude`'s usage is `claude [options] [command] [prompt]` -- two positional
    # slots. Passing the prompt as a bare command-line argument risks it binding to
    # `[command]` instead of `[prompt]`, leaving the real prompt empty (issue #20).
    # Sending it on stdin (`-p`'s own documented "useful for pipes" behavior) avoids
    # that ambiguity entirely.
    captured = {}

    def runner(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, stdout=_json_result("{}"), stderr="")

    run_claude_step("classification", SAMPLE_PROMPT, model="haiku", runner=runner)
    assert SAMPLE_PROMPT not in captured["args"]
    assert captured["kwargs"]["input"] == SAMPLE_PROMPT


def test_run_claude_step_rejects_a_prompt_that_is_too_short():
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=_json_result("{}"), stderr="")

    with pytest.raises(ClaudeCallError):
        run_claude_step("classification", "way too short", model="haiku", runner=runner)
    assert calls == []  # must not spend a real call on a prompt this short


def test_run_claude_step_accepts_a_prompt_right_at_the_minimum_length():
    prompt = "x" * MIN_PROMPT_CHARS
    runner = _fake_runner(stdout=_json_result("{}"))
    run_claude_step("classification", prompt, model="haiku", runner=runner)  # must not raise


def test_run_claude_step_does_not_enforce_a_minimum_length_for_unclear_page():
    # A scraped page's visible text can legitimately be very short (that sparseness is
    # often why the plain-text heuristics called it unclear in the first place) -- the
    # minimum-length guard would otherwise reject a real, valid call.
    runner = _fake_runner(stdout=_json_result('{"decision": "unclear", "note": null}'))
    run_claude_step("unclear_page", "short page text", model="haiku", runner=runner)  # must not raise


def test_run_claude_step_logs_the_command_and_prompt_length(capsys):
    runner = _fake_runner(stdout=_json_result("{}"))
    run_claude_step("discovery", SAMPLE_PROMPT, model="sonnet", runner=runner)
    output = capsys.readouterr().out
    assert str(len(SAMPLE_PROMPT)) in output
    assert "--allowedTools WebSearch,WebFetch" in output
    assert "--model sonnet" in output
    assert SAMPLE_PROMPT[:200] in output


def test_run_claude_step_disables_tools_for_text_only_steps():
    captured = {}

    def runner(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout=_json_result("{}"), stderr="")

    run_claude_step("classification", SAMPLE_PROMPT, model="haiku", runner=runner)
    args = captured["args"]
    assert args[args.index("--tools") + 1] == ""
    assert "--allowedTools" not in args


def test_run_claude_step_unknown_step_rejected():
    with pytest.raises(ValueError):
        run_claude_step("not_a_step", SAMPLE_PROMPT, model="sonnet", runner=_fake_runner())


def test_run_claude_step_raises_on_nonzero_exit():
    runner = _fake_runner(returncode=1, stderr="boom")
    with pytest.raises(ClaudeCallError):
        run_claude_step("classification", SAMPLE_PROMPT, model="haiku", runner=runner)


def test_run_claude_step_raises_limit_error_for_rate_limit_message():
    runner = _fake_runner(returncode=1, stderr="Error: usage limit reached for this account")
    with pytest.raises(ClaudeLimitError):
        run_claude_step("classification", SAMPLE_PROMPT, model="haiku", runner=runner)


def test_run_claude_step_raises_on_invalid_json_stdout():
    runner = _fake_runner(stdout="not json")
    with pytest.raises(ClaudeCallError):
        run_claude_step("classification", SAMPLE_PROMPT, model="haiku", runner=runner)


def test_run_claude_step_raises_when_payload_reports_error():
    import json
    runner = _fake_runner(stdout=json.dumps({"result": "boom", "is_error": True, "usage": {}}))
    with pytest.raises(ClaudeCallError):
        run_claude_step("classification", SAMPLE_PROMPT, model="haiku", runner=runner)


def test_run_claude_step_times_out():
    def runner(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=1)

    with pytest.raises(ClaudeCallError):
        run_claude_step("classification", SAMPLE_PROMPT, model="haiku", runner=runner, timeout=1)


def test_run_claude_step_gives_web_search_steps_a_longer_timeout_by_default():
    captured = {}

    def runner(args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args, 0, stdout=_json_result("{}"), stderr="")

    run_claude_step("discovery", SAMPLE_PROMPT, model="sonnet", runner=runner)
    assert captured["timeout"] == STEP_TIMEOUT_SECONDS["discovery"]
    assert captured["timeout"] > DEFAULT_TIMEOUT_SECONDS


def test_run_claude_step_uses_default_timeout_for_text_only_steps():
    captured = {}

    def runner(args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args, 0, stdout=_json_result("{}"), stderr="")

    run_claude_step("classification", SAMPLE_PROMPT, model="haiku", runner=runner)
    assert captured["timeout"] == DEFAULT_TIMEOUT_SECONDS


def test_run_claude_step_explicit_timeout_overrides_the_per_step_default():
    captured = {}

    def runner(args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args, 0, stdout=_json_result("{}"), stderr="")

    run_claude_step("discovery", SAMPLE_PROMPT, model="sonnet", runner=runner, timeout=42)
    assert captured["timeout"] == 42


def test_resolve_model_accepts_sonnet_and_haiku():
    assert resolve_model("sonnet") == "sonnet"
    assert resolve_model("Haiku") == "haiku"


def test_resolve_model_rejects_unknown_name():
    with pytest.raises(ValueError):
        resolve_model("opus")


def test_step_tools_covers_every_step():
    assert set(STEP_TOOLS) == {
        "discovery", "scout", "classification", "unclear_page", "improvement", "careers_extraction",
    }
    assert STEP_TOOLS["discovery"] == ("WebSearch", "WebFetch")
    assert STEP_TOOLS["classification"] == ()


def test_is_configured_checks_either_env_var(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert is_configured() is False

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    assert is_configured() is True

    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    assert is_configured() is True
