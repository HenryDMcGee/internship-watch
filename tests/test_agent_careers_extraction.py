"""job/agent/careers_extraction.py: Haiku pulls structured listings out of a
changed careers page's text (issue #53, phase 6 part 1). Judging whether a
listing matches an active title is a separate, later step
(job/agent/classification.py) -- this only checks extraction and its
validation.
"""
from __future__ import annotations

import json

from job.agent import careers_extraction
from job.agent.client import ClaudeStepResult
from job.agent.schemas import ClaudeOutputError


def _result(text: str) -> ClaudeStepResult:
    return ClaudeStepResult(step="careers_extraction", model="haiku", text=text)


def test_build_prompt_includes_company_name_and_page_text():
    prompt = careers_extraction.build_prompt("Acme", "PM Intern - San Francisco")
    assert "Acme" in prompt
    assert "PM Intern - San Francisco" in prompt


def test_build_prompt_truncates_long_pages():
    long_text = "x" * 50000
    prompt = careers_extraction.build_prompt("Acme", long_text, max_chars=100)
    assert "x" * 100 in prompt
    assert "x" * 101 not in prompt


def test_run_extraction_returns_valid_listings():
    payload = {"listings": [
        {"title": "MBA Product Manager Intern", "location": "San Francisco, CA",
         "link": "https://acme.example/jobs/1", "posted_date": "2026-09-01", "job_id": "123"},
    ]}

    def fake_run_claude(step, prompt, *, model):
        assert step == "careers_extraction"
        return _result(json.dumps(payload))

    listings, result = careers_extraction.run_extraction("Acme", "page text", model="haiku", run_claude=fake_run_claude)
    assert listings == payload["listings"]
    assert result is not None


def test_run_extraction_drops_malformed_listings_but_keeps_good_ones():
    payload = {"listings": [
        {"title": "MBA Product Manager Intern", "location": "San Francisco, CA", "link": "https://acme.example/jobs/1"},
        {"title": "", "location": "Remote", "link": "https://acme.example/jobs/2"},  # empty title
        {"location": "Remote", "link": "https://acme.example/jobs/3"},  # missing title entirely
    ]}

    def fake_run_claude(step, prompt, *, model):
        return _result(json.dumps(payload))

    listings, result = careers_extraction.run_extraction("Acme", "page text", model="haiku", run_claude=fake_run_claude)
    assert len(listings) == 1
    assert listings[0]["title"] == "MBA Product Manager Intern"


def test_run_extraction_handles_empty_listings():
    def fake_run_claude(step, prompt, *, model):
        return _result(json.dumps({"listings": []}))

    listings, result = careers_extraction.run_extraction("Acme", "page text", model="haiku", run_claude=fake_run_claude)
    assert listings == []


def test_run_extraction_raises_on_invalid_json():
    def fake_run_claude(step, prompt, *, model):
        return _result("not json")

    try:
        careers_extraction.run_extraction("Acme", "page text", model="haiku", run_claude=fake_run_claude)
        assert False, "expected ClaudeOutputError"
    except ClaudeOutputError:
        pass
