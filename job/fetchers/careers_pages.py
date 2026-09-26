"""Reads a company's careers page for companies with a `careers_url` on file
and no recognized job feed (issue #53, phase 6 part 1). Cloudflare Browser
Run is tried first; Playwright on the GitHub Actions runner (already
installed for the daily status checks, `job/verify/browser.py`) is the
backup for when Browser Run can't be used. See `run_careers_pages`'s
docstring for the switch-to-backup rules.

Cloudflare's current Browser Run docs weren't reachable while building this
(the tool this was built with wasn't granted permission to fetch web pages in
this run), so `fetch_via_browser_run` sends only `{"url": url}` -- no
wait-for-content option -- per the issue's own fallback for exactly this
case. A page whose listings only render after scripts run (test case 2 in
issue #53) is left for Henry to confirm against Cloudflare's docs and wire
in as a follow-up; until then such a page will read as empty and fail with
reason "no content", same as a page that's actually blank.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse
from urllib import robotparser

import requests

from job.fetchers.base import USER_AGENT

BROWSER_RUN_TIMEOUT_SECONDS = 60
MAX_BROWSER_RUN_CONCURRENCY = 3  # the free plan's concurrency limit

DEFAULT_CAREERS_PAGES_PER_RUN = 60
DEFAULT_BROWSER_RUN_DAILY_MINUTES = 9.0
DEFAULT_CAREERS_BACKUP_PAGES_PER_RUN = 40
DEFAULT_CAREERS_BACKUP_ENABLED = True

_ROBOTS_TIMEOUT = 10

# REASON_BLOCKED/TIMEOUT/NO_CONTENT are the exact strings issue #53 itself
# quotes; everything else is free-form but kept short and consistent.
REASON_BLOCKED = "blocked"
REASON_TIMEOUT = "timeout"
REASON_NO_CONTENT = "no content"
REASON_DISALLOWED = "disallowed by robots.txt"
REASON_QUOTA_EXCEEDED = "Browser Run quota exceeded"
REASON_BACKUP_DISABLED = "Browser Run unavailable and the backup is off"
REASON_BACKUP_CAPACITY = "backup page cap reached for this run"
REASON_ERROR = "error"

_QUOTA_PHRASES = ("quota", "usage limit", "rate limit")


class BrowserRunError(RuntimeError):
    """Base class for a single Browser Run request failing."""


class BrowserRunBlocked(BrowserRunError):
    pass


class BrowserRunQuotaExceeded(BrowserRunError):
    pass


class BrowserRunServerError(BrowserRunError):
    pass


class BrowserRunTimeout(BrowserRunError):
    pass


class BrowserRunEmpty(BrowserRunError):
    pass


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

def robots_allowed(url: str, *, session: "requests.Session | None" = None) -> bool:
    """Whether `url` is allowed by its site's robots.txt. A robots.txt that
    can't be fetched (no file, network error, non-2xx/3xx/4xx-not-404-ish
    response) is treated as allowing everything -- the same default every
    well-behaved crawler uses for a missing robots.txt.
    """
    session = session or requests
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = session.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=_ROBOTS_TIMEOUT)
    except requests.RequestException:
        return True
    if resp.status_code >= 400:
        return True
    parser = robotparser.RobotFileParser()
    parser.parse(resp.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


# ---------------------------------------------------------------------------
# Browser Run
# ---------------------------------------------------------------------------

def _looks_like_quota_message(message: str) -> bool:
    lowered = (message or "").lower()
    return any(phrase in lowered for phrase in _QUOTA_PHRASES)


def fetch_via_browser_run(
    url: str, *, account_id: str, token: str, session: "requests.Session | None" = None,
    timeout: int = BROWSER_RUN_TIMEOUT_SECONDS,
) -> tuple[str, float | None]:
    """POSTs to Cloudflare's Browser Rendering `/markdown` endpoint. Returns
    `(markdown_text, browser_minutes)` -- `browser_minutes` is Cloudflare's
    own reported browser time when the response includes it, otherwise this
    call's own elapsed wall-clock time (the issue's fallback for when the
    response doesn't say).
    """
    session = session or requests
    endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/markdown"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
    }
    started = time.monotonic()
    try:
        resp = session.post(endpoint, json={"url": url}, headers=headers, timeout=timeout)
    except requests.Timeout as exc:
        raise BrowserRunTimeout(f"Browser Run timed out fetching {url}") from exc
    except requests.RequestException as exc:
        raise BrowserRunServerError(f"Browser Run request to {url} failed: {exc}") from exc
    elapsed_minutes = (time.monotonic() - started) / 60.0

    if resp.status_code == 403:
        raise BrowserRunBlocked(f"Browser Run was blocked (403) fetching {url}")
    if resp.status_code == 429:
        raise BrowserRunQuotaExceeded(f"Browser Run returned HTTP 429 for {url}")
    if resp.status_code >= 500:
        raise BrowserRunServerError(f"Browser Run returned HTTP {resp.status_code} for {url}")
    if not resp.ok:
        raise BrowserRunServerError(f"Browser Run returned HTTP {resp.status_code} for {url}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise BrowserRunServerError(f"Browser Run did not return JSON for {url}") from exc

    if not payload.get("success", True):
        errors = payload.get("errors") or []
        message = "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors) or "unknown error"
        if _looks_like_quota_message(message):
            raise BrowserRunQuotaExceeded(f"Browser Run reported a quota error for {url}: {message}")
        raise BrowserRunServerError(f"Browser Run reported an error for {url}: {message}")

    result = payload.get("result")
    if isinstance(result, str):
        text = result
        browser_minutes = None
    elif isinstance(result, dict):
        text = result.get("markdown") or result.get("content") or ""
        seconds = result.get("browserTimeSeconds") or (result.get("meta") or {}).get("browserTimeSeconds")
        browser_minutes = (float(seconds) / 60.0) if seconds is not None else None
    else:
        text = ""
        browser_minutes = None

    if browser_minutes is None:
        browser_minutes = elapsed_minutes

    if not text or not text.strip():
        raise BrowserRunEmpty(f"Browser Run returned empty content for {url}")

    return text, browser_minutes


def fetch_via_browser_run_with_retry(
    url: str, *, account_id: str, token: str, session: "requests.Session | None" = None,
    timeout: int = BROWSER_RUN_TIMEOUT_SECONDS,
) -> tuple[str, float | None]:
    """One retry, and only for a timeout (issue: "60-second timeout per page,
    with one retry"). Every other failure (blocked, quota, a server error,
    empty content) is a real answer from Browser Run, not a fluke worth
    retrying.
    """
    try:
        return fetch_via_browser_run(url, account_id=account_id, token=token, session=session, timeout=timeout)
    except BrowserRunTimeout:
        return fetch_via_browser_run(url, account_id=account_id, token=token, session=session, timeout=timeout)


# ---------------------------------------------------------------------------
# Playwright backup
# ---------------------------------------------------------------------------

def fetch_via_backup(url: str) -> str:
    """Loads `url` with Playwright's Chromium, same browser already installed
    on the runner for `job/verify/browser.py`'s status checks. Imported
    lazily so importing this module doesn't require Playwright to be
    installed (only the real daily job does).
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, wait_until="networkidle", timeout=20000)
            return page.inner_text("body")
        finally:
            browser.close()


