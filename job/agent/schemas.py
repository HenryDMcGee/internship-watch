"""Validates Claude's JSON output for each agent step before it's used.

Hand-written instead of using the `jsonschema` package, matching
`api/src/titles.py`'s approach (see its docstring) so the validation style
stays consistent across the codebase, even though `job/` runs under plain
CPython and could use the dependency.
"""
from __future__ import annotations

import json
import re

# Claude sometimes wraps an otherwise-valid JSON reply in a markdown code fence
# (```json ... ``` or ``` ... ```) even when the prompt asks for "only JSON, no
# other text" -- issue #13 saw classification rejected as "not valid JSON" for
# exactly this reason, even though the reply was usable once the fence was
# stripped. Matched non-greedily so a fence is stripped even if the model adds
# a trailing note after the closing ``` on the same reply.
_CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    match = _CODE_FENCE_RE.match(text)
    return match.group(1) if match else text


def _extract_first_json_object(text: str) -> str | None:
    """Finds the first balanced `{...}` block in `text`, so a reply that adds
    any other text around otherwise-valid JSON (a fence not caught by
    `_strip_code_fence`, a leading "Here's the JSON:", ...) still parses,
    instead of failing the whole step over formatting Claude wasn't asked to
    produce.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


class ClaudeOutputError(ValueError):
    """Claude's reply wasn't usable JSON. Carries the `ClaudeStepResult` the
    CLI actually returned (when there was one), so a caller can still record
    the tokens/searches that call spent even though the answer is being
    thrown away -- otherwise a step that runs and burns budget but replies
    with something we can't parse leaves no trace at all (see
    `pipeline.guarded`).
    """

    def __init__(self, message: str, *, result=None):
        super().__init__(message)
        self.result = result


def _parse_json_with_fallback(text: str) -> dict:
    candidate = _strip_code_fence(text)
    try:
        return json.loads(candidate)
    except ValueError as exc:
        extracted = _extract_first_json_object(candidate)
        if extracted is None:
            raise exc
        return json.loads(extracted)


def parse_and_validate(text: str, validator, *, result=None) -> dict:
    try:
        payload = _parse_json_with_fallback(text)
    except ValueError as exc:
        step = getattr(result, "step", "?")
        print(f"{step}: reply was not valid JSON ({exc}); first 500 character(s): {text[:500]!r}")
        raise ClaudeOutputError(f"not valid JSON: {exc}", result=result) from exc
    problems = validator(payload)
    if problems:
        raise ClaudeOutputError("; ".join(problems), result=result)
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
        problems.extend(f"decisions[{i}]: {p}" for p in validate_classification_decision(d))
    return problems


def validate_classification_document(payload) -> list[str]:
    """Shape-only check for a classification reply: a JSON object with a
    `decisions` list. Used as the hard parse gate in
    `classification.run_classification` instead of
    `validate_classification_output`, which also rejects the whole list
    over a single bad item -- the same failure mode fixed for the scout in
    issue #20 (see `validate_scout_document`), and the reason classification
    could spend real tokens on a batch yet leave every posting in it
    unclassified: one malformed decision sank the other, otherwise-good
    ones alongside it. Per-item problems are handled separately by
    `split_classification_decisions`.
    """
    if not isinstance(payload, dict):
        return ["document must be a JSON object"]
    if not isinstance(payload.get("decisions"), list):
        return ["'decisions' must be a list"]
    return []


def validate_classification_decision(d) -> list[str]:
    """Per-item version of `validate_classification_output`'s checks, for
    one entry in a classification reply's `decisions` list."""
    if not isinstance(d, dict):
        return ["must be an object"]
    problems: list[str] = []
    if not isinstance(d.get("id"), int):
        problems.append("id must be an integer")
    decision = d.get("decision")
    if decision not in _CLASSIFICATION_DECISIONS:
        problems.append(f"decision must be one of {sorted(_CLASSIFICATION_DECISIONS)}")
    if decision == "match" and not _is_nonempty_str(d.get("matched_title")):
        problems.append("matched_title is required when decision is 'match'")
    if not _is_str_or_none(d.get("title_en")):
        problems.append("title_en must be a string")
    location_match = d.get("location_match")
    if location_match is not None and location_match not in _LOCATION_MATCHES:
        problems.append(f"location_match must be one of {sorted(_LOCATION_MATCHES)}")
    if not _is_str_or_none(d.get("reason")):
        problems.append("reason must be a string")
    return problems


