import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / "api"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SCHEMA_SQL = (ROOT / "db" / "migrations" / "0001_init.sql").read_text(encoding="utf-8")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
# api/src/main.py uses plain module imports (e.g. `import companies`) to match
# how Cloudflare's Python Workers runtime loads it, so api/src must also be on
# sys.path for those imports to resolve when pytest imports src.main.
if str(API_DIR / "src") not in sys.path:
    sys.path.insert(0, str(API_DIR / "src"))

from src.db import SqliteDatabase  # noqa: E402


def fresh_schema_connection() -> sqlite3.Connection:
    # FastAPI's TestClient runs the app in a worker thread, but requests are
    # dispatched to it one at a time, so it's safe for the single in-memory
    # connection created here (in the test thread) to be reused there.
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


@pytest.fixture
def empty_db() -> SqliteDatabase:
    return SqliteDatabase(fresh_schema_connection())
