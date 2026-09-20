from datetime import date

from job.agent.client import ClaudeStepResult
from job.agent.improvement import build_prompt, run_improvement, tomorrow_iso

TITLES_DOC = {"titles": [{"title": "PM Intern", "active": True}], "exclude_words": []}


def test_tomorrow_iso():
    assert tomorrow_iso(date(2026, 9, 20)) == "2026-09-21"


def test_build_prompt_includes_run_summary_and_stats():
    prompt = build_prompt(
        {"summary": "Checked 5 companies"}, [{"query": "Acme PM intern", "found": 2, "useful": 1}], TITLES_DOC,
    )
    assert "Checked 5 companies" in prompt
    assert "Acme PM intern" in prompt
    assert "PM Intern" in prompt


def test_run_improvement_parses_mocked_response():
    def fake_run_claude(step, prompt, *, model):
        assert step == "improvement"
        return ClaudeStepResult(
            step=step, model=model,
            text='{"search_plan": {"query_templates": ["{company} MBA PM intern"], "notes": "n"}, '
                 '"proposals": [], "title_suggestions": []}',
        )

    payload, result = run_improvement({"summary": "x"}, [], TITLES_DOC, model="sonnet", run_claude=fake_run_claude)
    assert payload["search_plan"]["notes"] == "n"
    assert result.model == "sonnet"