# ---------------------------------------------------------------------------
# Per-run orchestration
# ---------------------------------------------------------------------------

@dataclass
class PageOutcome:
    company_id: int
    url: str
    ok: bool
    text: str | None = None
    method: str | None = None  # "browser_run" or "backup"
    browser_minutes: float | None = None
    reason: str | None = None


@dataclass
class CareersRunOutcome:
    pages: list[PageOutcome]
    browser_run_minutes_used: float
    # "quota", "minutes", "outage", or "missing_token" -- None if Browser Run
    # was never abandoned this run. Only "quota"/"minutes" are a Workers
    # Paid capacity problem; the daily summary's suggestion is limited to
    # those (issue #53, test case 24).
    switch_reason: str | None = None


def _classify_browser_run_call(url: str, *, account_id: str, token: str, session, fetch) -> tuple[str, str | None, float | None, str | None]:
    try:
        text, minutes = fetch(url, account_id=account_id, token=token, session=session)
        return ("ok", text, minutes, None)
    except BrowserRunBlocked as exc:
        return ("blocked", None, None, str(exc))
    except BrowserRunQuotaExceeded as exc:
        return ("quota_exceeded", None, None, str(exc))
    except BrowserRunTimeout as exc:
        return ("timeout", None, None, str(exc))
    except BrowserRunEmpty as exc:
        return ("no_content", None, None, str(exc))
    except BrowserRunServerError as exc:
        return ("server_error", None, None, str(exc))
    except Exception as exc:  # noqa: BLE001 - an unexpected Browser Run bug must not stop the run
        return ("server_error", None, None, str(exc))


def _run_browser_run_batch(urls: list[str], *, account_id, token, session, fetch, max_workers: int):
    # `max_workers` bounds how many of these run at once (issue: "At most 3
    # pages at a time, the free plan's concurrency limit") -- callers never
    # pass more than `max_workers` urls in one batch, so this never exceeds
    # it either.
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(
            lambda u: _classify_browser_run_call(u, account_id=account_id, token=token, session=session, fetch=fetch),
            urls,
        ))


