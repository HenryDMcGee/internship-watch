"""Issue #70: keep runs short. Run order, the careers time budget, the page
pre-check and trimming, concurrent extraction, flagging empty pages, and the
run time shown in the summary."""
import asyncio
import json
import re
import threading
import time

import job.run as run_module
from job import careers_snapshot
from job.agent import careers_extraction
from job.agent import client as agent_client
from job.agent.alerts import evaluate_long_run_alert
from job.agent.client import ClaudeStepResult
from job.close_stuck_runs import close_stuck_runs
from job.fetchers import FetchResult
from job.fetchers import careers_pages as cp
from job.models import FetchedPosting


def run(coro):
    return asyncio.run(coro)


def _insert_company(db, **overrides):
    row = {"name": "Acme", "status": "pinned", "feed_type": None, "feed_id": None,
           "careers_url": "https://acme.example/careers"}
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    return run(db.run(f"INSERT INTO companies ({columns}) VALUES ({placeholders})", *row.values()))


def _long_page(text: str = "") -> str:
    return text + " Apply for our internship. " + "Open roles listed below. " * 100


def _page_outcome(company_id, text, method="browser_run"):
    return cp.PageOutcome(company_id=company_id, url="https://x.example/careers", ok=True, text=text,
                          method=method, browser_minutes=0.1)


def _outcome(*pages):
    return cp.CareersRunOutcome(pages=list(pages), browser_run_minutes_used=0.0, switch_reason=None)


def _default_reply(step):
    return {
        "discovery": '{"companies": []}',
        "scout": '{"sources": []}',
        "careers_repair": '{"replacements": []}',
        "improvement": '{"search_plan": null, "proposals": [], "title_suggestions": []}',
        "careers_extraction": '{"listings": []}',
    }.get(step, "{}")


# --- page pre-check and trimming ------------------------------------------------

def test_pages_under_2000_characters_or_without_job_content_make_no_claude_call():
    assert careers_extraction.skip_reason("x" * 1999) is not None
    assert careers_extraction.skip_reason(_long_page()) is None
    # Long enough, but nothing job-like: no links and none of the words.
    assert careers_extraction.skip_reason("Our mission is to build great things. " * 100) is not None
    # A job-like link alone is enough.
    assert careers_extraction.skip_reason("See https://acme.example/jobs/123 " + "filler text " * 200) is None


def test_long_pages_send_only_their_relevant_sections():
    filler = "Marketing copy about our culture and values.\n" * 1000
    relevant = "MBA Product Manager Intern - Seattle\n"
    page = filler + relevant + filler
    assert len(page) > careers_extraction.MAX_PAGE_CHARS
    prompt = careers_extraction.build_prompt("Acme", page)
    assert relevant.strip() in prompt
    assert prompt.count("Marketing copy") < 5  # context lines only, not the whole page
    # The old behavior cut the page at the cap and lost this section entirely.
    assert relevant.strip() not in page[: careers_extraction.MAX_PAGE_CHARS]


def test_relevant_text_respects_the_size_cap_and_short_pages_are_unchanged():
    assert careers_extraction.relevant_text("short page", max_chars=100) == "short page"
    page = "Product Manager Intern role\n" * 5000
    assert len(careers_extraction.relevant_text(page, max_chars=1000)) <= 1000


def test_short_page_makes_no_claude_call_and_is_recorded_as_no_listings(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    company_id = _insert_company(empty_db)
    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages",
                        lambda candidates, **kw: _outcome(_page_outcome(company_id, "Welcome. Apply now.")))
    steps = []

    def fake_claude(step, prompt, *, model, **kwargs):
        steps.append(step)
        return ClaudeStepResult(step=step, model=model, text=_default_reply(step))

    monkeypatch.setattr(agent_client, "run_claude_step", fake_claude)
    run(run_module.run(empty_db, kind="daily"))

    assert "careers_extraction" not in steps
    snapshot = run(careers_snapshot.load_snapshot(empty_db, company_id))
    assert snapshot["status"] == "changed"
    assert snapshot["empty_runs"] == 1


