"""Copy config/titles.json into api/src/ so the Worker can serve it.

The Python Worker only has the files under `api/` once it's bundled and
deployed; it can't reach `config/titles.json` at the repository root. The
`/api/titles` endpoint is read-only, so a copy taken at build time (dev or
deploy) is enough. Run before `pywrangler dev` or `pywrangler deploy`:

    python scripts/bundle_titles.py

The copy (api/src/titles.json) is generated and gitignored; titles still
change only through pull requests to config/titles.json.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "config" / "titles.json"
DEST = ROOT / "api" / "src" / "titles.json"


def main() -> int:
    if not SOURCE.exists():
        print(f"{SOURCE} does not exist", file=sys.stderr)
        return 1
    DEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, DEST)
    print(f"Copied {SOURCE} to {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
