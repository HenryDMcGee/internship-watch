# Pending workflow files

`daily-search.yml` and `check-now.yml` here are meant to live in `.github/workflows/`. They aren't there yet because the GitHub App token this repository's Claude Code workflow runs with doesn't have the `workflows` permission, so it can't push commits that add or change files under `.github/workflows/` (GitHub rejects the push).

To finish installing them, from the repository root:
```bash
mkdir -p .github/workflows
mv docs/pending-workflows/daily-search.yml docs/pending-workflows/check-now.yml .github/workflows/
rmdir docs/pending-workflows
git add .github/workflows/daily-search.yml .github/workflows/check-now.yml docs/pending-workflows
git commit -m "Move daily job workflows into .github/workflows"
git push
```
Do this from your own machine (or the GitHub web UI) with your own GitHub credentials, since only you can push workflow files here. See the pull request description for the rest of the after-merge checklist.