# --- flag after 2 empty runs ----------------------------------------------------

def test_page_with_no_listings_twice_in_a_row_is_flagged_and_queued_for_the_scout(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    company_id = _insert_company(empty_db)
    # Long and job-like, so Claude is asked, and returns 0 listings. The second run
    # sees the same page unchanged, which still counts as another empty run.
    page = _long_page("Landing page")
    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages",
                        lambda candidates, **kw: _outcome(_page_outcome(company_id, page)))
    steps = []

    def fake_claude(step, prompt, *, model, **kwargs):
        steps.append(step)
        return ClaudeStepResult(step=step, model=model, text=_default_reply(step))

    monkeypatch.setattr(agent_client, "run_claude_step", fake_claude)

    run(run_module.run(empty_db, kind="daily"))
    flagged = run(empty_db.first("SELECT careers_page_flagged FROM companies WHERE id = ?", company_id))
    assert flagged["careers_page_flagged"] == 0
    assert "careers_repair" not in steps

    run(run_module.run(empty_db, kind="daily"))
    flagged = run(empty_db.first("SELECT careers_page_flagged FROM companies WHERE id = ?", company_id))
    assert flagged["careers_page_flagged"] == 1
    assert "careers_repair" in steps  # the scout looks for a better URL in that same run


def test_a_page_with_listings_resets_the_empty_count(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    company_id = _insert_company(empty_db)
    run(careers_snapshot.record_changed(
        empty_db, company_id, "old", method="browser_run", now_iso="2026-09-01T08:00:00", existing=None))
    run(empty_db.run("UPDATE careers_page_snapshots SET empty_runs = 1 WHERE company_id = ?", company_id))
    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages",
                        lambda candidates, **kw: _outcome(_page_outcome(company_id, _long_page("new"))))

    def fake_claude(step, prompt, *, model, **kwargs):
        text = _default_reply(step)
        if step == "careers_extraction":
            text = json.dumps({"listings": [{"title": "Barista", "location": "Seattle", "link": "https://a.example/jobs/1",
                                             "posted_date": None, "job_id": "1"}]})
        return ClaudeStepResult(step=step, model=model, text=text)

    monkeypatch.setattr(agent_client, "run_claude_step", fake_claude)
    run(run_module.run(empty_db, kind="daily"))
    assert run(careers_snapshot.load_snapshot(empty_db, company_id))["empty_runs"] == 0
    assert run(empty_db.first("SELECT careers_page_flagged FROM companies WHERE id = ?", company_id))["careers_page_flagged"] == 0


# --- concurrency ----------------------------------------------------------------

