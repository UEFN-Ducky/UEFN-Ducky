//! `conversations` / `folders` / `snapshots` (`backend/store/repos/chats.py`).
//!
//! Every function speaks the `Conversation.to_dict()` document: columns hold what lists
//! and lookups need, `state` holds the rest as JSON. Every chat and folder belongs to an
//! account: these functions see only its rows, and its titles, state and folder names
//! are sealed for it.
//!
//! ponytail: opened titles and state are not memoised as Python's `open_cached` does;
//! add that when a chat list shows up in a trace.

use std::collections::{HashMap, HashSet};
use std::sync::Mutex;

use aes_gcm::aead::rand_core::RngCore;
use aes_gcm::aead::OsRng;
use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use rusqlite::types::ValueRef;
use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::{json, Map, Value};

use super::duckies::Who;
use super::messages::{self, opened, sealed};
use super::pyjson::{float, pystr, truthy};
use super::tokens::Key;
use super::{now, seal, Error, Result, Store};

/// Deleted chat and folder ids, kept in `workspace_state` so a late save from any
/// process can't re-create them.
pub(super) const DELETED_CHAT: &str = "deleted_chat:";
const DELETED_FOLDER: &str = "deleted_folder:";
const DELETED_KEEP_S: f64 = 30.0 * 86400.0;

/// `Conversation.to_dict()` keys that are real columns; everything else lives in `state`.
pub(super) const COLUMNS: [&str; 15] = [
    "folder_id",
    "title",
    "created",
    "updated",
    "sort_order",
    "provider",
    "model",
    "coding_agent",
    "profile_id",
    "file_path",
    "parent_conv_id",
    "leader_conv_id",
    "is_group",
    "tool_call_count",
    "file_count",
];

pub struct Chats {
    pub(super) store: Store,
    /// Chat key by account: read (and unsealed) once per process.
    keys: Mutex<HashMap<String, Key>>,
}

async fn blocking<T: Send + 'static>(work: impl FnOnce() -> Result<T> + Send + 'static) -> Result<T> {
    tokio::task::spawn_blocking(work).await.unwrap_or_else(|_| Err(Error("the call panicked".into())))
}

/// A column as JSON, the way Python's sqlite3 hands it over.
fn cell(value: ValueRef) -> Value {
    match value {
        ValueRef::Integer(n) => json!(n),
        ValueRef::Real(n) => json!(n),
        ValueRef::Text(text) => json!(String::from_utf8_lossy(text)),
        ValueRef::Null | ValueRef::Blob(_) => Value::Null,
    }
}

pub(super) fn text(r: &Row, name: &str) -> rusqlite::Result<String> {
    Ok(r.get::<_, Option<String>>(name)?.unwrap_or_default())
}

/// A folder row as Python's `dict(row)`: every selected column, its name opened.
fn folder(r: &Row, who: &Who) -> rusqlite::Result<Value> {
    let mut out = Map::new();
    for (i, name) in r.as_ref().column_names().into_iter().enumerate() {
        out.insert(name.to_string(), cell(r.get_ref(i)?));
    }
    out.insert("name".into(), json!(opened(&text(r, "name")?, who)?));
    Ok(Value::Object(out))
}

pub(super) fn was_deleted(conn: &Connection, conv_id: &str) -> rusqlite::Result<bool> {
    conn.prepare("SELECT 1 FROM workspace_state WHERE key=?1")?.exists([format!("{DELETED_CHAT}{conv_id}")])
}

pub(super) fn owned(conn: &Connection, conv_id: &str, account: &str) -> rusqlite::Result<bool> {
    conn.prepare("SELECT 1 FROM conversations WHERE id=?1 AND account_id=?2")?.exists([conv_id, account])
}

fn mark_deleted(conn: &Connection, prefix: &str, ids: &[String], now: f64) -> rusqlite::Result<()> {
    let mut insert = conn.prepare(
        "INSERT INTO workspace_state(key, value, updated) VALUES (?1, ?2, ?3) \
         ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
    )?;
    for id in ids.iter().filter(|id| !id.is_empty()) {
        insert.execute(params![format!("{prefix}{id}"), json!(now).to_string(), now])?;
    }
    // No turn outlives a month: older marks only take space.
    conn.execute(
        "DELETE FROM workspace_state WHERE key >= ?1 AND key < ?2 AND updated < ?3",
        params![prefix, after(prefix), now - DELETED_KEEP_S],
    )?;
    Ok(())
}

