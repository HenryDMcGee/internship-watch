-- Internship Watch: track when the source scout last tried a company.
--
-- All 100 seeded companies currently have no source, and the scout's
-- search budget only covers a batch of them per run. Without remembering
-- who it already tried, every run would hand Claude the same leading
-- companies and the rest would never get a turn. This column lets the
-- scout pick never-tried companies first, then the least-recently-tried
-- ones, so it works through the full list in priority order across runs.
--
--   npx wrangler d1 migrations apply internship-watch-db --local   (or --remote)

ALTER TABLE companies ADD COLUMN last_scouted_at TEXT;
