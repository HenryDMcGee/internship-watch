import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / "api"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
MIGRATIONS_DIR = ROOT / "db" / "migrations"
SCHEMA_SQL = "\n".join(
    p.read_text(encoding="utf-8") for p in sorted(MIGRATIONS_DIR.glob("*.sql"))
)

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


def adapt_discovery_reply(text: str, prompt: str) -> str:
    """Turns an old-style discovery reply (`{"results": [...]}`) into the per-company
    batch reply discovery now requires (issue #63): one entry per company named in
    the prompt, marked searched, with that company's postings or "none found". Lets
    tests that only care what discovery *found* keep stating it simply.
    """
    import json
    import re

    try:
        payload = json.loads(text)
    except ValueError:
        return text  # a test of a reply that isn't JSON at all
    if "results" not in payload:
        return text
    match = re.search(r"watch list: (.*?)\.\nLook only", prompt, re.S)
    names = [n.strip() for n in match.group(1).split(";")] if match else []
    by_company: dict = {}
    for r in payload["results"]:
        by_company.setdefault(r["company_name"], []).append(
            {k: r[k] for k in ("title", "url", "location_text", "posted_date") if k in r}
        )
    entries = []
    for name in names:
        entries.append({
            "company_name": name, "searched": True,
            "queries": [name + " MBA product manager intern Summer 2027"],
            "postings": by_company.get(name) or "none found",
        })
    return json.dumps({"companies": entries})


@pytest.fixture(autouse=True)
def _no_ambient_claude_credentials(monkeypatch):
    # Every Claude call must be mocked in this suite (see CLAUDE.md and
    # docs/plan.md's phase 3 tests). Clearing these here means a test
    # environment that happens to have real Claude credentials set (e.g. a
    # developer's own shell) can never make job/run.py's `is_configured()`
    # gate open the door to a real `claude` subprocess call during a test
    # that forgot to mock one.
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
