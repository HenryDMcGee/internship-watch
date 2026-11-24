"""BR-001.3 and BR-001.4: `db/migrations/0018_*` gives each company's feed its
own address and a failure count, copies the Workday site links already on
file, and takes China-based companies off the ranked list."""
import sqlite3
from pathlib import Path

MIGRATIONS = sorted((Path(__file__).resolve().parent.parent / "db" / "migrations").glob("*.sql"))
NUMBER = 18


def _migrated_before(number: int) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for path in MIGRATIONS:
        if int(path.name[:4]) < number:
            conn.executescript(path.read_text(encoding="utf-8"))
    return conn


def _apply(conn: sqlite3.Connection, number: int) -> None:
    path = next(p for p in MIGRATIONS if p.name.startswith(f"{number:04d}_"))
    conn.executescript(path.read_text(encoding="utf-8"))


def _company(conn, name):
    return dict(conn.execute("SELECT * FROM companies WHERE name = ?", (name,)).fetchone())


def _insert(conn, **row):
    columns = ", ".join(row.keys())
    conn.execute(f"INSERT INTO companies ({columns}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))


def test_the_migration_exists():
    assert any(p.name.startswith(f"{NUMBER:04d}_") for p in MIGRATIONS)


def test_workday_site_links_on_file_become_the_feed_address():
    conn = _migrated_before(NUMBER)
    _insert(conn, name="Zebra", status="pinned", feed_type="workday",
            careers_url="https://zebra.wd1.myworkdayjobs.com/Zebra_Careers")
    _insert(conn, name="Micron", status="pinned", feed_type="workday",
            careers_url="https://www.micron.com/careers/university-recruiting")
    _insert(conn, name="Bare", status="pinned", feed_type="workday", careers_url="https://bare.wd1.myworkdayjobs.com")
    _insert(conn, name="Acme", status="pinned", feed_type="greenhouse", careers_url="https://boards.greenhouse.io/acme")

    _apply(conn, NUMBER)

    assert _company(conn, "Zebra")["feed_url"] == "https://zebra.wd1.myworkdayjobs.com/Zebra_Careers"
    assert _company(conn, "Micron")["feed_url"] is None  # a marketing page is not a feed address
    assert _company(conn, "Bare")["feed_url"] is None  # names no site
    assert _company(conn, "Acme")["feed_url"] is None  # Greenhouse is read by its board name
    # Nothing about the careers page itself changes.
    assert _company(conn, "Micron")["careers_url"] == "https://www.micron.com/careers/university-recruiting"


def test_every_company_starts_with_no_recorded_failures():
    conn = _migrated_before(NUMBER)
    _insert(conn, name="Acme", status="pinned", feed_type="greenhouse", last_feed_status="error")

    _apply(conn, NUMBER)

    assert _company(conn, "Acme")["feed_failures"] == 0


def test_ranked_china_based_companies_are_removed_and_pins_are_kept():
    conn = _migrated_before(NUMBER)
    _insert(conn, name="JD.com", status="ranked", hq_country="CN", feed_type="greenhouse")
    _insert(conn, name="Tencent", status="ranked", hq_country="CN")
    _insert(conn, name="Pinned CN", status="pinned", hq_country="CN")
    _insert(conn, name="Sony", status="ranked", hq_country="JP")

    _apply(conn, NUMBER)

    assert _company(conn, "JD.com")["status"] == "removed"
    assert _company(conn, "Tencent")["status"] == "removed"
    assert _company(conn, "Pinned CN")["status"] == "pinned"
    assert _company(conn, "Sony")["status"] == "ranked"
