from job.agent.classification import build_prompt, run_classification
from job.agent.client import ClaudeStepResult

TITLES_DOC = {"titles": [{"title": "PM Intern", "active": True}], "exclude_words": ["marketing"]}
LOCATIONS = {"countries": ["US", "JP"], "include_remote": False}


def test_build_prompt_includes_posting_details():
    postings = [{"id": 1, "company_name": "Acme", "title": "商品企画インターン", "location_text": "Tokyo"}]
    prompt = build_prompt(postings, TITLES_DOC, LOCATIONS)
    assert "id=1" in prompt
    assert "Acme" in prompt
    assert "Tokyo" in prompt
    assert "marketing" in prompt


def test_run_classification_empty_list_skips_claude():
    calls = []
    decisions, result = run_classification([], TITLES_DOC, LOCATIONS, model="haiku", run_claude=lambda *a, **k: calls.append(1))
    assert decisions == []
    assert result is None
    assert calls == []


def test_run_classification_parses_mocked_response():
    def fake_run_claude(step, prompt, *, model):
        assert step == "classification"
        assert model == "haiku"
        return ClaudeStepResult(
            step=step, model=model,
            text='{"decisions": [{"id": 5, "decision": "match", "matched_title": "PM Intern", "title_en": "Product Intern"}]}',
        )

    postings = [{"id": 5, "company_name": "Acme", "title": "foo"}]
    decisions, result = run_classification(postings, TITLES_DOC, LOCATIONS, model="haiku", run_claude=fake_run_claude)
    assert decisions[0]["id"] == 5
    assert decisions[0]["matched_title"] == "PM Intern"
    assert result.model == "haiku"
