# Deploying Internship Watch to Cloudflare

The dashboard and API run as one Cloudflare Worker named `internship-watch`, configured in `api/wrangler.toml`. It serves `https://internship-watch.mcgeehenry.com`, behind the Cloudflare Access email login, and the public job board at `https://jobs.mcgeehenry.com` (step 4b), which has no login.

You can deploy by hand once phase 1 is merged. From phase 4, merging a pull request deploys automatically.

## Before you start

- **Phase 1 is merged.** You need the API in `api/`, the dashboard in `frontend/`, and the migrations in `db/migrations/`.
- **The Access application exists** for exactly `internship-watch.mcgeehenry.com` (setup Step 3).
- **Node.js** (current LTS) is installed, for `wrangler`.
- **uv** is installed, for Python Workers. In PowerShell:
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
  Open a new terminal afterward.
- **Sign in to Cloudflare from the terminal**, once:
  ```powershell
  npx wrangler login
  ```

## One-time setup

### 1. Database ID

If you haven't created the database yet, run:
```powershell
npx wrangler d1 create internship-watch-db
```
Put the database ID in `api/wrangler.toml` as `database_id`. You can also find the ID in Cloudflare under **D1**. It isn't a secret.

### 2. Access details for login checks

The API checks the Access login token on every request, so it needs two values from Cloudflare. Put both in the `[vars]` section of `api/wrangler.toml`. Neither is a secret.
- **ACCESS_AUD:** in **Zero Trust**, open **Access controls**, **Applications**, then **Internship Watch**. Copy the **Application Audience (AUD) Tag**.
- **ACCESS_TEAM_DOMAIN:** your team domain, in the form `<team name>.cloudflareaccess.com`. It's shown in the Zero Trust settings.

### 3. GitHub token for the Titles panel, Check now, and title edits

The Worker calls GitHub's API for three things: reading `config/titles.json` for the Titles panel, starting `check-now.yml` when you click Check now, and opening or updating the pull request that title edits make. It needs a token with **Actions: write**, **Contents: write**, and **Pull requests: write** on this repository only (a fine-grained personal access token, or a token from a GitHub App installed just here).

Store it as a Worker secret, not in `wrangler.toml` (secrets never go in git):
```powershell
npx wrangler secret put GITHUB_TOKEN
```
Paste the token when prompted. If you skip this step, the dashboard still works, but the Titles panel, Check now, and title edits return an error until you add it.

### 3b. GitHub Actions secrets for the daily job's Claude steps

The daily job's Claude agent steps (`job/agent/`) run in GitHub Actions, not in the Worker, so these are repository secrets (**Settings**, **Secrets and variables**, **Actions**, **New repository secret**), not `wrangler secret put`:

- **`CLAUDE_CODE_OAUTH_TOKEN`**: from `claude setup-token`, using your Claude subscription. This is the default; set only this one to start.
- **`ANTHROPIC_API_KEY`**: set this instead (leave `CLAUDE_CODE_OAUTH_TOKEN` unset) to bill Claude calls to an API key instead of your subscription -- switching is just which of the two secrets is set, no code change. Also set the Budgets panel's billing mode to match.
- **`CLAUDE_ROUTINE_URL`** and **`CLAUDE_ROUTINE_TOKEN`**: the Claude routine (research preview) that receives the daily summary and alerts after every run. Leave both unset to skip sending it; the job still runs and records usage either way.

`GITHUB_TOKEN` for the daily improvement step's title-suggestion pull requests doesn't need setting up -- it's the default Actions token, already scoped by `contents: write` and `pull-requests: write` in `daily-search.yml`.

Until at least one of `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` is set, the daily job still runs (feeds, matching, verification, pruning) but skips every Claude step -- and says so loudly rather than silently: the run's summary on the dashboard gets a "Claude steps skipped" note, and a `claude_not_configured` alert is recorded once a day for as long as neither secret is set.

### 3c. GitHub Actions secret for reading careers pages

`job/fetchers/careers_pages.py` (phase 6 part 1) reads a company's careers page through Cloudflare's Browser Rendering API when it has no recognized job feed. It needs one more repository secret, alongside the ones in 3b:

