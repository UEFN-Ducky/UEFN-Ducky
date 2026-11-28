//! `POST /__rs/store/<repo>/<function>`, body = the arguments by name. For the parity
//! tests only: the panel never calls a repo directly.
//!
//! Answers `{"ok": true, "result": ...}`, `{"ok": false, "error": ...}` when the store
//! refused, and 404 for a function that is not here.

use std::path::PathBuf;
use std::sync::Arc;

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::Json;
use serde_json::{json, Map, Value};
use tokio::sync::OnceCell;

use super::settings::Settings;
use super::{misc, Error, Result, Store};

/// The store and its repos, opened on the first call that needs them.
pub struct Repos {
    data_dir: PathBuf,
    open: OnceCell<Open>,
}

struct Open {
    store: Store,
    settings: Settings,
}

impl Repos {
    pub fn new(data_dir: PathBuf) -> Repos {
        Repos { data_dir, open: OnceCell::new() }
    }

    async fn open(&self) -> Result<&Open> {
        let path = self.data_dir.join("ducky.db");
        let opening = || async move {
            let store = tokio::task::spawn_blocking(move || {
                // The Python backend creates the file and owns its schema.
                if !path.is_file() {
                    return Err(Error(format!("{} is not there yet", path.display())));
                }
                Store::open(&path)
            })
            .await
            .unwrap_or_else(|_| Err(Error("opening the store panicked".into())))?;
            Ok(Open { settings: Settings::new(store.clone()), store })
        };
        self.open.get_or_try_init(opening).await
    }
}

fn text(args: &Value, name: &str) -> String {
    args[name].as_str().unwrap_or_default().to_string()
}

fn map(args: &Value, name: &str) -> Map<String, Value> {
    args[name].as_object().cloned().unwrap_or_default()
}

fn texts(args: &Value, name: &str) -> Vec<String> {
    args[name].as_array().into_iter().flatten().filter_map(|v| v.as_str().map(str::to_string)).collect()
}

pub async fn call(
    State(repos): State<Arc<Repos>>,
    Path((repo, function)): Path<(String, String)>,
    Json(args): Json<Value>,
) -> (StatusCode, Json<Value>) {
    let out = match repos.open().await {
        Ok(open) => dispatch(open, &repo, &function, &args).await,
        Err(err) => Err(err),
    };
    match out {
        Ok(Some(result)) => (StatusCode::OK, Json(json!({"ok": true, "result": result}))),
        Ok(None) => (StatusCode::NOT_FOUND, Json(json!({"ok": false, "error": "no such store function"}))),
        Err(err) => (StatusCode::OK, Json(json!({"ok": false, "error": err.0}))),
    }
}

async fn dispatch(open: &Open, repo: &str, function: &str, a: &Value) -> Result<Option<Value>> {
    let (s, settings) = (&open.store, &open.settings);
    Ok(Some(match (repo, function) {
        ("settings", "load_fields") => Value::Object(settings.load_fields().await?),
        ("settings", "load_fields_versioned") => json!(settings.load_fields_versioned().await?),
        ("settings", "save_fields") => {
            json!(settings.save_fields(map(a, "new_values"), map(a, "defaults"), a["origin_version"].as_u64()).await?)
        }
        ("settings", "replace_all") => json!(settings.replace_all(map(a, "values"), map(a, "defaults")).await?),
        ("settings", "has_rows") => json!(settings.has_rows().await?),
        ("misc", "mcp_servers_get") => Value::Object(misc::mcp_servers_get(s).await?),
        ("misc", "mcp_servers_replace") => json!(misc::mcp_servers_replace(s, map(a, "servers")).await?),
        ("misc", "mcp_servers_present") => json!(misc::mcp_servers_present(s).await?),
        ("misc", "capture_add") => {
            json!(misc::capture_add(s, text(a, "filename"), text(a, "prefix"), a["nbytes"].as_i64().unwrap_or(0)).await?)
        }
        ("misc", "capture_prune") => json!(misc::capture_prune(s, a["keep"].as_i64().unwrap_or(0)).await?),
        ("misc", "capture_names") => json!(misc::capture_names(s).await?),
        ("misc", "diag_get") => Value::Object(misc::diag_get(s, text(a, "project_id")).await?),
        ("misc", "diag_replace") => json!(misc::diag_replace(s, text(a, "project_id"), map(a, "files")).await?),
        ("misc", "diag_stamp") => json!(misc::diag_stamp(s, text(a, "project_id")).await?),
        ("misc", "digest_indexed_mtime") => json!(misc::digest_indexed_mtime(s, text(a, "path")).await?),
        ("misc", "digest_index") => {
            json!(misc::digest_index(s, text(a, "path"), a["mtime"].as_f64().unwrap_or(0.0), texts(a, "lines")).await?)
        }
        ("misc", "digest_search") => {
            let limit = a["limit"].as_i64().unwrap_or(2000);
            json!(misc::digest_search(s, text(a, "query"), texts(a, "paths"), limit).await?)
        }
        _ => return Ok(None),
    }))
}
