"""Phase 9: the one-time migration of the stored budget settings to the
dollars-only shape (`db/migrations/0017_*`). Existing dollar values must
survive, the removed keys must go, and the change must show in the budgets
change history."""
import asyncio
import sqlite3
from pathlib import Path

MIGRATIONS = sorted((Path(__file__).resolve().parent.parent / "db" / "migrations").glob("*.sql"))
PHASE_9 = 17
REMOVED = ("billing_mode", "daily_token_budget", "weekly_token_budget")


def run(coro):
    return asyncio.run(coro)


def _migrated_before(number: int) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for path in MIGRATIONS:
        if int(path.name[:4]) < number:
            conn.executescript(path.read_text(encoding="utf-8"))
    return conn


def _apply(conn: sqlite3.Connection, number: int) -> None:
    path = next(p for p in MIGRATIONS if p.name.startswith(f"{number:04d}_"))
    conn.executescript(path.read_text(encoding="utf-8"))


def _settings_row(conn: sqlite3.Connection) -> dict:
    cur = conn.execute("SELECT * FROM budget_settings WHERE id = 1")
    return dict(zip([d[0] for d in cur.description], cur.fetchone()))


def test_the_phase_9_migration_exists():
    assert any(p.name.startswith(f"{PHASE_9:04d}_") for p in MIGRATIONS)


def test_migration_strips_the_removed_keys_and_keeps_every_other_value():
    conn = _migrated_before(PHASE_9)
    conn.execute(
        "UPDATE budget_settings SET billing_mode = 'api_key', daily_token_budget = 123, weekly_token_budget = 456, "
        "daily_usd_budget = 2.25, monthly_usd_budget = 41.5, daily_search_budget = 12, "
        "share_discovery = 0.5, share_scout = 0.3, share_classification = 0.1, share_improvement = 0.1, "
        "model_discovery = 'haiku', search_limit_per_run = 9, search_limit_per_company = 2, "
        "browser_checks_per_run = 7, feedback_issues_per_day = 3 WHERE id = 1"
    )
    before = _settings_row(conn)

    _apply(conn, PHASE_9)
    after = _settings_row(conn)

    for key in REMOVED:
        assert key in before
        assert key not in after
    for key, value in before.items():
        if key not in REMOVED:
            assert after[key] == value, key
    assert after["public_feedback_daily_limit"] == 20


def test_migration_records_the_removed_values_in_the_change_history():
    conn = _migrated_before(PHASE_9)
    conn.execute("UPDATE budget_settings SET billing_mode = 'api_key', daily_token_budget = 123 WHERE id = 1")

    _apply(conn, PHASE_9)

    rows = conn.execute("SELECT field, old_value, new_value, changed_at FROM budget_changes ORDER BY id").fetchall()
    by_field = {r[0]: r for r in rows}
    assert set(REMOVED) <= set(by_field)
    assert by_field["billing_mode"][1] == "api_key"
    assert by_field["daily_token_budget"][1] == "123"
    assert by_field["weekly_token_budget"][1] == "25000000"
    for field in REMOVED:
        assert by_field[field][2]  # says what happened, never blank
        assert by_field[field][3]  # dated


def test_migration_creates_the_public_board_views_table():
    conn = _migrated_before(PHASE_9 + 1)
    conn.execute("INSERT INTO public_board_views (day, views) VALUES ('2026-10-20', 1) "
                 "ON CONFLICT(day) DO UPDATE SET views = views + 1")
    conn.execute("INSERT INTO public_board_views (day, views) VALUES ('2026-10-20', 1) "
                 "ON CONFLICT(day) DO UPDATE SET views = views + 1")
    assert conn.execute("SELECT views FROM public_board_views WHERE day = '2026-10-20'").fetchone()[0] == 2


def test_a_fresh_database_has_the_dollars_only_shape(empty_db):
    row = run(empty_db.first("SELECT * FROM budget_settings WHERE id = 1"))
    for key in REMOVED:
        assert key not in row
    assert row["public_feedback_daily_limit"] == 20
    assert row["daily_usd_budget"] == 1.50
    assert row["monthly_usd_budget"] == 30.00
