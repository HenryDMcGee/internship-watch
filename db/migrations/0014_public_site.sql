-- Phase 7: public job board and feedback.
-- The on/off switch for the public site is the `public_site_enabled` key in
-- `settings` (JSON true or false; missing means off), so it needs no schema.

ALTER TABLE postings ADD COLUMN public_hidden INTEGER NOT NULL DEFAULT 0 CHECK (public_hidden IN (0, 1));

CREATE TABLE feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL CHECK (source IN ('public', 'owner')),
  kind TEXT NOT NULL CHECK (kind IN ('bug', 'dead_link', 'closed', 'wrong_job', 'suggestion', 'other')),
  posting_id INTEGER REFERENCES postings (id),
  message TEXT NOT NULL,
  page_url TEXT,
  visitor_hash TEXT, -- SHA-256 of FEEDBACK_SALT + the visitor's IP; never the address itself
  status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'read', 'issue_opened', 'dismissed')),
  issue_url TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT
);

CREATE INDEX idx_feedback_created_at ON feedback (created_at);
CREATE INDEX idx_feedback_posting_id ON feedback (posting_id);
