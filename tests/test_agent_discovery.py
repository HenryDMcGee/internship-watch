import json
import re

import pytest

from job.agent.client import ClaudeLimitError, ClaudeStepResult
from job.agent.discovery import (
    DiscoveryReport, build_prompt, order_companies, run_discovery, split_batches,
)
from job.agent.schemas import validate_discovery_batch_output

TITLES_DOC = {"titles": [{"title": "PM Intern", "active": True}], "exclude_words": []}
LOCATIONS = {"countries": ["US"], "include_remote": False}


def _companies(n):
    return [{"id": i, "name": f"Company {i:03d}", "rank": i} for i in range(1, n + 1)]


def _batch_names(prompt):
    return [n.strip() for n in re.search(r"watch list: (.*?)\.\nLook only", prompt, re.S).group(1).split(";")]


def _entry(name, *, searched=True, postings=None):
    return {
        "company_name": name, "searched": searched,
        "queries": [f"{name} MBA product manager intern Summer 2027"] if searched else [],
        "postings": postings or "none found",
    }


def _fake_claude(*, searched=lambda name: True, searches=lambda names: len(names), postings=None):
    """A `run_claude` stand-in that answers for every company in the prompt's batch."""
    calls = []

    def fake(step, prompt, *, model):
        names = _batch_names(prompt)
        calls.append(names)
        entries = [_entry(n, searched=searched(n), postings=(postings or {}).get(n)) for n in names]
        return ClaudeStepResult(
            step=step, model=model, web_searches=searches(names), text=json.dumps({"companies": entries}),
        )

    fake.calls = calls
    return fake


def _run(companies, fake, **kwargs):
    kwargs.setdefault("search_limit_per_run", 500)
    return run_discovery(companies, TITLES_DOC, LOCATIONS, None, model="sonnet", run_claude=fake, **kwargs)


# -- ordering and rotation ---------------------------------------------------

def test_order_companies_puts_watchlist_first_in_watchlist_order():
    companies = [{"name": "Zeta"}, {"name": "Acme"}, {"name": "Beta"}]
    ordered = order_companies(companies, watchlist=["Beta", "Zeta"])
    assert [c["name"] for c in ordered] == ["Beta", "Zeta", "Acme"]


def test_order_companies_no_watchlist_keeps_alphabetical_by_name():
    companies = [{"name": "Zeta"}, {"name": "Acme"}]
    ordered = order_companies(companies, watchlist=[])
    assert [c["name"] for c in ordered] == ["Acme", "Zeta"]


def test_order_companies_longest_since_searched_first_never_searched_before_any():
    companies = [
        {"name": "Recent", "last_discovery_searched_at": "2026-10-04T08:00:00"},
        {"name": "Never"},
        {"name": "Older", "last_discovery_searched_at": "2026-09-01T08:00:00"},
    ]
    ordered = order_companies(companies, watchlist=[])
    assert [c["name"] for c in ordered] == ["Never", "Older", "Recent"]


def test_order_companies_watch_list_stays_ahead_of_a_never_searched_company():
    companies = [
        {"name": "Never"},
        {"name": "Watched", "last_discovery_searched_at": "2026-10-04T08:00:00"},
    ]
    ordered = order_companies(companies, watchlist=["Watched"])
    assert [c["name"] for c in ordered] == ["Watched", "Never"]


def test_split_batches_makes_batches_of_15_to_20():
    batches = split_batches(_companies(180))
    assert [len(b) for b in batches] == [20] * 9
    assert [len(b) for b in split_batches(_companies(35))] == [18, 17]
    assert [len(b) for b in split_batches(_companies(15))] == [15]
    assert split_batches([]) == []


def test_180_companies_are_searched_in_batches_within_the_limits():
    fake = _fake_claude()
    report = _run(_companies(180), fake, search_limit_per_run=180)

    assert [len(names) for names in fake.calls] == [20] * 9
    assert report.planned_searches == 180
    assert report.searches_made == 180
    assert len(report.searched_ids) == 180
    assert not report.failed


def test_search_limits_cap_how_many_companies_a_run_plans():
    fake = _fake_claude()
    report = _run(_companies(180), fake, search_limit_per_run=35)
    assert sum(len(names) for names in fake.calls) == 35

    fake = _fake_claude()
    report = _run(_companies(180), fake, search_limit_per_run=35, daily_searches_remaining=10)
    assert sum(len(names) for names in fake.calls) == 10
    assert report.planned_searches == 10


