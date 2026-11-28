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

pub mod api;
pub mod guard;
pub mod perf;
pub mod store;

use std::future::Future;
use std::io;
use std::path::PathBuf;
use std::sync::atomic::Ordering;
use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
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
    routes(api::Api::new(Arc::new(store::http::Repos::new(data_dir)), api::methods()))
}

fn routes(api: Arc<api::Api>) -> Router {
    let mut routes = Router::new()
        .route("/__rs/health", get(health))
        .route("/__panel_api/{method}", post(api::router::call))
        .route("/__panel_event", post(api::router::publish))
        .with_state(Arc::clone(&api));
    // The repo route opens sealed rows for whoever asks: only under the parity tests,
    // which mark their environment with this.
    if std::env::var_os("DUCKY_TESTS_ISOLATED_APPDATA").is_some() {
        let store = Router::new().route("/__rs/store/{repo}/{function}", post(store::http::call));
        routes = routes.merge(store.with_state(Arc::clone(&api.repos)));
        routes = routes.merge(Router::new().route("/__rs/api/methods", get(api::router::reaches)).with_state(Arc::clone(&api)));
        // The account's agent limits are fetched after a sign-in, which only a real site answers.
        let caps = |State(api): State<Arc<api::Api>>| async move { Json(json!({"ok": api::account::fetch_agent_caps(&api).await.is_ok()})) };
        routes = routes.merge(Router::new().route("/__rs/account/agent_caps", post(caps)).with_state(Arc::clone(&api)));
        // Any usage event and a batch sent now: the page may only report its own few events.
        let usage = |State(api): State<Arc<api::Api>>, Path(what): Path<String>, Json(body): Json<Value>| async move { Json(api::usage::probe(&api, &what, &body).await) };
        routes = routes.merge(Router::new().route("/__rs/usage/{what}", post(usage)).with_state(Arc::clone(&api)));
    }
    // A window's poll waits 20 s by design, and a sign-in waits for the person to approve
    // its code: each sets its own limit, and is no slow call.
    let waits = Router::new().route("/__panel_events", get(api::router::events)).route("/__panel_api/duckyos_login", post(sign_in)).with_state(api);
    guard::wrap(routes, guard::CALL_LIMIT).merge(waits)
}

async fn sign_in(api: State<Arc<api::Api>>, headers: HeaderMap, body: Bytes) -> (StatusCode, Json<Value>) {
    api::router::call(api, Path("duckyos_login".to_string()), headers, body).await
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
    let repos = Arc::new(store::http::Repos::new(app_data_dir()));
    let perf = tokio::spawn(perf::run(Arc::clone(&repos)));
    let api = api::Api::new(Arc::clone(&repos), api::methods());
    // ponytail: the Python backend sends the usage queue until its own sender goes (two
    // senders would send every row twice); the launcher names this one then.
    let usage = std::env::var("DUCKY_USAGE_SENDER").is_ok_and(|who| who == "rust").then(|| tokio::spawn(api::usage::run(Arc::clone(&api))));
    let served = axum::serve(listener, routes(api)).with_graceful_shutdown(stop).await;
    perf.abort();
    if let Some(usage) = usage {
        usage.abort();
    }
    // What the last seconds traced is stored before the process ends.
    if perf::waiting() {
        if let Ok(store) = repos.store().await {
            let _ = perf::flush(store).await;
        }
    }
    served
}
