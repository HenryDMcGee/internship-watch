import subprocess

import pytest

from job.agent.client import (
    ClaudeCallError,
    ClaudeLimitError,
    STEP_TOOLS,
    is_configured,
    resolve_model,
    run_claude_step,
)


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
    result = run_claude_step("classification", "prompt", model="haiku", runner=runner)
    assert result.text == '{"ok": true}'
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.cache_read_tokens == 10
    assert result.cache_write_tokens == 5
    assert result.web_searches == 2
    assert result.model == "haiku"


def test_run_claude_step_builds_tools_flags_for_web_steps():
    captured = {}

    def runner(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout=_json_result("{}"), stderr="")

    run_claude_step("discovery", "prompt", model="sonnet", runner=runner)
    args = captured["args"]
    assert "--tools" in args
    assert args[args.index("--tools") + 1] == "WebSearch,WebFetch"
    assert "--allowedTools" in args
    assert "--permission-prompts" in args
    assert args[args.index("--permission-prompts") + 1] == "none"


def test_run_claude_step_disables_tools_for_text_only_steps():
    captured = {}

    def runner(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout=_json_result("{}"), stderr="")

    run_claude_step("classification", "prompt", model="haiku", runner=runner)
    args = captured["args"]
    assert args[args.index("--tools") + 1] == ""
    assert "--allowedTools" not in args


def test_run_claude_step_unknown_step_rejected():
    with pytest.raises(ValueError):
        run_claude_step("not_a_step", "prompt", model="sonnet", runner=_fake_runner())


def test_run_claude_step_raises_on_nonzero_exit():
    runner = _fake_runner(returncode=1, stderr="boom")
    with pytest.raises(ClaudeCallError):
        run_claude_step("classification", "prompt", model="haiku", runner=runner)


def test_run_claude_step_raises_limit_error_for_rate_limit_message():
    runner = _fake_runner(returncode=1, stderr="Error: usage limit reached for this account")
    with pytest.raises(ClaudeLimitError):
        run_claude_step("classification", "prompt", model="haiku", runner=runner)


def test_run_claude_step_raises_on_invalid_json_stdout():
    runner = _fake_runner(stdout="not json")
    with pytest.raises(ClaudeCallError):
        run_claude_step("classification", "prompt", model="haiku", runner=runner)


def test_run_claude_step_raises_when_payload_reports_error():
    import json
    runner = _fake_runner(stdout=json.dumps({"result": "boom", "is_error": True, "usage": {}}))
    with pytest.raises(ClaudeCallError):
        run_claude_step("classification", "prompt", model="haiku", runner=runner)


def test_run_claude_step_times_out():
    def runner(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=1)

    with pytest.raises(ClaudeCallError):
        run_claude_step("classification", "prompt", model="haiku", runner=runner, timeout=1)


def test_resolve_model_accepts_sonnet_and_haiku():
    assert resolve_model("sonnet") == "sonnet"
    assert resolve_model("Haiku") == "haiku"


def test_resolve_model_rejects_unknown_name():
    with pytest.raises(ValueError):
        resolve_model("opus")


def test_step_tools_covers_every_step():
    assert set(STEP_TOOLS) == {"discovery", "scout", "classification", "unclear_page", "improvement"}
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
