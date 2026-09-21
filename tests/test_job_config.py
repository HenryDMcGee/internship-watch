import asyncio
import json

from job.agent.discovery import order_companies
from job.config import load_watchlist_setting


def run(coro):
    return asyncio.run(coro)


def test_load_watchlist_setting_returns_empty_list_when_unset(empty_db):
    assert run(load_watchlist_setting(empty_db)) == []


def test_load_watchlist_setting_extracts_company_names_from_note_objects(empty_db):
    # The real watchlist setting (seed/settings.json, the dashboard's watch-list panel)
    # stores objects with a "company" name and a "note", not bare strings. Returning the
    # raw objects crashed discovery for every run: `order_companies` calls `.strip()` on
    # each entry expecting a string and got "'dict' object has no attribute 'strip'"
    # instead (issue #20, run 11).
    entries = [
        {"company": "ServiceNow", "note": "Posted 2026 MBA PM internships on Sep 25, 2025."},
        {"company": "Capital One", "note": "Posted its 2026 MBA Product Intern on Sep 26, 2025."},
    ]
    run(empty_db.run("INSERT INTO settings (key, value) VALUES (?, ?)", "watchlist", json.dumps(entries)))

    watchlist = run(load_watchlist_setting(empty_db))

    assert watchlist == ["ServiceNow", "Capital One"]
    # Must not raise -- this is the exact call `run_agent_steps` makes with the loaded
    # watchlist before ever reaching Claude.
    ordered = order_companies([{"name": "Capital One"}, {"name": "Acme"}], watchlist)
    assert [c["name"] for c in ordered] == ["Capital One", "Acme"]


def test_load_watchlist_setting_still_accepts_bare_strings(empty_db):
    run(empty_db.run(
        "INSERT INTO settings (key, value) VALUES (?, ?)", "watchlist", json.dumps(["Acme", "Globex"])
    ))
    assert run(load_watchlist_setting(empty_db)) == ["Acme", "Globex"]
