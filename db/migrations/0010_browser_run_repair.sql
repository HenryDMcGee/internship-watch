-- Internship Watch: careers page repair and Browser Run usage in Budgets
-- (phase 6 part 2, issue #55).
--
-- browser_run_usage: one row per daily run per method ('browser_run' or
-- 'backup'), so the Budgets panel and Analytics can show today's/this
-- month's minutes, a 7-day series, and the last 10 runs without
-- reconstructing history from job logs. `changed`/`unchanged` are here too
-- (beyond the issue's own column list) so Analytics's "careers pages
-- loaded, unchanged, changed, and failed per run, split by Browser Run and
-- backup" doesn't need a second table -- one row per run per method already
-- has everything else that split needs.
--
--   npx wrangler d1 migrations apply internship-watch-db --local   (or --remote)

CREATE TABLE browser_run_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER REFERENCES runs (id),
  at TEXT NOT NULL,
  method TEXT NOT NULL CHECK (method IN ('browser_run', 'backup')),
  pages INTEGER NOT NULL DEFAULT 0,
  unchanged INTEGER NOT NULL DEFAULT 0,
  changed INTEGER NOT NULL DEFAULT 0,
  browser_ms INTEGER,
  measured_by TEXT CHECK (measured_by IN ('cloudflare_header', 'elapsed')),
  failures INTEGER NOT NULL DEFAULT 0,
  backup_reason TEXT
);

CREATE INDEX idx_browser_run_usage_at ON browser_run_usage (at);
CREATE INDEX idx_browser_run_usage_run_id ON browser_run_usage (run_id);

-- "careers page not working" flag: set once a company's careers page has
-- failed 3 checks in a row (job/run.py), cleared only when Henry approves a
-- replacement page from Proposals (never by the page succeeding again on
-- its own -- see api/src/main.py's careers_repair proposal applier).
ALTER TABLE companies ADD COLUMN careers_page_flagged INTEGER NOT NULL DEFAULT 0 CHECK (careers_page_flagged IN (0, 1));

-- Replacement URLs Henry rejected for this company, so the scout never
-- proposes the same one again (JSON array of strings).
ALTER TABLE companies ADD COLUMN careers_url_rejected TEXT NOT NULL DEFAULT '[]';
