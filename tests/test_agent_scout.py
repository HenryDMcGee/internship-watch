from job.agent.client import ClaudeStepResult
from job.agent.scout import (
    DEMOTE_AFTER_EMPTY_CHECKS,
    PROMOTE_AFTER_FOUND,
    new_source_row,
    next_source_status,
    order_companies_for_scout,
    run_scout,
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
            step=step, model=model,
            text='{"sources": [{"company_name": "Acme", "name": "Acme careers", "url": "https://acme.example/careers", "kind": "Company career sites", "covers": "Acme roles"}]}',
        )

    found, result = run_scout([{"name": "Acme"}], model="sonnet", run_claude=fake_run_claude)
    assert found[0]["name"] == "Acme careers"
    assert result.step == "scout"


def test_new_source_row_starts_as_trial():
    row = new_source_row({"name": "x", "url": "https://x", "kind": "Job boards", "covers": ""}, now_iso="2026-09-20T08:00:00")
    assert row["status"] == "trial"
    assert row["found_count"] == 0
    assert row["discovered_by"] == "claude-scout"


def test_next_source_status_promotes_after_found_posting():
    assert next_source_status("trial", found_count=PROMOTE_AFTER_FOUND, empty_checks_in_a_row=0) == "active"


def test_next_source_status_demotes_after_repeated_empty_checks():
    assert next_source_status("active", found_count=0, empty_checks_in_a_row=DEMOTE_AFTER_EMPTY_CHECKS) == "demoted"


def test_next_source_status_stays_put_when_neither_threshold_met():
    assert next_source_status("trial", found_count=0, empty_checks_in_a_row=1) == "trial"


def test_next_source_status_never_touches_a_source_already_demoted():
    assert next_source_status("demoted", found_count=0, empty_checks_in_a_row=5) == "demoted"


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
