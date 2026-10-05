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

import functools
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse
from urllib import robotparser

import requests

from job.fetchers.base import USER_AGENT
from job.playwright_runtime import load_page_text

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
REASON_RATE_LIMITED = "Browser Run rate limited (HTTP 429)"
REASON_TOKEN_REJECTED = "Cloudflare rejected the Browser Run token"
REASON_BACKUP_DISABLED = "Browser Run unavailable and the backup is off"
REASON_BACKUP_CAPACITY = "backup page cap reached for this run"
REASON_ERROR = "error"

MEASURED_BY_CLOUDFLARE_HEADER = "cloudflare_header"
MEASURED_BY_ELAPSED = "elapsed"

# Phrases in Cloudflare's own error that say the daily allowance is used up (issue
# #63). A bare HTTP 429 is NOT one of these: it is a rate limit, which clears by
# waiting. Only an error that says the allowance is gone switches to the backup.
_DAILY_LIMIT_PHRASES = (
    "for today", "daily", "browser time", "quota", "usage limit", "allowance", "free plan", "minutes",
)
# Words in a 403's message that mean the target site refused the page, rather than
# Cloudflare refusing our token.
_SITE_BLOCK_PHRASES = ("captcha", "blocked by", "target", "bot protection", "challenge")

MAX_RATE_LIMIT_RETRIES = 3
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 5.0
MAX_RATE_LIMIT_WAIT_SECONDS = 60.0


class BrowserRunError(RuntimeError):
    """Base class for a single Browser Run request failing."""


class BrowserRunBlocked(BrowserRunError):
    pass


class BrowserRunQuotaExceeded(BrowserRunError):
    """Cloudflare says the daily allowance is used up."""


class BrowserRunRateLimited(BrowserRunError):
    """HTTP 429 without a daily-limit message: wait `retry_after` seconds and try again."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class BrowserRunAuthError(BrowserRunError):
    """api.cloudflare.com answered 401 or 403: the token is missing, expired, or
    lacks the Browser Rendering permission. Not a site blocking the page.
    """

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


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

def _looks_like_daily_limit_message(message: str) -> bool:
    lowered = (message or "").lower()
    return any(phrase in lowered for phrase in _DAILY_LIMIT_PHRASES)


def _looks_like_rate_limit_message(message: str) -> bool:
    lowered = (message or "").lower()
    return "rate limit" in lowered or "too many requests" in lowered


def _error_message(resp) -> str:
    """Cloudflare's own error text from a non-2xx response, if it sent any."""
    try:
        payload = resp.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    errors = payload.get("errors") or []
    return "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors)


def _retry_after_seconds(resp) -> float | None:
    raw = (getattr(resp, "headers", None) or {}).get("Retry-After")
    try:
        return max(float(raw), 0.0) if raw is not None else None
    except (TypeError, ValueError):
        return None  # an HTTP-date Retry-After isn't worth parsing; the default wait applies


