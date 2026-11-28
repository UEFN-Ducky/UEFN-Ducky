//! `plugin_kv`: Store plugin cache documents and UI prefs (`backend/store/repos/plugin_kv.py`).
//!
//! Rows are read fresh on every call (coding agents in other processes write the same
//! keys). A sensitive value is encrypted by the caller and stored as its base64 text
//! with `encrypted=1`. Every row belongs to one `(account, scope)`; `UNCLAIMED` is where
//! legacy rows wait until [`claim_unclaimed`] hands them to an account.

use rusqlite::{params, OptionalExtension};
use serde_json::{json, Map, Value};

use super::pyjson::dumps;
use super::{now, Result, Store};

pub const PREFS_KEY: &str = "__prefs__";
pub const UNCLAIMED: &str = "";

/// Whose rows: one plugin's, in one scope of one account.
#[derive(Clone)]
pub struct Owner {
    pub account: String,
    pub scope: String,
    pub plugin_id: String,
}

const MINE: &str = "account_id=?1 AND scope_id=?2 AND plugin_id=?3";

/// `(value, encrypted)`. An encrypted value comes back as its base64 text.
pub async fn get(store: &Store, of: Owner, key: String) -> Result<(Value, bool)> {
    store
        .readers
        .read(move |conn| {
            let row: Option<(String, i64)> = conn
                .query_row(&format!("SELECT value, encrypted FROM plugin_kv WHERE {MINE} AND key=?4"), params![of.account, of.scope, of.plugin_id, key], |r| {
                    Ok((r.get(0)?, r.get(1)?))
                })
                .optional()?;
            Ok(match row {
                None => (Value::Null, false),
                Some((text, 0)) => (serde_json::from_str(&text).unwrap_or(Value::Null), false),
                Some((text, _)) => (json!(text), true),
            })
        })
        .await
}

/// Store `value` as JSON, or `encrypted_b64` (the value sealed by the caller) as it is.
pub async fn set(store: &Store, of: Owner, key: String, value: Value, encrypted_b64: Option<String>) -> Result<()> {
    store
        .writer
        .write("plugin_kv::set", move |conn| {
            let encrypted = i64::from(encrypted_b64.is_some());
            let stored = encrypted_b64.clone().unwrap_or_else(|| dumps(&value));
            conn.execute(
                "INSERT INTO plugin_kv(account_id, scope_id, plugin_id, key, value, encrypted, updated) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7) ON CONFLICT(account_id, scope_id, plugin_id, key) DO UPDATE SET \
                 value=excluded.value, encrypted=excluded.encrypted, updated=excluded.updated",
                params![of.account, of.scope, of.plugin_id, key, stored, encrypted, now()],
            )
            .map(|_| ())
        })
        .await
}

pub async fn delete(store: &Store, of: Owner, key: String) -> Result<bool> {
    store
        .writer
        .write("plugin_kv::delete", move |conn| {
            Ok(conn.execute(&format!("DELETE FROM plugin_kv WHERE {MINE} AND key=?4"), params![of.account, of.scope, of.plugin_id, key])? > 0)
        })
        .await
}

/// The rows whose key starts with exactly the prefix (`_` and `%` are no wildcards),
/// never the prefs row. `?4` is the prefix's length in characters, `?5` the prefix.
const STARTING: &str = "account_id=?1 AND scope_id=?2 AND plugin_id=?3 AND substr(key, 1, ?4)=?5 AND key<>'__prefs__'";

pub async fn keys(store: &Store, of: Owner, prefix: String) -> Result<Vec<String>> {
    store
        .readers
        .read(move |conn| {
            conn.prepare(&format!("SELECT key FROM plugin_kv WHERE {STARTING} ORDER BY key"))?
                .query_map(params![of.account, of.scope, of.plugin_id, prefix.chars().count() as i64, prefix], |r| r.get(0))?
                .collect()
        })
        .await
}

