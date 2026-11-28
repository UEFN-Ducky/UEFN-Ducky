//! The Verse editor's calls (`frontend/ui_web/verse_editor/api.py`): its switch, the
//! Problems list's cache and its verse-lsp.
//! ponytail: the project scan, the workflow server and revision control are
//! still the Python backend's; each moves here with its own methods.

use std::path::Path;
use std::sync::Arc;

use serde_json::{json, Map, Value};

use super::project_files::{given, known};
use super::project_paths::{normalize, project_slug, verse_workspace, FOLDER};
use super::settings::{failed, files_backend, load};
use super::Api;
use crate::store::misc;
use crate::store::pyjson::{int, pystr, truthy};
use crate::verse_editor::diagnostics::{self, Files};
use crate::verse_editor::lsp;

type Fields = Map<String, Value>;

/// The project a call is about.
struct Place {
    /// Its root as the editor names it; empty when none is open.
    root: String,
    /// The id its rows are kept under.
    slug: String,
    /// A plain folder, not an island: no Verse tools.
    folder: bool,
}

/// The project named by the call, else the open one.
async fn place(api: &Api, args: &Fields) -> Result<Place, String> {
    if files_backend("diagnostics") {
        return Err("diagnostics are kept in files here (DUCKY_STORE_BACKEND)".into());
    }
    Ok(named(api, args).await?.0)
}

/// The project named by the call, else the open one; and whether the editor is on.
async fn named(api: &Api, args: &Fields) -> Result<(Place, bool), String> {
    let (settings, known) = known(api).await?;
    let enabled = truthy(settings.get("verse_editor_enabled"));
    let asked = given(args, "project_root").trim().to_string();
    let open = Some(settings.get("uefn_project_root")).filter(|root| truthy(root)).map(pystr).unwrap_or_default();
    let raw = if asked.is_empty() { open.trim().to_string() } else { asked };
    let found = move || {
        let root = normalize(&raw);
        Place { slug: project_slug(&root), folder: !root.is_empty() && known.kind(&root) == FOLDER, root }
    };
    Ok((tokio::task::spawn_blocking(found).await.map_err(|stopped| stopped.to_string())?, enabled))
}

/// `_with_workspace`: the island's workspace roots and watch files, added to a status.
async fn with_workspace(api: &Api, mut status: Fields, root: String) -> Result<Value, String> {
    if !root.is_empty() {
        let data_dir = api.repos.data_dir().to_path_buf();
        let found = move || verse_workspace(Path::new(&root), &data_dir, true);
        let found = tokio::task::spawn_blocking(found).await.map_err(|stopped| stopped.to_string())?;
        let folders: Vec<Value> = found.folders.iter().map(|folder| json!({"name": folder.name, "path": folder.path})).collect();
        status.insert("workspace_folders".into(), folders.into());
        status.insert("watch_files".into(), found.watch_files.into());
        status.insert("vproject_shadow".into(), found.vproject_shadow.into());
    }
    Ok(Value::Object(status))
}

pub async fn get_verse_lsp_status(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let enabled = truthy(load(&api).await?.get("verse_editor_enabled"));
    let mut status = lsp::session(&given(&args, "client_id")).await.status(api.repos.data_dir()).await?;
    status.insert("enabled".into(), enabled.into());
    let root = status["project_root"].as_str().unwrap_or_default().to_string();
    with_workspace(&api, status, root).await
}

/// Start (or find running) the window's verse-lsp for an island.
pub async fn start_verse_lsp(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let (place, _) = named(&api, &args).await?;
    let session = lsp::session(&given(&args, "client_id")).await;
    if place.root.is_empty() || place.folder {
        let mut status = session.status(api.repos.data_dir()).await?;
        let off = if place.folder { "Verse tools are off for folder projects (no .uefnproject)." } else { "No project root set" };
        status.insert("error".into(), off.into());
        if place.folder {
            status.insert("folder_project".into(), true.into());
        }
        return Ok(Value::Object(status));
    }
    let status = session.start(&place.root, api.repos.data_dir()).await?;
    with_workspace(&api, status, place.root).await
}

pub async fn stop_verse_lsp(_api: Arc<Api>, args: Fields) -> Result<Value, String> {
    lsp::stop(&given(&args, "client_id")).await;
    Ok(Value::Null)
}

/// The project's cache rows; none when they can't be read (as Python treats it).
async fn stored(api: &Api, slug: &str) -> Result<Files, String> {
    let store = api.repos.store().await.map_err(failed)?;
    Ok(misc::diag_get(store, slug.to_string()).await.unwrap_or_default())
}

pub async fn is_verse_editor_enabled(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    Ok(json!(truthy(load(&api).await?.get("verse_editor_enabled"))))
}

/// What the Problems list shows before any scan: the cache, and how many files need one.
pub async fn load_verse_diagnostics_cache(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let place = place(&api, &args).await?;
    if place.root.is_empty() || place.folder {
        return Ok(json!({"files": [], "stale_count": 0, "from_cache": false}));
    }
    let files = stored(&api, &place.slug).await?;
    tokio::task::spawn_blocking(move || diagnostics::for_ui(Path::new(&place.root), files)).await.map_err(|stopped| stopped.to_string())
}

/// The editor's own verse-lsp checked one file: keep its result, and tell every window
/// (the Problems badge showed different counts per window otherwise).
pub async fn save_verse_file_cache(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let place = place(&api, &args).await?;
    if place.root.is_empty() {
        return Ok(json!({"ok": false, "error": "No project root set"}));
    }
    let rel = given(&args, "relative_path").trim().replace('\\', "/").trim_start_matches('/').to_string();
    let key = rel.to_lowercase();
    let said = |name: &str| args.get(name).cloned().unwrap_or(Value::Null);
    let result = json!({"path": key, "errors": int(&said("errors")), "warnings": int(&said("warnings")), "items": said("items").as_array().cloned().unwrap_or_default()});
    let mut files = stored(&api, &place.slug).await?;
    let (root, checked) = (place.root.clone(), result.clone());
    let kept = move || {
        let root = Path::new(&root);
        let stamp = diagnostics::fingerprint(&root.join(&rel)).filter(|_| root.join(&rel).is_file())?;
        files.insert(diagnostics::norm_key(&rel), diagnostics::entry(stamp, &checked));
        diagnostics::prune(root, &mut files);
        Some(files)
    };
    let Some(files) = tokio::task::spawn_blocking(kept).await.map_err(|stopped| stopped.to_string())? else {
        return Ok(json!({"ok": false, "error": "File not found"}));
    };
    // A write that fails leaves the old rows: the next check of the file writes them again.
    let store = api.repos.store().await.map_err(failed)?;
    let _ = misc::diag_replace(store, place.slug, files).await;
    api.publish(vec![json!({"type": "verse_diagnostics_sync", "files": [result], "replace": false, "project_root": place.root, "live": true})]);
    Ok(json!({"ok": true, "path": key}))
}
