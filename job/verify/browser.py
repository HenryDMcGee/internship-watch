"""Headless browser check (Playwright + Chromium), third in the verification order.

Only reached when the data feed didn't cover the posting and the page has
no usable schema.org data. Capped per run by `BrowserBudget`
(`browser_checks_per_run`, default 50). Playwright is imported lazily so
importing this module -- and running the test suite -- doesn't require it
to be installed; only the real daily job does.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from job.playwright_runtime import load_page_text
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
    # Runs on its own thread: Playwright's sync API won't start inside the job's
    # running asyncio loop (issue #63). Playwright is imported lazily there.
    return load_page_text(url, timeout_ms=15000)


def check_via_browser(
    url: str, *, page_text: str | None = None, unclear_reader: Callable[[str], VerifyDecision | None] | None = None
) -> VerifyDecision:
    """Check whether `url` still shows an open posting.

    `page_text` lets tests (and any caller that already has the rendered
    text) skip launching a real browser. `unclear_reader`, if given, is
    tried when the plain text-signal heuristics can't decide -- phase 3's
    Claude unclear-page read (see `job/agent/unclear.py` and
    `job/agent/pipeline.py`, which wraps it with budget gating and error
    handling before passing it in here). A `None` return from it (skipped,
    or failed) just keeps the original "unclear" decision.
    """
    if page_text is None:
        try:
            page_text = _load_page_text(url)
        except Exception as exc:  # noqa: BLE001 - blocked/unavailable sites must not stop the run
            return VerifyDecision(decision="unclear", note=f"Browser check failed: {exc}")
    decision = classify_text(page_text)
    if decision.decision == "unclear" and unclear_reader is not None:
        claude_decision = unclear_reader(page_text)
        if claude_decision is not None:
            return claude_decision
    return decision
