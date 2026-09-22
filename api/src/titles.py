"""Titles editing: validate against the schema's rules, then open or update
a pull request that changes only `config/titles.json`.

Duplicates the constraints in `config/titles.schema.json` without a
`jsonschema` dependency, since the Worker runs on Pyodide and Python
Workers only support packages built for it (see CLAUDE.md). Keep this in
sync with the schema and with `scripts/validate_titles.py`, which validates
the same file with `jsonschema` in CI where that dependency is fine.
"""
from __future__ import annotations

import json

from github_client import GitHubApiError, GitHubClient

TITLES_PATH = "config/titles.json"
TITLES_BRANCH = "titles-update-from-dashboard"
BASE_BRANCH = "master"  # this repository's default branch (see README's setup steps)


def validate_titles_doc(doc: dict) -> list[str]:
    problems: list[str] = []

    if not isinstance(doc, dict):
        return ["document must be a JSON object"]

    for key in ("version", "exclude_words", "titles"):
        if key not in doc:
            problems.append(f"missing required field '{key}'")
    if problems:
        return problems

    if doc.get("version") != 1:
        problems.append("version must be 1")

    exclude_words = doc.get("exclude_words")
    if not isinstance(exclude_words, list) or not all(isinstance(w, str) and w.strip() for w in exclude_words):
        problems.append("exclude_words must be a list of non-empty strings")
        exclude_words = []
    if len(set(exclude_words)) != len(exclude_words):
        problems.append("exclude_words has duplicates")

    titles = doc.get("titles")
    if not isinstance(titles, list) or not titles:
        problems.append("titles must be a non-empty list")
        titles = []

    seen: dict[str, str] = {}
    for entry in titles:
        if not isinstance(entry, dict):
            problems.append("each title entry must be an object")
            continue
        for field_name in ("title", "group", "aliases", "active"):
            if field_name not in entry:
                problems.append(f"title entry missing '{field_name}'")

        title = entry.get("title")
        if not isinstance(title, str) or not (2 <= len(title) <= 120):
            problems.append(f"title '{title}' must be a string between 2 and 120 characters")

        group = entry.get("group")
        if not isinstance(group, str) or not (1 <= len(group) <= 60):
            problems.append(f"group '{group}' must be a string between 1 and 60 characters")

        aliases = entry.get("aliases")
        if not isinstance(aliases, list) or not all(isinstance(a, str) and 2 <= len(a) <= 120 for a in aliases):
            problems.append(f"aliases for '{title}' must be strings between 2 and 120 characters")
            aliases = []
        if len(set(aliases)) != len(aliases):
            problems.append(f"aliases for '{title}' has duplicates")

        if not isinstance(entry.get("active"), bool):
            problems.append(f"active for '{title}' must be true or false")

        if isinstance(title, str):
            for name in [title, *[a for a in aliases if isinstance(a, str)]]:
                key = " ".join(name.lower().split())
                if key in seen:
                    problems.append(f"duplicate: '{name}' also appears under '{seen[key]}'")
                else:
                    seen[key] = title

    for word in exclude_words:
        for key, owner in seen.items():
            if word.lower() in key:
                problems.append(f"exclude word '{word}' would block title '{owner}'")

    return problems


async def load_titles_doc(github: GitHubClient) -> dict:
    """Read `config/titles.json` from `BASE_BRANCH` on GitHub.

    The deployed Worker only bundles `api/src`, so it can't read repository
    files by path (see CLAUDE.md's "Things that have broken before"). This
    also means the panel always shows what's actually on `BASE_BRANCH`.
    """
    result = await github.get_file(TITLES_PATH, BASE_BRANCH)
    if result is None:
        raise GitHubApiError(f"{TITLES_PATH} was not found on {BASE_BRANCH}")
    content, _sha = result
    return json.loads(content)


async def open_or_update_titles_pr(github: GitHubClient, doc: dict) -> dict:
    """Write `doc` to `config/titles.json` on a dedicated branch, and open or
    update the one pull request that changes it. Returns the pull request.
    """
    content = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"

    await github.ensure_branch(TITLES_BRANCH, BASE_BRANCH)
    await github.put_file(TITLES_PATH, TITLES_BRANCH, content, "Update titles from the dashboard")

    existing_pr = await github.find_open_pr(TITLES_BRANCH)
    if existing_pr is not None:
        return existing_pr

    return await github.create_pr(
        TITLES_BRANCH,
        BASE_BRANCH,
        "Update titles from the dashboard",
        "Henry edited titles on the dashboard. This changes only `config/titles.json`.",
    )
