-- 0005: phase 4 — plans, tasks, project memory, usage ledger, event logs.

-- Plans and plan templates (kind = project | template; templates use project_id '').
CREATE TABLE plans (
  project_id TEXT NOT NULL,
  key        TEXT NOT NULL,                 -- chat_id for project plans, template id for templates
  kind       TEXT NOT NULL,
  plan_id    TEXT NOT NULL DEFAULT '',
  title      TEXT NOT NULL DEFAULT '',
  status     TEXT NOT NULL DEFAULT '',
  updated_at REAL NOT NULL DEFAULT 0,
  doc        TEXT NOT NULL,                 -- JSON: the normalised plan document
  PRIMARY KEY (project_id, key)
);
CREATE INDEX ix_plans_kind ON plans(kind, updated_at DESC);

CREATE TABLE tasks (
  project_id TEXT NOT NULL,
  id         TEXT NOT NULL,
  updated    REAL NOT NULL DEFAULT 0,
  doc        TEXT NOT NULL,
  PRIMARY KEY (project_id, id)
);

-- Project memory: name is 'entry' or 'entry/sub'; parent is '' or 'entry'.
CREATE TABLE memory_entries (
  project_id  TEXT NOT NULL,
  name        TEXT NOT NULL,
  parent      TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  author      TEXT NOT NULL DEFAULT '',
  updated     TEXT NOT NULL DEFAULT '',
  body        TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (project_id, name)
);
CREATE VIRTUAL TABLE memory_fts USING fts5(name, description, body, content='memory_entries', tokenize='unicode61');
CREATE TRIGGER memory_ai AFTER INSERT ON memory_entries BEGIN
  INSERT INTO memory_fts(rowid, name, description, body) VALUES (new.rowid, new.name, new.description, new.body);
END;
CREATE TRIGGER memory_ad AFTER DELETE ON memory_entries BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, name, description, body) VALUES ('delete', old.rowid, old.name, old.description, old.body);
END;
CREATE TRIGGER memory_au AFTER UPDATE ON memory_entries BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, name, description, body) VALUES ('delete', old.rowid, old.name, old.description, old.body);
  INSERT INTO memory_fts(rowid, name, description, body) VALUES (new.rowid, new.name, new.description, new.body);
END;

-- One row per LLM / coding-agent call (was provider_usage.jsonl + per-chat token_usage.calls).
CREATE TABLE usage_calls (
  id                 INTEGER PRIMARY KEY,
  ts                 REAL NOT NULL,
  provider           TEXT NOT NULL,
  model              TEXT NOT NULL DEFAULT '',
  input_tokens       INTEGER NOT NULL DEFAULT 0,
  output_tokens      INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
  cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  conv_id            TEXT NOT NULL DEFAULT '',
  agent              TEXT NOT NULL DEFAULT '',
  ducky_label        TEXT NOT NULL DEFAULT '',
  cost_usd           REAL
);
CREATE INDEX ix_usage_ts ON usage_calls(ts);
CREATE INDEX ix_usage_conv ON usage_calls(conv_id, ts);

-- Rolling logs: errors, activity, agent crashes, verse error stats, ...
CREATE TABLE events (
  id      INTEGER PRIMARY KEY,
  kind    TEXT NOT NULL,
  ts      REAL NOT NULL,
  source  TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  payload TEXT                              -- JSON for kinds with structured rows
);
CREATE INDEX ix_events_kind_ts ON events(kind, ts);
