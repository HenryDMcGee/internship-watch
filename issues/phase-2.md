Phase 2: Daily job

---

Henry approved phases 2 to 4 on September 20, 2026. Build this phase directly. If you need to go beyond this scope, comment with a proposal in three sections (Functionality, Technical code changes, Architecture) and wait for approval.

Read `CLAUDE.md`, `docs/plan.md`, and `docs/deploy.md` first. Phase 1 is merged.

## Build

- **Daily job, `job/` (Python 3.12):**
  - `job/run.py` is the entry point.
  - Load settings, companies, sources, and postings from D1 through Cloudflare's D1 HTTP API, using the `CLOUDFLARE_D1_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and `D1_DATABASE_ID` environment variables.
  - Load titles from `config/titles.json`.
- **Feed fetchers, `job/fetchers/`:**
  - Greenhouse, Lever, Ashby, SmartRecruiters, Workday (unofficial JSON endpoints), and Amazon.jobs.
  - Each returns a common posting model.
  - Handle errors per company, so one broken feed never stops the run.
- **Feed detection, `job/feeds/detect.py`:** recognize the feed type and ID from a careers page link. Use the same logic when a company is added on the dashboard.
- **Matching without Claude:**
  - Match active titles and aliases (case-insensitive) and apply exclude words.
  - Cycle rules: Summer 2027 only. Reject "MBA class of 2027" and other last-cycle wording.
  - Location parsing from feed fields: match, outside, or unclear, against the Locations setting (US, JP, remote switch).
  - Store items the rules can't decide as `unverified` for phase 3 to classify.
- **Verification, `job/verify/`:** follow the order in `docs/plan.md`.
  - Order: data feed, then structured page data, then headless browser (Playwright with Chromium, capped by `browser_checks_per_run`, default 50), then mirror sites (Dreamwork, Simplify) as a tiebreaker only.
  - Respect Henry's Mark open and Mark closed answers.
  - Record `verified_via` and `verified_at`.
  - Respect sites that block automated browsers, and never try to get around them.
- **Pruning:** archive with a reason. Never delete.
- **Quarterly ranking refresh:** on the first weekday of January, April, July, and October, using the source chosen in phase 1. Pins survive the refresh.
- **Run records:** `runs`, `run_stats`, and `source_checks` rows for every run.
- **Workflows:**
  - `.github/workflows/daily-search.yml`: `schedule` at `0 13 * * *` and `0 14 * * *` plus `workflow_dispatch`. A guard step exits unless it's 08 in America/Chicago, and manual runs skip the guard. Cache Playwright browsers.
  - `.github/workflows/check-now.yml`: `workflow_dispatch` with a `request_id` input. It runs the job for the requested companies only.
  - Both use one concurrency group so they never overlap.
- **API additions:**
  - `POST /api/companies/check`: creates a `check_requests` row, enforces 5 per day, and starts `check-now.yml` through GitHub's API using the Worker secret `GITHUB_TOKEN`. Add a status endpoint too.
  - `POST /api/postings/{id}/confirm`: takes `open` or `closed`.
  - Title edits: validate against `config/titles.schema.json`, then create or update one branch and one pull request that changes only `config/titles.json`.
- **Dashboard:** Check now buttons with status, Mark open and Mark closed, verification labels, location tags including "Location unclear", and the Titles editor with a "Pending change" link to the open pull request.
- **Docs:** add the `npx wrangler secret put GITHUB_TOKEN` step to `docs/deploy.md`, and describe running the job by hand in the README.
- **Tests:**
  - Saved sample responses for every fetcher.
  - Feed detection, title matching, cycle rules, location parsing, verification order, and pruning reasons.
  - The 08:00 guard logic, including daylight saving time.
  - The Check now daily limit.
  - The title pull request flow, with a mocked GitHub API.

## Done when

- Starting `daily-search.yml` by hand adds real postings to the D1 database.
- CI passes.
- The pull request description lists what Henry must do after merging: store the Worker secret, redeploy, and run the job once by hand.

@claude please build phase 2 as described above and open a pull request.
