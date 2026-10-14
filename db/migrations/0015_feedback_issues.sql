-- Issue #96: visitor feedback creates a GitHub issue.
--   budget_settings.feedback_issues_per_day: caps issues created from feedback
--     per Central day. Past the cap, feedback is still saved and the daily run
--     retries it the next day.
--   feedback.issue_created_at: when the issue was created, so the cap can
--     count today's issues.
--
--   npx wrangler d1 migrations apply internship-watch-db --local   (or --remote)

ALTER TABLE budget_settings ADD COLUMN feedback_issues_per_day INTEGER NOT NULL DEFAULT 20;
ALTER TABLE feedback ADD COLUMN issue_created_at TEXT;
