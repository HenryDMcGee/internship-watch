import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / "api"
SCHEMA_SQL = (ROOT / "db" / "migrations" / "0001_init.sql").read_text(encoding="utf-8")

if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from src.db import SqliteDatabase  # noqa: E402


def fresh_schema_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


@pytest.fixture
def empty_db() -> SqliteDatabase:
    return SqliteDatabase(fresh_schema_connection())
