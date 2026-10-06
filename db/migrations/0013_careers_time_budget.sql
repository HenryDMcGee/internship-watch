-- Internship Watch: keep runs short (issue #70).
--   careers_minutes_per_run: how long the careers-pages step may run before it
--     stops and leaves the rest for the next run.
--   empty_runs: runs in a row where a careers page had no listings (skipped as
--     a landing page, or 0 extracted); 2 flags the page for the scout.
--
--   npx wrangler d1 migrations apply internship-watch-db --local   (or --remote)

ALTER TABLE budget_settings ADD COLUMN careers_minutes_per_run REAL NOT NULL DEFAULT 10;
ALTER TABLE careers_page_snapshots ADD COLUMN empty_runs INTEGER NOT NULL DEFAULT 0;
