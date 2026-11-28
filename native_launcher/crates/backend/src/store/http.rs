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
use super::{account, blocking, duckies, misc, seal, sealed_file, sealed_files, secrets, tokens, Error, Result, Store};

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
