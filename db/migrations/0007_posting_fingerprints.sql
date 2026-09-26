-- Internship Watch: fingerprint every posting and every rejected result so
-- a repost under a new URL is recognized instead of saved as a second row
-- (issue #49: an NVIDIA role was found again at a different URL and saved
-- twice), and so a result already rejected once isn't re-classified with
-- Claude every day.
--
--   npx wrangler d1 migrations apply internship-watch-db --local   (or --remote)

CREATE TABLE posting_fingerprints (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  clean_url TEXT,
  title_key TEXT NOT NULL,
  posting_id INTEGER REFERENCES postings (id),
  rejection_reason TEXT,
  settings_version TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  last_source TEXT
);

CREATE INDEX idx_posting_fingerprints_job_id ON posting_fingerprints (job_id);
CREATE INDEX idx_posting_fingerprints_clean_url ON posting_fingerprints (clean_url);
CREATE INDEX idx_posting_fingerprints_title_key ON posting_fingerprints (title_key);
