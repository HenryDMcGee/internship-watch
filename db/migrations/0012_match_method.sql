-- Internship Watch: record how each posting matched a title (issue #68).
--
-- match_method: how the posting's matched_title was found. One of
--   exact            the title appeared in the posting title
--   alias            one of the title's aliases appeared in the posting title
--   fuzzy:<score>    word-by-word fuzzy match, for example fuzzy:93
--   classification   Claude classified it (fuzzy score 80 to 89, or a title the
--                    rules can't read)
-- NULL on postings saved before this change.
--
--   npx wrangler d1 migrations apply internship-watch-db --local   (or --remote)
--
-- To undo: ALTER TABLE postings DROP COLUMN match_method;

ALTER TABLE postings ADD COLUMN match_method TEXT;
