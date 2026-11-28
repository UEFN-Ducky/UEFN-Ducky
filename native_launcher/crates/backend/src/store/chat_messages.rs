//! Saving, loading and searching a chat's messages (`conv_save`, `upsert_run_message`
//! and the readers of `backend/store/repos/chats.py`).
//!
//! The delta-save contract. Writers say what changed; the database guards against
//! other writers.
//!
//! - Rows are serialised, hashed, compacted and sealed before the write lock; the
//!   transaction only reads the stored hashes, applies the rules below and writes.
//! - `conversations.msg_rev` is bumped by SQLite triggers on every change to
//!   `messages` (any connection, any process), never by this code.
//! - Delta: `dirty_from` given and `expect_rev` is the stored rev. Writes
//!   `messages[dirty_from..]` by hash and deletes rows past the end; rows below
//!   `dirty_from` are not read, but for the listed `dirty_rows`.
//! - Merge: `dirty_from > 0` with a stale rev (another writer touched the chat).
//!   Writes only `messages[dirty_from..]`, starting no later than the stored end, and
//!   never deletes, so another writer's older rows and newer tail survive.
//! - Full: `dirty_from` absent or 0 with a stale rev, or a new chat. Every message is
//!   hashed and compared with its stored row; rows past the end are deleted.
//! - Meta: no messages given; the transcript and its rev are untouched.
//! - `base_hashes`: each row's hash when this copy last read or saved it. A row whose
//!   hash is still its base but whose stored hash differs is another writer's newer
//!   edit and is never written over (`theirs`); a row both changed goes to this copy.

use std::collections::HashSet;
use std::sync::Arc;

use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Map, Value};

use super::chats::{msg_rev, owned, row_count, text, was_deleted, Chats, COLUMNS};
use super::duckies::Who;
use super::messages::{self, dirty_span, encode_rows, opened, prep_row, seal_new_parts, sealed, write_row, Chat, Encoded, PartReader};
use super::pyjson::{dumps, float, int, pystr, truthy};
use super::tokens::{blind_query, split, strip, words};
use super::{now, seal, Error, Result};

const STATE_EXCLUDED: [&str; 3] = ["id", "messages", "skill_snapshot"];
const PREVIEW_WORDS: usize = 12;

/// One `conv_save`: the chat document and, with `messages`, the transcript to sync.
pub struct Save {
    pub project_id: String,
    pub doc: Map<String, Value>,
    pub messages: Option<Vec<Value>>,
    pub dirty_from: Option<i64>,
    pub expect_rev: Option<i64>,
    pub base_hashes: Option<Vec<String>>,
    pub dirty_rows: Vec<i64>,
}

/// The chat row's sealed title and state against what was stored then (`seen`), and the
/// snapshot hash; the transaction seals again only if `seen` moved.
struct Meta {
    title: String,
    state_text: String,
    snapshot: String,
    seen: (String, String),
    sealed: (String, String),
}

/// `text` sealed, reusing the stored value when it already holds `text`.
fn sealed_as(stored: &str, text: &str, who: &Who) -> Result<String> {
    if seal::is_sealed(stored) && seal::open_text(stored, &who.entropy).is_ok_and(|open| open == text) {
        return Ok(stored.to_string());
    }
    sealed(text, who)
}

/// A document field as text: `str(value or default)`.
fn field(doc: &Map<String, Value>, name: &str, default: &str) -> String {
    doc.get(name).filter(|value| truthy(value)).map_or(default.to_string(), pystr)
}

fn prepare_meta(conn: &Connection, doc: &Map<String, Value>, conv_id: &str, who: &Who) -> rusqlite::Result<Meta> {
    let in_state = |name: &str| !COLUMNS.contains(&name) && !STATE_EXCLUDED.contains(&name);
    let state: Map<String, Value> = doc.iter().filter(|(name, _)| in_state(name)).map(|(name, value)| (name.clone(), value.clone())).collect();
    let (title, state_text) = (field(doc, "title", ""), dumps(&Value::Object(state)));
    let seen = conn
        .query_row("SELECT title, state FROM conversations WHERE id=?1", [conv_id], |r| Ok((text(r, "title")?, text(r, "state")?)))
        .optional()?
        .unwrap_or_default();
    let sealed = (sealed_as(&seen.0, &title, who)?, sealed_as(&seen.1, &state_text, who)?);
    Ok(Meta { title, state_text, snapshot: field(doc, "skill_snapshot", ""), seen, sealed })
}

