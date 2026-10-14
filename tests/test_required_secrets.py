"""Issue #89: a missing FEEDBACK_SALT secret looked like a bad submission.

Pins down: the secrets check names the missing secret, the Worker logs it at startup,
the deploy script fails on it, and the visitor sees a server-side message (not the
validation wording) with the secret named only in the log.
"""
import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.main as main_module
from schema_check import REQUIRED_SCHEMA, REQUIRED_SECRETS, secret_problems
from src.main import app

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_schema as deploy_check  # noqa: E402

SERVER_MESSAGE = "Something went wrong on our side. Please try again later."


def _all_columns(table, where):
    return set(REQUIRED_SCHEMA[table])


def test_feedback_salt_is_a_required_secret():
    assert "FEEDBACK_SALT" in REQUIRED_SECRETS


def test_secret_problems_names_the_missing_secret():
    assert any("FEEDBACK_SALT" in p for p in secret_problems(set()))
    assert secret_problems({"FEEDBACK_SALT", "GITHUB_TOKEN"}) == []


def test_worker_startup_logs_the_missing_secret(caplog):
    with caplog.at_level(logging.ERROR):
        main_module.log_missing_secrets(app, {"GITHUB_TOKEN": "x"})
    assert "FEEDBACK_SALT" in caplog.text
    assert app.state.secret_problems


def test_worker_startup_is_quiet_when_secrets_are_set(caplog):
    with caplog.at_level(logging.ERROR):
        main_module.log_missing_secrets(app, {"FEEDBACK_SALT": "s"})
    assert not caplog.records
    assert app.state.secret_problems == []


def test_deploy_check_fails_naming_the_secret(monkeypatch, capsys):
    monkeypatch.setattr(deploy_check, "read_columns", _all_columns)
    monkeypatch.setattr(deploy_check, "read_secret_names", lambda where: {"GITHUB_TOKEN"})
    assert deploy_check.main(["--remote"]) == 1
    assert "FEEDBACK_SALT" in capsys.readouterr().err


def test_deploy_check_passes_when_secret_is_set(monkeypatch):
    monkeypatch.setattr(deploy_check, "read_columns", _all_columns)
    monkeypatch.setattr(deploy_check, "read_secret_names", lambda where: {"FEEDBACK_SALT"})
    assert deploy_check.main(["--remote"]) == 0


def test_local_deploy_check_skips_secrets(monkeypatch):
    monkeypatch.setattr(deploy_check, "read_columns", _all_columns)
    monkeypatch.setattr(deploy_check, "read_secret_names", lambda where: pytest.fail("local runs have no Worker secrets"))
    assert deploy_check.main(["--local"]) == 0


def test_missing_salt_is_a_server_failure_not_a_validation_error(empty_db, caplog):
    app.state.db, app.state.feedback_salt, app.state.schema_problems = empty_db, "", []
    try:
        with caplog.at_level(logging.WARNING):
            resp = TestClient(app, raise_server_exceptions=False).post(
                "/api/public/feedback", json={"kind": "dead_link", "message": "dead"}
            )
        assert resp.status_code == 503
        assert resp.json()["detail"] == SERVER_MESSAGE
        assert "FEEDBACK_SALT" not in resp.text
        assert "FEEDBACK_SALT" in caplog.text and "503" in caplog.text
    finally:
        app.state.db = None
