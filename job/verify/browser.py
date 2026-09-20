"""Headless browser check (Playwright + Chromium), third in the verification order.

Only reached when the data feed didn't cover the posting and the page has
no usable schema.org data. Capped per run by `BrowserBudget`
(`browser_checks_per_run`, default 50). Playwright is imported lazily so
importing this module -- and running the test suite -- doesn't require it
to be installed; only the real daily job does.
"""
from __future__ import annotations

from dataclasses import dataclass

from job.fetchers.base import USER_AGENT
from job.verify.text_signals import VerifyDecision, classify_text


@dataclass
class BrowserBudget:
    remaining: int

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def _load_page_text(url: str) -> str:
    from playwright.sync_api import sync_playwright  # imported lazily, see module docstring

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, wait_until="networkidle", timeout=15000)
            return page.inner_text("body")
        finally:
            browser.close()


def check_via_browser(url: str, *, page_text: str | None = None) -> VerifyDecision:
    """Check whether `url` still shows an open posting.

    `page_text` lets tests (and any caller that already has the rendered
    text) skip launching a real browser.
    """
    if page_text is None:
        try:
            page_text = _load_page_text(url)
        except Exception as exc:  # noqa: BLE001 - blocked/unavailable sites must not stop the run
            return VerifyDecision(decision="unclear", note=f"Browser check failed: {exc}")
    return classify_text(page_text)
