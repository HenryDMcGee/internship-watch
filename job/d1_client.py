"""Talks to D1 through Cloudflare's D1 HTTP API.

The daily job runs on GitHub Actions, plain CPython, with no D1 binding
available (that only exists inside the Worker), so it uses the HTTP API
instead: https://developers.cloudflare.com/api/operations/cloudflare-d1-query-database

`D1HttpDatabase` implements the same small `all`/`first`/`run` interface as
`api/src/db.py`'s `Database` protocol, so `job/` code and `api/` code read
the same way even though they talk to D1 differently.
"""
from __future__ import annotations

import os
from typing import Any

import requests

API_BASE = "https://api.cloudflare.com/client/v4"


class D1HttpError(RuntimeError):
    pass


class D1HttpDatabase:
    def __init__(self, account_id: str, database_id: str, token: str, session: "requests.Session | None" = None):
        self._account_id = account_id
        self._database_id = database_id
        self._token = token
        self._session = session or requests.Session()

    @classmethod
    def from_env(cls) -> "D1HttpDatabase":
        account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
        database_id = os.environ["D1_DATABASE_ID"]
        token = os.environ["CLOUDFLARE_D1_TOKEN"]
        return cls(account_id, database_id, token)

    def _url(self) -> str:
        return f"{API_BASE}/accounts/{self._account_id}/d1/database/{self._database_id}/query"

    def _query(self, sql: str, params: tuple[Any, ...]) -> dict:
        resp = self._session.post(
            self._url(),
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            json={"sql": sql, "params": list(params)},
            timeout=30,
        )
        try:
            body = resp.json()
        except ValueError as exc:
            raise D1HttpError(f"D1 query returned non-JSON response (status {resp.status_code})") from exc
        if not resp.ok or not body.get("success", False):
            errors = body.get("errors") or [{"message": f"HTTP {resp.status_code}"}]
            message = "; ".join(e.get("message", str(e)) for e in errors)
            raise D1HttpError(f"D1 query failed: {message}")
        results = body.get("result") or []
        return results[0] if results else {"results": [], "meta": {}}

    async def all(self, sql: str, *params: Any) -> list[dict]:
        result = self._query(sql, params)
        return list(result.get("results") or [])

    async def first(self, sql: str, *params: Any) -> dict | None:
        rows = await self.all(sql, *params)
        return rows[0] if rows else None

    async def run(self, sql: str, *params: Any) -> int | None:
        result = self._query(sql, params)
        meta = result.get("meta") or {}
        return meta.get("last_row_id")