- **`CLOUDFLARE_BROWSER_TOKEN`**: an API token scoped to only the **Browser Rendering** permission (Cloudflare dashboard, **My Profile**, **API Tokens**, **Create Token**) on the same account as `CLOUDFLARE_ACCOUNT_ID`.

If it's left unset, the daily job still runs -- every careers page just goes to the Playwright backup instead of Browser Run for the whole run, and the run's summary says so.

### 4. DNS record for the subdomain

The Worker's route needs a proxied DNS record to answer on the subdomain. The record doesn't point at a real server.

In Cloudflare, open **mcgeehenry.com**, then **DNS**, then **Records**, and add:

| Type | Name | IPv6 address | Proxy status |
| --- | --- | --- | --- |
| AAAA | `internship-watch` | `100::` | **Proxied** (orange cloud) |

If a record named `internship-watch` already exists, remove it first. Leave every other record alone; they belong to your website.

### 4b. The public job board (phase 7)

`https://jobs.mcgeehenry.com` is a second route into the same Worker (`api/wrangler.toml` has both routes, and `PUBLIC_HOSTNAME`). It shows open postings to anyone, read only, with a feedback form. Do these in order after the pull request is merged and deployed:

1. **DNS record.** In Cloudflare, open **mcgeehenry.com**, **DNS**, **Records**, and add a proxied record, the same kind the dashboard uses:

   | Type | Name | IPv6 address | Proxy status |
   | --- | --- | --- | --- |
   | AAAA | `jobs` | `100::` | **Proxied** (orange cloud) |

2. **GitHub token.** Add **Issues: write** to the Worker's existing `GITHUB_TOKEN` (Open as GitHub issue in the Feedback view needs it), on this repository only.
3. **Feedback salt.** Run this from the `api` folder and paste a long random string when asked (at least 32 characters). Don't reuse another secret, and don't change it later unless you want every visitor's daily count to reset:
   ```powershell
   npx wrangler secret put FEEDBACK_SALT
   ```
   Without it, the public feedback form answers with an error and stores nothing.
4. **Switch it on.** In the dashboard, open **Locations**, turn on **Public site**, and Save. It starts off, so until then the public page only says "Not available right now."

**Cloudflare Access stays scoped to `internship-watch.mcgeehenry.com` only. Do not extend it to `jobs.mcgeehenry.com`**, or visitors would be asked to sign in. The public name is safe without it: the Worker only lets `/`, `/style.css`, `/public/*`, and `/api/public/*` through on that name and returns 404 for everything else, and every private endpoint still checks the Access token as well.

The public list is cached for 5 minutes, so switching the site on or off, or hiding a posting, can take up to 5 minutes to show.

## Deploy

Run these from the `api` folder.

### 5. Create the tables in the real database
```powershell
npx wrangler d1 migrations apply internship-watch-db --remote
```

### 5b. Check the tables, columns, and secrets
```powershell
cd ..
python scripts/check_schema.py
cd api
```
It exits with an error and names each missing table or column if a migration wasn't applied, and names each required Worker secret (`FEEDBACK_SALT`) that isn't set. The Worker runs the same check on its first request after each deploy: if it fails, every `/api/*` request returns 503 with the missing table or column named, and the cause is logged (`npx wrangler tail`). Fix it by running step 5. A missing secret is logged by name on each request (`Missing required secret FEEDBACK_SALT is not set`), and visitors see only the server-side message. Fix it with step 4b.

### 6. Load your current data (first deploy only)
```powershell
cd ..
python scripts/seed.py --remote
cd api
```

### 7. Deploy the Worker
```powershell
uv run pywrangler deploy
```

## Check it

1. Open `https://internship-watch.mcgeehenry.com` in a private browser window. It should ask you to sign in, then show the dashboard with your postings.
2. Open `https://mcgeehenry.com` and confirm your website looks the same as before.
3. After step 4b, open `https://jobs.mcgeehenry.com` in a private window. It should load without a login, and `https://jobs.mcgeehenry.com/api/postings` should return "Not found".

## Deploying later changes

