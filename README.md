# Internship Watch

Finds Summer 2027 MBA product internships every morning and shows them on a private dashboard, with a public job board for other students. Claude Code builds it through GitHub Actions, and every change goes through a pull request you review.

- The approved plan: `docs/plan.md`
- The rules Claude Code follows here: `CLAUDE.md`
- Your job titles: `config/titles.json`
- Deploying to Cloudflare: `docs/deploy.md`

The default branch is `master`, not `main`. Every workflow trigger, branch rule and issue written for Claude Code assumes that.

## What it does

Every morning at 08:00 Central, a job on GitHub Actions checks every company on your list for new postings, confirms the ones you already have are still open, and writes what it finds to a Cloudflare D1 database. You read the results on a private dashboard behind a login. Anything it judges to be a Summer 2027 MBA product internship in the US or Japan lands under **New today**.

It finds postings four ways, cheapest first:

1. **Company job feeds.** Greenhouse, Lever, Ashby, SmartRecruiters, Workday and Amazon.jobs publish machine-readable listings. Free, and the most reliable.
2. **Careers pages.** For companies with no recognized feed, Cloudflare Browser Run loads the page and a small Claude model pulls the listings out of it.
3. **Claude discovery.** For companies with neither, Claude searches the web directly. The most expensive route, so it runs last and only on what's left.
4. **The source scout.** Separately, Claude looks for feeds and careers pages for companies that have neither, moving them onto the cheaper routes over time.

Everything it finds is matched against your title list, then classified: is it MBA-level, is it Summer 2027, is it in a location you want. Postings it has already seen or already rejected are remembered, so the same work is never paid for twice.

## Releases

| Name | Date | What shipped |
| --- | --- | --- |
| apples | 2026-09-21 | First working implementation |
| bananas | 2026-09-22 | Bug fixes in the original implementation, plus automatic backups |
| coconuts | 2026-09-24 | Reworked the Titles panel |
| daiquiri | 2026-09-25 | Phase 5: search caching, so repeats and known rejections are skipped |
| eggfruit | 2026-10-05 | Phase 6: Cloudflare Browser Run reads pages before Playwright renders them, cutting cost and getting past sites that block GitHub's IP addresses. Search and processing split into async tasks |
| fig | 2026-10-14 | Phase 7: public job board for other students. Switched to test-driven development, with Jest and Playwright suites behind an 80% hard gate |
| guava | 2026-10-15 | Phase 8: three.js moving city background and a cyberpunk palette |

## How it was built

Spec first, then code. Nothing in this repository was written by opening an editor and starting to type.

Each phase was specified in full before any of it existed. The overall shape of the system lives in `docs/plan.md`, and each phase has its own spec in `issues/phase-N.md` saying what that phase builds, what it must not touch, what the data looks like afterward, and what "done" means. The spec is the thing that gets argued over. Once it reads right, it becomes a GitHub issue ending in `@claude`, and Claude Code implements that issue and nothing else.

The loop for every phase is the same:

1. **Write the spec.** Pin down the behavior, the schema changes, and the boundary of the phase while it is still only words.
2. **Review and approve it.** Changing a sentence costs nothing. Changing a shipped Worker and a live database costs a weekend.
3. **Open it as an issue.** The issue body is the spec. Claude Code picks it up from the `@claude` mention and opens a pull request.
4. **Review the pull request against the spec**, not against taste. If the code does something the spec didn't ask for, that is a finding even when the code is good.
5. **Merge, deploy, then do the after-merge steps** the pull request lists.
6. **Only then write the next spec**, with what the last phase actually taught now in hand.

Two conventions hold the loop together. Any change is proposed in three sections first, **Functionality**, **Technical code changes**, and **Architecture**, so the effects are visible before a line moves. And nothing reaches `master` without a pull request, so the spec and the diff can always be read side by side.

From phase 7 on, the same idea runs one level down. Tests are written before the implementation, and the Jest and Playwright suites each sit behind an independent 80% hard gate in CI. The spec says what a phase should do, the tests say it in code, and the implementation is what makes them pass.

The cost of this shows up as patience. A phase is a long session and the specs take real time to write. What it buys is in the phase sections below, under **What this phase taught**: every rule listed there came from a spec that turned out to be incomplete, and each one was folded into `CLAUDE.md` so the next phase inherits it instead of rediscovering it.

---

# The eight phases

