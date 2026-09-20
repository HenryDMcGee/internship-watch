from job.agent.client import ClaudeStepResult
from job.agent.unclear import build_prompt, read_unclear_page


def test_build_prompt_trims_long_page_text():
    text = "x" * 10000
    prompt = build_prompt(text)
    assert len(prompt) < 10000 + 500


def test_read_unclear_page_returns_verify_decision():
    def fake_run_claude(step, prompt, *, model):
        assert step == "unclear_page"
        return ClaudeStepResult(step=step, model=model, text='{"decision": "closed", "note": "no apply button"}')

    decision, result = read_unclear_page("Some page text", model="haiku", run_claude=fake_run_claude)
    assert decision.decision == "closed"
    assert decision.note == "no apply button"
    assert result.model == "haiku"