fn snapshot_put(conn: &Connection, kind: &str, text: &str) -> rusqlite::Result<String> {
    if text.is_empty() {
        return Ok(String::new());
    }
    let hash = messages::hash(text);
    conn.execute("INSERT OR IGNORE INTO snapshots(hash, kind, text, created) VALUES (?1, ?2, ?3, ?4)", params![hash, kind, text, now()])?;
    Ok(hash)
}

const UPSERT: &str = "INSERT INTO conversations(id, account_id, project_id, folder_id, title, created, updated, sort_order, \
    provider, model, coding_agent, profile_id, file_path, parent_conv_id, leader_conv_id, is_group, \
    tool_call_count, file_count, message_count, skill_snapshot_hash, state) \
    VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, \
    coalesce((SELECT message_count FROM conversations WHERE id=?1), 0), ?19, ?20) \
    ON CONFLICT(id) DO UPDATE SET project_id=excluded.project_id, folder_id=excluded.folder_id, \
    title=excluded.title, created=excluded.created, updated=excluded.updated, sort_order=excluded.sort_order, \
    provider=excluded.provider, model=excluded.model, coding_agent=excluded.coding_agent, \
    profile_id=excluded.profile_id, file_path=excluded.file_path, parent_conv_id=excluded.parent_conv_id, \
    leader_conv_id=excluded.leader_conv_id, is_group=excluded.is_group, tool_call_count=excluded.tool_call_count, \
    file_count=excluded.file_count, skill_snapshot_hash=excluded.skill_snapshot_hash, state=excluded.state";

fn fts_query(query: &str) -> String {
    let terms: Vec<String> = split(query).map(|term| term.replace('"', "\"\"")).collect();
    let all = terms.iter().map(|term| format!("\"{term}\"")).collect::<Vec<_>>().join(" ");
    if terms.len() > 1 {
        return format!("({all}) OR \"{}\"", terms.join("_"));
    }
    all
}

/// About a dozen words around the first one the query matched.
fn preview(text: &str, query: &str) -> String {
    let wanted: HashSet<String> = words(query).into_iter().collect();
    let chunks: Vec<&str> = split(text).collect();
    let at = chunks.iter().position(|chunk| words(chunk).iter().any(|word| wanted.contains(word))).unwrap_or(0);
    let start = (at + 1).saturating_sub(PREVIEW_WORDS / 2);
    let shown = &chunks[start..(start + PREVIEW_WORDS).min(chunks.len())];
    let (before, after) = (if start > 0 { "…" } else { "" }, if start + PREVIEW_WORDS < chunks.len() { "…" } else { "" });
    format!("{before}{}{after}", shown.join(" "))
}