Phases 1 to 4 build the thing. Phases 5 to 8 make it cheaper, wider and more presentable. Each phase has its issue text in `issues/`. Do them in order, and start the next one only after the previous phase's pull request is merged and deployed.

## Phase 1: Local version

**What it builds.** The repository structure, the D1 database schema and its migrations, the FastAPI app, and the dashboard. It loads `seed/` so there is real data to look at from the first run.

**Why it comes first.** Nothing is online yet and nothing costs anything. You get something you can open in a browser and click around, which is the fastest way to find out that a layout or a workflow is wrong, while changing it is still cheap.

**What you do after it merges.** Run it locally (see Local development below), click every tab, and create the D1 database on Cloudflare. Claude shows you the database ID; save it as a GitHub secret, because phase 2 can't run without it.

**What this phase taught.** Two rules came out of it that still apply. Every new database column needs a migration, and the API code can't use relative imports inside `api/src/`, because the Workers runtime loads the entry file as a standalone module. Both are in `CLAUDE.md` now.

## Phase 2: Daily job

**What it builds.** The job-feed fetchers for the recognized hiring platforms, the logic that verifies postings are still open, pruning for ones that have closed, and the GitHub Actions workflow that runs the whole thing at 08:00 Central.

**Why it matters.** This is the point where the project stops being a dashboard you update by hand and starts being something that works while you sleep.

**What you do after it merges.** Open the **Actions** tab, choose **Daily search**, and click **Run workflow** to start one by hand. Manual runs skip the 08:00 guard. Read the run summary afterward, then merge.

**What to watch.** Feeds fail quietly when a company changes platforms. The run summary reports feed errors per run; a number that climbs over a week means a company needs re-scouting.

## Phase 3: Claude agent

**What it builds.** Discovery, classification, the source scout, the daily improvement step, the Analytics tab, budget settings, and the alerts and daily summary that arrive in your Claude app.

**Why it matters.** Most companies don't publish a feed. Without this phase the search only covers the minority that do.

**What you do after it merges.** Create the Claude routine for the daily summary (see the token steps below), set your budgets on the dashboard, then run a search and check the summary arrives.

**What to watch.** This is where money gets spent. Budgets cap tokens and searches per day and per week, and the forecast on the Budgets panel estimates the month. Two failure modes have come up repeatedly: a Claude step that returns nothing but still spends tokens, and a step that silently stops searching. Both show in the run summary, which is why reading it matters.

## Phase 4: Launch

**What it builds.** The dashboard, API and database live on your own domain, behind Cloudflare Access, with automatic deploys on every merge and a nightly backup to R2.

**Why it matters.** Until this phase the project only exists on your computer. After it, a merged pull request is live within a few minutes.

**What you do after it merges.** Finish the Cloudflare Access setup before merging, not after, so the dashboard is never briefly public. Then deploy (see Deploying to Cloudflare below), open your domain in a private window, and confirm it asks you to sign in.

**What to watch.** Check that the Deploy run actually finished. A merged fix with a failed deploy leaves the old version live, which has caused real confusion here: the site showed old behavior while the code on `master` was correct.

## Phase 5: Caching

**What it builds.** A fingerprint for every posting, so the same job found at a different URL is recognized as one. A memory of rejections, so a posting already judged unsuitable isn't re-judged every day. And a fingerprint per company feed, so an unchanged feed is skipped.

**Why it matters.** Before this, every run paid to classify the same postings over and over. It also created duplicate rows when the same job turned up from two sources.

**What to watch.** The memory is only as good as the rules behind it. When the rules were wrong, real internships were rejected once and then permanently hidden, including roles at General Motors, HP and Amazon. The fix was to tie each rejection to the version of your titles and locations it was judged under, so changing either re-judges the affected ones. If postings stop appearing, that memory is the first place to look.

## Phase 6: Careers pages

**What it builds.** Cloudflare Browser Run loads the careers page of every company that has one but no recognized feed, and a small Claude model extracts the listings. A Playwright fallback covers the case where Browser Run is unavailable or out of free minutes.

**Why it matters.** This is the middle group, and it is the largest one. Covering it through web search was the single biggest cost in every run.

**What to watch.** Most careers pages are landing pages with no listings on them, so the page the scout saves matters more than the reading of it. An internship-filtered search URL yields far more than a company's general careers page. Also, this step is where run times grew: loading dozens of pages one at a time pushed a run past GitHub's 60-minute limit and left the search button stuck. Run time is capped and reported now.

