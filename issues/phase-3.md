Phase 3: Claude agent

---

Henry approved phases 2 to 4 on September 20, 2026. Build this phase directly. If you need to go beyond this scope, comment with a proposal in three sections (Functionality, Technical code changes, Architecture) and wait for approval.

Read `CLAUDE.md`, `docs/plan.md`, and `docs/deploy.md` first. Phases 1 and 2 are merged.

## Build

- **Calling Claude, `job/agent/`:**
  - Run Claude Code headless from the daily job, authenticated with `CLAUDE_CODE_OAUTH_TOKEN`. Switching to `ANTHROPIC_API_KEY` must be a single setting.
  - Every call uses a fixed JSON output format validated before use.
  - Only the tools the step needs: web search and web fetch for discovery and the scout. No shell or file-editing tools.
  - Model per step comes from `budget_settings`.
- **Discovery (Sonnet by default):** web searches for companies without a working feed, starting with the watch list and following the day's search plan.
- **Classification (Haiku by default):**
  - Checks unverified and new results: matches an active title or alias, MBA-level, Summer 2027, not an excluded word, and location.
  - Store `matched_title`, plus `title_en` for non-English postings.
- **Unclear pages (Haiku):** reads page text only when the phase 2 checkers couldn't decide.
- **Source scout (Sonnet):**
  - Finds careers pages, feeds, and job boards for companies with no working source first.
  - New sources start as `trial` and become `active` after a real open posting. Sources that keep finding nothing are `demoted`.
  - Retiring a source goes to Proposals.
- **Daily improvement (Sonnet):**
  - Writes `search_plans` for tomorrow. Small changes (query wording, source order) apply automatically.
  - Company and source changes go to `proposals`.
  - Title suggestions become a pull request editing `config/titles.json`. Give the workflow `pull-requests: write` and `contents: write`.
- **Usage and budgets:**
  - Record every Claude call in `usage`: step, model, tokens including cached, and web searches.
  - Enforce the budget settings and defaults in `docs/plan.md`: daily, weekly, and monthly limits, the share per step, searches per run and per company, and the order for cutting steps. Classification and pruning always run.
- **Alerts:**
  - Triggers: 80% and 100% of budget, a spike above twice the 7-day average, a run stopped by a Claude limit, and a failed run.
  - One alert per type per day, saved to `alerts`.
- **Daily summary:**
  - After every run, including failed ones, POST to `CLAUDE_ROUTINE_URL` with `Authorization: Bearer $CLAUDE_ROUTINE_TOKEN`, `anthropic-version: 2023-06-01`, and `anthropic-beta: experimental-cc-routine-2026-04-01`.
  - The body is `{"text": "<JSON summary>"}`. The summary holds date, run status and reason, today's tokens and searches, budgets, week-to-date tokens, usage by step, the forecast, and alerts.
  - Never include postings, company names, or anything personal.
  - Keep this call in one module, since the routine API is in research preview.
- **Dashboard:**
  - **Analytics tab:** coverage per company, source quality, first to find, postings per title, verification methods and unconfirmed count, pruned postings with reasons, and usage against budgets with alert history.
  - **Budgets panel:** every setting, the forecast, and change history, validated so step shares add up to 100%.
  - **Proposals tab:** approve and reject.
- **Tests:**
  - Budget enforcement, the order for cutting steps, and the forecast.
  - Each alert rule and the one-per-day limit.
  - The summary payload's shape, plus a test proving it carries no posting fields.
  - Validation of Claude's output format.
  - All Claude calls mocked.

## Done when

- Starting `daily-search.yml` by hand runs every Claude step within budget, records usage, and sends the daily summary to the routine.
- CI passes.
- The pull request description lists what Henry must do after merging: redeploy and set budgets.

@claude please build phase 3 as described above and open a pull request.
