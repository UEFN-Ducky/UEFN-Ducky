//! Ducky's backend in Rust.
//!
//! The launcher's UI server sends it the paths in `server::RUST_PATHS`; every other path
//! still goes to the Python backend. It speaks the supervisor's contract: one hello line
//! on stdout, and it ends when stdin closes.
//!
//! No call can hold up another: each request is its own task under a time limit
//! ([`guard`]), and blocking work only runs inside `tokio::task::spawn_blocking`
//! (`clippy.toml` lists the calls that are refused anywhere else).

#![deny(clippy::await_holding_lock, clippy::disallowed_methods)]

pub mod guard;
pub mod store;

use std::future::Future;
use std::io;
use std::sync::atomic::Ordering;

use axum::http::StatusCode;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{json, Value};
use tokio::net::TcpListener;

/// The supervisor contract version this backend speaks.
pub const PROTOCOL: i64 = 1;

/// Every route, under the no-freeze guard.
pub fn router() -> Router {
    let routes = Router::new().route("/__rs/health", get(health)).route("/__panel_api/{method}", post(panel_call));
    guard::wrap(routes, guard::CALL_LIMIT)
}

/// A panel call (`POST /__panel_api/<method>`, body `{"args": [...]}`), answered in the
/// Python backend's envelope. No method has moved here yet, so each one is unknown:
/// Python's 404.
async fn panel_call() -> (StatusCode, Json<Value>) {
    (StatusCode::NOT_FOUND, Json(json!({"ok": false, "error": "method not allowed"})))
}

async fn health() -> Json<Value> {
    Json(json!({
        "ok": true,
        "version": env!("CARGO_PKG_VERSION"),
        "pid": std::process::id(),
        "slow_calls": guard::SLOW_CALLS.load(Ordering::Relaxed),
    }))
}

/// The line the supervisor waits for (`supervisor::protocol::Hello`).
pub fn hello_line(port: u16) -> String {
    json!({
        "t": "hello",
        "protocol": PROTOCOL,
        "port": port,
        "origin": format!("http://127.0.0.1:{port}"),
        "pid": std::process::id(),
        "app_version": env!("CARGO_PKG_VERSION"),
    })
    .to_string()
}

/// Serve until `stop` resolves; requests in flight finish first.
pub async fn serve(listener: TcpListener, stop: impl Future<Output = ()> + Send + 'static) -> io::Result<()> {
    axum::serve(listener, router()).with_graceful_shutdown(stop).await
}