## Phase 7: Public board

**What it builds.** A view-only job board at a second hostname, showing only open postings, with New today and Earlier tabs. Alongside it, a visitor feedback form that files a GitHub issue for each submission.

**Why it matters.** It makes the project useful to other students rather than only to you, and it is the version you can show someone without handing over your own dashboard.

**What you do after it merges.** Point the second hostname at the Worker, set `FEEDBACK_SALT`, and make sure the GitHub token has Issues write. The form fails with a 503 if either is missing.

**What to watch.** Visitor feedback is untrusted public input, and this repository runs Claude Code on issues mentioning `@claude`. Anything a visitor writes is sanitized before it reaches an issue, so it can never trigger an automated action or ping anyone. If the repository is public, those issues are public too, so the form says so.

## Phase 8: City background

**What it builds.** A three.js night-city background across both sites, and a palette of neon blue, white and black with orange accents.

**Why it matters.** Presentation. The search was already working; this is what makes it worth showing to someone.

**What to watch.** A few sharp edges. Three.js belongs in `frontend/` as a static asset, never in the Worker bundle, which is close to Cloudflare's 3 MB compressed limit. An opaque background on `body` will paint over a fixed canvas behind it, which looks exactly like the background failing to load. And phones get a still image instead of the animation, so they never download three.js at all.

---

# Start the build

## 1. Set up the repository

1. Create a **private** repository named `internship-watch` on GitHub. Adding a README is optional; the one in this starter pack replaces it either way.
2. Run `claude setup-token` on your computer and sign in. Copy the token it shows.
3. In the repository, go to **Settings**, then **Secrets and variables**, then **Actions**. Add a secret named `CLAUDE_CODE_OAUTH_TOKEN` and paste the token. Never paste it anywhere else.
4. Install the Claude GitHub app on this repository only. In Claude Code, `/install-github-app` walks you through it.
   - This starter pack already includes the Claude workflow, `.github/workflows/claude.yml`. If the installer adds its own workflow file, delete one of the two before pushing. Two Claude workflows would make Claude run twice on every `@claude`.

## 2. Add these files

Push this starter pack to `master` **before** you protect the branch. Branch protection would block this first direct push.

If the offline snapshot you downloaded earlier is in the same folder, rename it first so the clone doesn't collide with it. That snapshot also unzips to a folder named `internship-watch`.

```bash
mv internship-watch internship-watch-snapshot   # only if the old snapshot folder is here
git clone https://github.com/<your-username>/internship-watch.git
cd internship-watch
# copy everything from this starter pack into the folder, including .github and .gitignore
git add -A
git commit -m "Add starter pack"
git branch -M master   # makes sure your branch is called master
git push -u origin master
```

On a Mac, the `.github` folder and `.gitignore` are hidden in Finder. Press Cmd+Shift+. to show them, or copy the files with the terminal.

## 3. Protect master

In **Settings**, add a branch rule for `master` that requires a pull request before merging. From now on, nothing reaches `master` without your review.

## 4. Build the phases

For each phase:

1. Open a new issue. Use the first line of the phase file as the title, and paste everything below the `---` line as the body.
2. The body ends with `@claude`, which starts Claude Code. Follow along in the **Actions** tab.
3. Review the pull request Claude opens. Comment `@claude` on it to ask for changes, and merge when you're happy.
4. Do the "after merging" steps in the pull request description, then deploy.

| Phase | File | Before you open it |
| --- | --- | --- |
| 1. Local version | `issues/phase-1.md` | Starter pack pushed, `master` protected |
| 2. Daily job | `issues/phase-2.md` | Database ID, database token and account ID saved in GitHub |
| 3. Claude agent | `issues/phase-3.md` | Routine URL and token saved in GitHub |
| 4. Launch | `issues/phase-4.md` | Deploy token saved in GitHub. Cloudflare Access configured |
| 5. Caching | `issues/phase-5.md` | Phase 4 merged and deployed |
| 6. Careers pages | `issues/phase-6.md` | `CLOUDFLARE_BROWSER_TOKEN` saved in GitHub |
| 7. Public board | `issues/phase-7.md` | Second hostname pointed at the Worker. `FEEDBACK_SALT` set. GitHub token has Issues write |
| 8. City background | `issues/phase-8.md` | Phase 7 merged and deployed |

