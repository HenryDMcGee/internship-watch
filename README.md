# Internship Watch

Finds Summer 2027 MBA product internships every morning and shows them on a private dashboard. Claude Code builds it through GitHub Actions, and every change goes through a pull request you review.

- The approved plan: `docs/plan.md`
- The rules Claude Code follows here: `CLAUDE.md`
- Your job titles: `config/titles.json`
- Deploying to Cloudflare: `docs/deploy.md`

## Start the build

### 1. Set up the repository

1. Create a **private** repository named `internship-watch` on GitHub. Adding a README is optional; the one in this starter pack replaces it either way.
2. Run `claude setup-token` on your computer and sign in. Copy the token it shows.
3. In the repository, go to **Settings**, then **Secrets and variables**, then **Actions**. Add a secret named `CLAUDE_CODE_OAUTH_TOKEN` and paste the token. Never paste it anywhere else.
4. Install the Claude GitHub app on this repository only. In Claude Code, `/install-github-app` walks you through it.
   - This starter pack already includes the Claude workflow, `.github/workflows/claude.yml`. If the installer adds its own workflow file, delete one of the two before pushing. Two Claude workflows would make Claude run twice on every `@claude`.

### 2. Add these files

Push this starter pack to `main` **before** you protect the branch. Branch protection would block this first direct push.

If the offline snapshot you downloaded earlier is in the same folder, rename it first so the clone doesn't collide with it. That snapshot also unzips to a folder named `internship-watch`.

```bash
mv internship-watch internship-watch-snapshot   # only if the old snapshot folder is here
git clone https://github.com/<your-username>/internship-watch.git
cd internship-watch
# copy everything from this starter pack into the folder, including .github and .gitignore
git add -A
git commit -m "Add starter pack"
git branch -M main   # makes sure your branch is called main
git push -u origin main
```

On a Mac, the `.github` folder and `.gitignore` are hidden in Finder. Press Cmd+Shift+. to show them, or copy the files with the terminal.

### 3. Protect main

In **Settings**, add a branch rule for `main` that requires a pull request before merging. From now on, nothing reaches `main` without your review.

### 4. Build the four phases

Each phase has its issue text in `issues/`. Do them in order, and start the next one only after you merge the previous phase's pull request.

For each phase:
1. Open a new issue. Use the first line of the phase file as the title, and paste everything below the `---` line as the body.
2. The body ends with `@claude`, which starts Claude Code. Follow along in the **Actions** tab.
3. Review the pull request Claude opens. Comment `@claude` on it to ask for changes, and merge when you're happy.
4. Do the "after merging" steps listed in the pull request description, then deploy with `docs/deploy.md`.

| Phase | File | Before you open it |
| --- | --- | --- |
| 1. Local version | `issues/phase-1.md` | Starter pack pushed, `main` protected |
| 2. Daily job | `issues/phase-2.md` | Database ID, database token, and account ID saved in GitHub |
| 3. Claude agent | `issues/phase-3.md` | Routine URL and token saved in GitHub |
| 4. Automatic deploys | `issues/phase-4.md` | Deploy token saved in GitHub, with R2 permission |

Only your own comments can start Claude. Mentions from anyone else are ignored.

## Usage

Building a phase is a long Claude Code session on your Claude subscription. It can use a large share of one five-hour usage window, so start a phase when you don't need Claude for anything else for a while.

## Seed data

`seed/` holds a snapshot of the dashboard you use today, taken September 19, 2026: 9 postings, 30 sources, 2 search log entries, 100 companies, and your settings (US and Japan). Phase 1 loads it into the local database.

## Local development

Requires **Node.js** (current LTS, for `wrangler`) and **uv** (for Python Workers; see `docs/deploy.md`'s "Before you start" for the install command). Sign in to Cloudflare once with `npx wrangler login` from `api/`.

From the repository root:

```bash
cd api
npx wrangler d1 migrations apply internship-watch-db --local
cd ..
python scripts/seed.py
cd api
uv run pywrangler dev
```

This creates the local D1 tables, loads the seed data (9 postings with 3 closed and hidden, 30 sources, 2 search log entries, the ranked companies, and the locations panel), and starts the dashboard and API. Open the address `pywrangler` prints.

Cloudflare Access isn't in front of a local server, so there's no login token to check there. The API skips the check when `ACCESS_TEAM_DOMAIN` or `ACCESS_AUD` is empty; leave `api/.dev.vars` (gitignored) unset, or set both to empty strings there, for local development. `api/wrangler.toml`'s committed `[vars]` values are for the real deployment and always take effect once deployed.

The Titles panel, Check now, and title edits all call GitHub's API through the `GITHUB_TOKEN` Worker secret (see `docs/deploy.md`'s step 3). Locally, put the same token in `api/.dev.vars` as `GITHUB_TOKEN=...` to exercise those; without it, they return a clear error instead of working.

To re-seed from scratch, delete `api/.wrangler/` (the local D1 state) and repeat the steps above.

### Running tests

```bash
pip install -r requirements-dev.txt
pytest
python scripts/validate_titles.py
```

## Running the daily job by hand

The daily job (`job/run.py`) normally runs on GitHub Actions at 08:00 America/Chicago (`.github/workflows/daily-search.yml`), or for specific companies when you click Check now on the dashboard (`.github/workflows/check-now.yml`). To run it yourself:

**From GitHub Actions (writes to the real database):** open the **Actions** tab, choose **Daily search**, and click **Run workflow**. Manual runs skip the 08:00 guard and run immediately. This needs `CLOUDFLARE_D1_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and `D1_DATABASE_ID` saved as repository secrets first (**Settings**, **Secrets and variables**, **Actions**).

**From your own computer (also writes to the real database, since the job always talks to D1's HTTP API, not a local database):**
```bash
pip install -r job/requirements.txt
python -m playwright install --with-deps chromium
export CLOUDFLARE_D1_TOKEN=...
export CLOUDFLARE_ACCOUNT_ID=...
export D1_DATABASE_ID=...
python -m job.run
```
Add `--request-id <id>` to run only the companies from one `check_requests` row, the same way `check-now.yml` does.
