-- Internship Watch: BR-001 (docs/requirements/BR-001-feed-coverage.md).
--
-- 1. companies.feed_url: the address a feed is read from, kept apart from
--    careers_url. The Workday reader builds its request from a site link
--    (https://<tenant>.wdN.myworkdayjobs.com/<site>), and until now that link
--    lived in careers_url, which the scout and careers-page repairs are free
--    to replace with a human-facing page. 18 Workday feeds broke that way.
--    Workday site links already on file are copied across; a marketing page
--    or a bare host is not a feed address and is left out.
-- 2. companies.feed_failures: feed reads that failed in a row. The job counts
--    it and raises an alert at a threshold, so a broken feed is never silent.
--    Starts at 0 for everyone: the history before this column is not known.
-- 3. China-based companies come off the ranked list (Henry's exclusion rule).
--    A pinned company is Henry's own choice and is left alone. The quarterly
--    ranking refresh no longer adds them back (job/ranking.py).
--
--   npx wrangler d1 migrations apply internship-watch-db --local   (or --remote)
--
-- To undo: ALTER TABLE companies DROP COLUMN feed_url; ALTER TABLE companies
-- DROP COLUMN feed_failures; and, for the companies taken off the list,
-- UPDATE companies SET status = 'ranked' WHERE hq_country = 'CN' AND status =
-- 'removed' (this also brings back any China-based company removed by hand).

ALTER TABLE companies ADD COLUMN feed_url TEXT;
ALTER TABLE companies ADD COLUMN feed_failures INTEGER NOT NULL DEFAULT 0;

UPDATE companies
   SET feed_url = careers_url
 WHERE feed_type = 'workday'
   AND careers_url LIKE 'https://%.myworkdayjobs.com/_%';

UPDATE companies
   SET status = 'removed'
 WHERE hq_country = 'CN'
   AND status = 'ranked';
