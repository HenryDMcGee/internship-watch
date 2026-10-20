"""A small GitHub REST API client for Check now and the titles pull request flow.

Runs inside the Worker via `js.fetch`, the same pattern `access.py`'s
`fetch_jwks` uses, since `requests` needs real sockets that Pyodide doesn't
have. Tests inject a fake `transport` so the GitHub API can be mocked under
plain CPython (see tests/test_github_client.py and tests/test_titles_pr.py).
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

GITHUB_API = "https://api.github.com"

Transport = Callable[..., Awaitable[tuple[int, Any]]]


class GitHubApiError(RuntimeError):
    pass


async def _default_transport(method: str, url: str, *, token: str, json_body: dict | None = None) -> tuple[int, Any]:
    from js import fetch, Object  # type: ignore  # provided by the Workers runtime
    from pyodide.ffi import to_js  # type: ignore

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "internship-watch-worker",
    }
    options: dict[str, Any] = {"method": method, "headers": headers}
    if json_body is not None:
        options["body"] = json.dumps(json_body)
    response = await fetch(url, to_js(options, dict_converter=Object.fromEntries))
    status = response.status
    text = await response.text()
    try:
        body = json.loads(text) if text else {}
    except ValueError:
        body = {"raw": text}
    return status, body


@dataclass
class GitHubClient:
    repo: str  # "HenryDMcGee/internship-watch"
    token: str
    transport: Transport = field(default=_default_transport)

    async def _request(self, method: str, path: str, json_body: dict | None = None) -> tuple[int, Any]:
        return await self.transport(method, f"{GITHUB_API}{path}", token=self.token, json_body=json_body)

    async def dispatch_workflow(self, workflow_file: str, ref: str, inputs: dict) -> int:
        """Starts a workflow_dispatch run and returns GitHub's status (204 when accepted)."""
        status, body = await self._request(
            "POST", f"/repos/{self.repo}/actions/workflows/{workflow_file}/dispatches",
            {"ref": ref, "inputs": inputs},
        )
        if status != 204:
            raise GitHubApiError(f"starting the workflow failed ({status}): {body}")
        return status

    async def get_file(self, path: str, ref: str) -> tuple[str, str] | None:
        """Return (content, sha) for a file on `ref`, or None if it doesn't exist there."""
        status, body = await self._request("GET", f"/repos/{self.repo}/contents/{path}?ref={ref}")
        if status == 404:
            return None
        if status != 200:
            raise GitHubApiError(f"reading {path}@{ref} failed ({status}): {body}")
        content = base64.b64decode(body["content"]).decode("utf-8")
        return content, body["sha"]

    async def get_branch_sha(self, branch: str) -> str | None:
        status, body = await self._request("GET", f"/repos/{self.repo}/git/ref/heads/{branch}")
        if status == 404:
            return None
        if status != 200:
            raise GitHubApiError(f"reading branch {branch} failed ({status}): {body}")
        return body["object"]["sha"]

    async def ensure_branch(self, branch: str, base: str) -> None:
        """Create `branch` from `base` if it doesn't already exist. Leaves an existing branch alone."""
        if await self.get_branch_sha(branch) is not None:
            return
        base_sha = await self.get_branch_sha(base)
        if base_sha is None:
            raise GitHubApiError(f"base branch {base} does not exist")
        status, body = await self._request(
            "POST", f"/repos/{self.repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": base_sha}
        )
        if status != 201:
            raise GitHubApiError(f"creating branch {branch} failed ({status}): {body}")

    async def put_file(self, path: str, branch: str, content: str, message: str) -> None:
        existing = await self.get_file(path, branch)
        body = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if existing is not None:
            body["sha"] = existing[1]
        status, resp_body = await self._request("PUT", f"/repos/{self.repo}/contents/{path}", body)
        if status not in (200, 201):
            raise GitHubApiError(f"writing {path} failed ({status}): {resp_body}")

    async def find_open_pr(self, branch: str) -> dict | None:
        owner = self.repo.split("/")[0]
        status, body = await self._request("GET", f"/repos/{self.repo}/pulls?head={owner}:{branch}&state=open")
        if status != 200:
            raise GitHubApiError(f"listing pull requests failed ({status}): {body}")
        return body[0] if body else None

    async def create_pr(self, branch: str, base: str, title: str, body_text: str) -> dict:
        status, body = await self._request(
            "POST", f"/repos/{self.repo}/pulls", {"title": title, "head": branch, "base": base, "body": body_text}
        )
        if status != 201:
            raise GitHubApiError(f"creating the pull request failed ({status}): {body}")
        return body

    async def list_open_issues(self, label: str) -> list[dict]:
        """Open issues carrying `label` (pull requests excluded), across pages."""
        issues: list[dict] = []
        for page in range(1, 11):
            status, body = await self._request(
                "GET", f"/repos/{self.repo}/issues?state=open&labels={label}&per_page=100&page={page}"
            )
            if status != 200 or not isinstance(body, list):
                raise GitHubApiError(f"listing issues failed ({status}): {body}")
            issues += [i for i in body if "pull_request" not in i]
            if len(body) < 100:
                break
        return issues

    async def create_issue(self, title: str, body_text: str, labels: list[str]) -> dict:
        status, body = await self._request(
            "POST", f"/repos/{self.repo}/issues", {"title": title, "body": body_text, "labels": labels}
        )
        if status != 201:
            raise GitHubApiError(f"creating the issue failed ({status}): {body}")
        return body
