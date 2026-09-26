"""Careers-page reading (issue #53, phase 6 part 1): Cloudflare Browser Run,
its backup switching rules, and the robots.txt check. No real network or
Playwright calls -- every test injects a fake `browser_run_fetch`,
`backup_fetch`, and/or `robots_check`.
"""
from __future__ import annotations

import threading
import time

import pytest

from job.fetchers import careers_pages as cp


def _company(company_id: int, url: str = "https://acme.example/careers") -> dict:
    return {"id": company_id, "careers_url": url}


def _always_allowed(url, *, session=None) -> bool:
    return True


def _never_allowed(url, *, session=None) -> bool:
    return False


# ---------------------------------------------------------------------------
# fetch_via_browser_run / retry
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text
        self.ok = 200 <= status_code < 400

    def json(self):
        if self._json_body is None:
            raise ValueError("no JSON body")
        return self._json_body


class _FakeSession:
    def __init__(self, responses=None, post_fn=None):
        self._responses = list(responses or [])
        self._post_fn = post_fn
        self.post_calls: list[dict] = []

    def post(self, url, *, json, headers, timeout):
        self.post_calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self._post_fn:
            return self._post_fn(url, json, headers, timeout)
        return self._responses.pop(0)


def test_browser_run_success_sends_only_the_url_and_returns_markdown():
    # Cloudflare's current Browser Run docs weren't reachable while building
    # this (see careers_pages.py's module docstring) -- the request body is
    # deliberately just {"url": ...}, no wait-for-content option yet.
    session = _FakeSession([_FakeResponse(200, {"success": True, "result": "# Jobs\n\n- PM Intern"})])
    text, minutes, measured_by = cp.fetch_via_browser_run("https://acme.example/careers", account_id="acc", token="tok", session=session)
    assert text == "# Jobs\n\n- PM Intern"
    assert minutes is not None and minutes >= 0
    assert measured_by == cp.MEASURED_BY_ELAPSED
    assert session.post_calls[0]["json"] == {"url": "https://acme.example/careers"}
    assert session.post_calls[0]["headers"]["Authorization"] == "Bearer tok"


def test_browser_run_uses_reported_browser_time_when_present():
    session = _FakeSession([_FakeResponse(200, {
        "success": True, "result": {"markdown": "content", "browserTimeSeconds": 30},
    })])
    _, minutes, measured_by = cp.fetch_via_browser_run("https://acme.example/careers", account_id="acc", token="tok", session=session)
    assert minutes == pytest.approx(0.5)
    assert measured_by == cp.MEASURED_BY_CLOUDFLARE_HEADER


def test_browser_run_blocked_403():
    session = _FakeSession([_FakeResponse(403)])
    with pytest.raises(cp.BrowserRunBlocked):
        cp.fetch_via_browser_run("https://acme.example/careers", account_id="acc", token="tok", session=session)


def test_browser_run_quota_exceeded_429():
    session = _FakeSession([_FakeResponse(429)])
    with pytest.raises(cp.BrowserRunQuotaExceeded):
        cp.fetch_via_browser_run("https://acme.example/careers", account_id="acc", token="tok", session=session)


def test_browser_run_quota_message_in_200_response():
    session = _FakeSession([_FakeResponse(200, {"success": False, "errors": [{"message": "quota exceeded for this account"}]})])
    with pytest.raises(cp.BrowserRunQuotaExceeded):
        cp.fetch_via_browser_run("https://acme.example/careers", account_id="acc", token="tok", session=session)


def test_browser_run_server_error_5xx():
    session = _FakeSession([_FakeResponse(503)])
    with pytest.raises(cp.BrowserRunServerError):
        cp.fetch_via_browser_run("https://acme.example/careers", account_id="acc", token="tok", session=session)


def test_browser_run_empty_content():
    session = _FakeSession([_FakeResponse(200, {"success": True, "result": "   "})])
    with pytest.raises(cp.BrowserRunEmpty):
        cp.fetch_via_browser_run("https://acme.example/careers", account_id="acc", token="tok", session=session)


def test_retry_wrapper_calls_the_real_fetch_twice_on_timeout(monkeypatch):
    calls = []

    def flaky(url, *, account_id, token, session=None, timeout=60):
        calls.append(url)
        raise cp.BrowserRunTimeout("timed out")

    monkeypatch.setattr(cp, "fetch_via_browser_run", flaky)
    with pytest.raises(cp.BrowserRunTimeout):
        cp.fetch_via_browser_run_with_retry("https://acme.example/careers", account_id="acc", token="tok")
    assert len(calls) == 2