impl Chats {
    /// Upsert the row; when messages are given, sync rows by content hash (the contract
    /// above). Returns the counts (`inserted`, `updated`, `deleted`, `bytes`), `path`,
    /// `msg_rev`, and with messages `hashes`, `hashes_from`, `dirty_hashes`, `theirs`.
    /// A chat of another account, or a deleted one, is never written (`skipped`).
    pub async fn conv_save(&self, who: Who, save: Save) -> Result<Map<String, Value>> {
        let Some(conv_id) = save.doc.get("id").map(pystr) else { return Err(Error("a chat needs an id".into())) };
        let key = self.chat_key(&who).await?;
        let save = Arc::new(save);
        let (meta, enc) = {
            let (who, key, save, conv_id) = (who.clone(), key.clone(), Arc::clone(&save), conv_id.clone());
            self.store
                .readers
                .read(move |conn| {
                    let enc = match &save.messages {
                        Some(messages) => {
                            let (rows, tail) = dirty_span(messages.len(), save.dirty_from, &save.dirty_rows);
                            let chat = Chat { conv_id: &conv_id, who: &who, key: &key, base: save.base_hashes.as_deref() };
                            encode_rows(conn, &chat, messages, &rows, tail)?
                        }
                        None => Encoded::default(),
                    };
                    Ok((prepare_meta(conn, &save.doc, &conv_id, &who)?, enc))
                })
                .await?
        };
        self.store
            .writer
            .write("chats::conv_save", move |conn| {
                let doc = &save.doc;
                let mut stats = json!({"inserted": 0, "updated": 0, "deleted": 0, "bytes": 0}).as_object().cloned().unwrap_or_default();
                let owner = conn
                    .query_row("SELECT account_id, title, state, msg_rev FROM conversations WHERE id=?1", [&conv_id], |r| {
                        Ok((text(r, "account_id")?, text(r, "title")?, text(r, "state")?, r.get::<_, i64>(3)?))
                    })
                    .optional()?;
                // Another account's chat, or one a turn, team message or other window still
                // holding it saved after it was deleted: never written, never brought back.
                if owner.as_ref().is_some_and(|owner| owner.0 != who.id) || was_deleted(conn, &conv_id)? {
                    stats.insert("skipped".into(), json!(1));
                    return Ok(stats);
                }
                let stored = owner.as_ref().map(|owner| (owner.1.clone(), owner.2.clone())).unwrap_or_default();
                let (title, state) = if stored == meta.seen {
                    meta.sealed.clone()
                } else {
                    // Another writer changed them since they were sealed.
                    (sealed_as(&stored.0, &meta.title, &who)?, sealed_as(&stored.1, &meta.state_text, &who)?)
                };
                let snap_hash = snapshot_put(conn, "skill_index", &meta.snapshot)?;
                let number = |name: &str| doc.get(name).and_then(float).unwrap_or(0.0);
                let count = |name: &str| doc.get(name).map_or(0, int);
                conn.execute(
                    UPSERT,
                    params![
                        conv_id,
                        who.id,
                        save.project_id,
                        field(doc, "folder_id", ""),
                        title,
                        number("created"),
                        number("updated"),
                        number("sort_order"),
                        field(doc, "provider", ""),
                        field(doc, "model", ""),
                        field(doc, "coding_agent", "ducky"),
                        field(doc, "profile_id", ""),
                        field(doc, "file_path", ""),
                        field(doc, "parent_conv_id", ""),
                        field(doc, "leader_conv_id", ""),
                        doc.get("is_group").is_some_and(truthy) as i64,
                        count("tool_call_count"),
                        count("file_count"),
                        snap_hash,
                        state
                    ],
                )?;
                stats.insert("path".into(), json!("meta"));
                if let Some(messages) = &save.messages {
                    let len = messages.len() as i64;
                    // The rev was read in this write transaction: no other writer can slip in.
                    let current = owner.as_ref().is_some_and(|owner| Some(owner.3) == save.expect_rev);
                    let (path, start) = match save.dirty_from {
                        Some(from) if current => ("delta", from.clamp(0, len)),
                        // A stale copy: rows below dirty_from are the store's, and it never
                        // truncates. Starting no later than the stored end leaves no gap.
                        Some(from) if from != 0 && owner.is_some() => ("merge", from.min(len).min(row_count(conn, &conv_id)?).max(0)),
                        _ => ("full", 0), // an explicit reassign: this copy is the transcript
                    };
                    let (mut listed, _) = dirty_span(messages.len(), save.dirty_from, &save.dirty_rows);
                    listed.retain(|seq| path != "full" && *seq < start);
                    let chat = Chat { conv_id: &conv_id, who: &who, key: &key, base: save.base_hashes.as_deref() };
                    stats.extend(messages::sync(conn, &chat, messages, start, path != "merge", &listed, &enc)?);
                    stats.insert("path".into(), json!(path));
                    stats.insert("hashes_from".into(), json!(start));
                    let count = if path == "merge" { row_count(conn, &conv_id)? } else { len };
                    conn.execute("UPDATE conversations SET message_count=?1 WHERE id=?2", params![count, conv_id])?;
                }
                let rev: i64 = conn.query_row("SELECT msg_rev FROM conversations WHERE id=?1", [&conv_id], |r| r.get(0))?;
                stats.insert("msg_rev".into(), json!(rev));
                Ok(stats)
            })
            .await
    }

