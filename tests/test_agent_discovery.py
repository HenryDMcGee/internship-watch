from job.agent.client import ClaudeStepResult
from job.agent.discovery import build_prompt, order_companies, run_discovery
from job.agent.schemas import ClaudeOutputError

TITLES_DOC = {"titles": [{"title": "PM Intern", "active": True}], "exclude_words": []}
LOCATIONS = {"countries": ["US"], "include_remote": False}


def test_order_companies_puts_watchlist_first_in_watchlist_order():
    companies = [{"name": "Zeta"}, {"name": "Acme"}, {"name": "Beta"}]
    ordered = order_companies(companies, watchlist=["Beta", "Zeta"])
    assert [c["name"] for c in ordered] == ["Beta", "Zeta", "Acme"]


def test_order_companies_no_watchlist_keeps_alphabetical_by_name():
    companies = [{"name": "Zeta"}, {"name": "Acme"}]
    ordered = order_companies(companies, watchlist=[])
    assert [c["name"] for c in ordered] == ["Acme", "Zeta"]


def test_build_prompt_includes_company_names_and_limits():
    prompt = build_prompt(
        [{"name": "Acme"}], TITLES_DOC, LOCATIONS, None,
        search_limit_per_run=10, search_limit_per_company=2,
    )
    assert "Acme" in prompt
    assert "PM Intern" in prompt
    assert "at most 10 searches" in prompt
    assert "at most 2 per company" in prompt


def test_build_prompt_includes_search_plan_notes():
    plan = {"query_templates": ["{company} MBA intern"], "notes": "Try LinkedIn alternatives"}
    prompt = build_prompt([{"name": "Acme"}], TITLES_DOC, LOCATIONS, plan, search_limit_per_run=5, search_limit_per_company=1)
    assert "Try LinkedIn alternatives" in prompt
    assert "{company} MBA intern" in prompt


def test_run_discovery_returns_empty_without_calling_claude_when_no_companies():
    calls = []
    results, result = run_discovery(
        [], TITLES_DOC, LOCATIONS, None, model="sonnet",
        run_claude=lambda *a, **k: calls.append(1),
    )
    assert results == []
    assert result is None
    assert calls == []


def test_run_discovery_parses_and_validates_mocked_response():
    def fake_run_claude(step, prompt, *, model):
        assert step == "discovery"
        return ClaudeStepResult(
            step=step, model=model,
            text='{"results": [{"company_name": "Acme", "title": "PM Intern", "url": "https://x", "location_text": "SF"}]}',
        )

    results, result = run_discovery(
        [{"name": "Acme"}], TITLES_DOC, LOCATIONS, None, model="sonnet", run_claude=fake_run_claude,
    )
    assert len(results) == 1
    assert results[0]["company_name"] == "Acme"
    assert result.step == "discovery"


def test_run_discovery_raises_on_invalid_claude_output():
    def fake_run_claude(step, prompt, *, model):
        return ClaudeStepResult(step=step, model=model, text='{"nope": true}')

    try:
        run_discovery([{"name": "Acme"}], TITLES_DOC, LOCATIONS, None, model="sonnet", run_claude=fake_run_claude)
        assert False, "expected ClaudeOutputError"
    except ClaudeOutputError:
        pass
