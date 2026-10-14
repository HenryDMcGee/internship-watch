Phase 7: Public job board and feedback

---

Henry approved Phase 7 on October 14, 2026, with these decisions: the public address is `jobs.mcgeehenry.com`; only `open` and `closing` postings show; the public page keeps the New today and Earlier tabs; the footer carries no name or link, only the one-line description of the site; the feedback form uses a honeypot and daily limits, not Turnstile; visitors cannot leave an email; the Worker's GitHub token gets Issues: write; the private Feedback button sits in the header next to Search now. Build this phase directly. If you need to go beyond this scope, comment with a proposal in three sections (Functionality, Technical code changes, Architecture) and wait for approval.

Read `CLAUDE.md`, `docs/plan.md`, and `docs/deploy.md` first. Phases 1 to 6 are merged. Phase 6 part 2 (`browser_run_usage`, careers page repair) must be merged. If it isn't, stop and comment instead of building.

## Problem

The dashboard is private behind Cloudflare Access, so there is no way to share what Internship Watch finds with classmates or recruiters, and no way for anyone, including Henry, to report a dead link or a bug without opening GitHub by hand.

## Functionality

Public job board at `https://jobs.mcgeehenry.com`, no login:

- Shows postings that are not archived, have `status` `open` or `closing`, `location_match = 'match'`, and are not hidden from the public. One card per posting: company, title (`title_en` when set, otherwise `title`), locations, posted date, first seen, and a link to `url`, the company's own posting page.
- Tabs New today and Earlier, grouped the same way as the private dashboard, Central time. A search box filtering by company, title, or location. A "Last updated" line from the most recent finished daily run.
- Header: "Summer 2027 MBA product internships, found daily by Internship Watch." Footer: the same line, no name, no link.
- Read only. Nothing on the page changes a posting.
- When the public site is switched off, the page shows "Not available right now." and nothing else.
- Never shown publicly: `applied`, `note`, `status_note`, `user_status`, `user_status_at`, `verified_via`, `verified_at`, `mirror_url`, `source_id`, `source_name`, `matched_title`, `archive_reason`, and every panel on the private dashboard (Companies, Sources, Titles, Locations, Analytics, Budgets, Proposals, Check now, Search now).

Feedback on the public board:

- A "Feedback" button in the footer opens a small form: type (Dead link, Job is closed, Wrong job, Suggestion, Other) and a message up to 2,000 characters. No email field. Each card has a "Report" link that opens the same form with that posting selected.
- Submitting shows "Thanks, sent." Visitors never see other feedback.
- Abuse protection: a hidden honeypot input. A filled honeypot is discarded silently with a 204. Limits of 10 submissions a day per visitor and 200 a day in total, Central time. Over a limit, the API returns 429 and the form says "Try again tomorrow."

New controls on the private dashboard:

- In the Locations panel (the existing settings panel), a "Public site" switch, on or off, default off. The pull request description must say it starts off so Henry turns it on after the DNS record is in place.
- On each posting card, a "Hide from public" toggle. Hidden postings show a small "Hidden from public" tag on Henry's card. Because the public API is cached for 5 minutes, say in the toggle's confirmation text that the public page updates within 5 minutes.
- A "Feedback" button in the header next to Search now, opening the same form plus a Bug type. Henry's submissions are stored with `source = 'owner'`.
- A Feedback view under the More menu, newest first: type, message, posting (linked to the card) if any, source (Visitor or You), when. Each row has Mark read, Dismiss, and Open as GitHub issue. The last creates an issue in this repository titled `Feedback: <type>: <first 60 characters>` with the message, the posting link if any, the source, and the date in the body, labeled `feedback`, and stores the issue link on the row.
- A posting a visitor reported as Dead link or Job is closed shows a "Reported by a visitor" tag with the count on Henry's card. It never changes the posting's status. Mark open and Mark closed stay the only overrides.
- The daily summary gains one line when there is new feedback since the last run, counts only, for example "3 new feedback items, 1 about a dead link." Never include the message text in the summary.

Everything else on the private dashboard is unchanged.

## Technical code changes

- `db/migrations/0014_public_site.sql`:
  - `ALTER TABLE postings ADD COLUMN public_hidden INTEGER NOT NULL DEFAULT 0 CHECK (public_hidden IN (0, 1))`.
  - New `feedback` table: `id`, `source` (`public` or `owner`), `kind` (`bug`, `dead_link`, `closed`, `wrong_job`, `suggestion`, `other`), `posting_id` nullable referencing `postings`, `message`, `page_url`, `visitor_hash` nullable, `status` (`new`, `read`, `issue_opened`, `dismissed`, default `new`), `issue_url` nullable, `created_at`, `decided_at` nullable. Indexes on `created_at` and `posting_id`.
  - The on/off switch is the `public_site_enabled` key in the existing `settings` table, JSON `true` or `false`, missing means off. No schema change for it.
