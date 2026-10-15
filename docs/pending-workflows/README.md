# Pending workflow files

Files here are meant to live in `.github/workflows/`, but the GitHub App token this repository's Claude Code workflow runs with doesn't have the `workflows` permission, so it can't push commits that add or change anything under `.github/workflows/` (GitHub rejects the push). Any pull request that needs to change a workflow file leaves its updated copy here instead, for you to move into place by hand.

**`daily-search.yml`** was updated in phase 3 (the Claude agent) to add `contents: write` and `pull-requests: write` permissions, install the `claude` CLI, and pass through `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, and `CLAUDE_ROUTINE_URL`/`CLAUDE_ROUTINE_TOKEN`. Phase 6 part 1 (issue #53) added `CLOUDFLARE_BROWSER_TOKEN`, for reading careers pages through Cloudflare Browser Run.

Phase 1 of the Cloudflare daily trigger added a `run_source` input to `workflow_dispatch` (default `manual`) and passes it to the job-run step as `RUN_SOURCE`, so `job/run.py` can record which path started each run. The crons and the guard step are unchanged in that phase. The copy here is the installed file plus that change (it also carries the "Close the run if the job was stopped" step the installed file already had).

**`check-now.yml`** was unchanged from phase 2 through phase 5; phase 6 part 1 (issue #53) added `CLOUDFLARE_BROWSER_TOKEN` here too, alongside the existing secrets.

To install both, from the repository root:
```bash
cp docs/pending-workflows/daily-search.yml .github/workflows/daily-search.yml
cp docs/pending-workflows/check-now.yml .github/workflows/check-now.yml
git add .github/workflows/daily-search.yml .github/workflows/check-now.yml
git commit -m "Add CLOUDFLARE_BROWSER_TOKEN to the daily-search and check-now workflows"
git push
```
Do this from your own machine (or the GitHub web UI) with your own GitHub credentials, since only you can push workflow files here. See the pull request description for the rest of the after-merge checklist, including which secrets to add.

**`ci.yml`** was updated for issue #81 (frontend tests). It keeps the existing pytest job and adds a `jest` job (`npm ci`, `npx jest --coverage`) and a `playwright` job (`npm ci`, Chromium, `npx playwright test`, then `nyc check-coverage` at 80%). Deploys stay gated on the whole CI workflow, so all three must pass. To install it:
```bash
cp docs/pending-workflows/ci.yml .github/workflows/ci.yml
git add .github/workflows/ci.yml
git commit -m "Add the Jest and Playwright jobs to CI"
git push
```
Until it is copied, CI only runs the pytest job.
