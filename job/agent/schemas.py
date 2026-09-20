"""Validates Claude's JSON output for each agent step before it's used.

Hand-written instead of using the `jsonschema` package, matching
`api/src/titles.py`'s approach (see its docstring) so the validation style
stays consistent across the codebase, even though `job/` runs under plain
CPython and could use the dependency.
"""
from __future__ import annotations

import json


class ClaudeOutputError(ValueError):
    pass


def parse_and_validate(text: str, validator) -> dict:
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise ClaudeOutputError(f"not valid JSON: {exc}") from exc
    problems = validator(payload)
    if problems:
        raise ClaudeOutputError("; ".join(problems))
    return payload


def _is_str(v) -> bool:
    return isinstance(v, str)


def _is_nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _is_str_or_none(v) -> bool:
    return v is None or isinstance(v, str)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def validate_discovery_output(payload) -> list[str]:
    if not isinstance(payload, dict):
        return ["document must be a JSON object"]
    results = payload.get("results")
    if not isinstance(results, list):
        return ["'results' must be a list"]

    problems: list[str] = []
    for i, r in enumerate(results):
        if not isinstance(r, dict):
            problems.append(f"results[{i}] must be an object")
            continue
        for field_name in ("company_name", "title", "url"):
            if not _is_nonempty_str(r.get(field_name)):
                problems.append(f"results[{i}].{field_name} must be a non-empty string")
        if not _is_str_or_none(r.get("location_text")):
            problems.append(f"results[{i}].location_text must be a string")
        if not _is_str_or_none(r.get("posted_date")):
            problems.append(f"results[{i}].posted_date must be a string")
    return problems


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

_CLASSIFICATION_DECISIONS = {"match", "reject"}
_LOCATION_MATCHES = {"match", "outside", "unclear"}


def validate_classification_output(payload) -> list[str]:
    if not isinstance(payload, dict):
        return ["document must be a JSON object"]
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        return ["'decisions' must be a list"]

    problems: list[str] = []
    for i, d in enumerate(decisions):
        if not isinstance(d, dict):
            problems.append(f"decisions[{i}] must be an object")
            continue
        if not isinstance(d.get("id"), int):
            problems.append(f"decisions[{i}].id must be an integer")
        decision = d.get("decision")
        if decision not in _CLASSIFICATION_DECISIONS:
            problems.append(f"decisions[{i}].decision must be one of {sorted(_CLASSIFICATION_DECISIONS)}")
        if decision == "match" and not _is_nonempty_str(d.get("matched_title")):
            problems.append(f"decisions[{i}].matched_title is required when decision is 'match'")
        if not _is_str_or_none(d.get("title_en")):
            problems.append(f"decisions[{i}].title_en must be a string")
        location_match = d.get("location_match")
        if location_match is not None and location_match not in _LOCATION_MATCHES:
            problems.append(f"decisions[{i}].location_match must be one of {sorted(_LOCATION_MATCHES)}")
        if not _is_str_or_none(d.get("reason")):
            problems.append(f"decisions[{i}].reason must be a string")
    return problems


# ---------------------------------------------------------------------------
# Unclear-page reads
# ---------------------------------------------------------------------------

_UNCLEAR_DECISIONS = {"open", "closed", "unclear"}


def validate_unclear_output(payload) -> list[str]:
    if not isinstance(payload, dict):
        return ["document must be a JSON object"]
    problems: list[str] = []
    if payload.get("decision") not in _UNCLEAR_DECISIONS:
        problems.append(f"decision must be one of {sorted(_UNCLEAR_DECISIONS)}")
    if not _is_str_or_none(payload.get("note")):
        problems.append("note must be a string")
    return problems


# ---------------------------------------------------------------------------
# Source scout
# ---------------------------------------------------------------------------

SOURCE_KINDS = {
    "Company career sites", "Job boards", "Trackers and aggregators",
    "School job boards", "Timing guides", "Added by you",
}


def validate_scout_output(payload) -> list[str]:
    if not isinstance(payload, dict):
        return ["document must be a JSON object"]
    sources = payload.get("sources")
    if not isinstance(sources, list):
        return ["'sources' must be a list"]

    problems: list[str] = []
    for i, s in enumerate(sources):
        if not isinstance(s, dict):
            problems.append(f"sources[{i}] must be an object")
            continue
        for field_name in ("name", "url"):
            if not _is_nonempty_str(s.get(field_name)):
                problems.append(f"sources[{i}].{field_name} must be a non-empty string")
        kind = s.get("kind")
        if kind is not None and kind not in SOURCE_KINDS:
            problems.append(f"sources[{i}].kind must be one of {sorted(SOURCE_KINDS)}")
        if not _is_str_or_none(s.get("covers")):
            problems.append(f"sources[{i}].covers must be a string")
    return problems


# ---------------------------------------------------------------------------
# Daily improvement
# ---------------------------------------------------------------------------

PROPOSAL_KINDS = {"company", "source", "source_retire"}
TITLE_SUGGESTION_ACTIONS = {"add_title", "add_alias", "deactivate"}


def validate_improvement_output(payload) -> list[str]:
    if not isinstance(payload, dict):
        return ["document must be a JSON object"]

    problems: list[str] = []
    search_plan = payload.get("search_plan")
    if search_plan is not None and not isinstance(search_plan, dict):
        problems.append("'search_plan' must be an object")

    for key in ("proposals", "title_suggestions"):
        if key in payload and not isinstance(payload.get(key), list):
            problems.append(f"'{key}' must be a list")

    for i, p in enumerate(payload.get("proposals") or []):
        if not isinstance(p, dict):
            problems.append(f"proposals[{i}] must be an object")
            continue
        if p.get("kind") not in PROPOSAL_KINDS:
            problems.append(f"proposals[{i}].kind must be one of {sorted(PROPOSAL_KINDS)}")
        if not isinstance(p.get("payload"), dict):
            problems.append(f"proposals[{i}].payload must be an object")
        if not _is_str_or_none(p.get("reason")):
            problems.append(f"proposals[{i}].reason must be a string")

    for i, t in enumerate(payload.get("title_suggestions") or []):
        if not isinstance(t, dict):
            problems.append(f"title_suggestions[{i}] must be an object")
            continue
        if t.get("action") not in TITLE_SUGGESTION_ACTIONS:
            problems.append(f"title_suggestions[{i}].action must be one of {sorted(TITLE_SUGGESTION_ACTIONS)}")
        if not _is_nonempty_str(t.get("title")):
            problems.append(f"title_suggestions[{i}].title must be a non-empty string")

    return problems
