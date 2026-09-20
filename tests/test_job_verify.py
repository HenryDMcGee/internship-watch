from datetime import date

from job.verify.browser import BrowserBudget, check_via_browser
from job.verify.mirrors import check_mirror
from job.verify.pipeline import verify_posting
from job.verify.structured import check_structured_data, classify_job_posting
from job.verify.text_signals import classify_text

JOBPOSTING_HTML = """
<html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "JobPosting", "title": "PM Intern", "validThrough": "%s"}
</script>
</head><body>Job details</body></html>
"""


def test_classify_job_posting_open_when_valid_through_future():
    result = classify_job_posting({"validThrough": "2099-01-01"}, today=date(2026, 9, 20))
    assert result.decision == "open"


def test_classify_job_posting_closed_when_valid_through_past():
    result = classify_job_posting({"validThrough": "2020-01-01"}, today=date(2026, 9, 20))
    assert result.decision == "closed"


def test_classify_job_posting_open_when_no_valid_through():
    result = classify_job_posting({})
    assert result.decision == "open"


def test_check_structured_data_parses_jsonld_from_html():
    html = JOBPOSTING_HTML % "2099-01-01"
    result = check_structured_data("https://example.com/job", html=html)
    assert result.decision == "open"


def test_check_structured_data_unclear_with_no_jobposting():
    result = check_structured_data("https://example.com/job", html="<html><body>Nothing here</body></html>")
    assert result.decision == "unclear"


def test_classify_text_closed_phrase():
    assert classify_text("Sorry, this position has been filled.").decision == "closed"


def test_classify_text_open_phrase():
    assert classify_text("Ready to apply? Apply now!").decision == "open"


def test_classify_text_unclear():
    assert classify_text("Welcome to our careers page.").decision == "unclear"


def test_check_via_browser_uses_given_page_text():
    result = check_via_browser("https://example.com/job", page_text="This job is no longer accepting applications.")
    assert result.decision == "closed"


def test_check_via_browser_skips_unclear_reader_when_text_signals_decide():
    calls = []
    result = check_via_browser(
        "https://example.com/job", page_text="Apply now!",
        unclear_reader=lambda text: calls.append(text) or None,
    )
    assert result.decision == "open"
    assert calls == []  # a clear text signal never needs Claude


def test_check_via_browser_uses_unclear_reader_when_text_signals_cant_decide():
    from job.verify.text_signals import VerifyDecision

    result = check_via_browser(
        "https://example.com/job", page_text="Welcome to our careers page.",
        unclear_reader=lambda text: VerifyDecision(decision="closed", note="Claude read the page"),
    )
    assert result.decision == "closed"
    assert result.note == "Claude read the page"


def test_check_via_browser_keeps_unclear_when_reader_returns_none():
    result = check_via_browser(
        "https://example.com/job", page_text="Welcome to our careers page.",
        unclear_reader=lambda text: None,
    )
    assert result.decision == "unclear"


def test_check_mirror_uses_given_page_text():
    result = check_mirror("https://dreamwork.example/job", page_text="Apply now for this role.")
    assert result.decision == "open"


def test_browser_budget_caps_checks():
    budget = BrowserBudget(remaining=1)
    assert budget.take() is True
    assert budget.take() is False


# ---------------------------------------------------------------------------
# Verification order
# ---------------------------------------------------------------------------

def _posting(**overrides):
    row = {
        "id": 1, "url": "https://example.com/job", "mirror_url": None,
        "user_status": None, "user_status_at": None, "status": "unverified",
        "verified_via": None, "verified_at": None,
    }
    row.update(overrides)
    return row


def test_verify_prefers_feed_when_found():
    outcome = verify_posting(
        _posting(), feed_checked_ok=True, found_in_current_feed=True,
        browser_budget=BrowserBudget(remaining=5), now_iso="2026-09-20T08:00:00",
    )
    assert outcome.status == "open"
    assert outcome.verified_via == "feed"


def test_verify_feed_absence_means_closed():
    outcome = verify_posting(
        _posting(), feed_checked_ok=True, found_in_current_feed=False,
        browser_budget=BrowserBudget(remaining=5), now_iso="2026-09-20T08:00:00",
    )
    assert outcome.status == "closed"
    assert outcome.verified_via == "feed"


def test_verify_falls_back_to_structured_when_feed_unavailable():
    def fake_structured(url, session=None):
        from job.verify.text_signals import VerifyDecision
        return VerifyDecision(decision="closed", note="expired")

    outcome = verify_posting(
        _posting(), feed_checked_ok=False, found_in_current_feed=False,
        browser_budget=BrowserBudget(remaining=5), now_iso="2026-09-20T08:00:00",
        check_structured=fake_structured,
    )
    assert outcome.status == "closed"
    assert outcome.verified_via == "structured"


