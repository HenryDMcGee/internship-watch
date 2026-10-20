# Internship Watch: approved plan

Approved by Henry on September 19, 2026. Human-readable version: the "Internship Watch: Plan Overview" document. This file is the technical spec.

## Goal

Every morning, find open Summer 2027 MBA internships that match Henry's titles at the top 100 tech companies worldwide plus pinned companies, in the US and Japan. Check them, show them on a private dashboard, and improve the search each day.

## Names

| Thing | Name |
| --- | --- |
| Display name | Internship Watch |
| Dashboard address | `https://internship-watch.mcgeehenry.com` (mcgeehenry.com is already in Henry's Cloudflare account, with his website on it) |
| Public job board address | `https://jobs.mcgeehenry.com` (no login; a second route into the same Worker) |
| GitHub repository | `internship-watch` |
| Cloudflare Worker (dashboard and API) | `internship-watch`, configured in `api/wrangler.toml` |
| Cloudflare D1 database | `internship-watch-db` |
| Cloudflare R2 backup bucket | `internship-watch-backups` |

## Components

| Part | Where it runs | Notes |
| --- | --- | --- |
| Daily job | GitHub Actions, Python 3.12 | 08:00 America/Chicago. Scheduled at 13:00 and 14:00 UTC with a guard step. Can also be started by hand. From the Cloudflare daily trigger (phase 1), the Worker's cron trigger also dispatches it at 08:00 Central, on time; each run records which path started it in `runs.source`. |
| Check now | GitHub Actions `check-now.yml` | Started by the Worker through GitHub's API. Checks specific companies only. Max 5 per day. |
| App | One Cloudflare Python Worker, FastAPI | Serves `/api/*` and the dashboard's static files from `frontend/` through the `ASSETS` binding (`run_worker_first = true`; unmatched paths go to `ASSETS`). Verifies the Cloudflare Access token on every API request. `workers_dev` and preview URLs are off. Developed and deployed with `pywrangler` (`uv run pywrangler dev`, `uv run pywrangler deploy`). Uses the Python version Cloudflare's runtime requires (3.13 at the time of writing). |
| Address | Worker route | `internship-watch.mcgeehenry.com/*` on the mcgeehenry.com zone, with a proxied `AAAA internship-watch 100::` DNS record added once by Henry. |
| Database | Cloudflare D1 | The daily job writes through Cloudflare's D1 HTTP API. The Worker uses its D1 binding. |
| Backups | Cloudflare R2 | Nightly SQL export from the daily job. |
| Login | Cloudflare Access | Self-hosted application for exactly `internship-watch.mcgeehenry.com`, one-time PIN, Henry's email only. Never on the root `mcgeehenry.com`, which hosts his website. |
| Claude | Claude Code in GitHub Actions (`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`, the same token used for building) | Default models: Sonnet for discovery, the source scout, and daily improvement; Haiku for classification and unclear-page checks. Which secret is set decides who is billed; the budgets are always in dollars (phase 9). |
| Alerts and summary | Claude Code remote routine, triggered by API call | One message a day after the run. Alerts are included in it. |

## Daily run, in order

1. Load settings from D1 and `config/titles.json`.
2. On the first weekday of each quarter, refresh the top-100 ranking.
3. Read company job feeds for every company with a known feed. Free, no Claude.
4. Claude discovery: web search for companies without a working feed and for the watch list, following the day's search plan. Budget checked before every Claude step.
5. Classify each result: matches an active title or alias, MBA-level, Summer 2027, not an excluded word, location (US, Japan, and remote if switched on). Store the matched title and an English title if the original isn't English.
6. Verify each posting, new and existing, stopping at the first definite answer:
   1. the company's data feed (Workday, Apple, Greenhouse, Lever, Ashby, and others)
   2. the company's careers page: a posting listed on it in this run counts as open; a posting missing from it is never marked closed, since careers pages often show only their first page of results
   3. structured job data in the page (schema.org JobPosting, `validThrough`)
   4. headless browser (Playwright and Chromium), capped by the `browser_checks_per_run` setting (default 50); unconfirmed postings, newest first, get these checks before the rest; Haiku reads the text only when it's unclear
   5. mirror sites (Dreamwork, Simplify) as a tiebreaker only
   6. Henry's Mark open or Mark closed answer, which sticks unless the posting disappears from the company's own feed
7. Archive postings that are gone, past their deadline, no longer accepting applications, or for the wrong cycle, such as "MBA class of 2027". Never delete.
8. Save everything to D1 and record verification method and time.
9. Source scout: find careers pages, feeds, and job boards for companies without a working source first. New sources start as `trial` and become `active` after finding a real open posting. Sources that keep finding nothing are `demoted`. Retiring needs Henry's approval.
10. Daily improvement: write tomorrow's search plan. Small changes (query wording, source order) apply automatically. Company and source changes go to Proposals. Title suggestions are pull requests editing `config/titles.json`.
11. Record usage per Claude call, evaluate alert rules, and trigger the Claude routine with the daily summary and any alerts.

## Features by area

- **Postings:** tabs New today (first seen today, Central time), Earlier, and Archived. Mark applied, delete, restore, Mark open, Mark closed. Each card shows how and when it was last verified, plus location tags and "Location unclear".
- **Titles:** `config/titles.json` with `group`, `aliases`, `active`, and `exclude_words`. The dashboard shows titles by group. From phase 2, dashboard edits open or update a single pull request through the Worker. Claude's suggestions are pull requests too.
- **Companies:** the top 100 tech companies worldwide by market value, including internet and platform companies (not only the IT sector), refreshed quarterly. Status `ranked`, `pinned`, or `removed`. Add one company with an optional careers link, which is auto-detected as a feed type when possible. Paste a list with a preview showing recognized, already on the list, unclear, and duplicates. Check now per company. Companies with no results for a few weeks are checked weekly.
- **Sources:** list grouped by kind with status, postings found, dead-link rate, and last checked. Add a site or remove one.
- **Locations:** the US always, plus chosen countries (starts with Japan), and a remote switch. Out-of-location postings are saved but hidden.
- **Analytics:** coverage per company, source quality, first to find, postings per title, verification methods and unconfirmed count, pruned postings with reasons, and usage against budgets with alert history.
- **Budgets** (one billing model since phase 9: API-key dollars):
  - daily and monthly dollar budgets and a daily search budget. Defaults: $1.50 a day, $30 a month, 35 searches a day. The daily job enforces today's dollars and searches; this week's dollars are forecast but not enforced.
  - share per step, defaulting to discovery 60%, scout 20%, classification 15%, improvement 5%. Shown as whole percents with a live total; stored as 0 to 1 decimals summing to 1.0. Shares cannot be saved unless they total 100%.
  - model per step (Haiku or Sonnet), defaulting to Sonnet for discovery, the source scout, and daily improvement, and Haiku for classification and unclear-page checks
  - advanced limits, collapsed: searches per run and per company, and browser checks per run
  - order for cutting steps when the budget runs low: scout, then low-yield discovery, then the rest. Classification and pruning always run.
  - forecast (this week and this month in dollars), usage (today and this month in dollars, searches used) with daily and weekly resets, and change history
  - the Public site block: the on/off switch, the `public_feedback_daily_limit` cap (default 20 submissions a day across all visitors), today's feedback count against it, and today's board views
  - the Browser Run status: summary lines and the last runs table
- **Alerts:** at 80% and 100% of budget, a spike above twice the 7-day average, a run stopped by a Claude limit, and a failed run. One alert per type per day, delivered inside the daily Claude app message.
- **Proposals:** list of pending company and source changes with approve and reject.

## Data model (D1)

Starting point for `0001_init.sql`. Adjust names if needed, but keep every concept.

- `settings(key TEXT PRIMARY KEY, value TEXT JSON)`: locations, include_remote, watchlist, last_run, and similar.
- `budget_settings` (single row: `daily_usd_budget`, `monthly_usd_budget`, `daily_search_budget`, the four `share_*` and `model_*` fields, `search_limit_per_run`, `search_limit_per_company`, `browser_checks_per_run`, `public_feedback_daily_limit`, and the Browser Run settings; `billing_mode` and the token budgets were removed in phase 9) and `budget_changes(id, changed_at, field, old_value, new_value)`.
- `public_board_views(day, views)`: loads of the public job board per Central day (phase 9).
- `companies(id, name, aliases JSON, status, rank, market_cap_usd, ranking_date, ranking_source, hq_country, careers_url, feed_type, feed_id, added_by, added_at, check_cadence, last_result_at)`.
- `ranking_snapshots(snapshot_date, source, company_name, rank, market_cap_usd)`.
- `sources(id, name, url, kind, covers, status, discovered_by, discovered_at, found_count, dead_link_rate, last_checked, last_success_at, notes)`.
- `source_checks(id, source_id, run_id, found, dead, error)`.
- `postings(id, company_id, company_name, title, title_en, matched_title, url, mirror_url, source_id, locations JSON, location_match, posted_date, first_seen, status, status_note, verified_via, verified_at, user_status, user_status_at, applied, archived_at, archive_reason, note, public_hidden)`.
  - `public_hidden` (0 or 1, default 0) keeps a posting off the public job board.
  - `status` is one of open, closing, closed, or unverified.
  - `location_match` is one of match, outside, or unclear.
- `posting_events(id, posting_id, type, detail JSON, at)`.
- `runs(id, kind, started_at, finished_at, status, new_count, summary, next_notes)`, where `kind` is daily or check_now.
- `run_stats(id, run_id, source_id, query, found, useful)`.
- `search_plans(plan_date, plan JSON)`.
- `proposals(id, kind, payload JSON, status, created_at, decided_at)`.
- `check_requests(id, company_ids JSON, requested_at, status, result JSON)`.
- `usage(id, run_id, step, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, web_searches, est_cost_usd, at)`.
- `alerts(id, type, severity, message, created_at, dismissed_at)`.
- `feedback(id, source, kind, posting_id, message, page_url, visitor_hash, status, issue_url, created_at, decided_at)`.
  - `source` is public or owner. `kind` is bug, dead_link, closed, wrong_job, suggestion, or other. `status` is new, read, issue_opened, or dismissed.
  - `visitor_hash` is SHA-256 of `FEEDBACK_SALT` plus the visitor's IP. The address itself is never stored.
- The public site's on/off switch is the `public_site_enabled` key in `settings` (JSON true or false; missing means off).

Seed data in `seed/` comes from the dashboard Henry uses today:
- `postings.json`: 9 postings, with 3 closed
- `sources.json`: 30 sources
- `runs.json`: 2 search log entries
- `companies.json`: 100 hand-picked companies
- `settings.json`: locations, watch list, and timezone

Map the old field names (`firstSeen`, `statusNote`, and so on) to the new schema in `scripts/seed.py`.

## Build phases

1. **Local version:**
   - repository structure, the full D1 schema, the API, and the dashboard ported from `docs/reference/current-dashboard.html`
   - a read-only Titles view
   - the ranked Companies list with pins, adding one company, and pasting a list
   - the Locations panel, seeded and running locally with `wrangler`
   - picking the ranking source and doing the first import
2. **Daily job:**
   - feed fetchers, feed detection from links, all verification checkers, and the confirm buttons
   - pruning, location parsing for feeds, the 08:00 schedule, and Check now
   - dashboard title edits that open pull requests
3. **Claude agent:**
   - discovery, classification, translation, and Claude's help on unclear pages
   - source scout and daily improvement
   - Analytics, Budgets, usage tracking, and the Claude routine summary and alerts
4. **Automatic deploys:**
   - the deploy workflow (`deploy.yml`): on merge to `main`, apply D1 migrations and run `pywrangler deploy`
   - nightly R2 backups
   - Henry can deploy by hand any time after phase 1 using `docs/deploy.md`
5. **Search optimization:**
   - cached page reads and posting fingerprints, so repeat searches skip work already done
   - Search now on the dashboard, and search coverage so companies are not skipped
6. **Browser Run:**
   - careers pages with no feed are read through Cloudflare Browser Run, with Playwright as the backup
   - `browser_run_usage` tracking, a careers time budget, and Claude-proposed careers page repairs in Proposals
7. **Public job board and feedback:**
   - `https://jobs.mcgeehenry.com`, no login, read only: open and closing postings with New today and Earlier tabs, search, and a "Last updated" line
   - postings whose status isn't confirmed yet show too, tagged "Not yet confirmed", so a new find appears after the run that found it (Henry, October 2026)
   - a switch in the Locations panel (starts off) and a Hide from public toggle per posting
   - a feedback form on the public board (honeypot, 10 a day per visitor, 200 a day in total), the owner's own Feedback button, and a Feedback view with Mark read, Dismiss, and Open as GitHub issue
   - a counts-only feedback line in the daily summary
   - guardrail (Henry, October 19, 2026, issue #129): feedback-to-issue creation is opt-in via `FEEDBACK_ISSUES_ENABLED = "true"`, set only on the deployed Worker and the real daily job, never in tests, CI, or local development; and a row whose `Feedback row: N` marker is already in an open `visitor-feedback` issue is linked to it, not posted again
8. **City background:** the three.js night-city background behind the dashboard and the public board, with a still image under reduced motion, and the cyberpunk palette
9. **Budgets dialog redesign, dollars-only billing** (approved October 19, 2026, `issues/phase-9.md`):
   - one billing model, API-key dollars: `billing_mode`, `daily_token_budget` and `weekly_token_budget` are removed everywhere, with a one-time migration (`0017`) that keeps every other stored value and records the removal in the change history
   - the daily job enforces today's dollars and searches only; weekly dollars are forecast, not enforced; the Search now 80% confirmation uses the dollar fraction
   - the dialog: exactly three limits (Daily $, Monthly $, Daily searches), one row per step with a whole-percent share and a model, a live total pill and an Even split button, inline validation that disables Save until every field is valid and the total is 100%, the rarely changed limits collapsed under Advanced, the forecast and usage in dollars, the Public site block, and Browser Run without the 7-day chart
   - `public_feedback_daily_limit` (default 20) caps public feedback submissions a day across all visitors, enforced by `/api/public/feedback` alongside the honeypot and the per-visitor limit; `public_board_views` counts board loads for the Public site status line

## Secrets (never in git)

| Secret | Stored in | Used by |
| --- | --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` (or `ANTHROPIC_API_KEY`) | GitHub | Building with Claude Code, daily job, Check now |
| `CLOUDFLARE_DEPLOY_TOKEN` (D1 edit, Workers Scripts edit, Workers R2 Storage edit, and Workers Routes edit on the mcgeehenry.com zone) | GitHub | Automatic deploys and nightly backups (phase 4) |
| `CLOUDFLARE_D1_TOKEN` | GitHub | Daily job, Check now (phase 2) |
| `CLOUDFLARE_ACCOUNT_ID`, `D1_DATABASE_ID` | GitHub | Deploys and daily job |
| Worker's GitHub token (Actions, Contents, Pull requests, and Issues read and write, this repository only) | Cloudflare Worker | Check now and title edits (phase 2), and Open as GitHub issue (phase 7). The Worker may only edit `config/titles.json`. |
| `FEEDBACK_SALT` | Cloudflare Worker secret | Hashes visitor addresses for the public feedback limits (phase 7) |
| `CLAUDE_ROUTINE_URL`, `CLAUDE_ROUTINE_TOKEN` | GitHub | Daily summary and alerts (phase 3) |
