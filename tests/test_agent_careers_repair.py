import pytest

from job.agent.careers_repair import build_prompt, run_repair
from job.agent.client import ClaudeStepResult
from job.agent.schemas import ClaudeOutputError


def test_run_repair_empty_companies_skips_claude():
    calls = []
    found, result = run_repair([], model="sonnet", run_claude=lambda *a, **k: calls.append(1))
    assert found == []
    assert result is None
    assert calls == []


def test_run_repair_parses_mocked_response():
    def fake_run_claude(step, prompt, *, model):
        assert step == "careers_repair"
        return ClaudeStepResult(
            step=step, model=model, web_searches=2,
            text='{"replacements": [{"company_name": "Acme", "url": "https://acme.example/careers?q=intern", '
                 '"note": "internship-filtered search"}]}',
        )

    found, result = run_repair(
        [{"name": "Acme", "careers_url": "https://acme.example/careers"}], model="sonnet", run_claude=fake_run_claude,
    )
    assert found[0]["url"] == "https://acme.example/careers?q=intern"
    assert result.step == "careers_repair"


def test_run_repair_keeps_valid_entries_when_some_fail_validation():
    def fake_run_claude(step, prompt, *, model):
        return ClaudeStepResult(
            step=step, model=model, web_searches=2,
            text=(
                '{"replacements": ['
                '{"company_name": "Acme", "url": "https://acme.example/careers?q=intern"}, '
                '{"url": "https://missing-name.example"}'
                ']}'
            ),
        )

    found, result = run_repair([{"name": "Acme"}], model="sonnet", run_claude=fake_run_claude)
    assert [f["url"] for f in found] == ["https://acme.example/careers?q=intern"]
    assert result.step == "careers_repair"


def test_run_repair_raises_loudly_when_every_result_fails_validation():
    def fake_run_claude(step, prompt, *, model):
        return ClaudeStepResult(
            step=step, model=model, web_searches=2, text='{"replacements": [{"url": "https://missing-name.example"}]}',
        )

    with pytest.raises(ClaudeOutputError) as exc_info:
        run_repair([{"name": "Acme"}], model="sonnet", run_claude=fake_run_claude)
    assert exc_info.value.result is not None
    assert exc_info.value.result.step == "careers_repair"


def test_run_repair_raises_loudly_when_it_made_no_web_searches():
    def fake_run_claude(step, prompt, *, model):
        return ClaudeStepResult(
            step=step, model=model, web_searches=0,
            text='{"replacements": [{"company_name": "Acme", "url": "https://acme.example/careers?q=intern"}]}',
        )

    with pytest.raises(ClaudeOutputError):
        run_repair([{"name": "Acme"}], model="sonnet", run_claude=fake_run_claude)


def test_build_prompt_lists_already_rejected_urls_so_claude_does_not_repeat_them():
    prompt = build_prompt(
        [{"name": "Acme", "careers_url": "https://acme.example/careers",
          "careers_url_rejected": ["https://acme.example/careers/old-search"]}],
        search_limit_per_run=10,
    )
    assert "https://acme.example/careers/old-search" in prompt
    assert "Acme" in prompt


def test_build_prompt_accepts_rejected_urls_stored_as_a_json_string():
    # companies.careers_url_rejected comes back from D1/sqlite as a JSON
    # string, not a parsed list -- the prompt builder must handle either.
    prompt = build_prompt(
        [{"name": "Acme", "careers_url": "https://acme.example/careers",
          "careers_url_rejected": '["https://acme.example/careers/old-search"]'}],
        search_limit_per_run=10,
    )
    assert "https://acme.example/careers/old-search" in prompt
