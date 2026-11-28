//! `automations` (legacy workflow rows, claimed into the data service by their owner)
//! and `workflow_versions` (saved copies per PC) (`backend/store/repos/automations.py`).

use aes_gcm::aead::rand_core::RngCore;
use aes_gcm::aead::OsRng;
use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::{json, Value};

use super::ledger::text_or;
use super::pyjson::{dumps, float, truthy};
use super::{Error, Result, Store};

fn no_graph() -> Value {
    json!({"nodes": [], "edges": []})
}

// --------------------------------------------------------------------------- legacy rows

/// Write a legacy row (the boot importer of old AppData JSON files uses this).
pub async fn put(store: &Store, doc: Value) -> Result<()> {
    if !doc["id"].is_string() {
        return Err(Error("a workflow row needs its id".into()));
    }
    store
        .writer
        .write("automations::put", move |conn| {
            let or = |name: &str, default: Value| if truthy(&doc[name]) { doc[name].clone() } else { default };
            // Enabled unless it says otherwise.
            let enabled = doc.get("enabled").is_none_or(truthy);
            conn.execute(
                "INSERT INTO automations(id, name, enabled, graph, runs, updated, last_run, kind, description) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 'automation', ?8) \
                 ON CONFLICT(id) DO UPDATE SET name=excluded.name, enabled=excluded.enabled, \
                 graph=excluded.graph, runs=excluded.runs, updated=excluded.updated, \
                 last_run=excluded.last_run, kind=excluded.kind, description=excluded.description",
                params![
                    text_or(&doc["id"], ""),
                    text_or(&doc["name"], ""),
                    i64::from(enabled),
                    dumps(&or("graph", no_graph())),
                    dumps(&or("runs", json!([]))),
                    float(&doc["updated"]).unwrap_or(0.0),
                    float(&doc["last_run"]).unwrap_or(0.0),
                    text_or(&doc["description"], ""),
                ],
            )
            .map(|_| ())
        })
        .await
}

pub async fn delete(store: &Store, workflow_id: String) -> Result<bool> {
    store.writer.write("automations::delete", move |conn| Ok(conn.execute("DELETE FROM automations WHERE id=?1", [workflow_id])? > 0)).await
}

pub async fn has_legacy(store: &Store) -> Result<bool> {
    store.readers.read(|conn| conn.prepare("SELECT 1 FROM automations LIMIT 1")?.exists([])).await
}

fn row(r: &Row) -> rusqlite::Result<Value> {
    let parsed = |at: usize| -> rusqlite::Result<Option<Value>> { Ok(serde_json::from_str(&r.get::<_, Option<String>>(at)?.unwrap_or_default()).ok()) };
    Ok(json!({
        "id": r.get::<_, String>(0)?,
        "name": r.get::<_, Option<String>>(1)?.unwrap_or_default(),
        "enabled": r.get::<_, Option<i64>>(2)?.unwrap_or(0) != 0,
        "graph": parsed(3)?.filter(Value::is_object).unwrap_or_else(no_graph),
        "runs": parsed(4)?.filter(Value::is_array).unwrap_or_else(|| json!([])),
        "updated": r.get::<_, Option<f64>>(5)?.unwrap_or(0.0),
        "last_run": r.get::<_, Option<f64>>(6)?.unwrap_or(0.0),
        "description": r.get::<_, Option<String>>(7)?.unwrap_or_default(),
    }))
}

pub async fn list_all(store: &Store) -> Result<Vec<Value>> {
    store
        .readers
        .read(|conn| {
            conn.prepare("SELECT id, name, enabled, graph, runs, updated, last_run, description FROM automations ORDER BY updated DESC, name")?
                .query_map([], row)?
                .collect()
        })
        .await
}

// --------------------------------------------------------------------------- saved versions

