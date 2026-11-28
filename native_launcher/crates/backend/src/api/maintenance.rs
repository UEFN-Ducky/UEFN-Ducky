//! Settings → App Data (`frontend/appdata_maintenance.py`): what is in Ducky's data
//! folder, clearing and deleting parts of it, each project's data, and the sweep.
//!
//! Every walk and delete runs on a blocking thread ([`disk`], [`sweep`]); a call reads
//! the rows it needs first.
//! ponytail: a sweep of a multi-GB database (its weekly full check, its daily copy)
//! outlasts the call's 30 s limit: the caller gets the limit's answer and the sweep
//! still finishes. Give the method its own limit when the page shows its result.
//! ponytail: the sweep at start (`start_appdata_maintenance_async`) is still the Python
//! backend's; two backends sweeping would check and copy the database twice.

mod disk;
mod sweep;

use std::collections::HashSet;
use std::path::Path;
use std::sync::Arc;

use rusqlite::types::ValueRef;
use rusqlite::Connection;
use serde_json::{json, Map, Value};

use super::project_files::given;
use super::settings::{failed, files_backend, imported, load};
use super::Api;
use crate::store::pyjson::{float_repr, pystr, truthy};
use crate::store::{account, projects, secrets, Result, Store};

type Fields = Map<String, Value>;

/// Remote Ducky's old tunnel token (`retire_cloudflare_tunnel`).
const TUNNEL_KEY: &str = "uefn-ducky/remote_named_tunnel";
/// Every table of ducky.db the page knows (`store_admin.TABLES`), in its order.
const TABLES: &[&str] = &[
    "folders", "conversations", "messages", "message_parts", "snapshots", "settings", "secrets", "projects", "workspace_state", "plugin_kv",
    "plugin_data", "scope_sync", "plugin_scopes", "space_meta", "mcp_servers", "meta", "runs", "run_entries", "run_seen", "path_index", "blobs",
    "file_versions", "watch_index", "plans", "tasks", "memory_entries", "verse_diagnostics", "digest_files", "digest_lines", "events", "usage_calls",
    "cache_docs", "captures", "search_docs", "search_vecs",
];

/// Run `work` on the data folder, off the async workers.
async fn on_disk<T: Send + 'static>(api: &Api, work: impl FnOnce(&Path) -> T + Send + 'static) -> std::result::Result<T, String> {
    let root = api.repos.data_dir().to_path_buf();
    tokio::task::spawn_blocking(move || work(&root)).await.map_err(|stopped| stopped.to_string())
}

/// The tables whose rows belong to a project.
fn project_tables(conn: &Connection) -> rusqlite::Result<Vec<&'static str>> {
    let mut found = Vec::new();
    for table in TABLES {
        if conn.prepare(&format!("SELECT 1 FROM pragma_table_info('{table}') WHERE name='project_id'"))?.exists([])? {
            found.push(*table);
        }
    }
    Ok(found)
}

/// `project_row_counts`: `{project id: {table: rows}}`.
async fn row_counts(store: &Store) -> Result<Fields> {
    store
        .readers
        .read(|conn| {
            let mut out = Fields::new();
            let mut count = |table: &str, sql: &str| -> rusqlite::Result<()> {
                let mut query = conn.prepare(sql)?;
                let mut rows = query.query([])?;
                while let Some(row) = rows.next()? {
                    // `str(project_id)`.
                    let project = match row.get_ref(0)? {
                        ValueRef::Null => "None".to_string(),
                        ValueRef::Integer(n) => n.to_string(),
                        ValueRef::Real(n) => float_repr(n),
                        ValueRef::Text(text) | ValueRef::Blob(text) => String::from_utf8_lossy(text).into_owned(),
                    };
                    out.entry(project).or_insert_with(|| json!({}))[table] = json!(row.get::<_, i64>(1)?);
                }
                Ok(())
            };
            for table in project_tables(conn)? {
                count(table, &format!("SELECT project_id, count(*) FROM {table} GROUP BY project_id"))?;
            }
            count("messages", "SELECT c.project_id, count(*) FROM messages m JOIN conversations c ON c.id = m.conv_id GROUP BY c.project_id")?;
            Ok(out)
        })
        .await
}

