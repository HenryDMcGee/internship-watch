"""Loads a page's visible text with Playwright's Chromium, for the careers-page
backup (`job/fetchers/careers_pages.py`) and the status check
(`job/verify/browser.py`).

Issue #63: the backup failed on every page. The daily job runs inside
`asyncio.run(...)`, and Playwright's sync API refuses to start on a thread
that has a running event loop ("It looks like you are using Playwright Sync
API inside the asyncio loop"). Every page load here runs on its own worker
thread, which has no loop. If Chromium itself isn't installed (a cache hit
that restored nothing, a fresh runner), it is installed once and the load
retried. Playwright is imported lazily so importing this module, and running
the test suite, doesn't require it.
"""
from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from job.fetchers.base import USER_AGENT

_MISSING_BROWSER_HINTS = ("executable doesn't exist", "playwright install")
_install_attempted = False


def install_chromium(runner: Callable = subprocess.run) -> tuple[bool, str]:
    """Runs `python -m playwright install chromium` with the interpreter that is
    running the job, so it installs into the same environment the backup then
    runs in. Returns (ok, detail).
    """
    completed = runner(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True, timeout=600, check=False,
    )
    detail = ((completed.stderr or "") + (completed.stdout or "")).strip()[-300:]
    return completed.returncode == 0, detail


def _looks_like_missing_browser(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(hint in message for hint in _MISSING_BROWSER_HINTS)


def _is_timeout(exc: Exception) -> bool:
    return "timeout" in type(exc).__name__.lower() or "timeout" in str(exc).lower()


def _read_page(sync_playwright, url: str, timeout_ms: int) -> str:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            try:
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except Exception as exc:  # noqa: BLE001
                # Busy careers sites never go network-idle; whatever rendered by
                # the deadline is still worth reading.
                if not _is_timeout(exc):
                    raise
            return page.inner_text("body")
        finally:
            browser.close()


def load_page_text(
    url: str, *, timeout_ms: int = 20000, sync_playwright: Callable | None = None,
    installer: Callable[[], tuple[bool, str]] = install_chromium,
) -> str:
    """The page's visible text. Raises with the underlying error, so the caller
    can log it per page.
    """
    def work() -> str:
        global _install_attempted
        factory = sync_playwright
        if factory is None:
            from playwright.sync_api import sync_playwright as factory  # imported lazily, see module docstring
        try:
            return _read_page(factory, url, timeout_ms)
        except Exception as exc:  # noqa: BLE001
            if _install_attempted or not _looks_like_missing_browser(exc):
                raise
            _install_attempted = True
            print("playwright: Chromium isn't installed; installing it now")
            ok, detail = installer()
            print(f"playwright: install {'succeeded' if ok else 'failed'}: {detail}")
            if not ok:
                raise
            return _read_page(factory, url, timeout_ms)

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(work).result()
