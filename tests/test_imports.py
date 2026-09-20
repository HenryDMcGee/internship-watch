"""Cloudflare's Python Workers runtime loads api/src/main.py as a standalone
module with no parent package, so relative imports (`from . import x`) raise
`ImportError: attempted relative import with no known parent package` at
deploy time. This guards against one coming back.
"""
from __future__ import annotations

import ast
from pathlib import Path

API_SRC = Path(__file__).resolve().parent.parent / "api" / "src"


def test_no_relative_imports_in_api_src():
    offenders = []
    for path in sorted(API_SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level > 0:
                offenders.append(f"{path.relative_to(API_SRC)}:{node.lineno}")

    assert offenders == [], f"relative imports found in api/src/: {offenders}"