/// `delete_project_rows`: every row of one project, in one transaction; how many went.
/// Chats and folders go only for the signed-in account; another account's stay.
async fn delete_rows(store: &Store, project: String) -> Result<i64> {
    let account = account::current(store).await?.id;
    store
        .writer
        .write("maintenance::delete_rows", move |conn| {
            let count = |sql: &str, of: &[&String]| conn.query_row(sql, rusqlite::params_from_iter(of), |row| row.get::<_, i64>(0));
            // What goes with its parent (messages, run entries, seen marks) is counted first.
            let mut removed = count("SELECT count(*) FROM messages WHERE conv_id IN (SELECT id FROM conversations WHERE project_id=?1 AND account_id=?2)", &[&project, &account])?;
            for child in ["run_entries", "run_seen"] {
                removed += count(&format!("SELECT count(*) FROM {child} WHERE run_id IN (SELECT run_id FROM runs WHERE project_id=?1)"), &[&project])?;
            }
            for table in project_tables(conn)? {
                removed += if table == "conversations" || table == "folders" {
                    conn.execute(&format!("DELETE FROM {table} WHERE project_id=?1 AND account_id=?2"), [&project, &account])?
                } else {
                    conn.execute(&format!("DELETE FROM {table} WHERE project_id=?1"), [&project])?
                } as i64;
            }
            Ok(removed + conn.execute("DELETE FROM projects WHERE id=?1", [&project])? as i64)
        })
        .await
}

pub async fn appdata_overview(api: Arc<Api>, _args: Fields) -> std::result::Result<Value, String> {
    on_disk(&api, disk::overview).await
}

pub async fn appdata_children(api: Arc<Api>, args: Fields) -> std::result::Result<Value, String> {
    let rel = given(&args, "rel");
    on_disk(&api, move |root| disk::children(root, &rel)).await
}

/// Empty a folder (and keep it) or delete a file.
pub async fn appdata_clear(api: Arc<Api>, args: Fields) -> std::result::Result<Value, String> {
    let rel = given(&args, "rel");
    on_disk(&api, move |root| disk::clear(root, &rel)).await
}

pub async fn appdata_delete(api: Arc<Api>, args: Fields) -> std::result::Result<Value, String> {
    let rel = given(&args, "rel");
    on_disk(&api, move |root| disk::delete(root, &rel)).await
}

pub async fn appdata_clear_caches(api: Arc<Api>, _args: Fields) -> std::result::Result<Value, String> {
    on_disk(&api, disk::clear_caches).await
}

pub async fn appdata_projects(api: Arc<Api>, _args: Fields) -> std::result::Result<Value, String> {
    let settings = load(&api).await?;
    imported(&api, "projects", "recent_projects.json").await?;
    let store = api.repos.store().await.map_err(failed)?;
    let opened: Vec<String> = projects::opened_rows(store).await.map_err(failed)?.into_iter().map(|(path, _opened)| path).collect();
    let rows = row_counts(store).await.map_err(failed)?;
    let current = if truthy(settings.get("uefn_project_root")) { pystr(settings.get("uefn_project_root")) } else { String::new() };
    on_disk(&api, move |root| disk::projects(root, &opened, &current, &rows)).await
}

/// Everything Ducky keeps for one project: its folders, then its rows.
pub async fn appdata_delete_project(api: Arc<Api>, args: Fields) -> std::result::Result<Value, String> {
    let slug = given(&args, "slug");
    if !disk::plain_name(&slug) {
        return Ok(json!({"ok": true, "removed": 0}));
    }
    let folder = slug.clone();
    let folders = on_disk(&api, move |root| disk::delete_project(root, &folder)).await?;
    let store = api.repos.store().await.map_err(failed)?;
    // Rows that will not go are no reason to say the folders did not.
    let rows = delete_rows(store, slug.trim().to_string()).await.unwrap_or_else(|refused| {
        eprintln!("deleting a project's rows failed: {refused}");
        0
    });
    Ok(json!({"ok": true, "removed": folders + rows}))
}

/// `maintain_appdata`: the sweep, run now.
pub async fn appdata_sweep(api: Arc<Api>, _args: Fields) -> std::result::Result<Value, String> {
    if files_backend("settings") {
        return Err("settings are kept in files here (DUCKY_STORE_BACKEND)".into());
    }
    let store = api.repos.store().await.map_err(failed)?;
    let chats = |conn: &Connection| conn.prepare("SELECT DISTINCT project_id FROM conversations WHERE project_id IS NOT NULL")?.query_map([], |row| row.get(0))?.collect();
    let with_chats: HashSet<String> = store.readers.read(chats).await.map_err(failed)?;
    let (result, tunnel) = on_disk(&api, move |root| sweep::run(root, &with_chats)).await?;
    if tunnel {
        let _ = secrets::delete(store, TUNNEL_KEY.into()).await;
    }
    Ok(result)
}
