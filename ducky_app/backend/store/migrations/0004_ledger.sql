-- 0004: phase 3 — the change ledger, blobs, file history, external-edit watch index.

-- One row per run; everything except entries/seen stays in doc (JSON) so the
-- run dict the journal logic manipulates round-trips unchanged.
CREATE TABLE runs (
  run_id     TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  conv_id    TEXT NOT NULL DEFAULT '',
  group_id   TEXT NOT NULL DEFAULT '',
  started    REAL NOT NULL DEFAULT 0,
  ended      REAL,
  status     TEXT NOT NULL DEFAULT 'running',
  archived   INTEGER NOT NULL DEFAULT 0,
  doc        TEXT NOT NULL
);
CREATE INDEX ix_runs_project ON runs(project_id, started DESC);
CREATE INDEX ix_runs_conv    ON runs(project_id, conv_id);

CREATE TABLE run_entries (
  run_id      TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  seq         INTEGER NOT NULL,
  path        TEXT NOT NULL DEFAULT '',
  ts          REAL NOT NULL DEFAULT 0,
  outcome     TEXT NOT NULL DEFAULT 'ok',
  before_blob TEXT NOT NULL DEFAULT '',
  after_blob  TEXT NOT NULL DEFAULT '',
  hash        TEXT NOT NULL,
  doc         TEXT NOT NULL,
  PRIMARY KEY (run_id, seq)
);
CREATE INDEX ix_entries_path ON run_entries(path, ts DESC);

-- Reads a run saw before writing (conflict detection); was run["seen"].
CREATE TABLE run_seen (
  run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  path   TEXT NOT NULL,
  doc    TEXT NOT NULL,
  PRIMARY KEY (run_id, path)
);

-- Last writer per path / editor slot; was index.json.
CREATE TABLE path_index (
  project_id TEXT NOT NULL,
  path       TEXT NOT NULL,
  doc        TEXT NOT NULL,
  PRIMARY KEY (project_id, path)
);

-- Content-addressed text: journal before/after bodies and file-history versions
-- share one copy per distinct content (sha256[:16], the pipeline's content_hash).
CREATE TABLE blobs (
  hash    TEXT PRIMARY KEY,
  text    TEXT NOT NULL,
  size    INTEGER NOT NULL,
  created REAL NOT NULL
);

-- Per-file version history (was file_history/<slug>/<path>/<id>.json with the
-- full content inline).
CREATE TABLE file_versions (
  project_id     TEXT NOT NULL,
  path           TEXT NOT NULL,
  entry_id       TEXT NOT NULL,
  saved_at       INTEGER NOT NULL DEFAULT 0,
  bytes          INTEGER NOT NULL DEFAULT 0,
  preview        TEXT NOT NULL DEFAULT '',
  content_hash   TEXT NOT NULL,
  schema_version INTEGER NOT NULL DEFAULT 2,
  source         TEXT NOT NULL DEFAULT '',
  run_id         TEXT NOT NULL DEFAULT '',
  conv_id        TEXT NOT NULL DEFAULT '',
  profile_id     TEXT NOT NULL DEFAULT '',
  ducky_name     TEXT NOT NULL DEFAULT '',
  model          TEXT NOT NULL DEFAULT '',
  tool           TEXT NOT NULL DEFAULT '',
  group_id       TEXT NOT NULL DEFAULT '',
  coding_agent   TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (project_id, path, entry_id)
);

-- External-edit watcher fingerprint; was human_index.json.
CREATE TABLE watch_index (
  project_id TEXT NOT NULL,
  path       TEXT NOT NULL,
  hash       TEXT NOT NULL,
  PRIMARY KEY (project_id, path)
);
