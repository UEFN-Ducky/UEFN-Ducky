//! `duckies` table: the person's duckies (`backend/store/repos/duckies.py`).
//!
//! Nothing here deletes a row. Removing a shipped ducky archives it, deleting a custom
//! one stamps `deleted` and keeps the row, and every write first copies the row it
//! replaces into `duckies_history`. Rows belong to an account and their data is sealed
//! for it: [`Who`] is that account, with the secret its rows are sealed with.

use rusqlite::{params, Connection, Row};
use serde_json::{json, Value};

use super::pyjson::{dumps, sorted, truthy};
use super::{now, seal, Result, Store};

const COLS: &str = "ducky_id, kind, data, archived, deleted, created, updated";

/// An account and the entropy its rows are sealed with (`account::entropy`).
#[derive(Clone)]
pub struct Who {
    pub id: String,
    pub entropy: Vec<u8>,
}

/// A stored `data` value as an object: sealed ones are opened, rows from before pass as
/// they are, anything that is not a JSON object is `{}`.
fn data(stored: &str, who: &Who) -> Result<Value> {
    let text = match stored {
        "" => "{}".to_string(),
        sealed if seal::is_sealed(sealed) => seal::open_text(sealed, &who.entropy)?,
        plain => plain.to_string(),
    };
    Ok(serde_json::from_str::<Value>(&text).ok().filter(Value::is_object).unwrap_or_else(|| json!({})))
}

fn sealed(data: &Value, who: &Who) -> Result<String> {
    seal::seal_text(&dumps(data), &who.entropy)
}

fn row(r: &Row, who: &Who) -> rusqlite::Result<Value> {
    Ok(json!({
        "id": r.get::<_, String>(0)?,
        "kind": r.get::<_, String>(1)?,
        "data": data(&r.get::<_, String>(2)?, who)?,
        "archived": r.get::<_, Option<i64>>(3)?.unwrap_or(0) != 0,
        "deleted": r.get::<_, Option<f64>>(4)?.unwrap_or(0.0),
        "created": r.get::<_, Option<f64>>(5)?.unwrap_or(0.0),
        "updated": r.get::<_, Option<f64>>(6)?.unwrap_or(0.0),
    }))
}

/// Every ducky row of the account, oldest first (the order the person made them).
pub async fn list_all(store: &Store, who: Who, include_deleted: bool) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            let only_live = if include_deleted { "" } else { " AND deleted = 0" };
            let sql = format!("SELECT {COLS} FROM duckies WHERE account_id=?1{only_live} ORDER BY created, ducky_id");
            conn.prepare(&sql)?.query_map([&who.id], |r| row(r, &who))?.collect()
        })
        .await
}

pub async fn get(store: &Store, who: Who, ducky_id: String) -> Result<Option<Value>> {
    store
        .readers
        .read(move |conn| {
            let sql = format!("SELECT {COLS} FROM duckies WHERE account_id=?1 AND ducky_id=?2");
            let mut stmt = conn.prepare(&sql)?;
            let mut rows = stmt.query([&who.id, &ducky_id])?;
            rows.next()?.map(|r| row(r, &who)).transpose()
        })
        .await
}

/// Earlier copies of one ducky, newest first.
pub async fn history(store: &Store, who: Who, ducky_id: String) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            conn.prepare(
                "SELECT seq, kind, data, archived, deleted, saved FROM duckies_history \
                 WHERE account_id=?1 AND id=?2 ORDER BY seq DESC",
            )?
            .query_map([&who.id, &ducky_id], |r| {
                Ok(json!({
                    "seq": r.get::<_, i64>(0)?,
                    "kind": r.get::<_, String>(1)?,
                    "data": data(&r.get::<_, String>(2)?, &who)?,
                    "archived": r.get::<_, Option<i64>>(3)?.unwrap_or(0) != 0,
                    "deleted": r.get::<_, Option<f64>>(4)?.unwrap_or(0.0),
                    "saved": r.get::<_, f64>(5)?,
                }))
            })?
            .collect()
        })
        .await
}

