-- Internship Watch: initial schema.
-- Covers every table in docs/plan.md's data model so later phases don't
-- need to reshape data. Applied with:
--   npx wrangler d1 migrations apply internship-watch-db --local   (or --remote)

CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL -- JSON
);

CREATE TABLE budget_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  billing_mode TEXT NOT NULL DEFAULT 'subscription' CHECK (billing_mode IN ('subscription', 'api_key')),
  daily_token_budget INTEGER NOT NULL DEFAULT 4000000,
  daily_search_budget INTEGER NOT NULL DEFAULT 35,
  weekly_token_budget INTEGER NOT NULL DEFAULT 25000000,
  daily_usd_budget REAL NOT NULL DEFAULT 1.50,
  monthly_usd_budget REAL NOT NULL DEFAULT 30.00,
  share_discovery REAL NOT NULL DEFAULT 0.60,
  share_scout REAL NOT NULL DEFAULT 0.20,
  share_classification REAL NOT NULL DEFAULT 0.15,
  share_improvement REAL NOT NULL DEFAULT 0.05,
  model_discovery TEXT NOT NULL DEFAULT 'sonnet',
  model_scout TEXT NOT NULL DEFAULT 'sonnet',
  model_classification TEXT NOT NULL DEFAULT 'haiku',
  model_improvement TEXT NOT NULL DEFAULT 'sonnet',
  search_limit_per_run INTEGER NOT NULL DEFAULT 35,
  search_limit_per_company INTEGER NOT NULL DEFAULT 5,
  browser_checks_per_run INTEGER NOT NULL DEFAULT 50,
  updated_at TEXT
);

INSERT INTO budget_settings (id) VALUES (1);

CREATE TABLE budget_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  changed_at TEXT NOT NULL,
  field TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT
);

CREATE TABLE companies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  aliases TEXT NOT NULL DEFAULT '[]', -- JSON array
  -- 'review' is a phase 1 addition to the plan's three statuses: companies
  -- from the old hand-picked list that the chosen ranking doesn't include,
  -- waiting on Henry's one-time pin decision.
  status TEXT NOT NULL DEFAULT 'pinned' CHECK (status IN ('ranked', 'pinned', 'removed', 'review')),
  rank INTEGER,
  market_cap_usd REAL,
  ranking_date TEXT,
  ranking_source TEXT,
  hq_country TEXT,
  careers_url TEXT,
  feed_type TEXT,
  feed_id TEXT,
  added_by TEXT,
  added_at TEXT,
  check_cadence TEXT,
  last_result_at TEXT
);

CREATE INDEX idx_companies_status ON companies (status);

CREATE TABLE ranking_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_date TEXT NOT NULL,
  source TEXT NOT NULL,
  company_name TEXT NOT NULL,
  rank INTEGER,
  market_cap_usd REAL
);

CREATE INDEX idx_ranking_snapshots_date ON ranking_snapshots (snapshot_date);

CREATE TABLE sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  url TEXT,
  kind TEXT NOT NULL DEFAULT 'Job boards',
  covers TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('trial', 'active', 'demoted')),
  discovered_by TEXT,
  discovered_at TEXT,
  found_count INTEGER NOT NULL DEFAULT 0,
  dead_link_rate REAL NOT NULL DEFAULT 0,
  last_checked TEXT,
  last_success_at TEXT,
  notes TEXT
);

CREATE INDEX idx_sources_kind ON sources (kind);

CREATE TABLE source_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES sources (id),
  run_id INTEGER,
  found INTEGER NOT NULL DEFAULT 0,
  dead INTEGER NOT NULL DEFAULT 0,
  error TEXT
);

CREATE TABLE postings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER REFERENCES companies (id),
  company_name TEXT NOT NULL,
  title TEXT NOT NULL,
  title_en TEXT,
  matched_title TEXT,
  url TEXT,
  mirror_url TEXT,
  source_id INTEGER REFERENCES sources (id),
  source_name TEXT,
  locations TEXT NOT NULL DEFAULT '[]', -- JSON array
  location_match TEXT NOT NULL DEFAULT 'unclear' CHECK (location_match IN ('match', 'outside', 'unclear')),
  posted_date TEXT,
  first_seen TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'unverified' CHECK (status IN ('open', 'closing', 'closed', 'unverified')),
  status_note TEXT,
  verified_via TEXT,
  verified_at TEXT,
  user_status TEXT,
  user_status_at TEXT,
  applied INTEGER NOT NULL DEFAULT 0,
  archived_at TEXT,
  archive_reason TEXT,
  note TEXT
);

CREATE INDEX idx_postings_first_seen ON postings (first_seen);
CREATE INDEX idx_postings_archived_at ON postings (archived_at);
CREATE INDEX idx_postings_company_id ON postings (company_id);

CREATE TABLE posting_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  posting_id INTEGER NOT NULL REFERENCES postings (id),
  type TEXT NOT NULL,
  detail TEXT, -- JSON
  at TEXT NOT NULL
);

CREATE TABLE runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL DEFAULT 'daily' CHECK (kind IN ('daily', 'check_now')),
  started_at TEXT,
  finished_at TEXT,
  status TEXT,
  new_count INTEGER NOT NULL DEFAULT 0,
  summary TEXT,
  next_notes TEXT
);

CREATE TABLE run_stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs (id),
  source_id INTEGER REFERENCES sources (id),
  query TEXT,
  found INTEGER NOT NULL DEFAULT 0,
  useful INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE search_plans (
  plan_date TEXT PRIMARY KEY,
  plan TEXT NOT NULL -- JSON
);

CREATE TABLE proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL, -- JSON
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
  created_at TEXT NOT NULL,
  decided_at TEXT
);

CREATE TABLE check_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_ids TEXT NOT NULL, -- JSON array
  requested_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  result TEXT -- JSON
);

CREATE TABLE usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER,
  step TEXT NOT NULL,
  model TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  web_searches INTEGER NOT NULL DEFAULT 0,
  est_cost_usd REAL NOT NULL DEFAULT 0,
  at TEXT NOT NULL
);

CREATE TABLE alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  severity TEXT,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL,
  dismissed_at TEXT
);