Only your own comments can start Claude. Mentions from anyone else are ignored.

---

# Adding each token

Nine credentials in total. Set each one up as you reach the phase that needs it. Copy every value straight into GitHub or Cloudflare, and never paste one into a chat.

**GitHub secrets** go in under **Settings**, **Secrets and variables**, **Actions**, then **New repository secret**.

**Worker secrets** go in from the `api/` folder with `npx wrangler secret put <NAME>`, which prompts for the value. They are never shown again, and `npx wrangler secret list` shows only the names.

## `CLAUDE_CODE_OAUTH_TOKEN` — GitHub secret, needed from phase 1

1. On your computer, run `claude setup-token`.
2. Sign in when prompted.
3. Copy the token it prints and save it as the GitHub secret.

This is how Claude Code runs on your subscription rather than a paid API key.

## `CLOUDFLARE_ACCOUNT_ID` — GitHub secret, needed from phase 2

1. Sign in to the Cloudflare dashboard.
2. Open **Workers & Pages**. The account ID is shown on the overview page.
3. Copy it and save it as the GitHub secret. It isn't secret in the way a token is, but the workflows read it from there.

## `D1_DATABASE_ID` — GitHub secret, needed from phase 2

1. Create the database, from `api/`: `npx wrangler d1 create internship-watch-db`.
2. Wrangler prints the database ID. Copy it.
3. Save it as the GitHub secret, and make sure the same ID is in `api/wrangler.toml`.

## `CLOUDFLARE_D1_TOKEN` — GitHub secret, needed from phase 2

The daily job writes to the database over Cloudflare's HTTP API, so it needs its own token with nothing else attached.

1. In Cloudflare, open your profile menu, then **API Tokens**, then **Create Token**, then **Create Custom Token**.
2. Name it `internship-watch d1`.
3. Under **Permissions**, set **Account** → **D1** → **Edit**. Add nothing else.
4. Under **Account Resources**, choose **Include** and pick your own account, not All accounts.
5. Create the token and copy it straight into GitHub. Cloudflare shows it once.

## `CLAUDE_ROUTINE_URL` and `CLAUDE_ROUTINE_TOKEN` — GitHub secrets, needed from phase 3

These let the daily job send you a summary in your Claude app.

1. In the Claude desktop app, create a cloud routine so it runs even when your computer is off.
2. Paste in the routine instructions Claude gives you during phase 3.
3. Set the routine to start from an API call.
4. Copy the routine's trigger address into GitHub as `CLAUDE_ROUTINE_URL`, and its access token as `CLAUDE_ROUTINE_TOKEN`.
5. Run the daily job once by hand and check the summary appears.

## `CLOUDFLARE_DEPLOY_TOKEN` — GitHub secret, needed from phase 4

This one deploys. It needs more than the D1 token, which is exactly why they're separate.

1. In Cloudflare, **API Tokens**, **Create Token**, **Create Custom Token**.
2. Name it `internship-watch deploy`.
3. Under **Permissions**, add four entries:
   - **Account** → **D1** → **Edit**
   - **Account** → **Workers Scripts** → **Edit**
   - **Account** → **Workers R2 Storage** → **Edit**, for the nightly backup
   - **Zone** → **Workers Routes** → **Edit**, scoped to your domain
4. Under **Account Resources**, Include your account. Under **Zone Resources**, Include your domain.
5. Create it and copy it straight into GitHub.

## `GITHUB_TOKEN` — Worker secret, needed from phase 4

The Titles panel, Check now, and the feedback form all call GitHub's API from the Worker.

1. Go to **github.com/settings/personal-access-tokens** and create a **fine-grained** token.
2. Name it `internship-watch`.
3. Under **Repository access**, choose **Only select repositories** and pick `internship-watch`.
4. Under **Repository permissions**, set all four to **Read and write**:
   - **Actions**, so the dashboard can start a search
   - **Contents**, so title edits can open a branch
   - **Pull requests**, so the Titles panel can open and list them
   - **Issues**, so visitor feedback can file an issue
5. Set an expiration. A year is reasonable, with a calendar reminder to rotate it.
6. Create it, then from `api/` run `npx wrangler secret put GITHUB_TOKEN` and paste it when prompted.

**Missing Issues write is a real failure mode here.** It makes the feedback form fail with a 503 and no obvious cause. Editing a fine-grained token's permissions later doesn't change its value, so you don't need to update the Worker secret when you do.

