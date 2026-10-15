"""Phase 1 of the Cloudflare daily trigger: the Worker's cron handler decides
whether the cron that just fired is 08:00 America/Chicago today and, if so,
dispatches `daily-search.yml` through GitHub's API.

Written before `api/src/cron.py` existed. They pin down:
- which of the two UTC crons is 08:00 Central on either side of US daylight
  saving time, including the transition days themselves (2026-03-08 and
  2026-11-01);
- the dispatch payload (`ref: master`, `run_source: cloudflare`, always);
- that a non-2xx answer from GitHub is raised, never swallowed;
- that the cron strings in `api/wrangler.toml` match the handler's
  character for character (Cloudflare compares them as strings);
- that the Worker entrypoint actually declares the `scheduled` handler and
  that nothing on the HTTP path calls the dispatch code.
"""
from __future__ import annotations

import ast
import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import cron
from github_client import GitHubApiError

ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc


def run(coro):
    return asyncio.run(coro)


def utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Daylight saving arithmetic
# ---------------------------------------------------------------------------

def test_daylight_saving_runs_from_second_sunday_in_march_to_first_sunday_in_november():
    assert cron.is_daylight_saving(utc(2026, 7, 15, 13)) is True
    assert cron.is_daylight_saving(utc(2026, 1, 15, 13)) is False
    assert cron.is_daylight_saving(utc(2026, 12, 15, 14)) is False


def test_daylight_saving_switches_at_the_utc_instant_not_the_date():
    # 2026-03-08: clocks go forward at 02:00 CST, which is 08:00 UTC.
    assert cron.is_daylight_saving(utc(2026, 3, 8, 7, 59)) is False
    assert cron.is_daylight_saving(utc(2026, 3, 8, 8, 0)) is True
    # 2026-11-01: clocks go back at 02:00 CDT, which is 07:00 UTC.
    assert cron.is_daylight_saving(utc(2026, 11, 1, 6, 59)) is True
    assert cron.is_daylight_saving(utc(2026, 11, 1, 7, 0)) is False


def test_daylight_saving_dates_for_other_years():
    assert cron.is_daylight_saving(utc(2027, 3, 14, 9)) is True  # second Sunday in March 2027
    assert cron.is_daylight_saving(utc(2027, 3, 7, 9)) is False
    assert cron.is_daylight_saving(utc(2027, 11, 7, 9)) is False  # first Sunday in November 2027
    assert cron.is_daylight_saving(utc(2027, 10, 31, 9)) is True


# ---------------------------------------------------------------------------
# The decision: is this cron 08:00 Central today?
# ---------------------------------------------------------------------------

def test_in_october_only_the_13_utc_cron_dispatches():
    assert cron.should_dispatch("0 13 * * *", utc(2026, 10, 15, 13)) is True
    assert cron.should_dispatch("0 14 * * *", utc(2026, 10, 15, 14)) is False


def test_in_december_only_the_14_utc_cron_dispatches():
    assert cron.should_dispatch("0 14 * * *", utc(2026, 12, 15, 14)) is True
    assert cron.should_dispatch("0 13 * * *", utc(2026, 12, 15, 13)) is False


def test_the_november_transition_day_already_uses_the_14_utc_cron():
    # Daylight time ended at 07:00 UTC that morning, so 08:00 Central is 14:00 UTC.
    assert cron.should_dispatch("0 14 * * *", utc(2026, 11, 1, 14)) is True
    assert cron.should_dispatch("0 13 * * *", utc(2026, 11, 1, 13)) is False
    # The day before is still daylight time.
    assert cron.should_dispatch("0 13 * * *", utc(2026, 10, 31, 13)) is True
    assert cron.should_dispatch("0 14 * * *", utc(2026, 10, 31, 14)) is False


def test_the_march_transition_day_already_uses_the_13_utc_cron():
    # Daylight time began at 08:00 UTC that morning, so 08:00 Central is 13:00 UTC.
    assert cron.should_dispatch("0 13 * * *", utc(2026, 3, 8, 13)) is True
    assert cron.should_dispatch("0 14 * * *", utc(2026, 3, 8, 14)) is False
    # The day before is still standard time.
    assert cron.should_dispatch("0 14 * * *", utc(2026, 3, 7, 14)) is True
    assert cron.should_dispatch("0 13 * * *", utc(2026, 3, 7, 13)) is False


def test_an_unknown_cron_never_dispatches():
    assert cron.should_dispatch("*/5 * * * *", utc(2026, 10, 15, 13)) is False
    assert cron.should_dispatch("", utc(2026, 10, 15, 13)) is False


def test_cron_spacing_is_normalized():
    # `wrangler dev --test-scheduled` passes `cron=0+13+*+*+*`; a stray double space must not matter.
    assert cron.should_dispatch("0  13 * * *", utc(2026, 10, 15, 13)) is True


def test_a_naive_datetime_is_treated_as_utc():
    assert cron.should_dispatch("0 13 * * *", datetime(2026, 10, 15, 13, 0)) is True


# ---------------------------------------------------------------------------
# The dispatch
# ---------------------------------------------------------------------------

class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, method, url, *, token, json_body=None):
        self.calls.append((method, url, token, json_body))
        return self.responses.pop(0)


def test_dispatch_posts_to_the_daily_search_workflow_on_master():
    transport = ScriptedTransport([(204, {})])
    result = run(cron.run_scheduled("0 13 * * *", utc(2026, 10, 15, 13), token="dispatch-token", transport=transport))

    assert result["dispatched"] is True
    assert result["status"] == 204
    method, url, token, body = transport.calls[0]
    assert method == "POST"
    assert url == "https://api.github.com/repos/HenryDMcGee/internship-watch/actions/workflows/daily-search.yml/dispatches"
    assert token == "dispatch-token"
    assert body["ref"] == "master"


