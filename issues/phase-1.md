Phase 1: Local version

---

Henry approved this proposal on September 19, 2026, so you can build it directly. If you need to go beyond this scope, comment with a proposal in three sections (Functionality, Technical code changes, Architecture) and wait for approval.

Read `CLAUDE.md` and `docs/plan.md` first.

## Build

- **Project setup:** use the provided `api/wrangler.toml` as is, apart from code it points to. Add `api/pyproject.toml` with FastAPI and the dev dependencies `workers-py` and `workers-runtime-sdk`, the entry point `api/src/main.py`, and a root `requirements-dev.txt` for tests. Local development runs with `uv run pywrangler dev` from `api/`. Add a README section for local development, and keep `docs/deploy.md` accurate.
- **Database:** `db/migrations/0001_init.sql` with every table in the plan's data model, so later phases don't need to reshape data.
- **Seed:** `scripts/seed.py` loads everything in `seed/` into the local D1, or into the real database with `--remote` (see `docs/deploy.md` step 5), mapping the old field names to the new schema. Use `seed/settings.json` for the Locations panel, which covers US and JP with remote off.
- **API:** FastAPI in the Python Worker under `api/`, with a catch-all that passes non-`/api` paths to the `ASSETS` binding so the dashboard loads from `frontend/`. Verify the Cloudflare Access login token on every `/api` request, using `ACCESS_TEAM_DOMAIN` and `ACCESS_AUD`. Endpoints for:
  - postings: list by tab, mark applied, delete, restore
  - sources: list, add, remove
  - companies: list, pin, unpin, add one with an optional careers link, and paste a list with a preview step and a save step
  - settings: read and update locations
  - titles: read only, from the copy of `config/titles.json` bundled at build
- **Dashboard:** under `frontend/`, ported from `docs/reference/current-dashboard.html`. Keep its look and swap its database calls for `fetch` to `/api/*`.
  - Tabs: New today, Earlier, Sources, Archived, and placeholders for Analytics and Proposals.
  - Closed postings stay hidden behind the show-closed toggle.
  - New views: Titles grouped by category (read only), the Companies list with pins, adding and pasting companies, and the Locations panel.
- **Ranking:** pick a published ranking of the 100 largest tech companies worldwide by market value that includes internet and platform companies like Amazon, Alphabet, and Meta. Its terms must allow automated access.
  - Put the choice and the reason in the pull request description.
  - If no source qualifies, add a CSV import at `seed/ranking.csv` and document the format.
  - Either way, mark the imported companies `ranked`.
  - List the companies from `seed/companies.json` that aren't in the ranking for a one-time pin review on the Companies tab.
- **Tests:** pytest covering the seed mapping, API routes (an in-memory SQLite stand-in is fine), parsing pasted company lists and detecting duplicates, and detecting feed types from careers links.

## Done when

- One documented command sequence runs the dashboard and API locally with the seeded data: 9 postings with 3 closed and hidden, 30 sources, and 2 search log entries.
- CI passes.
- The pull request description covers what changed, how to run it locally, the ranking decision, and anything deferred.

@claude please build phase 1 as described above and open a pull request.
