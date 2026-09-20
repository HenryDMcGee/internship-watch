# Pending workflow files

Files here are meant to live in `.github/workflows/`, but the GitHub App token this repository's Claude Code workflow runs with doesn't have the `workflows` permission, so it can't push commits that add or change anything under `.github/workflows/` (GitHub rejects the push). Any pull request that needs to change a workflow file leaves its updated copy here instead, for you to move into place by hand.

**`check-now.yml`** hasn't changed since phase 2 and is already installed at `.github/workflows/check-now.yml`; the copy here is stale and can be deleted.

**`daily-search.yml`** was updated in phase 3 (the Claude agent) to add `contents: write` and `pull-requests: write` permissions, install the `claude` CLI, and pass through `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, and `CLAUDE_ROUTINE_URL`/`CLAUDE_ROUTINE_TOKEN`. To install it, from the repository root:
```bash
cp docs/pending-workflows/daily-search.yml .github/workflows/daily-search.yml
rm docs/pending-workflows/check-now.yml docs/pending-workflows/daily-search.yml
git add .github/workflows/daily-search.yml docs/pending-workflows
git commit -m "Update the daily-search workflow for the Claude agent"
git push
```
Do this from your own machine (or the GitHub web UI) with your own GitHub credentials, since only you can push workflow files here. See the pull request description for the rest of the after-merge checklist, including which secrets to add.
