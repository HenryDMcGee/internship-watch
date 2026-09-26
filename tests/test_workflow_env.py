"""Regression test for issue #16: the daily job silently skipped every Claude
step because `.github/workflows/daily-search.yml`'s `env:` block never
passed `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, `CLAUDE_ROUTINE_URL`,
or `CLAUDE_ROUTINE_TOKEN` through to `job/run.py`, and nothing caught it.

This checks `docs/pending-workflows/daily-search.yml` and
`docs/pending-workflows/check-now.yml`, not the copies under
`.github/workflows/` -- the GitHub App token this Claude Code workflow runs
with can't push commits under `.github/workflows/` (see
`docs/pending-workflows/README.md`), so a PR from Claude can only ever keep
the pending copies correct. Once a copy is installed by hand, the two are
identical by construction, so this still catches the next time a new
required name is added to `job/` but forgotten here -- exactly what caused
this issue. `check-now.yml` didn't need a pending copy before phase 6 part 1
(issue #53) added `CLOUDFLARE_BROWSER_TOKEN` to it -- see that issue for why
check-now.yml gets the token even though it doesn't run the Claude agent
steps today.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Every name job/ reads from os.environ for a full daily run:
# job/d1_client.py (required), job/agent/client.py (one of these two),
# job/agent/github_pr.py, job/agent/summary.py (both optional, but the
# workflow must still pass the names through for them to ever work),
# job/fetchers/careers_pages.py (optional -- missing it just sends every
# careers page to the Playwright backup instead, see issue #53).
REQUIRED_DAILY_SEARCH_ENV_NAMES = (
    "CLOUDFLARE_D1_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "D1_DATABASE_ID",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "CLAUDE_ROUTINE_URL",
    "CLAUDE_ROUTINE_TOKEN",
    "CLOUDFLARE_BROWSER_TOKEN",
)

REQUIRED_CHECK_NOW_ENV_NAMES = (
    "CLOUDFLARE_D1_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "D1_DATABASE_ID", "CLOUDFLARE_BROWSER_TOKEN",
)


def _job_run_env_block(workflow_text: str) -> str:
    """The `env:` block text for whichever step's `run:` line invokes
    `job.run`, so a name that only appears elsewhere in the file (a comment,
    a different step) doesn't count.
    """
    match = re.search(
        r"env:\n((?:[ \t]+\S.*\n?)+?)[ \t]*run:[ \t]*python -m job\.run\b",
        workflow_text,
    )
    assert match, "couldn't find an env: block immediately followed by a `run: python -m job.run` line"
    return match.group(1)


def test_pending_daily_search_workflow_passes_every_required_env_name():
    text = (ROOT / "docs" / "pending-workflows" / "daily-search.yml").read_text(encoding="utf-8")
    env_block = _job_run_env_block(text)
    missing = [name for name in REQUIRED_DAILY_SEARCH_ENV_NAMES if name not in env_block]
    assert not missing, (
        f"docs/pending-workflows/daily-search.yml's job-run step env: block is missing {missing}. "
        "job/ reads these from os.environ -- see this test module's docstring for where."
    )


def test_pending_check_now_workflow_passes_every_required_env_name():
    text = (ROOT / "docs" / "pending-workflows" / "check-now.yml").read_text(encoding="utf-8")
    env_block = _job_run_env_block(text)
    missing = [name for name in REQUIRED_CHECK_NOW_ENV_NAMES if name not in env_block]
    assert not missing, (
        f"docs/pending-workflows/check-now.yml's job-run step env: block is missing {missing}. "
        "job/ reads these from os.environ -- see this test module's docstring for where."
    )
