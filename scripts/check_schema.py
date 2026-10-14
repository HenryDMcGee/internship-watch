"""Deploy check: the remote D1 database has every table and column the API writes to.

Run from the repo root after `wrangler d1 migrations apply`, before or after deploying:

    python scripts/check_schema.py            # the remote database
    python scripts/check_schema.py --local    # the local one

Exits 1 and names each missing table or column, so an unapplied migration fails the
deploy instead of surfacing later as a silent error. Needs `npx wrangler` and the
Cloudflare credentials the deploy already uses.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api" / "src"))

from schema_check import REQUIRED_SCHEMA, REQUIRED_SECRETS, problems_for, secret_problems  # noqa: E402

DATABASE = "internship-watch-db"


def read_columns(table: str, where: str) -> set[str]:
    cmd = ["npx", "--yes", "wrangler", "d1", "execute", DATABASE, where, "--json", "--command", f"PRAGMA table_info({table})"]
    out = subprocess.run(cmd, cwd=ROOT / "api", capture_output=True, text=True, check=True).stdout
    return {row["name"] for row in json.loads(out)[0]["results"]}


def read_secret_names(where: str) -> set[str]:
    """Names (never values) of the secrets set on the deployed Worker."""
    cmd = ["npx", "--yes", "wrangler", "secret", "list", "--format", "json"]
    out = subprocess.run(cmd, cwd=ROOT / "api", capture_output=True, text=True, check=True).stdout
    return {item["name"] for item in json.loads(out)}


def main(argv: list[str]) -> int:
    where = "--local" if "--local" in argv else "--remote"
    try:
        existing = {table: read_columns(table, where) for table in REQUIRED_SCHEMA}
        # Secrets live on the deployed Worker, so a local run has nothing to check.
        secrets = read_secret_names(where) if where == "--remote" else set(REQUIRED_SECRETS)
    except (subprocess.CalledProcessError, OSError, ValueError, KeyError, IndexError) as exc:
        print(f"Schema check could not read the database: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    problems = problems_for(existing) + secret_problems(secrets)
    for problem in problems:
        print(f"Schema check failed: {problem}", file=sys.stderr)
    if problems:
        print("Apply pending migrations: npx wrangler d1 migrations apply internship-watch-db --remote", file=sys.stderr)
        print("Set a missing secret: npx wrangler secret put <NAME> (docs/deploy.md step 4b)", file=sys.stderr)
        return 1
    print(f"Schema check passed: {len(REQUIRED_SCHEMA)} tables have every column the API writes, and the required secrets are set.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
