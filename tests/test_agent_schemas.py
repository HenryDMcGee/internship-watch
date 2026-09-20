import pytest

from job.agent.schemas import (
    ClaudeOutputError,
    parse_and_validate,
    split_scout_sources,
    validate_classification_output,
    validate_discovery_output,
    validate_improvement_output,
    validate_scout_document,
    validate_scout_output,
    validate_scout_source,
    validate_unclear_output,
)


def test_parse_and_validate_rejects_non_json_text():
    with pytest.raises(ClaudeOutputError):
        parse_and_validate("not json", validate_discovery_output)


def test_parse_and_validate_rejects_invalid_shape():
    with pytest.raises(ClaudeOutputError):
        parse_and_validate('{"nope": true}', validate_discovery_output)


def test_parse_and_validate_accepts_valid_document():
    doc = parse_and_validate('{"results": []}', validate_discovery_output)
    assert doc == {"results": []}


def test_parse_and_validate_attaches_result_to_error_on_bad_json():
    sentinel = object()
    with pytest.raises(ClaudeOutputError) as exc_info:
        parse_and_validate("not json", validate_discovery_output, result=sentinel)
    assert exc_info.value.result is sentinel


def test_parse_and_validate_attaches_result_to_error_on_invalid_shape():
    sentinel = object()
    with pytest.raises(ClaudeOutputError) as exc_info:
        parse_and_validate('{"nope": true}', validate_discovery_output, result=sentinel)
    assert exc_info.value.result is sentinel


def test_parse_and_validate_error_result_defaults_to_none():
    with pytest.raises(ClaudeOutputError) as exc_info:
        parse_and_validate("not json", validate_discovery_output)
    assert exc_info.value.result is None


# -- discovery ----------------------------------------------------------

def test_discovery_output_valid():
    payload = {"results": [{"company_name": "Acme", "title": "PM Intern", "url": "https://x", "location_text": "SF"}]}
    assert validate_discovery_output(payload) == []


def test_discovery_output_rejects_missing_url():
    payload = {"results": [{"company_name": "Acme", "title": "PM Intern"}]}
    problems = validate_discovery_output(payload)
    assert any("url" in p for p in problems)


def test_discovery_output_rejects_non_list_results():
    assert validate_discovery_output({"results": "nope"}) != []


# -- classification -------------------------------------------------------

def test_classification_output_valid_match():
    payload = {"decisions": [{"id": 1, "decision": "match", "matched_title": "PM Intern", "title_en": None}]}
    assert validate_classification_output(payload) == []


def test_classification_output_requires_matched_title_for_match():
    payload = {"decisions": [{"id": 1, "decision": "match"}]}
    problems = validate_classification_output(payload)
    assert any("matched_title" in p for p in problems)


def test_classification_output_rejects_bad_decision():
    payload = {"decisions": [{"id": 1, "decision": "maybe"}]}
    assert validate_classification_output(payload) != []


def test_classification_output_rejects_non_int_id():
    payload = {"decisions": [{"id": "1", "decision": "reject"}]}
    assert validate_classification_output(payload) != []


# -- unclear page -----------------------------------------------------------

def test_unclear_output_valid():
    assert validate_unclear_output({"decision": "open", "note": "apply now"}) == []


def test_unclear_output_rejects_bad_decision():
    assert validate_unclear_output({"decision": "maybe"}) != []


# -- scout --------------------------------------------------------------

def test_scout_output_valid():
    payload = {"sources": [{"name": "Acme careers", "url": "https://x", "kind": "Job boards", "covers": "PM roles"}]}
    assert validate_scout_output(payload) == []


def test_scout_output_rejects_bad_kind():
    payload = {"sources": [{"name": "x", "url": "https://x", "kind": "Not a kind"}]}
    assert validate_scout_output(payload) != []


def test_scout_output_rejects_missing_name():
    payload = {"sources": [{"url": "https://x"}]}
    assert validate_scout_output(payload) != []


def test_scout_document_only_checks_shape_not_items():
    # A bad item is not this gate's job -- `split_scout_sources` handles
    # per-item problems separately, so one bad entry doesn't fail the
    # whole reply (issue #20).
    payload = {"sources": [{"url": "https://x"}]}  # missing "name"
    assert validate_scout_document(payload) == []


def test_scout_document_rejects_non_list_sources():
    assert validate_scout_document({"sources": "nope"}) != []


def test_scout_source_rejects_bad_kind():
    assert validate_scout_source({"name": "x", "url": "https://x", "kind": "Not a kind"}) != []


def test_scout_source_accepts_a_valid_entry():
    assert validate_scout_source({"name": "x", "url": "https://x", "kind": "Job boards"}) == []


def test_split_scout_sources_keeps_good_entries_and_reports_bad_ones():
    payload = {"sources": [
        {"name": "Good", "url": "https://good.example"},
        {"url": "https://bad.example"},  # missing "name"
    ]}
    valid, problems = split_scout_sources(payload)
    assert [s["name"] for s in valid] == ["Good"]
    assert len(problems) == 1
    assert "sources[1]" in problems[0]


# -- improvement --------------------------------------------------------

def test_improvement_output_valid_empty():
    payload = {"search_plan": {"query_templates": [], "notes": ""}, "proposals": [], "title_suggestions": []}
    assert validate_improvement_output(payload) == []


def test_improvement_output_rejects_bad_proposal_kind():
    payload = {"proposals": [{"kind": "not-a-kind", "payload": {}}]}
    assert validate_improvement_output(payload) != []


def test_improvement_output_rejects_bad_title_suggestion_action():
    payload = {"title_suggestions": [{"action": "delete", "title": "x"}]}
    assert validate_improvement_output(payload) != []


def test_improvement_output_requires_payload_object_on_proposal():
    payload = {"proposals": [{"kind": "company", "payload": "not-an-object"}]}
    assert validate_improvement_output(payload) != []
