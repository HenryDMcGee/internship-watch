-- Phase 9: one billing model, API-key dollars (issues/phase-9.md).
--
-- The subscription/token billing mode is removed everywhere, so the stored
-- budget settings lose billing_mode, daily_token_budget and
-- weekly_token_budget. Every other value (the dollar budgets, the search
-- limits, the shares and models) is kept as it is. The removed values are
-- written to the budgets change history first, so nothing is lost silently.
--
-- public_feedback_daily_limit caps how many feedback submissions the public
-- job board accepts a day across all visitors (the /api/public/feedback
-- endpoint enforces it, alongside the honeypot and the per-visitor limit).
--
-- public_board_views counts loads of the public job board per Central day,
-- for the Budgets dialog's Public site status line.

ALTER TABLE budget_settings ADD COLUMN public_feedback_daily_limit INTEGER NOT NULL DEFAULT 20;

INSERT INTO budget_changes (changed_at, field, old_value, new_value)
  SELECT strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'), 'billing_mode', billing_mode,
         'removed: budgets are API-key dollars only (phase 9)'
  FROM budget_settings WHERE id = 1;
INSERT INTO budget_changes (changed_at, field, old_value, new_value)
  SELECT strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'), 'daily_token_budget', daily_token_budget,
         'removed: budgets are API-key dollars only (phase 9)'
  FROM budget_settings WHERE id = 1;
INSERT INTO budget_changes (changed_at, field, old_value, new_value)
  SELECT strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'), 'weekly_token_budget', weekly_token_budget,
         'removed: budgets are API-key dollars only (phase 9)'
  FROM budget_settings WHERE id = 1;

ALTER TABLE budget_settings DROP COLUMN billing_mode;
ALTER TABLE budget_settings DROP COLUMN daily_token_budget;
ALTER TABLE budget_settings DROP COLUMN weekly_token_budget;

CREATE TABLE public_board_views (
  day TEXT PRIMARY KEY,            -- Central date, YYYY-MM-DD
  views INTEGER NOT NULL DEFAULT 0
);
