import asyncio

import pytest

from src.github_client import GitHubApiError, GitHubClient


def run(coro):
    return asyncio.run(coro)


class ScriptedTransport:
    """A fake transport that returns queued (status, body) pairs and records calls."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, method, url, *, token, json_body=None):
        self.calls.append((method, url, json_body))
        return self.responses.pop(0)


def make_client(responses):
    transport = ScriptedTransport(responses)
    client = GitHubClient(repo="HenryDMcGee/internship-watch", token="tok", transport=transport)
    return client, transport


def test_dispatch_workflow_success():
    client, transport = make_client([(204, {})])
    run(client.dispatch_workflow("check-now.yml", "main", {"request_id": "1"}))
    method, url, body = transport.calls[0]
    assert method == "POST"
    assert url.endswith("/repos/HenryDMcGee/internship-watch/actions/workflows/check-now.yml/dispatches")
    assert body == {"ref": "main", "inputs": {"request_id": "1"}}


def test_dispatch_workflow_failure_raises():
    client, _ = make_client([(404, {"message": "not found"})])
    with pytest.raises(GitHubApiError):
        run(client.dispatch_workflow("check-now.yml", "main", {}))


def test_ensure_branch_creates_when_missing():
    # get branch -> 404, get base -> 200, create ref -> 201
    client, transport = make_client([
        (404, {}),
        (200, {"object": {"sha": "base-sha"}}),
        (201, {"ref": "refs/heads/titles-update-from-dashboard"}),
    ])
    run(client.ensure_branch("titles-update-from-dashboard", "main"))
    assert transport.calls[-1][0] == "POST"
    assert transport.calls[-1][2] == {"ref": "refs/heads/titles-update-from-dashboard", "sha": "base-sha"}


def test_ensure_branch_leaves_existing_branch_alone():
    client, transport = make_client([(200, {"object": {"sha": "existing-sha"}})])
    run(client.ensure_branch("titles-update-from-dashboard", "main"))
    assert len(transport.calls) == 1


def test_put_file_creates_when_no_existing_file():
    client, transport = make_client([
        (404, {}),  # get_file
        (201, {"content": {"sha": "new-sha"}}),  # PUT
    ])
    run(client.put_file("config/titles.json", "branch", "{}", "message"))
    method, url, body = transport.calls[-1]
    assert method == "PUT"
    assert "sha" not in body


def test_put_file_updates_when_file_exists():
    client, transport = make_client([
        (200, {"content": "e30=", "sha": "old-sha"}),  # get_file: base64 for "{}"
        (200, {"content": {"sha": "new-sha"}}),  # PUT
    ])
    run(client.put_file("config/titles.json", "branch", "{}", "message"))
    method, url, body = transport.calls[-1]
    assert body["sha"] == "old-sha"


def test_find_open_pr_returns_first_match():
    client, _ = make_client([(200, [{"number": 5, "html_url": "https://github.com/x/y/pull/5"}])])
    pr = run(client.find_open_pr("titles-update-from-dashboard"))
    assert pr["number"] == 5


def test_find_open_pr_none_when_empty():
    client, _ = make_client([(200, [])])
    assert run(client.find_open_pr("titles-update-from-dashboard")) is None


def test_create_pr_returns_body():
    client, _ = make_client([(201, {"number": 6, "html_url": "https://github.com/x/y/pull/6"})])
    pr = run(client.create_pr("branch", "main", "title", "body"))
    assert pr["number"] == 6


def test_list_open_issues_asks_for_open_labeled_issues_and_skips_pull_requests():
    client, transport = make_client([(200, [
        {"number": 1, "html_url": "https://github.com/x/y/issues/1", "body": "Feedback row: 1"},
        {"number": 2, "html_url": "https://github.com/x/y/pull/2", "body": "", "pull_request": {}},
    ])])
    issues = run(client.list_open_issues("visitor-feedback"))
    assert [i["number"] for i in issues] == [1]
    method, url, _ = transport.calls[0]
    assert method == "GET"
    assert "/repos/HenryDMcGee/internship-watch/issues" in url
    assert "state=open" in url and "labels=visitor-feedback" in url


def test_list_open_issues_follows_pages_and_raises_on_failure():
    full_page = [{"number": n, "html_url": f"u{n}", "body": ""} for n in range(100)]
    client, transport = make_client([(200, full_page), (200, [{"number": 100, "html_url": "u100", "body": ""}])])
    assert len(run(client.list_open_issues("visitor-feedback"))) == 101
    assert "page=2" in transport.calls[1][1]
    client, _ = make_client([(500, {"message": "boom"})])
    with pytest.raises(GitHubApiError):
        run(client.list_open_issues("visitor-feedback"))
