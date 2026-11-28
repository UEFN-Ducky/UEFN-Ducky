//! The no-freeze guard every route sits behind.
//!
//! A request that runs past its limit is dropped and answered with a JSON 504, so a caller
//! never hangs on it. One that ran longer than [`SLOW`] is counted and kept as a perf event.
//! Streams that stay open on purpose (agent turns, terminals) are websockets: the limit
//! covers the upgrade request only.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use axum::extract::Request;
use axum::http::StatusCode;
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::{Json, Router};
use serde_json::json;

/// How long a request may run.
pub const CALL_LIMIT: Duration = Duration::from_secs(30);
/// A request slower than this is worth a look.
pub const SLOW: Duration = Duration::from_secs(2);
/// Requests that ran longer than [`SLOW`] since this process started (shown by `/__rs/health`).
pub static SLOW_CALLS: AtomicU64 = AtomicU64::new(0);

/// The requests being answered now: ticket, path, since when.
static IN_FLIGHT: Mutex<Vec<(u64, String, Instant)>> = Mutex::new(Vec::new());
static TICKETS: AtomicU64 = AtomicU64::new(0);

/// One request in [`IN_FLIGHT`], until it is answered or its caller goes.
struct Flight(u64);

impl Flight {
    fn start(path: &str) -> Self {
        let ticket = TICKETS.fetch_add(1, Ordering::Relaxed);
        if let Ok(mut live) = IN_FLIGHT.lock() {
            live.push((ticket, path.to_owned(), Instant::now()));
        }
        Self(ticket)
    }
}

impl Drop for Flight {
    fn drop(&mut self) {
        if let Ok(mut live) = IN_FLIGHT.lock() {
            live.retain(|flight| flight.0 != self.0);
        }
    }
}

/// Every request in flight: its path and how long it has run (the health report's `inflight`).
pub fn in_flight() -> Vec<(String, Duration)> {
    IN_FLIGHT.lock().map(|live| live.iter().map(|(_, path, since)| (path.clone(), since.elapsed())).collect()).unwrap_or_default()
}

/// Put `router`'s routes under `limit`.
pub fn wrap(router: Router, limit: Duration) -> Router {
    router.layer(middleware::from_fn(move |req: Request, next: Next| guarded(req, next, limit)))
}

async fn guarded(req: Request, next: Next, limit: Duration) -> Response {
    let path = req.uri().path().to_owned();
    let _flight = Flight::start(&path);
    let started = Instant::now();
    let answer = tokio::time::timeout(limit, next.run(req)).await;
    let took = started.elapsed();
    if took >= SLOW.min(limit) {
        SLOW_CALLS.fetch_add(1, Ordering::Relaxed);
        crate::perf::trace("rs_slow", &path, took.as_secs_f64() * 1000.0, json!({"route": path}));
    }
    answer.unwrap_or_else(|_| timed_out(&path, limit))
}

fn timed_out(path: &str, limit: Duration) -> Response {
    let body = json!({"ok": false, "error": format!("timed out after {} ms", limit.as_millis()), "path": path});
    (StatusCode::GATEWAY_TIMEOUT, Json(body)).into_response()
}