def run_careers_pages(
    companies: list[dict],
    *,
    account_id: str | None,
    token: str | None,
    pages_per_run: int = DEFAULT_CAREERS_PAGES_PER_RUN,
    daily_minutes_budget: float = DEFAULT_BROWSER_RUN_DAILY_MINUTES,
    backup_enabled: bool = DEFAULT_CAREERS_BACKUP_ENABLED,
    backup_pages_per_run: int = DEFAULT_CAREERS_BACKUP_PAGES_PER_RUN,
    session: "requests.Session | None" = None,
    browser_run_fetch: Callable = fetch_via_browser_run_with_retry,
    backup_fetch: Callable = fetch_via_backup,
    robots_check: Callable = robots_allowed,
    max_concurrency: int = MAX_BROWSER_RUN_CONCURRENCY,
) -> CareersRunOutcome:
    """Reads each company's careers page (`companies` must already be
    filtered to ones with a `careers_url` and no recognized feed), up to
    `pages_per_run` pages total.

    Switches from Browser Run to the backup, for the rest of this run, on
    whichever comes first:
      - `CLOUDFLARE_BROWSER_TOKEN`/`account_id` missing -- backup from the start
      - a quota/usage-limit response (HTTP 429, or a reported quota error)
      - `daily_minutes_budget` of Browser Run time used
      - 3 Browser Run requests in a row failing with a server or network
        error (5xx, connection errors, timeouts don't count here -- see
        `fetch_via_browser_run_with_retry`)

    A page blocked (403) or disallowed by robots.txt is never sent to the
    backup either way -- it's a site decision, not a Browser Run capacity
    problem, so retrying it anywhere would be trying to get around a block.
    """
    candidates = [c for c in companies if c.get("careers_url")][:pages_per_run]

    pages: list[PageOutcome] = []
    allowed: list[dict] = []
    for company in candidates:
        url = company["careers_url"]
        if robots_check(url, session=session):
            allowed.append(company)
        else:
            pages.append(PageOutcome(company_id=company["id"], url=url, ok=False, reason=REASON_DISALLOWED))

    mode = "browser_run"
    switch_reason: str | None = None
    if not account_id or not token:
        mode = "backup"
        switch_reason = "missing_token"

    minutes_used = 0.0
    consecutive_server_errors = 0
    backup_used = 0

    i = 0
    n = len(allowed)
    while i < n:
        if mode == "browser_run":
            batch = allowed[i : i + max_concurrency]
            urls = [c["careers_url"] for c in batch]
            results = _run_browser_run_batch(
                urls, account_id=account_id, token=token, session=session,
                fetch=browser_run_fetch, max_workers=max_concurrency,
            )
            for company, (kind, text, minutes, _err) in zip(batch, results):
                company_id, url = company["id"], company["careers_url"]
                if kind == "ok":
                    minutes_used += minutes or 0.0
                    pages.append(PageOutcome(company_id, url, ok=True, text=text, method="browser_run", browser_minutes=minutes))
                    consecutive_server_errors = 0
                    if minutes_used >= daily_minutes_budget:
                        mode = "backup"
                        switch_reason = switch_reason or "minutes"
                elif kind == "blocked":
                    pages.append(PageOutcome(company_id, url, ok=False, reason=REASON_BLOCKED))
                elif kind == "timeout":
                    pages.append(PageOutcome(company_id, url, ok=False, reason=REASON_TIMEOUT))
                elif kind == "no_content":
                    pages.append(PageOutcome(company_id, url, ok=False, reason=REASON_NO_CONTENT))
                elif kind == "quota_exceeded":
                    pages.append(PageOutcome(company_id, url, ok=False, reason=REASON_QUOTA_EXCEEDED))
                    mode = "backup"
                    switch_reason = "quota"
                else:  # server_error
                    consecutive_server_errors += 1
                    pages.append(PageOutcome(company_id, url, ok=False, reason=REASON_ERROR))
                    if consecutive_server_errors >= 3:
                        mode = "backup"
                        switch_reason = "outage"
            i += len(batch)
        else:
            company = allowed[i]
            company_id, url = company["id"], company["careers_url"]
            if not backup_enabled:
                pages.append(PageOutcome(company_id, url, ok=False, reason=REASON_BACKUP_DISABLED))
            elif backup_used >= backup_pages_per_run:
                pages.append(PageOutcome(company_id, url, ok=False, reason=REASON_BACKUP_CAPACITY))
            else:
                backup_used += 1
                try:
                    text = backup_fetch(url)
                except Exception as exc:  # noqa: BLE001 - a blocked/unavailable site must not stop the run
                    pages.append(PageOutcome(company_id, url, ok=False, reason=REASON_ERROR))
                else:
                    if not text or not text.strip():
                        pages.append(PageOutcome(company_id, url, ok=False, reason=REASON_NO_CONTENT))
                    else:
                        pages.append(PageOutcome(company_id, url, ok=True, text=text, method="backup"))
            i += 1

    return CareersRunOutcome(pages=pages, browser_run_minutes_used=minutes_used, switch_reason=switch_reason)