def test_verify_falls_back_to_browser_when_structured_unclear():
    from job.verify.text_signals import VerifyDecision

    outcome = verify_posting(
        _posting(), feed_checked_ok=False, found_in_current_feed=False,
        browser_budget=BrowserBudget(remaining=5), now_iso="2026-09-20T08:00:00",
        check_structured=lambda url, session=None: VerifyDecision("unclear"),
        check_browser=lambda url: VerifyDecision("open", "apply now"),
    )
    assert outcome.status == "open"
    assert outcome.verified_via == "browser"


def test_verify_skips_browser_when_budget_exhausted():
    from job.verify.text_signals import VerifyDecision

    browser_called = []
    outcome = verify_posting(
        _posting(mirror_url="https://mirror.example/job"), feed_checked_ok=False, found_in_current_feed=False,
        browser_budget=BrowserBudget(remaining=0), now_iso="2026-09-20T08:00:00",
        check_structured=lambda url, session=None: VerifyDecision("unclear"),
        check_browser=lambda url: browser_called.append(url) or VerifyDecision("open"),
        check_mirror_site=lambda url, session=None: VerifyDecision("closed", "gone"),
    )
    assert browser_called == []
    assert outcome.status == "closed"
    assert outcome.verified_via == "mirror"


def test_verify_mirror_is_last_resort():
    from job.verify.text_signals import VerifyDecision

    outcome = verify_posting(
        _posting(mirror_url="https://mirror.example/job"), feed_checked_ok=False, found_in_current_feed=False,
        browser_budget=BrowserBudget(remaining=5), now_iso="2026-09-20T08:00:00",
        check_structured=lambda url, session=None: VerifyDecision("unclear"),
        check_browser=lambda url: VerifyDecision("unclear"),
        check_mirror_site=lambda url, session=None: VerifyDecision("closed", "gone"),
    )
    assert outcome.status == "closed"
    assert outcome.verified_via == "mirror"


def test_verify_respects_user_mark_open_answer():
    outcome = verify_posting(
        _posting(user_status="open", user_status_at="2026-09-01T00:00:00"),
        feed_checked_ok=True, found_in_current_feed=True,
        browser_budget=BrowserBudget(remaining=5), now_iso="2026-09-20T08:00:00",
    )
    assert outcome.status == "open"
    assert outcome.verified_via == "user"


def test_verify_respects_user_mark_open_answer_when_feed_not_checked():
    outcome = verify_posting(
        _posting(user_status="open", user_status_at="2026-09-01T00:00:00"),
        feed_checked_ok=False, found_in_current_feed=False,
        browser_budget=BrowserBudget(remaining=5), now_iso="2026-09-20T08:00:00",
    )
    assert outcome.status == "open"
    assert outcome.verified_via == "user"


def test_verify_user_answer_overridden_when_posting_disappears_from_feed():
    # This is the realistic case: the API always writes user_status_at
    # alongside user_status, so the override must not depend on it being
    # absent -- only on the posting actually disappearing from the feed.
    outcome = verify_posting(
        _posting(user_status="open", user_status_at="2026-09-01T00:00:00"),
        feed_checked_ok=True, found_in_current_feed=False,
        browser_budget=BrowserBudget(remaining=5), now_iso="2026-09-20T08:00:00",
    )
    assert outcome.status == "closed"
    assert outcome.verified_via == "feed"


def test_verify_passes_unclear_reader_through_to_default_browser_check():
    # verify_posting must forward `unclear_reader` when the caller supplies
    # one, using the default `check_via_browser` (which accepts it) rather
    # than crashing on an unexpected keyword.
    from job.verify.text_signals import VerifyDecision

    outcome = verify_posting(
        _posting(), feed_checked_ok=False, found_in_current_feed=False,
        browser_budget=BrowserBudget(remaining=5), now_iso="2026-09-20T08:00:00",
        check_structured=lambda url, session=None: VerifyDecision("unclear"),
        unclear_reader=lambda text: VerifyDecision("open", "Claude"),
    )
    # _load_page_text isn't mocked here, so the real browser launch fails
    # and check_via_browser returns "unclear" before ever reaching text
    # signals or the unclear_reader -- this just proves the call doesn't
    # raise a TypeError over the extra keyword.
    assert outcome.status == "unverified"


def test_verify_everything_unclear_keeps_previous_status():
    from job.verify.text_signals import VerifyDecision

    outcome = verify_posting(
        _posting(status="unverified"), feed_checked_ok=False, found_in_current_feed=False,
        browser_budget=BrowserBudget(remaining=5), now_iso="2026-09-20T08:00:00",
        check_structured=lambda url, session=None: VerifyDecision("unclear"),
        check_browser=lambda url: VerifyDecision("unclear"),
    )
    assert outcome.status == "unverified"
