-- 0026: a per-chat message revision, kept by triggers.
-- A delta save writes only the messages a writer changed, so it must know no other
-- writer touched the chat since it last looked. Triggers bump msg_rev on every insert,
-- update and delete of a chat's message rows, whoever writes them (another connection,
-- an agent host's upsert_run_message, raw SQL). Only equality is compared: a bulk
-- delete of N rows bumps it N times, so it is not a count.

ALTER TABLE conversations ADD COLUMN msg_rev INTEGER NOT NULL DEFAULT 0;

CREATE TRIGGER IF NOT EXISTS messages_rev_ai AFTER INSERT ON messages BEGIN
  UPDATE conversations SET msg_rev = msg_rev + 1 WHERE id = new.conv_id;
END;
CREATE TRIGGER IF NOT EXISTS messages_rev_ad AFTER DELETE ON messages BEGIN
  UPDATE conversations SET msg_rev = msg_rev + 1 WHERE id = old.conv_id;
END;
CREATE TRIGGER IF NOT EXISTS messages_rev_au AFTER UPDATE ON messages BEGIN
  UPDATE conversations SET msg_rev = msg_rev + 1 WHERE id IN (old.conv_id, new.conv_id);
END;