def test_rotation_searches_every_company_before_any_is_searched_twice():
    companies = _companies(180)
    searched_count = {c["id"]: 0 for c in companies}
    stamp = 0
    for _ in range(6):  # 6 runs of 35 cover 210 slots
        stamp += 1
        ordered = order_companies(companies, watchlist=[])
        report = _run(ordered, _fake_claude(), search_limit_per_run=35)
        for company in companies:
            if company["id"] in report.searched_ids:
                searched_count[company["id"]] += 1
                company["last_discovery_searched_at"] = f"2026-10-{stamp:02d}T08:00:00"
        if stamp == 5:
            # 5 runs x 35 = 175 slots: 175 distinct companies, none twice yet
            assert max(searched_count.values()) == 1
            assert sum(searched_count.values()) == 175
    # by the 6th run all 180 had a turn, and only then did anyone get a second one
    assert min(searched_count.values()) == 1
    assert sum(searched_count.values()) == 210
    assert sum(1 for n in searched_count.values() if n == 2) == 30


# -- the prompt ----------------------------------------------------------------

def test_build_prompt_includes_company_names_and_limits():
    prompt = build_prompt([{"name": "Acme"}], TITLES_DOC, LOCATIONS, None, search_limit_per_company=2)
    assert "Acme" in prompt
    assert "PM Intern" in prompt
    assert "at most 2 per company" in prompt


def test_build_prompt_tells_claude_to_search_each_company_by_name():
    prompt = build_prompt(
        [{"name": "Acme"}, {"name": "Beta"}], TITLES_DOC, LOCATIONS, None, search_limit_per_company=2,
    )
    assert "<company> MBA product manager intern Summer 2027" in prompt
    assert "<company> careers internship MBA" in prompt
    assert "Search for EACH company by name" in prompt
    assert "Do not stop after a few general searches" in prompt
    assert "at least 2 searches in total" in prompt


def test_build_prompt_includes_search_plan_notes():
    plan = {"query_templates": ["{company} MBA intern"], "notes": "Try LinkedIn alternatives"}
    prompt = build_prompt([{"name": "Acme"}], TITLES_DOC, LOCATIONS, plan, search_limit_per_company=1)
    assert "Try LinkedIn alternatives" in prompt
    assert "{company} MBA intern" in prompt


# -- running it ----------------------------------------------------------------

def test_run_discovery_returns_an_empty_report_without_calling_claude_when_no_companies():
    calls = []
    report = _run([], lambda *a, **k: calls.append(1))
    assert report.results == []
    assert report.usage == []
    assert calls == []


def test_run_discovery_collects_postings_per_company():
    postings = {"Company 001": [{"title": "PM Intern", "url": "https://x/1", "location_text": "SF"}]}
    fake = _fake_claude(postings=postings)
    report = _run(_companies(3), fake)

    assert report.results == [{
        "company_name": "Company 001", "title": "PM Intern", "url": "https://x/1",
        "location_text": "SF", "posted_date": None,
    }]
    assert not report.failed
    assert len(report.usage) == 1


def test_validation_requires_a_complete_entry_for_each_company():
    good = {"companies": [_entry("Acme"), _entry("Beta", searched=False)]}
    assert validate_discovery_batch_output(good) == []

    assert validate_discovery_batch_output({"results": []}) != []
    assert validate_discovery_batch_output({"companies": [{"company_name": "Acme"}]}) != []
    no_queries = {"companies": [{"company_name": "Acme", "searched": True, "queries": [], "postings": "none found"}]}
    assert any("no queries" in p for p in validate_discovery_batch_output(no_queries))
    bad_posting = {"companies": [{
        "company_name": "Acme", "searched": True, "queries": ["q"], "postings": [{"title": "PM Intern"}],
    }]}
    assert any("url" in p for p in validate_discovery_batch_output(bad_posting))
    assert any("none found" in p for p in validate_discovery_batch_output({"companies": [{
        "company_name": "Acme", "searched": True, "queries": ["q"], "postings": "nothing here",
    }]}))


def test_a_company_missing_from_the_reply_counts_as_unsearched_and_is_retried_once():
    calls = []

    def fake(step, prompt, *, model):
        names = _batch_names(prompt)
        calls.append(names)
        # First call forgets Company 002; the retry answers for it.
        answered = [n for n in names if not (len(calls) == 1 and n == "Company 002")]
        return ClaudeStepResult(
            step=step, model=model, web_searches=len(answered),
            text=json.dumps({"companies": [_entry(n) for n in answered]}),
        )

    report = _run(_companies(3), fake, search_limit_per_run=10)

    assert calls == [["Company 001", "Company 002", "Company 003"], ["Company 002"]]
    assert not report.failed
    assert report.unsearched == []
    assert len(report.searched_ids) == 3