/// The first key past every `prefix…` key (`deleted_chat:` → `deleted_chat;`).
fn after(prefix: &str) -> String {
    format!("{};", &prefix[..prefix.len() - 1])
}

pub(super) fn row_count(conn: &Connection, conv_id: &str) -> rusqlite::Result<i64> {
    conn.query_row("SELECT coalesce(max(seq), -1) + 1 FROM messages WHERE conv_id=?1", [conv_id], |r| r.get(0))
}

pub(super) fn msg_rev(conn: &Connection, conv_id: &str, account: &str) -> rusqlite::Result<Option<i64>> {
    conn.query_row("SELECT msg_rev FROM conversations WHERE id=?1 AND account_id=?2", [conv_id, account], |r| r.get(0))
        .optional()
}

fn snapshot_text(conn: &Connection, hash: &str) -> rusqlite::Result<String> {
    if hash.is_empty() {
        return Ok(String::new());
    }
    // Not kept reads as empty: it may be written later.
    let kept = conn.query_row("SELECT text FROM snapshots WHERE hash=?1", [hash], |r| r.get::<_, String>(0)).optional()?;
    Ok(kept.unwrap_or_default())
}

/// One row as the document it was saved as.
fn doc(conn: &Connection, r: &Row, who: &Who, with_messages: bool) -> rusqlite::Result<Value> {
    let state = text(r, "state")?;
    let state = if state.is_empty() { "{}".to_string() } else { opened(&state, who)? };
    let mut doc = match serde_json::from_str(&state) {
        Ok(Value::Object(fields)) => fields,
        _ => Map::new(),
    };
    for column in COLUMNS {
        doc.insert(column.to_string(), cell(r.get_ref(column)?));
    }
    doc.insert("title".into(), json!(opened(&text(r, "title")?, who)?));
    let is_group = truthy(&doc["is_group"]);
    doc.insert("is_group".into(), json!(is_group));
    doc.insert("id".into(), cell(r.get_ref("id")?));
    doc.insert("skill_snapshot".into(), json!(snapshot_text(conn, &text(r, "skill_snapshot_hash")?)?));
    let rows = if with_messages { messages::messages(conn, &text(r, "id")?, who, 0, None)? } else { Vec::new() };
    doc.insert("messages".into(), rows.into_iter().map(|(_, _, message)| message).collect());
    Ok(Value::Object(doc))
}

impl Chats {
    pub fn new(store: Store) -> Chats {
        Chats { store, keys: Mutex::new(HashMap::new()) }
    }

    fn cached_key(&self, account: &str) -> Option<Key> {
        self.keys.lock().unwrap_or_else(|e| e.into_inner()).get(account).cloned()
    }

    /// The account's key for search tokens and payload names: random, stored sealed.
    pub async fn chat_key(&self, who: &Who) -> Result<Key> {
        if let Some(key) = self.cached_key(&who.id) {
            return Ok(key);
        }
        let name = format!("chat_key:{}", who.id);
        let read = |conn: &Connection, name: &str| {
            conn.query_row("SELECT value FROM meta WHERE key=?1", [name], |r| r.get::<_, String>(0)).optional()
        };
        let stored = {
            let name = name.clone();
            self.store.readers.read(move |conn| read(conn, &name)).await?
        };
        let stored = match stored {
            Some(stored) => stored,
            None => {
                let entropy = who.entropy.clone();
                let fresh = blocking(move || {
                    let mut key = [0u8; 32];
                    OsRng.fill_bytes(&mut key);
                    seal::seal_text(&BASE64.encode(key), &entropy)
                })
                .await?;
                self.store
                    .writer
                    .write("chats::chat_key", move |conn| {
                        // Another process may have made it meanwhile: its key is the key.
                        conn.execute("INSERT OR IGNORE INTO meta(key, value, updated) VALUES (?1, ?2, ?3)", params![name, fresh, now()])?;
                        Ok(read(conn, &name)?.unwrap_or_default())
                    })
                    .await?
            }
        };
        let entropy = who.entropy.clone();
        let key = blocking(move || {
            let bytes = BASE64.decode(seal::open_text(&stored, &entropy)?).map_err(|_| Error("the chat key is damaged".into()))?;
            Key::new(bytes)
        })
        .await?;
        self.keys.lock().unwrap_or_else(|e| e.into_inner()).insert(who.id.clone(), key.clone());
        Ok(key)
    }

    // ----------------------------------------------------------------------- folders

