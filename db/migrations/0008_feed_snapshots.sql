-- Internship Watch: remember each company's last feed content so an
-- unchanged feed can be skipped instead of re-processed every day
-- (phase 5 part 2, issue #51).
--
--   npx wrangler d1 migrations apply internship-watch-db --local   (or --remote)

CREATE TABLE feed_snapshots (
  company_id INTEGER PRIMARY KEY REFERENCES companies (id),
  content_hash TEXT NOT NULL,
  last_changed TEXT NOT NULL,
  last_checked TEXT NOT NULL
);
