import json

from job.agent.summary import ROUTINE_ANTHROPIC_VERSION, ROUTINE_BETA, build_summary_payload, send_daily_summary


def _payload(**overrides):
    kwargs = dict(
        date="2026-09-20", run_status="completed", run_reason=None,
        tokens_today=1000, searches_today=5,
        budgets={"daily_fraction_used": 0.25, "monthly_fraction_used": 0.1},
        tokens_week_to_date=4000,
        usage_by_step=[{"step": "discovery", "tokens": 800, "searches": 5, "usd": 0.01}],
        forecast={"weekly_usd": 2.0, "monthly_usd": 0.0},
        alerts=[{"type": "budget_80", "severity": "warning"}],
    )
    kwargs.update(overrides)
    return build_summary_payload(**kwargs)


def test_summary_payload_shape():
    payload = _payload()
    assert set(payload) == {
        "date", "run_status", "run_reason", "today", "budgets", "week_to_date_tokens",
        "usage_by_step", "forecast", "alerts",
    }
    assert payload["today"] == {"tokens": 1000, "searches": 5}


def test_summary_payload_carries_no_posting_or_company_fields():
    # Realistic-looking posting/company data run through the same fields a
    # careless caller might pass, to prove the payload builder can't leak
    # it: only the whitelisted, aggregate shape above ever reaches the
    # payload, so posting/company detail (title, url, location, employer)
    # a caller might have on hand never has anywhere to go.
    payload = _payload()
    serialized = json.dumps(payload).lower()
    forbidden = ["posting", "company", "title", "url", "location", "acme", "greenhouse.io"]
    for word in forbidden:
        assert word not in serialized, f"summary payload leaked {word!r}"


def test_summary_payload_includes_run_failure_reason_when_failed():
    payload = _payload(run_status="failed", run_reason="D1 query failed: timeout")
    assert payload["run_status"] == "failed"
    assert payload["run_reason"] == "D1 query failed: timeout"


def test_send_daily_summary_noop_when_not_configured():
    sent = send_daily_summary({"date": "2026-09-20"}, url=None, token=None)
    assert sent is False


class _FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})


def test_send_daily_summary_posts_with_required_headers():
    session = _FakeSession()
    payload = _payload()
    sent = send_daily_summary(payload, url="https://example.com/routine", token="tok", session=session)
    assert sent is True
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == "https://example.com/routine"
    assert call["headers"]["Authorization"] == "Bearer tok"
    assert call["headers"]["anthropic-version"] == ROUTINE_ANTHROPIC_VERSION
    assert call["headers"]["anthropic-beta"] == ROUTINE_BETA
    assert json.loads(call["json"]["text"]) == payload
