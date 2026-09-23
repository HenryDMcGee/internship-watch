"""Validate config/titles.json against its schema and check for duplicates.

Run: python scripts/validate_titles.py
Exits with code 1 and a list of problems if anything is wrong.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TITLES = ROOT / "config" / "titles.json"
SCHEMA = ROOT / "config" / "titles.schema.json"


def find_problems(doc: dict, schema: dict) -> list[str]:
    problems: list[str] = []
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - CI installs it
        return ["jsonschema is not installed (pip install jsonschema)"]

    validator = jsonschema.Draft202012Validator(schema)
    for error in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in error.path) or "(root)"
        problems.append(f"schema: {where}: {error.message}")
    if problems:
        return problems

    seen: dict[str, str] = {}
    for entry in doc["titles"]:
        if not entry["title"].strip():
            problems.append("title must not be empty or whitespace-only")
        for name in [entry["title"], *entry["aliases"]]:
            key = " ".join(name.lower().split())
            if key in seen:
                problems.append(f"duplicate: '{name}' also appears under '{seen[key]}'")
            else:
                seen[key] = entry["title"]

    for word in doc["exclude_words"]:
        if word != word.strip() or not word.strip():
            problems.append(f"exclude word has extra spaces: '{word}'")
        for key, owner in seen.items():
            if word.lower() in key:
                problems.append(f"exclude word '{word}' would block title '{owner}'")
    return problems


def main() -> int:
    doc = json.loads(TITLES.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    problems = find_problems(doc, schema)
    if problems:
        print("titles.json has problems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    active = sum(1 for t in doc["titles"] if t["active"])
    print(f"titles.json OK: {len(doc['titles'])} titles ({active} active)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
