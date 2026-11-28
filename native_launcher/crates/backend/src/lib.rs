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
use std::path::PathBuf;
use std::sync::atomic::Ordering;
use std::sync::Arc;

use axum::http::StatusCode;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{json, Value};
use tokio::net::TcpListener;

/// The supervisor contract version this backend speaks.
pub const PROTOCOL: i64 = 1;

/// Ducky's data folder, where the Python backend looks for it (`frontend/app_paths.py`):
/// `%LOCALAPPDATA%\UEFN-Ducky`, `$XDG_DATA_HOME/UEFN-Ducky`, or
/// `~/Library/Application Support/UEFN-Ducky`.
pub fn app_data_dir() -> PathBuf {
    let dir = |name: &str| std::env::var_os(name).map(PathBuf::from).filter(|p| p.is_absolute());
    let home = || dir("HOME").or_else(|| dir("USERPROFILE")).unwrap_or_default();
    // The Python tests' own redirect, followed on every OS as they follow it.
    let test_dir = || dir("LOCALAPPDATA").filter(|_| std::env::var_os("DUCKY_TESTS_ISOLATED_APPDATA").is_some());
    let base = if cfg!(windows) {
        dir("LOCALAPPDATA").or_else(|| dir("APPDATA")).unwrap_or_else(|| home().join("AppData").join("Local"))
    } else if let Some(test) = test_dir() {
        test
    } else if cfg!(target_os = "macos") {
        home().join("Library").join("Application Support")
    } else {
        dir("XDG_DATA_HOME").unwrap_or_else(|| home().join(".local").join("share"))
    };
    base.join("UEFN-Ducky")
}

/// Every route, under the no-freeze guard. `data_dir` holds `ducky.db`.
pub fn router(data_dir: PathBuf) -> Router {
    let mut routes = Router::new().route("/__rs/health", get(health)).route("/__panel_api/{method}", post(panel_call));
    // The repo route opens sealed rows for whoever asks: only under the parity tests,
    // which mark their environment with this.
    if std::env::var_os("DUCKY_TESTS_ISOLATED_APPDATA").is_some() {
        routes = routes.route("/__rs/store/{repo}/{function}", post(store::http::call));
    }
    guard::wrap(routes.with_state(Arc::new(store::http::Repos::new(data_dir))), guard::CALL_LIMIT)
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
    axum::serve(listener, router(app_data_dir())).with_graceful_shutdown(stop).await
}
