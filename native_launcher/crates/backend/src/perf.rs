//! Notable perf events, kept as `events` rows of kind `perf` next to the Python
//! backend's (`frontend/perf_trace.py`: `trace`, the flush and the hourly trim).
//!
//! [`trace`] never blocks and never fails its caller: it only queues a row. [`run`]
//! inserts the queued rows in one transaction every [`REPORT_INTERVAL`].
//!
//! ponytail: no in-memory ring and no report documents; those are what the panel's
//! perf methods read, and move with them.

use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde_json::{json, Map, Value};

use crate::store::pyjson::truthy;
use crate::store::{events, now, Result, Store};

pub const REPORT_INTERVAL: Duration = Duration::from_secs(15);
const TRIM_EVERY: Duration = Duration::from_secs(3600);
const MAX_PENDING_ROWS: usize = 5000;
const KEEP_ROWS: i64 = 50_000;
const KEEP_SECONDS: f64 = 7.0 * 86400.0;
const QUEUE_DEPTH_NOTABLE: f64 = 8.0;
const PAYLOAD_BYTES_NOTABLE: f64 = 32_768.0;

/// Notable events waiting for [`flush`].
static PENDING: Mutex<Vec<Value>> = Mutex::new(Vec::new());
static LAST_TRIM: Mutex<Option<Instant>> = Mutex::new(None);

/// This process's rows: `source` ends in its pid, as the Python sessions' do.
fn session() -> &'static str {
    static SESSION: OnceLock<String> = OnceLock::new();
    SESSION.get_or_init(|| format!("session-rs-{}-{}", now() as u64, std::process::id()))
}

/// Persist when the duration reaches this (`perf_trace.SLOW_MS`).
fn slow_ms(kind: &str) -> f64 {
    match kind {
        "ui_js" => 16.0,
        "push_flush" => 25.0,
        "tool_push" | "ui_stall" | "ui_frame" | "ui_js_stall" | "boot" => 0.0,
        "listener_cmd" => 100.0,
        _ => 50.0,
    }
}

fn should_persist(kind: &str, duration_ms: f64, meta: &Map<String, Value>) -> bool {
    let number = |name: &str| meta.get(name).and_then(Value::as_f64).unwrap_or(0.0);
    let nbytes = ["payload_bytes", "js_bytes", "result_bytes"].into_iter().find(|name| meta.get(*name).is_some_and(truthy)).map_or(0.0, number);
    duration_ms >= slow_ms(kind)
        || number("queue_depth") >= QUEUE_DEPTH_NOTABLE
        || nbytes >= if kind == "ui_js" { 512.0 * 1024.0 } else { PAYLOAD_BYTES_NOTABLE }
        || number("event_count") >= 50.0
        || number("oldest_event_age_ms") >= 100.0
}

/// Record one event; it is stored when notable. `meta` is a JSON object of extra fields.
pub fn trace(kind: &str, name: &str, duration_ms: f64, meta: Value) {
    let meta = meta.as_object().cloned().unwrap_or_default();
    if !should_persist(kind, duration_ms, &meta) {
        return;
    }
    let (ts, name) = (now(), if name.is_empty() { kind } else { name });
    let mut entry = Map::new();
    entry.insert("ts".into(), json!(ts));
    entry.insert("kind".into(), json!(kind));
    entry.insert("name".into(), json!(name));
    entry.insert("duration_ms".into(), json!((duration_ms * 1000.0).round() / 1000.0));
    entry.extend(meta.into_iter().filter(|(_, value)| !value.is_null()));
    let mut pending = PENDING.lock().unwrap_or_else(|e| e.into_inner());
    pending.push(json!({"ts": ts, "source": session(), "message": name, "payload": entry}));
    if pending.len() > MAX_PENDING_ROWS {
        let extra = pending.len() - MAX_PENDING_ROWS;
        pending.drain(..extra);
    }
}

/// Insert the pending events in one transaction; trim at most hourly. Answers how many
/// rows were stored.
pub async fn flush(store: &Store) -> Result<usize> {
    let rows = std::mem::take(&mut *PENDING.lock().unwrap_or_else(|e| e.into_inner()));
    if rows.is_empty() {
        return Ok(0);
    }
    let stored = events::insert_many(store, "perf".into(), rows).await?;
    let due = {
        let mut last = LAST_TRIM.lock().unwrap_or_else(|e| e.into_inner());
        let due = last.is_none_or(|at| at.elapsed() >= TRIM_EVERY);
        if due {
            *last = Some(Instant::now());
        }
        due
    };
    if due {
        // A trim that fails is tried again in an hour; the rows are stored either way.
        let _ = events::trim(store, "perf".into(), now() - KEEP_SECONDS, KEEP_ROWS).await;
    }
    Ok(stored)
}

pub fn waiting() -> bool {
    !PENDING.lock().unwrap_or_else(|e| e.into_inner()).is_empty()
}

/// Store the pending events every [`REPORT_INTERVAL`], for as long as the process runs.
/// While ducky.db is not there yet, they wait (the newest [`MAX_PENDING_ROWS`]).
pub async fn run(repos: std::sync::Arc<crate::store::http::Repos>) {
    let mut tick = tokio::time::interval(REPORT_INTERVAL);
    loop {
        tick.tick().await;
        if waiting() {
            if let Ok(store) = repos.store().await {
                let _ = flush(store).await;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_notable_events_are_kept_as_python_decides() {
        let none = Map::new();
        assert!(!should_persist("anything", 49.9, &none) && should_persist("anything", 50.0, &none));
        assert!(!should_persist("ui_js", 15.0, &none) && should_persist("ui_js", 16.0, &none));
        assert!(should_persist("boot", 0.0, &none) && !should_persist("listener_cmd", 99.0, &none));
        let meta = |value: Value| value.as_object().cloned().unwrap();
        assert!(should_persist("x", 0.0, &meta(json!({"queue_depth": 8}))));
        assert!(should_persist("x", 0.0, &meta(json!({"payload_bytes": 0, "js_bytes": 32_768}))));
        // evaluate_js ships ~100 KB all day: only really large ones count.
        assert!(!should_persist("ui_js", 0.0, &meta(json!({"js_bytes": 100_000}))));
        assert!(should_persist("ui_js", 0.0, &meta(json!({"js_bytes": 524_288}))));
        assert!(should_persist("x", 0.0, &meta(json!({"event_count": 50}))) && should_persist("x", 0.0, &meta(json!({"oldest_event_age_ms": 100.0}))));
        assert!(!should_persist("x", 0.0, &meta(json!({"queue_depth": 7, "event_count": 49, "result_bytes": 100}))));
    }
}