From phase 4, merging a pull request to `master` deploys automatically: once CI passes (all three jobs: pytest, the Jest frontend tests, and the Playwright browser tests, each with its own 80% frontend coverage gate), `.github/workflows/deploy.yml` runs step 5 (apply migrations) and step 7 (deploy the Worker) for you, using the `CLOUDFLARE_DEPLOY_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` secrets. Watch the **Actions** tab for the **Deploy** run.

The manual commands above still work as a fallback (for example, if Actions is down, or you're deploying from a branch that isn't `master`):
1. If the change added files in `db/migrations/`, run step 5.
2. Run step 7.

### The night-city background (phase 8)

No new setup or deploy step. The background is static files in `frontend/public/`, so the Worker serves them with the rest of `frontend/`, and the public hostname can serve them because they sit under `/public/`. Nothing in them is read by path from the API.

Two committed files are generated. Regenerate them and commit the result when you change the scene or the three.js version:
- `node scripts/vendor-three.js` rebuilds `frontend/public/vendor/three.min.mjs` (three.js, cut down to the classes the scene uses).
- `node scripts/render-city-still.js` rebuilds the four static images, `frontend/public/city-still-{dark,light}.{webp,jpg}` (needs `npx playwright install chromium` once).

### Rolling back

Two options, depending on what's wrong:
- **Revert the pull request** on GitHub, then merge the revert. The automatic deploy ships the reverted code the same way as any other merge.
- **Roll back the Worker only**, without touching `master`:
  ```powershell
  npx wrangler rollback
  ```
  This is faster, but the next automatic deploy (or manual `pywrangler deploy`) overwrites it with whatever `master` still has, so also revert the pull request if the bad change shouldn't ship again.

Neither option undoes database migrations. Any pull request that changes the database says how to reverse it.

### Migration `0014_public_site.sql`

Migration `0014` adds `postings.public_hidden` (default 0) and the `feedback` table. The automatic deploy applies it (step 5). To undo it, drop the `feedback` table and the column. The on/off switch is the `public_site_enabled` row in `settings` (missing means off).

### One-time fingerprint backfill

