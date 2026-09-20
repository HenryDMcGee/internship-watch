import asyncio
import base64
import json

from job.agent.titles_pr import TITLE_SUGGESTIONS_BRANCH, apply_title_suggestions, open_title_suggestions_pr
from github_client import GitHubClient

CURRENT_DOC = {
    "version": 1,
    "exclude_words": [],
    "titles": [{"title": "PM Intern", "group": "Core PM", "aliases": [], "active": True}],
}


def run(coro):
    return asyncio.run(coro)


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, method, url, *, token, json_body=None):
        self.calls.append((method, url, json_body))
        return self.responses.pop(0)


def test_apply_title_suggestions_adds_new_title():
    doc, notes = apply_title_suggestions(CURRENT_DOC, [
        {"action": "add_title", "title": "Growth PM Intern", "group": "Core PM", "reason": "seen twice"},
    ])
    assert any(t["title"] == "Growth PM Intern" for t in doc["titles"])
    assert "Growth PM Intern" in notes[0]
    # The original document is untouched.
    assert len(CURRENT_DOC["titles"]) == 1


def test_apply_title_suggestions_adds_alias_to_existing_title():
    doc, notes = apply_title_suggestions(CURRENT_DOC, [
        {"action": "add_alias", "title": "PM Intern", "alias": "Product Manager Intern"},
    ])
    entry = next(t for t in doc["titles"] if t["title"] == "PM Intern")
    assert "Product Manager Intern" in entry["aliases"]
    assert notes


def test_apply_title_suggestions_deactivates_title():
    doc, notes = apply_title_suggestions(CURRENT_DOC, [{"action": "deactivate", "title": "PM Intern"}])
    entry = next(t for t in doc["titles"] if t["title"] == "PM Intern")
    assert entry["active"] is False
    assert notes


def test_apply_title_suggestions_ignores_unknown_title_for_alias_or_deactivate():
    doc, notes = apply_title_suggestions(CURRENT_DOC, [
        {"action": "add_alias", "title": "Not A Real Title", "alias": "x"},
        {"action": "deactivate", "title": "Not A Real Title"},
    ])
    assert doc == CURRENT_DOC
    assert notes == []


def test_open_title_suggestions_pr_returns_none_with_no_suggestions():
    client = GitHubClient(repo="x/y", token="tok", transport=ScriptedTransport([]))
    assert run(open_title_suggestions_pr(client, CURRENT_DOC, [])) is None


def test_open_title_suggestions_pr_returns_none_when_nothing_applies():
    client = GitHubClient(repo="x/y", token="tok", transport=ScriptedTransport([]))
    suggestions = [{"action": "deactivate", "title": "Not A Real Title"}]
    assert run(open_title_suggestions_pr(client, CURRENT_DOC, suggestions)) is None


def test_open_title_suggestions_pr_creates_branch_and_pr():
    transport = ScriptedTransport([
        (404, {}),  # ensure_branch: get branch -> missing
        (200, {"object": {"sha": "base-sha"}}),  # ensure_branch: get base sha
        (201, {}),  # ensure_branch: create ref
        (404, {}),  # put_file: get_file -> missing
        (201, {"content": {"sha": "new-sha"}}),  # put_file: PUT
        (200, []),  # find_open_pr -> none open
        (201, {"number": 42, "html_url": "https://github.com/x/y/pull/42"}),  # create_pr
    ])
    client = GitHubClient(repo="HenryDMcGee/internship-watch", token="tok", transport=transport)
    suggestions = [{"action": "add_title", "title": "Growth PM Intern", "group": "Core PM", "reason": "seen twice"}]

    pr = run(open_title_suggestions_pr(client, CURRENT_DOC, suggestions))
    assert pr["number"] == 42

    put_call = transport.calls[4]
    assert put_call[0] == "PUT"
    assert put_call[2]["branch"] == TITLE_SUGGESTIONS_BRANCH
    written = json.loads(base64.b64decode(put_call[2]["content"]).decode("utf-8"))
    assert any(t["title"] == "Growth PM Intern" for t in written["titles"])


def test_open_title_suggestions_pr_reuses_existing_pr():
    transport = ScriptedTransport([
        (200, {"object": {"sha": "existing"}}),  # ensure_branch: branch exists already
        (404, {}),  # put_file: get_file -> missing
        (201, {}),  # put_file: PUT
        (200, [{"number": 7, "html_url": "https://github.com/x/y/pull/7"}]),  # find_open_pr -> exists
    ])
    client = GitHubClient(repo="HenryDMcGee/internship-watch", token="tok", transport=transport)
    suggestions = [{"action": "deactivate", "title": "PM Intern"}]

    pr = run(open_title_suggestions_pr(client, CURRENT_DOC, suggestions))
    assert pr["number"] == 7
    assert len(transport.calls) == 4