## `CLOUDFLARE_BROWSER_TOKEN` — GitHub secret, needed from phase 6

1. In Cloudflare, **API Tokens**, **Create Token**, **Create Custom Token**.
2. Name it `internship-watch browser run`.
3. Under **Permissions**, set **Account** → **Browser Rendering** → **Edit**. Nothing else. It may be listed under Developer Platform, and Cloudflare sometimes calls it Browser Run.
4. Under **Account Resources**, Include your account.
5. Create it and save it as the GitHub secret.
6. Make sure the workflows pass it through: it must appear in the `env:` block of the step that runs `python -m job.run`, in both `daily-search.yml` and `check-now.yml`. A secret that exists but isn't passed through looks exactly like a missing one.

## `FEEDBACK_SALT` — Worker secret, needed from phase 7

This anonymizes visitors so the daily submission limit can count them without storing anything identifying. Any long random string works.

1. Generate one. On Windows PowerShell:
   ```powershell
   -join ((48..57)+(97..122) | Get-Random -Count 48 | % {[char]$_})
   ```
   On macOS or Linux: `openssl rand -hex 24`
2. From `api/`, run `npx wrangler secret put FEEDBACK_SALT` and paste the value.
3. Keep it stable from then on. Changing it changes every visitor's hash, which resets rate-limit counts.

Without it the feedback endpoint switches itself off rather than storing raw visitor data, which is the right call but looks like a server error from outside.

---

# Deploying to Cloudflare

`docs/deploy.md` is the authoritative version. This is the short path.

## Before you start

Install **Node.js** (current LTS, for `wrangler`) and **uv** (for Python Workers). Then sign in once, from `api/`:

```bash
npx wrangler login
```

## One-time setup

**1. Register the domain.** In Cloudflare, open **Domain Registration** and register your domain, or transfer one in. Cloudflare sells at cost and sets up DNS for you. Leave the DNS records alone; the deploy attaches the Worker to the hostname.

**2. Create the database.** From `api/`:

```bash
npx wrangler d1 create internship-watch-db
```

Copy the database ID it prints into `api/wrangler.toml` and into the `D1_DATABASE_ID` GitHub secret.

**3. Turn on the login.** Do this before the first deploy, so the dashboard is never briefly public.

1. In Cloudflare, open **Zero Trust** and choose the free plan.
2. Under **Access**, then **Applications**, add a **Self-hosted** application.
3. Set its domain to your dashboard hostname, for example `internship-watch.yourdomain.com`.
4. Add a policy that allows only your own email address.
5. Keep the default login method, a one-time code sent to your email.
6. From the application's settings, copy the **Application Audience (AUD) tag** and your **team domain**. Both go into `api/wrangler.toml` under `[vars]` as `ACCESS_AUD` and `ACCESS_TEAM_DOMAIN`.

**4. Set the Worker secrets.** From `api/`:

```bash
npx wrangler secret put GITHUB_TOKEN
npx wrangler secret put FEEDBACK_SALT   # from phase 7 onward
```

## Deploying

```bash
cd api
npx wrangler d1 migrations apply internship-watch-db --remote
uv run pywrangler deploy
```

The first command applies any new database migrations to the live database. The second builds and uploads the Worker, which serves both the API and the dashboard's static files.

A successful deploy prints the bindings, the upload size, and the route it attached:

```
Uploaded internship-watch
Deployed internship-watch triggers
  internship-watch.yourdomain.com/* (zone name: yourdomain.com)
```

**Watch the gzip figure in the upload line.** The free plan's limit is 3 MB compressed. This project runs close to it, which is why front-end libraries go in `frontend/` as static assets; those are uploaded separately and don't count toward the Worker's size.

## After phase 4, deploys are automatic

Merging to `master` runs CI, and a green CI run triggers the Deploy workflow, which applies migrations and deploys. You only run the commands above for the first deploy, or when something has gone wrong.

**Always confirm the Deploy run finished.** A merged pull request with a failed deploy leaves the old version live.

## Adding the public hostname (phase 7)

1. In Cloudflare DNS, add a record for the public hostname, for example `jobs.yourdomain.com`, pointing at the same Worker.
2. Add the route to `api/wrangler.toml` alongside the dashboard's.
3. Do **not** put this hostname behind Cloudflare Access. It is meant to be public, and the Worker serves only the public pages on it.
4. Deploy, then open it in a private window and confirm it loads without a login and shows only open postings.