def split_classification_decisions(payload: dict) -> tuple[list[dict], list[str]]:
    """Splits an already-shape-checked classification reply into the
    decisions that pass per-item validation and human-readable problems for
    the ones that don't, so one malformed entry doesn't cost every other
    real decision in the same batch (issue #26)."""
    decisions = payload.get("decisions") or []
    valid: list[dict] = []
    problems: list[str] = []
    for i, d in enumerate(decisions):
        item_problems = validate_classification_decision(d)
        if item_problems:
            problems.append(f"decisions[{i}]: " + "; ".join(item_problems))
        else:
            valid.append(d)
    return valid, problems


# ---------------------------------------------------------------------------
# Careers page extraction
# ---------------------------------------------------------------------------

def validate_careers_extraction_document(payload) -> list[str]:
    """Shape-only check, matching `validate_classification_document`'s
    reasoning: one malformed listing must not sink every other real one
    Haiku found on the same page. Per-item problems are handled separately
    by `split_careers_listings`.
    """
    if not isinstance(payload, dict):
        return ["document must be a JSON object"]
    if not isinstance(payload.get("listings"), list):
        return ["'listings' must be a list"]
    return []


def validate_careers_listing(item) -> list[str]:
    if not isinstance(item, dict):
        return ["must be an object"]
    problems: list[str] = []
    for field_name in ("title", "location", "link"):
        if not _is_nonempty_str(item.get(field_name)):
            problems.append(f"{field_name} must be a non-empty string")
    if not _is_str_or_none(item.get("posted_date")):
        problems.append("posted_date must be a string")
    if not _is_str_or_none(item.get("job_id")):
        problems.append("job_id must be a string")
    return problems


def split_careers_listings(payload: dict) -> tuple[list[dict], list[str]]:
    listings = payload.get("listings") or []
    valid: list[dict] = []
    problems: list[str] = []
    for i, item in enumerate(listings):
        item_problems = validate_careers_listing(item)
        if item_problems:
            problems.append(f"listings[{i}]: " + "; ".join(item_problems))
        else:
            valid.append(item)
    return valid, problems


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


def validate_scout_document(payload) -> list[str]:
    """Shape-only check for a scout reply: a JSON object with a `sources`
    list. Used as the hard parse gate in `scout.run_scout` instead of
    `validate_scout_output`, which also rejects the whole list over a single
    bad item -- exactly the failure mode that let a full batch of otherwise
    good scout results (issue #20) get thrown away silently. Per-item
    problems are handled separately by `split_scout_sources`.
    """
    if not isinstance(payload, dict):
        return ["document must be a JSON object"]
    if not isinstance(payload.get("sources"), list):
        return ["'sources' must be a list"]
    return []


def validate_scout_source(s) -> list[str]:
    """Per-item version of `validate_scout_output`'s checks, for one entry
    in a scout reply's `sources` list."""
    if not isinstance(s, dict):
        return ["must be an object"]
    problems: list[str] = []
    for field_name in ("name", "url"):
        if not _is_nonempty_str(s.get(field_name)):
            problems.append(f"{field_name} must be a non-empty string")
    kind = s.get("kind")
    if kind is not None and kind not in SOURCE_KINDS:
        problems.append(f"kind must be one of {sorted(SOURCE_KINDS)}")
    if not _is_str_or_none(s.get("covers")):
        problems.append("covers must be a string")
    return problems


def split_scout_sources(payload: dict) -> tuple[list[dict], list[str]]:
    """Splits an already-shape-checked scout reply into the sources that
    pass per-item validation and human-readable problems for the ones that
    don't, so one malformed entry in a big batch doesn't cost every other
    real result the same call found."""
    sources = payload.get("sources") or []
    valid: list[dict] = []
    problems: list[str] = []
    for i, s in enumerate(sources):
        item_problems = validate_scout_source(s)
        if item_problems:
            problems.append(f"sources[{i}]: " + "; ".join(item_problems))
        else:
            valid.append(s)
    return valid, problems


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
