"""Turns Claude's daily-improvement title suggestions into a pull request
that edits `config/titles.json`, reusing the dashboard's validation
(`api/src/titles.py`) so Claude's suggestions are held to the same rules
Henry's own edits are. Runs on its own branch so it never collides with a
dashboard-initiated edit (`titles-update-from-dashboard`).
"""
from __future__ import annotations

import copy
import json

import job.config  # noqa: F401 -- import side effect: adds api/src to sys.path
from github_client import GitHubApiError, GitHubClient
from titles import validate_titles_doc

TITLE_SUGGESTIONS_BRANCH = "titles-update-from-claude"
BASE_BRANCH = "master"
TITLES_PATH = "config/titles.json"


def apply_title_suggestions(current_doc: dict, suggestions: list[dict]) -> tuple[dict, list[str]]:
    """Apply Claude's suggested additions, aliases, and deactivations to
    `current_doc`. Returns the new document and a human-readable change
    note per suggestion actually applied (for the pull request body). The
    caller still validates the result before opening a pull request.
    """
    doc = copy.deepcopy(current_doc)
    notes: list[str] = []
    by_title = {t["title"]: t for t in doc.get("titles", [])}

    for suggestion in suggestions:
        action = suggestion.get("action")
        title = suggestion.get("title")
        reason = suggestion.get("reason") or ""

        if action == "add_title" and title and title not in by_title:
            entry = {
                "title": title,
                "group": suggestion.get("group") or "Suggested by Claude",
                "aliases": suggestion.get("aliases") or [],
                "active": True,
            }
            doc["titles"].append(entry)
            by_title[title] = entry
            notes.append(f'Add title "{title}". {reason}'.strip())
        elif action == "add_alias" and title in by_title:
            alias = suggestion.get("alias")
            if alias and alias not in by_title[title]["aliases"]:
                by_title[title]["aliases"].append(alias)
                notes.append(f'Add alias "{alias}" to "{title}". {reason}'.strip())
        elif action == "deactivate" and title in by_title and by_title[title]["active"]:
            by_title[title]["active"] = False
            notes.append(f'Deactivate "{title}". {reason}'.strip())

    return doc, notes


async def open_title_suggestions_pr(github: GitHubClient, current_doc: dict, suggestions: list[dict]) -> dict | None:
    """Opens or updates the pull request for Claude's title suggestions.
    Returns None (and does nothing) if there's nothing to change, or if the
    result wouldn't pass validation.
    """
    if not suggestions:
        return None
    new_doc, notes = apply_title_suggestions(current_doc, suggestions)
    if not notes:
        return None
    problems = validate_titles_doc(new_doc)
    if problems:
        raise GitHubApiError(f"Claude's title suggestions would produce an invalid titles.json: {problems}")

    content = json.dumps(new_doc, indent=2, ensure_ascii=False) + "\n"
    await github.ensure_branch(TITLE_SUGGESTIONS_BRANCH, BASE_BRANCH)
    await github.put_file(TITLES_PATH, TITLE_SUGGESTIONS_BRANCH, content, "Claude: suggest title changes")

    body = (
        "Claude's daily improvement step suggests these changes to `config/titles.json`:\n\n"
        + "\n".join(f"- {note}" for note in notes)
        + "\n\nReview and merge to apply, or close to reject."
    )
    existing_pr = await github.find_open_pr(TITLE_SUGGESTIONS_BRANCH)
    if existing_pr is not None:
        return existing_pr
    return await github.create_pr(TITLE_SUGGESTIONS_BRANCH, BASE_BRANCH, "Claude: suggested title changes", body)
