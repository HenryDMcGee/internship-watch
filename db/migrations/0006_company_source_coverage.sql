-- Internship Watch: track when the source scout last found something for a
-- company, so a repeat run doesn't keep spending search budget re-finding
-- it (issue #43: run 26 returned 64 sources, 58 already known).
--
--   npx wrangler d1 migrations apply internship-watch-db --local   (or --remote)

ALTER TABLE companies ADD COLUMN last_source_found_at TEXT;
