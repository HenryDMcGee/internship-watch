-- Tracks full daily searches started from the dashboard's "Search now"
-- button (issue #35), the same way `check_requests` tracks Check now.
-- `job/run.py` claims the newest pending row for a `kind = 'daily'` run so
-- the dashboard can poll a specific request without daily-search.yml
-- needing a workflow_dispatch input (it already has none).

CREATE TABLE search_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  requested_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', -- pending, running, completed, failed
  run_id INTEGER REFERENCES runs (id),
  result TEXT -- JSON
);