def test_retry_wrapper_succeeds_on_second_attempt(monkeypatch):
    calls = {"n": 0}

    def flaky(url, *, account_id, token, session=None, timeout=60):
        calls["n"] += 1
        if calls["n"] == 1:
            raise cp.BrowserRunTimeout("timed out")
        return "content", 1.0, cp.MEASURED_BY_ELAPSED

    monkeypatch.setattr(cp, "fetch_via_browser_run", flaky)
    text, minutes, measured_by = cp.fetch_via_browser_run_with_retry("https://acme.example/careers", account_id="acc", token="tok")
    assert text == "content"
    assert calls["n"] == 2
    assert measured_by == cp.MEASURED_BY_ELAPSED


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

def test_robots_disallow():
    session = _FakeSession([_FakeResponse(200, text="User-agent: *\nDisallow: /careers")])
    session.get = lambda url, headers, timeout: _FakeResponse(200, text="User-agent: *\nDisallow: /careers")
    assert cp.robots_allowed("https://acme.example/careers", session=session) is False


def test_robots_allow_when_no_disallow_rule():
    session = _FakeSession()
    session.get = lambda url, headers, timeout: _FakeResponse(200, text="User-agent: *\nAllow: /")
    assert cp.robots_allowed("https://acme.example/careers", session=session) is True


def test_robots_allow_when_robots_txt_missing():
    session = _FakeSession()
    session.get = lambda url, headers, timeout: _FakeResponse(404, text="")
    assert cp.robots_allowed("https://acme.example/careers", session=session) is True


# ---------------------------------------------------------------------------
# run_careers_pages orchestration
# ---------------------------------------------------------------------------

def _outcome_by_company(outcome: cp.CareersRunOutcome) -> dict[int, cp.PageOutcome]:
    return {p.company_id: p for p in outcome.pages}


def test_normal_page_loads_via_browser_run():
    companies = [_company(1)]

    def fetch(url, *, account_id, token, session=None):
        return "# Jobs\n- PM Intern", 1.0

    outcome = cp.run_careers_pages(
        companies, account_id="acc", token="tok", robots_check=_always_allowed, browser_run_fetch=fetch,
    )
    pages = _outcome_by_company(outcome)
    assert pages[1].ok is True
    assert pages[1].method == "browser_run"
    assert outcome.switch_reason is None


def test_disallowed_page_skips_browser_run_and_backup_entirely():
    calls = {"browser_run": 0, "backup": 0}

    def browser_run_fetch(url, **kwargs):
        calls["browser_run"] += 1
        return "text", 1.0

    def backup_fetch(url):
        calls["backup"] += 1
        return "text"

    outcome = cp.run_careers_pages(
        [_company(1)], account_id="acc", token="tok", robots_check=_never_allowed,
        browser_run_fetch=browser_run_fetch, backup_fetch=backup_fetch,
    )
    page = outcome.pages[0]
    assert page.ok is False
    assert page.reason == cp.REASON_DISALLOWED
    assert calls == {"browser_run": 0, "backup": 0}


def test_blocked_page_fails_and_is_never_retried_by_backup():
    def browser_run_fetch(url, **kwargs):
        raise cp.BrowserRunBlocked("blocked")

    backup_calls = []

    def backup_fetch(url):
        backup_calls.append(url)
        return "text"

    outcome = cp.run_careers_pages(
        [_company(1)], account_id="acc", token="tok", robots_check=_always_allowed,
        browser_run_fetch=browser_run_fetch, backup_fetch=backup_fetch,
    )
    page = outcome.pages[0]
    assert page.ok is False
    assert page.reason == cp.REASON_BLOCKED
    assert page.method is None
    assert backup_calls == []


def test_timeout_fails_with_reason_timeout():
    def browser_run_fetch(url, **kwargs):
        raise cp.BrowserRunTimeout("timed out")

    outcome = cp.run_careers_pages(
        [_company(1)], account_id="acc", token="tok", robots_check=_always_allowed, browser_run_fetch=browser_run_fetch,
    )
    page = outcome.pages[0]
    assert page.ok is False
    assert page.reason == cp.REASON_TIMEOUT


def test_empty_page_fails_with_reason_no_content():
    def browser_run_fetch(url, **kwargs):
        raise cp.BrowserRunEmpty("empty")

    outcome = cp.run_careers_pages(
        [_company(1)], account_id="acc", token="tok", robots_check=_always_allowed, browser_run_fetch=browser_run_fetch,
    )
    page = outcome.pages[0]
    assert page.ok is False
    assert page.reason == cp.REASON_NO_CONTENT