/// A random id, as Python's `str(uuid.uuid4())`.
fn uuid4() -> String {
    let mut bytes = [0u8; 16];
    OsRng.fill_bytes(&mut bytes);
    bytes[6] = bytes[6] & 0x0f | 0x40;
    bytes[8] = bytes[8] & 0x3f | 0x80;
    let hex: String = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
    format!("{}-{}-{}-{}-{}", &hex[..8], &hex[8..12], &hex[12..16], &hex[16..20], &hex[20..])
}

/// Inside a write. `snapshot` is sealed for the account that saved it (or legacy
/// plaintext JSON).
pub fn archive_in(conn: &Connection, workflow_id: &str, saved_at: f64, snapshot: &str) -> rusqlite::Result<()> {
    conn.execute("INSERT INTO workflow_versions(id, workflow_id, saved_at, snapshot) VALUES (?1, ?2, ?3, ?4)", params![uuid4(), workflow_id, saved_at, snapshot])
        .map(|_| ())
}

/// Inside a write: `new_id` gets a copy of every saved version of `old_id`, oldest
/// first (a workflow split off another keeps its History).
pub fn copy_versions_in(conn: &Connection, old_id: &str, new_id: &str) -> rusqlite::Result<()> {
    let versions: Vec<(Option<f64>, String)> = conn
        .prepare("SELECT saved_at, snapshot FROM workflow_versions WHERE workflow_id=?1 ORDER BY sequence")?
        .query_map([old_id], |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect::<rusqlite::Result<_>>()?;
    versions.iter().try_for_each(|(saved_at, snapshot)| archive_in(conn, new_id, saved_at.unwrap_or(0.0), snapshot))
}

pub async fn archive(store: &Store, workflow_id: String, saved_at: f64, snapshot: String) -> Result<()> {
    store.writer.write("automations::archive", move |conn| archive_in(conn, &workflow_id, saved_at, &snapshot)).await
}

pub async fn copy_versions(store: &Store, old_id: String, new_id: String) -> Result<()> {
    store.writer.write("automations::copy_versions", move |conn| copy_versions_in(conn, &old_id, &new_id)).await
}

pub async fn has_versions(store: &Store, workflow_id: String) -> Result<bool> {
    store.readers.read(move |conn| conn.prepare("SELECT 1 FROM workflow_versions WHERE workflow_id=?1 LIMIT 1")?.exists([workflow_id])).await
}

/// `(version_id, stored snapshot)`, newest first.
pub async fn version_rows(store: &Store, workflow_id: String) -> Result<Vec<(String, String)>> {
    store
        .readers
        .read(move |conn| {
            conn.prepare("SELECT id, snapshot FROM workflow_versions WHERE workflow_id=?1 ORDER BY sequence DESC")?
                .query_map([workflow_id], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect()
        })
        .await
}

pub async fn version_row(store: &Store, workflow_id: String, version_id: String) -> Result<Option<String>> {
    store
        .readers
        .read(move |conn| conn.query_row("SELECT snapshot FROM workflow_versions WHERE workflow_id=?1 AND id=?2", [workflow_id, version_id], |r| r.get(0)).optional())
        .await
}

/// Re-key and re-seal a workflow's versions (claim / import). `converted` is
/// `(version_id, new stored text)` for the rows the caller re-sealed from
/// [`version_rows`]; the others only move to `new_id`.
pub async fn reseal_versions(store: &Store, old_id: String, new_id: String, converted: Vec<(String, String)>) -> Result<()> {
    store
        .writer
        .write("automations::reseal_versions", move |conn| {
            for (version_id, text) in &converted {
                conn.execute("UPDATE workflow_versions SET snapshot=?1, workflow_id=?2 WHERE id=?3", params![text, new_id, version_id])?;
            }
            if old_id != new_id {
                conn.execute("UPDATE workflow_versions SET workflow_id=?1 WHERE workflow_id=?2", [&new_id, &old_id])?;
            }
            Ok(())
        })
        .await
}
