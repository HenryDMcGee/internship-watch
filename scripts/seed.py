"""Load seed/ into the local D1 database, or the real one with --remote.

Run from the repository root, after applying migrations:

    npx wrangler d1 migrations apply internship-watch-db --local
    python scripts/seed.py                 # local
    python scripts/seed.py --remote        # real database (docs/deploy.md step 5)

This maps the old dashboard's field names (`firstSeen`, `statusNote`, and
so on, in seed/postings.json etc.) to the new D1 schema in
db/migrations/0001_init.sql, and imports the top-100 ranking
(seed/ranking.csv) plus a one-time pin review list for companies in
seed/companies.json that the ranking doesn't include.

`build_statements()` and the `map_*` functions below have no dependency on
`wrangler` or a real database, so pytest exercises the mapping directly
against an in-memory SQLite database built from the same migration file.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SEED_DIR = ROOT / "seed"
API_DIR = ROOT / "api"
MIGRATIONS_DIR = ROOT / "db" / "migrations"
DATABASE_NAME = "internship-watch-db"

sys.path.insert(0, str(API_DIR / "src"))
from companies import detect_feed, normalize_company_name  # noqa: E402


Statement = tuple[str, tuple[Any, ...]]


def load_json(name: str) -> Any:
    return json.loads((SEED_DIR / name).read_text(encoding="utf-8"))


def load_ranking_csv(path: Path | None = None) -> list[dict]:
    path = path or (SEED_DIR / "ranking.csv")
    with path.open(newline="", encoding="utf-8") as f:
        data_lines = [line for line in f if not line.startswith("#") and line.strip()]
    rows = []
    for row in csv.DictReader(data_lines):
        rows.append(
            {
                "rank": int(row["rank"]),
                "name": row["name"].strip(),
                "market_cap_usd": float(row["market_cap_usd_billions"]) * 1_000_000_000,
                "hq_country": row["hq_country"].strip(),
                "as_of_date": row["as_of_date"].strip(),
                "source": row["source"].strip(),
                "careers_url": (row.get("careers_url") or "").strip() or None,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Field mapping: old seed shape -> new schema
# ---------------------------------------------------------------------------

def map_settings(settings: dict) -> list[tuple[str, Any]]:
    locations = settings.get("locations", {"countries": ["US", "JP"], "include_remote": False})
    entries = [
        ("locations", locations),
        ("watchlist", settings.get("watchlist", [])),
        ("timezone", settings.get("timezone", "America/Chicago")),
        ("daily_run_local_time", settings.get("daily_run_local_time", "08:00")),
        ("last_run", settings.get("last_run", "")),
    ]
    return [(key, json.dumps(value)) for key, value in entries]


def map_source(old: dict) -> dict:
    return {
        "name": old.get("name", ""),
        "url": old.get("url", ""),
        "kind": old.get("kind", "Job boards"),
        "covers": old.get("covers", ""),
        "status": "active",
        "discovered_by": "seed",
        "discovered_at": None,
        "found_count": int(old.get("found", 0)),
        "last_checked": old.get("lastChecked") or None,
        "notes": old.get("notes", ""),
    }


def map_run(old: dict) -> dict:
    return {
        "kind": "daily",
        "started_at": old.get("date"),
        "finished_at": old.get("date"),
        "status": "completed",
        "new_count": int(old.get("newCount", 0)),
        "summary": old.get("summary", ""),
        "next_notes": old.get("next", ""),
    }


def map_posting(old: dict) -> dict:
    location = old.get("location") or ""
    return {
        "company_name": old.get("company", ""),
        "title": old.get("title", ""),
        "title_en": None,
        "matched_title": None,
        "url": old.get("url", ""),
        "mirror_url": old.get("mirrorUrl"),
        "source_name": old.get("source"),
        "locations": [location] if location else [],
        # Seeded from Henry's existing curated dashboard, so these already
        # matched his location settings.
        "location_match": "match",
        "posted_date": old.get("postedDate") or None,
        "first_seen": old.get("firstSeen", ""),
        "status": old.get("status", "unverified"),
        "status_note": old.get("statusNote", ""),
        "verified_via": None,
        "verified_at": old.get("statusChecked") or None,
        "user_status": None,
        "user_status_at": None,
        "applied": bool(old.get("applied", False)),
        "note": old.get("note", ""),
    }


def map_ranked_company(row: dict) -> dict:
    feed_type, feed_id = detect_feed(row.get("careers_url"))
    return {
        "name": row["name"],
        "status": "ranked",
        "rank": row["rank"],
        "market_cap_usd": row["market_cap_usd"],
        "ranking_date": row["as_of_date"],
        "ranking_source": row["source"],
        "hq_country": row["hq_country"],
        "careers_url": row.get("careers_url"),
        "feed_type": feed_type,
        "feed_id": feed_id,
        "added_by": "seed (ranking)",
        "added_at": row["as_of_date"],
    }


def map_review_company(name: str) -> dict:
    return {
        "name": name,
        "status": "review",
        "rank": None,
        "market_cap_usd": None,
        "ranking_date": None,
        "ranking_source": None,
        "hq_country": None,
        "added_by": "seed (companies.json, not in ranking)",
        "added_at": None,
    }


# ---------------------------------------------------------------------------
# Building INSERT statements
# ---------------------------------------------------------------------------

def build_statements(seed_dir: Path = SEED_DIR, ranking_csv: Path | None = None) -> list[Statement]:
    ranking_csv = ranking_csv or (seed_dir / "ranking.csv")
    postings_old = json.loads((seed_dir / "postings.json").read_text(encoding="utf-8"))
    sources_old = json.loads((seed_dir / "sources.json").read_text(encoding="utf-8"))
    runs_old = json.loads((seed_dir / "runs.json").read_text(encoding="utf-8"))
    settings_old = json.loads((seed_dir / "settings.json").read_text(encoding="utf-8"))
    companies_old = json.loads((seed_dir / "companies.json").read_text(encoding="utf-8"))
    ranking_rows = load_ranking_csv(ranking_csv)

    statements: list[Statement] = []

    for key, value in map_settings(settings_old):
        statements.append(
            ("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
        )

    ranking_by_normalized = {normalize_company_name(r["name"]): r for r in ranking_rows}
    for row in ranking_rows:
        c = map_ranked_company(row)
        statements.append((
            """INSERT INTO companies
               (name, status, rank, market_cap_usd, ranking_date, ranking_source, hq_country,
                careers_url, feed_type, feed_id, added_by, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (c["name"], c["status"], c["rank"], c["market_cap_usd"], c["ranking_date"],
             c["ranking_source"], c["hq_country"], c["careers_url"], c["feed_type"], c["feed_id"],
             c["added_by"], c["added_at"]),
        ))
        statements.append((
            "INSERT INTO ranking_snapshots (snapshot_date, source, company_name, rank, market_cap_usd) VALUES (?, ?, ?, ?, ?)",
            (row["as_of_date"], row["source"], row["name"], row["rank"], row["market_cap_usd"]),
        ))

    for name in companies_old.get("companies", []):
        if normalize_company_name(name) in ranking_by_normalized:
            continue
        c = map_review_company(name)
        statements.append((
            "INSERT INTO companies (name, status, added_by, added_at) VALUES (?, ?, ?, ?)",
            (c["name"], c["status"], c["added_by"], c["added_at"]),
        ))

    for old in sources_old:
        s = map_source(old)
        statements.append((
            """INSERT INTO sources (name, url, kind, covers, status, discovered_by, discovered_at, found_count, last_checked, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (s["name"], s["url"], s["kind"], s["covers"], s["status"], s["discovered_by"],
             s["discovered_at"], s["found_count"], s["last_checked"], s["notes"]),
        ))

    for old in postings_old:
        p = map_posting(old)
        statements.append((
            """INSERT INTO postings
               (company_name, title, title_en, matched_title, url, mirror_url, source_name,
                locations, location_match, posted_date, first_seen, status, status_note,
                verified_via, verified_at, user_status, user_status_at, applied, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (p["company_name"], p["title"], p["title_en"], p["matched_title"], p["url"],
             p["mirror_url"], p["source_name"], json.dumps(p["locations"]), p["location_match"],
             p["posted_date"], p["first_seen"], p["status"], p["status_note"], p["verified_via"],
             p["verified_at"], p["user_status"], p["user_status_at"], 1 if p["applied"] else 0, p["note"]),
        ))

    for old in runs_old:
        r = map_run(old)
        statements.append((
            "INSERT INTO runs (kind, started_at, finished_at, status, new_count, summary, next_notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (r["kind"], r["started_at"], r["finished_at"], r["status"], r["new_count"], r["summary"], r["next_notes"]),
        ))

    return statements


def _render_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def render_sql(statements: list[Statement]) -> str:
    lines = []
    for sql, params in statements:
        rendered = sql
        for param in params:
            rendered = rendered.replace("?", _render_literal(param), 1)
        lines.append(rendered.strip() + ";")
    return "\n".join(lines) + "\n"


def resolve_executable(name: str) -> str | None:
    """Return the full path to a command-line program, or None if it isn't installed.

    On Windows, `npx` is really `npx.cmd`. subprocess can't launch it by the
    bare name, but shutil.which finds the real file (npx.cmd on Windows, npx
    elsewhere), and subprocess can run it from its full path.
    """
    return shutil.which(name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", action="store_true", help="Seed the real database instead of the local one")
    args = parser.parse_args()

    statements = build_statements()
    sql_text = render_sql(statements)

    npx = resolve_executable("npx")
    if npx is None:
        print(
            "Couldn't find npx. Install Node.js (current LTS), open a new terminal, "
            "and run this script again. npx comes with Node.js.",
            file=sys.stderr,
        )
        return 1

    # Write as UTF-8 so non-English text survives on Windows, where the
    # default file encoding isn't UTF-8.
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as f:
        f.write(sql_text)
        sql_path = Path(f.name)

    target = "--remote" if args.remote else "--local"
    args_after_npx = ["wrangler", "d1", "execute", DATABASE_NAME, target, f"--file={sql_path}"]
    print(f"Running: npx {' '.join(args_after_npx)} (from {API_DIR})")
    try:
        result = subprocess.run([npx, *args_after_npx], cwd=API_DIR)
    finally:
        sql_path.unlink(missing_ok=True)
    if result.returncode != 0:
        return result.returncode

    print(f"Seeded {DATABASE_NAME} ({'remote' if args.remote else 'local'}) with {len(statements)} statements.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
