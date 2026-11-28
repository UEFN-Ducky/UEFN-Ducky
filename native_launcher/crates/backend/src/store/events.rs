//! `events`: the rolling logs (errors, activity, agent crashes, verse stats, perf)
//! (`backend/store/repos/events.py`).

use rusqlite::types::ValueRef;
use rusqlite::{params, params_from_iter, OptionalExtension, Row};
use serde_json::{json, Value};

use super::pyjson::{dumps, float, pystr, truthy};
use super::{Result, Store};

/// Rows a trim deletes per write, so the writer is never held for long.
const TRIM_SLICE: usize = 1000;
const INSERT: &str = "INSERT INTO events(kind, ts, source, message, payload) VALUES (?1, ?2, ?3, ?4, ?5)";

fn stored(payload: &Value) -> Option<String> {
    (!payload.is_null()).then(|| dumps(payload))
}

pub async fn insert(store: &Store, kind: String, ts: f64, source: String, message: String, payload: Value) -> Result<()> {
    store
        .writer
        .write("events::insert", move |conn| conn.execute(INSERT, params![kind, ts, source, message, stored(&payload)]).map(|_| ()))
        .await
}

/// Each row: `{"ts", "source", "message", "payload"}`, all optional.
pub async fn insert_many(store: &Store, kind: String, rows: Vec<Value>) -> Result<usize> {
    store
        .writer
        .write("events::insert_many", move |conn| {
            let mut insert = conn.prepare(INSERT)?;
            let text = |value: &Value| if truthy(value) { pystr(value) } else { String::new() };
            for row in &rows {
                let ts = Some(&row["ts"]).filter(|ts| truthy(ts)).and_then(float).unwrap_or(0.0);
                insert.execute(params![kind, ts, text(&row["source"]), text(&row["message"]), stored(&row["payload"])])?;
            }
            Ok(rows.len())
        })
        .await
}

fn real(r: &Row, name: &str) -> rusqlite::Result<f64> {
    Ok(match r.get_ref(name)? {
        ValueRef::Integer(n) => n as f64,
        ValueRef::Real(n) => n,
        _ => 0.0,
    })
}

fn row(r: &Row) -> rusqlite::Result<Value> {
    let mut out = json!({"ts": real(r, "ts")?, "source": r.get::<_, String>("source")?, "message": r.get::<_, String>("message")?});
    // A payload that is not JSON is left out.
    if let Some(payload) = r.get::<_, Option<String>>("payload")?.and_then(|text| serde_json::from_str::<Value>(&text).ok()) {
        out["payload"] = payload;
    }
    Ok(out)
}

/// The newest rows of a kind, newest first; `since` leaves out older ones.
pub async fn newest(store: &Store, kind: String, limit: i64, since: Option<f64>) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            // No lower bound is every row, as Python's query without one.
            let sql = "SELECT ts, source, message, payload FROM events WHERE kind=?1 AND (?2 IS NULL OR ts>=?2) \
                       ORDER BY ts DESC, id DESC LIMIT ?3";
            conn.prepare(sql)?.query_map(params![kind, since, limit], row)?.collect()
        })
        .await
}

/// [`newest`], only rows of one `source` (a plugin id for the plugin error kinds).
pub async fn newest_from(store: &Store, kind: String, source: String, limit: i64, since: Option<f64>) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            let sql = "SELECT ts, source, message, payload FROM events WHERE kind=?1 AND source=?2 AND ts>=?3 \
                       ORDER BY ts DESC, id DESC LIMIT ?4";
            conn.prepare(sql)?.query_map(params![kind, source, since.unwrap_or(0.0), limit], row)?.collect()
        })
        .await
}

pub async fn last_message(store: &Store, kind: String) -> Result<Option<String>> {
    store
        .readers
        .read(move |conn| conn.query_row("SELECT message FROM events WHERE kind=?1 ORDER BY id DESC LIMIT 1", [kind], |r| r.get(0)).optional())
        .await
}

pub async fn clear(store: &Store, kind: String) -> Result<()> {
    store.writer.write("events::clear", move |conn| conn.execute("DELETE FROM events WHERE kind=?1", [kind]).map(|_| ())).await
}

/// Delete in slices until a slice comes back short: how many rows went.
async fn delete_sliced(store: &Store, sql: &'static str, kind: &str, bound: rusqlite::types::Value) -> Result<usize> {
    let mut n = 0;
    loop {
        let (kind, bound) = (kind.to_string(), bound.clone());
        let did = store.writer.write("events::trim", move |conn| conn.execute(sql, params![kind, bound, TRIM_SLICE as i64])).await?;
        n += did;
        if did < TRIM_SLICE {
            return Ok(n);
        }
    }
}

/// Drop a kind's rows older than `older_than`, then all but its newest `keep`. Other
/// writes get their turn between slices; rows added meanwhile are newer and stay.
pub async fn trim(store: &Store, kind: String, older_than: f64, keep: i64) -> Result<usize> {
    let aged = "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE kind=?1 AND ts < ?2 LIMIT ?3)";
    let mut n = delete_sliced(store, aged, &kind, older_than.into()).await?;
    // The oldest id that stays: every row of the kind below it goes.
    let first_kept: Option<i64> = {
        let kind = kind.clone();
        store
            .readers
            .read(move |conn| match keep {
                0 => conn.query_row("SELECT max(id) + 1 FROM events WHERE kind=?1", [kind], |r| r.get(0)),
                // As SQLite reads a negative LIMIT: no limit, everything stays.
                keep if keep < 0 => Ok(None),
                keep => conn
                    .query_row("SELECT id FROM events WHERE kind=?1 ORDER BY id DESC LIMIT 1 OFFSET ?2", params![kind, keep - 1], |r| r.get(0))
                    .optional(),
            })
            .await?
    };
    if let Some(first_kept) = first_kept {
        let extra = "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE kind=?1 AND id < ?2 LIMIT ?3)";
        n += delete_sliced(store, extra, &kind, first_kept.into()).await?;
    }
    Ok(n)
}

pub async fn count(store: &Store, kind: String) -> Result<i64> {
    store.readers.read(move |conn| conn.query_row("SELECT count(*) FROM events WHERE kind=?1", [kind], |r| r.get(0))).await
}

/// The oldest rows of a kind with their ids (a queue's head).
pub async fn oldest(store: &Store, kind: String, limit: i64) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            conn.prepare("SELECT id, ts, source, message, payload FROM events WHERE kind=?1 ORDER BY id ASC LIMIT ?2")?
                .query_map(params![kind, limit], |r| {
                    let mut out = row(r)?;
                    out["id"] = json!(r.get::<_, i64>("id")?);
                    Ok(out)
                })?
                .collect()
        })
        .await
}

pub async fn delete_ids(store: &Store, ids: Vec<i64>) -> Result<usize> {
    if ids.is_empty() {
        return Ok(0);
    }
    store
        .writer
        .write("events::delete_ids", move |conn| {
            // ponytail: one statement per 500 ids; a queue's head is never near that.
            let mut n = 0;
            for some in ids.chunks(500) {
                let sql = format!("DELETE FROM events WHERE id IN ({})", vec!["?"; some.len()].join(","));
                n += conn.execute(&sql, params_from_iter(some))?;
            }
            Ok(n)
        })
        .await
}
