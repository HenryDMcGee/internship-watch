import asyncio
import base64
import json

import pytest

from src.github_client import GitHubClient
from src.titles import BASE_BRANCH, TITLES_BRANCH, TITLES_PATH, open_or_update_titles_pr, validate_titles_doc

VALID_DOC = {
    "version": 1,
    "exclude_words": ["marketing"],
    "titles": [
        {"title": "MBA Product Manager Intern", "group": "Core PM", "aliases": [], "active": True},
    ],
}


def run(coro):
    return asyncio.run(coro)


def test_validate_titles_doc_accepts_valid_document():
    assert validate_titles_doc(VALID_DOC) == []


def test_validate_titles_doc_rejects_wrong_version():
    doc = dict(VALID_DOC, version=2)
    assert any("version" in p for p in validate_titles_doc(doc))


def test_validate_titles_doc_rejects_duplicate_titles():
    doc = dict(VALID_DOC, titles=VALID_DOC["titles"] * 2)
    assert any("duplicate" in p for p in validate_titles_doc(doc))


def test_validate_titles_doc_rejects_exclude_word_that_blocks_a_title():
    doc = dict(VALID_DOC, exclude_words=["product"])
    problems = validate_titles_doc(doc)
    assert any("would block" in p for p in problems)


def test_validate_titles_doc_rejects_missing_fields():
    assert validate_titles_doc({"version": 1}) != []


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, method, url, *, token, json_body=None):
        self.calls.append((method, url, json_body))
        return self.responses.pop(0)


def test_open_or_update_titles_pr_creates_branch_and_pr_when_none_exist():
    transport = ScriptedTransport([
        (404, {}),  # ensure_branch: get branch -> missing
        (200, {"object": {"sha": "base-sha"}}),  # ensure_branch: get base sha
        (201, {}),  # ensure_branch: create ref
        (404, {}),  # put_file: get_file -> missing
        (201, {"content": {"sha": "new-sha"}}),  # put_file: PUT
        (200, []),  # find_open_pr -> none open
        (201, {"number": 9, "html_url": "https://github.com/x/y/pull/9"}),  # create_pr
    ])
    client = GitHubClient(repo="HenryDMcGee/internship-watch", token="tok", transport=transport)

    pr = run(open_or_update_titles_pr(client, VALID_DOC))
    assert pr["number"] == 9

    put_call = transport.calls[4]
    assert put_call[0] == "PUT"
    written = json.loads(base64.b64decode(put_call[2]["content"]).decode("utf-8"))
    assert written == VALID_DOC
    assert put_call[2]["branch"] == TITLES_BRANCH


def test_open_or_update_titles_pr_reuses_existing_pr():
    transport = ScriptedTransport([
        (200, {"object": {"sha": "existing"}}),  # ensure_branch: branch exists already
        (404, {}),  # put_file: get_file -> missing
        (201, {}),  # put_file: PUT
        (200, [{"number": 3, "html_url": "https://github.com/x/y/pull/3"}]),  # find_open_pr -> exists
    ])
    client = GitHubClient(repo="HenryDMcGee/internship-watch", token="tok", transport=transport)

    pr = run(open_or_update_titles_pr(client, VALID_DOC))
    assert pr["number"] == 3
    # No create_pr call was needed: exactly 4 calls were made.
    assert len(transport.calls) == 4
