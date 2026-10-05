"""Browser Run errors and the Playwright backup (issue #63). No real network or browser."""
import asyncio

import pytest

from job import playwright_runtime
from job.fetchers import careers_pages as cp


class _Response:
    def __init__(self, status_code=200, json_body=None, headers=None):
        self.status_code = status_code
        self._json_body = json_body
        self.headers = headers or {}
        self.ok = 200 <= status_code < 400

    def json(self):
        if self._json_body is None:
            raise ValueError("no JSON body")
        return self._json_body


class _Session:
    def __init__(self, responses):
        self._responses = list(responses)
        self.posts = []

    def post(self, url, *, json, headers, timeout):
        self.posts.append(json["url"])
        return self._responses.pop(0)


def _ok(text="Open roles"):
    return _Response(200, {"success": True, "result": {"markdown": text, "browserTimeSeconds": 6}})


def _company(i):
    return {"id": i, "careers_url": f"https://acme{i}.example/careers"}


def _run(companies, session, backup_calls, sleeps, **kwargs):
    def backup_fetch(url):
        backup_calls.append(url)
        return "backup text"

    return cp.run_careers_pages(
        companies, account_id="acc", token="tok", session=session, backup_fetch=backup_fetch,
        robots_check=lambda url, *, session=None: True, sleep=sleeps.append, max_concurrency=1, **kwargs,
    )


# -- 429 is a rate limit, not a used-up allowance -------------------------------

def test_a_429_waits_for_retry_after_retries_and_does_not_switch_to_the_backup():
    session = _Session([_Response(429, headers={"Retry-After": "3"}), _ok()])
    backup_calls, sleeps = [], []

    outcome = _run([_company(0)], session, backup_calls, sleeps)

    assert sleeps == [3.0]
    assert session.posts == [_company(0)["careers_url"]] * 2
    assert outcome.switch_reason is None
    assert backup_calls == []
    page = outcome.pages[0]
    assert page.ok and page.method == "browser_run"
    assert outcome.rate_limit_events == 1


def test_a_429_without_retry_after_backs_off_and_still_does_not_switch():
    session = _Session([_Response(429), _Response(429), _ok()])
    backup_calls, sleeps = [], []

    outcome = _run([_company(0)], session, backup_calls, sleeps)

    assert sleeps == [5.0, 10.0]
    assert outcome.switch_reason is None and backup_calls == []
    assert outcome.pages[0].ok


def test_a_page_that_stays_rate_limited_fails_for_now_and_the_run_slows_down():
    always_429 = [_Response(429, headers={"Retry-After": "2"}) for _ in range(cp.MAX_RATE_LIMIT_RETRIES + 1)]
    session = _Session(always_429 + [_ok()])
    backup_calls, sleeps = [], []

    outcome = _run([_company(0), _company(1)], session, backup_calls, sleeps)

    assert outcome.pages[0].reason == cp.REASON_RATE_LIMITED
    assert outcome.pages[1].ok
    assert outcome.switch_reason is None and backup_calls == []
    # the first page's three waits, then a pause before the next page is tried
    assert sleeps[:3] == [2.0, 2.0, 2.0]
    assert len(sleeps) == 4 and sleeps[3] > 0


def test_a_rate_limit_leaves_the_minutes_budget_alone():
    session = _Session([_Response(429, headers={"Retry-After": "1"}), _ok()])
    outcome = _run([_company(0)], session, [], [], daily_minutes_budget=9.0)
    assert outcome.switch_reason is None
    assert outcome.browser_run_minutes_used == pytest.approx(0.1)


# -- the daily allowance --------------------------------------------------------

def test_a_daily_limit_error_switches_to_the_backup_and_reads_the_page_there():
    body = {"success": False, "errors": [{"message": "Browser time limit exceeded for today"}]}
    session = _Session([_Response(429, body)])
    backup_calls, sleeps = [], []

    outcome = _run([_company(0), _company(1)], session, backup_calls, sleeps)

    assert outcome.switch_reason == "quota"
    assert sleeps == []  # not a rate limit, nothing to wait for
    assert backup_calls == [_company(0)["careers_url"], _company(1)["careers_url"]]
    assert [p.method for p in outcome.pages] == ["backup", "backup"]


def test_minutes_used_earlier_today_count_toward_the_daily_allowance():
    session = _Session([_ok()])
    backup_calls, sleeps = [], []

    outcome = _run(
        [_company(0), _company(1)], session, backup_calls, sleeps,
        daily_minutes_budget=9.0, minutes_used_before=8.95,
    )

    # page 0 used 0.1 minute, taking today's total to 9.05: the rest use the backup
    assert outcome.switch_reason == "minutes"
    assert [p.method for p in outcome.pages] == ["browser_run", "backup"]


# -- Cloudflare API errors are token problems -------------------------------------

@pytest.mark.parametrize("status", [401, 403])
def test_a_401_or_403_from_the_cloudflare_api_is_reported_as_a_token_problem(status):
    body = {"success": False, "errors": [{"message": "Authentication error"}]}
    session = _Session([_Response(status, body)])
    backup_calls, sleeps = [], []

    outcome = _run([_company(0), _company(1)], session, backup_calls, sleeps)

    assert outcome.switch_reason == "token"
    assert "Authentication error" in outcome.token_problem and str(status) in outcome.token_problem
    # never recorded as a site blocking the page: the pages were read by the backup instead
    assert all(p.reason != cp.REASON_BLOCKED for p in outcome.pages)
    assert [p.method for p in outcome.pages] == ["backup", "backup"]
    assert len(session.posts) == 1  # no hammering a rejected token


