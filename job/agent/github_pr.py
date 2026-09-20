"""A `requests`-based transport for `github_client.GitHubClient`, so the
daily job (plain CPython, real sockets) can reuse the same tested GitHub
API client the Worker uses over `js.fetch` under Pyodide.
"""
from __future__ import annotations

import os
from typing import Any

import requests

import job.config  # noqa: F401 -- import side effect: adds api/src to sys.path
from github_client import GitHubClient


async def requests_transport(method: str, url: str, *, token: str, json_body: dict | None = None) -> tuple[int, Any]:
    resp = requests.request(
        method, url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "internship-watch-job",
        },
        json=json_body, timeout=30,
    )
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}
    return resp.status_code, body


def github_client_from_env(repo: str = "HenryDMcGee/internship-watch") -> GitHubClient | None:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None
    return GitHubClient(repo=repo, token=token, transport=requests_transport)