    pub async fn folders_get(&self, who: Who, project_id: String) -> Result<Vec<Value>> {
        self.store
            .readers
            .read(move |conn| {
                conn.prepare(
                    "SELECT id, name, parent_id, sort_order, group_hub_id FROM folders WHERE account_id=?1 AND project_id=?2 \
                     ORDER BY parent_id, sort_order, id",
                )?
                .query_map([&who.id, &project_id], |r| folder(r, &who))?
                .collect()
            })
            .await
    }

    /// `[(project id, its folders), ...]`.
    pub async fn folders_list_all_projects(&self, who: Who) -> Result<Vec<(String, Vec<Value>)>> {
        self.store
            .readers
            .read(move |conn| {
                let mut stmt = conn.prepare(
                    "SELECT project_id, id, name, parent_id, sort_order, group_hub_id FROM folders WHERE account_id=?1 \
                     ORDER BY project_id, parent_id, sort_order, id",
                )?;
                let mut rows = stmt.query([&who.id])?;
                let mut out: Vec<(String, Vec<Value>)> = Vec::new();
                while let Some(r) = rows.next()? {
                    let project = text(r, "project_id")?;
                    // Sorted by project: one project's folders come together.
                    if out.last().is_none_or(|(last, _)| *last != project) {
                        out.push((project, Vec::new()));
                    }
                    if let Some((_, folders)) = out.last_mut() {
                        folders.push(folder(r, &who)?);
                    }
                }
                Ok(out)
            })
            .await
    }

    /// The project a folder is stored under (the Archive folder is in every project: None).
    pub async fn folder_project_id(&self, account: String, folder_id: String) -> Result<Option<String>> {
        if folder_id.is_empty() || folder_id == "archive" {
            return Ok(None);
        }
        self.store
            .readers
            .read(move |conn| {
                conn.query_row("SELECT project_id FROM folders WHERE account_id=?1 AND id=?2 LIMIT 1", [&account, &folder_id], |r| r.get(0))
                    .optional()
            })
            .await
    }