def test_dispatch_payload_always_says_run_source_cloudflare():
    # Phase 4's guard exempts only `run_source: manual`; the workflow input
    # defaults to `manual` when the field is missing, so omitting it would
    # make the Worker's dispatch exempt and reopen the race. Assert it here
    # rather than trusting it.
    transport = ScriptedTransport([(204, {})])
    run(cron.run_scheduled("0 13 * * *", utc(2026, 10, 15, 13), token="t", transport=transport))
    assert transport.calls[0][3]["inputs"] == {"run_source": "cloudflare"}
    assert cron.DISPATCH_INPUTS == {"run_source": "cloudflare"}


def test_the_other_cron_makes_no_request():
    transport = ScriptedTransport([])
    result = run(cron.run_scheduled("0 14 * * *", utc(2026, 10, 15, 14), token="t", transport=transport))
    assert result["dispatched"] is False
    assert result["status"] is None
    assert transport.calls == []


def test_a_non_2xx_answer_is_raised_not_swallowed():
    transport = ScriptedTransport([(401, {"message": "Bad credentials"})])
    with pytest.raises(GitHubApiError) as excinfo:
        run(cron.run_scheduled("0 13 * * *", utc(2026, 10, 15, 13), token="expired", transport=transport))
    assert "401" in str(excinfo.value)
    assert "Bad credentials" in str(excinfo.value)


def test_a_missing_token_is_an_error_when_a_dispatch_is_due():
    transport = ScriptedTransport([(204, {})])
    with pytest.raises(RuntimeError, match="GITHUB_DISPATCH_TOKEN"):
        run(cron.run_scheduled("0 13 * * *", utc(2026, 10, 15, 13), token="", transport=transport))
    assert transport.calls == []


def test_a_missing_token_does_not_matter_when_no_dispatch_is_due():
    transport = ScriptedTransport([])
    result = run(cron.run_scheduled("0 14 * * *", utc(2026, 10, 15, 14), token="", transport=transport))
    assert result["dispatched"] is False


def test_the_decision_is_logged(caplog):
    import logging

    transport = ScriptedTransport([(204, {})])
    with caplog.at_level(logging.INFO, logger="internship_watch.cron"):
        run(cron.run_scheduled("0 13 * * *", utc(2026, 10, 15, 13), token="t", transport=transport))
    assert "0 13 * * *" in caplog.text
    assert "204" in caplog.text


# ---------------------------------------------------------------------------
# Configuration and wiring
# ---------------------------------------------------------------------------

def _wrangler_crons() -> list[str]:
    text = (ROOT / "api" / "wrangler.toml").read_text(encoding="utf-8")
    block = re.search(r"^\[triggers\]\s*\ncrons\s*=\s*\[([^\]]*)\]", text, re.M)
    assert block, "api/wrangler.toml has no [triggers] block with a crons list"
    return re.findall(r'"([^"]*)"', block.group(1))


def test_wrangler_toml_registers_exactly_the_two_utc_crons():
    assert _wrangler_crons() == ["0 13 * * *", "0 14 * * *"]


def test_handler_cron_strings_match_wrangler_toml_character_for_character():
    # Cloudflare hands the handler the configured string as-is; the comparison is textual.
    assert set(_wrangler_crons()) == {cron.DAYLIGHT_CRON, cron.STANDARD_CRON}


def _default_entrypoint_methods() -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    tree = ast.parse((ROOT / "api" / "src" / "main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Default":
            return {n.name: n for n in node.body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))}
    raise AssertionError("no `Default` Worker entrypoint class in api/src/main.py")


def test_worker_entrypoint_declares_the_scheduled_handler():
    # Python Workers: `async def scheduled(self, controller, env, ctx)` on the WorkerEntrypoint subclass.
    methods = _default_entrypoint_methods()
    assert "scheduled" in methods
    assert isinstance(methods["scheduled"], ast.AsyncFunctionDef)
    assert [a.arg for a in methods["scheduled"].args.args] == ["self", "controller", "env", "ctx"]


def test_the_http_handler_never_calls_the_dispatch_code():
    # The cron handler must not be reachable over HTTP: `fetch` serves the
    # dashboard and API only, and nothing in it names the dispatch function.
    methods = _default_entrypoint_methods()
    fetch_names = {n.id for n in ast.walk(methods["fetch"]) if isinstance(n, ast.Name)}
    fetch_attrs = {n.attr for n in ast.walk(methods["fetch"]) if isinstance(n, ast.Attribute)}
    assert "run_scheduled" not in fetch_names | fetch_attrs
    assert "GITHUB_DISPATCH_TOKEN" not in ast.dump(methods["fetch"])
    scheduled_names = {n.id for n in ast.walk(methods["scheduled"]) if isinstance(n, ast.Name)}
    scheduled_attrs = {n.attr for n in ast.walk(methods["scheduled"]) if isinstance(n, ast.Attribute)}
    assert "run_scheduled" in scheduled_names | scheduled_attrs


def test_scheduled_time_from_controller_is_read_as_utc():
    # controller.scheduledTime is milliseconds since the epoch, UTC.
    when = cron.scheduled_time(1_792_587_600_000)  # 2026-10-21T13:00:00Z
    assert when == utc(2026, 10, 21, 13)
    assert cron.scheduled_time(None).tzinfo is UTC