def fetch_via_browser_run(
    url: str, *, account_id: str, token: str, session: "requests.Session | None" = None,
    timeout: int = BROWSER_RUN_TIMEOUT_SECONDS,
) -> tuple[str, float | None, str]:
    """POSTs to Cloudflare's Browser Rendering `/markdown` endpoint. Returns
    `(markdown_text, browser_minutes, measured_by)` -- `browser_minutes` is
    Cloudflare's own reported browser time when the response includes it
    (`measured_by = "cloudflare_header"`), otherwise this call's own elapsed
    wall-clock time (`measured_by = "elapsed"`, the issue's fallback for when
    the response doesn't say -- recorded as such in `browser_run_usage`,
    issue #55).
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

    if resp.status_code in (401, 403):
        message = _error_message(resp)
        if resp.status_code == 403 and any(p in message.lower() for p in _SITE_BLOCK_PHRASES):
            raise BrowserRunBlocked(f"Browser Run was blocked (403) fetching {url}: {message}")
        raise BrowserRunAuthError(
            f"Cloudflare API returned HTTP {resp.status_code} for the Browser Run token"
            + (f": {message}" if message else ""),
            resp.status_code,
        )
    if resp.status_code == 429:
        message = _error_message(resp)
        if message and _looks_like_daily_limit_message(message) and not _looks_like_rate_limit_message(message):
            raise BrowserRunQuotaExceeded(f"Browser Run's daily allowance is used up ({message})")
        raise BrowserRunRateLimited(
            f"Browser Run returned HTTP 429 for {url}" + (f": {message}" if message else ""),
            retry_after=_retry_after_seconds(resp),
        )
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
        if _looks_like_rate_limit_message(message):
            raise BrowserRunRateLimited(f"Browser Run reported a rate limit for {url}: {message}")
        if _looks_like_daily_limit_message(message):
            raise BrowserRunQuotaExceeded(f"Browser Run reported a quota error for {url}: {message}")
        raise BrowserRunServerError(f"Browser Run reported an error for {url}: {message}")

    result = payload.get("result")
    if isinstance(result, str):
        text = result
        seconds = None
    elif isinstance(result, dict):
        text = result.get("markdown") or result.get("content") or ""
        seconds = result.get("browserTimeSeconds") or (result.get("meta") or {}).get("browserTimeSeconds")
    else:
        text = ""
        seconds = None

    if seconds is not None:
        browser_minutes = float(seconds) / 60.0
        measured_by = MEASURED_BY_CLOUDFLARE_HEADER
    else:
        browser_minutes = elapsed_minutes
        measured_by = MEASURED_BY_ELAPSED

    if not text or not text.strip():
        raise BrowserRunEmpty(f"Browser Run returned empty content for {url}")

    return text, browser_minutes, measured_by


class Throttle:
    """Shared slow-down for a run: once Browser Run rate-limits us, later
    batches wait first and go one page at a time (issue #63).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.delay = 0.0
        self.rate_limited = 0

    def note_rate_limit(self, retry_after: float | None) -> None:
        wait = retry_after if retry_after is not None else DEFAULT_RATE_LIMIT_WAIT_SECONDS
        with self._lock:
            self.rate_limited += 1
            self.delay = min(max(self.delay * 2, wait), MAX_RATE_LIMIT_WAIT_SECONDS)


def fetch_via_browser_run_with_retry(
    url: str, *, account_id: str, token: str, session: "requests.Session | None" = None,
    timeout: int = BROWSER_RUN_TIMEOUT_SECONDS, sleep: Callable[[float], None] = time.sleep,
    throttle: "Throttle | None" = None,
) -> tuple[str, float | None, str]:
    """One retry for a timeout (issue: "60-second timeout per page, with one
    retry"). An HTTP 429 is a rate limit, not a verdict: wait as long as
    Retry-After says (or a short default that doubles), then try again, up to
    `MAX_RATE_LIMIT_RETRIES` times, telling `throttle` so the rest of the run
    slows down too. Every other failure (blocked, daily allowance used up, a
    server error, empty content) is a real answer from Browser Run, not a
    fluke worth retrying.
    """
    timed_out = False
    rate_limit_retries = 0
    while True:
        try:
            return fetch_via_browser_run(url, account_id=account_id, token=token, session=session, timeout=timeout)
        except BrowserRunTimeout:
            if timed_out:
                raise
            timed_out = True
        except BrowserRunRateLimited as exc:
            if throttle is not None:
                throttle.note_rate_limit(exc.retry_after)
            if rate_limit_retries >= MAX_RATE_LIMIT_RETRIES:
                raise
            wait = exc.retry_after if exc.retry_after is not None else DEFAULT_RATE_LIMIT_WAIT_SECONDS * 2 ** rate_limit_retries
            rate_limit_retries += 1
            sleep(min(wait, MAX_RATE_LIMIT_WAIT_SECONDS))


# ---------------------------------------------------------------------------
# Playwright backup
# ---------------------------------------------------------------------------

def fetch_via_backup(url: str) -> str:
    """Loads `url` with Playwright's Chromium -- the same browser, installed in
    the same environment, as `job/verify/browser.py`'s status checks. It runs
    on its own thread because Playwright's sync API won't start inside the
    job's asyncio loop, which is why the backup failed on every page before
    issue #63 (see `job/playwright_runtime.py`).
    """
    return load_page_text(url, timeout_ms=20000)


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
    # "cloudflare_header" or "elapsed" -- only ever set for a successful
    # browser_run page (issue #55, test case 13). None for a backup page (it
    # doesn't draw on the Browser Run minutes budget at all) and for a
    # failure (nothing was measured).
    measured_by: str | None = None


@dataclass
class CareersRunOutcome:
    pages: list[PageOutcome]
    browser_run_minutes_used: float
    # "quota", "minutes", "outage", or "missing_token" -- None if Browser Run
    # was never abandoned this run. Only "quota"/"minutes" are a Workers
    # Paid capacity problem; the daily summary's suggestion is limited to
    # those (issue #53, test case 24).
    switch_reason: str | None = None
    # Pages successfully loaded/failed via each method this run -- "failed"
    # here means an attempt was actually made and came back an error, not a
    # page that never reached that method at all (disallowed by robots.txt,
    # or backup skipped because it's disabled/at its per-run cap). Used to
    # write `browser_run_usage` (issue #55); kept on the outcome rather than
    # recomputed from `pages` because a blocked/disallowed page deliberately
    # has no `method` set (see PageOutcome.method), so it can't be
    # reconstructed from `pages` alone.
    browser_run_pages: int = 0
    browser_run_failures: int = 0
    backup_pages: int = 0
    backup_failures: int = 0
    # HTTP 429s Browser Run answered with this run (each one waited and retried,
    # none switched to the backup), and the Cloudflare API's own 401/403 text
    # when the token was rejected (issue #63).
    rate_limit_events: int = 0
    token_problem: str | None = None

    @property
    def attempted(self) -> int:
        return len(self.pages)


def _classify_browser_run_call(
    url: str, *, account_id: str, token: str, session, fetch,
) -> tuple[str, str | None, float | None, str | None, str | None]:
    try:
        result = fetch(url, account_id=account_id, token=token, session=session)
        # Tolerates a caller's (or test's) fetch callable still returning the
        # older `(text, minutes)` shape -- only the real `fetch_via_browser_run`
        # itself is required to report `measured_by` (issue #55).
        if len(result) >= 3:
            text, minutes, measured_by = result[0], result[1], result[2]
        else:
            text, minutes = result
            measured_by = None
        return ("ok", text, minutes, measured_by, None)
    except BrowserRunBlocked as exc:
        return ("blocked", None, None, None, str(exc))
    except BrowserRunQuotaExceeded as exc:
        return ("quota_exceeded", None, None, None, str(exc))
    except BrowserRunRateLimited as exc:
        return ("rate_limited", None, None, None, str(exc))
    except BrowserRunAuthError as exc:
        return ("auth_error", None, None, None, str(exc))
    except BrowserRunTimeout as exc:
        return ("timeout", None, None, None, str(exc))
    except BrowserRunEmpty as exc:
        return ("no_content", None, None, None, str(exc))
    except BrowserRunServerError as exc:
        return ("server_error", None, None, None, str(exc))
    except Exception as exc:  # noqa: BLE001 - an unexpected Browser Run bug must not stop the run
        return ("server_error", None, None, None, str(exc))


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
    minutes_used_before: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> CareersRunOutcome:
    """Reads each company's careers page (`companies` must already be
    filtered to ones with a `careers_url` and no recognized feed), up to
    `pages_per_run` pages total.

    Switches from Browser Run to the backup, for the rest of this run, on
    whichever comes first (issue #63 narrowed these):
      - `CLOUDFLARE_BROWSER_TOKEN`/`account_id` missing -- backup from the start
      - Cloudflare's error says the daily allowance is used up
      - the minutes used today (`minutes_used_before` from earlier runs, plus
        this run's) reach `daily_minutes_budget`
      - Cloudflare's API rejects the token (401 or 403) -- reported as a token
        problem, never as a site blocking the page
      - 3 Browser Run requests in a row failing with a server or network
        error (5xx, connection errors, timeouts don't count here -- see
        `fetch_via_browser_run_with_retry`)

    An HTTP 429 is only a rate limit: it waits for Retry-After, retries, and
    slows the rest of the run down, and never switches to the backup.

    A page blocked by the site or disallowed by robots.txt is never sent to
    the backup either way -- it's a site decision, not a Browser Run capacity
    problem, so retrying it anywhere would be trying to get around a block.
    """
    throttle = Throttle()
    if browser_run_fetch is fetch_via_browser_run_with_retry:
        browser_run_fetch = functools.partial(fetch_via_browser_run_with_retry, throttle=throttle, sleep=sleep)
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
    browser_run_ok = 0
    browser_run_failed = 0
    backup_ok = 0
    backup_failed = 0
    token_problem: str | None = None

    def read_with_backup(company: dict) -> None:
        nonlocal backup_used, backup_ok, backup_failed
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
                backup_failed += 1
                # The underlying error, per page, so a backup that fails everywhere
                # says why (issue #63) instead of one generic "error".
                detail = f"{type(exc).__name__}: {str(exc).strip()[:200]}"
                print(f"careers backup: {url}: {detail}")
                pages.append(PageOutcome(company_id, url, ok=False, reason=f"{REASON_ERROR}: backup failed ({detail})"))
            else:
                if not text or not text.strip():
                    backup_failed += 1
                    pages.append(PageOutcome(company_id, url, ok=False, reason=REASON_NO_CONTENT))
                else:
                    backup_ok += 1
                    pages.append(PageOutcome(company_id, url, ok=True, text=text, method="backup"))

    i = 0
    n = len(allowed)
    while i < n:
        if mode == "browser_run":
            if throttle.delay:
                sleep(throttle.delay)
            # Once rate limited, one page at a time instead of three.
            batch_size = 1 if throttle.rate_limited else max_concurrency
            batch = allowed[i : i + batch_size]
            urls = [c["careers_url"] for c in batch]
            results = _run_browser_run_batch(
                urls, account_id=account_id, token=token, session=session,
                fetch=browser_run_fetch, max_workers=batch_size,
            )
            switched_pages: list[dict] = []  # not the site's fault: read these with the backup instead
            for company, (kind, text, minutes, measured_by, err) in zip(batch, results):
                company_id, url = company["id"], company["careers_url"]
                if kind == "ok":
                    minutes_used += minutes or 0.0
                    browser_run_ok += 1
                    pages.append(PageOutcome(
                        company_id, url, ok=True, text=text, method="browser_run",
                        browser_minutes=minutes, measured_by=measured_by,
                    ))
                    consecutive_server_errors = 0
                    if mode == "browser_run" and minutes_used_before + minutes_used >= daily_minutes_budget:
                        mode = "backup"
                        switch_reason = switch_reason or "minutes"
                    continue
                browser_run_failed += 1
                if kind == "blocked":
                    pages.append(PageOutcome(company_id, url, ok=False, reason=REASON_BLOCKED))
                elif kind == "timeout":
                    pages.append(PageOutcome(company_id, url, ok=False, reason=REASON_TIMEOUT))
                elif kind == "no_content":
                    pages.append(PageOutcome(company_id, url, ok=False, reason=REASON_NO_CONTENT))
                elif kind == "rate_limited":
                    # Waited and retried already; still limited. The page fails for now,
                    # and no switch to the backup -- a rate limit isn't a used-up allowance.
                    pages.append(PageOutcome(company_id, url, ok=False, reason=REASON_RATE_LIMITED))
                    if not throttle.rate_limited:  # a custom `browser_run_fetch` doesn't report to the throttle
                        throttle.note_rate_limit(None)
                elif kind == "quota_exceeded":
                    switched_pages.append(company)
                    if mode == "browser_run":
                        mode = "backup"
                        switch_reason = "quota"
                elif kind == "auth_error":
                    switched_pages.append(company)
                    token_problem = token_problem or err
                    if mode == "browser_run":
                        mode = "backup"
                        switch_reason = "token"
                else:  # server_error
                    consecutive_server_errors += 1
                    pages.append(PageOutcome(company_id, url, ok=False, reason=REASON_ERROR))
                    if consecutive_server_errors >= 3 and mode == "browser_run":
                        mode = "backup"
                        switch_reason = "outage"
            for company in switched_pages:
                read_with_backup(company)
            i += len(batch)
        else:
            read_with_backup(allowed[i])
            i += 1

    return CareersRunOutcome(
        pages=pages, browser_run_minutes_used=minutes_used, switch_reason=switch_reason,
        browser_run_pages=browser_run_ok, browser_run_failures=browser_run_failed,
        backup_pages=backup_ok, backup_failures=backup_failed,
        rate_limit_events=throttle.rate_limited, token_problem=token_problem,
    )
