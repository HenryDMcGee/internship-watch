"""Builds `IN (...)` clauses that can't hit D1's bound-parameter limit.

Cloudflare D1 (like SQLite) rejects a query with more than 100 bound
parameters. Building `IN (?, ?, ?, ...)` with one `?` per list item crashes
once the list passes that limit (issue #43: `_verify_and_prune` checking
every ranked/pinned company's postings). `json_each` takes the whole list as
a single JSON parameter instead, so the clause never grows past one bound
value no matter how long the list is.

No FastAPI or D1 imports here, so this is safe to import from job/ (which
adds this directory to `sys.path`, see `job/config.py`) as well as from
`api/src/main.py`.
"""
from __future__ import annotations

import json
from typing import Any


def in_json(column: str, values: list[Any]) -> tuple[str, str]:
    """Returns (sql_fragment, json_param) for a `column IN (...)` clause.

    Usage:
        clause, param = in_json("id", ids)
        rows = await db.all(f"SELECT * FROM companies WHERE {clause}", param)
    """
    return f"{column} IN (SELECT value FROM json_each(?))", json.dumps(list(values))