- `api/src/main.py`, public router:
  - A second `APIRouter(prefix="/api/public")` with no `require_access` dependency. Keep the private router exactly as it is.
  - `GET /api/public/postings`: when `public_site_enabled` is not true, return `{"enabled": false, "postings": [], "last_run": null}`. Otherwise select an explicit column list, never `SELECT *`: `id, company_name, title, title_en, locations, posted_date, first_seen, url`, filtered by `archived_at IS NULL AND status IN ('open', 'closing') AND location_match = 'match' AND public_hidden = 0`, ordered by `first_seen DESC, id DESC`. Also return `last_run` as the `finished_at` of the latest finished daily run. Set `Cache-Control: public, max-age=300` on every response from this endpoint.
  - `POST /api/public/feedback`: body `kind`, `message`, `posting_id` optional, `page_url` optional, and the honeypot field `website`. Reject with 400 when `kind` is not in the list, the message is empty or over 2,000 characters, or `posting_id` is not a posting the public endpoint would show right now. Return 204 without storing when `website` is non-empty. Hash the visitor with SHA-256 of `FEEDBACK_SALT` plus the `CF-Connecting-IP` header; never store the raw address. Count today's rows (Central) for that hash and in total before inserting, return 429 over 10 or 200. Store the message as plain text, with no HTML interpretation anywhere it is rendered. Return 503 when `FEEDBACK_SALT` is missing, so the endpoint never falls back to a weak hash.
