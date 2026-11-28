//! The key -> JSON document tables (`backend/store/repos/kv.py`), and `meta`.

use rusqlite::{params, OptionalExtension};
use serde_json::{Map, Value};

use super::pyjson::dumps;
use super::{now, Error, Result, Store};

/// The tables with `key, value, updated` columns. A table name goes into the SQL text,
/// so only these are ever taken.
const TABLES: [&str; 3] = ["settings", "workspace_state", "cache_docs"];

fn known(table: &str) -> Result<&'static str> {
    TABLES.into_iter().find(|known| *known == table).ok_or_else(|| Error(format!("{table} is not a document table")))
}

/// A document; None when it is not there or is not JSON.
pub async fn get_doc(store: &Store, table: &str, key: String) -> Result<Option<Value>> {
    let table = known(table)?;
    store
        .readers
        .read(move |conn| {
            let text: Option<String> = conn.query_row(&format!("SELECT value FROM {table} WHERE key=?1"), [key], |r| r.get(0)).optional()?;
            Ok(text.and_then(|text| serde_json::from_str(&text).ok()))
        })
        .await
}

pub async fn set_doc(store: &Store, table: &str, key: String, value: Value) -> Result<()> {
    let table = known(table)?;
    store
        .writer
        .write("kv::set_doc", move |conn| {
            let sql = format!(
                "INSERT INTO {table}(key, value, updated) VALUES (?1, ?2, ?3) \
                 ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated"
            );
            conn.execute(&sql, params![key, dumps(&value), now()]).map(|_| ())
        })
        .await
}

pub async fn delete_doc(store: &Store, table: &str, key: String) -> Result<()> {
    let table = known(table)?;
    store.writer.write("kv::delete_doc", move |conn| conn.execute(&format!("DELETE FROM {table} WHERE key=?1"), [key]).map(|_| ())).await
}

/// Delete documents whose key starts with `prefix` (not empty), except the `keep` with
/// the highest keys: how many went.
pub async fn prune_docs(store: &Store, table: &str, prefix: String, keep: i64) -> Result<usize> {
    let table = known(table)?;
    // The key range is the prefix, on the key index.
    let mut upper: Vec<char> = prefix.chars().collect();
    let last = upper.pop().ok_or_else(|| Error("a prune needs a prefix".into()))?;
    upper.push(char::from_u32(last as u32 + 1).ok_or_else(|| Error("no key sorts after this prefix".into()))?);
    let upper: String = upper.into_iter().collect();
    let (from, to) = (prefix.clone(), upper.clone());
    let total: i64 = store
        .readers
        .read(move |conn| conn.query_row(&format!("SELECT count(*) FROM {table} WHERE key >= ?1 AND key < ?2"), [from, to], |r| r.get(0)))
        .await?;
    if total <= keep {
        return Ok(0);
    }
    store
        .writer
        .write("kv::prune_docs", move |conn| {
            let sql = format!(
                "DELETE FROM {table} WHERE key >= ?1 AND key < ?2 AND key NOT IN \
                 (SELECT key FROM {table} WHERE key >= ?1 AND key < ?2 ORDER BY key DESC LIMIT ?3)"
            );
            conn.execute(&sql, params![prefix, upper, keep])
        })
        .await
}

/// Every document whose key starts with `prefix`, by key; one that is not JSON is left out.
pub async fn list_docs(store: &Store, table: &str, prefix: String) -> Result<Map<String, Value>> {
    let table = known(table)?;
    store
        .readers
        .read(move |conn| {
            let mut out = Map::new();
            let mut rows = conn.prepare(&format!("SELECT key, value FROM {table} WHERE key LIKE ?1 ORDER BY key"))?;
            for row in rows.query_map([prefix + "%"], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))? {
                let (key, text) = row?;
                if let Ok(value) = serde_json::from_str(&text) {
                    out.insert(key, value);
                }
            }
            Ok(out)
        })
        .await
}

pub async fn meta_get(store: &Store, key: String) -> Result<Option<String>> {
    store.readers.read(move |conn| conn.query_row("SELECT value FROM meta WHERE key=?1", [key], |r| r.get(0)).optional()).await
}

pub async fn meta_set(store: &Store, key: String, value: String) -> Result<()> {
    store
        .writer
        .write("kv::meta_set", move |conn| {
            conn.execute(
                "INSERT INTO meta(key, value, updated) VALUES (?1, ?2, ?3) \
                 ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                params![key, value, now()],
            )
            .map(|_| ())
        })
        .await
}
