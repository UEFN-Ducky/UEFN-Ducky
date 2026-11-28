//! `usage_calls`: one row per LLM / coding-agent call (`backend/store/repos/usage.py`).

use rusqlite::params_from_iter;
use serde_json::Value;

use super::ledger::sql;
use super::pyjson::{float, truthy};
use super::{record, Error, Result, Store};

const COLUMNS: [&str; 11] = [
    "ts", "provider", "model", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "conv_id", "agent", "ducky_label", "cost_usd",
];

/// Record a call; with `retention_s`, rows older than that before it go in the same write.
pub async fn insert(store: &Store, entry: Value, retention_s: Option<f64>) -> Result<()> {
    let oldest = match retention_s.filter(|kept| *kept != 0.0) {
        None => None,
        Some(kept) => Some(Some(&entry["ts"]).filter(|ts| truthy(ts)).and_then(float).ok_or_else(|| Error("a usage row needs its ts".into()))? - kept),
    };
    store
        .writer
        .write("usage::insert", move |conn| {
            let sql_text = format!("INSERT INTO usage_calls({}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", COLUMNS.join(", "));
            conn.execute(&sql_text, params_from_iter(COLUMNS.iter().map(|name| sql(&entry[*name]))))?;
            if let Some(oldest) = oldest {
                conn.execute("DELETE FROM usage_calls WHERE ts < ?1", [oldest])?;
            }
            Ok(())
        })
        .await
}

/// The calls since `ts`, oldest first; a call with no cost has no `cost_usd`.
pub async fn since(store: &Store, ts: f64) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            conn.prepare(&format!("SELECT {} FROM usage_calls WHERE ts >= ?1 ORDER BY ts", COLUMNS.join(", ")))?
                .query_map([ts], |r| {
                    let mut row = record(r)?;
                    if row["cost_usd"].is_null() {
                        row.remove("cost_usd");
                    }
                    Ok(Value::Object(row))
                })?
                .collect()
        })
        .await
}

pub async fn count(store: &Store) -> Result<i64> {
    store.readers.read(|conn| conn.query_row("SELECT count(*) FROM usage_calls", [], |r| r.get(0))).await
}