    /// Write a turn's in-flight assistant as ONE row: the run's last assistant after the
    /// last user row is replaced, else the message is appended. Returns `{"seq", "count"}`
    /// (`skipped` when the chat is gone or not this account's). `stats` sets
    /// `tool_call_count` / `file_count` when given.
    pub async fn upsert_run_message(
        &self,
        who: Who,
        conv_id: String,
        run_id: String,
        message: Value,
        updated: f64,
        stats: Option<Map<String, Value>>,
    ) -> Result<Value> {
        let key = self.chat_key(&who).await?;
        // Everything but SQL runs before the lock: encode, hash, seal, compress parts.
        let (prep, parts) = {
            let who = who.clone();
            self.store
                .readers
                .read(move |conn| {
                    let body = dumps(&message);
                    let prep = prep_row(&message, &body, messages::hash(&body), &who, &key)?;
                    let parts = seal_new_parts(conn, &prep.parts, &who)?;
                    Ok((prep, parts))
                })
                .await?
        };
        self.store
            .writer
            .write("chats::upsert_run_message", move |conn| {
                if !owned(conn, &conv_id, &who.id)? || was_deleted(conn, &conv_id)? {
                    return Ok(json!({"skipped": 1}));
                }
                let last_user: i64 =
                    conn.query_row("SELECT coalesce(max(seq), -1) FROM messages WHERE conv_id=?1 AND role='user'", [&conv_id], |r| r.get(0))?;
                let hit: Option<(i64, (String, i64))> = if run_id.is_empty() {
                    None
                } else {
                    conn.query_row(
                        "SELECT seq, hash, id FROM messages WHERE conv_id=?1 AND seq>?2 AND role='assistant' AND run_id=?3 \
                         ORDER BY seq DESC LIMIT 1",
                        params![conv_id, last_user, run_id],
                        |r| Ok((r.get(0)?, (r.get(1)?, r.get(2)?))),
                    )
                    .optional()?
                };
                let mut count: i64 = conn.query_row("SELECT count(*) FROM messages WHERE conv_id=?1", [&conv_id], |r| r.get(0))?;
                let (seq, row) = match hit {
                    Some((seq, row)) => (seq, Some(row)),
                    None => {
                        count += 1;
                        (count - 1, None)
                    }
                };
                if row.as_ref().is_none_or(|(have, _)| *have != prep.hash) {
                    write_row(conn, &conv_id, seq, row.as_ref(), &prep, &who, &parts)?;
                }
                conn.execute("UPDATE conversations SET updated=?1, message_count=?2 WHERE id=?3", params![updated, count, conv_id])?;
                if let Some(stats) = stats.filter(|stats| !stats.is_empty()) {
                    let counter = |name: &str| stats.get(name).map_or(0, int);
                    conn.execute(
                        "UPDATE conversations SET tool_call_count=?1, file_count=?2 WHERE id=?3",
                        params![counter("tool_call_count"), counter("file_count"), conv_id],
                    )?;
                }
                Ok(json!({"seq": seq, "count": count}))
            })
            .await
    }

    pub async fn messages_get(&self, who: Who, conv_id: String) -> Result<Vec<Value>> {
        Ok(self.messages_get_rev(who, conv_id).await?.0)
    }

    /// The transcript, its `msg_rev` and each row's hash (a copy's base) from one read
    /// snapshot, so the caller can save by delta at once. No rev: not this account's.
    pub async fn messages_get_rev(&self, who: Who, conv_id: String) -> Result<(Vec<Value>, Option<i64>, Vec<String>)> {
        let (messages, rev, _, hashes) = self.messages_since(who, conv_id, 0).await?;
        Ok((messages, rev, hashes))
    }

    /// `([(seq, message), ...], row count)` for seqs `start` to `stop` from one snapshot,
    /// decoding only those rows. `([], 0)` when the chat isn't this account's.
    pub async fn messages_range(&self, who: Who, conv_id: String, start: i64, stop: Option<i64>) -> Result<(Vec<(i64, Value)>, i64)> {
        self.store
            .readers
            .read(move |conn| {
                let _snapshot = conn.unchecked_transaction()?;
                if msg_rev(conn, &conv_id, &who.id)?.is_none() {
                    return Ok((Vec::new(), 0));
                }
                let rows = messages::messages(conn, &conv_id, &who, start.max(0), stop)?;
                Ok((rows.into_iter().map(|(seq, _, message)| (seq, message)).collect(), row_count(conn, &conv_id)?))
            })
            .await
    }

    /// A page for the chat view (`repos/chat_display.py`): `(seq, last seq, message)`,
    /// newest first, at most `limit` from `before_seq` (or the end) back, from one
    /// snapshot. Empty when the chat isn't this account's.
    ///
    /// ponytail: every field of the page's rows is opened here; Python opens a stored
    /// field only when the view renders it. Open on demand if a trace shows a page of
    /// big tool results being slow.
    pub async fn display_messages(&self, who: Who, conv_id: String, before_seq: Option<i64>, limit: i64) -> Result<Vec<(i64, i64, Value)>> {
        self.store
            .readers
            .read(move |conn| {
                let _snapshot = conn.unchecked_transaction()?;
                if msg_rev(conn, &conv_id, &who.id)?.is_none() {
                    return Ok(Vec::new());
                }
                let last: Option<i64> = conn.query_row("SELECT max(seq) FROM messages WHERE conv_id=?1", [&conv_id], |r| r.get(0))?;
                let Some(last) = last else { return Ok(Vec::new()) };
                let end = before_seq.map_or(last, |before| before.min(last));
                let rows = messages::messages(conn, &conv_id, &who, (end + 1 - limit).max(0), Some(end + 1))?;
                Ok(rows.into_iter().rev().map(|(seq, _, message)| (seq, last, message)).collect())
            })
            .await
    }