def test_no_more_than_3_browser_run_requests_in_flight_at_once():
    lock = threading.Lock()
    state = {"current": 0, "max": 0}

    def fetch(url, *, account_id, token, session=None):
        with lock:
            state["current"] += 1
            state["max"] = max(state["max"], state["current"])
        time.sleep(0.05)
        with lock:
            state["current"] -= 1
        return "content", 0.1

    companies = [_company(i, f"https://acme{i}.example/careers") for i in range(7)]
    outcome = cp.run_careers_pages(
        companies, account_id="acc", token="tok", robots_check=_always_allowed, browser_run_fetch=fetch,
    )
    assert state["max"] <= 3
    assert len(outcome.pages) == 7


def test_careers_pages_per_run_caps_pages_loaded():
    companies = [_company(i, f"https://acme{i}.example/careers") for i in range(5)]

    def fetch(url, *, account_id, token, session=None):
        return "content", 0.1

    outcome = cp.run_careers_pages(
        companies, account_id="acc", token="tok", pages_per_run=3, robots_check=_always_allowed, browser_run_fetch=fetch,
    )
    assert len(outcome.pages) == 3


def test_quota_exceeded_switches_remaining_pages_to_backup():
    companies = [_company(i, f"https://acme{i}.example/careers") for i in range(4)]
    browser_run_calls = []

    def browser_run_fetch(url, **kwargs):
        browser_run_calls.append(url)
        if url == companies[0]["careers_url"]:
            raise cp.BrowserRunQuotaExceeded("quota exceeded")
        return "content", 0.1

    backup_calls = []

    def backup_fetch(url):
        backup_calls.append(url)
        return "backup content"

    outcome = cp.run_careers_pages(
        companies, account_id="acc", token="tok", max_concurrency=1, robots_check=_always_allowed,
        browser_run_fetch=browser_run_fetch, backup_fetch=backup_fetch,
    )
    assert outcome.switch_reason == "quota"
    pages = _outcome_by_company(outcome)
    assert pages[companies[0]["id"]].reason == cp.REASON_QUOTA_EXCEEDED
    # every company after the one that triggered the switch used the backup
    assert backup_calls == [c["careers_url"] for c in companies[1:]]
    assert browser_run_calls == [companies[0]["careers_url"]]


def test_reaching_daily_minutes_budget_switches_to_backup():
    companies = [_company(i, f"https://acme{i}.example/careers") for i in range(4)]

    def browser_run_fetch(url, **kwargs):
        return "content", 5.0  # each page "costs" 5 minutes

    backup_calls = []

    def backup_fetch(url):
        backup_calls.append(url)
        return "backup content"

    outcome = cp.run_careers_pages(
        companies, account_id="acc", token="tok", max_concurrency=1, daily_minutes_budget=9.0,
        robots_check=_always_allowed, browser_run_fetch=browser_run_fetch, backup_fetch=backup_fetch,
    )
    assert outcome.switch_reason == "minutes"
    # the first 2 pages (10 minutes) push past the 9-minute budget
    assert len(backup_calls) == 2


def test_three_consecutive_server_errors_switch_to_backup():
    companies = [_company(i, f"https://acme{i}.example/careers") for i in range(5)]

    def browser_run_fetch(url, **kwargs):
        if url in [c["careers_url"] for c in companies[:3]]:
            raise cp.BrowserRunServerError("boom")
        return "content", 0.1

    backup_calls = []

    def backup_fetch(url):
        backup_calls.append(url)
        return "backup content"

    outcome = cp.run_careers_pages(
        companies, account_id="acc", token="tok", max_concurrency=1, robots_check=_always_allowed,
        browser_run_fetch=browser_run_fetch, backup_fetch=backup_fetch,
    )
    assert outcome.switch_reason == "outage"
    assert backup_calls == [c["careers_url"] for c in companies[3:]]


def test_two_server_errors_then_success_do_not_switch():
    companies = [_company(i, f"https://acme{i}.example/careers") for i in range(3)]

    def browser_run_fetch(url, **kwargs):
        if url in [c["careers_url"] for c in companies[:2]]:
            raise cp.BrowserRunServerError("boom")
        return "content", 0.1

    outcome = cp.run_careers_pages(
        companies, account_id="acc", token="tok", max_concurrency=1, robots_check=_always_allowed,
        browser_run_fetch=browser_run_fetch,
    )
    assert outcome.switch_reason is None
    pages = _outcome_by_company(outcome)
    assert pages[companies[2]["id"]].ok is True
    assert pages[companies[2]["id"]].method == "browser_run"


def test_missing_token_sends_every_page_to_backup():
    companies = [_company(1)]
    browser_run_calls = []

    def browser_run_fetch(url, **kwargs):
        browser_run_calls.append(url)
        return "content", 1.0

    backup_calls = []

    def backup_fetch(url):
        backup_calls.append(url)
        return "backup content"

    outcome = cp.run_careers_pages(
        companies, account_id="acc", token=None, robots_check=_always_allowed,
        browser_run_fetch=browser_run_fetch, backup_fetch=backup_fetch,
    )
    assert outcome.switch_reason == "missing_token"
    assert browser_run_calls == []
    assert backup_calls == [companies[0]["careers_url"]]
    assert outcome.pages[0].ok is True
    assert outcome.pages[0].method == "backup"


