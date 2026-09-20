import asyncio
from datetime import date

from job.ranking import is_quarterly_refresh_day, refresh_ranking


def run(coro):
    return asyncio.run(coro)


def test_is_quarterly_refresh_day_true_on_first_weekday():
    assert is_quarterly_refresh_day(date(2027, 1, 1)) is True  # Friday
    assert is_quarterly_refresh_day(date(2029, 1, 1)) is True  # Monday
    assert is_quarterly_refresh_day(date(2026, 4, 1)) is True  # Wednesday
    assert is_quarterly_refresh_day(date(2026, 7, 1)) is True  # Wednesday
    assert is_quarterly_refresh_day(date(2026, 10, 1)) is True  # Thursday


def test_is_quarterly_refresh_day_skips_weekend_first_of_month():
    assert is_quarterly_refresh_day(date(2028, 1, 1)) is False  # Saturday
    assert is_quarterly_refresh_day(date(2028, 1, 3)) is True  # following Monday
    assert is_quarterly_refresh_day(date(2029, 7, 1)) is False  # Sunday
    assert is_quarterly_refresh_day(date(2029, 7, 2)) is True  # following Monday
    assert is_quarterly_refresh_day(date(2028, 10, 1)) is False  # Sunday
    assert is_quarterly_refresh_day(date(2028, 10, 2)) is True  # following Monday


def test_is_quarterly_refresh_day_false_outside_refresh_months():
    assert is_quarterly_refresh_day(date(2026, 8, 1)) is False


def test_is_quarterly_refresh_day_false_for_later_weekday_in_month():
    assert is_quarterly_refresh_day(date(2026, 1, 2)) is False  # Friday, but not the 1st weekday
    assert is_quarterly_refresh_day(date(2026, 1, 3)) is False  # Saturday


def test_refresh_ranking_updates_and_preserves_pins(empty_db, tmp_path):
    csv_path = tmp_path / "ranking.csv"
    csv_path.write_text(
        "# comment line\n"
        "rank,name,market_cap_usd_billions,hq_country,as_of_date,source\n"
        "1,Acme,100,US,2027-01-01,manual snapshot\n"
        "2,Globex,50,JP,2027-01-01,manual snapshot\n",
        encoding="utf-8",
    )

    run(empty_db.run("INSERT INTO companies (name, status, rank) VALUES ('Acme', 'ranked', 5)"))
    pinned_id = run(empty_db.run("INSERT INTO companies (name, status, rank) VALUES ('Initech', 'pinned', NULL)"))
    dropped_id = run(empty_db.run("INSERT INTO companies (name, status, rank) VALUES ('Dropped Co', 'ranked', 99)"))

    summary = run(refresh_ranking(empty_db, ranking_csv=csv_path, today="2027-01-01"))

    assert summary["inserted"] == 1  # Globex
    assert summary["updated"] == 1  # Acme
    assert summary["removed"] == 1  # Dropped Co

    acme = run(empty_db.first("SELECT * FROM companies WHERE name = 'Acme'"))
    assert acme["rank"] == 1
    assert acme["status"] == "ranked"

    pinned = run(empty_db.first("SELECT * FROM companies WHERE id = ?", pinned_id))
    assert pinned["status"] == "pinned"  # pin survives even though not in the new list

    dropped = run(empty_db.first("SELECT * FROM companies WHERE id = ?", dropped_id))
    assert dropped["status"] == "removed"

    globex = run(empty_db.first("SELECT * FROM companies WHERE name = 'Globex'"))
    assert globex["status"] == "ranked"
    assert globex["rank"] == 2

    snapshots = run(empty_db.all("SELECT * FROM ranking_snapshots"))
    assert len(snapshots) == 2


def test_refresh_ranking_detects_feed_for_newly_inserted_company(empty_db, tmp_path):
    csv_path = tmp_path / "ranking.csv"
    csv_path.write_text(
        "rank,name,market_cap_usd_billions,hq_country,as_of_date,source,careers_url\n"
        "1,Acme,100,US,2027-01-01,manual snapshot,https://boards.greenhouse.io/acme\n",
        encoding="utf-8",
    )

    run(refresh_ranking(empty_db, ranking_csv=csv_path, today="2027-01-01"))

    acme = run(empty_db.first("SELECT * FROM companies WHERE name = 'Acme'"))
    assert acme["careers_url"] == "https://boards.greenhouse.io/acme"
    assert acme["feed_type"] == "greenhouse"
    assert acme["feed_id"] == "acme"
