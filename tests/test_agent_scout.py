
import pytest

from job.agent.client import ClaudeStepResult
from job.agent.schemas import ClaudeOutputError
from job.agent.scout import (
    new_source_row,
    order_companies_for_scout,
    run_scout,
    split_companies_by_coverage,
)


def test_run_scout_empty_companies_skips_claude():
    calls = []
    found, result = run_scout([], model="sonnet", run_claude=lambda *a, **k: calls.append(1))
    assert found == []
    assert result is None
    assert calls == []


def test_run_scout_parses_mocked_response():
    def fake_run_claude(step, prompt, *, model):
        assert step == "scout"
        return ClaudeStepResult(
            step=step, model=model, web_searches=3,
            text='{"sources": [{"company_name": "Acme", "name": "Acme careers", "url": "https://acme.example/careers", "kind": "Company career sites", "covers": "Acme roles"}]}',
        )

    found, result = run_scout([{"name": "Acme"}], model="sonnet", run_claude=fake_run_claude)
    assert found[0]["name"] == "Acme careers"
    assert result.step == "scout"


def test_run_scout_keeps_valid_entries_when_some_fail_validation():
    def fake_run_claude(step, prompt, *, model):
        return ClaudeStepResult(
            step=step, model=model, web_searches=3,
            text=(
                '{"sources": ['
                '{"company_name": "Acme", "name": "Acme careers", "url": "https://acme.example/careers"}, '
                '{"url": "https://missing-name.example"}'
                ']}'
            ),
        )

    found, result = run_scout([{"name": "Acme"}], model="sonnet", run_claude=fake_run_claude)
    assert [f["name"] for f in found] == ["Acme careers"]
    assert result.step == "scout"


def test_run_scout_raises_loudly_when_every_result_fails_validation():
    def fake_run_claude(step, prompt, *, model):
        return ClaudeStepResult(
            step=step, model=model, web_searches=3, text='{"sources": [{"url": "https://missing-name.example"}]}',
        )

    with pytest.raises(ClaudeOutputError) as exc_info:
        run_scout([{"name": "Acme"}], model="sonnet", run_claude=fake_run_claude)
    # The call still spent real tokens/searches -- that usage must not be
    # lost just because the reply couldn't be used (see `pipeline.guarded`).
    assert exc_info.value.result is not None
    assert exc_info.value.result.step == "scout"


def test_run_scout_raises_loudly_when_it_made_no_web_searches():
    # The scout has WebSearch/WebFetch enabled and real companies to look for -- zero
    # searches means Claude answered from memory, so any source it reports is
    # invented, not found (issue #20). That must fail loudly, not look like a clean
    # "found a source" run.
    def fake_run_claude(step, prompt, *, model):
        return ClaudeStepResult(
            step=step, model=model, web_searches=0,
            text='{"sources": [{"company_name": "Acme", "name": "Acme careers", "url": "https://acme.example/careers"}]}',
        )

    with pytest.raises(ClaudeOutputError) as exc_info:
        run_scout([{"name": "Acme"}], model="sonnet", run_claude=fake_run_claude)
    assert exc_info.value.result is not None
    assert exc_info.value.result.step == "scout"


def test_new_source_row_starts_as_trial():
    row = new_source_row({"name": "x", "url": "https://x", "kind": "Job boards", "covers": ""}, now_iso="2026-09-20T08:00:00")
    assert row["status"] == "trial"
    assert row["found_count"] == 0
    assert row["discovered_by"] == "claude-scout"



def test_order_companies_for_scout_puts_never_scouted_before_already_tried():
    companies = [
        {"name": "Tried", "rank": 1, "last_scouted_at": "2026-09-19T08:00:00"},
        {"name": "Never", "rank": 50, "last_scouted_at": None},
    ]
    ordered = order_companies_for_scout(companies)
    assert [c["name"] for c in ordered] == ["Never", "Tried"]


def test_order_companies_for_scout_breaks_ties_by_rank():
    companies = [
        {"name": "Low priority", "rank": 90, "last_scouted_at": None},
        {"name": "High priority", "rank": 2, "last_scouted_at": None},
    ]
    ordered = order_companies_for_scout(companies)
    assert [c["name"] for c in ordered] == ["High priority", "Low priority"]


def test_order_companies_for_scout_cycles_back_to_the_oldest_tried_first():
    companies = [
        {"name": "Tried first", "rank": 1, "last_scouted_at": "2026-09-18T08:00:00"},
        {"name": "Tried second", "rank": 2, "last_scouted_at": "2026-09-19T08:00:00"},
    ]
    ordered = order_companies_for_scout(companies)
    assert [c["name"] for c in ordered] == ["Tried first", "Tried second"]


def test_order_companies_for_scout_respects_the_limit():
    companies = [{"name": f"Company {i}", "rank": i, "last_scouted_at": None} for i in range(5)]
    ordered = order_companies_for_scout(companies, limit=2)
    assert len(ordered) == 2
    assert [c["name"] for c in ordered] == ["Company 0", "Company 1"]


def test_split_companies_by_coverage_skips_only_covered_ids():
    companies = [{"id": 1, "name": "Covered"}, {"id": 2, "name": "Uncovered"}]
    needs_scout, covered = split_companies_by_coverage(companies, covered_ids={1})
    assert [c["name"] for c in needs_scout] == ["Uncovered"]
    assert covered == 1


def test_split_companies_by_coverage_ignores_a_recent_last_source_found_at():
    # A source the scout found recently is not coverage (issue #63): only a working feed
    # or a careers page that loaded counts, and those arrive as `covered_ids`.
    companies = [{"id": 1, "name": "Found Before", "last_source_found_at": "2026-09-24"}]
    needs_scout, covered = split_companies_by_coverage(companies, covered_ids=set())
    assert [c["name"] for c in needs_scout] == ["Found Before"]
    assert covered == 0
