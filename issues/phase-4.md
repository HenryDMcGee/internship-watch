Phase 4: Automatic deploys and backups

---

Henry approved phases 2 to 4 on September 20, 2026. Build this phase directly. If you need to go beyond this scope, comment with a proposal in three sections (Functionality, Technical code changes, Architecture) and wait for approval.

Read `CLAUDE.md`, `docs/plan.md`, and `docs/deploy.md` first. Phases 1 to 3 are merged.

## Build

- **`.github/workflows/deploy.yml`:**
  - Runs after CI succeeds on `main`, using `workflow_run` on the CI workflow with a `conclusion == 'success'` check.
  - Steps: check out, set up Node.js and uv, run `npx wrangler d1 migrations apply internship-watch-db --remote`, then `uv run pywrangler deploy` from `api/`.
  - Uses `CLOUDFLARE_DEPLOY_TOKEN` as `CLOUDFLARE_API_TOKEN`, plus `CLOUDFLARE_ACCOUNT_ID`.
  - Uses a concurrency group so two deploys never overlap.
- **`.github/workflows/backup.yml`:**
  - Runs nightly at 09:00 UTC, after the 8am Central run, and by hand.
  - Exports the database with `npx wrangler d1 export internship-watch-db --remote`, then uploads it to the R2 bucket `internship-watch-backups` as `<date>.sql`.
  - Deletes backups older than 30 days.
  - Uses the deploy token, which needs **Workers R2 Storage: Edit**. Say so in the pull request description.
- **Docs:**
  - Update `docs/deploy.md`: merging now deploys automatically. The manual commands stay as a fallback.
  - Explain rollback: revert the pull request, or run `npx wrangler rollback`.
  - Explain restoring a backup from R2.
- **Tests:** validate both workflow files as YAML and check their triggers, schedule, and required secrets.

## Done when

- Merging a small change to `main` deploys it automatically.
- Starting `backup.yml` by hand puts a backup file in R2.
- CI passes.
- The pull request description lists what Henry must do before merging: add the R2 permission to the deploy token, create the bucket, and add `CLOUDFLARE_DEPLOY_TOKEN`.

@claude please build phase 4 as described above and open a pull request.