def test_a_company_marked_unsearched_is_retried_in_a_later_batch():
    calls = []

    def fake(step, prompt, *, model):
        names = _batch_names(prompt)
        calls.append(names)
        entries = [_entry(n, searched=not (len(calls) == 1 and n == "Company 001")) for n in names]
        return ClaudeStepResult(
            step=step, model=model, web_searches=sum(e["searched"] for e in entries),
            text=json.dumps({"companies": entries}),
        )

    report = _run(_companies(3), fake, search_limit_per_run=10)

    assert calls[1] == ["Company 001"]
    assert not report.failed


def test_step_fails_when_a_company_stays_unsearched_after_the_retry():
    fake = _fake_claude(searched=lambda name: name != "Company 001")
    report = _run(_companies(3), fake, search_limit_per_run=10)

    assert report.failed
    assert "Company 001" in report.failed_reason
    assert report.unsearched == ["Company 001"]


def test_step_fails_when_too_few_searches_are_made_even_if_claude_says_it_searched_everything():
    # The original bug: 3 to 6 searches for about 180 companies, with a reply that
    # looked fine. One call's searches can only vouch for that many companies.
    fake = _fake_claude(searches=lambda names: 3)
    report = _run(_companies(40), fake, search_limit_per_run=40)

    assert report.failed
    assert report.searches_made < report.planned_searches
    assert report.unsearched


def test_a_short_batch_is_made_up_by_the_retry_when_searches_catch_up():
    state = {"first": True}

    def fake(step, prompt, *, model):
        names = _batch_names(prompt)
        searches = len(names) - 2 if state.pop("first", False) else len(names)
        return ClaudeStepResult(
            step=step, model=model, web_searches=searches,
            text=json.dumps({"companies": [_entry(n) for n in names]}),
        )

    report = _run(_companies(10), fake, search_limit_per_run=20)

    assert not report.failed
    assert len(report.searched_ids) == 10


def test_two_calls_in_a_row_with_no_searches_stop_the_step_instead_of_burning_budget():
    fake = _fake_claude(searches=lambda names: 0)
    report = _run(_companies(180), fake, search_limit_per_run=180)

    assert len(fake.calls) == 2
    assert report.failed
    assert "no searches" in report.failed_reason


def test_an_unusable_reply_leaves_its_companies_unsearched_and_still_records_usage():
    def fake(step, prompt, *, model):
        return ClaudeStepResult(step=step, model=model, web_searches=3, text="not json at all")

    report = _run(_companies(2), fake, search_limit_per_run=10)

    assert report.failed
    assert len(report.usage) == 2  # the call and its retry both cost real searches
    assert report.searches_made == 6


def test_a_usage_limit_stops_discovery_but_keeps_what_was_collected_so_far():
    calls = []

    def fake(step, prompt, *, model):
        calls.append(1)
        if len(calls) == 2:
            raise ClaudeLimitError("usage limit reached")
        names = _batch_names(prompt)
        return ClaudeStepResult(
            step=step, model=model, web_searches=len(names),
            text=json.dumps({"companies": [_entry(n) for n in names]}),
        )

    report = DiscoveryReport()
    with pytest.raises(ClaudeLimitError):
        _run(_companies(40), fake, search_limit_per_run=40, report=report)

    assert len(report.usage) == 1
    assert len(report.searched_ids) == 20


def test_each_batch_is_logged_and_a_reply_with_no_results_shows_its_first_500_characters(capsys):
    long_note = "x" * 800

    def fake(step, prompt, *, model):
        names = _batch_names(prompt)
        entries = [_entry(n) for n in names]
        return ClaudeStepResult(
            step=step, model=model, web_searches=len(names),
            text=json.dumps({"note": long_note, "companies": entries}),
        )

    report = _run(_companies(3), fake, search_limit_per_run=10)

    out = capsys.readouterr().out
    assert "discovery batch 1/1: 3 companies, 3 search(es), 0 result(s)" in out
    assert report.batches[0].reply_head is not None
    assert len(report.batches[0].reply_head) == 500
    assert "x" * 100 in out


def test_a_batch_with_results_does_not_log_the_reply():
    postings = {"Company 001": [{"title": "PM Intern", "url": "https://x/1", "location_text": ""}]}
    report = _run(_companies(2), _fake_claude(postings=postings))
    assert report.batches[0].results == 1
    assert report.batches[0].reply_head is None
