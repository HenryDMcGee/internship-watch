import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from validate_titles import SCHEMA, TITLES, find_problems  # noqa: E402


def load():
    return json.loads(TITLES.read_text()), json.loads(SCHEMA.read_text())


def test_titles_file_is_valid():
    doc, schema = load()
    assert find_problems(doc, schema) == []


def test_duplicate_titles_are_caught():
    doc, schema = load()
    doc["titles"].append(dict(doc["titles"][0]))
    assert any(p.startswith("duplicate") for p in find_problems(doc, schema))


def test_alias_clash_is_caught():
    doc, schema = load()
    doc["titles"][1]["aliases"] = [doc["titles"][0]["title"].upper()]
    assert any(p.startswith("duplicate") for p in find_problems(doc, schema))


def test_exclude_word_blocking_a_title_is_caught():
    doc, schema = load()
    doc["exclude_words"].append("product")
    assert any("would block" in p for p in find_problems(doc, schema))


def test_unknown_field_is_rejected():
    doc, schema = load()
    doc["titles"][0]["priority"] = 1
    assert any(p.startswith("schema") for p in find_problems(doc, schema))


def test_whitespace_only_title_is_caught():
    doc, schema = load()
    doc["titles"][0]["title"] = "  "
    assert any("empty" in p for p in find_problems(doc, schema))
