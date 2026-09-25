-- Lets Henry restart today's or this week's usage count from zero without
-- waiting for the window to roll over (issue #38), for example after
-- several test runs used up the budget. Usage rows are never deleted --
-- Analytics and cost history stay built from `usage`; a reset only changes
-- what `load_budget_status` (job) and the Budgets panel (API) count toward
-- the daily/weekly limit, from the later of the window's start or the most
-- recent reset within it.

CREATE TABLE budget_resets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT NOT NULL CHECK (scope IN ('daily', 'weekly')),
  reset_at TEXT NOT NULL,
  note TEXT
);

CREATE INDEX idx_budget_resets_scope_reset_at ON budget_resets (scope, reset_at);
