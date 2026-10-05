-- Internship Watch: get the daily search finding new postings again (issue #63).
--
-- last_discovery_searched_at: when discovery last searched this company by name,
-- so each run starts with the company searched longest ago and every company gets
-- a turn before any gets a second one.
--
-- careers_url_approved: 1 once Henry approved the careers_url (added it himself,
-- approved a repair proposal, or approved a Claude company proposal). The scout
-- never overwrites an approved careers_url. Every careers_url already on file is
-- treated as approved, since each came from Henry, the seed list, or a repair he
-- approved.
--
-- careers_note: "careers page, no feed" for a careers_url the scout found that is
-- not on a platform with a feed we can read.
--
--   npx wrangler d1 migrations apply internship-watch-db --local   (or --remote)
--
-- To undo: ALTER TABLE companies DROP COLUMN each of the three columns below.

ALTER TABLE companies ADD COLUMN last_discovery_searched_at TEXT;
ALTER TABLE companies ADD COLUMN careers_url_approved INTEGER NOT NULL DEFAULT 0 CHECK (careers_url_approved IN (0, 1));
ALTER TABLE companies ADD COLUMN careers_note TEXT;

UPDATE companies SET careers_url_approved = 1 WHERE careers_url IS NOT NULL AND TRIM(careers_url) != '';
