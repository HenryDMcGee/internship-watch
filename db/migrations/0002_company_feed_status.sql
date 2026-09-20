-- Internship Watch: track per-company feed status and error text.
--
-- Every seeded company had no feed_type/feed_id, so the daily job's fetch
-- step reported all 100 as "feed errors" when really they had no known
-- source at all. This adds columns so the job can tell the two apart and
-- store what actually went wrong, per company, for the Sources or
-- Analytics view.
--
--   npx wrangler d1 migrations apply internship-watch-db --local   (or --remote)

ALTER TABLE companies ADD COLUMN last_feed_status TEXT
  CHECK (last_feed_status IN ('ok', 'error', 'no_source'));
ALTER TABLE companies ADD COLUMN last_feed_error TEXT;