    /// The messages from `seq` on, the chat's `msg_rev`, its row count and the rows'
    /// hashes, from one read snapshot: a copy refreshes its tail without loading the
    /// whole chat. `([], None, 0, [])` when the chat isn't this account's.
    pub async fn messages_since(&self, who: Who, conv_id: String, seq: i64) -> Result<(Vec<Value>, Option<i64>, i64, Vec<String>)> {
        self.store
            .readers
            .read(move |conn| {
                let _snapshot = conn.unchecked_transaction()?;
                let Some(rev) = msg_rev(conn, &conv_id, &who.id)? else { return Ok((Vec::new(), None, 0, Vec::new())) };
                let (mut out, mut hashes) = (Vec::new(), Vec::new());
                for (_, hash, message) in messages::messages(conn, &conv_id, &who, seq.max(0), None)? {
                    out.push(message);
                    hashes.push(hash);
                }
                Ok((out, Some(rev), row_count(conn, &conv_id)?, hashes))
            })
            .await
    }

    /// (the newest non-empty assistant `content`, stripped; how many assistant rows),
    /// opening only the rows from the end back to that reply.
    pub async fn last_reply(&self, who: Who, conv_id: String) -> Result<(String, i64)> {
        self.store
            .readers
            .read(move |conn| {
                if !owned(conn, &conv_id, &who.id)? {
                    return Ok((String::new(), 0));
                }
                let count: i64 =
                    conn.query_row("SELECT count(*) FROM messages WHERE conv_id=?1 AND role='assistant'", [&conv_id], |r| r.get(0))?;
                let mut stmt = conn.prepare("SELECT id, body, fmt FROM messages WHERE conv_id=?1 AND role='assistant' ORDER BY seq DESC")?;
                let mut rows = stmt.query([&conv_id])?;
                while let Some(r) = rows.next()? {
                    let (id, body, fmt): (i64, String, i64) = (r.get(0)?, r.get(1)?, r.get(2)?);
                    let message = messages::message(&body, fmt, &who, &mut PartReader::of_message(conn, &who, id))?;
                    let content = message.as_ref().and_then(|message| message.get("content")).and_then(Value::as_str).map(strip);
                    if let Some(content) = content.filter(|content| !content.is_empty()) {
                        return Ok((content.to_string(), count));
                    }
                }
                Ok((String::new(), count))
            })
            .await
    }

    /// Body search over every message of the account in a project via FTS5 (bm25
    /// ranked). Rows hold keyed word tokens; the plain query still finds rows an older
    /// build saved until they are sealed. Previews are made from the opened message.
    pub async fn search_messages(&self, who: Who, project_id: String, query: String, limit: i64) -> Result<Vec<Value>> {
        let (blind, plain) = (blind_query(&query, &self.chat_key(&who).await?), fts_query(&query));
        let matching = match (blind.is_empty(), plain.is_empty()) {
            (true, true) => return Ok(Vec::new()),
            (false, false) => format!("({blind}) OR ({plain})"),
            _ => format!("{blind}{plain}"),
        };
        self.store
            .readers
            .read(move |conn| {
                let mut stmt = conn.prepare(
                    "SELECT c.id AS conv_id, c.title, c.folder_id, m.seq, m.id AS message_id, m.body, m.fmt \
                     FROM message_fts JOIN messages m ON m.id = message_fts.rowid \
                     JOIN conversations c ON c.id = m.conv_id \
                     WHERE message_fts MATCH ?1 AND c.account_id=?2 AND c.project_id=?3 ORDER BY bm25(message_fts) LIMIT ?4",
                )?;
                let mut rows = stmt.query(params![matching, who.id, project_id, limit])?;
                let mut out = Vec::new();
                while let Some(r) = rows.next()? {
                    let (id, body, fmt): (i64, String, i64) = (r.get("message_id")?, r.get("body")?, r.get("fmt")?);
                    let message = messages::message(&body, fmt, &who, &mut PartReader::of_message(conn, &who, id))?;
                    let shown = match &message {
                        Some(Value::Object(message)) => preview(&messages::search_text(message), &query),
                        Some(_) => preview("", &query),
                        None => String::new(),
                    };
                    out.push(json!({
                        "conv_id": text(r, "conv_id")?,
                        "title": opened(&text(r, "title")?, &who)?,
                        "folder_id": text(r, "folder_id")?,
                        "seq": r.get::<_, i64>("seq")?,
                        "preview": shown,
                    }));
                }
                Ok(out)
            })
            .await
    }
}
