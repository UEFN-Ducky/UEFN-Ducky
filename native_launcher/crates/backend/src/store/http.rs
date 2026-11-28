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
use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use serde_json::{json, Map, Value};
use tokio::sync::OnceCell;

use super::settings::Settings;
use super::chat_messages::Save;
use super::chats::Chats;
use super::{automations, memory, plans, workflows};
use super::{account, blocking, duckies, ducky_mirrors, events, history, kv, ledger, misc, plugin_kv, projects, seal, search, usage, sealed_file, sealed_files, secrets, tokens, Error, Result, Store};

/// The store and its repos, opened on the first call that needs them.
pub struct Repos {
    data_dir: PathBuf,
    open: OnceCell<Open>,
}

struct Open {
    store: Store,
    settings: Settings,
    chats: Chats,
}

impl Repos {
    pub fn new(data_dir: PathBuf) -> Repos {
        Repos { data_dir, open: OnceCell::new() }
    }

    /// The store, once ducky.db is there.
    pub async fn store(&self) -> Result<&Store> {
        Ok(&self.open().await?.store)
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
            Ok(Open { settings: Settings::new(store.clone()), chats: Chats::new(store.clone()), store })
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

/// A bytes argument, sent as base64.
fn bytes(args: &Value, name: &str) -> Vec<u8> {
    BASE64.decode(args[name].as_str().unwrap_or_default()).unwrap_or_default()
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
        Ok(open) => dispatch(open, &repos.data_dir, &repo, &function, &args).await,
        Err(err) => Err(err),
    };
    match out {
        Ok(Some(result)) => (StatusCode::OK, Json(json!({"ok": true, "result": result}))),
        Ok(None) => (StatusCode::NOT_FOUND, Json(json!({"ok": false, "error": "no such store function"}))),
        Err(err) => (StatusCode::OK, Json(json!({"ok": false, "error": err.0}))),
    }
}

/// Named blobs, as `[[name, base64], ...]`.
fn named(blobs: Vec<(String, Vec<u8>)>) -> Value {
    json!(blobs.into_iter().map(|(name, blob)| (name, BASE64.encode(blob))).collect::<Vec<_>>())
}

/// The account signed in now, with the secret its rows are sealed with.
async fn who(s: &Store, data_dir: &std::path::Path) -> Result<duckies::Who> {
    let (account, entropy) = account::entropy(s, data_dir).await?;
    Ok(duckies::Who { id: account.id, entropy })
}

/// The chats repo, for the account signed in now. Functions that open or seal nothing
/// need only its id, so they answer while its data key is not here.
async fn chats(open: &Open, data_dir: &std::path::Path, function: &str, a: &Value) -> Result<Option<Value>> {
    let (s, c) = (&open.store, &open.chats);
    let (conv, project) = (text(a, "conv_id"), text(a, "project_id"));
    let int = |name: &str| a[name].as_i64().unwrap_or(0);
    let flag = |name: &str| a[name].as_bool().unwrap_or(false);
    match function {
        "conv_account" => return Ok(Some(json!(c.conv_account(conv).await?))),
        "conv_count" => return Ok(Some(json!(c.conv_count(project).await?))),
        "has_rows" => return Ok(Some(json!(c.has_rows().await?))),
        "folders_mark_deleted" => return Ok(Some(json!(c.folders_mark_deleted(texts(a, "folder_ids")).await?))),
        _ => {}
    }
    let id = || async { Ok::<_, Error>(account::current(s).await?.id) };
    Ok(Some(match function {
        "folder_project_id" => json!(c.folder_project_id(id().await?, text(a, "folder_id")).await?),
        "move_to_project" => {
            let folders = serde_json::from_value(a["folders"].clone()).unwrap_or_default();
            let convs = serde_json::from_value(a["convs"].clone()).unwrap_or_default();
            json!(c.move_to_project(id().await?, text(a, "target"), folders, convs).await?)
        }
        "conv_project_id" => json!(c.conv_project_id(id().await?, conv).await?),
        "conv_set_counts" => json!(c.conv_set_counts(id().await?, conv, int("tool_call_count"), int("file_count")).await?),
        "conv_message_summary" => json!(c.conv_message_summary(id().await?, conv).await?),
        "conv_msg_rev" => json!(c.conv_msg_rev(id().await?, conv).await?),
        "message_hashes_since" => json!(c.message_hashes_since(id().await?, conv, int("seq")).await?),
        "conv_delete" => json!(c.conv_delete(id().await?, conv).await?),
        "conv_ids_by_parent" => Value::Object(c.conv_ids_by_parent(id().await?, project).await?),
        "conv_max_sort_order" => json!(c.conv_max_sort_order(id().await?, project, text(a, "folder_id")).await?),
        "chat_key" => {
            let who = who(s, data_dir).await?;
            json!(BASE64.encode(c.chat_key(&who).await?.as_bytes()))
        }
        "folders_get" => json!(c.folders_get(who(s, data_dir).await?, project).await?),
        "folders_list_all_projects" => json!(c.folders_list_all_projects(who(s, data_dir).await?).await?),
        "folders_replace" => {
            json!(c.folders_replace(who(s, data_dir).await?, project, a["folders"].as_array().cloned().unwrap_or_default()).await?)
        }
        "conv_get" => {
            let project = a["project_id"].as_str().map(str::to_string);
            json!(c.conv_get(who(s, data_dir).await?, conv, project, flag("with_messages")).await?)
        }
        "conv_list" => json!(c.conv_list(who(s, data_dir).await?, project, flag("with_messages")).await?),
        "conv_list_all_projects" => json!(c.conv_list_all_projects(who(s, data_dir).await?, flag("with_messages")).await?),
        "conv_save" => {
            let save = Save {
                project_id: project,
                doc: map(a, "doc"),
                messages: a["messages"].as_array().cloned(),
                dirty_from: a["dirty_from"].as_i64(),
                expect_rev: a["expect_rev"].as_i64(),
                base_hashes: a["base_hashes"].is_array().then(|| texts(a, "base_hashes")),
                dirty_rows: serde_json::from_value(a["dirty_rows"].clone()).unwrap_or_default(),
            };
            Value::Object(c.conv_save(who(s, data_dir).await?, save).await?)
        }
        "upsert_run_message" => {
            let (updated, stats) = (a["updated"].as_f64().unwrap_or(0.0), a["stats"].as_object().cloned());
            c.upsert_run_message(who(s, data_dir).await?, conv, text(a, "run_id"), a["message"].clone(), updated, stats).await?
        }
        "messages_get" => json!(c.messages_get(who(s, data_dir).await?, conv).await?),
        "messages_get_rev" => json!(c.messages_get_rev(who(s, data_dir).await?, conv).await?),
        "messages_range" => json!(c.messages_range(who(s, data_dir).await?, conv, int("start"), a["stop"].as_i64()).await?),
        "messages_since" => json!(c.messages_since(who(s, data_dir).await?, conv, int("seq")).await?),
        "last_reply" => json!(c.last_reply(who(s, data_dir).await?, conv).await?),
        "search_messages" => {
            let limit = a["limit"].as_i64().unwrap_or(200);
            json!(c.search_messages(who(s, data_dir).await?, project, text(a, "query"), limit).await?)
        }
        "conv_if_settings_newer" => {
            json!(c.conv_if_settings_newer(who(s, data_dir).await?, conv, a["edited"].as_f64().unwrap_or(0.0)).await?)
        }
        _ => return Ok(None),
    }))
}

/// Chats made signed out, as the account signed in now sees them.
async fn local_chats(open: &Open, data_dir: &std::path::Path, function: &str, a: &Value) -> Result<Option<Value>> {
    let (s, c) = (&open.store, &open.chats);
    let account = account::current(s).await?.id;
    let ids = a["conv_ids"].is_array().then(|| texts(a, "conv_ids"));
    Ok(Some(match function {
        "waiting" => json!(c.waiting(account).await?),
        "adopt" => c.adopt(who(s, data_dir).await?, ids).await?,
        "remove" => json!(c.remove(account, ids.unwrap_or_default()).await?),
        _ => return Ok(None),
    }))
}

/// Sealed files, for the account signed in now.
async fn sealed_files(open: &Open, data_dir: &std::path::Path, function: &str, a: &Value) -> Result<Option<Value>> {
    let files = sealed_files::Files { store: &open.store, root: data_dir };
    let path = |name: &str| PathBuf::from(text(a, name));
    let account = a["account"].as_str();
    let me = || who(&open.store, data_dir);
    Ok(Some(match function {
        "ensure_keypair" => json!(files.ensure_keypair(&me().await?).await?),
        "public_key" => json!(files.public_key(account.unwrap_or_default()).await?.map(|key| BASE64.encode(key))),
        "sealed_for" => {
            let path = path("path");
            json!(blocking(move || Ok(sealed_file::sealed_for(&path))).await?)
        }
        "seal_file" => json!(files.seal_file(path("path"), text(a, "account")).await?),
        "open_file" => json!(files.open_file(path("path"), account, &me().await?).await?),
        "open_to" => json!(files.open_to(path("path"), path("dest"), account, &me().await?).await?),
        "read_bytes" => json!(BASE64.encode(files.read_bytes(path("path"), account, &me().await?).await?)),
        "open_chat_folder" => json!(files.open_chat_folder(path("folder"), account.unwrap_or_default(), &me().await?).await?),
        "open_local_folder" => json!(files.open_local_folder(path("folder")).await?),
        "arrange_chat_files" => Value::Object(files.arrange_chat_files(&me().await?).await?),
        "sweep_leftovers" => {
            let (root, older) = (data_dir.to_path_buf(), a["older_than_s"].as_f64().unwrap_or(600.0));
            json!(blocking(move || Ok(sealed_file::sweep_leftovers(&root, older))).await?)
        }
        _ => return Ok(None),
    }))
}

/// The events repo, and the perf events this process traced.
async fn events(s: &Store, repo: &str, function: &str, a: &Value) -> Result<Option<Value>> {
    let (kind, limit, since) = (text(a, "kind"), a["limit"].as_i64().unwrap_or(0), a["since"].as_f64());
    Ok(Some(match (repo, function) {
        ("perf", "trace") => {
            json!(crate::perf::trace(&kind, &text(a, "name"), a["duration_ms"].as_f64().unwrap_or(0.0), a["meta"].clone()))
        }
        ("perf", "flush") => json!(crate::perf::flush(s).await?),
        ("events", "insert") => {
            let ts = a["ts"].as_f64().unwrap_or(0.0);
            json!(events::insert(s, kind, ts, text(a, "source"), text(a, "message"), a["payload"].clone()).await?)
        }
        ("events", "insert_many") => json!(events::insert_many(s, kind, a["rows"].as_array().cloned().unwrap_or_default()).await?),
        ("events", "newest") => json!(events::newest(s, kind, limit, since).await?),
        ("events", "newest_from") => json!(events::newest_from(s, kind, text(a, "source"), limit, since).await?),
        ("events", "last_message") => json!(events::last_message(s, kind).await?),
        ("events", "clear") => json!(events::clear(s, kind).await?),
        ("events", "trim") => {
            json!(events::trim(s, kind, a["older_than"].as_f64().unwrap_or(0.0), a["keep"].as_i64().unwrap_or(0)).await?)
        }
        ("events", "count") => json!(events::count(s, kind).await?),
        ("events", "oldest") => json!(events::oldest(s, kind, limit).await?),
        ("events", "delete_ids") => json!(events::delete_ids(s, serde_json::from_value(a["ids"].clone()).unwrap_or_default()).await?),
        _ => return Ok(None),
    }))
}

/// The change journal's storage.
async fn ledger(s: &Store, function: &str, a: &Value) -> Result<Option<Value>> {
    let (project, run_id, digest) = (text(a, "project_id"), text(a, "run_id"), text(a, "digest"));
    let since = a["since"].as_f64().unwrap_or(0.0);
    Ok(Some(match function {
        "close_stale_runs" => {
            let ids = a["run_ids"].is_array().then(|| texts(a, "run_ids"));
            json!(ledger::close_stale_runs(s, a["now"].as_f64(), a["started_before"].as_f64(), ids).await?)
        }
        "run_get" => json!(ledger::run_get(s, project, run_id).await?),
        "runs_with_status" => json!(ledger::runs_with_status(s, project, texts(a, "statuses")).await?),
        "active_work" => json!(ledger::active_work(s, since, a["max_files"].as_u64().unwrap_or(8) as usize).await?),
        "runs_docs" => json!(ledger::runs_docs(s, project).await?),
        "runs_brief" => json!(ledger::runs_brief(s, project).await?),
        "run_put" => json!(ledger::run_put(s, project, a["run"].clone()).await?),
        "run_delete" => json!(ledger::run_delete(s, project, run_id).await?),
        "catalog" => Value::Object(ledger::catalog(s, project).await?),
        "latest_entry_for_path" => json!(ledger::latest_entry_for_path(s, project, texts(a, "paths"), since).await?),
        "index_get" => Value::Object(ledger::index_get(s, project).await?),
        "index_replace" => json!(ledger::index_replace(s, project, map(a, "index")).await?),
        "blob_put" => json!(ledger::blob_put(s, text(a, "text"), a["digest"].as_str().map(str::to_string)).await?),
        "blob_get" => json!(ledger::blob_get(s, digest).await?),
        "blob_exists" => json!(ledger::blob_exists(s, digest).await?),
        "sweep_blobs" => json!(ledger::sweep_blobs(s).await?),
        "watch_get" => Value::Object(ledger::watch_get(s, project).await?),
        "watch_replace" => json!(ledger::watch_replace(s, project, map(a, "seen")).await?),
        _ => return Ok(None),
    }))
}

/// The small repos: documents by key, plugin keys, usage, file history, projects.
async fn small(s: &Store, repo: &str, function: &str, a: &Value) -> Result<Option<Value>> {
    let (table, key, prefix) = (text(a, "table"), text(a, "key"), text(a, "prefix"));
    let (account, scope, path) = (text(a, "account"), text(a, "scope"), text(a, "path"));
    let of = || plugin_kv::Owner { account: text(a, "account"), scope: text(a, "scope"), plugin_id: text(a, "plugin_id") };
    let file = || history::File { project_id: text(a, "project_id"), path: text(a, "path") };
    let int = |name: &str| a[name].as_i64().unwrap_or(0);
    Ok(Some(match (repo, function) {
        ("kv", "get_doc") => json!(kv::get_doc(s, &table, key).await?),
        ("kv", "set_doc") => json!(kv::set_doc(s, &table, key, a["value"].clone()).await?),
        ("kv", "delete_doc") => json!(kv::delete_doc(s, &table, key).await?),
        ("kv", "prune_docs") => json!(kv::prune_docs(s, &table, prefix, int("keep")).await?),
        ("kv", "list_docs") => Value::Object(kv::list_docs(s, &table, prefix).await?),
        ("kv", "meta_get") => json!(kv::meta_get(s, key).await?),
        ("kv", "meta_set") => json!(kv::meta_set(s, key, text(a, "value")).await?),
        ("plugin_kv", "get") => json!(plugin_kv::get(s, of(), key).await?),
        ("plugin_kv", "set") => json!(plugin_kv::set(s, of(), key, a["value"].clone(), a["encrypted_b64"].as_str().map(str::to_string)).await?),
        ("plugin_kv", "delete") => json!(plugin_kv::delete(s, of(), key).await?),
        ("plugin_kv", "keys") => json!(plugin_kv::keys(s, of(), prefix).await?),
        ("plugin_kv", "delete_prefix") => json!(plugin_kv::delete_prefix(s, of(), prefix).await?),
        ("plugin_kv", "all_prefs") => Value::Object(plugin_kv::all_prefs(s, account, scope).await?),
        ("plugin_kv", "set_prefs") => json!(plugin_kv::set_prefs(s, of(), text(a, "sealed")).await?),
        ("plugin_kv", "has_rows") => json!(plugin_kv::has_rows(s).await?),
        ("plugin_kv", "has_unclaimed") => json!(plugin_kv::has_unclaimed(s).await?),
        ("plugin_kv", "claim_unclaimed") => json!(plugin_kv::claim_unclaimed(s, account).await?),
        ("plugin_kv", "unsealed_rows") => json!(plugin_kv::unsealed_rows(s, account, prefix).await?),
        ("plugin_kv", "replace_value") => json!(plugin_kv::replace_value(s, of(), key, text(a, "value"), text(a, "old")).await?),
        ("plugin_kv", "delete_scope") => json!(plugin_kv::delete_scope(s, account, scope).await?),
        ("usage", "insert") => json!(usage::insert(s, a["entry"].clone(), a["retention_s"].as_f64()).await?),
        ("usage", "since") => json!(usage::since(s, a["ts"].as_f64().unwrap_or(0.0)).await?),
        ("usage", "count") => json!(usage::count(s).await?),
        ("history", "newest") => json!(history::newest(s, file()).await?),
        ("history", "exists") => json!(history::exists(s, file(), text(a, "entry_id")).await?),
        ("history", "put") => {
            let version = history::Version {
                entry_id: text(a, "entry_id"),
                content: text(a, "content"),
                preview: text(a, "preview"),
                saved_at: int("saved_at"),
                attribution: a["attribution"].clone(),
                schema_version: a["schema_version"].as_i64().unwrap_or(2),
                content_hash: a["content_hash"].as_str().map(str::to_string),
            };
            json!(history::put(s, file(), version).await?)
        }
        ("history", "list_rows") => json!(history::list_rows(s, file()).await?),
        ("history", "content") => json!(history::content(s, file(), text(a, "entry_id")).await?),
        ("projects", "recent_paths") => json!(projects::recent_paths(s, a["limit"].as_i64().unwrap_or(projects::MAX_RECENT)).await?),
        ("projects", "opened_rows") => json!(projects::opened_rows(s).await?),
        ("projects", "removed_paths") => json!(projects::removed_paths(s).await?),
        ("projects", "touch") => {
            let open = projects::Open { slug: text(a, "slug"), path, name: text(a, "name"), when: a["when"].as_f64(), kind: text(a, "kind") };
            json!(projects::touch(s, open).await?)
        }
        ("projects", "kind_for") => json!(projects::kind_for(s, text(a, "slug")).await?),
        ("projects", "forget") => json!(projects::forget(s, text(a, "slug"), path).await?),
        ("projects", "replace_recent") => json!(projects::replace_recent(s, serde_json::from_value(a["projects"].clone()).unwrap_or_default()).await?),
        ("projects", "has_rows") => json!(projects::has_rows(s).await?),
        _ => return Ok(None),
    }))
}

/// Workflows (legacy rows, saved versions, per-account state), memory notes, plans and tasks.
async fn docs(s: &Store, repo: &str, function: &str, a: &Value) -> Result<Option<Value>> {
    let (id, old, new) = (text(a, "workflow_id"), text(a, "old_id"), text(a, "new_id"));
    let (account, space, project, name, key) = (text(a, "account"), text(a, "space"), text(a, "project_id"), text(a, "name"), text(a, "key"));
    let number = |name: &str| a[name].as_f64().unwrap_or(0.0);
    Ok(Some(match (repo, function) {
        ("automations", "put") => json!(automations::put(s, a["doc"].clone()).await?),
        ("automations", "delete") => json!(automations::delete(s, id).await?),
        ("automations", "has_legacy") => json!(automations::has_legacy(s).await?),
        ("automations", "list_all") => json!(automations::list_all(s).await?),
        ("automations", "archive") => json!(automations::archive(s, id, number("saved_at"), text(a, "snapshot")).await?),
        ("automations", "copy_versions") => json!(automations::copy_versions(s, old, new).await?),
        ("automations", "has_versions") => json!(automations::has_versions(s, id).await?),
        ("automations", "version_rows") => json!(automations::version_rows(s, id).await?),
        ("automations", "version_row") => json!(automations::version_row(s, id, text(a, "version_id")).await?),
        ("automations", "reseal_versions") => {
            json!(automations::reseal_versions(s, old, new, serde_json::from_value(a["converted"].clone()).unwrap_or_default()).await?)
        }
        ("workflows", "runtime_get") => workflows::runtime_get(s, account, id).await?,
        ("workflows", "runtime_put") => json!(workflows::runtime_put(s, account, id, map(a, "fields")).await?),
        ("workflows", "runtime_delete") => json!(workflows::runtime_delete(s, account, texts(a, "workflow_ids")).await?),
        ("workflows", "workflows_writable") => json!(workflows::workflows_writable(s, account, space).await?),
        ("workflows", "meta_put") => {
            json!(workflows::meta_put(s, account, space, a["workflows_writable"].as_bool().unwrap_or(false), text(a, "web_slug")).await?)
        }
        ("workflows", "web_slug") => json!(workflows::web_slug(s, account, space).await?),
        ("workflows", "synced_spaces") => json!(workflows::synced_spaces(s, account).await?),
        ("memory", "get") => json!(memory::get(s, project, name).await?),
        ("memory", "put") => {
            let entry = memory::Entry { name, description: text(a, "description"), author: text(a, "author"), updated: text(a, "updated"), body: text(a, "body") };
            json!(memory::put(s, project, entry).await?)
        }
        ("memory", "delete") => json!(memory::delete(s, project, name, a["with_subs"].as_bool().unwrap_or(false)).await?),
        ("memory", "index") => json!(memory::index(s, project).await?),
        ("memory", "subs_of") => json!(memory::subs_of(s, project, text(a, "entry")).await?),
        ("memory", "search") => json!(memory::search(s, project, text(a, "query"), a["limit"].as_i64().unwrap_or(20)).await?),
        ("memory", "has_rows") => json!(memory::has_rows(s, project).await?),
        ("plans", "merge_into_one_list") => json!(plans::merge_into_one_list(s).await?),
        ("plans", "plan_get") => json!(plans::plan_get(s, project, key).await?),
        ("plans", "plan_put") => json!(plans::plan_put(s, project, key, text(a, "kind"), a["doc"].clone()).await?),
        ("plans", "plan_delete") => json!(plans::plan_delete(s, project, key).await?),
        ("plans", "plan_docs") => json!(plans::plan_docs(s, project, text(a, "kind")).await?),
        ("plans", "task_get") => json!(plans::task_get(s, project, text(a, "task_id")).await?),
        ("plans", "task_put") => json!(plans::task_put(s, project, a["doc"].clone()).await?),
        ("plans", "task_docs") => json!(plans::task_docs(s, project).await?),
        _ => return Ok(None),
    }))
}

/// Copies of the other Duckies' chats, for the account signed in now.
async fn ducky_mirrors(s: &Store, data_dir: &std::path::Path, function: &str, a: &Value) -> Result<Option<Value>> {
    let who = who(s, data_dir).await?;
    let (account, ducky) = (who.id.clone(), text(a, "ducky"));
    let chat = ducky_mirrors::Chat { ducky: ducky.clone(), conv_id: text(a, "conv_id") };
    let at = |name: &str| a[name].as_f64().unwrap_or(0.0);
    Ok(Some(match function {
        "note_chat" => json!(ducky_mirrors::note_chat(s, who, chat, text(a, "title"), at("updated")).await?),
        "put_rows" => json!(ducky_mirrors::put_rows(s, who, chat, a["rows"].as_array().cloned().unwrap_or_default(), at("of")).await?),
        "mark_gone" => json!(ducky_mirrors::mark_gone(s, account, ducky, texts(a, "listed")).await?),
        "note_peer" => json!(ducky_mirrors::note_peer(s, account, ducky, text(a, "name")).await?),
        "forget_peer" => json!(ducky_mirrors::forget_peer(s, account, ducky).await?),
        "peers" => json!(ducky_mirrors::peers(s, who).await?),
        "rows" => json!(ducky_mirrors::rows(s, who, chat).await?),
        _ => return Ok(None),
    }))
}

/// The search index. Vectors travel as base64.
async fn search(s: &Store, data_dir: &std::path::Path, function: &str, a: &Value) -> Result<Option<Value>> {
    let (source, model, doc_key, plugin) = (text(a, "source_key"), text(a, "model"), text(a, "doc_key"), text(a, "plugin_id"));
    let unpacked = |blob: &Value| BASE64.decode(blob.as_str().unwrap_or_default()).unwrap_or_default();
    let vectors = || map(a, "vectors").iter().map(|(key, blob)| (key.clone(), unpacked(blob))).collect::<Vec<_>>();
    Ok(Some(match function {
        "existing" => Value::Object(search::existing(s, source, model).await?),
        "write_source" => {
            let source = search::Source {
                source_key: source,
                docs: a["docs"].as_array().cloned().unwrap_or_default(),
                vectors: vectors().into_iter().collect(),
                model,
                revision: text(a, "revision"),
                replace: a["replace"].as_bool().unwrap_or(true),
                keep: texts(a, "keep"),
            };
            search::write_source(s, source).await?
        }
        "missing_vectors" => json!(search::missing_vectors(s, model, a["limit"].as_i64().unwrap_or(5000)).await?),
        "set_vectors" => {
            let hashes = a["hashes"].is_object().then(|| map(a, "hashes").iter().map(|(key, hash)| (key.clone(), hash.as_str().unwrap_or_default().to_string())).collect());
            json!(search::set_vectors(s, vectors(), model, hashes).await?)
        }
        "tool_owners" => Value::Object(search::tool_owners(s, source).await?),
        "record_state" => json!(search::record_state(s, doc_key, model).await?),
        "upsert_one" => {
            let vec = a["vec"].is_string().then(|| bytes(a, "vec"));
            json!(search::upsert_one(s, a["doc"].clone(), vec, model, a["max_in_source"].as_i64()).await?)
        }
        "find_doc" => json!(search::find_doc(s, doc_key).await?),
        "count_source" => json!(search::count_source(s, source).await?),
        "delete_docs" => json!(search::delete_docs(s, texts(a, "doc_keys")).await?),
        "drop_source" => json!(search::drop_source(s, source).await?),
        "drop_sources_like" => json!(search::drop_sources_like(s, text(a, "prefix")).await?),
        "forget_records" => json!(search::forget_records(s, plugin).await?),
        "clear_plugin_gone" => json!(search::clear_plugin_gone(s, plugin).await?),
        "drop_scope_records" => json!(search::drop_scope_records(s, text(a, "account"), text(a, "scope_id")).await?),
        "drop_stale_sandbox_records" => json!(search::drop_stale_sandbox_records(s, a["older_than_s"].as_f64().unwrap_or(0.0)).await?),
        "source_keys" => json!(search::source_keys(s).await?),
        "source_ids" => json!(search::source_ids(s, source).await?),
        "tool_names" => json!(search::tool_names(s).await?),
        "get_state" => json!(search::get_state(s, source).await?),
        "states" => Value::Object(search::states(s).await?),
        "wipe" => json!(search::wipe(s).await?),
        "fts_query" => json!(search::fts_query(&text(a, "text"), a["prefix"].as_bool().unwrap_or(true))),
        "title_words" => json!(search::title_words(&text(a, "text"))),
        "keyword" => {
            let (limit, matching) = (a["limit"].as_i64().unwrap_or(50), a["match"].as_str().map(str::to_string));
            json!(search::keyword(s, text(a, "text"), texts(a, "kinds"), limit, source, matching).await?)
        }
        "load_vectors" => {
            let key = a["key"].is_string().then(|| tokens::Key::new(bytes(a, "key"))).transpose()?;
            let (ids, kinds, vecs) = search::load_vectors(s, model, text(a, "account"), key).await?;
            json!([ids, kinds, BASE64.encode(vecs)])
        }
        "get_docs" => {
            let ids = serde_json::from_value(a["ids"].clone()).unwrap_or_default();
            Value::Object(search::get_docs(s, ids, who(s, data_dir).await.ok()).await?)
        }
        "find_ids" => {
            json!(search::find_ids(s, text(a, "kind"), text(a, "pack_id"), text(a, "subskill_id"), text(a, "tool_name"), text(a, "title")).await?)
        }
        "has_docs" => json!(search::has_docs(s).await?),
        "counts" => search::counts(s).await?,
        "revision_token" => json!(search::revision_token(s).await?),
        _ => return Ok(None),
    }))
}

/// Search tokens under a key the test hands over.
fn tokens(function: &str, a: &Value) -> Result<Option<Value>> {
    let key = || tokens::Key::new(bytes(a, "key"));
    Ok(Some(match function {
        "words" => json!(tokens::words(&text(a, "text"))),
        "blind_text" => json!(tokens::blind_text(&text(a, "text"), &key()?)),
        "blind_query" => json!(tokens::blind_query(&text(a, "query"), &key()?)),
        "part_digest" => json!(tokens::part_digest(&text(a, "text"), &key()?)),
        _ => return Ok(None),
    }))
}

/// The duckies repo, for the account signed in now.
async fn duckies(s: &Store, data_dir: &std::path::Path, function: &str, a: &Value) -> Result<Option<Value>> {
    let who = who(s, data_dir).await?;
    let (id, kind) = (text(a, "ducky_id"), text(a, "kind"));
    let flag = |name: &str| a[name].as_bool().unwrap_or(false);
    Ok(Some(match function {
        "list_all" => json!(duckies::list_all(s, who, flag("include_deleted")).await?),
        "get" => json!(duckies::get(s, who, id).await?),
        "history" => json!(duckies::history(s, who, id).await?),
        "save" => json!(duckies::save(s, who, id, kind, a["data"].clone()).await?),
        "save_synced" => {
            json!(duckies::save_synced(s, who, id, kind, a["data"].clone(), a["updated"].as_f64().unwrap_or(0.0)).await?)
        }
        "set_archived" => json!(duckies::set_archived(s, who, id, kind, flag("archived")).await?),
        "set_deleted" => {
            let kind = if kind.is_empty() { "custom".to_string() } else { kind };
            json!(duckies::set_deleted(s, who, id, flag("deleted"), kind).await?)
        }
        "insert_missing" => {
            json!(duckies::insert_missing(s, who, a["rows"].as_array().cloned().unwrap_or_default()).await?)
        }
        _ => return Ok(None),
    }))
}

async fn dispatch(open: &Open, data_dir: &std::path::Path, repo: &str, function: &str, a: &Value) -> Result<Option<Value>> {
    match repo {
        "duckies" => return duckies(&open.store, data_dir, function, a).await,
        "chats" => return chats(open, data_dir, function, a).await,
        "local_chats" => return local_chats(open, data_dir, function, a).await,
        "events" | "perf" => return events(&open.store, repo, function, a).await,
        "ledger" => return ledger(&open.store, function, a).await,
        "search" => return search(&open.store, data_dir, function, a).await,
        "kv" | "plugin_kv" | "usage" | "history" | "projects" => return small(&open.store, repo, function, a).await,
        "automations" | "workflows" | "memory" | "plans" => return docs(&open.store, repo, function, a).await,
        "ducky_mirrors" => return ducky_mirrors(&open.store, data_dir, function, a).await,
        "sealed_files" => return sealed_files(open, data_dir, function, a).await,
        "tokens" => return tokens(function, a),
        _ => {}
    }
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
        ("secrets", "login_version") => json!(secrets::login_version(s).await?),
        ("secrets", "get_blob") => json!(secrets::get_blob(s, text(a, "name")).await?.map(|blob| BASE64.encode(blob))),
        ("secrets", "all_blobs") => named(secrets::all_blobs(s).await?),
        ("secrets", "set_blob") => json!(secrets::set_blob(s, text(a, "name"), bytes(a, "blob")).await?),
        ("secrets", "delete") => json!(secrets::delete(s, text(a, "name")).await?),
        ("secrets", "replace_all") => {
            let blobs = map(a, "blobs").iter().map(|(name, blob)| (name.clone(), bytes(&json!({"b": blob}), "b"))).collect();
            json!(secrets::replace_all(s, blobs).await?)
        }
        ("secrets", "has_rows") => json!(secrets::has_rows(s).await?),
        ("account", "current") => {
            let who = account::current(s).await?;
            json!({"key": who.key, "id": who.id})
        }
        ("account", "entropy") => {
            let (who, entropy) = account::entropy(s, data_dir).await?;
            json!({"id": who.id, "entropy": BASE64.encode(entropy)})
        }
        ("seal", "protect_secret") => {
            let value = text(a, "value");
            json!(BASE64.encode(blocking(move || seal::protect_secret(&value)).await?))
        }
        ("seal", "unprotect_secret") => {
            let blob = bytes(a, "blob");
            json!(blocking(move || seal::unprotect_secret(&blob)).await?)
        }
        ("seal", "seal_text") => {
            let (text, entropy) = (text(a, "text"), bytes(a, "entropy"));
            json!(blocking(move || seal::seal_text(&text, &entropy)).await?)
        }
        ("seal", "open_text") => {
            let (stored, entropy) = (text(a, "stored"), bytes(a, "entropy"));
            json!(blocking(move || seal::open_text(&stored, &entropy)).await?)
        }
        ("seal", "seal_bytes") => {
            let (data, entropy) = (bytes(a, "data"), bytes(a, "entropy"));
            json!(BASE64.encode(blocking(move || seal::seal_bytes(&data, &entropy)).await?))
        }
        ("seal", "open_bytes") => {
            let (raw, entropy) = (bytes(a, "raw"), bytes(a, "entropy"));
            json!(BASE64.encode(blocking(move || seal::open_bytes(&raw, &entropy)).await?))
        }
        _ => return Ok(None),
    }))
}