def test_summary_line_says_it_is_a_token_problem_and_not_a_site_blocking_the_page():
    from job.run import _careers_pages_summary_line

    line = _careers_pages_summary_line({
        "browser_run": 0, "backup": 2, "unchanged": 0, "changed": 2, "failed": 0, "browser_run_minutes": 0.0,
        "switch_reason": "token", "token_problem": "Cloudflare API returned HTTP 401: Authentication error",
        "candidates": 2, "attempted": 2, "rate_limited": 0, "failure_reasons": {},
    })
    assert "token problem" in line and "not a site blocking the page" in line
    assert "Workers Paid" not in line


def test_summary_line_does_not_blame_capacity_for_a_rate_limit():
    from job.run import _careers_pages_summary_line

    line = _careers_pages_summary_line({
        "browser_run": 3, "backup": 0, "unchanged": 0, "changed": 3, "failed": 0, "browser_run_minutes": 0.3,
        "switch_reason": None, "token_problem": None, "candidates": 3, "attempted": 3, "rate_limited": 2,
        "failure_reasons": {},
    })
    assert "rate-limited us 2 time(s)" in line
    assert "capacity ran out" not in line and "Workers Paid" not in line


# -- the Playwright backup ---------------------------------------------------------

class _FakePage:
    def __init__(self, text, goto_error=None):
        self._text, self._goto_error = text, goto_error

    def goto(self, url, **kwargs):
        if self._goto_error:
            raise self._goto_error

    def inner_text(self, selector):
        return self._text


class _FakeBrowser:
    def __init__(self, page):
        self._page = page

    def new_page(self, **kwargs):
        return self._page

    def close(self):
        pass


class _FakePlaywright:
    def __init__(self, launcher):
        self.chromium = type("Chromium", (), {"launch": staticmethod(launcher)})()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _factory(page=None, launch_error=None, launches=None):
    def launcher():
        if launches is not None:
            launches.append(1)
        if launch_error and (launches is None or len(launches) == 1):
            raise launch_error
        return _FakeBrowser(page)

    return lambda: _FakePlaywright(launcher)


def test_the_backup_loads_a_mocked_page():
    text = playwright_runtime.load_page_text(
        "https://acme.example/careers", sync_playwright=_factory(_FakePage("PM Intern - Austin")),
    )
    assert text == "PM Intern - Austin"


def test_the_backup_runs_inside_a_running_asyncio_loop():
    # Playwright's sync API refuses to start on a thread with a running event loop,
    # which is where the daily job runs (asyncio.run) -- the reason it failed on every page.
    def refuses_inside_a_loop():
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return _FakePlaywright(lambda: _FakeBrowser(_FakePage("loaded")))
        raise RuntimeError("It looks like you are using Playwright Sync API inside the asyncio loop.")

    async def main():
        return cp.load_page_text("https://acme.example/careers", sync_playwright=refuses_inside_a_loop)

    assert asyncio.run(main()) == "loaded"


def test_the_backup_reads_a_page_that_never_goes_network_idle():
    class PlaywrightTimeoutError(Exception):
        pass

    text = playwright_runtime.load_page_text(
        "https://acme.example/careers",
        sync_playwright=_factory(_FakePage("partial but readable", goto_error=PlaywrightTimeoutError("Timeout 20000ms"))),
    )
    assert text == "partial but readable"


def test_the_backup_installs_chromium_once_when_it_is_missing_and_retries(monkeypatch):
    monkeypatch.setattr(playwright_runtime, "_install_attempted", False)
    launches, installs = [], []
    missing = RuntimeError("BrowserType.launch: Executable doesn't exist at /x/chromium. Run playwright install")

    text = playwright_runtime.load_page_text(
        "https://acme.example/careers",
        sync_playwright=_factory(_FakePage("after install"), launch_error=missing, launches=launches),
        installer=lambda: installs.append(1) or (True, "installed"),
    )

    assert text == "after install"
    assert installs == [1] and len(launches) == 2


def test_a_failed_chromium_install_surfaces_the_original_error(monkeypatch):
    monkeypatch.setattr(playwright_runtime, "_install_attempted", False)
    missing = RuntimeError("Executable doesn't exist")
    with pytest.raises(RuntimeError, match="Executable doesn't exist"):
        playwright_runtime.load_page_text(
            "https://acme.example/careers", sync_playwright=_factory(launch_error=missing),
            installer=lambda: (False, "no network"),
        )


def test_each_backup_failure_is_logged_with_the_underlying_error_per_page(capsys):
    def backup_fetch(url):
        raise RuntimeError("Executable doesn't exist at /x/chromium")

    outcome = cp.run_careers_pages(
        [_company(0), _company(1)], account_id=None, token=None, backup_fetch=backup_fetch,
        robots_check=lambda url, *, session=None: True,
    )

    assert outcome.backup_failures == 2
    for page in outcome.pages:
        assert not page.ok
        assert "RuntimeError: Executable doesn't exist" in page.reason
    out = capsys.readouterr().out
    assert "careers backup: https://acme0.example/careers: RuntimeError" in out
    assert "careers backup: https://acme1.example/careers: RuntimeError" in out
