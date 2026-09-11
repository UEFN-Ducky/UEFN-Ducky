-- 0006: phases 5/6 — MCP servers, capture index, Verse diagnostics cache, Verse digest index.

-- Nested MCP servers (was mcp.json; the file is now an export for hand-editing).
CREATE TABLE mcp_servers (
  id      TEXT PRIMARY KEY,
  block   TEXT NOT NULL,                 -- JSON: the Cursor-shaped server block incl. Ducky UI metadata
  updated REAL NOT NULL
);

-- Screenshot / snip files under tool_captures/, so pruning is by row, not by filename prefix.
CREATE TABLE captures (
  filename TEXT PRIMARY KEY,
  prefix   TEXT NOT NULL DEFAULT '',
  bytes    INTEGER NOT NULL DEFAULT 0,
  created  REAL NOT NULL
);

-- Problems cache: one row per Verse file (was .ducky_verse_scan.json per project).
CREATE TABLE verse_diagnostics (
  project_id TEXT NOT NULL,
  path       TEXT NOT NULL,              -- normalised, lower-cased island-relative path
  mtime_ns   INTEGER NOT NULL DEFAULT 0,
  size       INTEGER NOT NULL DEFAULT 0,
  errors     INTEGER NOT NULL DEFAULT 0,
  warnings   INTEGER NOT NULL DEFAULT 0,
  items      TEXT NOT NULL DEFAULT '[]', -- JSON
  updated    REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (project_id, path)
);

-- Verse digest index: every line of every known digest, trigram-searchable
-- (substring semantics, case-insensitive), rebuilt per file when its mtime moves.
CREATE TABLE digest_files (
  path    TEXT PRIMARY KEY,
  mtime   REAL NOT NULL,
  lines   INTEGER NOT NULL DEFAULT 0,
  indexed REAL NOT NULL
);
CREATE TABLE digest_lines (
  id   INTEGER PRIMARY KEY,
  path TEXT NOT NULL REFERENCES digest_files(path) ON DELETE CASCADE,
  line INTEGER NOT NULL,
  text TEXT NOT NULL
);
CREATE INDEX ix_digest_lines_path ON digest_lines(path, line);
CREATE VIRTUAL TABLE digest_fts USING fts5(text, content='digest_lines', content_rowid='id', tokenize='trigram');
CREATE TRIGGER digest_lines_ai AFTER INSERT ON digest_lines BEGIN
  INSERT INTO digest_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER digest_lines_ad AFTER DELETE ON digest_lines BEGIN
  INSERT INTO digest_fts(digest_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