After migration `0007_posting_fingerprints.sql` has been applied to the real database (step 5), run the backfill once so existing postings, including archived ones, are fingerprinted and any duplicate already in the data is found and archived:
```powershell
python -m scripts.backfill_fingerprints
```
It uses the same `CLOUDFLARE_D1_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and `D1_DATABASE_ID` as the daily job. Add `--dry-run` first to see what it would do without writing anything. It's safe to run more than once.

### Migration `0011_search_coverage.sql` and the careers-page backfill

Migration `0011` adds three columns to `companies` (`last_discovery_searched_at`, `careers_url_approved`, `careers_note`) and marks every careers link already on file as approved, so the scout never overwrites it. The automatic deploy applies it (step 5); nothing else needs running by hand. To undo it, drop the three columns.

The first daily run after the deploy also does a one-time backfill, with no command to run: every company with no feed and no careers link gets the best careers page already in the `sources` table. The run's log line says how many (`careers backfill: attached a careers page from the sources table to N companies`), and the result is saved in the `settings` table under `careers_backfill`, which is what stops it running again. To run it again, delete that row.

### Migration `0012_match_method.sql` and the unmatched-postings cleanup

Migration `0012` adds `postings.match_method` (`exact`, `alias`, `fuzzy:<score>`, or `classification`; empty on older postings). The automatic deploy applies it (step 5). To undo it, drop the column.

The first daily run after the deploy also archives postings first seen on or after 2026-10-01 that have no matched title, with the reason "no matching title (cleanup)". Postings first seen earlier (including the seeded ones) and anything marked applied, open, or closed are never touched. The run summary says how many were archived, with examples. To see the list first, or to run it by hand: `python -m job.cleanup_unmatched --dry-run`, then without `--dry-run`. It uses the same D1 secrets as the daily job and is safe to run more than once. Archived postings can be restored from the dashboard.

### Migration `0013_careers_time_budget.sql` and run time

Migration `0013` adds `budget_settings.careers_minutes_per_run` (default 10) and `careers_page_snapshots.empty_runs`. The automatic deploy applies it (step 5). To undo it, drop the two columns.

The daily run order is now: company feeds, discovery, classification of everything found so far, careers pages, then the scout and improvement. The careers step stops at `careers_minutes_per_run` minutes (60% for loading pages, the rest for extraction), and the pages it didn't reach come first next run. The run summary and Analytics show total run time, and the daily summary warns when a run takes more than 30 minutes.

A run stopped by the job time limit can't write its own end. `python -m job.close_stuck_runs` closes any run still marked running (status failed, finish time set, daily summary sent). It needs a final `if: always()` step in `daily-search.yml`; Claude can't edit workflow files, so the exact YAML is in the pull request for issue #70 and has to be added by hand.

The careers-page backup needs Chromium on the runner. `daily-search.yml` already installs it, and the job installs it itself once if it is missing, so no workflow change is needed.

## Backups

`.github/workflows/backup.yml` runs nightly at 09:00 UTC (after the 08:00 America/Chicago daily job) and exports the database to the `internship-watch-backups` R2 bucket as `<date>.sql`, deleting backups older than 30 days. Run it by hand from the **Actions** tab (**Backup**, **Run workflow**) any time you want a backup right away, such as before a risky migration.

### Restoring a backup

1. In Cloudflare, open **R2**, then **internship-watch-backups**, and download the `.sql` file for the date you want.
2. Apply it to the database:
   ```powershell
   npx wrangler d1 execute internship-watch-db --remote --file=<date>.sql
   ```
   This replays the exported SQL; it doesn't delete rows added since the backup, so review the file first if you only want to restore part of it.
3. Recheck the dashboard (see **Check it**, above).

## If something goes wrong

| What you see | Likely cause | Fix |
| --- | --- | --- |
| "Site can't be reached" | The DNS record from step 4 is missing | Add it, set to Proxied |
| The dashboard loads without asking you to sign in | The DNS record is set to DNS only, or the Access application's hostname doesn't match | Set the record to Proxied, and check the hostname is exactly `internship-watch.mcgeehenry.com` |
| Sign-in works but the dashboard shows errors or no data | `ACCESS_AUD` or `ACCESS_TEAM_DOMAIN` is wrong, or migrations weren't applied | Recheck step 2, then run step 5 |
| Titles panel, Check now, or title edits return an error | `GITHUB_TOKEN` isn't set, or lacks Actions/Contents/Pull requests write | Recheck step 3 |
| Deploy fails because of a Python package | The package doesn't run in Python Workers | Tell Claude; the plan's fallback is a small TypeScript Worker, proposed first |
| `jobs.mcgeehenry.com` can't be reached | The `jobs` DNS record from step 4b is missing | Add it, set to Proxied |
| `jobs.mcgeehenry.com` asks for a login | The Access application covers the public name | Scope it to `internship-watch.mcgeehenry.com` only |
| The public page says "Not available right now." | The Public site switch is off (the default), or the 5-minute cache hasn't expired | Turn it on in Locations |
| Public feedback says "Something went wrong on our side" (503) | `FEEDBACK_SALT` isn't set. `wrangler tail` shows "FEEDBACK_SALT is not set" | Recheck step 4b |
| Public feedback says "Something went wrong on our side" (500), or every API call returns 503 naming a table or column | A migration wasn't applied | Run step 5, then 5b. `wrangler tail` shows the cause |
| Open as GitHub issue fails | The GitHub token lacks Issues: write | Recheck step 3 and 4b |
| Your main website shows a Cloudflare login | The Access application was created for `mcgeehenry.com` instead of the subdomain | Fix the application's hostname in Zero Trust |

### Migration `0015_feedback_issues.sql` and feedback issues

Migration `0015` adds `budget_settings.feedback_issues_per_day` (default 20) and `feedback.issue_created_at`. The automatic deploy applies it (step 5). To undo it, drop the two columns.

Public feedback now creates a GitHub issue labeled `visitor-feedback` (through the Worker's `GITHUB_TOKEN`, which already needs Issues: write). The daily run retries rows with no issue using the job's `GITHUB_TOKEN`, so the `permissions:` block in `.github/workflows/daily-search.yml` needs `issues: write`. Without it, retries fail quietly and the rows wait.
