"""Phase 4: validate .github/workflows/deploy.yml and backup.yml.

Checks the files parse as YAML, and that their triggers, schedule, branch,
and required secrets match what CLAUDE.md and docs/plan.md promise: deploy
watches `master` (the default branch, not `main`) via `workflow_run` on CI,
and backup runs nightly plus by hand.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _load(name: str) -> dict:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert isinstance(data, dict), f"{name} didn't parse to a mapping"
    return data


def _on(data: dict) -> dict:
    # PyYAML (YAML 1.1) reads the bare key `on:` as the boolean True, not
    # the string "on" -- this repo's other workflow files hit the same trap.
    if True in data:
        return data[True]
    return data["on"]


def test_deploy_workflow_is_valid_yaml():
    _load("deploy.yml")


def test_deploy_workflow_triggers_on_ci_success_on_master():
    on = _on(_load("deploy.yml"))
    workflow_run = on["workflow_run"]
    assert workflow_run["workflows"] == ["CI"]
    assert workflow_run["types"] == ["completed"]
    assert workflow_run["branches"] == ["master"]


def test_deploy_workflow_checks_conclusion_success():
    data = _load("deploy.yml")
    jobs = data["jobs"]
    assert len(jobs) == 1
    (job,) = jobs.values()
    assert job["if"] == "github.event.workflow_run.conclusion == 'success'"


def test_deploy_workflow_has_a_non_overlapping_concurrency_group():
    data = _load("deploy.yml")
    concurrency = data["concurrency"]
    assert concurrency["group"]
    assert concurrency["cancel-in-progress"] is False


def test_deploy_workflow_applies_migrations_then_deploys_using_deploy_token():
    data = _load("deploy.yml")
    (job,) = data["jobs"].values()
    steps = job["steps"]
    run_steps = [s for s in steps if "run" in s]

    migrate_index = next(i for i, s in enumerate(run_steps) if "d1 migrations apply internship-watch-db" in s["run"])
    deploy_index = next(i for i, s in enumerate(run_steps) if "pywrangler deploy" in s["run"])
    assert migrate_index < deploy_index, "migrations must run before the deploy"

    for step in (run_steps[migrate_index], run_steps[deploy_index]):
        env = step["env"]
        assert env["CLOUDFLARE_API_TOKEN"] == "${{ secrets.CLOUDFLARE_DEPLOY_TOKEN }}"
        assert env["CLOUDFLARE_ACCOUNT_ID"] == "${{ secrets.CLOUDFLARE_ACCOUNT_ID }}"


def test_backup_workflow_is_valid_yaml():
    _load("backup.yml")


def test_backup_workflow_runs_nightly_and_by_hand():
    on = _on(_load("backup.yml"))
    schedules = [entry["cron"] for entry in on["schedule"]]
    assert schedules == ["0 9 * * *"]
    assert "workflow_dispatch" in on


def test_backup_workflow_has_a_non_overlapping_concurrency_group():
    data = _load("backup.yml")
    concurrency = data["concurrency"]
    assert concurrency["group"]
    assert concurrency["cancel-in-progress"] is False


def test_backup_workflow_exports_uploads_and_prunes_using_deploy_token():
    data = _load("backup.yml")
    (job,) = data["jobs"].values()
    run_steps = [s for s in job["steps"] if "run" in s]
    joined = "\n".join(s["run"] for s in run_steps)

    assert "wrangler d1 export internship-watch-db --remote" in joined
    assert "internship-watch-backups" in joined
    assert "wrangler r2 object put" in joined
    assert "wrangler r2 object delete" in joined

    steps_with_secrets = [s for s in run_steps if "env" in s]
    assert steps_with_secrets, "expected at least one step to use the deploy token"
    for step in steps_with_secrets:
        env = step["env"]
        assert env["CLOUDFLARE_API_TOKEN"] == "${{ secrets.CLOUDFLARE_DEPLOY_TOKEN }}"
        assert env["CLOUDFLARE_ACCOUNT_ID"] == "${{ secrets.CLOUDFLARE_ACCOUNT_ID }}"


def test_ci_workflow_runs_on_master_so_deploy_can_watch_it():
    # deploy.yml's workflow_run only fires from a CI run that itself ran on
    # master -- if ci.yml's push trigger watches a different branch, CI
    # never runs on master and nothing ever deploys (see CLAUDE.md/issue #29).
    on = _on(_load("ci.yml"))
    assert on["push"]["branches"] == ["master"]