def test_robots_disallowed_page_is_never_loaded_by_the_backup():
    companies = [_company(1)]
    backup_calls = []

    def backup_fetch(url):
        backup_calls.append(url)
        return "backup content"

    outcome = cp.run_careers_pages(
        companies, account_id=None, token=None, robots_check=_never_allowed, backup_fetch=backup_fetch,
    )
    assert outcome.pages[0].reason == cp.REASON_DISALLOWED
    assert backup_calls == []


def test_careers_backup_pages_per_run_reached_falls_back_to_discovery():
    companies = [_company(i, f"https://acme{i}.example/careers") for i in range(3)]
    backup_calls = []

    def backup_fetch(url):
        backup_calls.append(url)
        return "backup content"

    outcome = cp.run_careers_pages(
        companies, account_id=None, token=None, backup_pages_per_run=1,
        robots_check=_always_allowed, backup_fetch=backup_fetch,
    )
    pages = _outcome_by_company(outcome)
    assert pages[companies[0]["id"]].ok is True
    assert pages[companies[1]["id"]].ok is False
    assert pages[companies[1]["id"]].reason == cp.REASON_BACKUP_CAPACITY
    assert pages[companies[2]["id"]].ok is False
    assert len(backup_calls) == 1


def test_outcome_counts_pages_and_failures_per_method():
    # issue #55: browser_run_usage needs per-method pages/failures without
    # having to reconstruct them from `pages` (a blocked/disallowed page
    # deliberately carries no `method`, see test_blocked_page_fails_and_is_never_retried_by_backup).
    companies = [_company(i, f"https://acme{i}.example/careers") for i in range(3)]

    def browser_run_fetch(url, **kwargs):
        if url == companies[0]["careers_url"]:
            raise cp.BrowserRunQuotaExceeded("quota exceeded")
        return "content", 0.1, cp.MEASURED_BY_ELAPSED

    def backup_fetch(url):
        if url == companies[2]["careers_url"]:
            raise RuntimeError("boom")
        return "backup content"

    outcome = cp.run_careers_pages(
        companies, account_id="acc", token="tok", max_concurrency=1, robots_check=_always_allowed,
        browser_run_fetch=browser_run_fetch, backup_fetch=backup_fetch,
    )
    # company[0]'s quota error switches the rest of the run to the backup, so
    # only company[0] is ever attempted via Browser Run.
    assert outcome.browser_run_pages == 0
    assert outcome.browser_run_failures == 1
    assert outcome.backup_pages == 1
    assert outcome.backup_failures == 1


def test_measured_by_propagates_to_the_page_outcome():
    def browser_run_fetch(url, **kwargs):
        return "content", 0.5, cp.MEASURED_BY_CLOUDFLARE_HEADER

    outcome = cp.run_careers_pages(
        [_company(1)], account_id="acc", token="tok", robots_check=_always_allowed, browser_run_fetch=browser_run_fetch,
    )
    assert outcome.pages[0].measured_by == cp.MEASURED_BY_CLOUDFLARE_HEADER


def test_a_two_tuple_fetch_callable_still_works_with_no_measured_by():
    # Backward compatible with a fetch callable (test double or otherwise)
    # that doesn't report `measured_by` at all.
    def browser_run_fetch(url, **kwargs):
        return "content", 0.5

    outcome = cp.run_careers_pages(
        [_company(1)], account_id="acc", token="tok", robots_check=_always_allowed, browser_run_fetch=browser_run_fetch,
    )
    assert outcome.pages[0].ok is True
    assert outcome.pages[0].measured_by is None


def test_careers_backup_disabled_sends_failures_straight_to_discovery():
    companies = [_company(i, f"https://acme{i}.example/careers") for i in range(2)]

    def browser_run_fetch(url, **kwargs):
        raise cp.BrowserRunQuotaExceeded("quota exceeded")

    backup_calls = []

    def backup_fetch(url):
        backup_calls.append(url)
        return "backup content"

    outcome = cp.run_careers_pages(
        companies, account_id="acc", token="tok", max_concurrency=1, backup_enabled=False,
        robots_check=_always_allowed, browser_run_fetch=browser_run_fetch, backup_fetch=backup_fetch,
    )
    pages = _outcome_by_company(outcome)
    assert all(not p.ok for p in pages.values())
    assert pages[companies[1]["id"]].reason == cp.REASON_BACKUP_DISABLED
    assert backup_calls == []