/// Delete the keys starting with `prefix`: the keys that went.
pub async fn delete_prefix(store: &Store, of: Owner, prefix: String) -> Result<Vec<String>> {
    let cleared = keys(store, of.clone(), prefix.clone()).await?;
    if !cleared.is_empty() {
        store
            .writer
            .write("plugin_kv::delete_prefix", move |conn| {
                conn.execute(&format!("DELETE FROM plugin_kv WHERE {STARTING}"), params![of.account, of.scope, of.plugin_id, prefix.chars().count() as i64, prefix])
            })
            .await?;
    }
    Ok(cleared)
}

/// plugin_id -> `[stored value, encrypted]` of every prefs row; the caller unseals.
pub async fn all_prefs(store: &Store, account: String, scope: String) -> Result<Map<String, Value>> {
    store
        .readers
        .read(move |conn| {
            conn.prepare("SELECT plugin_id, value, encrypted FROM plugin_kv WHERE account_id=?1 AND scope_id=?2 AND key=?3 ORDER BY plugin_id")?
                .query_map(params![account, scope, PREFS_KEY], |r| Ok((r.get::<_, String>(0)?, json!([r.get::<_, String>(1)?, r.get::<_, bool>(2)?]))))?
                .collect()
        })
        .await
}

pub async fn set_prefs(store: &Store, of: Owner, sealed: String) -> Result<()> {
    set(store, of, PREFS_KEY.into(), Value::Null, Some(sealed)).await
}

pub async fn has_rows(store: &Store) -> Result<bool> {
    store.readers.read(|conn| conn.prepare("SELECT 1 FROM plugin_kv LIMIT 1")?.exists([])).await
}

pub async fn has_unclaimed(store: &Store) -> Result<bool> {
    store.readers.read(|conn| conn.prepare("SELECT 1 FROM plugin_kv WHERE account_id=?1 LIMIT 1")?.exists([UNCLAIMED])).await
}

/// Legacy rows (account `''`) become `account`'s. A key the account already has keeps
/// the account's row. Answers how many rows moved.
pub async fn claim_unclaimed(store: &Store, account: String) -> Result<usize> {
    store
        .writer
        .write("plugin_kv::claim_unclaimed", move |conn| {
            let moved = conn.execute("UPDATE OR IGNORE plugin_kv SET account_id=?1 WHERE account_id=?2", [&account, UNCLAIMED])?;
            conn.execute("DELETE FROM plugin_kv WHERE account_id=?1", [UNCLAIMED])?;
            Ok(moved)
        })
        .await
}

/// `[plugin_id, scope, key, value, encrypted]` of the account's rows not yet sealed
/// (whose value does not start with `prefix`).
pub async fn unsealed_rows(store: &Store, account: String, prefix: String) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            conn.prepare("SELECT plugin_id, scope_id, key, value, encrypted FROM plugin_kv WHERE account_id=?1 AND value NOT LIKE ?2")?
                .query_map([account, prefix + "%"], |r| {
                    Ok(json!([r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?, r.get::<_, String>(3)?, r.get::<_, i64>(4)?]))
                })?
                .collect()
        })
        .await
}

/// Swap one row's value for its sealed form, only if nobody changed it meanwhile.
pub async fn replace_value(store: &Store, of: Owner, key: String, value: String, old: String) -> Result<bool> {
    store
        .writer
        .write("plugin_kv::replace_value", move |conn| {
            let sql = format!("UPDATE plugin_kv SET value=?5, encrypted=1 WHERE {MINE} AND key=?4 AND value=?6");
            Ok(conn.execute(&sql, params![of.account, of.scope, of.plugin_id, key, value, old])? > 0)
        })
        .await
}

pub async fn delete_scope(store: &Store, account: String, scope: String) -> Result<()> {
    store
        .writer
        .write("plugin_kv::delete_scope", move |conn| conn.execute("DELETE FROM plugin_kv WHERE account_id=?1 AND scope_id=?2", [account, scope]).map(|_| ()))
        .await
}
