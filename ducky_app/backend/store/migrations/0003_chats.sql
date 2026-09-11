-- 0003: phase 2 — conversations, messages, folders, snapshots.

CREATE TABLE folders (
  project_id   TEXT NOT NULL,
  id           TEXT NOT NULL,
  name         TEXT NOT NULL DEFAULT '',
  parent_id    TEXT NOT NULL DEFAULT '',
  sort_order   REAL NOT NULL DEFAULT 0,
  group_hub_id TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (project_id, id)
);

-- Frozen prompt text shared across chats (skill index): one row per distinct text.
CREATE TABLE snapshots (
  hash    TEXT PRIMARY KEY,
  kind    TEXT NOT NULL,
  text    TEXT NOT NULL,
  created REAL NOT NULL
);

CREATE TABLE conversations (
  id                  TEXT PRIMARY KEY,
  project_id          TEXT NOT NULL,
  folder_id           TEXT NOT NULL DEFAULT '',
  title               TEXT NOT NULL DEFAULT '',
  created             REAL NOT NULL DEFAULT 0,
  updated             REAL NOT NULL DEFAULT 0,
  sort_order          REAL NOT NULL DEFAULT 0,
  provider            TEXT NOT NULL DEFAULT '',
  model               TEXT NOT NULL DEFAULT '',
  coding_agent        TEXT NOT NULL DEFAULT 'ducky',
  profile_id          TEXT NOT NULL DEFAULT '',
  file_path           TEXT NOT NULL DEFAULT '',
  parent_conv_id      TEXT NOT NULL DEFAULT '',
  leader_conv_id      TEXT NOT NULL DEFAULT '',
  is_group            INTEGER NOT NULL DEFAULT 0,
  tool_call_count     INTEGER NOT NULL DEFAULT 0,
  file_count          INTEGER NOT NULL DEFAULT 0,
  message_count       INTEGER NOT NULL DEFAULT 0,
  skill_snapshot_hash TEXT NOT NULL DEFAULT '',
  state               TEXT NOT NULL DEFAULT '{}'   -- JSON: every other Conversation field
);
CREATE INDEX ix_conv_list   ON conversations(project_id, folder_id, sort_order, updated DESC);
CREATE INDEX ix_conv_parent ON conversations(project_id, parent_conv_id);
CREATE INDEX ix_conv_file   ON conversations(project_id, file_path);

CREATE TABLE messages (
  id      INTEGER PRIMARY KEY,
  conv_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  seq     INTEGER NOT NULL,
  hash    TEXT NOT NULL,                 -- sha256[:16] of body; lets a save skip unchanged rows
  role    TEXT NOT NULL DEFAULT '',
  ts      REAL NOT NULL DEFAULT 0,
  run_id  TEXT NOT NULL DEFAULT '',
  text    TEXT NOT NULL DEFAULT '',      -- search text (prompt, reply, tool names/args/results)
  body    TEXT NOT NULL,                 -- JSON: the message exactly as the UI stores it
  UNIQUE (conv_id, seq)
);
CREATE INDEX ix_msg_run ON messages(conv_id, run_id);

CREATE VIRTUAL TABLE message_fts USING fts5(text, content='messages', content_rowid='id', tokenize='unicode61');
CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO message_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO message_fts(message_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO message_fts(message_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO message_fts(rowid, text) VALUES (new.id, new.text);
END;