## Backups

The nightly backup exports the database to an R2 bucket named `internship-watch-backups`. Create the bucket in Cloudflare under **R2** before phase 4's backup workflow runs, and make sure the deploy token has Workers R2 Storage permission.

---

# Usage and cost

Building a phase is a long Claude Code session on your Claude subscription. It can use a large share of one five-hour usage window, so start a phase when you don't need Claude for anything else for a while.

Running costs, once it's live:

- **Cloudflare Workers, D1 and R2:** the free tier is enough. The Worker is near the free plan's 3 MB compressed limit, so libraries belong in `frontend/` as static assets.
- **Cloudflare Browser Run:** 10 minutes a day free. Past that, Workers Paid is $5 a month for 10 hours, then $0.09 an hour.
- **GitHub Actions:** 2,000 minutes a month on a private repository. Daily runs must stay well under 25 minutes or the quota runs out mid-month and everything silently stops. A public repository gets Actions free.
- **Claude:** daily runs use your subscription. Budgets on the dashboard cap tokens and searches per day and per week, and the Budgets panel forecasts the month.

# Seed data

`seed/` holds a snapshot of the dashboard you used before this project, taken September 19, 2026: 9 postings, 30 sources, 2 search log entries, 100 companies, and your settings (US and Japan). Phase 1 loads it into the local database.

# Local development

Requires **Node.js** (current LTS) and **uv**. Sign in to Cloudflare once with `npx wrangler login` from `api/`.

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

The Titles panel, Check now, and title edits all call GitHub's API through the `GITHUB_TOKEN` Worker secret. Locally, put the same token in `api/.dev.vars` as `GITHUB_TOKEN=...` to exercise those; without it, they return a clear error instead of working.

To re-seed from scratch, delete `api/.wrangler/` (the local D1 state) and repeat the steps above.

**The cron trigger.** The Worker's `scheduled` handler (`api/src/cron.py`) is what starts the daily search at 08:00 Central in production. To exercise it locally, start the Worker with the scheduled-test route switched on and then request it with the cron you want to simulate (Python Workers expose it at `/cdn-cgi/local/scheduled`; the `/__scheduled` path is the JavaScript one):

```bash
cd api
uv run pywrangler dev --test-scheduled
curl "http://localhost:8787/cdn-cgi/local/scheduled?cron=0+13+*+*+*"
curl "http://localhost:8787/cdn-cgi/local/scheduled?cron=0+14+*+*+*"
```

