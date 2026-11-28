//! Per-account workflow state that never syncs (`backend/store/repos/workflows.py`):
//! the run log, last run and "Run on this PC" (`workflow_runtime`), plus one meta row
//! per shared space (`space_meta`): whether this account may change the space's
//! workflows, and the space's web page. The workflow docs themselves are plugin data.

use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Map, Value};

use super::ledger::text_or;
use super::pyjson::{float, truthy};
use super::{now, Result, Store};

fn runtime(conn: &Connection, account: &str, workflow_id: &str) -> rusqlite::Result<Value> {
    let row = conn
        .query_row("SELECT runs, last_run, run_here FROM workflow_runtime WHERE account_id=?1 AND workflow_id=?2", [account, workflow_id], |r| {
            Ok(json!({
                "runs": r.get::<_, Option<String>>(0)?.unwrap_or_default(),
                "last_run": r.get::<_, Option<f64>>(1)?.unwrap_or(0.0),
                "run_here": r.get::<_, Option<i64>>(2)?.unwrap_or(0) != 0,
            }))
        })
        .optional()?;
    Ok(row.unwrap_or_else(|| json!({"runs": "", "last_run": 0.0, "run_here": false})))
}

/// `{runs, last_run, run_here}`; a workflow never run reads as empty.
pub async fn runtime_get(store: &Store, account: String, workflow_id: String) -> Result<Value> {
    store.readers.read(move |conn| runtime(conn, &account, &workflow_id)).await
}

/// Merge `fields` (runs, last_run, run_here) into the workflow's row.
pub async fn runtime_put(store: &Store, account: String, workflow_id: String, fields: Map<String, Value>) -> Result<()> {
    store
        .writer
        .write("workflows::runtime_put", move |conn| {
            let mut row = runtime(conn, &account, &workflow_id)?;
            for (name, value) in &fields {
                row[name.as_str()] = value.clone();
            }
            conn.execute(
                "INSERT OR REPLACE INTO workflow_runtime(account_id, workflow_id, runs, last_run, run_here) VALUES (?1, ?2, ?3, ?4, ?5)",
                params![account, workflow_id, text_or(&row["runs"], ""), float(&row["last_run"]).unwrap_or(0.0), i64::from(truthy(&row["run_here"]))],
            )
            .map(|_| ())
        })
        .await
}

pub async fn runtime_delete(store: &Store, account: String, workflow_ids: Vec<String>) -> Result<()> {
    if workflow_ids.is_empty() {
        return Ok(());
    }
    store
        .writer
        .write("workflows::runtime_delete", move |conn| {
            workflow_ids.iter().try_for_each(|id| conn.execute("DELETE FROM workflow_runtime WHERE account_id=?1 AND workflow_id=?2", [&account, id]).map(|_| ()))
        })
        .await
}

pub async fn workflows_writable(store: &Store, account: String, space: String) -> Result<bool> {
    let writable: Option<Option<i64>> = store
        .readers
        .read(move |conn| conn.query_row("SELECT workflows_writable FROM space_meta WHERE account_id=?1 AND space_id=?2", [account, space], |r| r.get(0)).optional())
        .await?;
    Ok(writable.flatten().unwrap_or(0) != 0)
}

pub async fn meta_put(store: &Store, account: String, space: String, workflows_writable: bool, web_slug: String) -> Result<()> {
    store
        .writer
        .write("workflows::meta_put", move |conn| {
            conn.execute(
                "INSERT OR REPLACE INTO space_meta(account_id, space_id, workflows_writable, web_slug, updated) VALUES (?1, ?2, ?3, ?4, ?5)",
                params![account, space, i64::from(workflows_writable), web_slug, now()],
            )
            .map(|_| ())
        })
        .await
}

/// The space's web page id, `""` until the provider recorded it.
pub async fn web_slug(store: &Store, account: String, space: String) -> Result<String> {
    let slug: Option<String> =
        store.readers.read(move |conn| conn.query_row("SELECT web_slug FROM space_meta WHERE account_id=?1 AND space_id=?2", [account, space], |r| r.get(0)).optional()).await?;
    Ok(slug.unwrap_or_default())
}

/// Spaces this account has a sync row for (linked or listed once), in label order.
pub async fn synced_spaces(store: &Store, account: String) -> Result<Vec<String>> {
    store
        .readers
        .read(move |conn| {
            conn.prepare("SELECT space_id FROM scope_sync WHERE account_id=?1 ORDER BY label COLLATE NOCASE, space_id")?.query_map([account], |r| r.get(0))?.collect()
        })
        .await
}