- `api/src/main.py`, private router:
  - `GET /api/settings/public-site` returning `{"enabled": bool}` and `PUT /api/settings/public-site` with the same body, stored in `settings` the same way `/api/settings/locations` stores its value.
  - `POST /api/postings/{id}/public-hidden` with `{"hidden": bool}`, returning the updated posting like the applied toggle does.
  - `GET /api/feedback` (newest first, with the posting's company and title joined when `posting_id` is set), `POST /api/feedback/{id}/status` with `{"status": "read" | "dismissed"}`, and `POST /api/feedback/{id}/issue`, which calls the GitHub client once, sets `status = 'issue_opened'` and `issue_url`, and returns the updated row. Return 503 when the GitHub client is not configured, matching the other GitHub-backed endpoints.
  - The postings list response gains `public_hidden` and `visitor_reports` (count of `feedback` rows with `kind IN ('dead_link', 'closed')` for that posting that are not dismissed).
- `api/src/main.py`, hostname routing:
  - Put the decision in a plain function, `route_for_host(hostname, path, public_hostname) -> "public" | "private" | "not_found"`, with a path rewrite helper, so pytest can cover it. `Default.fetch` calls it.
  - On the public hostname: `/` and `/index.html` are rewritten to `/public/index.html`; `/public/*` and `/style.css` go to `ASSETS`; `/api/public/*` goes to the app; everything else returns 404 with a plain-text body. Every private `/api/*` path returns 404 on the public hostname before FastAPI sees it.
  - On the private hostname, behavior is exactly as today.
- `api/src/github_client.py`: one new method, `create_issue(title, body_text, labels) -> dict`, `POST /repos/{repo}/issues`.
- `api/wrangler.toml`: a second route `{ pattern = "jobs.mcgeehenry.com/*", zone_name = "mcgeehenry.com" }` and `PUBLIC_HOSTNAME = "jobs.mcgeehenry.com"` under `[vars]`. Keep `workers_dev = false`, `preview_urls = false`, and both bindings. `FEEDBACK_SALT` is a Worker secret, never in the file.
- `frontend/public/index.html` and `frontend/public/public.js`: the public page, reusing `/style.css` for the same look. No private script is loaded on it. The page calls only `/api/public/*`.
- `frontend/index.html` and `frontend/app.js`: the Public site switch in the Locations panel, the Hide from public toggle and tags on cards, the header Feedback button and form, the Feedback view under More with its three actions.
- `job/run.py`: count `feedback` rows created since the previous run's `started_at` and add the counts-only line to the daily summary payload.
- `docs/deploy.md`: the second route, the DNS record (proxied `AAAA jobs 100::` in the `mcgeehenry.com` zone, the same kind the dashboard uses), the Issues: write permission on the Worker's GitHub token, `npx wrangler secret put FEEDBACK_SALT` with a note to use a long random string, and turning the Public site switch on after DNS resolves. State that Cloudflare Access stays scoped to `internship-watch.mcgeehenry.com` only and must not be extended to the public name.
- `docs/plan.md`: add the public address to the Names table, the `public_hidden` column and `feedback` table to the data model, and phases 5, 6, and 7 to Build phases in the same short style as phases 1 to 4.

## Test cases

Use pytest. Mock the GitHub client and count its calls.

Public postings:
1. The public payload contains only `id`, `company_name`, `title`, `title_en`, `locations`, `posted_date`, `first_seen`, and `url`. Assert the key set exactly, so a future `SELECT *` fails the test.
2. A posting with `applied = 1`, a `note`, `status_note`, and `user_status` set appears in the public list with none of those values anywhere in the response body.
3. Archived, `closed`, `unverified`, `location_match = 'outside'`, `location_match = 'unclear'`, and `public_hidden = 1` postings are excluded. `open` and `closing` with `match` are included.
4. With `public_site_enabled` missing or false, the response is `enabled: false` with an empty list. With true, postings are returned.
5. Every public postings response carries `Cache-Control: public, max-age=300`.
6. `last_run` is the latest finished daily run's `finished_at`, and null when there are no finished runs.

Hostname routing:
7. On the public hostname, `/` rewrites to `/public/index.html`, `/public/public.js` and `/style.css` go to assets, `/api/public/postings` goes to the app, and `/api/postings`, `/api/budgets`, `/api/settings`, `/api/feedback`, and `/app.js` return 404.
8. On the private hostname, `/api/postings` goes to the app and `/` goes to assets, unchanged.
9. Hostname comparison is case-insensitive and ignores a port.

Private endpoints stay private:
10. With Access configured, `GET /api/feedback`, `PUT /api/settings/public-site`, and `POST /api/postings/{id}/public-hidden` return 401 without the Access header. `GET /api/public/postings` and `POST /api/public/feedback` succeed without it.

Public feedback:
11. A valid submission stores one row with `source = 'public'`, the kind, the message, the posting id, and a `visitor_hash` that is not the IP address.
12. A submission with the honeypot filled returns 204 and stores nothing.
13. The 11th submission from the same visitor in a day returns 429. The 201st in total returns 429. Counts reset on the next Central day.
14. Empty message, a message over 2,000 characters, an unknown kind, and a `posting_id` that the public list would not show each return 400.
15. A message containing `<script>` is stored and returned as the same literal text.
16. A missing `FEEDBACK_SALT` returns 503 and stores nothing.

Owner feedback and the Feedback view:
17. The owner's submission is stored with `source = 'owner'` and may use kind `bug`.
18. `GET /api/feedback` lists rows newest first with the joined company and title when `posting_id` is set.
19. Mark read and Dismiss update `status` and `decided_at`.
20. Open as GitHub issue calls `create_issue` exactly once with a title starting `Feedback:`, a body containing the message and the posting link, and the `feedback` label; the row gets `status = 'issue_opened'` and the returned `issue_url`. Calling it again on the same row does not create a second issue.
21. Without a GitHub client configured, Open as GitHub issue returns 503 and the row is unchanged.

Dashboard data:
22. The private postings list includes `public_hidden` and `visitor_reports`. A posting with two `dead_link` reports and one dismissed `closed` report shows `visitor_reports = 2`.
23. A visitor report never changes `status`, `user_status`, or `archived_at`.
24. Toggling Hide from public flips `public_hidden`, and the posting disappears from the public list on the next call.

Daily summary:
25. The summary payload includes the feedback line with correct counts for 3 new items of which 1 is `dead_link`, and no message text. With no new feedback, the line is absent.

## Architecture

- Still one Worker, one database, $0. The public address is a second route into the same Worker. No new service, no copied data, no sync job. The 5-minute cache keeps repeat visits off D1.
- Cloudflare Access is untouched and stays scoped to `internship-watch.mcgeehenry.com`. On the public hostname the entrypoint only lets `/api/public/*` and the public page through; every private endpoint also still requires the Access token, so two independent guards must both fail before anything private is reachable.
- The public feedback endpoint is the only write the public hostname can reach. It writes to one table, bounded by the daily caps, with no joins to anything private.
- One permission change: Issues: write on the Worker's existing GitHub token. One new secret: `FEEDBACK_SALT`. No other secrets, bindings, or permissions change.
- Visitor privacy: hashed IP only, no cookies, no email, no analytics.

## Notes

- The default branch here is master, not main.
- Every new column needs a migration.
- The Worker only ships `api/src`; never read repo files by path from the API.
- Run the test suite and report the result in the pull request description, with manual checks: the public page on desktop and phone with the site on and off, the Hide from public toggle, a visitor feedback submission and the 429 after the limit, Open as GitHub issue, and that `internship-watch.mcgeehenry.com` still requires login.
- The pull request description must list what Henry does after merging, in order: add the proxied `AAAA jobs 100::` DNS record, add Issues: write to the Worker's GitHub token, run `npx wrangler secret put FEEDBACK_SALT`, then turn the Public site switch on in the Locations panel.

@claude Henry approved this. Please build it and open a pull request.