fn keep_previous(conn: &Connection, account: &str, ducky_id: &str, now: f64) -> rusqlite::Result<()> {
    conn.execute(
        "INSERT INTO duckies_history(account_id, id, kind, data, archived, deleted, saved) \
         SELECT account_id, ducky_id, kind, data, archived, deleted, ?1 FROM duckies WHERE account_id=?2 AND ducky_id=?3",
        params![now, account, ducky_id],
    )?;
    Ok(())
}

struct New<'a> {
    ducky_id: &'a str,
    kind: &'a str,
    data: &'a str,
    archived: bool,
    deleted: f64,
    created: f64,
    updated: f64,
    /// An id the account already has is left as it is.
    ignore: bool,
}

fn insert(conn: &Connection, account: &str, new: New) -> rusqlite::Result<usize> {
    let verb = if new.ignore { "INSERT OR IGNORE" } else { "INSERT" };
    conn.execute(
        &format!(
            "{verb} INTO duckies(id, account_id, ducky_id, kind, data, archived, deleted, created, updated) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)"
        ),
        params![
            format!("{account}~{}", new.ducky_id),
            account,
            new.ducky_id,
            new.kind,
            new.data,
            new.archived as i64,
            new.deleted,
            new.created,
            new.updated
        ],
    )
}

/// Sealing blocks (a system call): done before the write, never inside the transaction.
async fn seal_first(data: Value, who: &Who) -> Result<String> {
    let who = who.clone();
    tokio::task::spawn_blocking(move || sealed(&data, &who))
        .await
        .unwrap_or_else(|_| Err(super::Error("sealing panicked".into())))
}

/// Write one ducky's fields. Archived and deleted state is left as it is.
pub async fn save(store: &Store, who: Who, ducky_id: String, kind: String, data: Value) -> Result<()> {
    let sealed = seal_first(data, &who).await?;
    store
        .writer
        .write("duckies::save", move |conn| {
            let now = now();
            keep_previous(conn, &who.id, &ducky_id, now)?;
            let updated = conn.execute(
                "UPDATE duckies SET data=?1, updated=?2 WHERE account_id=?3 AND ducky_id=?4",
                params![sealed, now, who.id, ducky_id],
            )?;
            if updated == 0 {
                let new = New { ducky_id: &ducky_id, kind: &kind, data: &sealed, archived: false, deleted: 0.0, created: now, updated: now, ignore: false };
                insert(conn, &who.id, new)?;
            }
            Ok(())
        })
        .await
}

/// Take another of the person's Duckies' copy of one ducky. Only when it changed there
/// after it changed here, and only its fields: archive and delete stay this computer's
/// own, and a new ducky comes in active. `updated` keeps the other Ducky's time, so the
/// two never pass the same copy back and forth. True when something was written.
pub async fn save_synced(store: &Store, who: Who, ducky_id: String, kind: String, data: Value, updated: f64) -> Result<bool> {
    let new = dumps(&sorted(&data));
    let sealed = {
        let (who, new) = (who.clone(), new.clone());
        tokio::task::spawn_blocking(move || seal::seal_text(&new, &who.entropy))
            .await
            .unwrap_or_else(|_| Err(super::Error("sealing panicked".into())))?
    };
    store
        .writer
        .write("duckies::save_synced", move |conn| {
            let mut stmt = conn.prepare("SELECT kind, data, updated FROM duckies WHERE account_id=?1 AND ducky_id=?2")?;
            let mut rows = stmt.query([&who.id, &ducky_id])?;
            let have = match rows.next()? {
                Some(r) => Some((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, Option<f64>>(2)?.unwrap_or(0.0))),
                None => None,
            };
            let Some((have_kind, have_data, have_updated)) = have else {
                let row = New { ducky_id: &ducky_id, kind: &kind, data: &sealed, archived: false, deleted: 0.0, created: updated, updated, ignore: false };
                insert(conn, &who.id, row)?;
                return Ok(true);
            };
            let same = dumps(&sorted(&self::data(&have_data, &who)?)) == new;
            if same || have_updated >= updated || have_kind != kind {
                return Ok(false);
            }
            keep_previous(conn, &who.id, &ducky_id, now())?;
            conn.execute(
                "UPDATE duckies SET data=?1, updated=?2 WHERE account_id=?3 AND ducky_id=?4",
                params![sealed, updated, who.id, ducky_id],
            )?;
            Ok(true)
        })
        .await
}

