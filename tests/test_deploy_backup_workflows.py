"""Phase 4: validate the deploy and backup workflows.

The GitHub App token this repository's Claude Code workflow runs with
doesn't have the `workflows` permission, so it can't push commits that add
or change anything under `.github/workflows/` (GitHub rejects the push --
see the pull request description for the files Henry needs to add by hand,
and docs/deploy.md for what they do). Since the files can't be committed,
this module holds the canonical text as the single source of truth and
validates it directly, the way CLAUDE.md asks for tests on new logic even
when the logic can't land in its final location yet.
"""
from __future__ import annotations

import yaml

DEPLOY_YAML = """\
# Deploys automatically after CI succeeds on master: applies D1 migrations,
# then deploys the Worker. Watches master, the default branch, not main --
# see docs/deploy.md for the manual fallback and rollback steps.
name: Deploy

on:
  workflow_run:
    workflows: ["CI"]
    types: [completed]
    branches: [master]

concurrency:
  group: internship-watch-deploy
  cancel-in-progress: false

jobs:
  deploy:
    if: github.event.workflow_run.conclusion == 'success'
    runs-on: ubuntu-latest
    timeout-minutes: 15
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.workflow_run.head_sha }}

      - uses: actions/setup-node@v4
        with:
          node-version: "20"

      - uses: astral-sh/setup-uv@v3

      - name: Apply D1 migrations
        working-directory: api
        run: npx wrangler d1 migrations apply internship-watch-db --remote
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_DEPLOY_TOKEN }}
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}

      - name: Deploy the Worker
        working-directory: api
        run: uv run pywrangler deploy
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_DEPLOY_TOKEN }}
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
"""

BACKUP_YAML = """\
# Nightly D1 export, uploaded to the internship-watch-backups R2 bucket as
# <date>.sql, with backups older than 30 days deleted. Runs after the 08:00
# America/Chicago daily job. Can also be started by hand to check the R2
# upload works.
name: Backup

on:
  schedule:
    - cron: "0 9 * * *"
  workflow_dispatch:

concurrency:
  group: internship-watch-backup
  cancel-in-progress: false

jobs:
  backup:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Set backup date
        run: echo "BACKUP_DATE=$(date -u +%F)" >> "$GITHUB_ENV"

      - name: Export the database
        working-directory: api
        run: npx wrangler d1 export internship-watch-db --remote --output="${{ env.BACKUP_DATE }}.sql"
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_DEPLOY_TOKEN }}
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}

      - name: Upload the backup to R2
        working-directory: api
        run: npx wrangler r2 object put "internship-watch-backups/${{ env.BACKUP_DATE }}.sql" --file="${{ env.BACKUP_DATE }}.sql"
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_DEPLOY_TOKEN }}
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}

      # Filenames are deterministic (<date>.sql), so no listing call is
      # needed -- just try to delete each date past the 30-day cutoff. The
      # extra week of range past 31 days catches up if a run was missed.
      - name: Delete backups older than 30 days
        run: |
          for days_ago in $(seq 31 37); do
            old_date=$(date -u -d "-${days_ago} days" +%F)
            npx wrangler r2 object delete "internship-watch-backups/${old_date}.sql" || true
          done
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_DEPLOY_TOKEN }}
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
"""


def _parse(text: str) -> dict:
    data = yaml.safe_load(text)
    assert isinstance(data, dict)
    return data


def _on(data: dict) -> dict:
    # PyYAML (YAML 1.1) reads the bare key `on:` as the boolean True, not
    # the string "on" -- this repo's other workflow files hit the same trap.
    return data[True] if True in data else data["on"]


def test_deploy_workflow_is_valid_yaml():
    _parse(DEPLOY_YAML)


def test_deploy_workflow_triggers_on_ci_success_on_master():
    on = _on(_parse(DEPLOY_YAML))
    workflow_run = on["workflow_run"]
    assert workflow_run["workflows"] == ["CI"]
    assert workflow_run["types"] == ["completed"]
    assert workflow_run["branches"] == ["master"]


def test_deploy_workflow_checks_conclusion_success():
    data = _parse(DEPLOY_YAML)
    (job,) = data["jobs"].values()
    assert job["if"] == "github.event.workflow_run.conclusion == 'success'"


def test_deploy_workflow_has_a_non_overlapping_concurrency_group():
    concurrency = _parse(DEPLOY_YAML)["concurrency"]
    assert concurrency["group"]
    assert concurrency["cancel-in-progress"] is False


def test_deploy_workflow_applies_migrations_then_deploys_using_deploy_token():
    data = _parse(DEPLOY_YAML)
    (job,) = data["jobs"].values()
    run_steps = [s for s in job["steps"] if "run" in s]

    migrate_index = next(i for i, s in enumerate(run_steps) if "d1 migrations apply internship-watch-db" in s["run"])
    deploy_index = next(i for i, s in enumerate(run_steps) if "pywrangler deploy" in s["run"])
    assert migrate_index < deploy_index, "migrations must run before the deploy"

    for step in (run_steps[migrate_index], run_steps[deploy_index]):
        env = step["env"]
        assert env["CLOUDFLARE_API_TOKEN"] == "${{ secrets.CLOUDFLARE_DEPLOY_TOKEN }}"
        assert env["CLOUDFLARE_ACCOUNT_ID"] == "${{ secrets.CLOUDFLARE_ACCOUNT_ID }}"


def test_backup_workflow_is_valid_yaml():
    _parse(BACKUP_YAML)


def test_backup_workflow_runs_nightly_and_by_hand():
    on = _on(_parse(BACKUP_YAML))
    schedules = [entry["cron"] for entry in on["schedule"]]
    assert schedules == ["0 9 * * *"]
    assert "workflow_dispatch" in on


def test_backup_workflow_has_a_non_overlapping_concurrency_group():
    concurrency = _parse(BACKUP_YAML)["concurrency"]
    assert concurrency["group"]
    assert concurrency["cancel-in-progress"] is False


def test_backup_workflow_exports_uploads_and_prunes_using_deploy_token():
    data = _parse(BACKUP_YAML)
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
