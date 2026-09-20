"""Quarterly ranking refresh.

`docs/plan.md`'s phase 1 chose a manually-compiled snapshot as the ranking
source (see `seed/ranking.csv`'s header comment): Henry refreshes that file
by hand each quarter through a pull request, and the job applies whatever
is currently committed there to the `companies` table. Pinned companies
never lose their pin, even if they drop out of the new top 100.
"""
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RANKING_CSV = ROOT / "seed" / "ranking.csv"
API_SRC = ROOT / "api" / "src"
if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))

from companies import detect_feed, normalize_company_name  # noqa: E402

REFRESH_MONTHS = {1, 4, 7, 10}


def is_quarterly_refresh_day(day: date) -> bool:
    """True on the first weekday (Mon-Fri) of January, April, July, or October."""
    if day.month not in REFRESH_MONTHS:
        return False
    if day.weekday() >= 5:  # Saturday or Sunday
        return False
    first_of_month = day.replace(day=1)
    # The first weekday of the month is day 1, 2, or 3 depending on what
    # weekday the 1st falls on (Sat -> the 3rd, Sun -> the 2nd, else the 1st).
    offset = {5: 2, 6: 1}.get(first_of_month.weekday(), 0)
    return day.day == 1 + offset


@dataclass
class RankingRow:
    rank: int
    name: str
    market_cap_usd: float
    hq_country: str
    as_of_date: str
    source: str
    careers_url: str | None = None


def load_ranking_csv(path: Path = RANKING_CSV) -> list[RankingRow]:
    with path.open(newline="", encoding="utf-8") as f:
        data_lines = [line for line in f if not line.startswith("#") and line.strip()]
    rows = []
    for row in csv.DictReader(data_lines):
        rows.append(
            RankingRow(
                rank=int(row["rank"]),
                name=row["name"].strip(),
                market_cap_usd=float(row["market_cap_usd_billions"]) * 1_000_000_000,
                hq_country=row["hq_country"].strip(),
                as_of_date=row["as_of_date"].strip(),
                source=row["source"].strip(),
                careers_url=(row.get("careers_url") or "").strip() or None,
            )
        )
    return rows


async def refresh_ranking(db, *, ranking_csv: Path = RANKING_CSV, today: str | None = None) -> dict:
    """Apply `seed/ranking.csv` to the `companies` table. Returns a summary dict."""
    today = today or date.today().isoformat()
    rows = load_ranking_csv(ranking_csv)
    by_normalized = {normalize_company_name(r.name): r for r in rows}

    existing = await db.all("SELECT id, name, status FROM companies")
    existing_by_normalized = {normalize_company_name(r["name"]): r for r in existing}

    updated = 0
    inserted = 0
    removed = 0

    for row in rows:
        key = normalize_company_name(row.name)
        existing_row = existing_by_normalized.get(key)
        if existing_row is None:
            feed_type, feed_id = detect_feed(row.careers_url)
            await db.run(
                """INSERT INTO companies
                   (name, status, rank, market_cap_usd, ranking_date, ranking_source, hq_country,
                    careers_url, feed_type, feed_id, added_by, added_at)
                   VALUES (?, 'ranked', ?, ?, ?, ?, ?, ?, ?, ?, 'quarterly refresh', ?)""",
                row.name, row.rank, row.market_cap_usd, row.as_of_date, row.source, row.hq_country,
                row.careers_url, feed_type, feed_id, today,
            )
            inserted += 1
        else:
            new_status = "pinned" if existing_row["status"] == "pinned" else "ranked"
            await db.run(
                """UPDATE companies
                   SET rank = ?, market_cap_usd = ?, ranking_date = ?, ranking_source = ?, hq_country = ?, status = ?
                   WHERE id = ?""",
                row.rank, row.market_cap_usd, row.as_of_date, row.source, row.hq_country, new_status, existing_row["id"],
            )
            updated += 1

        await db.run(
            "INSERT INTO ranking_snapshots (snapshot_date, source, company_name, rank, market_cap_usd) VALUES (?, ?, ?, ?, ?)",
            today, row.source, row.name, row.rank, row.market_cap_usd,
        )

    for key, existing_row in existing_by_normalized.items():
        if existing_row["status"] == "ranked" and key not in by_normalized:
            await db.run("UPDATE companies SET status = 'removed' WHERE id = ?", existing_row["id"])
            removed += 1

    return {"updated": updated, "inserted": inserted, "removed": removed, "as_of_date": today}
