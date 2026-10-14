"""Verification order: data feed, careers page, structured page data, headless browser, mirror sites.

Follows `docs/plan.md`'s "Daily run, in order" step 6. Henry's Mark
open/closed answer is sticky and skips every automated check, unless the
posting has disappeared from the company's own feed.
"""
from __future__ import annotations

from dataclasses import dataclass

from job.verify.browser import BrowserBudget, check_via_browser
from job.verify.mirrors import check_mirror
from job.verify.structured import check_structured_data


@dataclass
class VerifyOutcome:
    status: str  # open, closing, closed, unverified
    verified_via: str | None
    verified_at: str
    note: str | None = None


def verify_posting(
    posting: dict,
    *,
    feed_checked_ok: bool,
    found_in_current_feed: bool,
    browser_budget: BrowserBudget,
    now_iso: str,
    found_on_careers_page: bool = False,
    session=None,
    check_structured=check_structured_data,
    check_browser=check_via_browser,
    check_mirror_site=check_mirror,
    unclear_reader=None,
) -> VerifyOutcome:
    user_status = posting.get("user_status")
    user_status_at = posting.get("user_status_at")
    posting_disappeared = feed_checked_ok and not found_in_current_feed

    # Henry's Mark open/closed answer sticks unless the posting disappears
    # from the company's own feed.
    if user_status in ("open", "closed") and not posting_disappeared:
        return VerifyOutcome(
            status=user_status, verified_via="user", verified_at=user_status_at or now_iso
        )

    # 1. The company's own data feed, when we could check it today.
    if feed_checked_ok:
        if found_in_current_feed:
            return VerifyOutcome(status="open", verified_via="feed", verified_at=now_iso)
        return VerifyOutcome(
            status="closed", verified_via="feed", verified_at=now_iso,
            note="No longer listed in the company's feed.",
        )

    # 1b. Listed on the company's own careers page this run. Only a listing
    # confirms anything: careers pages often show just their first page of
    # results, so a posting missing from one falls through to the checks below.
    if found_on_careers_page:
        return VerifyOutcome(
            status="open", verified_via="careers_page", verified_at=now_iso,
            note="Listed on the company's careers page.",
        )

    # 2. Structured page data (schema.org JobPosting).
    structured = check_structured(posting["url"], session=session)
    if structured.decision != "unclear":
        return VerifyOutcome(status=structured.decision, verified_via="structured", verified_at=now_iso, note=structured.note)

    # 3. Headless browser, budget permitting.
    if browser_budget.take():
        # Only pass `unclear_reader` through when there is one: callers
        # (and tests) that inject their own `check_browser` don't all
        # accept that keyword, and the real `check_via_browser` already
        # defaults it to None.
        browser_result = (
            check_browser(posting["url"], unclear_reader=unclear_reader)
            if unclear_reader is not None
            else check_browser(posting["url"])
        )
        if browser_result.decision != "unclear":
            return VerifyOutcome(status=browser_result.decision, verified_via="browser", verified_at=now_iso, note=browser_result.note)

    # 4. Mirror sites, as a tiebreaker only.
    mirror_url = posting.get("mirror_url")
    if mirror_url:
        mirror_result = check_mirror_site(mirror_url, session=session)
        if mirror_result.decision != "unclear":
            return VerifyOutcome(status=mirror_result.decision, verified_via="mirror", verified_at=now_iso, note=mirror_result.note)

    return VerifyOutcome(
        status=posting.get("status") or "unverified",
        verified_via=posting.get("verified_via"),
        verified_at=posting.get("verified_at") or now_iso,
        note="Could not confirm status from any source.",
    )
