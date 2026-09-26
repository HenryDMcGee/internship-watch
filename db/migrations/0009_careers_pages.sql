-- Internship Watch: read companies' careers pages through Cloudflare Browser
-- Run, with a Playwright backup, for companies with a careers_url on file
-- but no recognized job feed (phase 6 part 1, issue #53). One row per
-- company remembers the last page content (to skip an unchanged page with
-- no Claude call) and the run's outcome for it.
--
--   npx wrangler d1 migrations apply internship-watch-db --local   (or --remote)

CREATE TABLE careers_page_snapshots (
  company_id INTEGER PRIMARY KEY REFERENCES companies (id),
  content_hash TEXT,
  last_changed TEXT,
  last_checked TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('changed', 'unchanged', 'failed')),
  method TEXT CHECK (method IN ('browser_run', 'backup')),
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);

ALTER TABLE budget_settings ADD COLUMN careers_pages_per_run INTEGER NOT NULL DEFAULT 60;
ALTER TABLE budget_settings ADD COLUMN browser_run_plan TEXT NOT NULL DEFAULT 'free' CHECK (browser_run_plan IN ('free', 'paid'));
ALTER TABLE budget_settings ADD COLUMN browser_run_daily_minutes REAL NOT NULL DEFAULT 9;
ALTER TABLE budget_settings ADD COLUMN careers_backup_pages_per_run INTEGER NOT NULL DEFAULT 40;
ALTER TABLE budget_settings ADD COLUMN careers_backup_enabled INTEGER NOT NULL DEFAULT 1 CHECK (careers_backup_enabled IN (0, 1));