    pub async fn folders_replace(&self, who: Who, project_id: String, folders: Vec<Value>) -> Result<()> {
        let or_empty = |v: &Value| if truthy(v) { pystr(v) } else { String::new() };
        let account = who.id.clone();
        let rows = blocking(move || {
            folders
                .iter()
                .filter(|f| truthy(&f["id"]))
                .map(|f| {
                    let name = f.get("name").map(pystr).unwrap_or_default();
                    let sort_order = float(&f["sort_order"]).unwrap_or(0.0);
                    Ok((pystr(&f["id"]), sealed(&name, &who)?, or_empty(&f["parent_id"]), sort_order, or_empty(&f["group_hub_id"])))
                })
                .collect::<Result<Vec<_>>>()
        })
        .await?;
        self.store
            .writer
            .write("chats::folders_replace", move |conn| {
                // A writer holding a folder list read before a delete must not put the folder back.
                let gone: HashSet<String> = conn
                    .prepare("SELECT key FROM workspace_state WHERE key >= ?1 AND key < ?2")?
                    .query_map([DELETED_FOLDER, &after(DELETED_FOLDER)], |r| r.get::<_, String>(0))?
                    .map(|key| key.map(|key| key[DELETED_FOLDER.len()..].to_string()))
                    .collect::<rusqlite::Result<_>>()?;
                conn.execute("DELETE FROM folders WHERE account_id=?1 AND project_id=?2", [&account, &project_id])?;
                let mut insert = conn.prepare(
                    "INSERT INTO folders(account_id, project_id, id, name, parent_id, sort_order, group_hub_id) \
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                )?;
                for (id, name, parent, sort_order, hub) in rows.iter().filter(|row| !gone.contains(&row.0)) {
                    insert.execute(params![account, project_id, id, name, parent, sort_order, hub])?;
                }
                Ok(())
            })
            .await
    }

    /// Move folders `(source project, folder id, new parent)` and chats `(chat id, folder)`
    /// of the account onto the `target` project, in one transaction.
    pub async fn move_to_project(
        &self,
        account: String,
        target: String,
        folders: Vec<(String, String, String)>,
        convs: Vec<(String, String)>,
    ) -> Result<()> {
        self.store
            .writer
            .write("chats::move_to_project", move |conn| {
                for (source, folder_id, parent_id) in &folders {
                    conn.execute(
                        "UPDATE folders SET project_id=?1, parent_id=?2 WHERE account_id=?3 AND project_id=?4 AND id=?5",
                        params![target, parent_id, account, source, folder_id],
                    )?;
                }
                for (conv_id, folder_id) in &convs {
                    conn.execute(
                        "UPDATE conversations SET project_id=?1, folder_id=?2 WHERE account_id=?3 AND id=?4",
                        params![target, folder_id, account, conv_id],
                    )?;
                }
                Ok(())
            })
            .await
    }

    /// Folders the person deleted: never written back by a stale folder list.
    pub async fn folders_mark_deleted(&self, folder_ids: Vec<String>) -> Result<()> {
        let ids: Vec<String> = folder_ids.into_iter().filter(|id| !id.trim().is_empty()).collect();
        if ids.is_empty() {
            return Ok(());
        }
        self.store.writer.write("chats::folders_mark_deleted", move |conn| mark_deleted(conn, DELETED_FOLDER, &ids, now())).await
    }

    // ----------------------------------------------------------------------- conversations

    pub async fn conv_get(&self, who: Who, conv_id: String, project_id: Option<String>, with_messages: bool) -> Result<Option<Value>> {
        self.store
            .readers
            .read(move |conn| {
                let mut stmt = conn
                    .prepare("SELECT * FROM conversations WHERE id=?1 AND account_id=?2 AND (?3 IS NULL OR project_id=?3)")?;
                let mut rows = stmt.query(params![conv_id, who.id, project_id])?;
                rows.next()?.map(|r| doc(conn, r, &who, with_messages)).transpose()
            })
            .await
    }

    pub async fn conv_project_id(&self, account: String, conv_id: String) -> Result<Option<String>> {
        self.store
            .readers
            .read(move |conn| {
                conn.query_row("SELECT project_id FROM conversations WHERE id=?1 AND account_id=?2", [&conv_id, &account], |r| r.get(0))
                    .optional()
            })
            .await
    }

    /// Whose chat this is (any account), or None when there is no such chat.
    pub async fn conv_account(&self, conv_id: String) -> Result<Option<String>> {
        self.store
            .readers
            .read(move |conn| conn.query_row("SELECT account_id FROM conversations WHERE id=?1", [&conv_id], |r| r.get(0)).optional())
            .await
    }

    pub async fn conv_list(&self, who: Who, project_id: String, with_messages: bool) -> Result<Vec<Value>> {
        self.store
            .readers
            .read(move |conn| {
                conn.prepare("SELECT * FROM conversations WHERE account_id=?1 AND project_id=?2 ORDER BY sort_order, updated DESC, id")?
                    .query_map([&who.id, &project_id], |r| doc(conn, r, &who, with_messages))?
                    .collect()
            })
            .await
    }

    /// `[(project id, chat), ...]`.
    pub async fn conv_list_all_projects(&self, who: Who, with_messages: bool) -> Result<Vec<(String, Value)>> {
        self.store
            .readers
            .read(move |conn| {
                conn.prepare("SELECT * FROM conversations WHERE account_id=?1 ORDER BY project_id, id")?
                    .query_map([&who.id], |r| Ok((text(r, "project_id")?, doc(conn, r, &who, with_messages)?)))?
                    .collect()
            })
            .await
    }

    /// Chats of every account in one project: AppData upkeep keeps a project's folder
    /// (attachments) while any account still has a chat in it.
    pub async fn conv_count(&self, project_id: String) -> Result<i64> {
        self.store
            .readers
            .read(move |conn| conn.query_row("SELECT count(*) FROM conversations WHERE project_id=?1", [&project_id], |r| r.get(0)))
            .await
    }

    /// The stored chat (no messages) when its ducky settings changed after `edited`, else None.
    pub async fn conv_if_settings_newer(&self, who: Who, conv_id: String, edited: f64) -> Result<Option<Value>> {
        self.store
            .readers
            .read(move |conn| {
                let mut stmt = conn.prepare("SELECT * FROM conversations WHERE id=?1 AND account_id=?2")?;
                let mut rows = stmt.query([&conv_id, &who.id])?;
                let Some(r) = rows.next()? else { return Ok(None) };
                let state = text(r, "state")?;
                let state = if state.is_empty() { "{}".to_string() } else { opened(&state, &who)? };
                let newer = match serde_json::from_str::<Value>(&state) {
                    Ok(Value::Object(state)) => {
                        float(state.get("settings_edited").unwrap_or(&Value::Null)).is_some_and(|at| at > edited)
                    }
                    _ => false,
                };
                if newer { doc(conn, r, &who, false).map(Some) } else { Ok(None) }
            })
            .await
    }

    /// The sidebar's two counters, after a save that synced messages.
    pub async fn conv_set_counts(&self, account: String, conv_id: String, tool_call_count: i64, file_count: i64) -> Result<()> {
        self.store
            .writer
            .write("chats::conv_set_counts", move |conn| {
                conn.execute(
                    "UPDATE conversations SET tool_call_count=?1, file_count=?2 WHERE id=?3 AND account_id=?4",
                    params![tool_call_count, file_count, conv_id, account],
                )?;
                Ok(())
            })
            .await
    }

    /// (message count, any assistant row) without loading bodies.
    pub async fn conv_message_summary(&self, account: String, conv_id: String) -> Result<(i64, bool)> {
        self.store
            .readers
            .read(move |conn| {
                conn.query_row(
                    "SELECT count(m.id), coalesce(max(m.role='assistant'), 0) FROM messages m \
                     JOIN conversations c ON c.id = m.conv_id WHERE m.conv_id=?1 AND c.account_id=?2",
                    [&conv_id, &account],
                    |r| Ok((r.get(0)?, r.get::<_, i64>(1)? != 0)),
                )
            })
            .await
    }

    /// The chat's message revision, or None when it isn't this account's.
    pub async fn conv_msg_rev(&self, account: String, conv_id: String) -> Result<Option<i64>> {
        self.store.readers.read(move |conn| msg_rev(conn, &conv_id, &account)).await
    }

    /// `(msg_rev, row count, row hash per seq from seq on)` from one read snapshot,
    /// reading no row body. `(None, 0, [])` when the chat isn't this account's.
    pub async fn message_hashes_since(&self, account: String, conv_id: String, seq: i64) -> Result<(Option<i64>, i64, Vec<String>)> {
        self.store
            .readers
            .read(move |conn| {
                let _snapshot = conn.unchecked_transaction()?;
                let Some(rev) = msg_rev(conn, &conv_id, &account)? else { return Ok((None, 0, Vec::new())) };
                let hashes = conn
                    .prepare("SELECT hash FROM messages WHERE conv_id=?1 AND seq>=?2 ORDER BY seq")?
                    .query_map(params![conv_id, seq.max(0)], |r| r.get(0))?
                    .collect::<rusqlite::Result<_>>()?;
                Ok((Some(rev), row_count(conn, &conv_id)?, hashes))
            })
            .await
    }

    /// Delete one of the account's chats (another account's is left alone).
    pub async fn conv_delete(&self, account: String, conv_id: String) -> Result<bool> {
        self.store
            .writer
            .write("chats::conv_delete", move |conn| {
                if !owned(conn, &conv_id, &account)? {
                    return Ok(false);
                }
                conn.execute("DELETE FROM messages WHERE conv_id=?1", [&conv_id])?;
                let gone = conn.execute("DELETE FROM conversations WHERE id=?1 AND account_id=?2", [&conv_id, &account])?;
                mark_deleted(conn, DELETED_CHAT, &[conv_id], now())?;
                Ok(gone > 0)
            })
            .await
    }

    /// Chat ids by the chat that started them.
    pub async fn conv_ids_by_parent(&self, account: String, project_id: String) -> Result<Map<String, Value>> {
        self.store
            .readers
            .read(move |conn| {
                let mut stmt = conn.prepare(
                    "SELECT parent_conv_id, id FROM conversations WHERE account_id=?1 AND project_id=?2 AND parent_conv_id<>''",
                )?;
                let mut rows = stmt.query([&account, &project_id])?;
                let mut out = Map::new();
                while let Some(r) = rows.next()? {
                    let (parent, id): (String, String) = (r.get(0)?, r.get(1)?);
                    if let Some(ids) = out.entry(parent).or_insert_with(|| json!([])).as_array_mut() {
                        ids.push(json!(id));
                    }
                }
                Ok(out)
            })
            .await
    }

    pub async fn conv_max_sort_order(&self, account: String, project_id: String, folder_id: String) -> Result<f64> {
        self.store
            .readers
            .read(move |conn| {
                let max: Option<f64> = conn.query_row(
                    "SELECT max(sort_order) FROM conversations WHERE account_id=?1 AND project_id=?2 AND folder_id=?3",
                    [&account, &project_id, &folder_id],
                    |r| r.get(0),
                )?;
                Ok(max.unwrap_or(-1.0))
            })
            .await
    }

    pub async fn has_rows(&self) -> Result<bool> {
        self.store.readers.read(|conn| conn.prepare("SELECT 1 FROM conversations LIMIT 1")?.exists([])).await
    }
}
