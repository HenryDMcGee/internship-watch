-- Cloudflare daily trigger, phase 1: every run records which path started it.
--   runs.source: 'cloudflare' (the Worker's cron trigger dispatched it),
--     'manual' (Run workflow clicked in GitHub), 'schedule' (the GitHub
--     Actions cron), or NULL for runs from before this column existed.
--     Nullable on purpose; nothing is backfilled.
--
--   npx wrangler d1 migrations apply internship-watch-db --local   (or --remote)

ALTER TABLE runs ADD COLUMN source TEXT;