pub async fn set_archived(store: &Store, who: Who, ducky_id: String, kind: String, archived: bool) -> Result<()> {
    let empty = seal_first(json!({}), &who).await?;
    store
        .writer
        .write("duckies::set_archived", move |conn| {
            let now = now();
            keep_previous(conn, &who.id, &ducky_id, now)?;
            let updated = conn.execute(
                "UPDATE duckies SET archived=?1, updated=?2 WHERE account_id=?3 AND ducky_id=?4",
                params![archived as i64, now, who.id, ducky_id],
            )?;
            if updated == 0 {
                let new = New { ducky_id: &ducky_id, kind: &kind, data: &empty, archived, deleted: 0.0, created: now, updated: now, ignore: false };
                insert(conn, &who.id, new)?;
            }
            Ok(())
        })
        .await
}

/// Mark a ducky deleted (or bring it back). The row and its data stay. A custom ducky
/// must already have a row; a shipped one (`kind` = `bundled`) gets a row if it has none.
pub async fn set_deleted(store: &Store, who: Who, ducky_id: String, deleted: bool, kind: String) -> Result<bool> {
    let empty = seal_first(json!({}), &who).await?;
    store
        .writer
        .write("duckies::set_deleted", move |conn| {
            let now = now();
            let stamp = if deleted { now } else { 0.0 };
            keep_previous(conn, &who.id, &ducky_id, now)?;
            if kind != "bundled" {
                let changed = conn.execute(
                    "UPDATE duckies SET deleted=?1, updated=?2 WHERE account_id=?3 AND ducky_id=?4 AND kind='custom'",
                    params![stamp, now, who.id, ducky_id],
                )?;
                return Ok(changed > 0);
            }
            let mut changed = conn.execute(
                "UPDATE duckies SET deleted=?1, updated=?2 WHERE account_id=?3 AND ducky_id=?4",
                params![stamp, now, who.id, ducky_id],
            )?;
            if changed == 0 {
                let new = New { ducky_id: &ducky_id, kind: "bundled", data: &empty, archived: false, deleted: stamp, created: now, updated: now, ignore: false };
                changed = insert(conn, &who.id, new)?;
            }
            Ok(changed > 0)
        })
        .await
}

/// Importer entry: add rows whose id the account doesn't have yet. Never overwrites.
/// Each row: `{"id", "kind", "data", "archived", "created", "updated"}`.
pub async fn insert_missing(store: &Store, who: Who, rows: Vec<Value>) -> Result<usize> {
    let mut sealed_rows = Vec::with_capacity(rows.len());
    for row in rows {
        let data = if truthy(&row["data"]) { row["data"].clone() } else { json!({}) };
        let sealed = seal_first(data, &who).await?;
        sealed_rows.push((row, sealed));
    }
    store
        .writer
        .write("duckies::insert_missing", move |conn| {
            let text = |v: &Value| v.as_str().map(str::to_string).unwrap_or_else(|| v.to_string());
            let mut added = 0;
            for (row, sealed) in &sealed_rows {
                let created = row["created"].as_f64().unwrap_or(0.0);
                let updated = row["updated"].as_f64().filter(|at| *at != 0.0).unwrap_or(created);
                let (id, kind) = (text(&row["id"]), text(&row["kind"]));
                let new = New { ducky_id: &id, kind: &kind, data: sealed, archived: truthy(&row["archived"]), deleted: 0.0, created, updated, ignore: true };
                added += insert(conn, &who.id, new)?;
            }
            Ok(added)
        })
        .await
}