def test_up_to_four_extractions_run_at_once(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    ids = [_insert_company(empty_db, name=f"Co{i}", careers_url=f"https://co{i}.example/careers") for i in range(8)]
    monkeypatch.setattr(
        run_module.careers_pages, "run_careers_pages",
        lambda candidates, **kw: _outcome(*[_page_outcome(i, _long_page(f"page {i}")) for i in ids]),
    )
    lock = threading.Lock()
    active = {"now": 0, "max": 0}

    def fake_claude(step, prompt, *, model, **kwargs):
        if step == "careers_extraction":
            with lock:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(0.15)
            with lock:
                active["now"] -= 1
        return ClaudeStepResult(step=step, model=model, text=_default_reply(step))

    monkeypatch.setattr(agent_client, "run_claude_step", fake_claude)
    run(run_module.run(empty_db, kind="daily"))
    assert active["max"] == 4


# --- run order and the time budget ----------------------------------------------

def test_classification_runs_before_careers_and_earlier_postings_survive_a_careers_failure(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    _insert_company(empty_db, name="Feedco", feed_type="greenhouse", feed_id="feedco", careers_url=None)
    _insert_company(empty_db, name="Pagesco")
    order = []

    def fake_run_fetcher(company, session=None):
        postings = []
        if company["name"] == "Feedco":
            postings = [FetchedPosting(
                company_name="Feedco", title="Intern Product Manager Search Maps Cloud Seattle",
                url="https://jobs.example/1", location_text="Seattle, WA",
            )]
        return FetchResult(company_id=company["id"], company_name=company["name"], postings=postings)

    monkeypatch.setattr(run_module, "run_fetcher", fake_run_fetcher)

    def careers_that_runs_out_of_time(candidates, **kw):
        order.append("careers")
        raise RuntimeError("careers step blew up")

    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages", careers_that_runs_out_of_time)

    def fake_claude(step, prompt, *, model, **kwargs):
        order.append(step)
        if step == "classification":
            posting_id = int(re.search(r"id=(\d+)", prompt).group(1))
            text = json.dumps({"decisions": [{"id": posting_id, "decision": "match",
                                               "matched_title": "MBA Product Manager Intern", "reason": "ok"}]})
            return ClaudeStepResult(step=step, model=model, text=text)
        return ClaudeStepResult(step=step, model=model, text=_default_reply(step))

    monkeypatch.setattr(agent_client, "run_claude_step", fake_claude)
    result = run(run_module.run(empty_db, kind="daily"))

    assert order.index("classification") < order.index("careers")
    assert order.index("careers") < order.index("improvement")
    if "scout" in order:
        assert order.index("careers") < order.index("scout")
    postings = run(empty_db.all("SELECT * FROM postings"))
    assert len(postings) == 1 and postings[0]["matched_title"] == "MBA Product Manager Intern"
    assert result["new_count"] == 1


def test_careers_budget_defaults_to_10_minutes(empty_db):
    row = run(empty_db.first("SELECT careers_minutes_per_run FROM budget_settings WHERE id = 1"))
    assert row["careers_minutes_per_run"] == 10


def test_careers_step_gets_a_deadline_from_careers_minutes_per_run(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    _insert_company(empty_db)
    run(empty_db.run("UPDATE budget_settings SET careers_minutes_per_run = 5 WHERE id = 1"))
    monkeypatch.setattr(run_module, "clock", lambda: 1000.0)
    seen = {}

    def fake_pages(candidates, **kw):
        seen.update(kw)
        return _outcome()

    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages", fake_pages)
    monkeypatch.setattr(agent_client, "run_claude_step",
                        lambda step, prompt, *, model, **kw: ClaudeStepResult(step=step, model=model, text=_default_reply(step)))
    run(run_module.run(empty_db, kind="daily"))
    # 5 minutes in total; page loading gets 60% of it.
    assert seen["deadline"] == 1000.0 + 5 * 60 * run_module.CAREERS_FETCH_SHARE


def test_fetching_stops_at_the_deadline_and_leaves_the_rest_for_next_run():
    companies = [{"id": i, "careers_url": f"https://c{i}.example/careers"} for i in range(1, 6)]
    ticks = {"n": 0}

    def clock():
        ticks["n"] += 1
        return float(ticks["n"])

    outcome = cp.run_careers_pages(
        companies, account_id=None, token=None, robots_check=lambda url, session=None: True,
        backup_fetch=lambda url: "page text", deadline=8.0, clock=clock,
    )
    assert 0 < len(outcome.pages) < 5
    assert [p.company_id for p in outcome.pages] == list(range(1, len(outcome.pages) + 1))


def test_out_of_time_resumes_next_run_with_the_least_recently_checked(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    a = _insert_company(empty_db, name="A", careers_url="https://a.example/careers")
    b = _insert_company(empty_db, name="B", careers_url="https://b.example/careers")
    c = _insert_company(empty_db, name="C", careers_url="https://c.example/careers")
    # C was checked longest ago, then A; B never.
    for company_id, when in ((c, "2026-09-01T08:00:00"), (a, "2026-09-20T08:00:00")):
        run(careers_snapshot.record_changed(
            empty_db, company_id, "h", method="browser_run", now_iso=when, existing=None))
    orders = []

    def fake_pages(candidates, **kw):
        orders.append([x["id"] for x in candidates])
        first = candidates[0]  # time runs out after one page
        return _outcome(_page_outcome(first["id"], _long_page(first["name"])))

    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages", fake_pages)
    monkeypatch.setattr(agent_client, "run_claude_step",
                        lambda step, prompt, *, model, **kw: ClaudeStepResult(step=step, model=model, text=_default_reply(step)))
    run(run_module.run(empty_db, kind="daily"))
    run(run_module.run(empty_db, kind="daily"))
    run(run_module.run(empty_db, kind="daily"))
    assert orders[0] == [b, c, a]  # never checked first, then least recently checked
    assert orders[1][0] == c  # B was just checked; C is next
    assert orders[2][0] == a


def test_page_not_extracted_before_the_deadline_is_left_for_next_run(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    company_id = _insert_company(empty_db)
    now = {"t": 0.0}
    monkeypatch.setattr(run_module, "clock", lambda: now["t"])

    def slow_pages(candidates, **kw):
        now["t"] += 11 * 60  # page loading used the whole 10 minutes
        return _outcome(_page_outcome(company_id, _long_page("jobs")))

    monkeypatch.setattr(run_module.careers_pages, "run_careers_pages", slow_pages)
    steps = []

    def fake_claude(step, prompt, *, model, **kwargs):
        steps.append(step)
        return ClaudeStepResult(step=step, model=model, text=_default_reply(step))

    monkeypatch.setattr(agent_client, "run_claude_step", fake_claude)
    result = run(run_module.run(empty_db, kind="daily"))
    assert "careers_extraction" not in steps
    assert run(careers_snapshot.load_snapshot(empty_db, company_id)) is None  # read again next run
    assert "careers limit" in result["summary"]
    assert "improvement" in steps  # the rest of the run still finished


# --- run time ---------------------------------------------------------------------

def test_summary_shows_run_time(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    _insert_company(empty_db, careers_url=None)
    monkeypatch.setattr(agent_client, "run_claude_step",
                        lambda step, prompt, *, model, **kw: ClaudeStepResult(step=step, model=model, text=_default_reply(step)))
    result = run(run_module.run(empty_db, kind="daily"))
    assert "Run time:" in result["summary"]
    assert "more than 30 minutes" not in result["summary"]
    assert result["run_minutes"] is not None


def test_run_over_30_minutes_warns_in_summary_and_alerts(empty_db, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    _insert_company(empty_db, careers_url=None)
    monkeypatch.setattr(run_module, "_minutes_between", lambda start, end: 45.0)
    monkeypatch.setattr(agent_client, "run_claude_step",
                        lambda step, prompt, *, model, **kw: ClaudeStepResult(step=step, model=model, text=_default_reply(step)))
    result = run(run_module.run(empty_db, kind="daily"))
    assert "more than 30 minutes" in result["summary"]
    alert_types = [a["type"] for a in run(empty_db.all("SELECT type FROM alerts"))]
    assert "long_run" in alert_types


def test_long_run_alert_only_over_the_limit():
    assert evaluate_long_run_alert(30.0) is None
    assert evaluate_long_run_alert(None) is None
    assert evaluate_long_run_alert(31.0).type == "long_run"


def test_stuck_run_is_closed_with_a_finish_time(empty_db):
    run_id = run(empty_db.run(
        "INSERT INTO runs (kind, started_at, status) VALUES ('daily', '2026-10-06T08:00:00-05:00', 'running')"))
    closed = run(close_stuck_runs(empty_db, reason="the job hit its time limit", now_iso="2026-10-06T09:00:00-05:00"))
    assert closed == 1
    row = run(empty_db.first("SELECT * FROM runs WHERE id = ?", run_id))
    assert row["status"] == "failed" and row["finished_at"] == "2026-10-06T09:00:00-05:00"
    assert "after 60 minutes" in row["summary"]
    assert run(close_stuck_runs(empty_db, reason="x")) == 0
