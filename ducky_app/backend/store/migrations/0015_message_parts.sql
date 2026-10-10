-- 0015: large message fields (tool results, tool arguments, long replies) are stored
-- once in message_parts, zlib-compressed, instead of in full inside every message row.
-- Most of the database was tool output: written whole again on every in-flight
-- checkpoint and kept once per copy of a chat. Rows written before this keep fmt 0
-- and read exactly as before.

-- 0: body is the message JSON. 1: body is {"m": the message with its large fields
-- set to null, "x": [[path..., part hash], ...]} and the fields live in message_parts.
ALTER TABLE messages ADD COLUMN fmt INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS message_parts (
  hash    TEXT PRIMARY KEY,   -- sha256 of the field's JSON
  data    BLOB NOT NULL,      -- that JSON, UTF-8, zlib-compressed
  size    INTEGER NOT NULL,   -- uncompressed bytes
  created REAL NOT NULL
);

-- Which message uses which part. Deleting a message (or its chat) drops its refs
-- through the cascade, and a part goes with its last ref.
CREATE TABLE IF NOT EXISTS message_part_refs (
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  hash       TEXT NOT NULL REFERENCES message_parts(hash),
  PRIMARY KEY (message_id, hash)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_part_refs_hash ON message_part_refs(hash);

CREATE TRIGGER IF NOT EXISTS message_part_refs_ad AFTER DELETE ON message_part_refs BEGIN
  DELETE FROM message_parts WHERE hash = old.hash
    AND NOT EXISTS (SELECT 1 FROM message_part_refs WHERE hash = old.hash);
END;

-- Search text only changes when the text column is written; moving a row's body
-- into parts must not delete and re-add its search entry.
DROP TRIGGER IF EXISTS messages_au;
CREATE TRIGGER messages_au AFTER UPDATE OF text ON messages BEGIN
  INSERT INTO message_fts(message_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO message_fts(rowid, text) VALUES (new.id, new.text);
END;
