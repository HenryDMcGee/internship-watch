# Internship Watch

A private tool that finds Summer 2027 MBA product-type internships every morning. Owner: Henry (the repository owner). Read `docs/plan.md` before any work; it is the approved plan and the source of truth for scope.

## How work gets approved

* Henry's rule: before changing the dashboard, code, files, or data, post a proposal and wait for his approval.
* A proposal has exactly three sections: **Functionality** (what he sees and can do), **Technical code changes** (files, modules, endpoints, schema), and **Architecture** (data flow, services, permissions, cost). If a section has no changes, say so.
* An issue that says its proposal is already approved can be built directly. Anything outside that approved scope needs a new proposal as a comment first. Stop and wait for his reply.
* Never merge. Open a pull request and let Henry review it.

## Stack

* Python 3.12 for the daily job, scripts, and tests. The API uses the Python version Cloudflare's Python Workers runtime requires (3.13 at the time of writing).
* Daily job: `job/`, runs on GitHub Actions at 08:00 America/Chicago. The workflow is scheduled at 13:00 and 14:00 UTC, and the first step exits unless it is 08:00 in Chicago.
* App: one Cloudflare Python Worker in `api/`, configured by `api/wrangler.toml` (keep its settings: `workers\_dev = false`, `preview\_urls = false`, the route, the `ASSETS` and `DB` bindings). FastAPI serves `/api/\*` and passes every other path to the `ASSETS` binding. Use `pywrangler`: `uv run pywrangler dev` and `uv run pywrangler deploy`. Python Workers only support packages that run on Pyodide. If something needed won't run there, propose a small TypeScript Worker instead of working around it.
* Database: Cloudflare D1 (SQLite). Migrations in `db/migrations/`, numbered `0001\_\*.sql`. Local development uses `wrangler` with a local D1.
* Dashboard: static HTML, CSS, and JavaScript in `frontend/`, served by the same Worker as static assets. It calls the API at `/api/\*` on the same domain. Keep the look of `docs/reference/current-dashboard.html`.
* Names: repository and folders `internship-watch`; Cloudflare Worker `internship-watch` (dashboard and API), D1 database `internship-watch-db`, R2 bucket `internship-watch-backups`. Display name "Internship Watch". Dashboard address `internship-watch.mcgeehenry.com`. Never change anything on the root `mcgeehenry.com`, which hosts Henry's website.
* Titles: `config/titles.json`, validated by `config/titles.schema.json` and `scripts/validate\_titles.py`. Titles change only through pull requests.

## Rules

* Deployment steps live in `docs/deploy.md`. Keep it accurate when changing anything about deploys.
* Never commit secrets, tokens, or keys. Local values go in `.dev.vars` or `.env`, which are gitignored. In code, read secrets from environment variables or Worker bindings.
* Don't scrape LinkedIn. Prefer company job feeds (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Amazon.jobs) and public careers pages. Respect sites that block automated browsers; never try to get around a block or a CAPTCHA.
* Postings are archived, never deleted, unless Henry deletes them.
* Anything Claude-powered must record its usage (tokens, web searches, model, step) and respect the budget settings.
* Keep pull requests focused on one phase or one change. Every pull request description includes: what changed, how to run or test it locally, anything deferred, and any decision Henry needs to make.
* Write tests with pytest for new logic. CI must pass.
* Dashboard text is plain and short. Use "Summer 2027", US and Japan locations by default, and Central time for display.



<!-- Add this to CLAUDE.md, under the "Rules" section. -->

## Testing before a pull request

* Install dependencies and run the suite before opening a pull request:

```
  python -m pip install -r requirements-dev.txt
  python -m pytest -q
  ```

* Fix failures and run it again until the suite is green.
* Say in the pull request description whether the suite passed, and paste the summary line (for example, `158 passed in 11.02s`).
* If the tests can't run, say so plainly at the top of the description rather than at the end, and list which checks you could not make.
* When a test fails, fix the code the test is checking. Only change a test when the test itself is wrong, and say why in the description.

## Things that have broken before

* The deployed Worker only contains `api/src` (see `api/wrangler.toml`'s `main = "src/main.py"`). Never read repo files by path from the API (e.g. `config/titles.json`) — they aren't there in production, only on disk locally and in CI. Read them through `github_client` instead.