Under daylight time the first dispatches and the second does nothing; under standard time it is the other way round. Leave `GITHUB_DISPATCH_TOKEN` unset in `api/.dev.vars` so a local dispatch fails with a clear error instead of starting a real 40-minute run.

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
python scripts/validate_titles.py
```

# Frontend tests

Two independent suites cover the dashboard (`frontend/app.js`) and the public page (`frontend/public/public.js`). Each one has its own hard gate: at least 80% of statements, branches, functions and lines of `frontend/**/*.js`, measured from that suite alone (the two are never merged). Below 80% on any measure, the suite fails and so does CI. Node is dev and CI tooling only; nothing from it ships in the Worker, and there is still no build step.

Install once from the repository root (Node 22):

```bash
npm ci
npx playwright install chromium     # add --with-deps on a fresh Linux machine
pip install -r requirements-dev.txt # the Playwright test server is Python
```

**Jest (unit tests, jsdom).** Tests are in `frontend/__tests__/`. They load the real `index.html` files into jsdom and run the real scripts against a faked API.

```bash
npx jest --coverage
```

The 80% threshold is `coverageThreshold` in `jest.config.js`.

**Playwright (browser tests, Chromium).** Tests are in `tests/ui/`. `playwright.config.js` starts `tests/ui/serve.py`, which serves the real FastAPI app on the in-memory database with seeded postings, plus `frontend/` the way the `ASSETS` binding does. Port 8787 stands in for the dashboard hostname and port 8788 for the public one (`/` shows `frontend/public/index.html`). Cloudflare Access stays unconfigured, GitHub is a small in-memory fake, and Google Fonts requests are blocked. The Pyodide entrypoint in `api/src/main.py` is not run.

```bash
npx playwright test     # also writes Chromium's V8 coverage to .nyc_output/
npm run coverage:ui     # prints the table, then fails below 80%
```

The gate itself is `nyc check-coverage --statements 80 --branches 80 --functions 80 --lines 80`.

Playwright runs with `reducedMotion: "reduce"`, so pages show the city's still image and don't pay for software WebGL. Only `tests/ui/city-background.spec.js` turns the animation on. Third-party code in `frontend/**/vendor/` is not measured by either gate.

# City background

The night-city background (Phase 8) is `frontend/public/city-background.js`, drawn with three.js. The camera drifts forward down a street canyon, low and tilted slightly down; its height, tilt, field of view, speed and drift (the `CAMERA` object) and the low-frame-rate check (`LOW_FPS`, `LOW_FPS_SECONDS`) are named constants at the top of that file. Colors come from the CSS tokens in `frontend/style.css`.

It lives under `frontend/public/` because the public hostname only serves `/public/*`; the dashboard loads the same files. Phones (viewport under 768px with a coarse pointer), reduced motion, no WebGL and machines that stay under 15 frames a second for 10 seconds get `city-still-dark.webp` (with a JPEG fallback) instead, and never download three.js. The still is always the dark one.

Two things to regenerate, then commit the results:

```bash
npm run vendor:three  # frontend/public/vendor/three.min.mjs, only the three.js classes the city uses
npm run city:still    # the still images, one rendered frame of the same scene (run after palette or scene changes)
```

# Running the daily job by hand

The daily job (`job/run.py`) normally runs on GitHub Actions at 08:00 America/Chicago (`.github/workflows/daily-search.yml`), or for specific companies when you click Check now on the dashboard (`.github/workflows/check-now.yml`). To run it yourself:

**From GitHub Actions (writes to the real database):** open the **Actions** tab, choose **Daily search**, and click **Run workflow**. Manual runs skip the 08:00 guard and run immediately. This needs `CLOUDFLARE_D1_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` and `D1_DATABASE_ID` saved as repository secrets first. Leave the `run_source` input at `manual`; the dashboard's Recent runs list shows it, next to `cloudflare` for runs the Worker's cron trigger started and `schedule` for the GitHub Actions cron.

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

# Making changes after launch

1. Ask Claude for a change. It replies with a proposal in three sections: Functionality, Technical code changes, Architecture.
2. Approve it, ask for changes, or reject it.
3. Open a GitHub issue with the approved text, ending in `@claude`.
4. Claude Code creates a branch, makes the change, runs the tests, and opens a pull request.
5. Review it and merge. CI runs the tests again and deploys within a few minutes.

**To undo a change,** use **Revert** on the merged pull request. That deploys the previous version. Reverting code doesn't undo database changes, so any proposal that changes the database says how to undo it.

**Claude Code can't edit files in `.github/workflows/`.** When a change needs one, it puts the exact YAML in the pull request description for you to apply in GitHub's web editor.

# When something goes wrong

| Symptom | Where to look first |
| --- | --- |
| The Search now button is stuck on "Searching…" | A run that ended without recording a finish. Check the Actions tab, then clear unfinished rows in `runs`, `search_requests` and `check_requests` |
| The daily search isn't running | A workflow file error on `master`, or GitHub Actions minutes exhausted. Check the Actions tab for a warning banner, then **Settings**, **Billing and plans** |
| An endpoint returns 5xx with nothing in the logs | An exception caught and converted to a status code without logging. Run `npx wrangler tail internship-watch` and try again |
| A fix is merged but the site is unchanged | Check the Deploy run in the Actions tab finished. A failed deploy leaves the old version live |
| Postings stop appearing | Read the run summary. It reports searches made, companies covered, pages loaded, and anything skipped for budget |
| The feedback form fails | `FEEDBACK_SALT` missing, or the GitHub token lacking Issues write |

The run summary is the single most useful thing to read. Every run records one, in the `runs` table and in the job's output.

# Keeping it safe

- Keys and tokens go only in GitHub secrets and Cloudflare Worker secrets. Never in chat, in code, or in files in the repository.
- If a key or token is ever exposed, delete it where you created it and make a new one.
- Each token holds the least it needs. That's why the D1 token and the deploy token are separate, and why the Browser Run token has one permission.
- Visitor feedback is untrusted public input. It is sanitized before it reaches a GitHub issue, so it can never trigger Claude Code or ping anyone.
- Look closely at any pull request that changes files in `.github/workflows`, since those control what runs and deploys.
