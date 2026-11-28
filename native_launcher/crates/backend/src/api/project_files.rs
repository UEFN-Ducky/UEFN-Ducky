//! The Content pane's reads (`panel_api_project.py`): the tree, a file's text or media
//! URL, and Quick Open's list of paths.
//!
//! A call reads the settings and the project rows, then does all its disk work in one
//! blocking task ([`disk`]).
//! ponytail: the calls that change files (create, rename, move, copy, delete, import)
//! are still the Python backend's, and so is Quick Open's index after one of them: they
//! move here with the write pipeline, which then calls `disk::forget_rows`. Until then
//! these methods are not routed.

mod disk;

use std::sync::Arc;

use serde_json::{Map, Value};

use super::file_kinds::classify;
use super::project_paths::{documents_dir, Known};
use super::settings::{failed, imported, load, Loaded};
use super::Api;
use crate::store::projects;
use crate::store::pyjson::{int, pystr, truthy};
use disk::Project;

type Fields = Map<String, Value>;

/// `(value or "")` as text.
pub(super) fn given(args: &Fields, name: &str) -> String {
    args.get(name).filter(|value| truthy(value)).map(pystr).unwrap_or_default()
}

/// The settings and the project rows a call starts from (`documents` is not filled in:
/// finding it reads the disk).
pub(super) async fn known(api: &Api) -> Result<(Loaded, Known), String> {
    let settings = load(api).await?;
    imported(api, "projects", "recent_projects.json").await?;
    let store = api.repos.store().await.map_err(failed)?;
    let known = Known {
        kinds: projects::kinds(store).await.map_err(failed)?.into_iter().collect(),
        opened: projects::opened_rows(store).await.map_err(failed)?.into_iter().map(|(path, _opened)| path).collect(),
        removed: projects::removed_paths(store).await.map_err(failed)?,
        ..Known::default()
    };
    Ok((settings, known))
}

/// Run `work` on the open project, off the async workers.
async fn on_disk<T: Send + 'static>(api: &Api, work: impl FnOnce(&Project) -> T + Send + 'static) -> Result<T, String> {
    let (settings, mut known) = known(api).await?;
    let root = if truthy(settings.get("uefn_project_root")) { pystr(settings.get("uefn_project_root")) } else { String::new() };
    let show_hidden = truthy(settings.get("show_hidden_project_files"));
    let data_dir = api.repos.data_dir().to_path_buf();
    let run = move || {
        known.documents = documents_dir();
        work(&Project::open(&root, show_hidden, known, data_dir))
    };
    tokio::task::spawn_blocking(run).await.map_err(|stopped| stopped.to_string())
}

/// The folders a team member's full-path lane may be in (`lanes.project_roots`).
pub async fn lane_roots(api: &Api) -> Result<Vec<String>, String> {
    on_disk(api, |project| project.lane_roots()).await
}

/// The tree's roots (no path, or `__roots__`), or one folder's rows.
pub async fn list_project_files(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let path = given(&args, "relative_path");
    on_disk(&api, move |project| project.list(&path)).await?
}

/// The workspace roots: the project's Content, then UEFN's read-only Verse folders.
pub async fn list_workspace_roots(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    on_disk(&api, |project| project.roots()).await?
}

/// Quick Open and @-mentions: `{paths, total, truncated}` without a query (the first
/// 2,000), `{paths, truncated}` for one.
pub async fn list_project_file_paths(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let (query, limit) = (given(&args, "query"), args.get("limit").map_or(50, int));
    if !query.trim().is_empty() && args.get("limit").is_some_and(Value::is_null) {
        // ponytail: `int(None)` only; other limits that are not numbers read as 0.
        return Err("int() argument must be a string, a bytes-like object or a real number, not 'NoneType'".into());
    }
    on_disk(&api, move |project| project.paths(&query, limit)).await?
}

pub async fn read_project_file(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let path = given(&args, "relative_path");
    on_disk(&api, move |project| project.read(&path)).await?
}

/// What a file is by its name alone; no file is read.
pub async fn classify_project_file(_api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let path = given(&args, "relative_path");
    tokio::task::spawn_blocking(move || classify(&path, None)).await.map_err(|stopped| stopped.to_string())
}

/// The URL an editor tab loads a picture, model, sound or video from.
pub async fn project_file_media_url(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let path = given(&args, "relative_path");
    on_disk(&api, move |project| project.media(&path)).await?
}

pub async fn stat_project_file(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let path = given(&args, "relative_path");
    on_disk(&api, move |project| project.stat(&path)).await?
}

/// The sidebar's poll: a fingerprint per open folder.
pub async fn fingerprint_project_dirs(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let paths = args.get("relative_paths").and_then(Value::as_array).cloned().unwrap_or_default();
    on_disk(&api, move |project| project.fingerprints(&paths)).await
}
