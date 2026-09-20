"""Shared helpers for feed fetchers."""
from __future__ import annotations

from typing import Any

import requests

DEFAULT_TIMEOUT = 20
USER_AGENT = "internship-watch (private daily job; contact via github.com/HenryDMcGee/internship-watch)"


class FetchError(RuntimeError):
    """Raised when a company's feed can't be read.

    Caught per-company in `job/run.py` so one broken feed never stops the
    rest of the run.
    """


def get_json(url: str, *, params: dict | None = None, session: "requests.Session | None" = None) -> Any:
    session = session or requests
    try:
        resp = session.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise FetchError(f"request to {url} failed: {exc}") from exc
    if resp.status_code == 403:
        raise FetchError(f"{url} blocked the request (403); not retrying or working around it")
    if not resp.ok:
        raise FetchError(f"{url} returned HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise FetchError(f"{url} did not return JSON") from exc


def post_json(url: str, *, json_body: dict, session: "requests.Session | None" = None) -> Any:
    session = session or requests
    try:
        resp = session.post(url, json=json_body, headers={"User-Agent": USER_AGENT}, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise FetchError(f"request to {url} failed: {exc}") from exc
    if resp.status_code == 403:
        raise FetchError(f"{url} blocked the request (403); not retrying or working around it")
    if not resp.ok:
        raise FetchError(f"{url} returned HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise FetchError(f"{url} did not return JSON") from exc
