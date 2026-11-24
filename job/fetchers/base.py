"""Shared helpers for feed fetchers."""
from __future__ import annotations

import re
from typing import Any

import requests

DEFAULT_TIMEOUT = 20
USER_AGENT = "internship-watch (private daily job; contact via github.com/HenryDMcGee/internship-watch)"


class FetchError(RuntimeError):
    """Raised when a company's feed can't be read.

    Caught per-company in `job/run.py` so one broken feed never stops the
    rest of the run. `status` is the HTTP status when the site answered, and
    `retry_after` the seconds it asked us to wait, when it said (BR-004).
    """

    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


_RATE_LIMIT_RE = re.compile(r"\bHTTP 429\b")
_SERVER_ERROR_RE = re.compile(r"\bHTTP 5\d\d\b")
_NO_ANSWER_RE = re.compile(r"^request to \S+ failed:")


def is_rate_limit_error(error: str | None) -> bool:
    """The site told us to slow down. Says nothing about the address."""
    return bool(error) and bool(_RATE_LIMIT_RE.search(error))


def is_temporary_error(error: str | None) -> bool:
    """The site gave no real answer: it told us to slow down, it had a fault
    of its own, or it did not answer at all. None of these is evidence about
    the address, so none of them is a reason to drop a source (BR-004.2).
    """
    if not error:
        return False
    return bool(_RATE_LIMIT_RE.search(error) or _SERVER_ERROR_RE.search(error) or _NO_ANSWER_RE.search(error))


def _retry_after_seconds(resp) -> float | None:
    """`Retry-After` as seconds. The header may also be a date; that, and
    anything else unreadable, counts as the site naming no wait."""
    headers = getattr(resp, "headers", None) or {}
    try:
        seconds = float(headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _status_error(url: str, resp) -> FetchError:
    return FetchError(
        f"{url} returned HTTP {resp.status_code}", status=resp.status_code, retry_after=_retry_after_seconds(resp),
    )


def get_json(url: str, *, params: dict | None = None, session: "requests.Session | None" = None) -> Any:
    session = session or requests
    try:
        resp = session.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise FetchError(f"request to {url} failed: {exc}") from exc
    if resp.status_code == 403:
        raise FetchError(f"{url} blocked the request (403); not retrying or working around it", status=403)
    if not resp.ok:
        raise _status_error(url, resp)
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
        raise FetchError(f"{url} blocked the request (403); not retrying or working around it", status=403)
    if not resp.ok:
        raise _status_error(url, resp)
    try:
        return resp.json()
    except ValueError as exc:
        raise FetchError(f"{url} did not return JSON") from exc
