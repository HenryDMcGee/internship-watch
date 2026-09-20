"""Database access, with two implementations behind the same small interface.

`D1Database` talks to the real Cloudflare D1 binding and only runs inside
the Worker (Pyodide) runtime. `SqliteDatabase` is the in-memory stand-in
used by pytest (see the issue: "an in-memory SQLite stand-in is fine") and
by `scripts/seed.py` for `--local` runs where the `wrangler` CLI isn't
driving the insert. Both expose the same async methods so `main.py`
doesn't need to know which one it has.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Protocol


class Database(Protocol):
    async def all(self, sql: str, *params: Any) -> list[dict]: ...

    async def first(self, sql: str, *params: Any) -> dict | None: ...

    async def run(self, sql: str, *params: Any) -> int | None:
        """Execute a write statement. Returns the new row's id for INSERT."""
        ...


class D1Database:
    """Wraps a Cloudflare D1 binding (`env.DB`) from inside a Python Worker."""

    def __init__(self, binding):
        self._binding = binding

    def _stmt(self, sql: str, params: tuple[Any, ...]):
        stmt = self._binding.prepare(sql)
        return stmt.bind(*params) if params else stmt

    @staticmethod
    def _to_py(value):
        to_py = getattr(value, "to_py", None)
        return to_py() if callable(to_py) else value

    async def all(self, sql: str, *params: Any) -> list[dict]:
        result = await self._stmt(sql, params).all()
        rows = self._to_py(result.results if hasattr(result, "results") else result)
        return [dict(row) for row in rows] if rows else []

    async def first(self, sql: str, *params: Any) -> dict | None:
        row = await self._stmt(sql, params).first()
        row = self._to_py(row)
        return dict(row) if row else None

    async def run(self, sql: str, *params: Any) -> int | None:
        result = await self._stmt(sql, params).run()
        meta = self._to_py(getattr(result, "meta", None))
        if isinstance(meta, dict):
            return meta.get("last_row_id")
        return getattr(meta, "last_row_id", None)


class SqliteDatabase:
    """In-memory (or file) SQLite stand-in for D1, used by tests and local seeding."""

    def __init__(self, connection: sqlite3.Connection):
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        self._conn = connection

    @classmethod
    def in_memory(cls) -> "SqliteDatabase":
        return cls(sqlite3.connect(":memory:"))

    async def all(self, sql: str, *params: Any) -> list[dict]:
        cur = self._conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    async def first(self, sql: str, *params: Any) -> dict | None:
        cur = self._conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

    async def run(self, sql: str, *params: Any) -> int | None:
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return cur.lastrowid

    def executescript(self, script: str) -> None:
        self._conn.executescript(script)
        self._conn.commit()
